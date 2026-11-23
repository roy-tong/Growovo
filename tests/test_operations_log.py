#!/usr/bin/env python3
"""统一操作日志（评审 2026-11-17 §4.2 最低字段清单）。

每次真实/干跑动作必须 append-only 落一条完整记录：operation_id/run_id/
campaign/queue_item_id、channel/action/target/content_sha256、browser_context_ref、
account.platform_user_id/handle/observed_at/proof_ref、operator/approval_ref/
policy_sha256、started_at/finished_at/result、receipt.id/url/readback_sha256、
artifacts[].path/sha256、error.code/retryable/recovery_ref。

字段缺失要 fail loud（OperationsLogError），不能静默落半条记录；账号切换
（观测身份变化）自动记录，且两条记录各自携带自己的账号（不串号）。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import operations_log
from core.action_runner import run_action
from core.queue_runner import run_queue


def _full_entry(**over):
    base = dict(
        operation_id="op-accept-0001", run_id="g-accept", campaign="accept-campaign",
        queue_item_id="q1", channel="x", action="reply",
        target="https://x.com/a/status/1",
        content_sha256=operations_log.content_sha256("验收回复内容"),
        browser_context_ref="cdp:http://127.0.0.1:9223",
        account={"platform_user_id": "1234567890", "handle": "accept_bot",
                 "observed_at": "2026-11-17T09:00:00+00:00",
                 "proof_ref": "x:2026-11-17T09:00:00+00:00"},
        operator="acceptance-script", approval_ref="routine-policy",
        policy_sha256="a" * 64,
        started_at="2026-11-17T09:00:01+00:00",
        finished_at="2026-11-17T09:00:02+00:00",
        result="committed",
        receipt={"id": "1790000000000000000",
                 "url": "https://x.com/accept_bot/status/1790000000000000000",
                 "readback_sha256": operations_log.content_sha256("验收回复内容")},
        artifacts=[], error=None,
    )
    base.update(over)
    return base


class MinimumFieldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_append_writes_every_minimum_field(self):
        entry = operations_log.OperationsLog(self.dir).append(_full_entry())
        rows = operations_log.OperationsLog(self.dir).all()
        self.assertEqual(1, len(rows))
        for field in operations_log.MINIMUM_FIELDS:
            self.assertIn(field, rows[0], f"操作日志缺最低字段 {field}")
        self.assertEqual(entry["operation_id"], rows[0]["operation_id"])
        # append-only：第二条不覆盖第一条
        second = _full_entry(operation_id="op-accept-0002",
                             content_sha256=operations_log.content_sha256("第二条"),
                             receipt={"id": "", "url": "", "readback_sha256": ""})
        operations_log.OperationsLog(self.dir).append(second)
        self.assertEqual(2, len(operations_log.OperationsLog(self.dir).all()))

    def test_missing_optional_evidence_is_explicit_null(self):
        entry = operations_log.OperationsLog(self.dir).append(_full_entry(
            run_id=None, campaign=None, queue_item_id=None,
            browser_context_ref=None, approval_ref=None, policy_sha256=None,
            receipt={}, result="dry-run",
            account={"platform_user_id": "1234567890", "handle": None,
                     "observed_at": None, "proof_ref": None}))
        for field in ("run_id", "campaign", "queue_item_id", "browser_context_ref",
                      "approval_ref", "policy_sha256"):
            self.assertIsNone(entry[field], field)
        for field in ("handle", "observed_at", "proof_ref", "authorized_account"):
            self.assertIsNone(entry["account"][field], field)
        for field in ("id", "url", "readback_sha256"):
            self.assertIsNone(entry["receipt"][field], field)

    def test_secret_fields_are_rejected_without_writing(self):
        log = operations_log.OperationsLog(self.dir)
        with self.assertRaises(operations_log.OperationsLogError):
            log.append(_full_entry(cookie="must-not-be-written"))
        self.assertEqual([], log.all())
        self.assertIsNone(operations_log.browser_context_ref(env={
            "MKT_CDP_IDENTITY_URL": "http://127.0.0.1:9223/?token=must-not-be-written",
        }))

    def test_missing_content_hash_fails_loud_for_committed_write(self):
        log = operations_log.OperationsLog(self.dir)
        with self.assertRaises(operations_log.OperationsLogError):
            log.append(_full_entry(content_sha256=""))
        # 失败的写入不落半条记录
        self.assertEqual(0, len(log.all()))

    def test_artifact_hash_must_match_file(self):
        art = self.dir / "reply-1.json"
        art.write_text('{"record": {}}', encoding="utf-8")
        good = operations_log.OperationsLog(self.dir).append(
            _full_entry(artifacts=[{"path": str(art),
                                    "sha256": operations_log.file_sha256(art)}]))
        self.assertEqual(operations_log.file_sha256(art),
                         good["artifacts"][0]["sha256"])
        with self.assertRaises(operations_log.OperationsLogError):
            operations_log.OperationsLog(self.dir).append(
                _full_entry(operation_id="op-x",
                            artifacts=[{"path": str(art), "sha256": "0" * 64}]))

    def test_x_committed_requires_observed_account(self):
        with self.assertRaises(operations_log.OperationsLogError):
            operations_log.OperationsLog(self.dir).append(
                _full_entry(account={}))

    def test_error_block_shape(self):
        entry = operations_log.OperationsLog(self.dir).append(_full_entry(
            result="failed",
            error={"code": "vendor_exit_5", "retryable": False,
                   "recovery_ref": "mkt grow incident <run> --list"}))
        self.assertEqual("vendor_exit_5", entry["error"]["code"])
        self.assertFalse(entry["error"]["retryable"])

    def test_target_level_terminal_states_are_auditable_and_not_retryable(self):
        expected = {
            "target_rejected": "target_rejected",
            "unavailable_or_unrenderable": "target_unavailable",
        }
        for state, code in expected.items():
            with self.subTest(state=state):
                entry = operations_log.record_action(
                    self.dir,
                    {"result": state, "ref": "op-" + state,
                     "transport": "browser-attach"},
                    channel="github", action="reply", target="owner/repo#1",
                    text="reply body", campaign="night-test", go=True)
                self.assertEqual(state, entry["result"])
                self.assertEqual(code, entry["error"]["code"])
                self.assertFalse(entry["error"]["retryable"])
                self.assertIn("隔离", entry["error"]["recovery_ref"])
                self.assertEqual(state,
                                 operations_log.OperationsLog(self.dir).all()[-1]["result"])


class AccountSwitchTests(unittest.TestCase):
    """账号切换自动记录；每条记录只携带自己的观测账号。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.log = operations_log.OperationsLog(self.dir)

    def _account(self, uid, handle):
        return {"platform_user_id": uid, "handle": handle,
                "observed_at": "2026-11-17T09:00:00+00:00",
                "proof_ref": f"x:{uid}"}

    def test_switch_is_recorded_and_identities_do_not_cross(self):
        first = self.log.append(_full_entry(
            account=self._account("111", "alpha")))
        second = self.log.append(_full_entry(
            operation_id="op-2", account=self._account("222", "beta")))
        self.assertNotIn("switched_from", first["account"])
        self.assertEqual("111", first["account"]["platform_user_id"])
        self.assertEqual("222", second["account"]["platform_user_id"])
        self.assertEqual("111",
                         second["account"]["switched_from"]["platform_user_id"])
        self.assertEqual("alpha", second["account"]["switched_from"]["handle"])
        # 落盘后仍可审计
        rows = self.log.all()
        self.assertEqual("222", rows[-1]["account"]["platform_user_id"])
        self.assertIn("switched_from", rows[-1]["account"])

    def test_same_account_no_switch_marker(self):
        self.log.append(_full_entry(account=self._account("111", "alpha")))
        second = self.log.append(_full_entry(
            operation_id="op-2", account=self._account("111", "alpha")))
        self.assertNotIn("switched_from", second["account"])


class _OfflineVendor:
    """离线 vendor 替身：只落账本行，不碰浏览器/网络。"""

    def __init__(self, result="dry-run"):
        self.result = result

    def __call__(self, action, args, *, go=False, campaign="", ledger=None,
                 log=print, data_dir=None, identity=None, **kw):
        if ledger is not None:
            ledger.append(channel="x", transport="cdp", action=action,
                          campaign=campaign, target=args[1] if len(args) > 1 else "?",
                          result=self.result, ref="")
        return 0


class RunActionIntegrationTests(unittest.TestCase):
    """run_action（单动作/队列共用路径）必须落操作日志。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_single_action_writes_operations_entry(self):
        with patch("channels.x.adapter.run", _OfflineVendor("dry-run")):
            rec = run_action(channel="x", action="reply", data_dir=self.dir,
                             campaign="acc1", target="https://x.com/a/status/1",
                             text="验收回复内容", go=False, log=lambda *_: None)
        self.assertEqual("dry-run", rec["result"])
        rows = operations_log.OperationsLog(self.dir).all()
        self.assertEqual(1, len(rows), "run_action 后必须有操作日志")
        entry = rows[0]
        for field in operations_log.MINIMUM_FIELDS:
            self.assertIn(field, entry)
        self.assertEqual(operations_log.content_sha256("验收回复内容"),
                         entry["content_sha256"])
        self.assertEqual("https://x.com/a/status/1", entry["target"])
        self.assertTrue(entry["operator"])

    def test_queue_action_carries_queue_item_identity(self):
        qdir = self.dir / "queues"
        qdir.mkdir(parents=True)
        (qdir / "accq.json").write_text(json.dumps({
            "campaign": "accq", "channel": "x", "action": "reply",
            "items": [{"id": "i1", "target": "https://x.com/a/status/2",
                       "body": "队列验收内容"}],
        }, ensure_ascii=False), encoding="utf-8")
        with patch("channels.x.adapter.run", _OfflineVendor("dry-run")):
            rows = run_queue(qdir / "accq.json", data_dir=self.dir,
                             go=False, run_id="run-q", log=lambda *_: None)
        self.assertEqual(1, len(rows))
        ops_rows = operations_log.OperationsLog(self.dir).all()
        self.assertEqual(1, len(ops_rows))
        self.assertEqual("i1", ops_rows[0]["queue_item_id"])
        self.assertEqual("run-q", ops_rows[0]["run_id"])
        self.assertEqual("accq", ops_rows[0]["campaign"])
        self.assertTrue(ops_rows[0]["operator"].startswith("queue"),
                        "队列入口的操作者要可识别")
        self.assertEqual(operations_log.content_sha256("队列验收内容"),
                         ops_rows[0]["content_sha256"])

    def test_email_queue_delegates_operations_log_to_send_queue(self):
        """email 的操作日志由 send_queue 统一写，队列入口不得再补一条。"""
        qdir = self.dir / "queues"
        qdir.mkdir(parents=True)
        (qdir / "acce.json").write_text(json.dumps({
            "campaign": "acce", "channel": "email", "action": "message",
            "items": [{"id": "e1", "target": "a@example.com",
                       "subject": "s", "body": "b"}],
        }, ensure_ascii=False), encoding="utf-8")
        fake = [{"channel": "email", "action": "message", "result": "dry-run",
                 "target": "a@example.com", "ref": "e1", "detail": ""}]
        with patch("channels.email_smtp.send_queue", return_value=fake) as sender:
            run_queue(qdir / "acce.json", data_dir=self.dir, go=False,
                      log=lambda *_: None, operator="queue:acce", run_id="run-e")
        kwargs = sender.call_args.kwargs
        self.assertEqual("queue:acce", kwargs["operator"])
        self.assertEqual("run-e", kwargs["run_id"])
        self.assertEqual([], operations_log.OperationsLog(self.dir).all(),
                         "send_queue 已负责落日志；队列入口重复写会让一次动作像两次")

    def test_cli_x_adhoc_path_records_operations(self):
        """mkt x 直通路径（不经过 run_action 的队列形态）同样落操作日志。"""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import mkt
        old_data = mkt.DATA
        mkt.DATA = self.dir
        self.addCleanup(setattr, mkt, "DATA", old_data)

        class Args:
            action = "reply"
            go = False
            adhoc = True
            campaign = "accx"
            account = ""
            extra = []
            file = None
            thread = False
            tweet_url = "https://x.com/a/status/3"
            text = "CLI 直通验收内容"

        with patch("channels.x.adapter.run", _OfflineVendor("dry-run")) as runner:
            rc = mkt.cmd_x(Args())
        self.assertEqual(0, rc)
        ops_rows = operations_log.OperationsLog(self.dir).all()
        self.assertEqual(1, len(ops_rows), "mkt x 入口必须落操作日志")
        self.assertEqual(operations_log.content_sha256("CLI 直通验收内容"),
                         ops_rows[0]["content_sha256"])
        self.assertTrue(ops_rows[0]["operator"].startswith("cli"))


class AccountLogCliTests(unittest.TestCase):
    def test_account_log_reads_unified_operations_and_actual_account_proof(self):
        import contextlib
        import io
        from types import SimpleNamespace
        import mkt

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            expected = operations_log.OperationsLog(data).append(_full_entry(
                operation_id="op-cli-log", campaign="cli-log-campaign",
                account={"platform_user_id": "1234567890", "handle": "actual-a",
                         "observed_at": "2026-11-17T09:00:00+00:00",
                         "proof_ref": "offline:proof:1234567890"}))
            output = io.StringIO()
            args = SimpleNamespace(action="log", limit=20, json=True)
            with patch.object(mkt, "DATA", data), contextlib.redirect_stdout(output):
                rc = mkt.cmd_account(args)
            self.assertEqual(0, rc)
            rows = json.loads(output.getvalue())
            self.assertEqual(1, len(rows))
            self.assertEqual(expected["operation_id"], rows[0]["operation_id"])
            self.assertEqual("actual-a", rows[0]["account"]["handle"])
            self.assertEqual("offline:proof:1234567890",
                             rows[0]["account"]["proof_ref"])
            self.assertEqual("cli-log-campaign", rows[0]["campaign"])


if __name__ == "__main__":
    unittest.main()
