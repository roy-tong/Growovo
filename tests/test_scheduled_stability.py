import datetime as dt
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from core import cycle_scheduler, engagement_cycle as cycle, scheduled_build, scheduled_scope
from core.gtm_design import GTMDesignError, normalize_constraints
from core.job_kernel import JobStateMachine


class ScheduledStabilityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.config = self.data / "jobs.json"
        self.queue = self.data / "approved.json"
        self.row = {"id": "one", "channel": "github", "action": "reply",
                    "target": "issue:example/repo#1", "text": "approved reply", "actor": "peer",
                    "context": "approved peer context"}
        self.now = dt.datetime.now().astimezone()
        github = patch("core.bridge.gh_post.GH", sys.executable)
        github.start()
        self.addCleanup(github.stop)
        self.job = {"id": "first", "kind": "wave", "at": (self.now - dt.timedelta(seconds=3)).isoformat(),
                    "expire_after_sec": 200, "queue": self.queue.name, "accounts": {"github": "owner"},
                    "targets": {"github/reply": 3}}
        self.save()
        self.queue.write_text(json.dumps([self.row]))

    def save(self, **fields):
        self.config.write_text(json.dumps(dict(jobs=[self.job], **fields)))

    def approve(self):
        return scheduled_scope.approve(self.data, self.config, by="fixture")

    def test_mutations_clocks_and_explicit_stop(self):
        self.approve()
        with scheduled_scope.activate(self.data, self.config, self.job):
            scheduled_scope.check_identity("github", "@Owner")
            scheduled_scope.check_item(self.row)
            for channel, handle in (("discord", "owner"), ("github", "other")):
                with self.assertRaisesRegex(ValueError, "account_mismatch"):
                    scheduled_scope.check_identity(channel, handle)
            with self.assertRaisesRegex(ValueError, "item_changed"):
                scheduled_scope.check_item(dict(self.row, text="substituted"))
            self.save(plan_status="STOPPED_BY_USER")
            with self.assertRaisesRegex(ValueError, "schedule_stopped"):
                scheduled_scope.check_item(self.row)
        self.save()
        for edit in ({"at": self.now.isoformat()}, {"expire_after_sec": 600}, {"queue": "other.json"}):
            (self.data / "other.json").write_text(self.queue.read_text())
            with self.assertRaisesRegex(ValueError, "job_changed"), scheduled_scope.activate(
                    self.data, self.config, dict(self.job, **edit)):
                self.fail("changed job dispatched")
        self.queue.write_text(json.dumps([dict(self.row, channel="discord")]))
        with self.assertRaisesRegex(ValueError, "item_changed"), scheduled_scope.activate(
                self.data, self.config, self.job):
            self.fail("changed row dispatched")

    def test_outside_window_cannot_use_simulated_time_for_dispatch(self):
        self.job.update(at=(self.now-dt.timedelta(hours=1)).isoformat())
        self.save()
        self.approve()
        fake_now = dt.datetime.fromisoformat(self.job["at"])
        with patch.object(cycle, "run") as run:
            result = cycle_scheduler.tick(self.data, self.config, now=fake_now, go=True)
        run.assert_not_called()
        self.assertEqual("outside_approved_window", result["changes"][0]["reason"])

    def test_offline_preview_then_two_independent_authorized_batches(self):
        preview = scheduled_scope.approve(self.data, self.config, preview=True)
        self.assertEqual(3, preview["capacity"]["first"]["targets"]["github/reply"])
        self.assertFalse((self.data / "approvals").exists())
        policies = self.data / "policies"
        policies.mkdir()
        (policies / "account-quotas.json").write_text(json.dumps({"accounts": [{
            "platform": "github", "handle": "owner", "account_tier": "unknown",
            "daily_caps": {"reply": 10},
        }]}))
        policy = json.loads(json.dumps(cycle.DEFAULT_POLICY))
        policy["window"] = {"start_hour": self.now.hour, "end_hour": (self.now.hour + 2) % 24}
        policy["platforms"] = {"github": {"reply": {
            "min_gap_sec": 0, "per_hour": 10, "per_night": 10}}}
        (policies / "engagement-cycle.json").write_text(json.dumps(policy))
        for index in range(2):
            self.job["id"] = "batch-%d" % index
            self.row.update(target="issue:example/repo#%s" % (index+1))
            self.queue.write_text(json.dumps([self.row]))
            self.save()
            self.approve()

            def adapter(**kwargs):
                return {"ok": True, "result": "committed", "ledger_result": "committed",
                        "ref": "offline-receipt-%s" % kwargs["target"]}

            with patch("core.platform_compliance.owner_attested_active", return_value=True), \
                    patch("core.action_gateway.execute", side_effect=adapter) as dispatch, \
                    patch.object(cycle.time, "sleep"):
                first = cycle_scheduler.tick(self.data, self.config, go=True)
                again = cycle_scheduler.tick(self.data, self.config, go=True)
            self.assertEqual(1, dispatch.call_count, first)
            self.assertEqual(self.row["target"], dispatch.call_args.kwargs["target"])
            self.assertEqual([], again["changes"])
            self.assertEqual("completed_with_errors", first["jobs"][0]["status"])
            self.assertEqual(1, first["jobs"][0]["business_result"]["execution"]["committed"])
            settled = cycle_scheduler.status(self.data, self.config)["settlement"]
            self.assertEqual((1, 3, 1), (settled["settled"], settled["target"], settled["committed"]))

    def test_missing_queue_is_terminal_and_reportable_without_wave_file(self):
        del self.job["queue"]
        self.save()
        with patch.object(cycle, "run") as run:
            result = cycle_scheduler.tick(self.data, self.config, go=True)
        run.assert_not_called()
        self.assertEqual("scheduled_queue_required", result["changes"][0]["reason"])
        self.assertEqual(1, cycle_scheduler.status(self.data, self.config)["settlement"]["settled"])

    def test_legacy_settled_wave_without_a_card_is_not_reported_as_waiting(self):
        # 旧构建写下的终态没有结算卡；把它算成「还在等」会让整晚报告永远失败。
        state = Path(self.data) / "state" / "cycle-jobs-state.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"jobs": {"first": {
            "intent": scheduled_scope.digest(scheduled_scope.job_spec(self.job)),
            "status": "succeeded", "reason": "", "updated_at": self.now.isoformat()}}}))
        settlement = cycle_scheduler.status(self.data, self.config)["settlement"]
        self.assertEqual((1, 1, 0), (settlement["due"], settlement["terminal"], settlement["settled"]))
        self.assertEqual([], settlement["waiting"])
        self.assertEqual(["first"], settlement["unmeasured"])

    def test_status_reports_undelivered_notifications(self):
        from core import notification_outbox
        notification_outbox.record(self.data, {"id": "first", "event_id": "first@1",
                                               "status": "succeeded", "next_action": "observe"})
        got = cycle_scheduler.status(self.data, self.config)
        self.assertEqual({"pending": 1}, got["notifications"])
        notification_outbox.deliver_pending(self.data, lambda event: "receipt-1")
        self.assertEqual({"pending": 0}, cycle_scheduler.status(self.data, self.config)["notifications"])

    def test_queue_shared_by_two_waves_survives_row_consumption(self):
        rows = [dict(self.row), dict(self.row, id="two", target="issue:example/repo#2")]
        self.queue.write_text(json.dumps(rows))
        second_job = dict(self.job, id="second")
        self.config.write_text(json.dumps({"jobs": [self.job, second_job]}))
        self.approve()
        # 第一波正常出队后，同一队列文件里剩下的行仍属于第二波已批准的范围内。
        from core.queue_store import QueueStore
        QueueStore(self.queue).consume(rows, rows[0], action_id="dispatch-1")
        with scheduled_scope.activate(self.data, self.config, second_job) as (path, capacity):
            self.assertEqual(self.queue.resolve(), Path(path).resolve())
            self.assertEqual({"github/reply": 3}, capacity["targets"])
        # 但被替换正文的行仍然必须拒绝。
        QueueStore(self.queue).save([dict(rows[1], text="substituted")])
        with self.assertRaisesRegex(ValueError, "item_changed"):
            with scheduled_scope.activate(self.data, self.config, second_job):
                self.fail("substituted row dispatched")

    def hourly_pair(self):
        later = dict(self.job, id="next", slot=1, requires=["first"])
        self.job["slot"] = 0
        self.config.write_text(json.dumps({"cadence": "hourly", "jobs": [self.job, later]}))
        with self.assertRaisesRegex(ValueError, "hourly"):
            cycle_scheduler.status(self.data, self.config)
        later["at"] = (self.now + dt.timedelta(seconds=3597)).isoformat()
        self.config.write_text(json.dumps({"cadence": "hourly", "jobs": [self.job, later]}))
        return later

    def test_hourly_slots_and_partial_dependency_keeps_catching_up(self):
        later = self.hourly_pair()
        # 目标 3 条只提交 1 条 = 缺口，但上游「跑完了」；下一小时正是补缺口的机会，
        # 不能因为一次限流/少量失败就把后面所有小时波次整夜 blocked。
        first = cycle_scheduler.tick(
            self.data, self.config, go=True,
            executor=lambda *_: {"ok": True, "results": [dict(self.row, result="committed")]})
        self.assertEqual("completed_with_errors", first["jobs"][0]["status"])
        ran = []

        def executor(job, go):
            ran.append(job["id"])
            return {"ok": True, "results": [dict(self.row, result="committed")]}

        got = cycle_scheduler.tick(self.data, self.config, now=later["at"], go=True,
                                   job_id="next", executor=executor)
        self.assertEqual(["next"], ran)
        self.assertEqual("completed_with_errors", got["jobs"][0]["status"])

    def test_unknown_upstream_result_blocks_the_next_wave(self):
        later = self.hourly_pair()
        first = cycle_scheduler.tick(
            self.data, self.config, go=True,
            executor=lambda *_: {"ok": True,
                                 "results": [dict(self.row, result="indeterminate")]})
        self.assertEqual("needs_reconciliation", first["jobs"][0]["status"])
        got = cycle_scheduler.tick(self.data, self.config, now=later["at"], go=True,
                                   job_id="next", executor=lambda *_: self.fail("blocked dependency"))
        self.assertEqual("blocked", got["jobs"][0]["status"])

    def test_future_hourly_wave_does_not_start_with_due_wave(self):
        later = dict(self.job, id="next", slot=1,
                     at=(dt.datetime.fromisoformat(self.job["at"]) + dt.timedelta(hours=1)).isoformat())
        self.job["slot"] = 0
        self.config.write_text(json.dumps({
            "cadence": "hourly", "start_at": self.job["at"], "jobs": [self.job, later]}))
        calls = []

        def executor(job, go):
            calls.append(job["id"])
            return {"ok": True, "results": [dict(self.row, result="committed")]}

        cycle_scheduler.tick(self.data, self.config, go=True, executor=executor)
        self.assertEqual(["first"], calls)
        cycle_scheduler.tick(self.data, self.config, now=later["at"], go=True,
                             job_id="next", executor=executor)
        self.assertEqual(["first", "next"], calls)

    def test_jobs_serve_waits_and_finishes_without_a_host_agent(self):
        self.assertTrue(hasattr(cycle_scheduler, "serve"))
        later = dict(self.job, id="next",
                     at=(self.now + dt.timedelta(hours=1)).isoformat())
        self.config.write_text(json.dumps({"jobs": [self.job, later]}))
        current = [self.now]
        calls = []
        sleeps = []

        def executor(job, go):
            calls.append(job["id"])
            return {"ok": True, "results": [dict(self.row, result="committed")] * 3}

        def sleep(seconds):
            sleeps.append(seconds)
            current[0] = later["at"]

        got = cycle_scheduler.serve(
            self.data, self.config, executor=executor, poll_sec=0.01,
            clock=lambda: current[0], sleeper=sleep)
        self.assertEqual(["first", "next"], calls)
        self.assertEqual([0.01], sleeps)
        self.assertEqual("schedule_finished", got["reason"])
        self.assertTrue(got["ok"])

    def test_jobs_serve_cli_uses_the_pinned_product_runner(self):
        self.queue.write_text("[]")
        self.job.pop("targets")
        self.job["policy_overrides"] = {"window": {
            "start_at": (self.now - dt.timedelta(minutes=1)).isoformat(),
            "end_at": (self.now + dt.timedelta(minutes=5)).isoformat(),
        }}
        self.save()
        self.approve()
        root = Path(__file__).resolve().parents[1]
        ran = subprocess.run([
            sys.executable, str(root / "mkt.py"), "--workspace", str(self.data),
            "cycle", "jobs-serve", "--file", str(self.config), "--once",
            "--poll-sec", "0.01",
        ], text=True, capture_output=True)
        self.assertEqual(0, ran.returncode, ran.stdout + ran.stderr)
        got = json.loads(ran.stdout)
        self.assertEqual("schedule_finished", got["reason"])
        self.assertEqual(1, got["executed"])
        self.assertEqual("not_used", got["operations"]["host_agent"])
        self.assertEqual(0, got["operations"]["scheduler_token_usage"])
        self.assertEqual("not_measured", got["operations"]["other_token_usage"])

    def test_historical_38_github_rows_have_zero_eligible_actions(self):
        from core.ledger import Ledger
        ledger = Ledger(self.data / "ledger.jsonl")
        rows = []
        for index in range(38):
            item = dict(self.row, id="row-%d" % index,
                        target="issue:example/repo#%d" % (index + 1),
                        queued_at=self.now.isoformat())
            if index < 29:
                ledger.append(channel="github", action="reply", target=item["target"],
                              result="committed", ref="old-%d" % index)
            elif index < 37:
                item["text"] = ""
            else:
                item["context"] = ""
            rows.append(item)
        policy = json.loads(json.dumps(cycle.DEFAULT_POLICY))
        policy["platforms"] = {"github": policy["platforms"]["github"]}
        policy["supply"]["wave_targets"] = {"github/reply": 38}
        got = cycle.plan(rows, policy, ledger=ledger, now=self.now)
        self.assertEqual([], got["scheduled"])
        reasons = {}
        for item in got["skipped"]:
            reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
        self.assertEqual({"already_done": 29, "text_empty": 8,
                          "reply_context_missing": 1}, reasons)

    def test_signed_immediate_window_overrides_the_daily_window(self):
        self.queue.write_text("[]")
        self.job.pop("targets")
        self.job["policy_overrides"] = {"window": {
            "start_at": (self.now - dt.timedelta(minutes=1)).isoformat(),
            "end_at": (self.now + dt.timedelta(minutes=5)).isoformat(),
        }}
        self.save()
        policies = self.data / "policies"
        policies.mkdir()
        (policies / "engagement-cycle.json").write_text(json.dumps({"window": {
            "start_hour": (self.now.hour + 2) % 24,
            "end_hour": (self.now.hour + 3) % 24,
        }}))
        self.approve()
        got = cycle_scheduler.tick(self.data, self.config, go=True)
        self.assertEqual("succeeded", got["jobs"][0]["status"], got)
        self.assertEqual(0, got["jobs"][0]["business_result"]["execution"]["target"])

    def test_explicit_window_must_cover_the_job_deadline(self):
        self.job["policy_overrides"] = {"window": {
            "start_at": (self.now - dt.timedelta(minutes=1)).isoformat(),
            "end_at": (self.now + dt.timedelta(seconds=30)).isoformat(),
        }}
        self.save()
        with self.assertRaisesRegex(ValueError, "execution_window_mismatch"):
            self.approve()

    def test_unknown_policy_override_is_rejected_before_approval(self):
        for key in ("unknown_hidden_switch", "quota", "wave_stops"):
            with self.subTest(key=key):
                self.job["policy_overrides"] = {key: {}}
                self.save()
                with self.assertRaisesRegex(ValueError, "unknown_policy_override"):
                    self.approve()

    def test_github_dependency_missing_blocks_approval_before_any_wave(self):
        with patch("core.bridge.gh_post.GH", "/offline/missing-gh"):
            with self.assertRaisesRegex(ValueError, "scheduled_dependency_missing:gh"):
                self.approve()

    def test_six_blog_posts_with_links_run_in_a_signed_immediate_window(self):
        rows = [{"id": "blog-%d" % index, "channel": "x", "action": "publish",
                 "target": "", "actor": "", "text": "OVO article %d https://example.test/blog/%d" % (index, index)}
                for index in range(6)]
        self.queue.write_text(json.dumps(rows))
        self.job.update(accounts={"x": "owner"}, allowed_channels=["x"],
                        targets={"x/publish": 6})
        self.job["policy_overrides"] = {"window": {
            "start_at": (self.now - dt.timedelta(minutes=1)).isoformat(),
            "end_at": (self.now + dt.timedelta(minutes=5)).isoformat(),
        }, "content_rules": {"x_publish": {"links_in_body": True}},
           "platforms": {"x": {"publish": {"min_gap_sec": 0, "per_hour": 10, "per_night": 90},
                                  "reply": {"min_gap_sec": 0}}}}
        self.save()
        policies = self.data / "policies"
        policies.mkdir()
        (policies / "account-quotas.json").write_text(json.dumps({"accounts": [{
            "platform": "x", "handle": "owner", "account_tier": "premium",
            "daily_caps": {"publish": 500}}]}))
        (policies / "engagement-cycle.json").write_text(json.dumps({"window": {
            "start_hour": (self.now.hour + 2) % 24,
            "end_hour": (self.now.hour + 3) % 24}}))
        with patch("core.platform_compliance.owner_attested_active", return_value=True):
            preview = scheduled_scope.approve(self.data, self.config, preview=True)
            self.approve()
        self.assertEqual(6, preview["capacity"]["first"]["eligible"].get("x/publish"), preview)

        def adapter(**kwargs):
            return {"ok": True, "result": "committed", "ledger_result": "committed",
                    "ref": "offline-x-%s" % kwargs["text"].split()[2]}

        with patch("core.platform_compliance.owner_attested_active", return_value=True), \
                patch("core.action_gateway.execute", side_effect=adapter) as dispatch, \
                patch.object(cycle.time, "sleep"):
            got = cycle_scheduler.tick(self.data, self.config, go=True)
        self.assertEqual(6, dispatch.call_count, got)
        self.assertTrue(all("https://example.test/blog/" in call.kwargs["text"]
                            for call in dispatch.call_args_list))
        self.assertEqual("succeeded", got["jobs"][0]["status"])
        self.assertEqual(6, got["jobs"][0]["business_result"]["execution"]["committed"])

    def test_matching_frozen_build_can_activate_with_subset_manifest(self):
        self.approve()
        record = json.loads(scheduled_scope._record(self.data, self.config).read_text())
        frozen_root = self.data / "state" / "scheduled-builds" / record["build"]
        with patch("core.scheduled_scope.source_fingerprint", return_value={"sha256": "subset-manifest"}), \
             patch("core.scheduled_scope._frozen_build_matches", return_value=True), \
             patch("core.scheduled_build.verify", return_value=frozen_root) as verify:
            with scheduled_scope.activate(self.data, self.config, self.job):
                pass
        verify.assert_called_once_with(self.data, record["build"])

    def test_pin_and_pre_dispatch_build_integrity(self):
        self.approve()
        sha = scheduled_scope.pinned_build(self.data, self.config)
        root = scheduled_build.verify(self.data, sha)
        file = root / "mkt.py"
        file.chmod(0o600)
        file.write_text("changed")
        with self.assertRaisesRegex(ValueError, "build_changed"):
            scheduled_build.verify(self.data, sha)

    def test_cli_uses_frozen_build_after_development_source_changes(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            for directory in ("core", "channels"):
                shutil.copytree(root / directory, source / directory,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            for name in ("mkt.py", "mcp_server.py"):
                if (root / name).is_file():
                    shutil.copy2(root / name, source / name)
            data = Path(tmp) / "workspace"
            data.mkdir()
            config = data / "jobs.json"
            (data / "approved.json").write_text("[]")
            self.job.pop("targets")
            self.job["at"] = self.now.isoformat()
            config.write_text(json.dumps({"jobs": [self.job]}))
            policy = {"window": {"start_hour": self.now.hour,
                                 "end_hour": (self.now.hour + 2) % 24}}
            (data / "policies").mkdir()
            (data / "policies" / "engagement-cycle.json").write_text(json.dumps(policy))
            base = [sys.executable, str(source / "mkt.py"), "--workspace", str(data), "cycle"]
            approved = subprocess.run(base + ["jobs-approve", "--file", str(config), "--by", "fixture"],
                                      text=True, capture_output=True)
            self.assertEqual(0, approved.returncode, approved.stdout + approved.stderr)
            with (source / "core" / "engagement_cycle.py").open("a") as handle:
                handle.write("\n# development tree changed after approval\n")
            ran = subprocess.run(base + ["jobs-tick", "--file", str(config), "--job-id", "first"],
                                 text=True, capture_output=True)
            self.assertEqual(0, ran.returncode, ran.stdout + ran.stderr)
            got = json.loads(ran.stdout)
            self.assertEqual("succeeded", got["jobs"][0]["status"])
            self.assertEqual(1, got["executed"])
            report = subprocess.run(base + ["report", "--file", str(config)],
                                    text=True, capture_output=True)
            self.assertEqual(0, report.returncode, report.stdout + report.stderr)
            settlement = json.loads(report.stdout)["settlement"]
            self.assertEqual({"due": 1, "settled": 1},
                             {key: settlement[key] for key in ("due", "settled")})
            self.assertEqual(0, settlement["target"])

    def test_unknown_and_rate_limit_survive_midnight(self):
        for channel, reason in (("github", "indeterminate"), ("x", "429")):
            cycle.mark_stopped(self.data, "2026-11-23", channel, reason,
                               requires_human_resolution=channel == "github", unresolved_ref="ref")
            self.assertEqual("open", cycle.stop_state(self.data, "2026-11-24", channel))
        self.assertEqual("closed", cycle.stop_state(self.data, "2026-11-24", "discord"))

    def test_zero_partial_complete_and_input_type(self):
        for count, wanted in ((0, "completed_with_errors"), (1, "completed_with_errors"), (3, "succeeded")):
            result = dict(ok=True, target=3, results=[{"result": "committed"}]*count)
            self.assertEqual(wanted, JobStateMachine.transition("wave", result)[0])
        with self.assertRaisesRegex(GTMDesignError, "data_connected"):
            normalize_constraints({"data_connected": True})

    def test_removed_queue_item_invalidates_the_approved_scope(self):
        self.approve()
        self.queue.write_text("[]")
        with self.assertRaisesRegex(ValueError, "item_changed"), scheduled_scope.activate(
                self.data, self.config, self.job):
            self.fail("removed approved row dispatched")

    def test_partial_tick_is_not_successful(self):
        self.approve()
        with patch.object(cycle, "run", return_value={
                "ok": True, "results": [dict(self.row, result="committed")]}):
            partial = cycle_scheduler.tick(self.data, self.config, go=True)
        self.assertEqual("completed_with_errors", partial["jobs"][0]["status"])
        self.assertFalse(partial["ok"])
        operations = partial["jobs"][0]["business_result"]["operations"]
        self.assertEqual("completed_with_errors", operations["scheduler_status"])
        self.assertEqual(2, operations["exit_code"])
        self.assertEqual("not_measured", operations["token_usage"])

    def test_expired_tick_is_not_successful(self):
        self.job["expire_after_sec"] = 1
        self.save()
        expired = cycle_scheduler.tick(
            self.data, self.config, now=self.now + dt.timedelta(seconds=10), go=True,
            executor=lambda *_: self.fail("expired job dispatched"))
        self.assertEqual("expired", expired["jobs"][0]["status"])
        self.assertFalse(expired["ok"])

    def test_user_stop_settles_due_job_without_dispatch_or_wave_file(self):
        self.approve()
        self.save(plan_status="STOPPED_BY_USER")
        with patch.object(cycle, "run") as run:
            got = cycle_scheduler.tick(self.data, self.config, go=True)
        run.assert_not_called()
        self.assertEqual("failed", got["jobs"][0]["status"])
        self.assertEqual("schedule_stopped", got["changes"][0]["reason"])
        self.assertEqual(1, cycle_scheduler.status(self.data, self.config)["settlement"]["settled"])
        from core import notification_outbox
        self.assertEqual(1, len(notification_outbox.pending(self.data)))
        self.assertEqual("schedule_stopped", notification_outbox.pending(self.data)[0]["reason"])
