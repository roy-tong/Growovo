#!/usr/bin/env python3
"""Authorized business observations and non-causal baseline/final comparisons."""
import datetime as dt
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import business_observations
from core import cycle_report
from core import engagement_cycle as cycle
import mkt

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 11, 23, 22, 5, tzinfo=TZ)


def payload(followers=36, stars=220, users=None):
    return {"observed_at": "2026-11-23T19:00:00+08:00",
            "metrics": {"x_followers": followers,
                        "github_stars": {"Verovo": stars},
                        "product_users": users},
            "source": {"kind": "manual_read", "evidence": ["snapshot.json"]}}


class BusinessObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def test_import_defaults_to_dry_run_and_preserves_unknown(self):
        preview = business_observations.import_observation(
            self.data, payload(), night="2026-11-23", phase="baseline", by="roy")
        self.assertFalse(preview["apply"])
        self.assertFalse(business_observations.path(self.data).exists())
        got = business_observations.import_observation(
            self.data, payload(), night="2026-11-23", phase="baseline", by="roy", apply=True)
        self.assertTrue(got["apply"])
        repeat = business_observations.import_observation(
            self.data, payload(), night="2026-11-23", phase="baseline", by="roy", apply=True)
        self.assertTrue(repeat["duplicate"])
        rows = [json.loads(line) for line in business_observations.path(self.data).read_text().splitlines()]
        self.assertEqual(1, len(rows))
        self.assertIsNone(rows[0]["metrics"]["product_users"])

    def test_comparison_reports_delta_without_claiming_attribution(self):
        business_observations.import_observation(
            self.data, payload(), night="2026-11-23", phase="baseline", by="roy", apply=True)
        final = payload(followers=38, users=None)
        final["observed_at"] = "2026-11-24T07:00:00+08:00"
        business_observations.import_observation(
            self.data, final, night="2026-11-23", phase="final", by="roy", apply=True)
        got = business_observations.compare(self.data, "2026-11-23")
        self.assertEqual("measured", got["status"])
        self.assertEqual(2, got["metrics"]["x_followers"]["delta"])
        self.assertEqual(0, got["metrics"]["github_stars"]["Verovo"]["delta"])
        self.assertEqual("unknown", got["metrics"]["product_users"]["status"])
        self.assertIsNone(got["metrics"]["product_users"]["delta"])
        self.assertEqual("not_claimed", got["attribution"])

    def test_cycle_report_includes_independent_business_comparison(self):
        business_observations.import_observation(
            self.data, payload(), night="2026-11-23", phase="baseline", by="roy", apply=True)
        final = payload(followers=38)
        final["observed_at"] = "2026-11-24T07:00:00+08:00"
        business_observations.import_observation(
            self.data, final, night="2026-11-23", phase="final", by="roy", apply=True)
        directory = self.data / "state" / "engagement-2026-11-23"
        directory.mkdir(parents=True)
        (directory / "wave-01-1905.json").write_text(json.dumps({
            "ok": True, "wave": "01-1905", "campaign": "night-test", "dry": False,
            "planned": 1, "supply": {"x/publish": {"effective": 1, "scheduled": 1}},
            "results": [{"channel": "x", "action": "publish", "result": "committed",
                         "ledger_result": "committed", "ref": "r1"}],
            "inbox": {}, "content": {},
        }), encoding="utf-8")
        got = cycle_report.build_report(self.data, night="2026-11-23")
        self.assertEqual("measured", got["business_outcomes"]["status"])
        self.assertEqual(2, got["business_outcomes"]["metrics"]["x_followers"]["delta"])

    def test_cli_observe_defaults_to_dry_run(self):
        source = self.data / "observation.json"
        source.write_text(json.dumps(payload()), encoding="utf-8")
        output = io.StringIO()
        with mock.patch.object(mkt, "DATA", self.data), contextlib.redirect_stdout(output):
            code = mkt.cmd_cycle(SimpleNamespace(
                action="observe", file=str(source), night="2026-11-23",
                phase="baseline", by="roy", apply=False))
        self.assertEqual(0, code)
        self.assertFalse(json.loads(output.getvalue())["apply"])
        self.assertFalse(business_observations.path(self.data).exists())

    def test_required_baseline_blocks_before_browser_or_send(self):
        policies = self.data / "policies"
        policies.mkdir(parents=True)
        (policies / "engagement-cycle.json").write_text(json.dumps({
            "business": {"require_baseline": True},
        }), encoding="utf-8")
        with mock.patch.object(cycle, "_now", return_value=NOW), \
                mock.patch("core.engagement_cycle.collect_inbox_report") as collect, \
                mock.patch("core.action_runner.run_action") as dispatch:
            got = cycle.run(self.data, go=True, wave_id="01-2205", log=lambda *_: None)
        self.assertFalse(got["ok"])
        self.assertEqual("business_baseline_missing", got["reason"])
        collect.assert_not_called()
        dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
