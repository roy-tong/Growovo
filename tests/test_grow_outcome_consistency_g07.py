"""G-07 反例冻结：业务结果只能有一个有效口径（事件事实 + 最新资格投影）。

先于修复编写。修复前，``OutcomeLedger`` 里的投影行不会随人工资格改动一起
撤回：``set_event_qualification(event_id, qualifies=False)`` 只追加审计记录，
漏斗 / journey / 周卡 / 学习样本仍读原始行，于是同一份数据给出两个有效付费
人数（L3 说 0，漏斗说 1）。

冻结的契约：

1. 撤销一条已核验 ``paid`` 后，L3、漏斗、journey、周卡、日卡与学习样本的
   有效付费人数**同时**变成 0；再标回 ``qualifies=True`` 全部回到 1
   （幂等、可逆）。
2. 原始事件与撤销审计都保留：台账行不删、导入记录一个字节不改，
   理由/署名/时间可从 ``mkt grow qualify --list --event <id>`` 看到。
3. 首次/重复判定按过滤后的事实算：撤销一条首次激活后，该主体下一次真实
   激活就是首次，不能被记成「重复激活」。

全部离线：合成 fixture，零网络、零真实发送（工单 G-07，P1）。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import controlled_learning, grow_learning, outcome_layers, result_card
from core.attribution import OutcomeLedger
from core.execution_policy import authorize
from core.grow_v2 import GrowRunV2

ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"
ACTIONS = ["x:reply", "email:message"]
EVIDENCE_KEYS = ("event_id", "event_type", "subject_id", "source_system",
                 "source_ref", "amount", "currency", "payment_ref", "paid_at")


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


def evidence(row):
    """测试侧冻结的凭证 hash 口径：绑定事件身份 + 结构化支付字段。"""
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
    if row["verified_by"] and not row.get("evidence_hash"):
        row["evidence_hash"] = evidence(row)
    return row


def paid_event(**kw):
    row = {
        "event_id": "p-0001",
        "event_type": "paid",
        "subject_id": "buyer-1",
        "occurred_at": "2026-11-03T09:00:00",
        "source_system": "product_export",
        "source_ref": "stripe:pi_123",
        "campaign_id": "g1",
        "verified_by": "stripe",
        "amount": "49.00",
        "currency": "USD",
        "payment_ref": "pi_123",
        "paid_at": "2026-11-03T08:59:00",
        "evidence_hash": "",
    }
    row.update(kw)
    row["evidence_hash"] = kw.get("evidence_hash") or evidence(row)
    return row


def funnel_subjects(payload, stage):
    for row in payload.get("funnel") or []:
        if row.get("stage") == stage:
            return row.get("subjects")
    return None


class G07Base(unittest.TestCase):
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

    def _import(self, rows):
        return outcome_layers.import_events(
            self.dir, self._json_file(rows),
            allowed_sources={"product_export"}, imported_by="tester")

    def _revoke(self, event_id="p-0001", note="内部测试交易，不算付费"):
        return outcome_layers.set_event_qualification(
            self.dir, event_id, qualifies=False, by="roy", note=note)

    def _restore(self, event_id="p-0001"):
        return outcome_layers.set_event_qualification(
            self.dir, event_id, qualifies=True, by="roy", note="复核后算回")

    def _run_data(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _weekly_card(self):
        return result_card.build(self.dir, self._run_data(),
                                 "2026-11-01", "2026-11-07")

    def _daily_card(self):
        return self.run.daily(self.dir, on="2026-11-03")

    def _learn(self):
        return grow_learning.analyze(self.dir, "g1", self._run_data())

    def _views(self):
        """同一份数据的六个对外读数：必须同口径。"""
        layers = outcome_layers.layer_summary(self.dir, run_id="g1")
        journey = outcome_layers.prospect_journey(
            self.dir, prospect_ref="buyer-1")
        return {
            "L3": layers["L3"]["paid"],
            "funnel": funnel_subjects(OutcomeLedger(self.dir).funnel("g1"),
                                      "paid"),
            "journey": journey["qualified_counts"]["paid"],
            "weekly": self._weekly_card()["business"]["funnel"]["paid"],
            "daily": self._daily_card()["layers"]["L3"]["paid"],
            "learn": self._learn()["business"]["paid"],
        }


class UnifiedQualificationTests(G07Base):
    def test_revoked_paid_is_zero_in_every_surface(self):
        self._import([paid_event()])
        self.assertEqual({"L3": 1, "funnel": 1, "journey": 1,
                          "weekly": 1, "daily": 1, "learn": 1},
                         self._views())
        self._revoke()
        self.assertEqual({"L3": 0, "funnel": 0, "journey": 0,
                          "weekly": 0, "daily": 0, "learn": 0},
                         self._views())

    def test_business_stages_all_move_together(self):
        self._import([
            event(event_id="s-1", event_type="signup",
                  subject_id="user-a", occurred_at="2026-11-02T10:00:00",
                  verified_by="", evidence_hash=""),
            event(event_id="a-1", event_type="activation",
                  subject_id="user-a", occurred_at="2026-11-03T10:00:00"),
            paid_event(),
        ])
        before = outcome_layers.layer_summary(self.dir, run_id="g1")["L3"]
        self.assertEqual((1, 1, 1),
                         (before["signup"], before["activation"],
                          before["paid"]))
        self._revoke("a-1", note="测试账号激活，不算")
        after = outcome_layers.layer_summary(self.dir, run_id="g1")["L3"]
        self.assertEqual(0, after["activation"])
        self.assertEqual(1, after["signup"])
        self.assertEqual(1, after["paid"])
        self.assertEqual(1, len(after["disqualified"]))
        self.assertEqual("roy", after["disqualified"][0]["by"])

    def test_restore_qualification_returns_to_one_and_is_idempotent(self):
        self._import([paid_event()])
        self._revoke()
        self.assertEqual(0, self._views()["funnel"])
        self._restore()
        self._restore()
        self.assertEqual({"L3": 1, "funnel": 1, "journey": 1,
                          "weekly": 1, "daily": 1, "learn": 1},
                         self._views())
        audits = outcome_layers.read_qualifications(self.dir)
        self.assertEqual([False, True, True],
                         [row["qualifies"] for row in audits])

    def test_raw_event_and_revocation_audit_survive_the_revocation(self):
        self._import([paid_event()])
        product_bytes = (self.dir / "outcomes" / "product_events.jsonl").read_bytes()
        self._revoke()
        # 原事件一字不改，台账行也不删（append-only）
        self.assertEqual(product_bytes, (self.dir / "outcomes"
                                         / "product_events.jsonl").read_bytes())
        ledger_rows = OutcomeLedger(self.dir).records("g1")
        self.assertEqual(1, len([r for r in ledger_rows
                                 if r.get("event_type") == "paid"]))
        audits = outcome_layers.read_qualifications(self.dir)
        self.assertEqual("roy", audits[-1]["by"])
        self.assertEqual("内部测试交易，不算付费", audits[-1]["note"])
        self.assertTrue(audits[-1]["ts"])


class FirstAndRepeatAfterRevocationTests(G07Base):
    def test_revoking_first_activation_does_not_mark_next_one_as_repeat(self):
        self._import([
            event(event_id="a-1", subject_id="u-9",
                  occurred_at="2026-11-02T10:00:00",
                  source_ref="app:activation:9a"),
            event(event_id="a-2", subject_id="u-9",
                  occurred_at="2026-11-03T10:00:00",
                  source_ref="app:activation:9b"),
        ])
        before = outcome_layers.layer_summary(self.dir, run_id="g1")["L3"]
        self.assertEqual(1, before["activation"])
        self.assertEqual(1, len(before["repeated_activations"]))
        self._revoke("a-1")
        after = outcome_layers.layer_summary(self.dir, run_id="g1")["L3"]
        self.assertEqual(1, after["activation"])
        self.assertEqual([], after["repeated_activations"])
        # 首次判定落在剩下那条真实激活上，撤销的那条不能占着「首次」位置
        summary = outcome_layers.layer_summary(self.dir, run_id="g1")
        first = summary["attribution"]["activations"][0]
        self.assertEqual("a-2", first["event_id"])


class FalsificationMetricsTests(G07Base):
    def test_revoked_event_leaves_qualified_prospect_count(self):
        self._import([event(event_id="a-1", subject_id="u-9")])
        metrics = controlled_learning.falsification_metrics(
            self.dir, run_id="g1",
            window={"start": "2026-11-01", "end": "2026-11-07"})
        self.assertEqual(1, metrics["unique_qualified_prospects"])
        self._revoke("a-1", note="内部测试账号")
        metrics = controlled_learning.falsification_metrics(
            self.dir, run_id="g1",
            window={"start": "2026-11-01", "end": "2026-11-07"})
        self.assertEqual(0, metrics["unique_qualified_prospects"])
        self.assertEqual(0, metrics["independently_observed_activations"])


class QualifyAuditCliTests(unittest.TestCase):
    """CLI 契约：撤销理由、署名、时间与原始事件都能查。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ, MKT_HOME=str(self.home))
        self.events = Path(self.tmp.name) / "events.json"
        self.events.write_text(json.dumps([paid_event()], ensure_ascii=False),
                               encoding="utf-8")

    def run_mkt(self, *args, check=True):
        proc = subprocess.run([sys.executable, str(MKT), *args],
                              cwd=ROOT, env=self.env, text=True,
                              capture_output=True)
        if check and proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed ({proc.returncode})"
                      f"\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return proc

    def _import(self):
        self.run_mkt("grow", "events", "import", "--from", str(self.events),
                     "--allowed-source", "product_export", "--by", "roy",
                     "--apply")

    def test_qualify_list_shows_event_reason_and_author(self):
        self._import()
        self.run_mkt("grow", "qualify", "p-0001", "--no", "--by", "roy",
                     "--note", "内部测试交易，不算付费", "--apply")
        listing = self.run_mkt("grow", "qualify", "--list",
                               "--event", "p-0001").stdout
        for text in ("p-0001", "buyer-1", "内部测试交易，不算付费", "roy",
                     "不计入"):
            self.assertIn(text, listing)

    def test_qualify_list_json_keeps_every_change(self):
        self._import()
        self.run_mkt("grow", "qualify", "p-0001", "--no", "--by", "roy",
                     "--note", "内部测试交易", "--apply")
        self.run_mkt("grow", "qualify", "p-0001", "--yes", "--by", "roy",
                     "--note", "复核后算回", "--apply")
        report = json.loads(self.run_mkt(
            "grow", "qualify", "--list", "--event", "p-0001",
            "--json").stdout)
        self.assertEqual("p-0001", report["event_id"])
        self.assertEqual("paid", report["event"]["event_type"])
        self.assertTrue(report["qualifies"])
        self.assertEqual([False, True],
                         [row["qualifies"] for row in report["history"]])
        self.assertEqual("内部测试交易", report["history"][0]["note"])

    def test_outcome_funnel_cli_hides_revoked_rows(self):
        self._import()
        payload = json.loads(self.run_mkt(
            "outcome", "funnel", "--campaign", "g1").stdout)
        self.assertEqual(1, funnel_subjects(payload, "paid"))
        self.run_mkt("grow", "qualify", "p-0001", "--no", "--by", "roy",
                     "--note", "内部测试交易", "--apply")
        payload = json.loads(self.run_mkt(
            "outcome", "funnel", "--campaign", "g1").stdout)
        self.assertEqual(0, funnel_subjects(payload, "paid"))
        self.assertEqual(1, len(payload["disqualified"]))
        self.assertEqual("内部测试交易", payload["disqualified"][0]["note"])


if __name__ == "__main__":
    unittest.main()
