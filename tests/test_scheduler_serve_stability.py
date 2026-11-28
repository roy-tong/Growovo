#!/usr/bin/env python3
"""调度器恢复语义：锁冲突和暂时不可运行都不能让调度器直接退出。"""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import cycle_scheduler


class ServeStabilityTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.config = self.data / "jobs.json"
        past = (dt.datetime.now().astimezone() - dt.timedelta(hours=1)).isoformat()
        self.config.write_text(json.dumps({"jobs": [{"id": "j1", "kind": "wave", "at": past}]}),
                               encoding="utf-8")

    def test_already_running_waits_for_next_tick(self):
        calls = []

        def fake_tick(data_dir, config_path, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {"reason": "scheduler_already_running", "jobs": [], "executed": 0}
            return {"reason": "tick_finished", "jobs": [], "executed": 0}

        def fake_status(data_dir, config_path, now=None):
            return {"ok": True, "jobs": [{"id": "j1", "status": "succeeded",
                              "at": "2026-11-27T20:00:00+08:00"}]}

        with mock.patch.object(cycle_scheduler, "tick", side_effect=fake_tick), \
                mock.patch.object(cycle_scheduler, "status", side_effect=fake_status):
            got = cycle_scheduler.serve(self.data, self.config, go=True, once=False,
                                        poll_sec=0.001, sleeper=lambda _: None)
        self.assertEqual(len(calls), 2)
        self.assertEqual(got["reason"], "schedule_finished")

    def test_blocked_due_job_waits_instead_of_no_runnable_jobs(self):
        calls = []

        def fake_tick(data_dir, config_path, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {"reason": "tick_finished", "jobs": [], "executed": 0}
            return {"reason": "tick_finished", "jobs": [], "executed": 0}

        statuses = iter([
            {"ok": True, "jobs": [{"id": "j1", "status": "blocked", "at": "2026-11-27T20:00:00+08:00"}]},
            {"ok": True, "jobs": [{"id": "j1", "status": "succeeded", "at": "2026-11-27T20:00:00+08:00"}]},
        ])
        with mock.patch.object(cycle_scheduler, "tick", side_effect=fake_tick), \
                mock.patch.object(cycle_scheduler, "status", side_effect=lambda *args, **kwargs: next(statuses)):
            got = cycle_scheduler.serve(self.data, self.config, go=True, once=False,
                                        poll_sec=0.001, sleeper=lambda _: None)
        self.assertEqual(len(calls), 2)
        self.assertEqual(got["reason"], "schedule_finished")


if __name__ == "__main__":
    unittest.main()
