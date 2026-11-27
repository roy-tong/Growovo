import copy
import json
import unittest
from pathlib import Path

from core import growth_quality, output_strategy


class GrowthQualityTest(unittest.TestCase):
    def setUp(self):
        self.document = output_strategy.compile_strategy(json.loads(Path(
            "examples/execution-strategy-300.json").read_text()))

    def item(self, context, text, product="verovo"):
        return {"id": "quality-item", "wave_id": "w01", "product": product,
                "platform": "x", "action": "reply", "goal_ids": ["joint_recalculation"],
                "target": {"handle": "maintainer", "url": "https://x.com/maintainer/status/1",
                           "context": context,
                           "qualification": {"persona": "AI 网关维护者", "trigger": context,
                                             "source": "public-issue", "observed_at": "2026-11-27T08:00:00+08:00"}},
                "content": {"text": text},
                "measurement": {"execution_event": "sent", "interaction_event": "reply",
                                "business_event": "joint_recalculation"}}

    def test_specific_task_and_actionable_reply_scores_high(self):
        item = self.item(
            "Our gateway double counts retry attempts in the invoice and distorts LLM cost per accepted task.",
            "Add a fixture with one initial call, one retry, and one accepted result. Compare the provider usage counter with the invoice before treating the two requests as three.")
        result = growth_quality.evaluate_item(item, self.document)
        self.assertGreaterEqual(result["score"], 70)
        self.assertEqual("recalculate_together", result["communication"]["hook_type"])
        self.assertIn("具体复现步骤", result["communication"]["required_parts"])

    def test_generic_news_and_template_content_scores_low(self):
        item = self.item("AI is changing everything.", "I would innovate with robust AI.")
        result = growth_quality.evaluate_item(item, self.document)
        self.assertLess(result["score"], 50)
        self.assertIn("low_task_relevance", result["issues"])
        self.assertIn("template_language", result["issues"])

    def test_hook_selection_distinguishes_failure_decision_and_benchmark(self):
        cases = [
            ("The agent fails on timeout", "reproduce_failure"),
            ("Should we change this product decision?", "review_offer"),
            ("Benchmark results differ across models", "compare_accepted_results"),
        ]
        for context, expected in cases:
            with self.subTest(expected=expected):
                item = self.item(context, "Use the same input and compare the accepted result.")
                self.assertEqual(expected, growth_quality.communication_brief(item, self.document)["hook_type"])

    def test_allocation_scales_with_quality_and_results_but_reserves_exploration(self):
        result = growth_quality.allocate({
            "total_actions": 100, "min_quality_score": 70, "explore_ratio": 0.2,
            "candidates": [
                {"id": "verovo-billing", "product": "verovo", "quality_score": 85,
                 "evidence": {"actions": 10, "qualified_interactions": 2, "first_runs": 1, "accepted_results": 1}},
                {"id": "askovo-new", "product": "askovo", "quality_score": 80,
                 "evidence": {"actions": 20}},
                {"id": "low-quality", "product": "growovo", "quality_score": 50, "evidence": {}},
            ]})
        self.assertEqual(100, sum(row["allocation"] for row in result["candidates"]))
        self.assertEqual(20, result["explore_reserved"])
        explore = next(row for row in result["candidates"] if row["id"] == "askovo-new")
        self.assertEqual("explore", explore["mode"])
        self.assertEqual(20, explore["allocation"])
        self.assertEqual(0, next(row for row in result["candidates"] if row["id"] == "low-quality")["allocation"])

    def test_zero_accepted_results_remains_exploration(self):
        result = growth_quality.allocate({"total_actions": 10, "candidates": [
            {"id": "unproven", "quality_score": 80,
             "evidence": {"actions": 10, "accepted_results": 0}}
        ]})
        self.assertEqual("explore", result["candidates"][0]["mode"])
        self.assertEqual(10, result["candidates"][0]["allocation"])

    def test_incomplete_funnel_does_not_zero_fill_candidate_evidence(self):
        payload = {"candidates": [{"id": "c", "quality_score": 80, "evidence": {}}]}
        events = [{"event_id": "a", "event_type": "committed", "campaign_id": "c",
                   "subject_id": "s", "occurred_at": "2026-11-27T09:00:00+08:00"}]
        enriched = growth_quality.attach_funnel_evidence(payload, events, complete_observation=False)
        self.assertEqual(1, enriched["candidates"][0]["evidence"]["actions"])
        self.assertNotIn("accepted_results", enriched["candidates"][0]["evidence"])
        self.assertNotIn("first_runs", enriched["candidates"][0]["evidence"])

    def test_unknown_outcomes_are_not_zero_rates(self):
        self.assertIsNone(growth_quality.metric_rate({"actions": 0}, "first_runs"))
        self.assertIsNone(growth_quality.metric_rate({"actions": 10}, "first_runs"))
        self.assertEqual(0.1, growth_quality.metric_rate(
            {"actions": 10, "first_runs": 1}, "first_runs"))

    def test_funnel_computes_marginal_outcomes_per_hundred_actions(self):
        events = [
            {"event_id": "a1", "event_type": "committed", "campaign_id": "c1", "subject_id": "s1", "occurred_at": "2026-11-27T09:00:00+08:00"},
            {"event_id": "a2", "event_type": "committed", "campaign_id": "c1", "subject_id": "s2", "occurred_at": "2026-11-27T09:01:00+08:00"},
            {"event_id": "b1", "event_type": "qualified_interaction", "campaign_id": "c1", "subject_id": "s1", "occurred_at": "2026-11-27T09:02:00+08:00"},
            {"event_id": "c1", "event_type": "first_run", "campaign_id": "c1", "subject_id": "s1", "occurred_at": "2026-11-27T09:03:00+08:00"},
            {"event_id": "d1", "event_type": "accepted_result", "campaign_id": "c1", "subject_id": "s1", "occurred_at": "2026-11-27T09:04:00+08:00"},
            {"event_id": "e1", "event_type": "second_use", "campaign_id": "c1", "subject_id": "s1", "occurred_at": "2026-11-27T09:05:00+08:00"},
            {"event_id": "a1", "event_type": "committed", "campaign_id": "c1", "subject_id": "s1", "occurred_at": "2026-11-27T09:00:00+08:00"},
        ]
        result = growth_quality.project_funnel(events, complete_observation=True)
        group = result["groups"][0]
        self.assertEqual(2, group["counts"]["committed"])
        self.assertEqual(50, group["per_100_actions"]["first_runs"])
        self.assertEqual(50, group["per_100_actions"]["accepted_results"])
        self.assertEqual(50, group["per_100_actions"]["second_use"])
        self.assertEqual(1, result["deduplicated_events"])

    def test_incomplete_observation_keeps_rates_unknown(self):
        events = [{"event_id": "a1", "event_type": "committed", "campaign_id": "c1",
                   "subject_id": "s1", "occurred_at": "2026-11-27T09:00:00+08:00"}]
        group = growth_quality.project_funnel(events)["groups"][0]
        self.assertIsNone(group["per_100_actions"]["first_runs"])
        self.assertEqual("not_observed", group["stage_status"]["first_run"])


if __name__ == "__main__":
    unittest.main()
