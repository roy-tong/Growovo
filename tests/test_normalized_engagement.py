import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import growth_engagement, growth_runtime
from tests.test_growth_domain_state import interaction


class NormalizedEngagementTest(unittest.TestCase):
    def test_import_persists_messages_without_synchronous_json_projection(self):
        with tempfile.TemporaryDirectory() as data:
            with mock.patch("core.growth_runtime.AtomicJSONStore.save") as save:
                growth_engagement.import_interactions(
                    data, "svc-test", [interaction(1), interaction(2)])
                self.assertEqual(0, save.call_count)
                rows = growth_engagement.list_conversations(data, "svc-test")
                self.assertEqual(1, save.call_count)
            self.assertEqual(2, growth_runtime.conversation_message_count(data, "svc-test"))
            self.assertEqual({"issue-comment-1", "issue-comment-2"},
                             set(rows[0]["interaction_ids"]))

    def test_legacy_json_is_migrated_and_can_be_rebuilt(self):
        with tempfile.TemporaryDirectory() as data:
            growth_engagement.import_interactions(data, "svc-test", [interaction(1)])
            growth_engagement.list_conversations(data, "svc-test")
            path = Path(data) / "state" / "growth-engagement" / "svc-test.json"
            path.unlink()
            rows = growth_engagement.list_conversations(data, "svc-test")
            self.assertEqual(1, len(rows))
            self.assertTrue(path.is_file())
            self.assertEqual("issue-comment-1", rows[0]["interaction_ids"][0])


if __name__ == "__main__":
    unittest.main()
