import json
import unittest
from pathlib import Path

from core import output_strategy


class GuidedIntakeTest(unittest.TestCase):
    def test_minimal_draft_returns_one_useful_question(self):
        result = output_strategy.guide({"product": {"name": "Askovo"}})
        self.assertFalse(result["ready"])
        self.assertEqual("needs_input", result["status"])
        self.assertEqual("product.url", result["next_question"]["field"])
        self.assertIn("为什么", result["next_question"]["why"])
        self.assertIn("example", result["next_question"])
        self.assertIn("output-guide", result["next_question"]["answer_command"])
        self.assertGreater(len(result["remaining_fields"]), 1)
        self.assertNotIn(result["next_question"]["field"], result["answered_fields"])

    def test_answers_advance_without_losing_existing_input(self):
        draft = output_strategy.apply_answer(
            {"product": {"name": "Askovo"}},
            "product.url", "https://github.com/roy-tong/Askovo")
        self.assertEqual("Askovo", draft["product"]["name"])
        result = output_strategy.guide(draft)
        self.assertEqual("outcomes.primary", result["next_question"]["field"])

    def test_answer_rejects_unknown_field(self):
        with self.assertRaisesRegex(ValueError, "未知引导字段"):
            output_strategy.apply_answer({}, "unknown.field", 1)

    def test_question_can_be_skipped_with_explicit_default(self):
        draft = output_strategy.apply_answer({}, "product.name", "Askovo")
        question = output_strategy.guide(draft)["next_question"]
        draft = output_strategy.apply_answer(
            draft, question["field"], question["suggested_default"])
        self.assertEqual("outcomes.primary", output_strategy.guide(draft)["next_question"]["field"])

    def test_multi_product_missing_primary_outcome_is_not_ready(self):
        payload = json.loads(Path("examples/execution-strategy-300.json").read_text())
        payload["products"][0]["outcomes"] = {}
        result = output_strategy.guide(payload)
        self.assertFalse(result["ready"])
        self.assertEqual("outcomes.primary", result["next_question"]["field"])

    def test_missing_total_budget_is_not_hidden_by_product_mix(self):
        payload = json.loads(Path("examples/execution-strategy-300.json").read_text())
        del payload["campaign"]["total_actions"]
        result = output_strategy.guide(payload)
        self.assertFalse(result["ready"])
        self.assertEqual("budget", result["next_question"]["field"])


if __name__ == "__main__":
    unittest.main()
