import tempfile
import unittest

from core import growth_opportunities


def candidate(external_id="one", **changes):
    row = {
        "source": "github", "external_id": external_id,
        "author": "developer", "problem": "Need to verify agent outputs",
        "trigger": "Agent said complete but user cannot replay it",
        "intent": "solution_seeking",
        "fit": "high",
        "evidence": [{"url": "https://example.invalid/post/1", "quote": "need a replay"}],
        "asset_reproduced": True,
        "existing_user": False,
        "human_minutes": 5,
        "risk": "low",
    }
    row.update(changes)
    return row


class GrowthOpportunityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name

    def test_score_is_explainable_and_not_a_probability(self):
        got = growth_opportunities.evaluate(candidate())
        self.assertGreater(got["score"], 60)
        self.assertNotIn("probability", got)
        self.assertTrue(got["reasons"])
        self.assertEqual("engage", got["decision"])

    def test_missing_reproduced_asset_changes_next_action(self):
        got = growth_opportunities.evaluate(candidate(asset_reproduced=False))
        self.assertEqual("prepare_asset", got["decision"])

    def test_import_dedupes_and_next_returns_highest_value(self):
        growth_opportunities.import_opportunities(self.data, "svc-test", [candidate("low", fit="low")])
        growth_opportunities.import_opportunities(self.data, "svc-test", [candidate("high", existing_user=True)])
        growth_opportunities.import_opportunities(self.data, "svc-test", [candidate("high", existing_user=True)])
        self.assertEqual(2, len(growth_opportunities.list_opportunities(self.data, "svc-test")))
        got = growth_opportunities.next_action(self.data, "svc-test")
        self.assertEqual("opp-github-high", got["opportunity_id"])
        self.assertEqual("engage", got["decision"])

    def test_high_risk_candidate_is_not_executed(self):
        got = growth_opportunities.evaluate(candidate(risk="high"))
        self.assertEqual("ignore", got["decision"])


if __name__ == "__main__":
    unittest.main()
