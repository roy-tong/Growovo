"""策略引擎：主渠道唯一、不做清单、五阶段、放大条件、风控映射。"""
import unittest

from core import strategy


def _analysis():
    return {"channels": [
        {"channel": "github", "name": "GitHub", "actions": 77, "responses": 4,
         "outcomes": 30, "response_per_100": 5.19, "outcome_per_100": 38.96},
        {"channel": "x", "name": "X", "actions": 900, "responses": 5,
         "outcomes": 0, "response_per_100": 0.56, "outcome_per_100": 0.0},
        {"channel": "email", "name": "邮件", "actions": 1200, "responses": 9,
         "outcomes": 1, "response_per_100": 0.75, "outcome_per_100": 0.08},
    ]}


class StrategyTests(unittest.TestCase):
    def test_primary_is_single_and_has_account_requirements(self):
        s = strategy.build({"name": "Verovo"},
                           focus={"primary": "github", "launch": "hackernews",
                                  "deferred": ["reddit", "linkedin", "product-hunt"]},
                           analysis=_analysis())
        self.assertEqual(s["primary"]["id"], "github")
        self.assertTrue(s["primary"]["account"])
        self.assertEqual(s["launch"]["id"], "hackernews")

    def test_not_doing_uses_retro_numbers(self):
        s = strategy.build({"name": "Verovo"},
                           focus={"primary": "github", "deferred": []},
                           analysis=_analysis())
        names = [x["name"] for x in s["not_doing"]]
        self.assertIn("X", names)
        why = next(x["why"] for x in s["not_doing"] if x["name"] == "X")
        self.assertIn("900", why)

    def test_five_cold_start_stages_with_criteria(self):
        s = strategy.build({"name": "x"}, focus={"primary": "github"}, analysis=_analysis())
        self.assertEqual(len(s["cold_start"]), 5)
        self.assertEqual(s["cold_start"][0]["stage"], "有资格")
        self.assertTrue(all(x["criterion"] for x in s["cold_start"]))
        self.assertEqual([x["stage"] for x in s["cold_start"]][2], "被问到")

    def test_scale_needs_signal_not_volume(self):
        s = strategy.build({"name": "x"}, focus={"primary": "reddit"}, analysis=_analysis())
        self.assertIn("信号", strategy.render(s))
        self.assertTrue(s["scale"]["signal"])
        self.assertIn("加人", s["scale"]["how"])

    def test_risk_maps_detection_to_signal_to_fix(self):
        s = strategy.build({"name": "x"}, focus={"primary": "hackernews"}, analysis=_analysis())
        self.assertTrue(s["risk"])
        for r in s["risk"]:
            self.assertTrue(r["detect"] and r["signal"] and r["adjust"])

    def test_unknown_platform_falls_back_not_crashes(self):
        s = strategy.build({"name": "x"}, focus={"primary": "not-a-real-platform"}, analysis={})
        self.assertEqual(s["primary"]["id"], "not-a-real-platform")
        self.assertTrue(s["primary"]["account"])
        self.assertEqual(len(s["cold_start"]), 5)

    def test_render_is_business_language(self):
        s = strategy.build({"name": "Verovo"}, focus={"primary": "github", "launch": "hackernews"},
                           analysis=_analysis())
        md = strategy.render(s)
        for part in ("主渠道只有一个", "这一轮不做", "冷启动五个阶段", "什么时候可以放大",
                     "平台会怎么查我们", "第一周三件事"):
            self.assertIn(part, md)
        for code in ("P0", "P1", "PROVEN", "UNVERIFIED"):
            self.assertNotIn(code, md)


if __name__ == "__main__":
    unittest.main()
