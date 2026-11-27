import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import strategy_intelligence


class StrategyIntelligenceTest(unittest.TestCase):
    def context(self, **changes):
        value = {
            "intent": "scale", "stage": "early", "channels": ["x", "github"],
            "quality_score": 82, "accepted_results": 0, "existing_users": 0,
            "product_assets": ["demo", "fixture"], "paid_allowed": False,
        }
        value.update(changes)
        return value

    def test_census_preserves_raw_and_eligible_counts(self):
        rows = [{"platform": "x", "knowledge_type": "unverified_lead"},
                {"platform": "x", "knowledge_type": "reproducible_method"}]
        report = strategy_intelligence.census(rows, assessor=lambda row: {
            "eligible": row["knowledge_type"] == "reproducible_method",
            "reasons": [] if row["knowledge_type"] == "reproducible_method" else ["unverified"],
            "review_grade": "unreviewed"})
        self.assertEqual(2, report["records"])
        self.assertEqual(1, report["eligibility"]["eligible"])
        self.assertEqual(1, report["eligibility"]["not_eligible"])

    def test_mechanism_cards_have_traceable_evidence_and_usage_boundary(self):
        cards = strategy_intelligence.strategy_cards()
        self.assertGreaterEqual(len(cards), 19)
        for card in cards:
            self.assertTrue(card["evidence_refs"])
            self.assertIn("usage_boundary", card)
            self.assertIn("corpus_examples", card)

    def test_scale_context_selects_volume_only_with_quality_inventory(self):
        result = strategy_intelligence.select_strategy(self.context())
        self.assertIn("volume_probability", [row["id"] for row in result["selected"][:3]])
        self.assertTrue(result["selected"][0]["selection_reason"])
        low = strategy_intelligence.select_strategy(self.context(quality_score=45))
        volume = next(row for row in low["candidates"] if row["id"] == "volume_probability")
        self.assertFalse(volume["eligible"])
        self.assertIn("quality_below_floor", volume["missing_inputs"])

    def test_existing_results_enable_compound_and_embedded_distribution(self):
        result = strategy_intelligence.select_strategy(self.context(
            intent="adoption", accepted_results=3, existing_users=5))
        ids = [row["id"] for row in result["selected"][:5]]
        self.assertTrue({"compound_loop", "product_embedded"} & set(ids))

    def test_accepted_results_without_second_use_builds_retention_before_more_acquisition(self):
        decision = strategy_intelligence.adjust_strategy(
            self.context(), {"id": "volume_probability"},
            {"actions": 100, "qualified_interactions": 8, "first_runs": 4, "accepted_results": 1, "second_use": 0})
        self.assertEqual("build_retention_loop", decision["action"])
        self.assertEqual("compound_loop", decision["next_mechanism"])

    def test_second_use_supports_scaling_the_same_mechanism(self):
        decision = strategy_intelligence.adjust_strategy(
            self.context(), {"id": "volume_probability"},
            {"actions": 100, "qualified_interactions": 8, "first_runs": 4, "accepted_results": 2, "second_use": 1})
        self.assertEqual("scale_up", decision["action"])
        self.assertEqual(1.5, decision["allocation_multiplier"])

    def test_no_response_switches_audience_or_hook_not_blindly_scales(self):
        decision = strategy_intelligence.adjust_strategy(
            self.context(), {"id": "volume_probability"},
            {"actions": 50, "qualified_interactions": 0, "first_runs": 0, "accepted_results": 0})
        self.assertEqual("switch_audience_or_hook", decision["action"])
        self.assertEqual(0.5, decision["allocation_multiplier"])

    def test_channel_stop_and_unknown_outcomes_have_different_actions(self):
        stopped = strategy_intelligence.adjust_strategy(
            self.context(), {"id": "volume_probability"}, {"actions": 10, "channel_stop": "github_403"})
        self.assertEqual("recover_channel", stopped["action"])
        unknown = strategy_intelligence.adjust_strategy(
            self.context(), {"id": "volume_probability"}, {"actions": 0})
        self.assertEqual("explore", unknown["action"])

    def test_strategy_select_and_adjust_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = Path(tmp) / "context.json"
            context.write_text(json.dumps(self.context()))
            got = subprocess.run([sys.executable, "mkt.py", "growth", "strategy-select",
                                  "--file", str(context)], text=True, capture_output=True)
            self.assertEqual(0, got.returncode, got.stderr)
            payload = json.loads(got.stdout)
            self.assertEqual("growovo.strategy-decision/1", payload["schema"])
            outcomes = Path(tmp) / "outcomes.json"
            outcomes.write_text(json.dumps({"actions": 50, "qualified_interactions": 0}))
            got = subprocess.run([sys.executable, "mkt.py", "growth", "strategy-adjust",
                                  "--file", str(context), "--outcomes", str(outcomes)], text=True, capture_output=True)
            self.assertEqual(0, got.returncode, got.stderr)
            self.assertEqual("switch_audience_or_hook", json.loads(got.stdout)["adjustment"]["action"])


if __name__ == "__main__":
    unittest.main()
