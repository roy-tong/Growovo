"""目标选择：目录完整性、产品适配、平台分工、选择校验、策略/动作表联动。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import cadence, goals, strategy
from core.platforms import BY_ID as PLATFORM_BY_ID


ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"


def _facts(product_type="service", stage="launched", owned=None, baseline=None):
    facts = {
        "product": {"name": "Verovo", "what": "AI 账期对账", "url": "https://example.com",
                    "stage": stage, "product_type": product_type},
        "icp": [
            {"hypothesis": "FinOps 负责人", "tags": ["b2b-buyers"],
             "evidence_label": "self_research", "evidence": "r/FinOps 10+ 条"},
            {"hypothesis": "技术创始人", "tags": ["technical-founders"],
             "evidence_label": "hypothesis", "evidence": "待验证"},
        ],
        "value_events": [{"name": "付费", "source": "收款记录", "is_approximation": False}],
        "constraints": {"red_lines": ["不做群发"], "human_minutes_budget": 840},
        "owned_assets": {"platforms": owned or ["github", "reddit", "hackernews"]},
    }
    if baseline is not None:
        facts["baseline"] = baseline
    return facts


class CatalogTests(unittest.TestCase):
    def test_nine_goals_with_required_fields(self):
        self.assertEqual(len(goals.GOALS), 9)
        ids = [g["id"] for g in goals.GOALS]
        self.assertEqual(len(ids), len(set(ids)))
        required = ("id", "family", "name", "metric", "metric_label", "definition",
                    "product_types", "affinity", "target_kind", "default_target",
                    "proves", "does_not_prove", "leading", "counter",
                    "stop_rules", "switch_rules")
        for spec in goals.GOALS:
            for key in required:
                self.assertIn(key, spec, f"{spec['id']} 缺 {key}")
            self.assertIn(spec["family"], goals.FAMILIES)
            self.assertGreater(spec["default_target"], 0)
            self.assertTrue(spec["stop_rules"])
            self.assertTrue(spec["switch_rules"])

    def test_every_affinity_platform_exists(self):
        for spec in goals.GOALS:
            for pid in spec["affinity"]:
                self.assertIn(pid, PLATFORM_BY_ID, f"{spec['id']} 引用了不存在的平台 {pid}")

    def test_every_goal_has_a_non_launch_platform(self):
        for spec in goals.GOALS:
            non_launch = [p for p in spec["affinity"]
                          if p in PLATFORM_BY_ID and
                          p not in strategy.gtm.LAUNCH_PLATFORMS]
            self.assertTrue(non_launch, spec["id"])


class ApplicabilityTests(unittest.TestCase):
    def test_devtool_gets_awareness_and_adoption(self):
        opts = goals.list_goals(_facts("devtool"))
        by_id = {g["id"]: g for g in opts["goals"]}
        self.assertIn(by_id["awareness.stars"]["fit"]["level"], ("推荐", "可选"))
        self.assertIn(by_id["adoption.forks"]["fit"]["level"], ("推荐", "可选"))
        self.assertIn(by_id["commercial.first_paid"]["fit"]["level"], ("推荐", "可选"))

    def test_service_with_github_can_choose_stars(self):
        opts = goals.list_goals(_facts("service", owned=["github"]))
        stars = next(g for g in opts["goals"] if g["id"] == "awareness.stars")
        self.assertNotEqual(stars["fit"]["level"], "不适配")
        self.assertIn("已有 github", stars["fit"]["why"])

    def test_service_without_x_cannot_choose_followers(self):
        opts = goals.list_goals(_facts("service", owned=["github"]))
        followers = next(g for g in opts["goals"] if g["id"] == "awareness.followers")
        self.assertEqual(followers["fit"]["level"], "不适配")

    def test_repeat_requires_baseline(self):
        opts = goals.list_goals(_facts("service", baseline={"paid_deals": 0}))
        repeat = next(g for g in opts["goals"] if g["id"] == "commercial.repeat")
        self.assertEqual(repeat["fit"]["level"], "不适配")
        self.assertIn("基线", repeat["fit"]["why"])

    def test_repeat_available_after_first_paid(self):
        opts = goals.list_goals(_facts("service", baseline={"paid_deals": 1,
                                                           "repeat_purchases": 0}))
        repeat = next(g for g in opts["goals"] if g["id"] == "commercial.repeat")
        self.assertNotEqual(repeat["fit"]["level"], "不适配")

    def test_idea_stage_rejected(self):
        opts = goals.list_goals(_facts("devtool", stage="idea"))
        for g in opts["goals"]:
            self.assertEqual(g["fit"]["level"], "不适配", g["id"])


class PlanTests(unittest.TestCase):
    def test_plan_has_roles_within_limits(self):
        plan = goals.build_plan(_facts(), "commercial.first_paid")
        roles = plan["platforms"]
        self.assertIsNotNone(roles["primary"])
        self.assertLessEqual(len(roles["support"]), goals.MAX_SUPPORT)
        ids = [roles["primary"]["id"]]
        if roles["launch"]:
            ids.append(roles["launch"]["id"])
        ids += [r["id"] for r in roles["support"]]
        self.assertLessEqual(len(ids), goals.MAX_PLATFORMS)
        self.assertNotIn(roles["primary"]["id"], strategy.gtm.LAUNCH_PLATFORMS)

    def test_user_selection_controls_primary(self):
        plan = goals.build_plan(_facts(), "commercial.first_paid",
                                platforms=["reddit", "hackernews", "github"])
        self.assertEqual(plan["platforms"]["primary"]["id"], "reddit")
        self.assertEqual(plan["platforms"]["launch"]["id"], "hackernews")
        self.assertEqual([r["id"] for r in plan["platforms"]["support"]], ["github"])

    def test_launch_only_is_rejected(self):
        with self.assertRaises(goals.GoalError):
            goals.build_plan(_facts(), "commercial.first_paid", platforms=["hackernews"])

    def test_platform_without_goal_fit_is_rejected(self):
        with self.assertRaises(goals.GoalError):
            goals.build_plan(_facts(), "commercial.first_paid", platforms=["tiktok"])

    def test_duplicate_platforms_rejected(self):
        with self.assertRaises(goals.GoalError):
            goals.build_plan(_facts(), "commercial.first_paid",
                             platforms=["reddit", "reddit"])

    def test_too_many_platforms_rejected(self):
        with self.assertRaises(goals.GoalError):
            goals.build_plan(_facts(), "commercial.first_paid",
                             platforms=["reddit", "hackernews", "github", "linkedin",
                                        "email"])

    def test_inapplicable_goal_rejected(self):
        with self.assertRaises(goals.GoalError):
            goals.build_plan(_facts("service", owned=["github"]), "awareness.followers")

    def test_target_and_window_validated(self):
        with self.assertRaises(goals.GoalError):
            goals.build_plan(_facts(), "commercial.first_paid", target=0)
        with self.assertRaises(goals.GoalError):
            goals.build_plan(_facts(), "commercial.first_paid", window_days=3)

    def test_baseline_is_not_invented(self):
        plan = goals.build_plan(_facts(), "commercial.first_paid")
        self.assertIsNone(plan["goal"]["baseline"])
        self.assertIn("基线未提供", plan["goal"]["target_text"])
        plan = goals.build_plan(_facts(baseline={"paid_deals": 0}),
                                "commercial.first_paid")
        self.assertEqual(plan["goal"]["baseline"], 0.0)
        self.assertNotIn("基线未提供", plan["goal"]["target_text"])

    def test_delta_target_uses_baseline(self):
        plan = goals.build_plan(_facts("devtool", baseline={"github_stars": 220}),
                                "awareness.stars")
        self.assertEqual(plan["goal"]["target_value"], 270.0)
        self.assertIn("220", plan["goal"]["target_text"])
        self.assertIn("270", plan["goal"]["target_text"])

    def test_plan_is_deterministic(self):
        a = goals.build_plan(_facts(), "commercial.first_paid")
        b = goals.build_plan(_facts(), "commercial.first_paid")
        self.assertEqual(a["selection"], b["selection"])
        self.assertEqual(a["platforms"]["primary"]["id"], b["platforms"]["primary"]["id"])

    def test_render_keeps_boundaries(self):
        md = goals.render_plan(goals.build_plan(_facts(), "commercial.first_paid"))
        self.assertIn("目标方案", md)
        self.assertIn("不证明", md)
        self.assertIn("什么时候停", md)
        self.assertIn("目标是选择，不是预测", md)


class SelectionTests(unittest.TestCase):
    def test_selection_contract_roundtrips(self):
        sel = goals.select(_facts(), "commercial.first_paid",
                           ["reddit", "hackernews", "github"],
                           target=1, window_days=42, note="test")
        self.assertEqual(sel["schema"], goals.SELECTION_SCHEMA)
        self.assertEqual(sel["platforms"]["primary"], "reddit")
        self.assertEqual(sel["platforms"]["launch"], "hackernews")
        self.assertEqual(sel["platforms"]["support"], ["github"])
        self.assertEqual(sel["selected"], ["reddit", "hackernews", "github"])
        self.assertEqual(sel["window_days"], 42)
        self.assertTrue(sel["facts_sha256"])


class StrategyIntegrationTests(unittest.TestCase):
    def test_goal_plan_overrides_retro_and_adds_support(self):
        facts = _facts()
        plan = goals.build_plan(facts, "commercial.first_paid",
                                platforms=["reddit", "hackernews", "github"])
        analysis = {"channels": [{
            "channel": "github", "name": "GitHub", "actions": 77,
            "responses": 4, "results": 30, "response_per_100": 5.19,
            "outcome_per_100": 38.96,
        }], "waste": []}
        s = strategy.build(facts["product"], focus={}, analysis=analysis,
                           goal_plan=plan)
        self.assertEqual(s["primary"]["id"], "reddit")
        self.assertEqual(s["goal"]["id"], "commercial.first_paid")
        self.assertEqual([x["id"] for x in s["support"]], ["github"])
        md = strategy.render(s)
        self.assertIn("这一轮的目标", md)
        self.assertIn("平台分工", md)
        self.assertIn("不证明", md)

    def test_goal_cadence_does_not_use_generic_volume(self):
        facts = _facts()
        plan = goals.build_plan(facts, "commercial.first_paid",
                                platforms=["reddit", "hackernews", "github"])
        s = strategy.build(facts["product"], focus={}, goal_plan=plan)
        p = cadence.build(s, start="2026-11-05", goal_plan=plan)
        self.assertEqual(p["goal"], "第一笔付费")
        self.assertEqual(p["actions_planned"], len(plan["actions"]))
        md = cadence.render(p)
        self.assertIn("按目标方案", md)
        self.assertNotIn("计划触达 60 次", md)
        self.assertIn("第一笔付费", md)


class PairPlanTests(unittest.TestCase):
    def test_pair_has_two_metrics_and_combined_actions(self):
        plan = goals.build_plan(_facts(), "awareness.stars",
                                secondary_goal_id="adoption.real_data",
                                platforms=["github", "hackernews", "reddit"])
        self.assertEqual(plan["goal"]["id"], "awareness.stars")
        self.assertEqual(plan["secondary"]["id"], "adoption.real_data")
        self.assertEqual([m["role"] for m in plan["metrics"]],
                         ["primary", "secondary"])
        self.assertTrue(any(a.get("serves") == "both" for a in plan["actions"]))
        md = goals.render_plan(plan)
        self.assertIn("两个都达成", md)
        self.assertIn("声量是 reach", md)
        self.assertIn("组合证明什么", md)

    def test_pair_secondary_must_differ(self):
        with self.assertRaises(goals.GoalError):
            goals.build_plan(_facts(), "awareness.stars",
                             secondary_goal_id="awareness.stars")

    def test_pair_secondary_must_be_applicable(self):
        with self.assertRaises(goals.GoalError):
            goals.build_plan(_facts("service"), "awareness.stars",
                             secondary_goal_id="adoption.activation")

    def test_pair_primary_must_serve_primary(self):
        with self.assertRaises(goals.GoalError):
            goals.build_plan(_facts(), "awareness.stars",
                             secondary_goal_id="adoption.real_data",
                             platforms=["email", "reddit"])

    def test_pair_selection_roundtrip(self):
        sel = goals.select(_facts(), "awareness.stars",
                           ["github", "hackernews", "reddit"],
                           secondary_goal_id="adoption.real_data",
                           secondary_target=2)
        self.assertEqual(sel["secondary_goal_id"], "adoption.real_data")
        self.assertEqual(len(sel["metrics"]), 2)
        self.assertEqual(sel["platforms"]["primary"], "github")
        self.assertEqual(sel["metrics"][1]["target"], 2)

    def test_pair_strategy_and_cadence(self):
        facts = _facts()
        plan = goals.build_plan(facts, "awareness.stars",
                                secondary_goal_id="adoption.real_data",
                                platforms=["github", "hackernews", "reddit"])
        s = strategy.build(facts["product"], focus={}, goal_plan=plan)
        self.assertEqual(s["secondary"]["id"], "adoption.real_data")
        md = strategy.render(s)
        self.assertIn("两个都达成", md)
        self.assertIn("+", md)
        p = cadence.build(s, start="2026-11-05", goal_plan=plan)
        self.assertIn("+", p["goal"])
        self.assertIn("真实日志", cadence.render(p))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir(parents=True)
        self.facts = Path(self.tmp.name) / "facts.json"
        self.facts.write_text(json.dumps(_facts(), ensure_ascii=False), encoding="utf-8")
        self.env = dict(os.environ, MKT_HOME=str(self.home))
        self.sel = Path(self.tmp.name) / "selection.json"

    def run_mkt(self, *args):
        proc = subprocess.run([sys.executable, str(MKT), *args], cwd=ROOT, env=self.env,
                              text=True, capture_output=True)
        if proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed ({proc.returncode})\n"
                      f"STDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return proc.stdout

    def test_goal_list_json(self):
        out = self.run_mkt("goal", "list", "--facts", str(self.facts), "--json")
        payload = json.loads(out)
        self.assertEqual(len(payload["goals"]), 9)
        self.assertIn("boundary", payload)

    def test_goal_plan_json(self):
        out = self.run_mkt("goal", "plan", "--facts", str(self.facts),
                           "--goal", "commercial.first_paid", "--json")
        plan = json.loads(out)
        self.assertEqual(plan["goal"]["id"], "commercial.first_paid")
        self.assertIn("platforms", plan)
        self.assertTrue(plan["actions"])

    def test_select_then_strategy(self):
        self.run_mkt("goal", "select", "--facts", str(self.facts),
                     "--goal", "commercial.first_paid",
                     "--platforms", "reddit,hackernews,github",
                     "--out", str(self.sel))
        sel = json.loads(self.sel.read_text(encoding="utf-8"))
        self.assertEqual(sel["platforms"]["primary"], "reddit")
        out = self.run_mkt("strategy", "build", "--facts", str(self.facts),
                           "--selection", str(self.sel), "--json")
        s = json.loads(out)
        self.assertEqual(s["primary"]["id"], "reddit")
        self.assertEqual(s["goal"]["id"], "commercial.first_paid")

    def test_select_pair_then_strategy(self):
        self.run_mkt("goal", "select", "--facts", str(self.facts),
                     "--goal", "awareness.stars",
                     "--secondary", "adoption.real_data",
                     "--platforms", "github,hackernews,reddit",
                     "--out", str(self.sel))
        sel = json.loads(self.sel.read_text(encoding="utf-8"))
        self.assertEqual(sel["secondary_goal_id"], "adoption.real_data")
        out = self.run_mkt("strategy", "build", "--facts", str(self.facts),
                           "--selection", str(self.sel), "--json")
        s = json.loads(out)
        self.assertEqual(s["primary"]["id"], "github")
        self.assertEqual(s["secondary"]["id"], "adoption.real_data")

    def test_invalid_selection_exits_nonzero(self):
        proc = subprocess.run(
            [sys.executable, str(MKT), "goal", "plan", "--facts", str(self.facts),
             "--goal", "commercial.first_paid", "--platforms", "hackernews"],
            cwd=ROOT, env=self.env, text=True, capture_output=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("集中发布不能单独作为主渠道", proc.stderr)


class McpTests(unittest.TestCase):
    def test_readonly_tools(self):
        from mcp_server import _call_tool
        facts = _facts()
        res = _call_tool("mkt_goals", {"facts": facts})
        self.assertFalse(res.get("isError"))
        payload = json.loads(res["content"][0]["text"])
        self.assertEqual(len(payload["goals"]), 9)
        res = _call_tool("mkt_goal_plan", {
            "facts": facts, "goal": "commercial.first_paid",
            "platforms": ["reddit", "hackernews", "github"]})
        self.assertFalse(res.get("isError"))
        plan = json.loads(res["content"][0]["text"])
        self.assertEqual(plan["platforms"]["primary"]["id"], "reddit")

    def test_mcp_reports_goal_error(self):
        from mcp_server import _call_tool
        res = _call_tool("mkt_goal_plan", {"facts": _facts(), "goal": "nope"})
        self.assertTrue(res.get("isError"))


if __name__ == "__main__":
    unittest.main()
