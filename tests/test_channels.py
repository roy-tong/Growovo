"""渠道库：具体地方、评分、组合、经济学、容量、实验与 CLI/MCP。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import channels, goals


ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"


def _facts():
    return {
        "product": {"name": "Verovo", "what": "AI 账期对账", "url": "https://example.com",
                    "stage": "launched", "product_type": "service"},
        "icp": [{"hypothesis": "FinOps", "tags": ["b2b-buyers"],
                 "evidence_label": "self_research", "evidence": "r/FinOps 10+ 条"},
                {"hypothesis": "AI 交付方", "tags": ["technical-founders"],
                 "evidence_label": "hypothesis", "evidence": "2 条"}],
        "baseline": {"github_stars": 220, "forks": 10, "paid_deals": 0},
        "value_events": [{"name": "付费", "source": "收款", "is_approximation": False}],
        "constraints": {"red_lines": ["不做群发"], "human_minutes_budget": 840},
        "owned_assets": {"platforms": ["github", "reddit", "hackernews"]},
    }


def _goal_plan():
    return goals.build_plan(_facts(), "awareness.stars",
                            secondary_goal_id="adoption.real_data",
                            platforms=["github", "hackernews", "reddit"])


class VenueTests(unittest.TestCase):
    def test_registry_has_required_fields_and_unique_ids(self):
        self.assertGreaterEqual(len(channels.VENUES), 10)
        ids = [v["id"] for v in channels.VENUES]
        self.assertEqual(len(ids), len(set(ids)))
        required = ("id", "platform", "name", "audience", "size", "access",
                    "promo", "formats", "signal_density", "competition",
                    "speed", "risk", "evidence", "notes")
        for row in channels.VENUES:
            for key in required:
                self.assertIn(key, row, f"{row['id']} 缺 {key}")
            self.assertIn(row["promo"], ("allowed", "limited", "banned"))
            self.assertIn(row["access"], ("public", "join", "login", "paid"))

    def test_score_prefers_primary_icp(self):
        rows = channels.rank(_facts(), _goal_plan())
        by_id = {r["id"]: r for r in rows}
        self.assertGreater(by_id["reddit-finops"]["score"],
                           by_id["indie-hackers"]["score"])

    def test_promotion_banned_never_primary(self):
        plan = channels.plan(_facts(), _goal_plan())
        self.assertNotEqual(plan["venues"]["primary"]["promo"], "banned")
        for v in plan["venues"]["ambient"]:
            self.assertTrue(v["promo"] == "banned"
                            or v["access"] in ("login", "paid"), v["id"])


class PlanTests(unittest.TestCase):
    def test_roles_and_launch(self):
        plan = channels.plan(_facts(), _goal_plan())
        self.assertEqual(plan["venues"]["primary"]["id"], "reddit-finops")
        self.assertEqual(plan["venues"]["launch"]["id"], "hackernews")
        self.assertLessEqual(len(plan["venues"]["support"]), 2)

    def test_economics_are_bounded_and_ordered(self):
        plan = channels.plan(_facts(), _goal_plan())
        e = plan["economics"]["expected"]
        self.assertGreaterEqual(e["reach"], e["replies"])
        self.assertGreaterEqual(e["replies"], e["qualified"])
        self.assertGreaterEqual(e["qualified"], e["samples"])
        self.assertGreaterEqual(e["samples"], e["paid"])
        self.assertLess(e["paid"], 10)
        self.assertIn("假设", plan["economics"]["boundary"])

    def test_capacity_and_experiments(self):
        plan = channels.plan(_facts(), _goal_plan())
        self.assertTrue(plan["capacity"])
        roles = {x["role"] for x in plan["experiments"]}
        self.assertIn("primary", roles)
        self.assertIn("direct", roles)

    def test_render_keeps_rules_and_boundary(self):
        md = channels.render(channels.plan(_facts(), _goal_plan()))
        for word in ("渠道判断", "结论", "为什么是这几处", "为什么不是别处",
                     "这笔账怎么算", "什么情况下这条路不成立", "纪律"):
            self.assertIn(word, md)
        self.assertIn("渠道组合是排期假设", md)


class ChannelCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir(parents=True)
        self.facts = Path(self.tmp.name) / "facts.json"
        self.facts.write_text(json.dumps(_facts(), ensure_ascii=False), encoding="utf-8")
        self.env = dict(os.environ, MKT_HOME=str(self.home))

    def run_mkt(self, *args):
        proc = subprocess.run([sys.executable, str(MKT), *args], cwd=ROOT,
                              env=self.env, text=True, capture_output=True)
        if proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed\n{proc.stdout}\n{proc.stderr}")
        return proc.stdout

    def test_venues_json(self):
        out = self.run_mkt("channel", "venues", "--platform", "reddit", "--json")
        rows = json.loads(out)
        self.assertTrue(rows)
        self.assertTrue(all(r["platform"] == "reddit" for r in rows))

    def test_plan_json(self):
        out = self.run_mkt("channel", "plan", "--facts", str(self.facts),
                           "--goal", "awareness.stars",
                           "--secondary", "adoption.real_data",
                           "--platforms", "github,hackernews,reddit", "--json")
        plan = json.loads(out)
        self.assertEqual(plan["venues"]["primary"]["id"], "reddit-finops")
        self.assertIn("economics", plan)

    def test_score(self):
        out = self.run_mkt("channel", "score", "--venue", "reddit-finops",
                           "--facts", str(self.facts), "--goal", "awareness.stars",
                           "--platforms", "github,hackernews,reddit", "--json")
        row = json.loads(out)
        self.assertEqual(row["id"], "reddit-finops")
        self.assertIn("b2b-buyers", row["audience_hits"])


class ChannelMcpTests(unittest.TestCase):
    def test_readonly_tools(self):
        from mcp_server import _call_tool
        res = _call_tool("mkt_channel_venues", {"platform": "reddit"})
        self.assertFalse(res.get("isError"))
        rows = json.loads(res["content"][0]["text"])
        self.assertTrue(rows)
        res = _call_tool("mkt_channel_plan", {
            "facts": _facts(), "goal": "awareness.stars",
            "secondary": "adoption.real_data",
            "platforms": ["github", "hackernews", "reddit"]})
        self.assertFalse(res.get("isError"))
        plan = json.loads(res["content"][0]["text"])
        self.assertEqual(plan["venues"]["primary"]["id"], "reddit-finops")


if __name__ == "__main__":
    unittest.main()
