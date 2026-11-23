#!/usr/bin/env python3
"""Persistent dependency-aware cycle jobs with no blind retries."""
import datetime as dt
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import cycle_scheduler
import mkt

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 11, 23, 19, 10, tzinfo=TZ)


class CycleSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.config = self.data / "jobs.json"

    def jobs(self, rows):
        self.config.write_text(json.dumps({"version": 1, "jobs": rows}), encoding="utf-8")

    def job(self, job_id, kind="wave", requires=None, **extra):
        return dict({"id": job_id, "kind": kind, "at": "2026-11-23T19:05:00+08:00",
                     "requires": requires or []}, **extra)

    def test_dependencies_run_in_order_across_persistent_ticks(self):
        self.jobs([self.job("prepare", "inbox-update"),
                   self.job("wave", requires=["prepare"])])
        calls = []

        def execute(job, go):
            calls.append((job["id"], go))
            return {"ok": True}

        first = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True,
                                     executor=execute, max_jobs=1)
        second = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True,
                                      executor=execute, max_jobs=1)
        self.assertEqual(["succeeded", "succeeded"],
                         [first["jobs"][0]["status"], second["jobs"][0]["status"]])
        self.assertEqual(["prepare", "wave"], [job for job, _ in calls])
        self.assertTrue(all(go for _, go in calls))
        state = cycle_scheduler.status(self.data, self.config, now=NOW)
        self.assertEqual(2, state["counts"]["succeeded"])

    def test_failed_dependency_blocks_wave_and_never_auto_retries(self):
        self.jobs([self.job("prepare", "inbox-update"), self.job("wave", requires=["prepare"])])
        calls = []

        def execute(job, go):
            calls.append(job["id"])
            return {"ok": False, "reason": "invalid_draft"}

        cycle_scheduler.tick(self.data, self.config, now=NOW, go=True, executor=execute)
        again = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True, executor=execute)
        self.assertEqual(["prepare"], calls)
        statuses = {row["id"]: row["status"] for row in again["jobs"]}
        self.assertEqual("failed", statuses["prepare"])
        self.assertEqual("blocked", statuses["wave"])

    def test_indeterminate_requires_reconciliation_and_is_not_retried(self):
        self.jobs([self.job("wave")])
        calls = []

        def execute(job, go):
            calls.append(job["id"])
            return {"ok": True, "results": [{"result": "indeterminate"}]}

        got = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True, executor=execute)
        again = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True, executor=execute)
        self.assertEqual("needs_reconciliation", got["jobs"][0]["status"])
        self.assertEqual(1, len(calls))
        self.assertEqual("needs_reconciliation", again["jobs"][0]["status"])

    def test_wave_lock_is_deferred_and_safe_to_recheck(self):
        self.jobs([self.job("wave")])
        outcomes = iter([{"ok": False, "reason": "wave_already_running"}, {"ok": True, "results": []}])
        calls = []

        def execute(job, go):
            calls.append(job["id"])
            return next(outcomes)

        first = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True, executor=execute)
        second = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True, executor=execute)
        self.assertEqual("deferred", first["jobs"][0]["status"])
        self.assertEqual("succeeded", second["jobs"][0]["status"])
        self.assertEqual(2, len(calls))

    def test_dry_run_plans_without_executor_or_state_write(self):
        self.jobs([self.job("wave")])
        execute = lambda *_: self.fail("dry-run must not execute")
        got = cycle_scheduler.tick(self.data, self.config, now=NOW, go=False, executor=execute)
        self.assertEqual("dry-run", got["jobs"][0]["status"])
        self.assertFalse(cycle_scheduler.state_path(self.data).exists())

    def test_cli_jobs_tick_dry_run_does_not_write_state(self):
        self.jobs([self.job("wave")])
        output = io.StringIO()
        with mock.patch.object(mkt, "DATA", self.data), contextlib.redirect_stdout(output):
            code = mkt.cmd_cycle(SimpleNamespace(
                action="jobs-tick", file=str(self.config), now=NOW.isoformat(),
                go=False, max_jobs=1))
        self.assertEqual(0, code)
        self.assertEqual("dry-run", json.loads(output.getvalue())["jobs"][0]["status"])
        self.assertFalse(cycle_scheduler.state_path(self.data).exists())

    def test_expired_wave_does_not_execute_after_its_deadline(self):
        self.jobs([self.job("wave", expire_after_sec=60)])
        execute = lambda *_: self.fail("expired wave must not execute")
        late = NOW + dt.timedelta(minutes=10)
        got = cycle_scheduler.tick(self.data, self.config, now=late, go=True, executor=execute)
        self.assertEqual("expired", got["jobs"][0]["status"])


if __name__ == "__main__":
    unittest.main()
