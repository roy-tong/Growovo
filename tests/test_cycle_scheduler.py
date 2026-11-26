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

    def test_wave_uses_its_bound_queue_instead_of_the_default_queue(self):
        queue = self.data / "approved-wave.json"
        queue.write_text("[]", encoding="utf-8")
        from core import scheduled_scope
        at = dt.datetime.now().astimezone() - dt.timedelta(seconds=5)
        self.jobs([self.job("wave", queue=str(queue), at=at.isoformat())])
        scheduled_scope.approve(self.data, self.config, by="fixture")
        with mock.patch("core.engagement_cycle.run", return_value={"ok": True}) as run:
            execute = cycle_scheduler._default_executor(self.config, self.data)
            execute(json.loads(self.config.read_text(encoding="utf-8"))["jobs"][0], True)

        self.assertEqual(run.call_args.kwargs["queue_path"], queue.resolve())

    def test_expired_wave_does_not_execute_after_its_deadline(self):
        self.jobs([self.job("wave", expire_after_sec=60)])
        execute = lambda *_: self.fail("expired wave must not execute")
        late = NOW + dt.timedelta(minutes=10)
        got = cycle_scheduler.tick(self.data, self.config, now=late, go=True, executor=execute)
        self.assertEqual("expired", got["jobs"][0]["status"])

    def test_business_result_separates_committed_from_growth(self):
        self.jobs([self.job("wave", ovo={"wave": "三产品价值内容", "business_goal": "外部首用与传播"})])
        result = {"ok": True, "planned": 3, "committed": 3, "failed": 0,
                  "indeterminate": 0, "results": [{"result": "committed"}] * 3}
        got = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True,
                                   executor=lambda *_: result)
        card = got["jobs"][0]["business_result"]
        self.assertEqual("executed_business_unproven", card["status"])
        self.assertEqual("外部首用与传播", card["goal"])
        self.assertEqual(3, card["execution"]["external_writes"])
        self.assertEqual("not_measured", card["business_outcome"])
        self.assertIn("不能判定业务增长", card["headline"])

    def test_business_result_marks_empty_wave_as_supply_blocked(self):
        self.jobs([self.job("wave")])
        result = {"ok": True, "planned": 0, "committed": 0, "results": [],
                  "skipped": [{"reason": "text_empty"}, {"reason": "text_empty"}]}
        got = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True,
                                   executor=lambda *_: result)
        self.assertEqual("succeeded", got["jobs"][0]["status"])
        card = got["jobs"][0]["business_result"]
        self.assertEqual("blocked_supply", card["status"])
        self.assertEqual({"text_empty": 2}, card["blockers"])
        self.assertIn("业务产出为 0", card["headline"])

    def test_rate_limited_wave_names_the_stopped_channel_instead_of_nothing(self):
        self.jobs([self.job("wave")])
        result = {"ok": True, "planned": 3, "committed": 0, "results": [],
                  "skipped": [], "stopped": {"x": "429"}}
        got = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True,
                                   executor=lambda *_: result)
        card = got["jobs"][0]["business_result"]
        self.assertEqual("blocked", card["status"])
        self.assertEqual(1, card["blockers"]["x:429"])
        self.assertIn("渠道停止 x", card["headline"])

    def test_failed_wave_business_result_survives_terminal_recheck(self):
        self.jobs([self.job("wave")])
        calls = []

        def execute(job, go):
            calls.append(job["id"])
            return {"ok": False, "reason": "outside_window"}

        first = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True, executor=execute)
        second = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True, executor=execute)
        self.assertEqual(1, len(calls))
        self.assertEqual("blocked", first["jobs"][0]["business_result"]["status"])
        self.assertEqual("schedule_future_wave",
                         first["jobs"][0]["business_result"]["recovery"]["disposition"])
        self.assertEqual(first["jobs"][0]["business_result"], second["jobs"][0]["business_result"])
        stored = cycle_scheduler.status(self.data, self.config, now=NOW)
        self.assertEqual("blocked", stored["jobs"][0]["business_result"]["status"])

    def test_job_id_selects_only_requested_due_job(self):
        self.jobs([self.job("first"), self.job("second")])
        calls = []

        def execute(job, go):
            calls.append(job["id"])
            return {"ok": True, "results": []}

        got = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True,
                                   executor=execute, job_id="second")
        self.assertEqual(["second"], calls)
        self.assertEqual(["second"], [row["id"] for row in got["jobs"]])

    def test_business_result_itemizes_actions_before_business_outcome(self):
        self.jobs([self.job("wave")])
        result = {"ok": True, "planned": 7, "committed": 3, "failed": 2, "indeterminate": 1,
                  "supply": {
                      "x/publish": {"effective": 5, "scheduled": 3},
                      "x/reply": {"effective": 10, "scheduled": 2},
                      "github/reply": {"effective": 1, "scheduled": 1},
                  },
                  "results": [
                      {"channel": "x", "action": "publish", "result": "committed", "ref": "p1"},
                      {"channel": "x", "action": "publish", "result": "committed", "ref": "p2"},
                      {"channel": "x", "action": "publish", "result": "failed"},
                      {"channel": "x", "action": "reply", "result": "committed", "ref": "r1"},
                      {"channel": "x", "action": "reply", "result": "indeterminate"},
                      {"channel": "github", "action": "reply", "result": "failed"},
                  ],
                  "skipped": [{"channel": "x", "action": "reply", "reason": "text_empty"},
                              {"channel": "x", "action": "reply", "reason": "text_empty"}]}
        got = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True,
                                   executor=lambda *_: result)
        card = got["jobs"][0]["business_result"]
        publish = card["actions"]["x/publish"]
        self.assertEqual({"attempted": 3, "committed": 2, "failed": 1,
                          "indeterminate": 0, "skipped": 0},
                         {key: publish[key] for key in
                          ("attempted", "committed", "failed", "indeterminate", "skipped")})
        reply = card["actions"]["x/reply"]
        self.assertEqual({"attempted": 2, "committed": 1, "failed": 0,
                          "indeterminate": 1, "skipped": 2},
                         {key: reply[key] for key in
                          ("attempted", "committed", "failed", "indeterminate", "skipped")})
        self.assertEqual(5, publish["target"])
        self.assertEqual(0.4, publish["attainment_rate"])
        self.assertEqual(3, publish["gap"])
        self.assertEqual(6, card["execution"]["attempted"])
        self.assertEqual(16, card["execution"]["target"])
        self.assertEqual(13, card["execution"]["gap"])
        self.assertIn("X 原创：执行 3，发送成功 2，失败 1，未知 0，跳过 0", card["action_summary"])
        self.assertIn("X 回帖：执行 2，发送成功 1，失败 0，未知 1，跳过 2", card["action_summary"])
        self.assertTrue(card["headline"].startswith(card["action_summary"]))

    def test_business_result_uses_explicit_wave_business_targets(self):
        self.jobs([self.job("wave", ovo={"business_targets": {"x/publish": 2}})])
        result = {"ok": True, "supply": {"x/publish": {"effective": 5, "scheduled": 1},
                                               "x/reply": {"effective": 41, "scheduled": 0}},
                  "results": [{"channel": "x", "action": "publish", "result": "committed"}]}
        got = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True,
                                   executor=lambda *_: result)
        action = got["jobs"][0]["business_result"]["actions"]["x/publish"]
        self.assertEqual(2, action["target"])
        self.assertEqual(0.5, action["attainment_rate"])
        self.assertEqual(1, action["gap"])
        card = got["jobs"][0]["business_result"]
        self.assertEqual(2, card["execution"]["target"])
        self.assertEqual(0, card["actions"]["x/reply"]["target"])

    def test_business_result_keeps_missing_verification_unknown(self):
        self.jobs([self.job("wave")])
        result = {"ok": True, "results": [
            {"channel": "x", "action": "publish", "result": "committed"},
            {"channel": "x", "action": "reply", "result": "committed", "public_verified": True},
        ]}
        got = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True,
                                   executor=lambda *_: result)
        actions = got["jobs"][0]["business_result"]["actions"]
        self.assertIsNone(actions["x/publish"]["verified"])
        self.assertEqual(1, actions["x/reply"]["verified"])

    def test_business_result_reports_action_quota_errors_as_execution_blockers(self):
        self.jobs([self.job("wave")])
        result = {"ok": True, "results": [
            {"channel": "github", "action": "reply", "result": "blocked",
             "reason": "quota_exhausted", "error": "account_quota_identity_required"},
        ], "skipped": [{"channel": "x", "action": "reply", "reason": "text_empty"}]}
        got = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True,
                                   executor=lambda *_: result)
        card = got["jobs"][0]["business_result"]
        self.assertEqual("blocked", card["status"])
        self.assertEqual(1, card["blockers"]["quota_exhausted"])
        self.assertEqual(1, card["blockers"]["text_empty"])

    def test_business_result_uses_paired_observations_without_claiming_attribution(self):
        state = self.data / "state"
        state.mkdir()
        source = {"subject": {"account": "@elliwoodtong", "product": "ovo"},
                  "metric_definitions": {"x_followers": "X 公开关注者数"}}
        rows = [
            {"night": "2026-11-23", "phase": "baseline", "observation_pair_id": "pair-1",
             "observed_at": "2026-11-23T19:00:00+08:00", "metrics": {"x_followers": 36},
             "observation_grade": "observed", "source": source},
            {"night": "2026-11-23", "phase": "final", "observation_pair_id": "pair-1",
             "observed_at": "2026-11-23T20:00:00+08:00", "metrics": {"x_followers": 38},
             "observation_grade": "observed", "source": source},
        ]
        (state / "business-observations.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        self.jobs([self.job("wave")])
        result = {"ok": True, "night": "2026-11-23", "planned": 2, "committed": 2,
                  "results": [{"result": "committed"}] * 2}
        got = cycle_scheduler.tick(self.data, self.config, now=NOW, go=True,
                                   executor=lambda *_: result)
        card = got["jobs"][0]["business_result"]
        self.assertEqual("observed", card["business_outcome"])
        self.assertEqual(2, card["business_metrics"]["x_followers"]["delta"])
        self.assertEqual("not_claimed", card["business_attribution"])
        self.assertIn("不声明因果归因", card["headline"])


if __name__ == "__main__":
    unittest.main()
