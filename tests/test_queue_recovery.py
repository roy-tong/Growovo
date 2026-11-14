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


class DuplicateContinuationTests(unittest.TestCase):
    """每天复用的队列：碰到账本幂等命中要跳过继续，碰到真拒绝仍要停。"""

    def _run(self, side_effect, **kwargs):
        queue = {"campaign": "c", "channel": "github", "action": "reply",
                 "items": [{"id": "old", "target": "issue:o/r#1", "body": "hello"},
                           {"id": "new", "target": "issue:o/r#2", "body": "world"}]}
        with tempfile.TemporaryDirectory() as tmp, \
                patch("core.queue_runner.load_queue", return_value=queue), \
                patch("core.queue_runner.lint_queue", return_value=[]), \
                patch("core.queue_runner.precheck"), \
                patch("core.queue_runner.run_action", side_effect=side_effect) as action:
            rows = run_queue(Path(tmp) / "q.json", data_dir=tmp,
                             log=lambda _: None, **kwargs)
            return rows, action.call_count

    def test_duplicate_is_skipped_and_later_items_still_send(self):
        rows, calls = self._run(
            [{"result": "rejected", "detail": "同 campaign 已成功发过（防重发）"},
             {"result": "committed"}],
            continue_on_duplicate=True)
        self.assertEqual(2, calls)
        self.assertEqual(["rejected", "committed"], [r["result"] for r in rows])

    def test_duplicate_still_stops_without_the_flag(self):
        rows, calls = self._run(
            [{"result": "rejected", "detail": "同 campaign 已成功发过（防重发）"},
             {"result": "committed"}])
        self.assertEqual(1, calls)
        self.assertEqual(1, len(rows))

    def test_real_rejection_still_stops_even_with_the_flag(self):
        rows, calls = self._run(
            [{"result": "rejected", "detail": "内容检查：回复不得带链接"},
             {"result": "committed"}],
            continue_on_duplicate=True)
        self.assertEqual(1, calls)
        self.assertEqual(1, len(rows))


class PacingTests(unittest.TestCase):
    """真实发送之间要有间隔；dry-run 和最后一条不等待。"""

    def _queue(self):
        return {"campaign": "c", "channel": "github", "action": "reply",
                "items": [{"id": str(i), "target": f"issue:o/r#{i}", "body": "hello"}
                          for i in range(3)]}

    def _run(self, results, **kwargs):
        sleeps = []
        rows = iter(results)
        with tempfile.TemporaryDirectory() as tmp, \
                patch("core.queue_runner.load_queue", return_value=self._queue()), \
                patch("core.queue_runner.lint_queue", return_value=[]), \
                patch("core.queue_runner.precheck"), \
                patch("core.queue_runner.run_action",
                      side_effect=lambda **kw: {"result": next(rows)}):
            records = run_queue(Path(tmp) / "c.json", data_dir=tmp,
                                log=lambda _: None, sleeper=sleeps.append,
                                **kwargs)
        return records, sleeps

    def test_sleeps_between_real_sends_only(self):
        records, sleeps = self._run(["committed"] * 3, go=True,
                                    interval_seconds=(10, 10))
        self.assertEqual(3, len(records))
        self.assertEqual([10.0, 10.0], sleeps)  # 最后一条后不等待

    def test_no_pacing_without_interval_or_on_dry_run(self):
        _, sleeps = self._run(["committed"] * 3, go=True)
        self.assertEqual([], sleeps)
        _, sleeps = self._run(["dry-run"] * 3, go=False,
                              interval_seconds=(10, 10))
        self.assertEqual([], sleeps)

    def test_failure_stops_before_pacing(self):
        records, sleeps = self._run(["committed", "failed", "committed"],
                                    go=True, interval_seconds=(10, 10))
        self.assertEqual(2, len(records))
        self.assertEqual([10.0], sleeps)
