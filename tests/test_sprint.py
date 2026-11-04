"""冲刺引擎：3 天并行排期、每日目标、容量校验、止损与渲染。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import goals, sprint


ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"


def _facts():
    return {
        "product": {"name": "Verovo", "what": "AI 账期对账", "url": "https://example.com",
                    "stage": "launched", "product_type": "service"},
        "icp": [{"hypothesis": "FinOps", "tags": ["b2b-buyers"],
                 "evidence_label": "self_research", "evidence": "r/FinOps 10+ 条"}],
        "baseline": {"github_stars": 220, "forks": 10, "paid_deals": 0},
        "value_events": [{"name": "付费", "source": "收款记录", "is_approximation": False}],
        "constraints": {"red_lines": ["不做群发"], "human_minutes_budget": 840},
        "owned_assets": {"platforms": ["github", "reddit", "hackernews", "x", "linkedin"]},
    }


def _goal_plan(days=3):
    return goals.build_plan(_facts(), "awareness.stars",
                            secondary_goal_id="adoption.real_data",
                            platforms=["github", "hackernews", "reddit"])


class SprintTests(unittest.TestCase):
    def test_three_day_targets_sum_to_goal(self):
        plan = sprint.build(_goal_plan(), days=3, start="2026-11-05")
        self.assertEqual(plan["days"], 3)
        self.assertEqual(plan["starts"], "2026-11-05")
        self.assertEqual(plan["ends"], "2026-11-07")
        self.assertEqual(sum(d["primary"] for d in plan["daily_targets"]), 50)
        self.assertEqual(sum(d["secondary"] for d in plan["daily_targets"]), 2)

    def test_targets_are_front_loaded_and_data_lags(self):
        plan = sprint.build(_goal_plan(), days=3, start="2026-11-05")
        stars = [d["primary"] for d in plan["daily_targets"]]
        data = [d["secondary"] for d in plan["daily_targets"]]
        self.assertGreaterEqual(stars[0], stars[-1])
        self.assertEqual(data[0], 0)
        self.assertGreaterEqual(data[-1], 1)

    def test_blocks_cover_asset_launch_direct_and_judge(self):
        plan = sprint.build(_goal_plan(), days=3, start="2026-11-05")
        self.assertGreaterEqual(len(plan["blocks"]), 15)
        text = " ".join(b["task"] for b in plan["blocks"])
        for word in ("资产", "Hacker News", "私信", "判定"):
            self.assertIn(word, text)
        for b in plan["blocks"]:
            self.assertTrue(b["accept"], b["task"])
            self.assertIn(b["owner"], ("human", "machine"))

    def test_one_and_seven_days(self):
        one = sprint.build(_goal_plan(), days=1, start="2026-11-05")
        self.assertEqual(len(one["daily_targets"]), 1)
        self.assertEqual(one["ends"], "2026-11-05")
        seven = sprint.build(_goal_plan(), days=7, start="2026-11-05")
        self.assertEqual(len(seven["daily_targets"]), 7)
        self.assertEqual(sum(d["primary"] for d in seven["daily_targets"]), 50)
        self.assertEqual(seven["ends"], "2026-11-11")

    def test_invalid_days_rejected(self):
        for days in (0, 15, -1):
            with self.assertRaises(sprint.SprintError):
                sprint.build(_goal_plan(), days=days)

    def test_over_capacity_is_flagged(self):
        plan = sprint.build(_goal_plan(), days=3, hours_per_day=1)
        self.assertTrue(plan["cost"]["over_capacity"])
        md = sprint.render(plan)
        self.assertIn("超预算", md)

    def test_capacity_rows_and_limits(self):
        plan = sprint.build(_goal_plan(), days=3)
        rows = {(r["platform"], r["action"]): r for r in plan["capacity"]["rows"]}
        self.assertEqual(rows[("hackernews", "post")]["limit"], 1)
        self.assertTrue(rows[("hackernews", "post")]["ok"])

    def test_render_keeps_levers_and_red_lines(self):
        md = sprint.render(sprint.build(_goal_plan(), days=3, start="2026-11-05"))
        for section in ("天冲刺", "每天追什么", "并行工作流", "小时级排期",
                        "加速杠杆", "到点就换", "容量校验", "红线", "72 小时"):
            self.assertIn(section, md)
        self.assertIn("3 天冲刺是信号测试", md)


class SprintCliTests(unittest.TestCase):
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

    def test_sprint_plan_json(self):
        out = self.run_mkt("sprint", "plan", "--facts", str(self.facts),
                           "--goal", "awareness.stars",
                           "--secondary", "adoption.real_data",
                           "--platforms", "github,hackernews,reddit",
                           "--days", "3", "--start", "2026-11-05", "--json")
        plan = json.loads(out)
        self.assertEqual(plan["days"], 3)
        self.assertEqual(plan["goal"]["id"], "awareness.stars")
        self.assertEqual(plan["secondary"]["id"], "adoption.real_data")

    def test_sprint_plan_markdown(self):
        out = self.run_mkt("sprint", "plan", "--facts", str(self.facts),
                           "--goal", "awareness.stars",
                           "--platforms", "github,hackernews,reddit",
                           "--days", "3", "--start", "2026-11-05")
        self.assertIn("3 天冲刺", out)
        self.assertIn("小时级排期", out)

    def test_sprint_rejects_bad_days(self):
        proc = subprocess.run([sys.executable, str(MKT), "sprint", "plan",
                               "--facts", str(self.facts), "--goal", "awareness.stars",
                               "--days", "0"], cwd=ROOT, env=self.env,
                              text=True, capture_output=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("天数", proc.stderr)


class SprintMcpTests(unittest.TestCase):
    def test_readonly_tool(self):
        from mcp_server import _call_tool
        res = _call_tool("mkt_sprint_plan", {
            "facts": _facts(), "goal": "awareness.stars",
            "secondary": "adoption.real_data",
            "platforms": ["github", "hackernews", "reddit"], "days": 3})
        self.assertFalse(res.get("isError"))
        plan = json.loads(res["content"][0]["text"])
        self.assertEqual(plan["days"], 3)
        self.assertEqual(len(plan["blocks"]) >= 15, True)


if __name__ == "__main__":
    unittest.main()
