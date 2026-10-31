#!/usr/bin/env python3
"""实验引擎与效果归因的离线单测（零网络、零账号）。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core import experiments
from core.attribution import AttributionError, OutcomeLedger


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name)

    def test_preregistration_required_before_start(self):
        with self.assertRaises(experiments.ExperimentError):
            experiments.new_experiment(
                self.data, exp_id="e1", hypothesis="h", metric="m",
                decision_rule="", start="2026-11-01", end="2026-11-14")

    def test_lifecycle_and_human_signed_conclusion(self):
        exp = experiments.new_experiment(
            self.data, exp_id="d14-gate", hypothesis="P0 端到端人工 ≤12h",
            metric="manual_hours", decision_rule="D14 人工 ≤12h 为 met；>20h 为 not_met",
            start="2026-10-31", end="2026-11-14", agg="sum", owner="Roy")
        self.assertEqual("planned", exp["status"])
        experiments.start_experiment(self.data, "d14-gate")
        with self.assertRaises(experiments.ExperimentError):
            experiments.record_observation(self.data, "d14-gate", note="无出处数据")  # 必须带出处或标近似
        experiments.record_observation(self.data, "d14-gate", note="第 1 天",
                                       data={"manual_hours": 3}, evidence_ref="dogfood/askovo/ledger.jsonl")
        experiments.record_observation(self.data, "d14-gate", note="近似计数",
                                       data={"manual_hours": 2}, approximated=True)
        exp = experiments.conclude(self.data, "d14-gate", result="met", decided_by="Roy")
        self.assertEqual("analyzed", exp["status"])
        self.assertEqual(5, exp["conclusion"]["aggregate"])  # sum 聚合
        self.assertEqual("Roy", exp["conclusion"]["decided_by"])

    def test_conclusion_requires_signature(self):
        experiments.new_experiment(
            self.data, exp_id="e2", hypothesis="h", metric="m", decision_rule="r",
            start="2026-11-01", end="2026-11-07")
        with self.assertRaises(experiments.ExperimentError):
            experiments.conclude(self.data, "e2", result="met", decided_by="")

    def test_observation_freeze_outside_running(self):
        """观察只在 running 期接受：启动前/判定后追加都是破坏预注册纪律。"""
        experiments.new_experiment(
            self.data, exp_id="e3", hypothesis="h", metric="m", decision_rule="r",
            start="2026-11-01", end="2026-11-07")
        with self.assertRaises(experiments.ExperimentError):   # planned 拒
            experiments.record_observation(self.data, "e3", note="提前观察",
                                           approximated=True)
        experiments.start_experiment(self.data, "e3")
        experiments.record_observation(self.data, "e3", note="正常观察",
                                       approximated=True)
        experiments.conclude(self.data, "e3", result="met", decided_by="Roy")
        with self.assertRaises(experiments.ExperimentError):   # analyzed 拒
            experiments.record_observation(self.data, "e3", note="事后凑数",
                                           approximated=True)
        with self.assertRaises(experiments.ExperimentError):   # 已有结论不改判
            experiments.conclude(self.data, "e3", result="not_met", decided_by="Roy")

    def test_prereg_fingerprint_detects_tampering(self):
        experiments.new_experiment(
            self.data, exp_id="e4", hypothesis="h", metric="m", decision_rule="原规则",
            start="2026-11-01", end="2026-11-07")
        experiments.start_experiment(self.data, "e4")
        # 手改判定规则（模拟绕过 API 直接改文件）
        path = self.data / "experiments" / "e4.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["decision_rule"] = "事后改的规则"
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(experiments.ExperimentError):
            experiments.record_observation(self.data, "e4", note="观察",
                                           approximated=True)
        with self.assertRaises(experiments.ExperimentError):
            experiments.conclude(self.data, "e4", result="met", decided_by="Roy")

    def test_stop_records_change_log(self):
        experiments.new_experiment(
            self.data, exp_id="e5", hypothesis="h", metric="m", decision_rule="r",
            start="2026-11-01", end="2026-11-07")
        experiments.start_experiment(self.data, "e5")
        exp = experiments.stop_experiment(self.data, "e5", reason="流量中断")
        self.assertEqual(2, len(exp["change_log"]))           # start + stop
        self.assertEqual("stop", exp["change_log"][-1]["action"])
        self.assertEqual("流量中断", exp["change_log"][-1]["reason"])
        with self.assertRaises(experiments.ExperimentError):   # stopped 拒观察
            experiments.record_observation(self.data, "e5", note="x", approximated=True)


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ledger = OutcomeLedger(Path(self._tmp.name))

    def test_funnel_counts_distinct_subjects_with_denominators(self):
        self.ledger.add_event(subject="v1", event_type="signal", channel="show-hn")
        self.ledger.add_event(subject="v1", event_type="signal", channel="blog")
        self.ledger.add_event(subject="v2", event_type="signal", channel="show-hn")
        self.ledger.add_event(subject="v1", event_type="signup", channel="show-hn", approximated=True)
        funnel = self.ledger.funnel()
        stages = {s["stage"]: s for s in funnel["funnel"]}
        self.assertEqual(2, stages["signal"]["subjects"])  # 去重
        self.assertEqual(1, stages["signup"]["subjects"])
        self.assertEqual(0.5, stages["signup"]["conversion_from_prev"])
        self.assertEqual(["v1"], funnel["approximated_signup_subjects"])  # 近似口径单列

    def test_value_events_reject_approximation(self):
        with self.assertRaises(AttributionError):
            self.ledger.add_event(subject="v1", event_type="paid", approximated=True)
        with self.assertRaises(AttributionError):
            self.ledger.add_event(subject="v1", event_type="activation", approximated=True)

    def test_value_events_require_source_and_verification(self):
        """activation/paid 必须带 source_ref 与 verification(method,ref)。"""
        with self.assertRaises(AttributionError):   # 无来源
            self.ledger.add_event(subject="v1", event_type="paid",
                                   verification={"method": "payment", "ref": "o1"})
        with self.assertRaises(AttributionError):   # 无核验
            self.ledger.add_event(subject="v1", event_type="paid",
                                  source_ref="Stripe 后台")
        with self.assertRaises(AttributionError):   # 核验不完整
            self.ledger.add_event(subject="v1", event_type="activation",
                                  source_ref="产品后台",
                                  verification={"method": "产品后台"})
        rec = self.ledger.add_event(
            subject="v1", event_type="paid", source_ref="Stripe 后台",
            verification={"method": "payment-processor", "ref": "order-001"})
        self.assertEqual("verified", rec["verification"]["status"])
        self.assertEqual("order-001", rec["verification"]["ref"])

    def test_funnel_unifies_identity_via_contacts(self):
        """同一人的平台别名经 contacts.jsonl 归一，不重复计数。"""
        (self.ledger.data_dir / "contacts.jsonl").write_text(json.dumps({
            "id": "contact-0001", "name": "Alice",
            "platforms": {"github": "alice-gh", "email": "alice@example.com"},
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        self.ledger.add_event(subject="alice-gh", event_type="signal", channel="github")
        self.ledger.add_event(subject="contact-0001", event_type="signup", channel="blog")
        funnel = self.ledger.funnel()
        stages = {s["stage"]: s for s in funnel["funnel"]}
        self.assertEqual(1, stages["signal"]["subjects"])   # 归一后 1 人
        self.assertEqual(1, stages["signal"]["raw_subjects"])
        self.assertEqual(1, stages["signup"]["subjects"])
        self.assertIn("contact-0001", funnel["identity"]["merged_canonical_subjects"])
        # 归因口径同样按归一主体聚合
        row = self.ledger.attribution()["subjects"][0]
        self.assertEqual("contact-0001", row["subject"])
        self.assertEqual("github", row["first_touch"])

    def test_funnel_flags_sequence_anomalies_and_unverified_events(self):
        self.ledger.add_event(subject="v9", event_type="signal", channel="show-hn")
        self.ledger.add_event(subject="v9", event_type="signup", channel="show-hn",
                              approximated=True)
        # 手写存量记录：paid 早于 signup（时序倒挂）且缺 verification
        self.ledger.path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "kind": "event", "ts": "2026-01-01T00:00:00", "subject": "v9",
                "event_type": "paid", "channel": "show-hn", "touch": "",
                "source_ref": "old-note", "approximated": False,
            }, ensure_ascii=False) + "\n")
        funnel = self.ledger.funnel()
        self.assertEqual(1, len(funnel["sequence_anomalies"]))
        self.assertIn("早于", funnel["sequence_anomalies"][0]["detail"])
        self.assertEqual(1, len(funnel["unverified_value_events"]))
        self.assertEqual("paid", funnel["unverified_value_events"][0]["event_type"])
        stages = {s["stage"]: s for s in funnel["funnel"]}
        self.assertEqual(1, stages["paid"]["subjects"])     # 单列披露但不静默剔除

    def test_counts_require_source_and_window(self):
        with self.assertRaises(AttributionError):
            self.ledger.add_count(metric="clones", value=10, window="2026-11 W1", source_ref="")
        rec = self.ledger.add_count(metric="clones", value=10, window="2026-11 W1",
                                    source_ref="GitHub Insights /roy-tong/askovo")
        self.assertTrue(rec["approximated"])
        self.assertEqual(1, len(self.ledger.funnel()["counts"]))

    def test_attribution_reports_models_not_causality(self):
        self.ledger.add_event(subject="v1", event_type="signal", channel="show-hn")
        self.ledger.add_event(subject="v1", event_type="signal", channel="blog")
        self.ledger.add_event(subject="v1", event_type="activation", channel="blog",
                              source_ref="产品后台",
                              verification={"method": "product-db", "ref": "act-001"})
        out = self.ledger.attribution()
        self.assertEqual(["last_touch", "first_touch"], out["models"])
        row = out["subjects"][0]
        self.assertEqual("show-hn", row["first_touch"])
        self.assertEqual("blog", row["last_touch"])
        self.assertIn("因果", out["note"])  # 免责必须在场


if __name__ == "__main__":
    unittest.main()
