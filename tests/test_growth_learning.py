import tempfile
import unittest

from core import growth_engagement, growth_experiments, growth_learning, growth_service
from tests.test_growth_goals import goal


class GrowthLearningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.service = growth_service.start(self.data, goal())
        self.sid = self.service["id"]

    def test_allocation_prioritizes_existing_user_without_spending_manual_budget(self):
        growth_engagement.import_interactions(self.data, self.sid, [{
            "source": "github", "external_id": "question", "author": "user",
            "body": "Need help activating", "received_at": "2026-11-23T22:00:00+08:00",
            "existing_user": True,
        }])
        got = growth_learning.allocate(self.data, self.sid, human_minutes=0)
        self.assertEqual("conv-github-question", got["selected"][0]["conversation_id"])
        self.assertEqual(0, got["manual_minutes_allocated"])

    def test_manual_work_is_deferred_when_over_budget(self):
        for action_id in ("diagnose-product", "baseline-observe", "asset-prepare"):
            growth_service.record_action(self.data, self.sid, action_id, "completed", by="tester")
        got = growth_learning.allocate(self.data, self.sid, human_minutes=0)
        self.assertTrue(got["deferred"])
        self.assertTrue(all(row["id"] != "x-publish" for row in got["selected"]))

    def test_manual_baseline_comparison_uses_the_same_work_definition(self):
        got = growth_learning.compare_baseline(
            {"definition": "same-work", "human_minutes": 120, "accepted_tasks": 4,
             "major_errors": 0, "quality_score": 80},
            {"definition": "same-work", "human_minutes": 60, "accepted_tasks": 4,
             "major_errors": 0, "quality_score": 80})
        self.assertEqual(50.0, got["time_reduction_percent"])
        self.assertTrue(got["quality_not_below"])
        with self.assertRaisesRegex(ValueError, "definition"):
            growth_learning.compare_baseline(
                {"definition": "a", "human_minutes": 1, "accepted_tasks": 1},
                {"definition": "b", "human_minutes": 1, "accepted_tasks": 1})

    def test_advice_targets_the_actual_funnel_break_and_requires_human_for_scope_changes(self):
        growth_experiments.create(self.data, self.sid, {
            "id": "activation-test", "hypothesis": "demo drives activation",
            "primary_metric": "activation", "target": 1,
            "window": {"start": "2026-11-23T22:00:00+08:00",
                       "end": "2026-11-24T07:00:00+08:00"},
            "stop_rule": "stop on warning",
        })
        growth_experiments.observe(self.data, self.sid, "activation-test", [
            {"id": "e1", "type": "signal", "subject": "u1", "source": "x",
             "at": "2026-11-23T22:30:00+08:00"},
            {"id": "e2", "type": "click", "subject": "u1", "source": "github",
             "at": "2026-11-23T22:31:00+08:00"},
        ])
        got = growth_learning.advise(self.data, self.sid)
        self.assertEqual("first_value_path", got["kind"])
        self.assertTrue(got["requires_human"])


if __name__ == "__main__":
    unittest.main()
