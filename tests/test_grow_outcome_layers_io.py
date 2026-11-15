"""G-03 实现侧补充测试：导入形态、审计、投影、只读投影与 CLI 默认 dry-run。

冻结契约在 ``test_grow_outcome_layers_g03.py``；这里补三块它没覆盖的实现细节：

1. 导入形态（JSONL/CSV、dry-run、批量内重复、署名与来源门槛）；
2. 审计与投影（资格改动的 latest 生效、L1 回执 dry-run、业务台账投影）；
3. CLI 入口默认不写盘 + 输出可机器读（MKT_HOME 临时工作区，零网络）。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import outcome_layers
from core.attribution import OutcomeLedger
from core.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"
EVENT = {
    "event_id": "e-0001",
    "event_type": "activation",
    "subject_id": "u1@example.com",
    "occurred_at": "2026-11-03T10:00:00",
    "source_system": "product_export",
    "source_ref": "app:activation:1",
    "campaign_id": "g1",
    "evidence_hash": "sha256:abc",
    "verified_by": "product-db",
}


def _row(**kw):
    row = dict(EVENT)
    row.update(kw)
    return row


class TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name, payload):
        path = self.dir / name
        path.write_text(
            payload if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False),
            encoding="utf-8")
        return path

    def import_rows(self, rows, **kw):
        kw.setdefault("allowed_sources", {"product_export"})
        kw.setdefault("imported_by", "tester")
        return outcome_layers.import_events(
            self.dir, self.write("events.json", rows), **kw)


class ImportShapeTests(TmpDir):
    def test_dry_run_writes_nothing(self):
        result = self.import_rows([_row()], dry_run=True)
        self.assertEqual(0, result["created"])
        self.assertEqual(1, result["would_create"])
        self.assertEqual([], outcome_layers.read_product_events(self.dir))
        self.assertEqual([], outcome_layers.read_receipts(self.dir))

    def test_duplicate_inside_one_batch_counts_once(self):
        result = self.import_rows([_row(), _row()])
        self.assertEqual(1, result["created"])
        self.assertEqual(1, result["skipped_duplicate"])
        self.assertEqual(1, len(outcome_layers.read_product_events(self.dir)))

    def test_requires_authorized_source_and_signature(self):
        path = self.write("events.json", [_row()])
        with self.assertRaises(outcome_layers.OutcomeLayerError):
            outcome_layers.import_events(self.dir, path, allowed_sources=[],
                                         imported_by="tester")
        with self.assertRaises(outcome_layers.OutcomeLayerError):
            outcome_layers.import_events(self.dir, path,
                                         allowed_sources={"product_export"},
                                         imported_by="")

    def test_jsonl_and_csv_import(self):
        jsonl = self.write("events.jsonl",
                           "\n".join(json.dumps(r, ensure_ascii=False)
                                     for r in (_row(), _row(event_id="e-2"))))
        result = outcome_layers.import_events(
            self.dir, jsonl, allowed_sources={"product_export"}, imported_by="t")
        self.assertEqual(2, result["created"])
        csv_path = self.write(
            "more.csv",
            "event_id,event_type,subject_id,occurred_at,source_system,"
            "source_ref,campaign_id,evidence_hash,verified_by\n"
            "e-3,activation,u3@example.com,2026-11-04T10:00:00,product_export,"
            "app:activation:3,g1,sha256:x,product-db\n")
        more = outcome_layers.import_events(
            self.dir, csv_path, allowed_sources={"product_export"},
            imported_by="t")
        self.assertEqual(1, more["created"])
        self.assertEqual(3, len(outcome_layers.read_product_events(self.dir)))

    def test_window_boundaries_are_inclusive(self):
        result = self.import_rows(
            [_row(occurred_at="2026-11-01T00:00:00"),
             _row(event_id="e-2", occurred_at="2026-11-07T23:59:59")],
            window={"start": "2026-11-01", "end": "2026-11-07"})
        self.assertEqual(2, result["created"])
        edge = self.import_rows(
            [_row(event_id="e-3", occurred_at="2026-11-08T00:00:00")],
            window={"start": "2026-11-01", "end": "2026-11-07"})
        self.assertEqual(0, edge["created"])
        self.assertIn("out_of_window", json.dumps(edge["errors"]))

    def test_control_characters_in_subject_are_rejected(self):
        result = self.import_rows([_row(subject_id="u1\nbot")])
        self.assertEqual(0, result["created"])
        self.assertIn("invalid_subject", json.dumps(result["errors"]))
        self.assertEqual([], outcome_layers.read_product_events(self.dir))

    def test_bad_file_raises_actionable_error(self):
        path = self.write("broken.json", "{not json")
        with self.assertRaises(outcome_layers.OutcomeLayerError):
            outcome_layers.import_events(self.dir, path,
                                         allowed_sources={"product_export"},
                                         imported_by="t")
        with self.assertRaises(outcome_layers.OutcomeLayerError):
            outcome_layers.import_events(self.dir, self.dir / "missing.json",
                                         allowed_sources={"product_export"},
                                         imported_by="t")


class ReceiptAndProjectionTests(TmpDir):
    def test_unverified_claim_gets_unverified_receipt_and_no_projection(self):
        self.import_rows([_row(event_type="paid", verified_by="",
                               evidence_hash="")])
        receipts = outcome_layers.read_receipts(self.dir, layer="L3")
        self.assertEqual(1, len(receipts))
        self.assertEqual("unverified_claim", receipts[0]["assertion"])
        self.assertEqual("unverified", receipts[0]["verification_state"])
        ol = OutcomeLedger(self.dir)
        self.assertEqual([], [r for r in ol.records()
                              if r.get("kind") == "event"])

    def test_verified_event_is_projected_once_into_outcome_ledger(self):
        self.import_rows([_row()])
        self.import_rows([_row()])  # 幂等：event_id 相同不再投影
        events = [r for r in OutcomeLedger(self.dir).records()
                  if r.get("kind") == "event"]
        self.assertEqual(1, len(events))
        self.assertEqual("product_events:e-0001", events[0]["import_key"])
        self.assertEqual("verified", events[0]["verification"]["status"])

    def test_reimport_repairs_missing_receipt(self):
        self.import_rows([_row()])
        receipts = self.dir / "outcomes" / "receipts.jsonl"
        receipts.write_text("", encoding="utf-8")  # 模拟凭证文件丢了
        again = self.import_rows([_row()])
        self.assertEqual(0, again["created"])
        self.assertEqual(1, again["repaired"])
        self.assertEqual(1, len(outcome_layers.read_receipts(self.dir)))

    def test_mixed_batch_is_partial_and_reports_both_sides(self):
        result = self.import_rows([_row(),
                                   _row(event_id="bad", source_system="random")])
        self.assertEqual(1, result["created"])
        self.assertTrue(result["partial"])
        self.assertEqual(1, len(result["errors"]))
        self.assertEqual(1, len(outcome_layers.read_product_events(self.dir)))

    def test_l1_sync_dry_run_then_apply_is_idempotent(self):
        ledger = Ledger(self.dir / "ledger.jsonl")
        ledger.append(channel="email", action="message",
                      target="u1@example.com", result="committed",
                      campaign="g1", ts="2026-11-02T09:00:00", ref="ref-1")
        dry = outcome_layers.sync_action_receipts(self.dir, run_id="g1",
                                                  dry_run=True)
        self.assertEqual(1, dry["would_write"])
        self.assertEqual(0, dry["written"])
        self.assertEqual([], outcome_layers.read_receipts(self.dir))
        first = outcome_layers.sync_action_receipts(self.dir, run_id="g1")
        self.assertEqual(1, first["written"])
        again = outcome_layers.sync_action_receipts(self.dir, run_id="g1")
        self.assertEqual(0, again["written"])

    def test_l1_sync_window_filter(self):
        ledger = Ledger(self.dir / "ledger.jsonl")
        ledger.append(channel="x", action="reply", target="tweet:1",
                      result="committed", campaign="g1",
                      ts="2026-10-20T09:00:00", ref="old")
        out = outcome_layers.sync_action_receipts(
            self.dir, run_id="g1",
            window={"start": "2026-11-01", "end": "2026-11-07"})
        self.assertEqual(0, out["written"])
        self.assertEqual(0, out["would_write"])


class QualificationAuditTests(TmpDir):
    def test_qualification_requires_existing_event_and_reason(self):
        with self.assertRaises(outcome_layers.OutcomeLayerError):
            outcome_layers.set_event_qualification(
                self.dir, "nope", qualifies=False, by="roy", note="x")
        self.import_rows([_row()])
        with self.assertRaises(outcome_layers.OutcomeLayerError):
            outcome_layers.set_event_qualification(
                self.dir, "e-0001", qualifies=False, by="roy", note=" ")
        self.assertEqual([], outcome_layers.read_qualifications(self.dir))

    def test_dry_run_keeps_original_and_writes_no_audit(self):
        self.import_rows([_row()])
        record = outcome_layers.set_event_qualification(
            self.dir, "e-0001", qualifies=False, by="roy",
            note="内部测试账号", dry_run=True)
        self.assertFalse(record["applied"])
        self.assertEqual([], outcome_layers.read_qualifications(self.dir))
        self.assertEqual(1, outcome_layers.layer_summary(
            self.dir, run_id="g1")["L3"]["activation"])

    def test_latest_qualification_wins_and_original_is_untouched(self):
        self.import_rows([_row()])
        outcome_layers.set_event_qualification(
            self.dir, "e-0001", qualifies=False, by="roy", note="不算")
        outcome_layers.set_event_qualification(
            self.dir, "e-0001", qualifies=True, by="roy", note="复核后算回")
        audits = outcome_layers.read_qualifications(self.dir)
        self.assertEqual(2, len(audits))
        self.assertEqual([False, True], [a["qualifies"] for a in audits])
        self.assertEqual(1, outcome_layers.layer_summary(
            self.dir, run_id="g1")["L3"]["activation"])
        self.assertEqual(1, len(outcome_layers.read_product_events(self.dir)))


class LayerContractTests(TmpDir):
    def test_layer_kind_mismatch_rejected(self):
        with self.assertRaises(outcome_layers.OutcomeLayerError):
            outcome_layers.write_receipt(
                self.dir, layer="L2", kind="action_receipt",
                source_ref="ledger:x", assertion="engagement_observed")
        with self.assertRaises(outcome_layers.OutcomeLayerError):
            outcome_layers.write_receipt(
                self.dir, layer="L3", kind="activation_receipt",
                source_ref="", assertion="activation_verified")

    def test_l3_unknown_is_not_zero_after_signup_only(self):
        self.import_rows([_row(event_type="signup", verified_by="",
                               evidence_hash="", collected_at="")])
        summary = outcome_layers.layer_summary(
            self.dir, run_id="g1",
            window={"start": "2026-12-01", "end": "2026-12-07"})
        self.assertEqual(0, summary["L3"]["signup"])
        self.assertFalse(summary["L3"]["available"])
        self.assertEqual("NO_EVENTS_IN_WINDOW", summary["L3"]["state"])
        self.assertIn("来源已接入", summary["L3"]["text"])


class JourneyReadModelTests(TmpDir):
    def _contacts(self, rows):
        path = self.dir / "contacts.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def test_channel_prefixed_ref_resolves_contact(self):
        self._contacts([{"id": "contact-0001", "name": "Alice",
                         "platforms": {"x": "111"},
                         "state": {"dnc": True, "bounced": None}}])
        journey = outcome_layers.prospect_journey(self.dir,
                                                  prospect_ref="x:111")
        self.assertEqual("contact-0001", journey["prospect_ref"])
        self.assertEqual("dnc", journey["suppression_state"])
        self.assertEqual("suppressed", journey["current_state"])

    def test_bounced_contact_reports_bounced(self):
        self._contacts([{"id": "contact-0002", "name": "Bob",
                         "platforms": {"email": "b@example.com"},
                         "state": {"dnc": False, "bounced": True}}])
        journey = outcome_layers.prospect_journey(
            self.dir, prospect_ref="b@example.com")
        self.assertEqual("bounced", journey["suppression_state"])
        self.assertIn("退信", journey["next_allowed_action"])

    def test_unknown_prospect_is_empty_not_invented(self):
        journey = outcome_layers.prospect_journey(
            self.dir, prospect_ref="nobody@example.com")
        self.assertEqual([], journey["contacted_actions"])
        self.assertEqual([], journey["product_events"])
        self.assertEqual([], journey["responses"])
        self.assertEqual("discovered", journey["current_state"])
        self.assertIsNone(journey["evidence_updated_at"])

    def test_journey_is_read_only(self):
        self._contacts([{"id": "contact-0003", "name": "Cara",
                         "platforms": {"email": "c@example.com"},
                         "state": {"dnc": False, "bounced": None}}])
        before = sorted(p.name for p in self.dir.iterdir())
        outcome_layers.prospect_journey(self.dir,
                                        prospect_ref="c@example.com")
        self.assertEqual(before, sorted(p.name for p in self.dir.iterdir()))

    def test_unparseable_suppression_source_is_unknown(self):
        (self.dir / "contacts.jsonl").write_text("{broken\n", encoding="utf-8")
        journey = outcome_layers.prospect_journey(
            self.dir, prospect_ref="anyone@example.com")
        self.assertEqual("unknown", journey["suppression_state"])
        self.assertIn("抑制源", journey["next_allowed_action"])


class ResultCardConsistencyTests(TmpDir):
    def _run(self):
        return {"id": "g1", "started": "2026-11-01", "status": "active",
                "goal": {"metric": "followers", "name": "社媒关注者增长",
                         "baseline": 120, "target": 20,
                         "target_kind": "delta", "target_text": "从 120 到 140"},
                "plan": {"actions_per_week": 7},
                "telemetry": {"weeks": {"2026-11-01": {"human_minutes": 40}}}}

    def test_business_funnel_follows_l3_after_qualification(self):
        from core import result_card
        self.import_rows([_row()])
        card = result_card.build(self.dir, self._run(), "2026-11-01",
                                 "2026-11-07")
        self.assertEqual(1, card["business"]["funnel"]["activation"])
        outcome_layers.set_event_qualification(
            self.dir, "e-0001", qualifies=False, by="roy", note="不算")
        card = result_card.build(self.dir, self._run(), "2026-11-01",
                                 "2026-11-07")
        self.assertEqual(0, card["business"]["funnel"]["activation"])
        self.assertEqual(0, card["layers"]["L3"]["activation"])
        self.assertEqual(40, card["human_minutes"])

    def test_goal_status_met_uses_readings_not_activity(self):
        from core import result_card
        ledger = Ledger(self.dir / "ledger.jsonl")
        for i in range(3):
            ledger.append(channel="x", action="reply", target=f"tweet:{i}",
                          result="committed", campaign="g1",
                          ts="2026-11-03T09:00:00", ref=f"r{i}")
        OutcomeLedger(self.dir).add_count(
            metric="x_followers", value=145, window="2026-11-06",
            source_ref="seed", approximated=True)
        card = result_card.build(self.dir, self._run(), "2026-11-01",
                                 "2026-11-07")
        self.assertEqual("met", card["goal_status"]["state"])
        text = result_card.render(card)
        self.assertIn("主目标", text)
        self.assertIn("证据覆盖与盲点", text)
        self.assertIn("未接入", text)  # L3 仍如实说没接业务事件

    def test_render_never_writes_zero_activation(self):
        from core import result_card
        ledger = Ledger(self.dir / "ledger.jsonl")
        ledger.append(channel="x", action="reply", target="tweet:0",
                      result="committed", campaign="g1",
                      ts="2026-11-03T09:00:00", ref="r0")
        text = result_card.render(
            result_card.build(self.dir, self._run(), "2026-11-01",
                              "2026-11-07"))
        self.assertIn("激活事件未接入", text)
        self.assertNotIn("activation 0", text)


class AttributionChainTests(TmpDir):
    def _touch(self, campaign, ref, ts="2026-11-02T09:00:00"):
        Ledger(self.dir / "ledger.jsonl").append(
            channel="email", action="message", target="u1@example.com",
            result="committed", campaign=campaign, ts=ts, ref=ref)

    def test_run_campaign_and_daily_queue_campaign_are_one_chain(self):
        self._touch("g1-20261103", "ref-1")
        self.import_rows([_row()])  # event campaign_id = g1
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        row = summary["attribution"]["activations"][0]
        self.assertEqual("direct_receipt", row["attribution"])
        self.assertEqual("not_claimed", row["causal_credit"])
        self.assertEqual(1, len(row["touches"]))

    def test_other_campaign_touch_is_only_assisted(self):
        self._touch("g2-20261103", "ref-2")
        self.import_rows([_row()])
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        self.assertEqual("assisted_observation",
                         summary["attribution"]["activations"][0]["attribution"])


class DegradedReadTests(TmpDir):
    def test_corrupt_event_file_is_called_out_not_shown_as_zero(self):
        outcomes = self.dir / "outcomes"
        outcomes.mkdir(parents=True)
        (outcomes / "product_events.jsonl").write_text("{broken\n",
                                                       encoding="utf-8")
        from core import result_card
        from core.grow_v2 import GrowRunV2
        run = {"id": "g1", "started": "2026-11-01", "status": "active",
               "goal": {"metric": "followers", "target": 20,
                        "baseline": 120, "target_kind": "delta"},
               "plan": {"actions_per_week": 7}}
        layers = GrowRunV2._layers(self.dir, run, "2026-11-03")
        self.assertTrue(layers["degraded"])
        self.assertIn("读取失败", layers["L3"]["text"])
        card = result_card.build(self.dir, run, "2026-11-01", "2026-11-07")
        text = result_card.render(card)
        self.assertIn("三层账本读取失败", text)
        self.assertIn("修三层账本", card["recommendation"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ, MKT_HOME=str(self.home))
        self.events = Path(self.tmp.name) / "events.json"
        self.events.write_text(json.dumps([EVENT], ensure_ascii=False),
                               encoding="utf-8")

    def run_mkt(self, *args, check=True):
        proc = subprocess.run([sys.executable, str(MKT), *args],
                              cwd=ROOT, env=self.env, text=True,
                              capture_output=True)
        if check and proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed ({proc.returncode})"
                      f"\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return proc

    def test_import_is_dry_run_by_default(self):
        proc = self.run_mkt("grow", "events", "import", "--from",
                            str(self.events), "--allowed-source",
                            "product_export", "--by", "roy")
        self.assertIn("预演（未写入）", proc.stdout)
        self.assertFalse((self.home / "outcomes" / "product_events.jsonl").exists())

    def test_import_apply_then_layers_and_journey_json(self):
        self.run_mkt("grow", "events", "import", "--from", str(self.events),
                     "--allowed-source", "product_export", "--by", "roy",
                     "--apply")
        layers = json.loads(self.run_mkt("grow", "layers", "g1", "--json").stdout)
        self.assertEqual(1, layers["L3"]["activation"])
        journey = json.loads(
            self.run_mkt("grow", "journey", "u1@example.com", "--json").stdout)
        self.assertEqual("activated", journey["current_state"])
        listing = self.run_mkt("grow", "events", "list").stdout
        self.assertIn("e-0001", listing)

    def test_import_requires_signature(self):
        proc = self.run_mkt("grow", "events", "import", "--from",
                            str(self.events), "--allowed-source",
                            "product_export", check=False)
        self.assertEqual(2, proc.returncode)
        self.assertIn("署名", proc.stdout)

    def test_qualify_is_dry_run_by_default(self):
        self.run_mkt("grow", "events", "import", "--from", str(self.events),
                     "--allowed-source", "product_export", "--by", "roy",
                     "--apply")
        proc = self.run_mkt("grow", "qualify", "e-0001", "--no", "--by",
                            "roy", "--note", "内部测试账号")
        self.assertIn("预演", proc.stdout)
        self.assertEqual([], outcome_layers.read_qualifications(self.home))
        self.run_mkt("grow", "qualify", "e-0001", "--no", "--by", "roy",
                     "--note", "内部测试账号", "--apply")
        self.assertEqual(1, len(outcome_layers.read_qualifications(self.home)))

    def test_qualify_rejects_missing_verdict(self):
        proc = self.run_mkt("grow", "qualify", "e-0001", "--by", "roy",
                            "--note", "x", check=False)
        self.assertEqual(2, proc.returncode)
        self.assertIn("--yes", proc.stdout)


class OutreachAdmissionHelperTests(unittest.TestCase):
    def test_none_when_untouched_and_false_when_incomplete(self):
        self.assertEqual({"outreach_ready": None, "outreach_missing": []},
                         outcome_layers.outreach_admission({"target": "x"}))
        partial = outcome_layers.outreach_admission(
            {"their_problem": "问题", "why_we_help": "理由"})
        self.assertFalse(partial["outreach_ready"])
        self.assertEqual(["expected_action"], partial["outreach_missing"])
        self.assertTrue(outcome_layers.is_outreach_ready({"outreach_ready": None}))
        self.assertFalse(outcome_layers.is_outreach_ready({"outreach_ready": False}))


if __name__ == "__main__":
    unittest.main()
