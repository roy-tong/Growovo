#!/usr/bin/env python3
"""Scorecard, optimization proposals and the persisted schedule."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import optimization, scheduler, scorecard  # noqa: E402
from core.ledger import Ledger  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name) / "data"
        self.data.mkdir()
        self.ledger = Ledger(self.data / "ledger.jsonl")

    def record(self, channel, action, result, campaign="c1", evidence=""):
        self.ledger.append(channel=channel, action=action, target="t", result=result,
                           campaign=campaign, evidence=evidence)


class ScorecardTests(Base):
    def test_rates_and_warnings_are_descriptive(self):
        for _ in range(6):
            self.record("github", "reply", "committed", evidence="shot")
        for _ in range(2):
            self.record("github", "reply", "indeterminate")
        self.record("github", "reply", "failed")
        self.record("github", "reply", "dry-run")
        payload = scorecard.build(self.data)
        group = payload["groups"][0]
        self.assertEqual(9, group["attempted"])
        self.assertEqual(6, group["committed"])
        self.assertEqual(2, group["unknown"])
        self.assertAlmostEqual(6 / 9, group["committed_rate"], places=4)
        self.assertAlmostEqual(2 / 9, group["unknown_rate"], places=4)
        self.assertEqual(1.0, group["evidence_rate"])
        ids = {warning["id"] for warning in payload["warnings"]}
        self.assertIn("unknown_results", ids)
        self.assertIn("failed_results", ids)
        self.assertIn("no_business_outcomes", ids)
        self.assertIn("描述", payload["boundary"])

    def test_dry_run_only_is_called_out(self):
        self.record("email", "message", "dry-run")
        payload = scorecard.build(self.data)
        ids = {warning["id"] for warning in payload["warnings"]}
        self.assertIn("dry_run_only", ids)


class OptimizationTests(Base):
    def test_unknown_results_are_the_first_fix(self):
        self.record("github", "reply", "indeterminate")
        card = scorecard.build(self.data)
        payload = optimization.propose(card)
        self.assertEqual("reconcile-unknown", payload["proposals"][0]["id"])
        self.assertEqual("high", payload["proposals"][0]["severity"])
        self.assertTrue(all(item["automatic_apply"] is False for item in payload["proposals"]))
        self.assertTrue(all(item["requires_human"] is True for item in payload["proposals"]))

    def test_channel_mix_proposal_uses_descriptive_rates(self):
        for _ in range(8):
            self.record("github", "reply", "committed")
        for _ in range(8):
            self.record("reddit", "reply", "failed")
        card = scorecard.build(self.data)
        payload = optimization.propose(card)
        shift = [item for item in payload["proposals"] if item["id"] == "shift-volume"]
        self.assertTrue(shift, json.dumps(payload, ensure_ascii=False))
        self.assertIn("不是渠道效果证明", shift[0]["statement"])


class SchedulerTests(Base):
    def make_queue(self, name="q1"):
        path = self.data / "queues" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"campaign": name, "channel": "github", "action": "reply",
                                    "items": [{"id": "1", "target": "o/r#1", "body": "hi"}]}),
                        encoding="utf-8")
        return path

    def test_tick_runs_one_due_wave_and_advances(self):
        queue = self.make_queue()
        start = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
        schedule = scheduler.add(self.data, schedule_id="s1", queues=[str(queue)],
                                 interval_minutes=60, go=False, start_at=start)
        calls = []
        def runner(path, **kwargs):
            calls.append((str(path), kwargs.get("go")))
            return [{"result": "dry-run"}]
        early = scheduler.tick(self.data, now=start - timedelta(minutes=1), runner=runner)
        self.assertEqual(0, early["due"])
        first = scheduler.tick(self.data, now=start, runner=runner)
        self.assertEqual(1, first["due"])
        self.assertEqual(1, calls.__len__())
        loaded = scheduler.load(self.data, "s1")
        self.assertEqual(1, loaded["waves_done"])
        self.assertEqual("active", loaded["status"])
        again = scheduler.tick(self.data, now=start + timedelta(minutes=10), runner=runner)
        self.assertEqual(0, again["due"])

    def test_pause_skips_without_running(self):
        queue = self.make_queue()
        start = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
        scheduler.add(self.data, schedule_id="s1", queues=[str(queue)], interval_minutes=60,
                      start_at=start)
        (self.data / "state").mkdir(exist_ok=True)
        (self.data / "state" / "PAUSED").write_text("incident", encoding="utf-8")
        calls = []
        result = scheduler.tick(self.data, now=start,
                                runner=lambda *a, **k: calls.append(1) or [])
        self.assertEqual([], calls)
        self.assertEqual("paused", result["skipped"][0]["reason"])

    def test_unknown_result_stops_the_schedule(self):
        queue = self.make_queue()
        start = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
        scheduler.add(self.data, schedule_id="s1", queues=[str(queue)], interval_minutes=60,
                      start_at=start)
        result = scheduler.tick(self.data, now=start,
                                runner=lambda *a, **k: [{"result": "indeterminate"}])
        self.assertEqual("needs_review", result["ran"][0]["outcome"])
        loaded = scheduler.load(self.data, "s1")
        self.assertEqual("needs_review", loaded["status"])
        self.assertIn("unknown", json.dumps(loaded))
        skipped = scheduler.tick(self.data, now=start + timedelta(minutes=120))
        self.assertEqual(0, skipped["due"])
        resumed = scheduler.resume(self.data, "s1", note="checked the platform", by="tester")
        self.assertEqual("active", resumed["status"])

    def test_interrupted_wave_is_detected(self):
        queue = self.make_queue()
        start = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
        schedule = scheduler.add(self.data, schedule_id="s1", queues=[str(queue)],
                                 interval_minutes=60, start_at=start)
        schedule["status"] = "running"
        scheduler.save(self.data, schedule)
        result = scheduler.tick(self.data, now=start)
        self.assertEqual("interrupted", result["skipped"][0]["reason"])
        self.assertEqual("needs_review", scheduler.load(self.data, "s1")["status"])


if __name__ == "__main__":
    unittest.main()
