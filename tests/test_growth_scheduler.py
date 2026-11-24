import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import cycle_scheduler, growth_service
from tests.test_growth_goals import goal


NOW = "2026-11-23T22:00:00+08:00"


class GrowthSchedulerTest(unittest.TestCase):
    def test_goal_to_next_experiment_runs_as_persistent_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "goal.json").write_text(json.dumps(goal()), encoding="utf-8")
            sid = growth_service.compile_goal(goal())["service"]["id"]
            (data / "metrics.json").write_text(json.dumps({
                "baseline": {"github_stars": 221, "x_followers": 38},
                "final": {"activated_users": 1, "github_stars": 224, "x_followers": 41},
            }), encoding="utf-8")
            jobs = [
                {"id": "compile", "kind": "goal-compile", "at": NOW,
                 "file": "goal.json", "start_service": True},
                {"id": "baseline", "kind": "baseline-observe", "at": NOW,
                 "service_id": sid, "file": "metrics.json", "requires": ["compile"]},
                {"id": "evaluate", "kind": "goal-evaluate", "at": NOW,
                 "service_id": sid, "file": "metrics.json", "requires": ["baseline"]},
                {"id": "experiment", "kind": "next-experiment", "at": NOW,
                 "service_id": sid, "requires": ["evaluate"]},
            ]
            config = data / "jobs.json"
            config.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
            for _ in range(4):
                got = cycle_scheduler.tick(data, config, now=NOW, go=True, max_jobs=1)
            self.assertEqual(1, got["executed"])
            self.assertTrue(all(row["status"] == "succeeded" for row in got["jobs"]))
            service = growth_service.load(data, sid)
            self.assertTrue(service["evaluations"][0]["primary_success"])
            self.assertTrue((data / "state" / "growth-experiments" / sid).is_dir())

    def test_read_job_result_survives_cycle_state_save_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            jobs = [{"id": "read-once", "kind": "inbox-collect", "at": NOW}]
            config = data / "jobs.json"
            config.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
            executor = mock.Mock(return_value={"ok": True, "rows": []})
            with mock.patch("core.cycle_scheduler._save_state", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    cycle_scheduler.tick(data, config, now=NOW, go=True, executor=executor)
            got = cycle_scheduler.tick(data, config, now=NOW, go=True, executor=executor)
            self.assertEqual(1, executor.call_count)
            self.assertEqual("succeeded", got["jobs"][0]["status"])

    def test_interrupted_external_schedule_requires_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            jobs = [{"id": "wave", "kind": "wave", "at": NOW, "wave": "wave-1"}]
            config = data / "jobs.json"
            config.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
            from core import growth_runtime
            growth_runtime.claim_scheduled_job(data, jobs[0], external=True)
            executor = mock.Mock(return_value={"ok": True, "results": []})
            got = cycle_scheduler.tick(data, config, now=NOW, go=True, executor=executor)
            self.assertEqual("needs_reconciliation", got["jobs"][0]["status"])
            executor.assert_not_called()

    def test_github_sync_job_imports_real_adapter_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            jobs = [{"id": "github", "kind": "github-sync", "at": NOW,
                     "service_id": "svc-test", "repo": "o/r"}]
            config = data / "jobs.json"
            config.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
            rows = [{"source": "github_app", "external_id": "issue-comment-9",
                     "author": "bob", "body": "Need help", "received_at": NOW,
                     "repository": "o/r"}]
            client = mock.Mock()
            client.sync_community_interactions.return_value = rows
            with mock.patch("core.github_app.GitHubAppClient.from_env", return_value=client):
                got = cycle_scheduler.tick(data, config, now=NOW, go=True)
            self.assertEqual("succeeded", got["jobs"][0]["status"])
            from core import growth_engagement
            self.assertEqual(1, len(growth_engagement.list_conversations(data, "svc-test")))

    def test_operator_tick_job_uses_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            jobs = [{"id": "operator", "kind": "operator-tick", "at": NOW,
                     "service_id": "svc-test", "repo": "o/r", "gmail_sync": True}]
            config = data / "jobs.json"
            config.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
            with mock.patch("core.growth_worker.run_once",
                            return_value={"components": {"recovery": {"ok": True}}}) as worker:
                got = cycle_scheduler.tick(data, config, now=NOW, go=True)
            self.assertEqual("succeeded", got["jobs"][0]["status"])
            self.assertEqual("o/r", worker.call_args.kwargs["github_repo"])
            self.assertTrue(worker.call_args.kwargs["gmail_sync"])

    def test_opportunity_and_inbox_jobs_use_the_same_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            sid = "svc-test"
            (data / "opportunities.json").write_text(json.dumps([]), encoding="utf-8")
            (data / "interactions.json").write_text(json.dumps([]), encoding="utf-8")
            jobs = [
                {"id": "opportunities", "kind": "opportunity-scan", "at": NOW,
                 "service_id": sid, "file": "opportunities.json"},
                {"id": "inbox", "kind": "inbox-resolve", "at": NOW,
                 "service_id": sid, "file": "interactions.json",
                 "requires": ["opportunities"]},
            ]
            config = data / "jobs.json"
            config.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
            first = cycle_scheduler.tick(data, config, now=NOW, go=True, max_jobs=1)
            second = cycle_scheduler.tick(data, config, now=NOW, go=True, max_jobs=1)
            self.assertEqual(1, first["executed"])
            self.assertEqual(1, second["executed"])
            from core import growth_engagement, growth_opportunities
            growth_opportunities.list_opportunities(data, sid)
            growth_engagement.list_conversations(data, sid)
            self.assertTrue((data / "state" / "growth-opportunities" / (sid + ".json")).is_file())
            self.assertTrue((data / "state" / "growth-engagement" / (sid + ".json")).is_file())


class LiveObservationSchedulerTest(unittest.TestCase):
    def test_baseline_job_can_read_live_sources_without_metrics_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            jobs = [{"id": "baseline", "kind": "baseline-observe", "at": NOW,
                     "service_id": "svc-test", "repos": ["owner/demo"],
                     "x_handle": "owner"}]
            config = data / "jobs.json"
            config.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")
            with mock.patch("core.growth_metrics.observe_live", return_value={"ok": True}) as live:
                got = cycle_scheduler.tick(data, config, now=NOW, go=True)
            self.assertEqual("succeeded", got["jobs"][0]["status"])
            live.assert_called_once_with(
                data, "svc-test", "baseline", ["owner/demo"], "owner", by="cycle-scheduler")


if __name__ == "__main__":
    unittest.main()
