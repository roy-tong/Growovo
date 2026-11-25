import json
import tempfile
import unittest
from pathlib import Path

from core import geo_agent, geo_entity
from tests.geo_fixtures import entity_document


class GeoAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        geo_entity.init(str(self.data), entity_document())
        (self.repo / "llms.txt").write_text(
            "Growovo https://example.com https://github.com/example/growovo\n",
            encoding="utf-8")
        (self.repo / "mcp_server.py").write_text(
            'TOOLS = [{"name": "mkt_growth"}, {"name": "mkt_geo"}]\n', encoding="utf-8")
        (self.repo / "AGENTS.md").write_text("Growovo agent rules\n", encoding="utf-8")
        (self.repo / "README.md").write_text(
            "Growovo https://example.com https://github.com/example/growovo\n",
            encoding="utf-8")

    def test_assets_tools_and_lower_bound_counts(self):
        logs = Path(self.tmp.name) / "crawl.csv"
        logs.write_text(
            "user_agent,path,status\n"
            "GPTBot/1.0,/docs,200\n"
            "GPTBot/1.0,/pricing,200\n"
            "ClaudeBot/1.0,/docs,200\n"
            "Mozilla/5.0,/docs,200\n", encoding="utf-8")
        usage = Path(self.tmp.name) / "usage.jsonl"
        usage.write_text("\n".join([
            json.dumps({"tool": "mkt_geo", "success": True}),
            json.dumps({"tool": "mkt_geo", "success": False}),
        ]) + "\n", encoding="utf-8")
        result = geo_agent.check(str(self.data), str(self.repo),
                                 logs=str(logs), usage=str(usage))
        assets = {row["path"]: row for row in result["assets"]}
        self.assertTrue(assets["llms.txt"]["name_present"])
        self.assertEqual(["mkt_geo", "mkt_growth"], assets["mcp_server.py"]["tools"])
        self.assertEqual(2, result["summary"]["crawler"]["by_bot"]["GPTBot"])
        self.assertEqual(1, result["summary"]["crawler"]["by_bot"]["ClaudeBot"])
        self.assertTrue(result["summary"]["crawler"]["lower_bound"])
        self.assertEqual(2, result["summary"]["usage"]["calls"])
        self.assertEqual(0.5, result["summary"]["usage"]["success_rate"])

    def test_missing_logs_are_not_run_not_zero(self):
        result = geo_agent.check(str(self.data), str(self.repo))
        self.assertEqual("not_run", result["summary"]["crawler"]["status"])
        self.assertEqual("not_run", result["summary"]["usage"]["status"])
        self.assertIsNone(result["summary"]["usage"]["success_rate"])

    def test_missing_repo_is_refused(self):
        with self.assertRaises(ValueError):
            geo_agent.check(str(self.data), str(Path(self.tmp.name) / "missing"))


if __name__ == "__main__":
    unittest.main()
