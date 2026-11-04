"""能力、竞争与进化评估契约。"""
import unittest

from core import evaluation


class EvaluationTests(unittest.TestCase):
    def _run(self, task_id="t1", quality=1, cost=1):
        return {"task_id": task_id,
                "conditions": {"data_ref": "d1", "budget": 100,
                               "time_window": "2026-11", "authorization": "read-only"},
                "metrics": {"quality": quality, "human_minutes": 10,
                            "cost": cost, "verified_outcomes": 1}}

    def test_competition_requires_identical_conditions(self):
        other = self._run(quality=2)
        other["conditions"]["budget"] = 50
        with self.assertRaises(ValueError):
            evaluation.compare_runs(self._run(), other)

    def test_competition_is_metric_by_metric_not_total_score(self):
        result = evaluation.compare_runs(self._run(quality=2, cost=2), self._run(quality=1, cost=1))
        self.assertEqual("ours_higher", result["metrics"][0]["comparison"])
        self.assertEqual("alternative_lower", result["metrics"][2]["comparison"])
        self.assertIn("不是总分排名", result["boundary"])

    def test_evolution_distinguishes_fixed_regression_and_unseen(self):
        result = evaluation.assess_evolution(
            [{"case_id": "old", "status": "failed"}],
            [{"case_id": "old", "status": "passed"},
             {"case_id": "new", "status": "unknown"}])
        self.assertEqual(1, result["summary"]["fixed"])
        self.assertEqual(1, result["summary"]["new_case"])

    def test_external_case_requires_independence_and_real_workflow(self):
        problems = evaluation.validate_external_case({"business_question": "真实问题"})
        self.assertTrue(any("非自家任务" in p for p in problems))
        self.assertTrue(any("现有人工作业流程" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
