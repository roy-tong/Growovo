import tempfile
import unittest

from core import growth_runtime, growth_service
from tests.test_growth_goals import goal


class GrowthRuntimeRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.service = growth_service.start(self.data, goal())
        self.sid = self.service["id"]

    def action(self, action_id):
        loaded = growth_service.load(self.data, self.sid)
        return next(row for row in loaded["actions"] if row["id"] == action_id)

    def test_external_attempt_expires_into_verification_instead_of_retry(self):
        claimed = growth_runtime.claim_action(
            self.data, self.sid, "github-engage", worker="worker-a",
            payload={"target": "issue:owner/repo#1", "text": "answer"}, lease_seconds=0)
        recovered = growth_runtime.recover_expired(self.data)
        self.assertEqual(1, recovered["recovered"])
        self.assertEqual("waiting_verification", self.action("github-engage")["status"])
        attempts = growth_runtime.attempts(self.data, self.sid)
        self.assertEqual(claimed["attempt_id"], attempts[0]["id"])
        self.assertEqual("expired_unknown", attempts[0]["status"])
        next_claim = growth_runtime.claim_action(
            self.data, self.sid, "github-engage", worker="worker-b",
            payload={"target": "issue:owner/repo#1", "text": "answer"})
        self.assertEqual("waiting_verification", next_claim["status"])
        self.assertNotIn("attempt_id", next_claim)

    def test_local_attempt_expires_into_pending_with_same_history(self):
        claimed = growth_runtime.claim_action(
            self.data, self.sid, "diagnose-product", worker="worker-a",
            payload={}, lease_seconds=0)
        recovered = growth_runtime.recover_expired(self.data)
        self.assertEqual(1, recovered["recovered"])
        self.assertEqual("pending", self.action("diagnose-product")["status"])
        attempts = growth_runtime.attempts(self.data, self.sid)
        self.assertEqual(claimed["attempt_id"], attempts[0]["id"])
        self.assertEqual("expired_retryable", attempts[0]["status"])

    def test_attempt_and_service_event_survive_reopen(self):
        claimed = growth_runtime.claim_action(
            self.data, self.sid, "diagnose-product", worker="worker-a", payload={})
        growth_service.record_action(
            self.data, self.sid, "diagnose-product", "completed", by="tester")
        attempts = growth_runtime.attempts(self.data, self.sid)
        self.assertEqual(claimed["attempt_id"], attempts[0]["id"])
        self.assertEqual("completed", attempts[0]["status"])
        kinds = [row["kind"] for row in growth_runtime.events(self.data, self.sid)]
        self.assertIn("action_claimed", kinds)
        self.assertIn("action_recorded", kinds)


if __name__ == "__main__":
    unittest.main()
