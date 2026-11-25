import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import growth_engagement, growth_feedback

ROOT = Path(__file__).resolve().parents[1]


class GrowthFeedbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        growth_engagement.import_interactions(self.data, "svc-test", [{
            "source": "x", "external_id": "comment-1", "author": "alice",
            "body": "The demo worked, but I could not replay the result.",
            "url": "https://example.test/c1", "received_at": "2026-11-24T09:00:00Z",
            "conversation_key": "thread-1",
        }])
        self.review = {
            "conversation_id": "conv-x-thread-1", "category": "usability",
            "summary": "The user needs a replayable result.",
            "evidence_quote": "could not replay the result",
        }

    def test_reviewed_feedback_binds_exact_source_quote(self):
        document = growth_feedback.build(self.data, "svc-test", [self.review], reviewed_by="product-owner")
        self.assertEqual("growovo.product-feedback/1", document["schema"])
        row = document["feedback"][0]
        self.assertEqual("reviewed", row["review"]["status"])
        self.assertEqual("product-owner", row["review"]["reviewed_by"])
        self.assertEqual("https://example.test/c1", row["source"]["url"])
        self.assertEqual("x:alice", row["subject_ref"])
        self.assertFalse(row["canonical_evidence"])
        self.assertEqual("not_decided", row["product_decision_status"])

    def test_quote_must_be_exactly_present_in_source_conversation(self):
        with self.assertRaisesRegex(ValueError, "evidence_quote"):
            growth_feedback.build(self.data, "svc-test", [
                dict(self.review, evidence_quote="not in the source")], reviewed_by="owner")

    def test_unknown_conversation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "conversation"):
            growth_feedback.build(self.data, "svc-test", [
                dict(self.review, conversation_id="conv-x-missing")], reviewed_by="owner")

    def test_conflicting_duplicate_feedback_id_is_rejected(self):
        other = dict(self.review, summary="A different conclusion")
        with self.assertRaisesRegex(ValueError, "conflict"):
            growth_feedback.build(self.data, "svc-test", [self.review, other], reviewed_by="owner")

    def test_cli_writes_new_feedback_without_mutating_conversations(self):
        before = json.dumps(growth_engagement.list_conversations(self.data, "svc-test"),
                            sort_keys=True, ensure_ascii=False)
        review = Path(self.data) / "review.json"
        review.write_text(json.dumps([self.review]), encoding="utf-8")
        out = Path(self.data) / "feedback.json"
        result = subprocess.run([
            sys.executable, str(ROOT / "mkt.py"), "--workspace", self.data,
            "growth", "feedback-export", "svc-test", "--file", str(review),
            "--by", "product-owner", "--out", str(out),
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual(1, json.loads(result.stdout)["feedback"])
        after = json.dumps(growth_engagement.list_conversations(self.data, "svc-test"),
                           sort_keys=True, ensure_ascii=False)
        self.assertEqual(before, after)
        second = subprocess.run([
            sys.executable, str(ROOT / "mkt.py"), "--workspace", self.data,
            "growth", "feedback-export", "svc-test", "--file", str(review),
            "--by", "product-owner", "--out", str(out),
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(0, second.returncode)
        self.assertEqual("growovo.product-feedback/1", json.loads(out.read_text())["schema"])


if __name__ == "__main__":
    unittest.main()
