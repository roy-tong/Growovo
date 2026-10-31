"""No business outcome inferred from channel receipts; no automatic replay."""
import unittest

from core.operations_memory import summarize


class OperationsMemoryTests(unittest.TestCase):
    def test_reuse_candidate_never_grants_replay(self):
        recs = [{"channel": "github", "action": "reply", "result": "committed",
                 "campaign": "p", "target": "issue:private/repo#1",
                 "detail": "confidential customer message"} for _ in range(3)]
        summary = summarize(recs, campaign="p")
        item = summary["patterns"][0]
        self.assertTrue(item["candidate_for_review"])
        self.assertFalse(item["automatic_replay_allowed"])
        self.assertEqual(item["confirmed_operations"], 3)
        self.assertNotIn("confidential", str(summary))
        self.assertNotIn("issue:private", str(summary))

    def test_unknown_receipt_blocks_review_recommendation(self):
        recs = [
            {"channel": "email", "action": "message", "result": "committed"} for _ in range(5)
        ]
        recs.append({"channel": "email", "action": "message", "result": "indeterminate"})
        item = summarize(recs)["patterns"][0]
        self.assertFalse(item["candidate_for_review"])
        self.assertEqual(item["risk_receipts"], 1)
        self.assertTrue(item["review_blockers"])

    def test_filters_and_empty(self):
        recs = [{"channel": "github", "action": "reply", "campaign": "one",
                 "result": "committed"},
                {"channel": "email", "action": "message", "campaign": "two",
                 "result": "failed"}]
        result = summarize(recs, campaign="one")
        self.assertEqual(len(result["patterns"]), 1)
        self.assertEqual(result["patterns"][0]["channel"], "github")
        self.assertEqual(summarize(recs, campaign="none")["patterns"], [])


if __name__ == "__main__":
    unittest.main()
