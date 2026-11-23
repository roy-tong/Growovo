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
            "observation_pair_id": "pair-1",
            "metrics": {"x_followers": followers,
                        "github_stars": {"Verovo": stars},
                        "product_users": users},
            "source": {"kind": "manual_read", "evidence": ["snapshot.json"],
                       "subject": {"account": "account-1", "product": "product-1"},
                       "metric_definitions": {"x_followers": "v1", "github_stars": "v1",
                                              "product_users": "v1"}}}


class BusinessObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def test_feature_status_documents_evidence_grades_and_pairing(self):
        text = (Path(__file__).resolve().parents[1] / "docs" / "feature-status.md").read_text(
            encoding="utf-8")
        for token in ("owner_declared", "independently_verified", "observation_pair_id"):
            self.assertIn(token, text)

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
        self.assertEqual("owner_declared", got["status"])
        self.assertEqual("current", got["revision_state"])
        self.assertTrue(got["comparison_id"].startswith("cmp:"))
        historical = business_observations.compare(
            self.data, "2026-11-23", pair_id="pair-1")
        self.assertEqual(got["comparison_id"], historical["comparison_id"])
        self.assertEqual(2, got["metrics"]["x_followers"]["delta"])
        self.assertEqual(0, got["metrics"]["github_stars"]["Verovo"]["delta"])
        self.assertEqual("unknown", got["metrics"]["product_users"]["status"])
        self.assertIsNone(got["metrics"]["product_users"]["delta"])
        self.assertEqual("not_claimed", got["attribution"])

    def test_observed_grade_requires_existing_hash_bound_evidence(self):
        evidence = self.data / "platform-snapshot.json"
        evidence.write_text('{"followers": 38}', encoding="utf-8")
        import hashlib
        digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
        baseline = payload()
        baseline["source"]["evidence"] = [{"path": "platform-snapshot.json", "sha256": digest}]
        final = payload(followers=38)
        final["observed_at"] = "2026-11-24T07:00:00+08:00"
        final["source"]["evidence"] = [{"path": "platform-snapshot.json", "sha256": digest}]
        first = business_observations.import_observation(
            self.data, baseline, night="2026-11-23", phase="baseline", by="roy", apply=True)
        second = business_observations.import_observation(
            self.data, final, night="2026-11-23", phase="final", by="roy", apply=True)
        got = business_observations.compare(self.data, "2026-11-23")
        self.assertEqual("observed", first["observation"]["observation_grade"])
        self.assertEqual("observed", second["observation"]["observation_grade"])
        self.assertEqual("observed", got["status"])

    def test_independent_verification_requires_a_separate_review_receipt(self):
        evidence = self.data / "platform-snapshot.json"
        review = self.data / "independent-review.json"
        evidence.write_text('{"followers": 36}', encoding="utf-8")
        review.write_text('{"accepted": true}', encoding="utf-8")
        import hashlib
        row = payload()
        source = row["source"]
        source["evidence"] = [{"path": "platform-snapshot.json",
                               "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}]
        source["independent_verification"] = {
            "reviewer": "independent-reviewer", "control_domain": "third_party",
            "authority": "external_maintainer", "verification_result": "verified",
            "verified_at": "2026-11-23T20:00:00+08:00",
            "evidence": [{"path": "independent-review.json",
                          "sha256": hashlib.sha256(review.read_bytes()).hexdigest()}]}
        got = business_observations.import_observation(
            self.data, row, night="2026-11-23", phase="baseline", by="roy", apply=True)
        self.assertEqual("independently_verified", got["observation"]["observation_grade"])

    def test_executor_self_review_cannot_become_independently_verified(self):
        evidence = self.data / "platform-snapshot.json"
        review = self.data / "self-review.json"
        evidence.write_text('{"followers": 36}', encoding="utf-8")
        review.write_text('{"accepted": true}', encoding="utf-8")
        import hashlib
        row = payload()
        row["source"]["evidence"] = [{"path": "platform-snapshot.json",
                                      "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}]
        row["source"]["independent_verification"] = {
            "reviewer": "growovo", "control_domain": "executor",
            "authority": "owner", "verification_result": "verified",
            "verified_at": "2026-11-23T20:00:00+08:00",
            "evidence": [{"path": "self-review.json",
                          "sha256": hashlib.sha256(review.read_bytes()).hexdigest()}]}
        got = business_observations.import_observation(
            self.data, row, night="2026-11-23", phase="baseline", by="roy", apply=True)
        self.assertEqual("owner_declared", got["observation"]["observation_grade"])
        self.assertIn("independent_control_domain_invalid", got["observation"]["source_issues"])

    def test_compare_rejects_backwards_time_and_mismatched_scope(self):
        cases = {
            "backwards": ("2026-11-24T07:00:00+08:00", "2026-11-23T19:00:00+08:00", "account-1", "v1"),
            "account": ("2026-11-23T19:00:00+08:00", "2026-11-24T07:00:00+08:00", "account-2", "v1"),
            "definition": ("2026-11-23T19:00:00+08:00", "2026-11-24T07:00:00+08:00", "account-1", "v2"),
        }
        for name, (baseline_at, final_at, final_account, final_definition) in cases.items():
            with self.subTest(name=name):
                data = self.data / name
                baseline = payload()
                baseline["observed_at"] = baseline_at
                final = payload(followers=38)
                final["observed_at"] = final_at
                final["source"]["subject"]["account"] = final_account
                final["source"]["metric_definitions"]["x_followers"] = final_definition
                business_observations.import_observation(
                    data, baseline, night="2026-11-23", phase="baseline", by="roy", apply=True)
                business_observations.import_observation(
                    data, final, night="2026-11-23", phase="final", by="roy", apply=True)
                got = business_observations.compare(data, "2026-11-23")
                self.assertEqual("invalid", got["status"])
                self.assertTrue(got["errors"])

    def test_late_baseline_invalidates_the_previous_pair(self):
        baseline = payload()
        final = payload(followers=38)
        final["observed_at"] = "2026-11-24T07:00:00+08:00"
        revised = payload(followers=35)
        revised["observed_at"] = "2026-11-23T18:00:00+08:00"
        for phase, row in (("baseline", baseline), ("final", final), ("baseline", revised)):
            business_observations.import_observation(
                self.data, row, night="2026-11-23", phase=phase, by="roy", apply=True)
        got = business_observations.compare(self.data, "2026-11-23")
        self.assertEqual("invalid", got["status"])
        self.assertIn("pair_revision_required", got["errors"])
        self.assertEqual(1, len(got["superseded_comparisons"]))
        superseded = got["superseded_comparisons"][0]
        self.assertEqual("superseded", superseded["revision_state"])
        self.assertTrue(superseded["comparison_id"].startswith("cmp:"))

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
        self.assertEqual("owner_declared", got["business_outcomes"]["status"])
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
