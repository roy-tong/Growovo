import json
import unittest
from pathlib import Path

from core import output_strategy
from core import preparation_plan


class PreparationPlanTest(unittest.TestCase):
    def setUp(self):
        payload = json.loads(Path("examples/execution-strategy-300.json").read_text())
        self.strategy = output_strategy.compile_strategy(payload)

    def test_all_independent_roots_start_ready(self):
        tasks = self.strategy["preparation"]["tasks"]
        by_id = {row["id"]: row for row in tasks}
        self.assertEqual("ready", by_id["generate-original-titles"]["status"])
        self.assertEqual("ready", by_id["mine-reply-topics"]["status"])
        self.assertEqual("ready", by_id["mine-koc"]["status"])
        self.assertEqual("ready", by_id["mine-dm-prospects"]["status"])
        self.assertEqual("pending", by_id["review-originals-my-voice"]["status"])

    def test_original_review_requires_my_voice_and_current_hash(self):
        tasks = self.strategy["preparation"]["tasks"]
        review = next(row for row in tasks if row["id"] == "review-originals-my-voice")
        self.assertEqual("my-voice", review["config"]["reviewer"])
        self.assertEqual(["generate-original-content"], review["depends_on"])
        self.assertTrue(review["config"]["requires_source_sha256"])

    def test_reply_and_dm_branches_have_explicit_stages(self):
        ids = [row["id"] for row in self.strategy["preparation"]["tasks"]]
        self.assertLess(ids.index("qualify-reply-targets"), ids.index("draft-replies"))
        self.assertLess(ids.index("qualify-dm-prospects"), ids.index("draft-dms"))
        self.assertLess(ids.index("draft-replies"), ids.index("review-reply-strategy"))
        self.assertLess(ids.index("draft-dms"), ids.index("review-dm-content"))

    def test_generation_without_adapter_is_manual_not_complete(self):
        tasks = self.strategy["preparation"]["tasks"]
        generate = next(row for row in tasks if row["id"] == "generate-original-titles")
        self.assertEqual("manual", generate["runner"])
        self.assertEqual("ready", generate["status"])

    def test_supply_keys_come_from_strategy_not_hardcoded_ovo_rules(self):
        strategy = self.strategy
        strategy["waves"] = strategy["waves"][:1]
        strategy["waves"][0]["allocations"] = {"demo": {"reddit/reply": 2}}
        plan = preparation_plan.build_preparation_plan(strategy)
        reply = next(row for row in plan["tasks"] if row["id"] == "mine-reply-topics")
        self.assertEqual(["reddit/reply"], reply["supply_keys"])

    def test_host_adapter_configuration_is_bound_to_generation_task(self):
        strategy = self.strategy
        strategy["preparation"]["adapters"]["generate-original-titles"] = {
            "runner": "host_adapter", "adapter": {"adapter_id": "writer"}}
        plan = preparation_plan.build_preparation_plan(strategy)
        task = next(row for row in plan["tasks"] if row["id"] == "generate-original-titles")
        self.assertEqual("host_adapter", task["runner"])
        self.assertEqual({"adapter_id": "writer"}, task["config"]["adapter"])


if __name__ == "__main__":
    unittest.main()
