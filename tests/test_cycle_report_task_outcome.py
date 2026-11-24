#!/usr/bin/env python3
"""The run-level task_outcome projection stays separate from legacy status fields."""
import json
import tempfile
import unittest
from pathlib import Path

from core import cycle_report


def wave(wave_id, *, planned, supply, results, ok=True, deferred=None):
    return {
        "ok": ok,
        "wave": wave_id,
        "campaign": "night-task-outcome-" + wave_id,
        "night": "2026-11-22",
        "dry": False,
        "planned": planned,
        "supply": {
            key: {
                "configured": values[0], "effective": values[0],
                "scheduled": values[1], "gap": values[0] - values[1],
            }
            for key, values in supply.items()
        },
        "results": results,
        "deferred": deferred or [],
        "stopped": {},
        "preflight_skipped": {},
        "inbox": {"collected": 0, "pending_text": 0, "errors": {}},
        "content": {
            "reply_artifact_floor_met": True,
            "focus_floor_met": True,
            "x_publish_artifact_floor_met": True,
        },
    }


def result(channel, action, state, target="target"):
    return {"channel": channel, "action": action, "target": target,
            "result": state, "ledger_result": state, "error": ""}


class CycleReportTaskOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.directory = self.data / "state" / "engagement-2026-11-22"
        self.directory.mkdir(parents=True)

    def write(self, wave_id, payload):
        (self.directory / ("wave-%s.json" % wave_id)).write_text(
            json.dumps(payload), encoding="utf-8")

    def test_complete_wave_is_resolved(self):
        self.write("01-1905", wave(
            "01-1905", planned=1, supply={"x/publish": (1, 1)},
            results=[result("x", "publish", "committed")]))
        report = cycle_report.build_report(self.data, night="2026-11-22")
        self.assertEqual("complete", report["status"])
        self.assertEqual("resolved", report["task_outcome"]["outcome"])

    def test_mixed_wave_is_partial(self):
        self.write("01-1905", wave(
            "01-1905", planned=2,
            supply={"x/publish": (1, 1), "x/reply": (1, 1)},
            results=[result("x", "publish", "committed"),
                     result("x", "reply", "failed")]))
        report = cycle_report.build_report(self.data, night="2026-11-22")
        self.assertEqual("attention", report["status"])
        self.assertEqual("partial", report["task_outcome"]["outcome"])
        self.assertEqual(1, report["task_outcome"]["committed"])
        self.assertEqual(2, report["task_outcome"]["target"])

    def test_failed_zero_delivery_is_unresolved(self):
        self.write("01-1905", wave(
            "01-1905", planned=1, supply={"x/publish": (1, 1)},
            results=[result("x", "publish", "failed")], ok=False))
        report = cycle_report.build_report(self.data, night="2026-11-22")
        self.assertEqual("unresolved", report["task_outcome"]["outcome"])
        self.assertIn("wave_failed", report["task_outcome"]["reasons"])

    def test_failed_wave_with_committed_work_is_partial(self):
        self.write("01-1905", wave(
            "01-1905", planned=1, supply={"x/publish": (1, 1)},
            results=[result("x", "publish", "committed")]))
        self.write("02-2005", wave(
            "02-2005", planned=1, supply={"x/reply": (1, 1)},
            results=[result("x", "reply", "failed")], ok=False))
        report = cycle_report.build_report(self.data, night="2026-11-22")
        self.assertEqual("partial", report["task_outcome"]["outcome"])

    def test_no_basis_is_unknown(self):
        self.write("01-1905", wave(
            "01-1905", planned=0, supply={}, results=[]))
        report = cycle_report.build_report(self.data, night="2026-11-22")
        self.assertEqual("unknown", report["task_outcome"]["outcome"])
        self.assertEqual("none", report["task_outcome"]["basis"])


if __name__ == "__main__":
    unittest.main()
