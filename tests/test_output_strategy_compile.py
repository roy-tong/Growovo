import copy
import json
import unittest
from pathlib import Path

from core import output_strategy


class StrategyCompileTest(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(Path(
            "examples/execution-strategy-300.json").read_text(encoding="utf-8"))

    def test_reference_campaign_closes_at_300(self):
        plan = output_strategy.compile_strategy(self.payload)
        self.assertEqual("growovo.execution-strategy/1", plan["schema"])
        self.assertEqual(300, plan["readiness"]["required"])
        self.assertEqual(10, len(plan["waves"]))
        for wave in plan["waves"]:
            self.assertEqual(30, wave["total"])
            for product in ("askovo", "growovo", "verovo"):
                self.assertEqual(1, wave["allocations"][product]["x/publish"])
        self.assertEqual({"x/publish": 30, "x/reply": 180,
                          "github/reply": 60, "x/message": 30},
                         plan["readiness"]["by_action"])
        self.assertEqual(0, plan["readiness"]["materialized"])
        self.assertEqual(0, plan["readiness"]["eligible"])

    def test_missing_target_and_text_are_not_ready(self):
        plan = output_strategy.compile_strategy(self.payload)
        plan["items"] = [{"id": "bad", "wave_id": "w01", "product": "askovo",
                          "platform": "x", "action": "reply", "goal_ids": ["demo"],
                          "target": {}, "content": {"text": ""}, "measurement": {}}]
        errors = output_strategy.lint_strategy(plan, require_ready=True)
        self.assertIn("target_missing", errors)
        self.assertIn("text_missing", errors)
        self.assertIn("measurement_missing", errors)

    def test_dm_recipients_are_unique_across_products(self):
        plan = output_strategy.compile_strategy(self.payload)
        item = {"wave_id": "w01", "platform": "x", "action": "message",
                "target": {"handle": "@same", "url": "https://x.com/same",
                           "context": "讨论真实决定", "qualification": {"dm_open": True}},
                "content": {"text": "specific"}, "measurement": {
                    "execution_event": "sent", "interaction_event": "reply",
                    "business_event": "demo"}}
        plan["items"] = [dict(item, id="a", product="askovo", goal_ids=["demo"]),
                         dict(item, id="b", product="growovo", goal_ids=["demo"])]
        self.assertIn("duplicate_dm_handle", output_strategy.lint_strategy(plan))

    def test_renderer_exposes_goals_waves_todos_targets_and_measurement(self):
        text = output_strategy.render_strategy(output_strategy.compile_strategy(self.payload))
        for expected in ("目标拆解", "波次矩阵", "备料 TODO", "对象与正文", "验收事件"):
            self.assertIn(expected, text)

    def test_invalid_action_budget_is_rejected(self):
        payload = copy.deepcopy(self.payload)
        payload["products"][0]["per_wave"]["x/publish"] = -1
        with self.assertRaisesRegex(ValueError, "动作配额"):
            output_strategy.compile_strategy(payload)


if __name__ == "__main__":
    unittest.main()
