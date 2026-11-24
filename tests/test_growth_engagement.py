import tempfile
import unittest

from core import growth_engagement


def interaction(external_id="1", existing_user=False, body="How do I run the demo?"):
    return {
        "source": "github", "external_id": external_id,
        "author": "external-user", "body": body,
        "url": "https://example.invalid/interaction/" + external_id,
        "received_at": "2026-11-23T22:00:00+08:00",
        "existing_user": existing_user,
    }


class GrowthEngagementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name

    def test_import_is_idempotent_and_existing_user_has_priority(self):
        growth_engagement.import_interactions(self.data, "svc-test", [interaction("cold")])
        growth_engagement.import_interactions(self.data, "svc-test", [interaction("cold")])
        growth_engagement.import_interactions(self.data, "svc-test", [interaction("known", True)])
        self.assertEqual(2, len(growth_engagement.list_conversations(self.data, "svc-test")))
        next_action = growth_engagement.next_action(self.data, "svc-test")
        self.assertEqual("conv-github-known", next_action["conversation_id"])
        self.assertEqual("draft", next_action["next_step"])
        self.assertGreater(next_action["priority"], 100)

    def test_draft_then_commit_requires_real_receipt(self):
        growth_engagement.import_interactions(self.data, "svc-test", [interaction("one", True)])
        growth_engagement.draft(self.data, "svc-test", "conv-github-one", "Run python3 mkt.py demo", by="tester")
        with self.assertRaisesRegex(ValueError, "receipt"):
            growth_engagement.record_terminal(
                self.data, "svc-test", "conv-github-one", "committed", by="tester")
        got = growth_engagement.record_terminal(
            self.data, "svc-test", "conv-github-one", "committed", by="tester",
            receipt_ref="https://example.invalid/reply/1")
        self.assertEqual("committed", got["status"])
        self.assertIsNone(growth_engagement.next_action(self.data, "svc-test"))

    def test_dismissal_requires_a_named_reason(self):
        growth_engagement.import_interactions(self.data, "svc-test", [interaction("bot")])
        with self.assertRaisesRegex(ValueError, "reason"):
            growth_engagement.record_terminal(
                self.data, "svc-test", "conv-github-bot", "dismissed", by="tester")
        got = growth_engagement.record_terminal(
            self.data, "svc-test", "conv-github-bot", "dismissed", by="tester",
            reason="robot notification")
        self.assertEqual("robot notification", got["not_replied_reason"])

    def test_status_reports_stale_open_items(self):
        growth_engagement.import_interactions(self.data, "svc-test", [interaction("old")])
        got = growth_engagement.status(
            self.data, "svc-test", now="2026-11-23T23:30:00+08:00", stale_minutes=60)
        self.assertEqual(["conv-github-old"], got["stale"])
        self.assertEqual(1, got["counts"]["needs_draft"])


if __name__ == "__main__":
    unittest.main()
