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

    def test_recovery_lint_requires_original_results_and_preserves_scope(self):
        plan = output_strategy.compile_strategy(self.payload)
        original = {"id": "w02-askovo-x-reply-002", "wave_id": "w02", "product": "askovo",
                    "platform": "x", "action": "reply", "goal_ids": ["demo"],
                    "target": {"url": "https://x.com/original/status/1", "context": "context",
                               "qualification": {"persona": "developer"}},
                    "content": {"text": "old", "sha256": "unused"},
                    "measurement": {"execution_event": "sent", "interaction_event": "reply",
                                    "business_event": "demo"}}
        original["content"]["sha256"] = __import__("hashlib").sha256(b"old").hexdigest()
        recovery = copy.deepcopy(original)
        recovery.update(id="recovery-askovo-x-reply-001", wave_id="w10", replaces=original["id"])
        recovery["target"] = dict(original["target"], url="https://x.com/new/status/2")
        plan["items"] = [original, recovery]
        self.assertIn("recovery_results_required", output_strategy.lint_strategy(plan))
        plan["execution_results"] = [{"queue_item_id": original["id"], "result": "failed",
                                      "target": original["target"]["url"]}]
        errors = output_strategy.lint_strategy(plan)
        self.assertNotIn("recovery_results_required", errors)
        self.assertNotIn("recovery_scope_mismatch", errors)
        recovery["replaces"] = "missing"
        self.assertIn("recovery_original_missing", output_strategy.lint_strategy(plan))

    def test_recovery_does_not_reduce_unmaterialized_original_supply(self):
        payload = copy.deepcopy(self.payload)
        original = {"id": "original", "wave_id": "w02", "product": "askovo",
                    "platform": "x", "action": "reply", "goal_ids": ["demo"],
                    "target": {"url": "https://x.com/original/status/1", "context": "context",
                               "qualification": {"persona": "developer"}},
                    "content": {"text": "old"},
                    "measurement": {"execution_event": "sent", "interaction_event": "reply",
                                    "business_event": "demo"}}
        recovery = copy.deepcopy(original)
        recovery.update(id="recovery", wave_id="w10", replaces="original")
        recovery["target"] = dict(original["target"], url="https://x.com/new/status/2")
        payload["items"] = [original, recovery]
        payload["execution_results"] = [{"queue_item_id": "original", "result": "failed",
                                         "target": original["target"]["url"]}]
        plan = output_strategy.compile_strategy(payload)
        self.assertEqual(1, plan["readiness"]["materialized"])
        self.assertEqual(1, plan["readiness"]["eligible"])
        self.assertEqual(299, plan["readiness"]["missing"])
        self.assertEqual(1, plan["readiness"]["recovery_items"])

    def test_renderer_exposes_goals_waves_todos_targets_and_measurement(self):
        text = output_strategy.render_strategy(output_strategy.compile_strategy(self.payload))
        for expected in ("目标拆解", "波次矩阵", "备料 TODO", "对象与正文", "验收事件"):
            self.assertIn(expected, text)

    def test_zero_quota_is_omitted_from_wave_allocations(self):
        payload = copy.deepcopy(self.payload)
        payload["products"][0]["per_wave"]["x/message"] = 0
        payload["campaign"]["total_actions"] = 290
        plan = output_strategy.compile_strategy(payload)
        self.assertNotIn("x/message", plan["waves"][0]["allocations"]["askovo"])
        from tests.test_preparation_runtime import ready_items
        plan["items"] = [item for wave in plan["waves"] for item in ready_items(plan, wave["id"])]
        self.assertNotIn("wave_allocation_mismatch",
                         output_strategy.lint_strategy(plan, require_ready=True))

    def test_quality_floor_blocks_generic_material_when_ready_is_required(self):
        payload = copy.deepcopy(self.payload)
        payload["products"] = [payload["products"][0]]
        payload["products"][0]["per_wave"] = {"x/reply": 1}
        payload["campaign"].update(total_actions=1, wave_count=1, quality_floor=70)
        payload["items"] = [{"id": "generic", "wave_id": "w01", "product": "askovo",
                             "platform": "x", "action": "reply", "goal_ids": ["decision_replay"],
                             "target": {"url": "https://x.com/generic/status/1", "handle": "generic",
                                        "context": "AI news", "qualification": {"persona": "general"}},
                             "content": {"text": "I would innovate."},
                             "measurement": {"execution_event": "sent", "interaction_event": "reply",
                                             "business_event": "decision_replay"}}]
        plan = output_strategy.compile_strategy(payload)
        self.assertEqual(1, plan["readiness"]["quality"]["below_floor"])
        self.assertIn("quality_below_floor", output_strategy.lint_strategy(plan, require_ready=True))

    def test_dm_quota_requires_independent_channel_acceptance(self):
        payload = copy.deepcopy(self.payload)
        payload["products"] = [payload["products"][0]]
        payload["products"][0]["per_wave"] = {"x/message": 1}
        payload["campaign"].update(total_actions=1, wave_count=1)
        payload["items"] = [{"id": "dm", "wave_id": "w01", "product": "askovo",
                             "platform": "x", "action": "message", "goal_ids": ["decision_replay"],
                             "target": {"handle": "person", "context": "decision review",
                                        "qualification": {"dm_open": True, "persona": "manager"}},
                             "content": {"text": "Would you review one redacted decision with me?"},
                             "measurement": {"execution_event": "sent", "interaction_event": "reply",
                                             "business_event": "decision_replay"}}]
        plan = output_strategy.compile_strategy(payload)
        self.assertIn("x/message", plan["readiness"]["independent_channel_acceptance"])

    def test_invalid_action_budget_is_rejected(self):
        payload = copy.deepcopy(self.payload)
        payload["products"][0]["per_wave"]["x/publish"] = -1
        with self.assertRaisesRegex(ValueError, "动作配额"):
            output_strategy.compile_strategy(payload)


if __name__ == "__main__":
    unittest.main()
