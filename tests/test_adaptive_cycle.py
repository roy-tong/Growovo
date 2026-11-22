#!/usr/bin/env python3
"""自适应波次调度：实测耗时、动态补位与后台供给预取；全程离线。"""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import browser_attach
from core import engagement_cycle as cycle

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 11, 21, 22, 5, tzinfo=TZ)


def row(channel, action, target="", text="t", actor="", source="queue"):
    out = {"channel": channel, "action": action, "target": target, "text": text,
           "actor": actor, "source": source}
    if action == "reply":
        out["their_text"] = "peer context"
    return out


class DurationStatsTest(unittest.TestCase):
    def test_real_operations_produce_per_action_ema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operations.jsonl"
            rows = [
                {"channel": "x", "action": "reply", "result": "committed",
                 "started_at": "2026-11-21T14:00:00+00:00",
                 "finished_at": "2026-11-21T14:00:20+00:00"},
                {"channel": "x", "action": "reply", "result": "indeterminate",
                 "started_at": "2026-11-21T14:01:00+00:00",
                 "finished_at": "2026-11-21T14:01:40+00:00"},
                {"channel": "x", "action": "reply", "result": "dry-run",
                 "started_at": "2026-11-21T14:02:00+00:00",
                 "finished_at": "2026-11-21T14:02:59+00:00"},
            ]
            path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            got = cycle.duration_stats(tmp)
            self.assertEqual(got["x/reply"]["samples"], 2)
            self.assertEqual(got["x/reply"]["seconds"], 25.0)
            self.assertEqual(cycle.expected_duration(got, "x", "reply"), 25.0)

    def test_invalid_or_missing_durations_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "operations.jsonl"
            path.write_text(json.dumps({
                "channel": "github", "action": "reply", "result": "committed",
                "started_at": "bad", "finished_at": "also-bad"}) + "\n", encoding="utf-8")
            self.assertEqual(cycle.duration_stats(tmp), {})
            self.assertEqual(cycle.expected_duration({}, "github", "reply"), 0.0)


class ChooseNextTest(unittest.TestCase):
    def test_other_channel_fills_x_cooldown(self):
        rows = [row("x", "reply", "https://x.com/a/status/1"),
                row("github", "reply", "https://github.com/a/b/issues/1")]
        index, ready_at, decision = cycle.choose_next(
            rows, cycle.DEFAULT_POLICY, now=NOW,
            last_channel={"x": NOW.timestamp()}, last_any=NOW.timestamp(), durations={})
        self.assertEqual(rows[index]["channel"], "github")
        self.assertEqual(ready_at, NOW.timestamp() + 15)
        self.assertEqual(decision["reason"], "earliest_ready")

    def test_ready_tie_prefers_shorter_observed_action(self):
        rows = [row("x", "publish"), row("discord", "publish", "123")]
        durations = {"x/publish": {"seconds": 60, "samples": 2},
                     "discord/publish": {"seconds": 10, "samples": 2}}
        index, _, decision = cycle.choose_next(
            rows, cycle.DEFAULT_POLICY, now=NOW, last_channel={}, last_any=0,
            durations=durations)
        self.assertEqual(rows[index]["channel"], "discord")
        self.assertEqual(decision["reason"], "shortest_observed_duration")

    def test_inbox_still_precedes_shorter_regular_work(self):
        rows = [row("discord", "publish", "123"),
                row("x", "reply", "https://x.com/a/status/1", source="inbox")]
        durations = {"x/reply": {"seconds": 60, "samples": 2},
                     "discord/publish": {"seconds": 10, "samples": 2}}
        index, _, decision = cycle.choose_next(
            rows, cycle.DEFAULT_POLICY, now=NOW, last_channel={}, last_any=0,
            durations=durations)
        self.assertEqual(rows[index]["source"], "inbox")
        self.assertEqual(decision["reason"], "inbox_first")


class RuntimeCompactionTest(unittest.TestCase):
    def test_missing_channel_does_not_leave_later_work_behind_fixed_x_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            queue = data / "queue.json"
            rows = [
                row("x", "publish", text="x1"),
                row("github", "reply", "https://github.com/a/b/issues/1", actor="a"),
                row("discord", "publish", "123", text="d1"),
                row("x", "publish", text="x2"),
                row("github", "reply", "https://github.com/c/d/issues/2", actor="b"),
            ]
            queue.write_text(json.dumps(rows), encoding="utf-8")

            def fake_execute(channel, action, data_dir, **kwargs):
                if channel == "discord":
                    raise browser_attach.BridgeError("tab_not_found", "missing")
                return {"ok": True, "result": "success", "ledger_result": "committed"}

            with mock.patch.object(cycle, "_now", return_value=NOW), \
                    mock.patch.object(cycle.time, "sleep"), \
                    mock.patch("core.action_runner.run_action", side_effect=fake_execute):
                got = cycle.run(data, go=True, queue_path=queue, wave_id="2205",
                                log=lambda *_: None)

            attempted = [(r["channel"], r["target"]) for r in got["results"]]
            self.assertEqual(attempted, [
                ("x", ""), ("github", "https://github.com/a/b/issues/1"),
                ("github", "https://github.com/c/d/issues/2"), ("x", ""),
            ])
            self.assertEqual(got["deferred_channels"], ["discord"])


class CyclePrefetchIntegrationTest(unittest.TestCase):
    def test_real_run_starts_and_finishes_prefetch_but_dry_run_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "policies").mkdir()
            (data / "policies" / "engagement-cycle.json").write_text(json.dumps({
                "supply": {"prefetch": {"enabled": True}}}), encoding="utf-8")
            queue = data / "queue.json"
            queue.write_text(json.dumps([row("x", "publish")]), encoding="utf-8")
            worker = mock.Mock()
            worker.start.return_value = {"ok": True, "status": "running"}
            worker.finish.return_value = {"ok": True, "status": "done"}
            fake = {"ok": True, "result": "success", "ledger_result": "committed"}
            with mock.patch("core.supply_prefetch.Prefetcher", return_value=worker) as factory, \
                    mock.patch.object(cycle, "_now", return_value=NOW), \
                    mock.patch("core.action_runner.run_action", return_value=fake):
                dry = cycle.run(data, go=False, queue_path=queue, log=lambda *_: None)
                factory.assert_not_called()
                real = cycle.run(data, go=True, queue_path=queue, wave_id="2205",
                                 log=lambda *_: None)
            worker.start.assert_called_once_with()
            worker.finish.assert_called_once_with()
            self.assertEqual(real["prefetch"]["status"], "done")
            self.assertNotIn("prefetch", dry)


if __name__ == "__main__":
    unittest.main()
