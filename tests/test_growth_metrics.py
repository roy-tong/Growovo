import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from types import SimpleNamespace
from core import growth_cli, growth_metrics, growth_service
from tests.test_growth_goals import goal


class GrowthMetricsTest(unittest.TestCase):
    def test_collects_per_repo_totals_and_x_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            metadata = {
                "status": "OBSERVED", "stars": 5, "forks": 1, "open_issues": 2,
                "source": "https://api.github.com/repos/owner/demo",
            }
            stats = {"followers": 42, "saved": str(Path(tmp) / "x.json"),
                     "screenshots": []}
            Path(stats["saved"]).write_text("{}", encoding="utf-8")
            with mock.patch("core.growth_metrics.fetch_repo_metadata", return_value=metadata), \
                    mock.patch("core.growth_metrics.read_stats.read", return_value=stats):
                got = growth_metrics.collect_metrics(
                    tmp, ["owner/demo"], "owner", phase="baseline")
            self.assertEqual(5, got["metrics"]["github_stars"])
            self.assertEqual(42, got["metrics"]["x_followers"])
            self.assertEqual("OBSERVED", got["metrics"]["github_stars_by_repo"]["owner/demo"]["status"])
            self.assertTrue(got["evidence"])
            for row in got["evidence"]:
                self.assertTrue(Path(row["path"]).is_file())
                self.assertEqual(64, len(row["sha256"]))

    def test_unknown_repo_prevents_total_instead_of_becoming_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("core.growth_metrics.fetch_repo_metadata",
                            return_value={"status": "UNKNOWN", "stars": None}), \
                    mock.patch("core.growth_metrics.read_stats.read",
                               return_value={"followers": None, "saved": str(Path(tmp) / "x.json")}):
                Path(tmp, "x.json").write_text("{}", encoding="utf-8")
                got = growth_metrics.collect_metrics(tmp, ["owner/demo"], "owner")
            self.assertIsNone(got["metrics"]["github_stars"])
            self.assertIsNone(got["metrics"]["x_followers"])

    def test_observe_live_persists_metrics_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = growth_service.start(tmp, goal())
            payload = {
                "metrics": {"github_stars": 2, "x_followers": 38},
                "evidence": [],
            }
            with mock.patch("core.growth_metrics.collect_metrics", return_value=payload):
                got = growth_metrics.observe_live(
                    tmp, service["id"], "baseline", ["roy-tong/Growovo"], "elliwoodtong",
                    by="tester")
            self.assertEqual(2, got["measurement"]["baseline"]["github_stars"])
            action = next(row for row in got["actions"] if row["id"] == "baseline-observe")
            self.assertEqual("completed", action["status"])


class GrowthMetricsCliTest(unittest.TestCase):
    def test_observe_live_command_uses_the_same_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(
                growth_action="observe-live", service_id="svc-test", phase="baseline",
                repos="owner/a,owner/b", handle="owner", by="tester")
            with mock.patch("core.growth_metrics.observe_live", return_value={"status": "ok"}) as live:
                code = growth_cli.handle(args, tmp)
            self.assertEqual(0, code)
            live.assert_called_once_with(
                tmp, "svc-test", "baseline", ["owner/a", "owner/b"], "owner", by="tester")


if __name__ == "__main__":
    unittest.main()
