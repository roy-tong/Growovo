#!/usr/bin/env python3
"""波次执行期间的候选预取：只读发现、独立锁、可停止；不触网。"""
import json
import tempfile
import threading
import unittest
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import supply_mine
from core import supply_pool
from core.supply_prefetch import Prefetcher, plan_prefetch


def policy(**prefetch):
    config = {"enabled": True, "lookahead_waves": 2, "sources": ["github", "x", "content"],
              "max_queries": 2, "per_query": 5, "gap_sec": 0}
    config.update(prefetch)
    return {"supply": {"wave_targets": {
        "x/publish": 10, "x/reply": 20, "github/reply": 15, "discord/publish": 6},
        "prefetch": config}}


class PlanTest(unittest.TestCase):
    def test_low_github_supply_gets_background_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = plan_prefetch(tmp, policy())
            self.assertEqual([j["source"] for j in got["jobs"]], ["github", "content"])
            self.assertEqual(got["deferred"], [
                {"source": "x", "reason": "shared_browser_tab"}])
            self.assertEqual(got["jobs"][0]["available"], 0)
            self.assertEqual(got["jobs"][0]["target"], 30)

    def test_enough_supply_does_not_start_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            supply_pool.merge(tmp, [{
                "channel": "github", "action": "reply",
                "target": "https://github.com/a/b/issues/%d" % i,
                "context": "cost question"} for i in range(30)])
            local = policy()
            local["supply"]["wave_targets"] = {"github/reply": 15}
            got = plan_prefetch(tmp, local)
            self.assertEqual(got["jobs"], [])

    def test_disabled_or_unlisted_source_is_not_planned(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(plan_prefetch(tmp, policy(enabled=False))["enabled"])
            got = plan_prefetch(tmp, policy(sources=["content"]))
            self.assertEqual([j["source"] for j in got["jobs"]], ["content"])


class WorkerTest(unittest.TestCase):
    def test_worker_runs_read_only_miner_and_returns_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = {"ok": True, "rows": 3, "merge": {"added": 3}}
            with mock.patch("core.supply_mine.mine_github", return_value=report) as mine:
                worker = Prefetcher(tmp, policy(sources=["github"]), log=lambda *_: None)
                started = worker.start()
                self.assertEqual(started["status"], "running")
                got = worker.finish()
            self.assertTrue(got["ok"])
            self.assertEqual(got["reports"]["github"]["rows"], 3)
            kwargs = mine.call_args.kwargs
            self.assertEqual(kwargs["per_query"], 5)
            self.assertEqual(kwargs["gap"], 0)
            self.assertIsInstance(kwargs["stop_event"], threading.Event)

    def test_miner_failure_does_not_escape_into_sending_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("core.supply_mine.mine_github", side_effect=RuntimeError("boom")):
                worker = Prefetcher(tmp, policy(sources=["github"]), log=lambda *_: None)
                worker.start()
                got = worker.finish()
            self.assertFalse(got["ok"])
            self.assertIn("boom", got["error"])

    def test_second_prefetcher_does_not_compete_for_pool(self):
        import fcntl
        from core.supply_prefetch import lock_path

        with tempfile.TemporaryDirectory() as tmp:
            path = lock_path(tmp)
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a+")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                worker = Prefetcher(tmp, policy(), log=lambda *_: None)
                got = worker.start()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            self.assertFalse(got["ok"])
            self.assertEqual(got["reason"], "prefetch_already_running")


class CancellationTest(unittest.TestCase):
    def test_github_mining_stops_between_queries(self):
        stop = threading.Event()

        def search(query, limit):
            stop.set()
            return {"ok": True, "items": [{
                "url": "https://github.com/a/b/issues/1", "title": "cost",
                "body": "How do you track LLM cost?", "repo": "a/b"}]}

        with tempfile.TemporaryDirectory() as tmp:
            got = supply_mine.mine_github(
                tmp, queries=["q1", "q2", "q3"], gap=0, search=search,
                stop_event=stop)
        self.assertEqual(len(got["queries"]), 1)
        self.assertTrue(got["stopped"])


if __name__ == "__main__":
    unittest.main()
