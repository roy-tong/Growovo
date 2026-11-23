#!/usr/bin/env python3
"""Persistent inbox ownership: draft, dismiss, plan, send and reconcile."""
import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import engagement_cycle as cycle
from core import inbox_tracking
import mkt

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 11, 23, 9, 0, tzinfo=TZ)


def inbound(target="https://x.com/a/status/1", *, text="", actor="a"):
    return {"id": cycle.stable_id({"channel": "x", "action": "reply", "target": target}),
            "channel": "x", "action": "reply", "target": target,
            "thread_ref": target, "actor": actor, "source": "inbox",
            "their_text": "peer context", "received_at": "2026-11-23T08:00:00+08:00",
            "text": text}


class InboxTrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.queue = self.data / "state" / "engagement-queue.json"

    def merge(self, rows):
        return cycle.merge_inbox(self.data, rows, queue_path=self.queue)

    def test_fill_only_updates_existing_blank_row_and_tracks_state(self):
        original = inbound()
        self.merge([original])
        got = inbox_tracking.status_report(self.data, self.queue, now=NOW)
        self.assertEqual(1, got["counts"]["needs_draft"])

        result = cycle.update_inbox(self.data, {
            "items": [{"target": original["target"], "text": "specific answer"}],
        }, queue_path=self.queue, by="roy", now=NOW)

        self.assertTrue(result["ok"])
        self.assertEqual(1, result["written"])
        queued = cycle.load_queue(self.queue)
        self.assertEqual("specific answer", queued[0]["text"])
        self.assertEqual(original["id"], queued[0]["id"])
        self.assertEqual("peer context", queued[0]["their_text"])
        got = inbox_tracking.status_report(self.data, self.queue, now=NOW)
        self.assertEqual(1, got["counts"]["drafted"])
        self.assertEqual("roy", got["items"][0]["updated_by"])

    def test_update_cannot_add_new_target_or_overwrite_a_draft(self):
        row = inbound()
        self.merge([row])
        first = cycle.update_inbox(self.data, [
            {"target": "https://x.com/new/status/2", "text": "must not add"},
            {"target": row["target"], "text": "first"},
        ], queue_path=self.queue, by="roy", now=NOW)
        self.assertEqual(1, first["written"])
        self.assertEqual(1, len(first["rejected"]))
        self.assertEqual("not_blank_inbox", first["rejected"][0]["reason"])
        second = cycle.update_inbox(self.data, [
            {"target": row["target"], "text": "replacement"},
        ], queue_path=self.queue, by="roy", now=NOW)
        self.assertEqual(0, second["written"])
        self.assertEqual("not_blank_inbox", second["rejected"][0]["reason"])
        self.assertEqual(["first"], [r["text"] for r in cycle.load_queue(self.queue)])

    def test_dismiss_has_auditable_reason_and_never_sends(self):
        row = inbound()
        self.merge([row])
        missing = cycle.update_inbox(self.data, [
            {"target": row["target"], "not_replied_reason": ""},
        ], queue_path=self.queue, by="roy", now=NOW)
        self.assertEqual("dismiss_reason_required", missing["rejected"][0]["reason"])
        got = cycle.update_inbox(self.data, [
            {"target": row["target"], "not_replied_reason": "bot test scan"},
        ], queue_path=self.queue, by="roy", now=NOW)
        self.assertEqual(1, got["dismissed"])
        self.assertEqual([], cycle.load_queue(self.queue))
        status = inbox_tracking.status_report(self.data, self.queue, now=NOW)
        self.assertEqual(1, status["counts"]["dismissed"])
        self.assertEqual("bot test scan", status["items"][0]["reason"])
        self.assertEqual("roy", status["items"][0]["updated_by"])

    def test_planned_state_is_visible_until_terminal_receipt(self):
        row = inbound()
        self.merge([row])
        cycle.update_inbox(self.data, [{"target": row["target"], "text": "answer"}],
                           queue_path=self.queue, by="roy", now=NOW)
        inbox_tracking.mark_planned(self.data, cycle.load_queue(self.queue),
                                    wave="01-1905", now=NOW)
        got = inbox_tracking.status_report(self.data, self.queue, now=NOW)
        self.assertEqual(1, got["counts"]["planned"])
        self.assertEqual("01-1905", got["items"][0]["wave"])

    def test_committed_terminal_does_not_requeue_on_later_collection(self):
        row = inbound()
        self.merge([row])
        cycle.update_inbox(self.data, [{"target": row["target"], "text": "answer"}],
                           queue_path=self.queue, by="roy", now=NOW)
        inbox_tracking.mark_planned(self.data, [row], wave="01-1905", now=NOW)
        inbox_tracking.mark_result(self.data, row, "committed", ref="status-1",
                                   wave="01-1905", now=NOW)
        cycle.save_queue(self.queue, [])
        got = self.merge([inbound()])
        self.assertEqual(1, got["terminal_skipped"])
        self.assertEqual([], cycle.load_queue(self.queue))
        status = inbox_tracking.status_report(self.data, self.queue, now=NOW)
        self.assertEqual(1, status["counts"]["committed"])
        self.assertEqual("status-1", status["items"][0]["receipt_ref"])

    def test_nonterminal_missing_from_queue_needs_reconciliation(self):
        row = inbound()
        self.merge([row])
        cycle.save_queue(self.queue, [])
        got = inbox_tracking.status_report(self.data, self.queue, now=NOW)
        self.assertEqual(1, got["counts"]["needs_reconciliation"])
        self.assertEqual("queue_missing", got["items"][0]["reconciliation_reason"])

    def test_old_pending_items_are_overdue_not_zero(self):
        self.merge([inbound()])
        got = inbox_tracking.status_report(
            self.data, self.queue, now=NOW, stale_minutes=30)
        self.assertEqual(1, got["counts"]["needs_draft"])
        self.assertEqual(1, got["overdue_count"])
        self.assertEqual(60, got["items"][0]["age_minutes"])

    def test_update_uses_the_same_wave_lock(self):
        self.merge([inbound()])
        lock = cycle._lock_path(self.data)
        handle = lock.open("a+")
        cycle.fcntl.flock(handle.fileno(), cycle.fcntl.LOCK_EX | cycle.fcntl.LOCK_NB)
        try:
            got = cycle.update_inbox(self.data, [
                {"target": inbound()["target"], "text": "answer"},
            ], queue_path=self.queue, by="roy", now=NOW)
        finally:
            cycle.fcntl.flock(handle.fileno(), cycle.fcntl.LOCK_UN)
            handle.close()
        self.assertFalse(got["ok"])
        self.assertEqual("wave_already_running", got["reason"])
        self.assertEqual("", cycle.load_queue(self.queue)[0]["text"])

    def test_cli_update_and_status_use_product_paths(self):
        self.merge([inbound()])
        draft_file = self.data / "drafts.json"
        draft_file.write_text(json.dumps([
            {"target": inbound()["target"], "text": "answer"},
        ]), encoding="utf-8")
        output = io.StringIO()
        with mock.patch.object(mkt, "DATA", self.data), contextlib.redirect_stdout(output):
            update_code = mkt.cmd_cycle(SimpleNamespace(
                action="inbox-update", file=str(draft_file), queue="", by="roy"))
        self.assertEqual(0, update_code)
        output = io.StringIO()
        with mock.patch.object(mkt, "DATA", self.data), contextlib.redirect_stdout(output):
            status_code = mkt.cmd_cycle(SimpleNamespace(
                action="inbox-status", queue="", stale_minutes=60))
        self.assertEqual(0, status_code)
        self.assertEqual(1, json.loads(output.getvalue())["counts"]["drafted"])


if __name__ == "__main__":
    unittest.main()
