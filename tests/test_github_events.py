import hashlib
import hmac
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from core import github_events, growth_engagement


def payload():
    return {
        "action": "created",
        "installation": {"id": 456},
        "repository": {"id": 123, "full_name": "o/r"},
        "issue": {"number": 7, "id": 70},
        "comment": {
            "id": 99, "body": "Which file should I change?", "created_at": "2026-11-24T00:02:00Z",
            "html_url": "https://github.com/o/r/issues/7#issuecomment-99",
            "user": {"id": 11, "login": "bob"},
        },
    }


class GitHubEventTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name

    def test_signature_verification_and_duplicate_delivery(self):
        raw = json.dumps(payload()).encode("utf-8")
        signature = "sha256=" + hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
        github_events.verify_signature(b"secret", raw, signature)
        with self.assertRaises(ValueError):
            github_events.verify_signature(b"secret", raw, "sha256=bad")
        first = github_events.receive(
            self.data, "svc-test", "issue_comment", raw, "delivery-1", signature, b"secret")
        second = github_events.receive(
            self.data, "svc-test", "issue_comment", raw, "delivery-1", signature, b"secret")
        self.assertTrue(first["stored"])
        self.assertTrue(second["duplicate"])
        rows = growth_engagement.list_conversations(self.data, "svc-test")
        self.assertEqual(1, len(rows))
        self.assertEqual("issue-comment-99", rows[0]["external_id"])
        self.assertEqual("456", rows[0]["installation_id"])
        self.assertEqual("untrusted_external_content", rows[0]["trust"])

    def test_failed_projection_is_replayed_without_new_source_event(self):
        raw = json.dumps(payload()).encode("utf-8")
        signature = "sha256=" + hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
        with mock.patch("core.growth_engagement.import_interactions", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                github_events.receive(
                    self.data, "svc-test", "issue_comment", raw, "delivery-1", signature, b"secret")
        replayed = github_events.replay(self.data, "svc-test")
        self.assertEqual(1, replayed["processed"])
        self.assertEqual(1, len(growth_engagement.list_conversations(self.data, "svc-test")))
        self.assertEqual(0, github_events.replay(self.data, "svc-test")["processed"])

    def test_followup_comment_reuses_the_issue_conversation(self):
        issue = {"action": "opened", "installation": {"id": 456},
                 "repository": {"id": 123, "full_name": "o/r"},
                 "issue": {"number": 7, "id": 70, "title": "Config failed", "body": "Cannot read config",
                           "created_at": "2026-11-24T00:00:00Z",
                           "html_url": "https://github.com/o/r/issues/7",
                           "user": {"id": 10, "login": "alice"}}}
        github_events.ingest(self.data, "svc-test", "issues", issue, "delivery-issue")
        github_events.ingest(self.data, "svc-test", "issue_comment", payload(), "delivery-comment")
        rows = growth_engagement.list_conversations(self.data, "svc-test")
        self.assertEqual(1, len(rows))
        self.assertEqual({"issue-7", "issue-comment-99"}, set(rows[0]["interaction_ids"]))
        self.assertEqual(payload()["comment"]["body"], rows[0]["body"])

    def test_concurrent_comments_cannot_overwrite_conversation_projection(self):
        first = payload()
        second = json.loads(json.dumps(first))
        second["comment"]["id"] = 100
        second["comment"]["body"] = "A follow-up question"
        original_load = growth_engagement._load
        release = threading.Event()
        lock = threading.Lock()
        calls = [0]

        def slowed_load(*args):
            value = original_load(*args)
            with lock:
                calls[0] += 1
                current = calls[0]
            if current <= 2:
                release.wait(timeout=1)
            return value

        with mock.patch("core.growth_engagement._load", side_effect=slowed_load):
            with ThreadPoolExecutor(max_workers=2) as pool:
                tasks = [pool.submit(github_events.ingest, self.data, "svc-test", "issue_comment",
                                     row, delivery) for row, delivery in ((first, "delivery-a"),
                                                                         (second, "delivery-b"))]
                time.sleep(0.2)
                release.set()
                for task in tasks:
                    task.result()
        rows = growth_engagement.list_conversations(self.data, "svc-test")
        self.assertEqual(1, len(rows))
        self.assertEqual({"issue-comment-99", "issue-comment-100"}, set(rows[0]["interaction_ids"]))

    def test_unsupported_event_is_ignored_before_projection(self):
        got = github_events.ingest(self.data, "svc-test", "push", {"ref": "refs/heads/main"}, "delivery-2")
        self.assertFalse(got["stored"])
        self.assertTrue(got["ignored"])
        self.assertEqual([], growth_engagement.list_conversations(self.data, "svc-test"))


if __name__ == "__main__":
    unittest.main()
