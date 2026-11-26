import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import notification_outbox


class NotificationOutboxTests(unittest.TestCase):
    def test_event_is_deduped_and_delivery_is_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            change = {"event_id": "job-1@one", "id": "job-1", "status": "failed",
                      "reason": "outside_window", "next_action": "另开获批窗口"}
            notification_outbox.record(data, change)
            notification_outbox.record(data, change)
            self.assertEqual(1, len(notification_outbox.pending(data)))
            got = notification_outbox.deliver_pending(
                data, lambda event: {"receipt": "offline-delivery-1"})
            self.assertEqual(1, got["delivered"])
            self.assertEqual([], notification_outbox.pending(data))
            self.assertEqual(0, notification_outbox.deliver_pending(
                data, lambda event: self.fail("delivered twice"))["delivered"])

    def test_cli_lists_pending_events(self):
        import mkt
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            notification_outbox.record(data, {
                "event_id": "job-3@one", "id": "job-3", "status": "failed",
                "reason": "job_expired", "next_action": "另开获批窗口"})
            output = io.StringIO()
            with mock.patch.object(mkt, "DATA", data), contextlib.redirect_stdout(output):
                code = mkt.cmd_cycle(SimpleNamespace(action="notifications"))
            self.assertEqual(0, code)
            got = json.loads(output.getvalue())
            self.assertEqual(1, got["pending"])
            self.assertEqual("job-3", got["items"][0]["id"])

    def test_empty_delivery_receipt_remains_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            notification_outbox.record(data, {
                "event_id": "job-empty@one", "id": "job-empty", "status": "failed",
                "reason": "offline", "next_action": "重试通知"})
            got = notification_outbox.deliver_pending(data, lambda event: None)
            self.assertEqual(1, got["failed"])
            self.assertEqual(0, got["delivered"])
            self.assertEqual(1, len(notification_outbox.pending(data)))

    def test_failed_delivery_remains_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            notification_outbox.record(data, {
                "event_id": "job-2@one", "id": "job-2", "status": "needs_reconciliation",
                "reason": "indeterminate", "next_action": "只读核验"})

            def fail(event):
                raise RuntimeError("offline adapter unavailable")

            got = notification_outbox.deliver_pending(data, fail)
            self.assertEqual(1, got["failed"])
            self.assertEqual(1, len(notification_outbox.pending(data)))


if __name__ == "__main__":
    unittest.main()
