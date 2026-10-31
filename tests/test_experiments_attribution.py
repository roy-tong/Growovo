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
        self.ledger.add_event(subject="v1", event_type="activation", channel="blog")
        out = self.ledger.attribution()
        self.assertEqual(["last_touch", "first_touch"], out["models"])
        row = out["subjects"][0]
        self.assertEqual("show-hn", row["first_touch"])
        self.assertEqual("blog", row["last_touch"])
        self.assertIn("因果", out["note"])  # 免责必须在场


if __name__ == "__main__":
    unittest.main()
