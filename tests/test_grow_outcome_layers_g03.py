"""G-03 验收冻结：执行-互动-激活三层结果与真实激活回流。

先于实现编写：授权产品事件导入（event_id 幂等 / 时间窗 / 主体与来源校验 /
schema 版本）、L1/L2/L3 凭证（append-only、指向来源、按层限制声明）、
归因状态 direct_receipt/assisted_observation/unattributed（非因果）、
ProspectJourney 只读归并投影、结果卡新信息结构（未接入=UNKNOWN 不是 0）、
G07–G11 增补验收与自动触达前置三要素准入。

全部离线：合成 fixture，零真实发送、零网络（工单 G-03，P1）。
"""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core import outcome_layers
from core.execution_policy import authorize
from core.grow_v2 import GrowRunV2
from core.ledger import Ledger

ACTIONS = ["x:reply", "email:message"]
WINDOW = {"start": "2026-11-01", "end": "2026-11-07"}


def _due_now(day: str = "2026-11-03"):
    """16:00Z＝美西上午黄金窗，条目必然到期可发（与 v2 测试口径一致）。"""
    return datetime.fromisoformat(f"{day}T16:00:00").replace(
        tzinfo=timezone.utc)


def _facts(tmp: Path) -> Path:
    facts = {
        "product": {"name": "Verovo", "what": "AI 账期分摊",
                    "url": "https://example.invalid", "stage": "launched",
                    "product_type": "devtool"},
        "icp": [{"hypothesis": "开发者", "tags": ["developers"],
                 "evidence_label": "hypothesis", "evidence": "待验证"}],
        "baseline": {"followers": 120},
        "value_events": [{"name": "激活", "source": "产品事件",
                          "is_approximation": False}],
        "constraints": {"red_lines": ["不做群发"]},
        "owned_assets": {"platforms": ["x", "email"]},
    }
    path = tmp / "facts.json"
    path.write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
    return path


EVIDENCE_KEYS = ("event_id", "event_type", "subject_id", "source_system",
                 "source_ref", "amount", "currency", "payment_ref", "paid_at")


def evidence(row):
    """测试侧冻结的凭证 hash 口径：绑定事件身份 + 结构化支付字段。

    生产实现必须复算同一 payload；只判非空或伪造 ``sha256:fake`` 不算核验。
    """
    import hashlib
    payload = {}
    for key in EVIDENCE_KEYS:
        value = str(row.get(key) or "").strip()
        if key == "currency":
            value = value.upper()
        payload[key] = value
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def event(**kw):
    row = {
        "event_id": "e-0001",
        "event_type": "activation",
        "subject_id": "u1@example.com",
        "occurred_at": "2026-11-03T10:00:00",
        "source_system": "product_export",
        "source_ref": "app:activation:1",
        "campaign_id": "g1",
        "evidence_hash": "",
        "verified_by": "product-db",
    }
    row.update(kw)
    if row["verified_by"] and not row["evidence_hash"]:
        row["evidence_hash"] = evidence(row)
    return row


class G03Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.facts = _facts(self.dir)
        authorize(self.dir, by="tester", actions=ACTIONS, days=90)
        self.path = self.dir / "grow" / "g1.json"
        GrowRunV2.launch(self.path, run_id="g1", facts_path=str(self.facts),
                         goal_id="awareness.followers", channels=["x", "email"],
                         target=20, actions_per_week=7, started="2026-11-01",
                         handle="acct-a", data_dir=self.dir, skip_probe=True)
        self.run = GrowRunV2(self.path)
        self.run.arm(by="roy")

    def _json_file(self, rows, name="events.json"):
        path = self.dir / name
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return path

    def _import(self, rows, **kw):
        return outcome_layers.import_events(
            self.dir, self._json_file(rows), allowed_sources={"product_export"},
            imported_by="tester", **kw)

    def _seed_touch(self, target="u1@example.com", ts="2026-11-02T09:00:00",
                    campaign="g1"):
        ledger = Ledger(self.dir / "ledger.jsonl")
        return ledger.append(channel="email", action="message", target=target,
                             result="committed", campaign=campaign, ts=ts,
                             ref=f"ref-{ts}-{target}")


class ImportContractTests(G03Base):
    """AC G03：导入去重、主体/来源/类型校验、schema 版本。"""

    def test_same_event_imported_twice_counts_once(self):
        result = self._import([event()])
        self.assertEqual(1, result["created"])
        again = self._import([event()])
        self.assertEqual(0, again["created"])
        self.assertEqual(1, again["skipped_duplicate"])
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertEqual(1, summary["L3"]["activation"])

    def test_missing_required_fields_rejected_with_reason(self):
        rows = [event(event_id="", source_ref=""),
                event(event_id="e-2", event_type="nonsense"),
                event(event_id="e-3", subject_id="   "),
                event(event_id="e-4", occurred_at="not-a-date")]
        result = self._import(rows)
        self.assertEqual(0, result["created"])
        self.assertEqual(4, len(result["errors"]))
        self.assertEqual([], outcome_layers.read_product_events(self.dir))

    def test_unauthorized_source_system_rejected(self):
        path = self._json_file([event(source_system="random-saas")])
        result = outcome_layers.import_events(
            self.dir, path, allowed_sources={"product_export"}, imported_by="t")
        self.assertEqual(0, result["created"])
        self.assertIn("source_not_authorized", json.dumps(result["errors"]))

    def test_events_carry_schema_version_and_import_audit(self):
        self._import([event()])
        rec = outcome_layers.read_product_events(self.dir)[0]
        self.assertEqual(outcome_layers.EVENT_SCHEMA_VERSION,
                         rec["schema_version"])
        self.assertEqual("tester", rec["imported_by"])
        self.assertTrue(rec.get("import_batch"))

    def test_out_of_window_event_rejected_at_import(self):
        result = self._import([event(occurred_at="2026-10-20T10:00:00")],
                              window=WINDOW)
        self.assertEqual(0, result["created"])
        self.assertEqual(1, len(result["errors"]))
        self.assertIn("out_of_window", json.dumps(result["errors"]))

    def test_out_of_window_event_not_in_window_kpi(self):
        self._import([event(occurred_at="2026-10-20T10:00:00")])
        summary = outcome_layers.layer_summary(self.dir, run_id="g1",
                                               window=WINDOW)
        self.assertEqual(0, summary["L3"]["activation"])
        full = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertEqual(1, full["L3"]["activation"])


class ActivationJudgmentTests(G03Base):
    """AC G03：首次激活语义、campaign 缺失、供应商宣称、多触点。"""

    def test_same_user_second_activation_is_repeat_not_new(self):
        self._import([event(event_id="a1", occurred_at="2026-11-03T10:00:00"),
                      event(event_id="a2", occurred_at="2026-11-05T10:00:00")])
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertEqual(1, summary["L3"]["activation"])
        self.assertEqual(1, len(summary["L3"]["repeated_activations"]))

    def test_signup_twice_still_one_signup_subject(self):
        self._import([event(event_id="s1", event_type="signup",
                            occurred_at="2026-11-02T10:00:00",
                            verified_by="", evidence_hash=""),
                      event(event_id="s2", event_type="signup",
                            occurred_at="2026-11-04T10:00:00",
                            verified_by="", evidence_hash="")])
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertEqual(1, summary["L3"]["signup"])

    def test_missing_campaign_counts_total_but_not_attributed(self):
        self._import([event(campaign_id="")])
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertEqual(1, summary["L3"]["activation"])  # 总激活可计
        counts = summary["attribution"]["summary"]
        self.assertEqual(1, counts["unattributed"])
        self.assertEqual(0, counts["direct_receipt"])
        self.assertEqual(0, counts["assisted_observation"])

    def test_supplier_paid_claim_without_confirmation_is_unverified(self):
        self._import([event(event_type="paid", verified_by="",
                            evidence_hash="")])
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertEqual(0, summary["L3"]["paid"])  # 未确认不计
        self.assertEqual(1, len(summary["L3"]["unverified"]))
        self.assertEqual("unverified",
                         summary["L3"]["unverified"][0]["verification_state"])

    def test_multi_touch_reported_without_auto_causality(self):
        self._seed_touch(ts="2026-11-01T09:00:00")
        self._seed_touch(ts="2026-11-02T09:00:00")
        self._import([event()])
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        act = summary["attribution"]["activations"][0]
        self.assertEqual("direct_receipt", act["attribution"])
        self.assertEqual("not_claimed", act["causal_credit"])
        self.assertEqual(2, len(act["touches"]))
        self.assertIn("非因果", summary["attribution"]["note"])

    def test_campaign_without_matching_touch_is_assisted_observation(self):
        self._import([event(campaign_id="g1")])  # 无任何触达匹配
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertEqual("assisted_observation",
                         summary["attribution"]["activations"][0]["attribution"])

    def test_manual_qualification_change_is_audited(self):
        self._import([event()])
        with self.assertRaises(outcome_layers.OutcomeLayerError):
            outcome_layers.set_event_qualification(
                self.dir, "e-0001", qualifies=False, by="", note="")
        before_bytes = (self.dir / "outcomes" / "product_events.jsonl").read_bytes()
        before_sha = __import__("hashlib").sha256(before_bytes).hexdigest()
        audit = outcome_layers.set_event_qualification(
            self.dir, "e-0001", qualifies=False, by="roy",
            note="内部测试账号，不算激活")
        self.assertTrue(audit["ts"])
        after_bytes = (self.dir / "outcomes" / "product_events.jsonl").read_bytes()
        self.assertEqual(before_bytes, after_bytes)  # 原事件一个字节都不改
        self.assertEqual(before_sha,
                         __import__("hashlib").sha256(after_bytes).hexdigest())
        records = outcome_layers.read_product_events(self.dir)
        self.assertEqual(1, len(records))  # 原事件未被改写
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertEqual(0, summary["L3"]["activation"])
        audits = outcome_layers.read_qualifications(self.dir)
        self.assertEqual(1, len(audits))
        self.assertEqual("roy", audits[0]["by"])


class BuyerVerifiedPaymentTests(G03Base):
    """D1：付款核验必须来自支付/买方控制域，且证据 hash 可复算。"""

    @staticmethod
    def _paid(**kw):
        row = event(event_type="paid", verified_by="stripe",
                    subject_id="buyer-1",
                    source_ref="stripe:pi_123",
                    amount="49.00", currency="USD",
                    payment_ref="pi_123",
                    paid_at="2026-11-03T09:59:00")
        row.update(kw)
        row["evidence_hash"] = kw.get("evidence_hash") or evidence(row)
        return row

    def test_supplier_paid_claim_with_nonempty_fields_is_unverified(self):
        # 字段都非空，但核验方是供应商销售 → 仍然只是宣称。
        row = self._paid(verified_by="vendor-sales")
        self._import([row])
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertEqual(0, summary["L3"]["paid"])
        self.assertEqual(1, len(summary["L3"]["unverified"]))
        receipts = outcome_layers.read_receipts(self.dir)
        self.assertTrue(receipts)
        self.assertTrue(all(r["assertion"] == "unverified_claim"
                            for r in receipts))

    def test_unverifiable_hash_is_unverified_even_for_trusted_verifier(self):
        self._import([self._paid(evidence_hash="sha256:fake")])
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertEqual(0, summary["L3"]["paid"])
        self.assertEqual(1, len(summary["L3"]["unverified"]))

    def test_tampered_structured_fields_break_the_hash(self):
        row = self._paid()
        row["amount"] = "4900.00"  # 改成大额，hash 对不上
        self._import([row])
        self.assertEqual(0, outcome_layers.layer_summary(
            self.dir, run_id="g1")["L3"]["paid"])

    def test_buyer_payment_record_verifies_and_writes_receipt(self):
        self._import([self._paid()])
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertEqual(1, summary["L3"]["paid"])
        receipt = [r for r in outcome_layers.read_receipts(self.dir)
                   if r["layer"] == "L3"][0]
        self.assertEqual("payment_verified", receipt["assertion"])

    def test_buyer_finance_export_verifies(self):
        self._import([self._paid(verified_by="buyer:finance",
                                 source_ref="ap:2026-11-001")])
        self.assertEqual(1, outcome_layers.layer_summary(
            self.dir, run_id="g1")["L3"]["paid"])

    def test_activation_also_needs_verifiable_evidence(self):
        row = event(verified_by="vendor-sales")
        row["evidence_hash"] = "sha256:fake"
        self._import([row])
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertEqual(0, summary["L3"]["activation"])
        self.assertEqual(1, len(summary["L3"]["unverified"]))

    def test_paid_without_structured_payment_fields_is_unverified(self):
        row = self._paid()
        row.pop("amount", None)
        row["evidence_hash"] = evidence(row)
        self._import([row])
        self.assertEqual(0, outcome_layers.layer_summary(
            self.dir, run_id="g1")["L3"]["paid"])

    def test_operator_can_extend_trusted_verifiers(self):
        row = self._paid(verified_by="acme-ap")
        self._import([row], trusted_verifiers={"acme-ap"})
        self.assertEqual(1, outcome_layers.layer_summary(
            self.dir, run_id="g1")["L3"]["paid"])


class ReceiptTests(G03Base):
    """三层凭证：append-only、指向来源、按层限制允许的声明。"""

    def test_import_writes_receipts_pointing_at_source(self):
        self._import([event()])
        receipts = outcome_layers.read_receipts(self.dir)
        self.assertEqual(1, len(receipts))
        self.assertEqual("L3", receipts[0]["layer"])
        self.assertEqual("product_events:e-0001",
                         receipts[0]["source_ref"])
        self.assertEqual("not_claimed", receipts[0]["causality"])

    def test_layer_cannot_claim_beyond_its_evidence(self):
        with self.assertRaises(outcome_layers.OutcomeLayerError):
            outcome_layers.write_receipt(
                self.dir, layer="L1", kind="action_receipt",
                source_ref="ledger:ref-1",
                assertion="business_growth")  # L1 不允许宣称业务增长
        rec = outcome_layers.write_receipt(
            self.dir, layer="L1", kind="action_receipt",
            source_ref="ledger:ref-1", assertion="platform_action_confirmed")
        self.assertEqual("L1", rec["layer"])

    def test_action_receipt_sync_is_idempotent(self):
        self._seed_touch()
        first = outcome_layers.sync_action_receipts(self.dir, run_id="g1")
        self.assertEqual(1, first["written"])
        again = outcome_layers.sync_action_receipts(self.dir, run_id="g1")
        self.assertEqual(0, again["written"])
        receipts = outcome_layers.read_receipts(self.dir)
        self.assertEqual(1, len([r for r in receipts if r["layer"] == "L1"]))


class LayerAvailabilityTests(G03Base):
    """G11：无独立事件源时 activation=UNKNOWN，不是 0。"""

    def test_no_event_source_is_unknown_not_zero(self):
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertFalse(summary["L3"]["available"])
        self.assertEqual("UNKNOWN", summary["L3"]["state"])
        self.assertIn("未接入", summary["L3"]["text"])

    def test_daily_card_says_unknown_not_zero(self):
        card = self.run.daily(self.dir, on="2026-11-03")
        self.assertIn("layers", card)
        text = GrowRunV2.render_daily(card)
        self.assertIn("激活事件未接入", text)
        self.assertIn("UNKNOWN", text)
        self.assertNotIn("激活 0", text)

    def test_followers_data_without_business_events(self):
        from core.attribution import OutcomeLedger
        ol = OutcomeLedger(self.dir)
        ol.add_count(metric="x_followers", value=121, window="2026-11-03",
                     source_ref="seed", approximated=True)
        card = self.run.daily(self.dir, on="2026-11-03")
        self.assertIn("121", json.dumps(card["readings"]))
        self.assertEqual("UNKNOWN", card["layers"]["L3"]["state"])


class ProspectJourneyTests(G03Base):
    """§12.2 只读投影 + G07–G10。"""

    def _contacts_file(self, rows):
        path = self.dir / "contacts.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def test_journey_merges_ledger_events_and_suppression(self):
        self._seed_touch()
        self._import([event()])
        journey = outcome_layers.prospect_journey(
            self.dir, prospect_ref="u1@example.com", run_id="g1")
        for field in ("prospect_ref", "source_refs", "qualification_basis",
                      "business_relevance", "runs_seen", "contacted_actions",
                      "responses", "product_events", "suppression_state",
                      "current_state", "next_allowed_action",
                      "evidence_updated_at"):
            self.assertIn(field, journey)
        self.assertEqual(1, len(journey["contacted_actions"]))
        self.assertEqual(1, len(journey["product_events"]))
        self.assertEqual("none", journey["suppression_state"])
        self.assertTrue(journey["source_refs"])

    def test_g07_new_run_does_not_recontact_dnc(self):
        self._contacts_file([
            {"id": "contact-0001", "name": "DNC Person",
             "platforms": {"email": "dnc@example.com"},
             "state": {"dnc": True, "bounced": None, "replies": 0,
                       "last_touch": {}}}])
        result = self.run.stage(
            self.dir, [{"target": "dnc@example.com",
                        "body": "一条具体的回复，先说结论再给细节。",
                        "their_problem": "对账差异难定位",
                        "why_we_help": "我们有行级核对",
                        "expected_action": "试用一次对账"}],
            scheduled_for="2026-11-03", channel="email", action="message")
        queue = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        committed = GrowRunV2._suppressed_targets(self.dir)
        selected = GrowRunV2._select_items(
            queue, on="2026-11-03", now=_due_now(),
            default_window="", committed_targets=committed,
            limit=10)
        self.assertEqual([], selected)  # DNC 对象不进入可自动触达集合
        journey = outcome_layers.prospect_journey(
            self.dir, prospect_ref="dnc@example.com", run_id="g1")
        self.assertEqual("dnc", journey["suppression_state"])

    def test_g08_same_name_different_platform_id_not_merged(self):
        self._contacts_file([
            {"id": "contact-0001", "name": "Alice",
             "platforms": {"x": "111"}, "state": {"dnc": False}},
            {"id": "contact-0002", "name": "Alice",
             "platforms": {"x": "222"}, "state": {"dnc": False}}])
        first = outcome_layers.prospect_journey(self.dir, prospect_ref="x:111")
        second = outcome_layers.prospect_journey(self.dir, prospect_ref="x:222")
        self.assertNotEqual(first["prospect_ref"], second["prospect_ref"])
        self._import([event(subject_id="111", event_id="x1"),
                      event(subject_id="222", event_id="x2")])
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertEqual(2, summary["L3"]["activation"])  # 两人不合并

    def test_g09_journey_does_not_leak_across_workspaces(self):
        other = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(other, True))
        ledger = Ledger(other / "ledger.jsonl")
        ledger.append(channel="email", action="message",
                      target="u1@example.com", result="committed",
                      campaign="other-run", ts="2026-11-02T09:00:00",
                      ref="foreign-1")
        self._import([event()])
        journey = outcome_layers.prospect_journey(
            self.dir, prospect_ref="u1@example.com", run_id="g1")
        refs = json.dumps(journey, ensure_ascii=False)
        self.assertNotIn("foreign-1", refs)  # 别的 workspace 不泄漏进来
        foreign = outcome_layers.prospect_journey(
            other, prospect_ref="u1@example.com")
        self.assertEqual([], foreign["product_events"])

    def test_g10_three_replies_are_one_prospect(self):
        rows = [event(event_id=f"m{i}", event_type="meaningful_conversation",
                      occurred_at=f"2026-11-0{i+1}T10:00:00",
                      verified_by="human", evidence_hash="sha256:x")
                for i in range(3)]
        self._import(rows)
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertEqual(1, summary["L2"]["unique_subjects"])
        journey = outcome_layers.prospect_journey(
            self.dir, prospect_ref="u1@example.com", run_id="g1")
        self.assertEqual(3, len(journey["responses"]))
        self.assertEqual(1, journey["prospect_count"])


class OutreachAdmissionTests(G03Base):
    """三要素准入：缺任一要素不进入可自动触达集合。"""

    def _stage_items(self, *items):
        return self.run.stage(self.dir, list(items), scheduled_for="2026-11-03")

    def test_missing_any_element_blocks_auto_reach(self):
        complete = {"target": "tweet:https://x.com/a/1",
                    "body": "先说结论，再给一个可复现的细节。",
                    "their_problem": "发票差异定位难",
                    "why_we_help": "行级核对可复算",
                    "expected_action": "跑一次对账样例"}
        no_action = dict(complete, target="tweet:https://x.com/a/2",
                         expected_action="")
        result = self._stage_items(complete, no_action)
        queue = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        by_target = {i["target"]: i for i in queue["items"]}
        self.assertTrue(by_target["tweet:https://x.com/a/1"]["outreach_ready"])
        self.assertFalse(by_target["tweet:https://x.com/a/2"]["outreach_ready"])
        self.assertIn("expected_action",
                      by_target["tweet:https://x.com/a/2"]["outreach_missing"])
        selected = GrowRunV2._select_items(
            queue, on="2026-11-03", now=_due_now(),
            default_window="", committed_targets=set(), limit=10)
        targets = [i["target"] for i in selected]
        self.assertIn("tweet:https://x.com/a/1", targets)
        self.assertNotIn("tweet:https://x.com/a/2", targets)

    def test_legacy_items_without_elements_keep_working(self):
        result = self._stage_items(
            {"target": "tweet:https://x.com/a/3",
             "body": "旧式手工条目：先说结论，再给一个可复现的细节。"})
        queue = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        item = queue["items"][0]
        self.assertIsNone(item["outreach_ready"])  # 未评估≠出局（既有功能）
        selected = GrowRunV2._select_items(
            queue, on="2026-11-03", now=_due_now(),
            default_window="", committed_targets=set(), limit=10)
        self.assertEqual(1, len(selected))


class ResultCardStructureTests(G03Base):
    """结果卡新信息结构：主目标状态、三层分列、证据盲点、人工分钟、建议。"""

    def _run_data(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data.setdefault("telemetry", {}).setdefault("weeks", {})["2026-11-01"] = {
            "human_minutes": 25}
        self.path.write_text(json.dumps(data, ensure_ascii=False),
                             encoding="utf-8")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_card_has_goal_status_and_layers(self):
        from core import result_card
        self._seed_touch()
        self._import([event()])
        run = self._run_data()
        card = result_card.build(self.dir, run, "2026-11-01", "2026-11-07")
        self.assertIn(card["goal_status"]["state"], ("met", "short", "no_data"))
        for key in ("L1", "L2", "L3", "attribution"):
            self.assertIn(key, card["layers"])
        self.assertIn("coverage", card["evidence"])
        self.assertIn("blind_spots", card["evidence"])
        self.assertEqual(25, card["human_minutes"])
        self.assertTrue(card["recommendation"])
        text = result_card.render(card)
        self.assertIn("主目标", text)
        self.assertIn("L1", text)
        self.assertIn("L2", text)
        self.assertIn("L3", text)
        self.assertIn("direct_receipt", text)
        self.assertIn("证据覆盖与盲点", text)
        self.assertIn("人工分钟", text)

    def test_card_without_events_says_unknown(self):
        from core import result_card
        run = self._run_data()
        card = result_card.build(self.dir, run, "2026-11-01", "2026-11-07")
        self.assertEqual("UNKNOWN", card["layers"]["L3"]["state"])
        text = result_card.render(card)
        self.assertIn("未接入", text)
        self.assertNotIn("activation 0", text)

    def test_no_data_goal_status(self):
        from core import result_card
        run = self._run_data()
        card = result_card.build(self.dir, run, "2026-11-01", "2026-11-07")
        self.assertEqual("no_data", card["goal_status"]["state"])


if __name__ == "__main__":
    unittest.main()
