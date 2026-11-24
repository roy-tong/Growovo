import datetime as dt
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import mkt
from core import engagement_cycle as cycle
from core import growth_runtime, job_kernel, receipt_audit, run_manifest, supply_pool
from core.job_kernel import JobStateMachine
from core.supply_prefetch import Prefetcher, plan_prefetch


class NightStabilityMatrixTest(unittest.TestCase):
    def test_positive_target_with_zero_actions_is_not_success(self):
        status, reason = JobStateMachine.transition(
            "wave", {"ok": True, "target": 54, "planned": 0, "results": []})
        self.assertEqual("completed_with_errors", status)
        self.assertEqual("target_shortfall", reason)

    def test_zero_target_observation_can_succeed(self):
        status, _ = JobStateMachine.transition(
            "inbox-collect", {"ok": True, "target": 0, "planned": 0, "results": []})
        self.assertEqual("succeeded", status)

    def test_now_is_forwarded_to_cycle_run(self):
        with tempfile.TemporaryDirectory() as data, \
                mock.patch.object(mkt, "DATA", Path(data)), \
                mock.patch("core.engagement_cycle.run", return_value={"ok": True}) as run:
            code = mkt.cmd_cycle(SimpleNamespace(
                action="run", now="2026-11-24T01:05:00+08:00", go=False, dry_run=True,
                queue="", max_actions=0, wave="04-0105", budget_sec=3000,
                session="ovo-night-run", prefetch=None, auto_inbox=None))
        self.assertEqual(0, code)
        self.assertEqual(dt.datetime.fromisoformat("2026-11-24T01:05:00+08:00"),
                         run.call_args.kwargs["now"])

    def test_cycle_run_parser_accepts_now(self):
        args = mkt.build_parser().parse_args(["cycle", "run", "--now", "2026-11-24T01:05:00+08:00"])
        self.assertEqual("2026-11-24T01:05:00+08:00", args.now)

    def test_source_change_between_waves_is_blocked_before_external_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            source = Path(tmp) / "source"
            (source / "core").mkdir(parents=True)
            (source / "channels").mkdir()
            (source / "mkt.py").write_text("# entry\n", encoding="utf-8")
            (source / "core" / "a.py").write_text("A = 1\n", encoding="utf-8")
            queue = data / "state" / "engagement-queue.json"
            cycle.save_queue(queue, [{"channel": "github", "action": "reply",
                                      "target": "owner/repo#1", "text": "hello",
                                      "source": "queue", "actor": "", "context": "peer context"}])
            now = dt.datetime(2026, 11, 23, 22, 5, tzinfo=dt.timezone(dt.timedelta(hours=8)))
            original_assert = run_manifest.assert_source_stable
            with mock.patch.object(cycle, "_now", return_value=now), \
                    mock.patch("core.run_manifest.assert_source_stable",
                               side_effect=lambda *args, **kwargs: original_assert(
                                   data, night=kwargs["night"], source_dir=source)), \
                    mock.patch("core.action_runner.run_action",
                               return_value={"result": "committed", "ledger_result": "committed", "ref": "op-1"}) as send:
                first = cycle.run(data, go=True, queue_path=queue, wave_id="01-2205", log=lambda *_: None)
                self.assertTrue(first["ok"])
                (source / "core" / "a.py").write_text("A = 2\n", encoding="utf-8")
                second = cycle.run(data, go=True, queue_path=queue, wave_id="02-2305", log=lambda *_: None)
            self.assertFalse(second["ok"])
            self.assertEqual("source_version_changed", second["reason"])
            self.assertEqual(1, send.call_count)

    def test_zero_eligible_wave_skips_browser_inbox_collection(self):
        with tempfile.TemporaryDirectory() as data:
            queue = Path(data) / "state" / "engagement-queue.json"
            cycle.save_queue(queue, [])
            with mock.patch("core.engagement_cycle.collect_inbox_report") as collect:
                got = cycle.run(data, go=True, queue_path=queue, wave_id="04-0105",
                                auto_inbox=True,
                                now=dt.datetime.fromisoformat("2026-11-24T01:05:00+08:00"))
            self.assertEqual(0, got["planned"])
            collect.assert_not_called()

    def test_worker_can_execute_legacy_wave_through_ready_adapter(self):
        with tempfile.TemporaryDirectory() as data:
            from core import growth_worker
            with mock.patch("core.engagement_cycle.run", return_value={"ok": True, "planned": 0}) as run:
                result = growth_worker.run_once(data, "svc-test", wave_id="04-0105")
            self.assertEqual("04-0105", run.call_args.kwargs["wave_id"])
            self.assertTrue(result["components"]["legacy_wave"]["ok"])

    def test_account_lane_blocks_second_writer_without_stopping_reads(self):
        import threading
        from core import action_gateway
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def send(**kwargs):
            calls.append(kwargs)
            entered.set()
            release.wait(timeout=5)
            return {"result": "committed", "evidence": "receipt"}

        with tempfile.TemporaryDirectory() as data:
            with mock.patch("core.action_runner.run_action", side_effect=send) as sender:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(action_gateway.execute,
                                        data_dir=data, channel="github", action="reply",
                                        campaign="test", go=True, target="issue:o/r#1", text="one")
                    self.assertTrue(entered.wait(timeout=5))
                    second = action_gateway.execute(
                        data_dir=data, channel="github", action="reply",
                        campaign="test", go=True, target="issue:o/r#2", text="two")
                    self.assertEqual("account_busy", second["result"])
                    self.assertEqual({"conversations": []},
                                     growth_runtime.load_engagement(data, "svc-test"))
                    release.set()
                    self.assertEqual("committed", first.result()["result"])
            self.assertEqual(1, sender.call_count)

    def test_account_lane_is_renewed_while_a_slow_writer_is_active(self):
        from core import action_gateway
        entered = threading.Event()

        def send(**kwargs):
            entered.set()
            time.sleep(4.2)
            return {"result": "committed"}

        real_acquire = growth_runtime.try_acquire_execution_lane

        def short_lease(data_dir, account, route, owner, lease_seconds=300):
            return real_acquire(data_dir, account, route, owner, lease_seconds=3)

        with tempfile.TemporaryDirectory() as data:
            with mock.patch("core.growth_runtime.try_acquire_execution_lane",
                            side_effect=short_lease), \
                    mock.patch("core.action_gateway.LEASE_HEARTBEAT_SECONDS", 0.2,
                               create=True), \
                    mock.patch("core.action_runner.run_action", side_effect=send):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    first = pool.submit(action_gateway.execute,
                                        data_dir=data, channel="github", action="reply",
                                        campaign="test", go=True, target="issue:o/r#1", text="one")
                    self.assertTrue(entered.wait(timeout=5))
                    time.sleep(3.4)
                    second = action_gateway.execute(
                        data_dir=data, channel="github", action="reply",
                        campaign="test", go=True, target="issue:o/r#2", text="two")
                    self.assertEqual("account_busy", second["result"])
                    self.assertEqual("committed", first.result(timeout=5)["result"])

    def test_candidate_inventory_creates_draft_preparation_not_false_idle(self):
        with tempfile.TemporaryDirectory() as data:
            supply_pool.merge(data, [{"channel": "github", "action": "reply",
                                      "target": f"https://github.com/a/b/issues/{index}",
                                      "context": "cost question"} for index in range(30)])
            policy = {"supply": {"wave_targets": {"github/reply": 15},
                                 "prefetch": {"enabled": True, "lookahead_waves": 2,
                                              "sources": ["github"]}}}
            got = plan_prefetch(data, policy)
        self.assertEqual("draft_preparation", got["jobs"][0]["kind"])
        self.assertEqual(30, got["jobs"][0]["candidates"])

    def test_draft_preparation_requires_a_real_writer(self):
        with tempfile.TemporaryDirectory() as data:
            supply_pool.merge(data, [{"channel": "github", "action": "reply",
                                      "target": f"https://github.com/a/b/issues/{index}",
                                      "context": "cost question"} for index in range(30)])
            policy = {"supply": {"wave_targets": {"github/reply": 15},
                                 "prefetch": {"enabled": True, "lookahead_waves": 2,
                                              "sources": ["github"]}}}
            writer = mock.Mock(return_value={"ok": True, "written": 30})
            worker = Prefetcher(data, policy, draft_writer=writer)
            worker.start()
            got = worker.finish()
        self.assertTrue(got["ok"])
        self.assertEqual(30, writer.call_args.kwargs["job"]["candidates"])

    def test_discord_live_verifier_checks_real_message_id_and_content(self):
        evidence = str({"serverId": "1574426907913752730",
                        "content": "Keep the counter-evidence in the same document."})
        record = {"channel": "discord", "target": "1573251023500476427",
                  "evidence": evidence, "identity_handle": "tongroy"}
        browser = mock.Mock()
        browser.eval.side_effect = lambda tab, code: (
            "https://discord.com/channels/1573250833259171951/1573251023500476427"
            if code == "location.href" else
            [{"id": "chat-messages-1573251023500476427-1574426907913752730", "text": evidence}])
        browser.read_page.return_value = "chat-messages-1573251023500476427-1574426907913752730\n" + evidence
        browser.observe_identity.return_value = {"handle": "tongroy"}
        got = receipt_audit.live_verify(record, browser=browser)
        self.assertEqual("verified", got["status"])

    def test_discord_legacy_receipt_fields_and_indeterminate_audit_are_supported(self):
        evidence = "{'lastId': '1', 'lastText': 'Keep the counter-evidence.', 'mine'"
        record = {"channel": "discord", "target": "1573251023500476427",
                  "evidence": evidence, "identity_handle": "tongroy",
                  "operations_receipt_id": "1574426907913752730"}
        browser = mock.Mock()
        browser.eval.side_effect = lambda tab, code: (
            "https://discord.com/channels/1573250833259171951/1573251023500476427"
            if code == "location.href" else
            [{"id": "chat-messages-1573251023500476427-1574426907913752730", "text": evidence}])
        browser.read_page.return_value = "chat-messages-1573251023500476427-1574426907913752730\n" + evidence
        browser.observe_identity.return_value = {"handle": "tongroy"}
        got = receipt_audit.live_verify(record, browser=browser)
        self.assertEqual("verified", got["status"])

        with tempfile.TemporaryDirectory() as data:
            directory = Path(data) / "state" / "engagement-2026-11-23"
            directory.mkdir(parents=True)
            (directory / "wave-01-2205.json").write_text(json.dumps({
                "ok": True, "wave": "01-2205", "night": "2026-11-23",
                "campaign": "night-2026-11-23-w01-2205", "dry": False,
                "results": [{"channel": "discord", "action": "publish", "target": "1",
                             "result": "indeterminate", "ref": "op-1"}]}), encoding="utf-8")
            from core.ledger import Ledger
            ledger = Ledger(Path(data) / "ledger.jsonl")
            ledger.append(channel="discord", action="publish", target="1", result="indeterminate",
                          campaign="night-2026-11-23-w01-2205", evidence=evidence, ref="op-1")
            got = receipt_audit.audit(
                data, night="2026-11-23", live=True,
                verifier=lambda record: {"status": "verified", "detail": "independent"})
        self.assertEqual(1, got["counts"]["verified"])

    def test_x_live_verifier_checks_real_status_page(self):
        record = {"channel": "x", "action": "publish", "evidence": "2124902346899742875",
                  "identity_handle": "elliwoodtong"}
        browser = mock.Mock()
        browser.eval.return_value = "https://x.com/elliwoodtong/status/2124902346899742875"
        browser.read_page.return_value = "Roy.Tong @elliwoodtong authorized canary reply"
        got = receipt_audit.live_verify(record, browser=browser)
        self.assertEqual("verified", got["status"])


if __name__ == "__main__":
    unittest.main()
