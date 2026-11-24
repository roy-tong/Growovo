from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from channels import email_smtp
from core.throttle import Throttle


class Rolling24hTests(unittest.TestCase):
    def test_events_expire_individually_not_at_midnight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 11, 24, 23, 30, tzinfo=timezone.utc)
            with mock.patch("core.throttle._now", return_value=now):
                throttle = Throttle(Path(directory) / "state", day_boundary="rolling_24h")
                throttle.daily_charge("email", "message")
            with mock.patch("core.throttle._now", return_value=now + timedelta(hours=2)):
                throttle = Throttle(Path(directory) / "state", day_boundary="rolling_24h")
                self.assertEqual(1, throttle.daily_used("email", "message"))
            with mock.patch("core.throttle._now", return_value=now + timedelta(hours=24, seconds=1)):
                throttle = Throttle(Path(directory) / "state", day_boundary="rolling_24h")
                self.assertEqual(0, throttle.daily_used("email", "message"))

    def test_reserve_reopens_only_after_oldest_event_expires(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            start = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
            with mock.patch("core.throttle._now", return_value=start):
                throttle = Throttle(Path(directory) / "state", day_boundary="rolling_24h")
                for _ in range(100):
                    throttle.reserve("email", "message")
            with mock.patch("core.throttle._now", return_value=start + timedelta(hours=23, minutes=59)):
                throttle = Throttle(Path(directory) / "state", day_boundary="rolling_24h")
                with self.assertRaises(Exception):
                    throttle.reserve("email", "message")
            with mock.patch("core.throttle._now", return_value=start + timedelta(hours=24, seconds=1)):
                throttle = Throttle(Path(directory) / "state", day_boundary="rolling_24h")
                self.assertEqual(1, throttle.reserve("email", "message"))

    def test_smtp_uses_rolling_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queue = root / "queue.json"
            queue.write_text(json.dumps({"campaign": "demo", "items": [{"target": "reader@example.com", "subject": "Demo", "body": "Hello"}]}), encoding="utf-8")
            with mock.patch("channels.email_smtp.Throttle") as constructor:
                constructor.return_value = Throttle(root / "state")
                email_smtp.send_queue(queue, go=False, adhoc=True, data_dir=root)
            constructor.assert_called_once_with(root / "state", day_boundary="rolling_24h")


if __name__ == "__main__":
    unittest.main()
