#!/usr/bin/env python3
"""browser-attach 统一执行入口与回执测试；全部替身，不连接浏览器、不发送。

适配器与回执用例可模拟 browser_write 或授权门，以隔离日志和回执映射；
这些测试不证明相应平台允许写。真实平台边界由平台门禁与队列拒绝用例覆盖。
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import action_runner, browser_write, operations_log
from core.queue import lint_queue
from core.queue_runner import QueueRunError, run_queue


def identity(platform="x", handle="owner"):
    return {
        "enforced": True,
        "mode": "cdp_observed",
        "observed_platform_identity": "%s:%s" % (platform, handle),
        "handle": handle,
        "identity_proof_ref": "browser-attach:11@now",
        "browser_context": "webbridge-session:test",
        "observed_at": "2026-11-21T08:00:00+08:00",
    }


class ActionRunnerAdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.policy = self.data / "policies" / "owner-attested-x.json"
        self.policy.parent.mkdir(parents=True)
        self.policy.write_text('{"route":"owner_attested_session"}\n', encoding="utf-8")

    def test_dry_run_skips_cdp_and_plan_precheck_but_writes_unified_log(self):
        returned = {"dry": True, "ref": "ba:dry", "identity": identity()}
        with mock.patch("core.browser_write.execute", return_value=returned) as execute, \
                mock.patch.object(action_runner, "precheck") as precheck, \
                mock.patch.object(action_runner, "gate_account_identity") as gate, \
                mock.patch.object(action_runner, "Ledger") as ledger:
            rec = action_runner.run_action(
                channel="x", action="publish", data_dir=self.data, campaign="c1",
                text="hello", go=False, transport="browser-attach", log=lambda *_: None)
        self.assertEqual(rec["result"], "dry-run")
        self.assertEqual(rec["transport"], "browser-attach")
        self.assertEqual(rec["mode"], "owner_attested_session")
        precheck.assert_not_called()
        gate.assert_not_called()
        ledger.assert_not_called()
        execute.assert_called_once()
        rows = operations_log.OperationsLog(self.data).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"], "dry-run")
        self.assertEqual(rows[0]["approval_ref"], "owner_attested_session:x")
        self.assertEqual(rows[0]["policy_sha256"], operations_log.file_sha256(self.policy))
        self.assertEqual(rows[0]["browser_context_ref"], "webbridge-session:test")

    def test_committed_receipt_and_identity_flow_into_operations_log(self):
        returned = {
            "ok": True, "ledger_result": "committed", "ref": "ba:x:1",
            "identity": identity(),
            "receipt": {"id": "123", "url": "https://x.com/owner/status/123"},
        }
        with mock.patch("core.browser_write.execute", return_value=returned), \
                mock.patch.object(action_runner, "precheck") as precheck, \
                mock.patch.object(action_runner, "gate_account_identity") as gate, \
                mock.patch.object(action_runner, "Ledger") as ledger:
            rec = action_runner.run_action(
                channel="x", action="reply", data_dir=self.data, campaign="c2",
                target="https://x.com/a/status/1", text="answer", go=True,
                transport="browser-attach", operator="cycle:test", run_id="run-1",
                queue_item_id="item-1", log=lambda *_: None)
        self.assertEqual(rec["result"], "committed")
        precheck.assert_not_called()
        gate.assert_not_called()
        ledger.assert_not_called()
        row = operations_log.OperationsLog(self.data).all()[-1]
        self.assertEqual(row["operation_id"], "ba:x:1")
        self.assertEqual(row["operator"], "cycle:test")
        self.assertEqual(row["run_id"], "run-1")
        self.assertEqual(row["queue_item_id"], "item-1")
        self.assertEqual(row["account"]["platform_user_id"], "x:owner")
        self.assertEqual(row["account"]["handle"], "owner")
        self.assertEqual(row["receipt"]["id"], "123")
        self.assertEqual(row["receipt"]["url"], "https://x.com/owner/status/123")
        self.assertIsNone(row["receipt"]["readback_sha256"])

    def test_real_success_without_ledger_result_is_indeterminate(self):
        returned = {"ok": True, "result": "success", "ref": "ba:x:unknown",
                    "identity": identity()}
        with mock.patch("core.browser_write.execute", return_value=returned):
            rec = action_runner.run_action(
                channel="x", action="publish", data_dir=self.data, campaign="c3",
                text="hello", go=True, transport="browser-attach", log=lambda *_: None)
        self.assertEqual(rec["result"], "indeterminate")
        self.assertFalse(rec["ok"])
        self.assertIn("ledger_result", rec["error"])
        self.assertEqual(operations_log.OperationsLog(self.data).all()[-1]["result"],
                         "indeterminate")

    def test_idempotent_skip_is_not_a_send_or_failure(self):
        returned = {"ok": True, "skipped": True, "reason": "already_done", "ref": "ba:skip"}
        with mock.patch("core.browser_write.execute", return_value=returned):
            rec = action_runner.run_action(
                channel="x", action="reply", data_dir=self.data, campaign="c4",
                target="https://x.com/a/status/1", text="answer", go=True,
                transport="browser-attach", log=lambda *_: None)
        self.assertEqual(rec["result"], "skipped")
        self.assertEqual(operations_log.OperationsLog(self.data).all()[-1]["result"], "skipped")

    def test_routine_policy_cannot_be_mixed_with_owner_attested_route(self):
        with mock.patch("core.browser_write.execute") as execute:
            with self.assertRaises(action_runner.ActionError):
                action_runner.run_action(
                    channel="x", action="publish", data_dir=self.data, campaign="c5",
                    text="hello", go=True, routine=True, transport="browser-attach",
                    log=lambda *_: None)
        execute.assert_not_called()


class BrowserAttachQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.queue_path = self.data / "c-queue.json"
        self.queue_path.write_text(json.dumps({
            "campaign": "c-queue", "channel": "discord", "action": "publish",
            "transport": "browser-attach",
            "items": [{"id": "i1", "target": "1573250833871536161", "body": "hello", "actor": "alice"}],
        }, ensure_ascii=False), encoding="utf-8")

    def test_queue_inherits_transport_and_skips_generic_prechecks(self):
        with mock.patch("core.queue_runner.run_action", return_value={"result": "dry-run"}) as run, \
                mock.patch("core.queue_runner.precheck") as precheck, \
                mock.patch("core.queue_runner.gate_account_identity") as gate, \
                mock.patch("core.queue_runner.Throttle") as throttle:
            rows = run_queue(self.queue_path, data_dir=self.data, go=False,
                             operator="queue:test", run_id="run-q", log=lambda *_: None)
        self.assertEqual(rows[0]["result"], "dry-run")
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["transport"], "browser-attach")
        self.assertEqual(kwargs["actor"], "alice")
        self.assertEqual(kwargs["queue_item_id"], "i1")
        precheck.assert_not_called()
        gate.assert_not_called()
        throttle.assert_not_called()

    def test_queue_rejects_routine_mix_and_transport_conflict(self):
        with self.assertRaises(QueueRunError):
            run_queue(self.queue_path, data_dir=self.data, routine=True,
                      log=lambda *_: None)
        with self.assertRaises(QueueRunError):
            run_queue(self.queue_path, data_dir=self.data, transport="",
                      log=lambda *_: None)

    def _write_two_item_queue(self):
        self.queue_path.write_text(json.dumps({
            "campaign": "c-queue", "channel": "discord", "action": "publish",
            "transport": "browser-attach",
            "items": [
                {"id": "i1", "target": "1573250833871536161", "body": "hello", "actor": "alice"},
                {"id": "i2", "target": "1573251023500476427", "body": "again", "actor": "alice"},
            ],
        }, ensure_ascii=False), encoding="utf-8")

    def test_paused_channel_defers_instead_of_queue_error(self):
        self._write_two_item_queue()
        blocked = browser_write.ActionBlocked("paused", "test pause")
        with mock.patch("core.queue_runner.run_action", side_effect=blocked) as run:
            rows = run_queue(self.queue_path, data_dir=self.data, go=True,
                             log=lambda *_: None)
        self.assertEqual([r["result"] for r in rows], ["deferred", "deferred"])
        self.assertIn("paused", rows[0]["detail"])
        self.assertTrue(rows[0]["next"])
        self.assertIn("渠道已停", rows[1]["detail"])
        self.assertEqual(run.call_count, 1)

    def test_platform_risk_signal_blocks_channel_and_defers_rest(self):
        self._write_two_item_queue()
        stop = browser_write.StopChannel("discord", "challenge page")
        with mock.patch("core.queue_runner.run_action", side_effect=stop):
            rows = run_queue(self.queue_path, data_dir=self.data, go=True,
                             log=lambda *_: None)
        self.assertEqual([r["result"] for r in rows], ["blocked", "deferred"])
        self.assertIn("challenge page", rows[0]["detail"])
        self.assertIn("风控", rows[0]["next"])

    def test_item_level_rejection_continues_with_next_item(self):
        self._write_two_item_queue()
        blocked = browser_write.ActionBlocked("suppressed", "target in suppression list")
        with mock.patch("core.queue_runner.run_action",
                        side_effect=[blocked, {"result": "committed"}]) as run:
            rows = run_queue(self.queue_path, data_dir=self.data, go=True,
                             log=lambda *_: None)
        self.assertEqual([r["result"] for r in rows], ["rejected", "committed"])
        self.assertIn("suppressed", rows[0]["detail"])
        self.assertTrue(rows[0]["next"])
        self.assertEqual(run.call_count, 2)

    def test_x_queue_is_refused_before_transport(self):
        self.queue_path.write_text(json.dumps({
            "campaign": "x-denied", "channel": "x", "action": "publish",
            "transport": "browser-attach",
            "items": [{"id": "i1", "target": "", "body": "synthetic"}],
        }), encoding="utf-8")
        with mock.patch("core.queue_runner.run_action") as run:
            with self.assertRaises(QueueRunError):
                run_queue(self.queue_path, data_dir=self.data, go=True, log=lambda *_: None)
        run.assert_not_called()

    def test_queue_lint_rejects_unknown_transport(self):
        queue = {"campaign": "c", "channel": "x", "action": "publish",
                 "transport": "magic", "items": [{"id": "i", "body": "hello"}]}
        self.assertTrue(any("未知队列传输" in row for row in lint_queue(queue)))


class ReceiptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.attach = mock.Mock()
        self.attach.ensure_tab.return_value = 11
        self.attach.probe_visibility.return_value = "visible"

    def _patches(self, platform="x"):
        self.attach.observe_identity.return_value = identity(platform)
        return mock.patch.object(browser_write, "BrowserAttach", return_value=self.attach), \
            mock.patch.object(browser_write, "require_owner_attested_browser_write",
                              return_value={"route": "test"}), \
            mock.patch("core.throttle.Throttle.reserve", return_value=None)

    def test_x_status_id_becomes_permalink_without_claiming_full_text_hash(self):
        attach, auth, quota = self._patches("x")
        with attach, auth, quota, \
                mock.patch.object(browser_write.x_engine, "reply_native",
                                  return_value={"ok": True}), \
                mock.patch.object(browser_write.x_engine, "readback",
                                  side_effect=lambda _tab, _handle, manifest: {
                                      manifest[0]["key"]: "1234567890"}):
            rec = browser_write.execute(
                "x", "reply", self.data, text="answer",
                target="https://x.com/a/status/1", go=True, log=lambda *_: None)
        self.assertEqual(rec["ledger_result"], "committed")
        self.assertEqual(rec["receipt"], {
            "id": "1234567890", "url": "https://x.com/owner/status/1234567890"})
        self.assertNotIn("readback_sha256", rec["receipt"])

    def test_github_receipt_uses_verified_url(self):
        attach, auth, quota = self._patches("github")
        with attach, auth, quota, \
                mock.patch.object(browser_write.gh_post, "post", return_value={"ok": True}), \
                mock.patch.object(browser_write.gh_post, "verify", return_value={
                    "ok": True, "url": "https://github.com/o/r/issues/1#issuecomment-2"}):
            rec = browser_write.execute(
                "github", "reply", self.data, text="answer", target="o/r#1",
                go=True, log=lambda *_: None)
        self.assertEqual(rec["receipt"]["url"],
                         "https://github.com/o/r/issues/1#issuecomment-2")

    def test_discord_receipt_uses_real_server_id_without_invented_url(self):
        attach, auth, quota = self._patches("discord")
        with attach, auth, quota, \
                mock.patch.object(browser_write.discord_engine, "post", return_value={
                    "ok": True, "serverId": "998877", "author": "owner",
                    "readback": {"lastId": "998877"}}):
            rec = browser_write.execute(
                "discord", "publish", self.data, text="hello", target="12345",
                go=True, log=lambda *_: None)
        self.assertEqual(rec["receipt"], {"id": "998877"})
        self.assertNotIn("url", rec["receipt"])


class CliSessionTest(unittest.TestCase):
    def tearDown(self):
        from core.bridge import wb
        wb.set_session("growovo")

    def test_single_action_inherits_configured_bridge_session(self):
        import mkt
        from core.bridge import wb

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "policies").mkdir()
            (data / "policies" / "engagement-cycle.json").write_text(
                json.dumps({"bridge": {"session": "ovo-night-run"}}), encoding="utf-8")
            args = mkt.build_parser().parse_args([
                "action", "x", "publish", "--text", "hello",
                "--transport", "browser-attach",
            ])
            wb.set_session("growovo")
            with mock.patch.object(mkt, "DATA", data), \
                    mock.patch("core.action_runner.run_action",
                               return_value={"result": "dry-run"}), \
                    redirect_stdout(io.StringIO()):
                code = args.fn(args)
            self.assertEqual(code, 0)
            self.assertEqual(wb.SESSION, "ovo-night-run")


class CliParserTest(unittest.TestCase):
    def test_cycle_defaults_leave_ten_minutes_before_next_wave(self):
        import mkt
        parser = mkt.build_parser()
        for action in ("plan", "run"):
            args = parser.parse_args(["cycle", action])
            self.assertEqual(args.budget_sec, 3000)

    def test_action_and_queue_accept_explicit_browser_attach(self):
        import mkt
        parser = mkt.build_parser()
        action = parser.parse_args([
            "action", "discord", "publish", "--target", "123", "--text", "hello",
            "--transport", "browser-attach"])
        self.assertEqual(action.channel, "discord")
        self.assertEqual(action.transport, "browser-attach")
        queue = parser.parse_args(["queue", "run", "c.json", "--transport", "browser-attach"])
        self.assertEqual(queue.transport, "browser-attach")


if __name__ == "__main__":
    unittest.main()
