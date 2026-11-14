"""策略编译器的契约测试：只允许自动渠道、计划量精确、指纹稳定。"""
import json
import unittest
from pathlib import Path

from core.grow_compile import CompileError, compile_run, fingerprint


def _facts(**over):
    facts = {
        "product": {"name": "Verovo", "what": "AI 账期分摊",
                    "url": "https://example.invalid", "stage": "launched",
                    "product_type": "devtool"},
        "icp": [{"hypothesis": "开发者", "tags": ["developers"],
                 "evidence_label": "hypothesis", "evidence": "待验证"}],
        "baseline": {"followers": 120},
        "value_events": [{"name": "激活", "source": "产品事件",
                          "is_approximation": False}],
        "constraints": {"red_lines": ["不做群发"]},
        "owned_assets": {"platforms": ["x", "email"]},
    }
    facts.update(over)
    return facts


class CompileTests(unittest.TestCase):
    def test_only_auto_channels_allowed(self):
        with self.assertRaises(CompileError) as ctx:
            compile_run(run_id="g1", facts=_facts(), goal_id="awareness.followers",
                        channels=["zhihu"], data_dir=Path("/tmp"))
        self.assertIn("自动执行", str(ctx.exception))

    def test_goal_must_match_auto_channel(self):
        with self.assertRaises(CompileError):
            compile_run(run_id="g1", facts=_facts(), goal_id="adoption.forks",
                        channels=["x"], data_dir=Path("/tmp"))

    def test_compiled_plan_is_executable(self):
        spec = compile_run(run_id="g1", facts=_facts(),
                           goal_id="awareness.followers", channels=["x", "email"],
                           target=20, actions_per_week=20,
                           started="2026-11-14", handle="verovo",
                           data_dir=Path("/tmp"))
        self.assertEqual("growovo.grow-run/2", spec["schema"])
        self.assertEqual(["x", "email"], spec["channels"])
        self.assertEqual(["email:message", "x:reply"], spec["actions"])
        self.assertEqual("x", spec["strategy"]["primary"]["channel"])
        self.assertEqual(120, spec["goal"]["baseline"])
        self.assertEqual(20, spec["goal"]["target"])
        self.assertEqual("2026-11-14", spec["goal"]["starts"])
        self.assertEqual(20, spec["quota"]["actions_per_week"])
        self.assertEqual(20, sum(sum(d.values()) for d in
                                 spec["quota"]["daily_preview"].values()))
        self.assertEqual([{"metric": "x_followers", "handle": "verovo", "daily": True},
                          {"metric": "x_impressions", "handle": "verovo", "daily": True}],
                         spec["measurement"]["readers"])
        self.assertTrue(spec["supply"]["briefs"])
        self.assertTrue(spec["strategy"]["fingerprint"])
        self.assertFalse(spec["go"])
        self.assertEqual("koc", spec["growth_strategy"]["focus"])
        self.assertEqual([1000, 50000], spec["growth_strategy"]["koc_band"])
        self.assertEqual("koc_first", spec["supply"]["briefs"][0]["target_policy"])

    def test_fingerprint_is_stable_and_changes_with_plan(self):
        first = compile_run(run_id="g1", facts=_facts(), goal_id="awareness.followers",
                            channels=["x"], started="2026-11-14",
                            actions_per_week=20, data_dir=Path("/tmp"))
        second = compile_run(run_id="g1", facts=_facts(), goal_id="awareness.followers",
                             channels=["x"], started="2026-11-14",
                             actions_per_week=20, data_dir=Path("/tmp"))
        self.assertEqual(first["strategy"]["fingerprint"],
                         second["strategy"]["fingerprint"])
        third = compile_run(run_id="g1", facts=_facts(), goal_id="awareness.followers",
                            channels=["x"], started="2026-11-14",
                            actions_per_week=28, data_dir=Path("/tmp"))
        self.assertNotEqual(first["strategy"]["fingerprint"],
                            third["strategy"]["fingerprint"])

    def test_publish_action_adds_weight(self):
        spec = compile_run(run_id="g1", facts=_facts(),
                           goal_id="awareness.followers", channels=["x"],
                           actions_per_week=10, actions=["reply", "publish"],
                           started="2026-11-14", data_dir=Path("/tmp"))
        self.assertEqual(["x:publish", "x:reply"], spec["actions"])
        self.assertEqual({"reply", "publish"},
                         set(spec["quota"]["action_weights"]))
        self.assertEqual(10, sum(sum(d.values()) for d in
                                 spec["quota"]["daily_preview"].values()))

    def test_missing_baseline_is_reported_not_invented(self):
        facts = _facts(baseline={})
        spec = compile_run(run_id="g1", facts=facts,
                           goal_id="awareness.followers", channels=["x"],
                           started="2026-11-14", data_dir=Path("/tmp"))
        self.assertIsNone(spec["goal"]["baseline"])


if __name__ == "__main__":
    unittest.main()
