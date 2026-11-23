#!/usr/bin/env python3
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from core import engagement_cycle as cycle
from core.runtime import RunContext
from core.storage import AtomicJSONStore, AppendOnlyJSONL, FileLock, StorageError


class RuntimeStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_run_context_correlates_attempts_and_children(self):
        now = dt.datetime(2026, 11, 23, 19, 5, tzinfo=dt.timezone.utc)
        first = RunContext.create(self.root, "campaign", "wave", now=now)
        second = RunContext.create(self.root, "campaign", "wave", now=now)
        self.assertEqual(first.run_id, second.run_id)
        self.assertNotEqual(first.attempt_id, second.attempt_id)
        self.assertEqual(now.isoformat(timespec="seconds"), first.created_at)
        child = first.child(action_id="action-1")
        self.assertEqual(first.attempt_id, child.parent_attempt_id)
        self.assertEqual("action-1", child.action_id)
        self.assertEqual(first.run_id, child.run_id)

    def test_atomic_json_round_trip_and_malformed_fail_loud(self):
        store = AtomicJSONStore(self.root / "state.json", schema="test/1",
                                default={"items": []})
        self.assertEqual({"items": []}, store.load())
        store.save({"items": [1]})
        self.assertEqual([1], store.load()["items"])
        self.assertFalse(list(self.root.glob("*.tmp")))
        store.path.write_text("{", encoding="utf-8")
        with self.assertRaises(StorageError):
            store.load()

    def test_append_only_jsonl_rejects_bad_lines_and_preserves_history(self):
        store = AppendOnlyJSONL(self.root / "events.jsonl")
        store.append({"id": 1})
        store.append({"id": 2})
        self.assertEqual([1, 2], [r["id"] for r in store.all()])
        with store.path.open("a") as handle:
            handle.write("{\n")
        with self.assertRaises(StorageError):
            store.all()

    def test_file_lock_excludes_second_holder(self):
        first = FileLock(self.root / "lock")
        second = FileLock(self.root / "lock")
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_corrupt_engagement_queue_cannot_be_rewritten_as_empty(self):
        path = self.root / "queue.json"
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(StorageError):
            cycle.load_queue(path)
        with self.assertRaises(StorageError):
            cycle._merge_inbox_rows(path, [])
        self.assertEqual("{", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
