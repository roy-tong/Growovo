#!/usr/bin/env python3
import json
import tempfile
import time
import unittest
from pathlib import Path

from core import engagement_cycle as cycle
from core.ledger import Ledger, TARGET_BLOCKED
from core.ledger_index import LedgerIndex
from core.queue_store import QueueStore
from core.storage import StorageError


def rec(target, result="committed", channel="x", ref=""):
    return {"channel": channel, "action": "reply", "target": target,
            "result": result, "campaign": "c", "ref": ref}


def item(i):
    return {"id": "q:%d" % i, "channel": "x", "action": "publish",
            "target": "", "text": "post %d" % i}


class LedgerQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_ledger_index_matches_legacy_membership(self):
        rows = [rec("a"), rec("b", "target_rejected"), rec("c", "indeterminate"),
                rec("d", "failed"), rec("e", "committed", channel="github")]
        index = LedgerIndex.build(rows)
        for row in rows:
            channel, target = row["channel"], row["target"]
            expected = any(r.get("result") == "committed" and r.get("channel") == channel
                           and r.get("target") == target for r in rows)
            self.assertEqual(expected, index.committed(channel, target))
            blocked = any(r.get("result") in TARGET_BLOCKED and r.get("channel") == channel
                          and r.get("target") == target for r in rows)
            self.assertEqual(blocked, index.blocked(channel, target))
        self.assertEqual({"c"}, index.no_retry("x") & {"c"})

    def test_large_index_lookup_is_constant_after_one_scan(self):
        rows = [rec("target-%d" % i, ref="r%d" % i) for i in range(5000)]
        index = LedgerIndex.build(rows)
        start = time.perf_counter()
        for i in range(500):
            self.assertTrue(index.committed("x", "target-%d" % i))
        self.assertLess(time.perf_counter() - start, 0.1)

    def test_queue_consume_recovers_without_replaying_external_actions(self):
        path = self.root / "queue.json"
        store = QueueStore(path)
        store.save([item(1), item(2), item(3)])
        left = store.consume(store.load(), item(1), action_id="action-1")
        self.assertEqual(["q:2", "q:3"], [r["id"] for r in left])
        recovered = QueueStore(path).load()
        self.assertEqual(["q:2", "q:3"], [r["id"] for r in recovered])
        store.save(left)
        self.assertEqual("", store.journal_path.read_text(encoding="utf-8"))
        self.assertEqual(["q:2", "q:3"], [r["id"] for r in store.load()])

    def test_cycle_queue_facade_replays_journal_and_bad_journal_fails_loud(self):
        path = self.root / "queue.json"
        store = QueueStore(path)
        store.save([item(1), item(2)])
        store.consume(store.load(), item(1))
        self.assertEqual(["q:2"], [r["id"] for r in cycle.load_queue(path)])
        store.journal_path.write_text("{\n", encoding="utf-8")
        with self.assertRaises(StorageError):
            cycle.load_queue(path)

    def test_inbox_terminal_set_loads_store_once(self):
        from core import inbox_tracking
        self.assertIsInstance(inbox_tracking.terminal_identities(self.root), set)


if __name__ == "__main__":
    unittest.main()
