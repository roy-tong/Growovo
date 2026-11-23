#!/usr/bin/env python3
"""Deterministic, read-only reports for persisted engagement waves."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import cycle_report
import mkt


def wave(wave_id, *, planned, supply, results, pending=0, collected=0,
         ok=True, dry=False, deferred=None, stopped=None):
    return {
        "ok": ok,
        "wave": wave_id,
        "campaign": "night-2026-11-22-w" + wave_id,
        "night": "2026-11-22",
        "dry": dry,
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
        "stopped": stopped or {},
        "preflight_skipped": {},
        "inbox": {"collected": collected, "pending_text": pending, "errors": {}},
        "content": {
            "reply_artifact_floor_met": True,
            "focus_floor_met": True,
            "x_publish_artifact_floor_met": True,
        },
    }


def result(channel, action, state, target="target"):
    return {"channel": channel, "action": action, "target": target,
            "result": state, "ledger_result": state, "error": "detail"}


class CycleReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.directory = self.data / "state" / "engagement-2026-11-22"
        self.directory.mkdir(parents=True)

    def write_wave(self, wave_id, payload):
        path = self.directory / ("wave-%s.json" % wave_id)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_totals_separate_target_plan_attempts_and_latest_inbox(self):
        self.write_wave("01-1905", wave(
            "01-1905", planned=2,
            supply={"x/publish": (1, 1), "x/reply": (2, 1)},
            results=[result("x", "publish", "committed", "p1"),
                     result("x", "reply", "target_rejected", "r1")],
            pending=2, collected=3))
        self.write_wave("02-2005", wave(
            "02-2005", planned=3,
            supply={"x/publish": (1, 1), "x/reply": (2, 2)},
            results=[result("x", "publish", "committed", "p2"),
                     result("x", "reply", "committed", "r2"),
                     result("x", "reply", "indeterminate", "r3")],
            pending=1, collected=2))

        got = cycle_report.build_report(self.data, night="2026-11-22")

        self.assertEqual("attention", got["status"])
        self.assertEqual(2, got["wave_count"])
        self.assertEqual({"target": 6, "planned": 5, "attempted": 5,
                          "committed": 3, "deferred": 0, "unattempted": 0},
                         {key: got["totals"][key] for key in
                          ("target", "planned", "attempted", "committed",
                           "deferred", "unattempted")})
        self.assertEqual(3, got["totals"]["not_committed"])
        self.assertEqual(1, got["totals"]["preplanning_shortfall"])
        self.assertEqual(1, got["by_key"]["x/reply"]["preplanning_shortfall"])
        self.assertEqual(1, got["by_key"]["x/reply"]["results"]["target_rejected"])
        self.assertEqual(1, got["by_key"]["x/reply"]["results"]["indeterminate"])
        self.assertEqual(5, got["inbox"]["collection_observations"])
        self.assertEqual(1, got["inbox"]["latest_pending_text"])
        self.assertEqual(2, len(got["exceptions"]))
        self.assertEqual("not_included", got["business_outcomes"]["status"])
        self.assertEqual(2, len(got["source_files"]))
        self.assertTrue(all(len(row["sha256"]) == 64 for row in got["source_files"]))

    def test_complete_wave_returns_zero_from_cli(self):
        self.write_wave("01-1905", wave(
            "01-1905", planned=1, supply={"x/publish": (1, 1)},
            results=[result("x", "publish", "committed")]))
        args = SimpleNamespace(action="report", night="2026-11-22")
        output = io.StringIO()
        with mock.patch.object(mkt, "DATA", self.data), contextlib.redirect_stdout(output):
            code = mkt.cmd_cycle(args)
        got = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("complete", got["status"])
        self.assertEqual(1, got["totals"]["committed"])

    def test_partial_wave_returns_business_refusal_from_cli(self):
        self.write_wave("01-1905", wave(
            "01-1905", planned=1, supply={"x/publish": (1, 1)},
            results=[result("x", "publish", "failed")]))
        args = SimpleNamespace(action="report", night="2026-11-22")
        output = io.StringIO()
        with mock.patch.object(mkt, "DATA", self.data), contextlib.redirect_stdout(output):
            code = mkt.cmd_cycle(args)
        self.assertEqual(2, code)
        self.assertEqual("attention", json.loads(output.getvalue())["status"])

    def test_dry_run_is_not_counted_as_real_execution(self):
        self.write_wave("01-1905", wave(
            "01-1905", planned=1, supply={"x/publish": (1, 1)},
            results=[result("x", "publish", "dry-run")], dry=True))
        got = cycle_report.build_report(self.data, night="2026-11-22")
        self.assertEqual(0, got["wave_count"])
        self.assertEqual(1, got["dry_run_wave_count"])
        self.assertEqual(0, got["totals"]["committed"])
        self.assertEqual("no_real_waves", got["reason"])

    def test_legacy_wave_without_targets_uses_planned_basis_explicitly(self):
        self.write_wave("01-1905", wave(
            "01-1905", planned=1, supply={},
            results=[result("x", "publish", "committed")]))
        got = cycle_report.build_report(self.data, night="2026-11-22")
        self.assertEqual("unavailable", got["totals"]["target_status"])
        self.assertEqual("planned_target_unavailable", got["totals"]["shortfall_basis"])
        self.assertEqual(1, got["totals"]["planned"])
        self.assertEqual(0, got["totals"]["not_committed"])
        self.assertIsNone(got["totals"]["completion_rate"])
        self.assertEqual("complete", got["waves"][0]["status"])

    def test_scheduled_only_excludes_named_catchup_wave_without_hiding_it(self):
        self.write_wave("01-1905", wave(
            "01-1905", planned=1, supply={"x/publish": (1, 1)},
            results=[result("x", "publish", "committed")]))
        self.write_wave("xreplies-0850", wave(
            "xreplies-0850", planned=0, supply={"x/reply": (1, 0)}, results=[]))
        self.write_wave("0851", wave(
            "0851", planned=1, supply={"x/reply": (1, 1)},
            results=[result("x", "reply", "committed", "catchup")]))

        scheduled = cycle_report.build_report(
            self.data, night="2026-11-22", scheduled_only=True)
        everything = cycle_report.build_report(self.data, night="2026-11-22")

        self.assertEqual(1, scheduled["wave_count"])
        self.assertEqual(2, scheduled["auxiliary_wave_count"])
        self.assertEqual(["0851", "xreplies-0850"], scheduled["auxiliary_waves"])
        self.assertEqual(1, scheduled["totals"]["target"])
        self.assertEqual(1, scheduled["totals"]["committed"])
        self.assertEqual(3, everything["wave_count"])
        self.assertEqual(3, everything["totals"]["target"])
        self.assertEqual(2, everything["totals"]["committed"])

    def test_malformed_wave_is_an_explicit_report_error(self):
        (self.directory / "wave-broken.json").write_text("{", encoding="utf-8")
        got = cycle_report.build_report(self.data, night="2026-11-22")
        self.assertEqual("attention", got["status"])
        self.assertEqual(1, len(got["errors"]))
        self.assertIn("wave-broken.json", got["errors"][0]["path"])

    def test_missing_night_is_refused(self):
        with self.assertRaisesRegex(ValueError, "没有可汇总的波次"):
            cycle_report.build_report(self.data, night="2026-11-23")


if __name__ == "__main__":
    unittest.main()
