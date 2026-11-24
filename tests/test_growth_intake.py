import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import growth_goals, growth_intake


class GrowthIntakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "README.md").write_text(
            "# Demo product\n\nRun controlled GTM work and inspect receipts.\n\n"
            "```bash\npython3 mkt.py demo\n```\n", encoding="utf-8")
        (self.repo / "AGENTS.md").write_text("test instructions", encoding="utf-8")
        (self.repo / "llms.txt").write_text("demo docs", encoding="utf-8")

    def test_inspect_local_repository_uses_evidence_not_assumptions(self):
        got = growth_intake.inspect_repository(self.repo, slug="owner/demo")
        self.assertEqual("Demo product", got["product"]["name"])
        self.assertTrue(got["readiness"]["quickstart"]["passed"])
        self.assertTrue(got["readiness"]["agent_contract"]["passed"])
        self.assertEqual("instrument_activation_events", got["recommended_first_action"])
        self.assertTrue((self.repo / "README.md").samefile(got["evidence"]["readme_path"]))
        self.assertEqual("UNKNOWN", got["metrics"]["status"])

    def test_manifest_product_name_beats_readme_section_heading(self):
        (self.repo / "pyproject.toml").write_text(
            '[project]\nname = "growovo"\ndescription = "Growth operations"\n', encoding="utf-8")
        (self.repo / "README.md").write_text(
            "<p align=\"center\">Logo</p>\n\n# 合规与纪律（先读这个）\n\n"
            "Run controlled GTM work and inspect receipts.\n\n"
            "```bash\npython3 mkt.py demo\n```\n", encoding="utf-8")
        got = growth_intake.inspect_repository(self.repo, slug="owner/demo")
        self.assertEqual("growovo", got["product"]["name"])

    def test_metadata_failure_stays_unknown(self):
        with mock.patch("core.growth_intake.urllib.request.urlopen", side_effect=OSError("403")):
            got = growth_intake.fetch_repo_metadata("owner/demo")
        self.assertEqual("UNKNOWN", got["status"])
        self.assertEqual("owner/demo", got["slug"])

    def test_build_goal_preserves_unreproduced_asset_and_measurement_evidence(self):
        report = growth_intake.inspect_repository(self.repo, slug="owner/demo")
        contract = growth_intake.build_goal(report)
        growth_goals.validate_goal(contract)
        self.assertFalse(contract["assets"][0]["reproduced"])
        self.assertEqual("live_with_manual_fallback", contract["execution_intent"])
        self.assertEqual("UNKNOWN", contract["intake"]["metrics_status"])
        self.assertEqual(1, contract["outcomes"][0]["target"])


if __name__ == "__main__":
    unittest.main()
