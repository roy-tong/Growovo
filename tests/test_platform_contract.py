"""全平台注册表与策略/执行生成契约。"""
import tempfile
import unittest
from pathlib import Path

from core import cadence, execute, gtm, platforms, strategy


class PlatformContractTests(unittest.TestCase):
    def test_registry_is_unique_and_free_of_case_defaults(self):
        ids = [p["id"] for p in platforms.PLATFORMS]
        self.assertEqual(37, len(ids))
        self.assertEqual(len(ids), len(set(ids)))
        source = Path(platforms.__file__).read_text(encoding="utf-8")
        self.assertEqual(1, source.count("_CORE = ["))
        self.assertNotIn("Verovo", source)
        self.assertNotIn("star 净增", source)

    def test_gtm_accepts_learning_intent_without_numeric_goals(self):
        facts = {
            "product": {"name": "X", "what": "test", "url": "https://example.invalid",
                        "stage": "launched", "product_type": "devtool"},
            "icp": [{"hypothesis": "开发者", "tags": ["developers"],
                     "evidence_label": "hypothesis", "evidence": "待验证"}],
            "value_events": [{"name": "激活", "source": "产品事件", "is_approximation": False}],
            "constraints": {"red_lines": ["不做群发"]},
            "owned_assets": {"platforms": ["github"]},
        }
        result = gtm.diagnose(facts)
        self.assertIn("focus", result)
        self.assertFalse(any("20–40" in x for x in result["unresolved"]))

    def test_every_platform_generates_strategy_cadence_and_manual_sheet(self):
        for p in platforms.PLATFORMS:
            with self.subTest(platform=p["id"]):
                s = strategy.build({"name": "X"}, focus={"primary": p["id"], "deferred": []}, analysis={})
                self.assertEqual(p["id"], s["primary"]["id"])
                self.assertIn("冷启动", strategy.render(s))
                plan = cadence.build(s, start="2026-11-04")
                self.assertEqual(4, len(plan["weeks"]))
                with tempfile.TemporaryDirectory() as tmp:
                    result = execute.build_manual(
                        s, [{"target": "user@example.com", "body": "先给一个可复现的解决办法"}],
                        campaign=f"c-{p['id']}", data_dir=Path(tmp))
                    self.assertTrue(result["manual"])
                    text = Path(result["sheet"]).read_text(encoding="utf-8")
                    self.assertIn("人工作业单", text)
                    self.assertIn("怎么说话", text)

    def test_x_strategy_builds_reply_queue_not_unsupported_message_queue(self):
        s = strategy.build({"name": "X"}, focus={"primary": "x", "deferred": []}, analysis={})
        self.assertEqual(("x", "reply"), execute.resolve_channel(s))
        with tempfile.TemporaryDirectory() as tmp:
            result = execute.build(s, [{"target": "@example", "body": "具体问题的最小复现"}],
                                   campaign="x-reply-test", data_dir=Path(tmp))
            self.assertEqual("reply", result["action"])
            self.assertTrue(Path(result["queue"]).exists())


if __name__ == "__main__":
    unittest.main()
