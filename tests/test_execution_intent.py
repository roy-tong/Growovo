import unittest

import mcp_server
import mkt
from core.execution_intent import resolve_go
from unittest import mock


class ResolveGoTest(unittest.TestCase):
    def test_omitted_go_is_live(self):
        self.assertTrue(resolve_go())

    def test_explicit_dry_run_is_preview(self):
        self.assertFalse(resolve_go(dry_run=True))

    def test_explicit_false_remains_preview(self):
        self.assertFalse(resolve_go(False))

    def test_explicit_true_remains_live(self):
        self.assertTrue(resolve_go(True))


class CliExecutionIntentTest(unittest.TestCase):
    def assert_live_then_preview(self, argv):
        args = mkt.build_parser().parse_args(argv)
        self.assertTrue(mkt._execution_go(args))
        args = mkt.build_parser().parse_args(argv + ["--dry-run"])
        self.assertFalse(mkt._execution_go(args))

    def test_queue_run_defaults_live(self):
        self.assert_live_then_preview(["queue", "run", "queue.json"])

    def test_single_action_defaults_live(self):
        self.assert_live_then_preview([
            "action", "x", "publish", "--text", "synthetic", "--campaign", "c"])

    def test_cycle_run_defaults_live(self):
        self.assert_live_then_preview(["cycle", "run"])

    def test_cycle_jobs_tick_defaults_live(self):
        self.assert_live_then_preview(["cycle", "jobs-tick", "--file", "jobs.json"])

    def test_schedule_add_defaults_live(self):
        self.assert_live_then_preview([
            "schedule", "add", "--id", "s", "--queue", "queue.json"])

    def test_channel_write_defaults_live(self):
        self.assert_live_then_preview([
            "github", "reply", "--repo", "o/r", "--number", "1", "--text", "hello"])


class McpExecutionIntentTest(unittest.TestCase):
    def test_queue_omitted_go_is_live_and_dry_run_is_preview(self):
        with mock.patch("core.queue_runner.run_queue", return_value=[]) as run:
            mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "mkt_run_queue",
                                          "arguments": {"queue_path": "queue.json"}}})
            self.assertTrue(run.call_args.kwargs["go"])
            mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "mkt_run_queue",
                                          "arguments": {"queue_path": "queue.json",
                                                        "dry_run": True}}})
            self.assertFalse(run.call_args.kwargs["go"])


if __name__ == "__main__":
    unittest.main()
