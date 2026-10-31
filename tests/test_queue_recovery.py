import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.queue_runner import run_queue


class QueueRecoveryTests(unittest.TestCase):
    def test_stops_after_uncertain_failure_or_rejection(self):
        queue = {"campaign": "c", "channel": "github", "action": "reply",
                 "items": [{"id": "a", "target": "issue:o/r#1", "body": "hello"},
                           {"id": "b", "target": "issue:o/r#2", "body": "world"}]}
        for result in ("indeterminate", "failed", "rejected"):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as tmp:
                with patch("core.queue_runner.load_queue", return_value=queue), \
                     patch("core.queue_runner.lint_queue", return_value=[]), \
                     patch("core.queue_runner.precheck"), \
                     patch("core.queue_runner.run_action", return_value={"result": result}) as action:
                    rows = run_queue(Path(tmp) / "q.json", data_dir=tmp, log=lambda _: None)
                    self.assertEqual(1, action.call_count)
                    self.assertEqual(1, len(rows))
