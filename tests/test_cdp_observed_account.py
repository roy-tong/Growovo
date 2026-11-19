try:  # discover -s tests puts tests/ on sys.path
    from support_compliance import bypass_x_compliance
except ImportError:  # python -m unittest tests.<module>
    from tests.support_compliance import bypass_x_compliance
#!/usr/bin/env python3
"""CDP-observed account mode: any logged-in browser account is allowed, but
the observation is mandatory and recorded; no pre-bound identity required."""
import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import account_identity, cdp_identity, operations_log
from core import action_runner
from unittest import mock
from core.account_identity import AccountIdentityProof, IdentityBlocked
from core.execution_policy import authorize
from core.ledger import Ledger
from core.queue_runner import QueueRunError, run_queue


def _install_fake_x_vendor(root: Path) -> Path:
    """本地进程替身：从注入身份状态生成回执；无浏览器、无网络、无真实发送。"""
    vendor = root / "fake-x-vendor"
    (vendor / "logs").mkdir(parents=True)
    (vendor / "reply_x.py").write_text(textwrap.dedent("""
        import hashlib
        import json
        import pathlib
        import sys
        import time

        vendor = pathlib.Path(__file__).resolve().parent
        argv = sys.argv[1:]
        target = argv[argv.index('--tweet-url') + 1]
        text = argv[argv.index('--text') + 1]
        state = json.loads((vendor / 'current-account.json').read_text(encoding='utf-8'))
        receipt_id = '179' + state['user_id']
        receipt_url = 'https://x.com/%s/status/%s' % (state['handle'], receipt_id)
        payload = {'record': {
            'url': target, 'posted': True, 'submitted': True,
            'readback': {'id': receipt_id, 'url': receipt_url,
                         'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()},
        }}
        result = vendor / 'logs' / ('reply-%d.json' % time.time_ns())
        result.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        with (vendor / 'called.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'target': target, 'account': state},
                                    ensure_ascii=False) + '\\n')
        sys.exit(0)
    """), encoding="utf-8")
    return vendor


class ObservedAccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.policy = authorize(
            self.dir, by="owner", actions=["x:reply"], days=30,
            observe_identity=True)

    def test_policy_marks_observed_mode(self):
        self.assertEqual("cdp_observed", self.policy.get("identity_mode"))
        self.assertEqual("observed",
                         account_identity.policy_state(self.policy)["mode"])

    def test_observed_mode_has_no_legacy_identity_blocker(self):
        self.assertEqual([], account_identity.identity_blockers(
            self.dir, policy=self.policy, policy_loaded=True))

    def test_any_logged_in_account_is_allowed_and_recorded(self):
        proof = AccountIdentityProof(channel="x", user_id="uid-b",
                                     handle="another_account")
        result = account_identity.enforce_identity(
            self.dir, "x", "", proof, policy=self.policy)
        self.assertTrue(result["enforced"])
        self.assertEqual("cdp_observed", result["mode"])
        self.assertEqual("uid-b", result["observed_platform_identity"])
        self.assertEqual("another_account", result["handle"])

    def test_action_gate_forces_probe_and_returns_evidence(self):
        proof = AccountIdentityProof(channel="x", user_id="uid-c", handle="browser_user")
        with mock.patch.object(account_identity, "observe", return_value=proof) as observe, \
                mock.patch.object(cdp_identity, "assert_identity_endpoint_matches"):
            evidence = action_runner.gate_account_identity("x", self.dir, "")
        self.assertEqual("cdp_observed", evidence["mode"])
        self.assertEqual("browser_user", evidence["handle"])
        self.assertEqual("1", observe.call_args.kwargs["env"]["MKT_CDP_IDENTITY"])

    def test_observation_is_mandatory(self):
        with self.assertRaises(IdentityBlocked) as ctx:
            account_identity.enforce_identity(self.dir, "x", "", None,
                                              policy=self.policy)
        self.assertEqual("identity_unknown", ctx.exception.reason_code)


class ObservedAccountVerticalChainTests(unittest.TestCase):
    """注入身份 + 假 vendor 的最小垂直链；真实门禁和 X 账本适配器不替换。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.vendor = _install_fake_x_vendor(self.dir)
        bypass_x_compliance(self)
        authorize(self.dir, by="owner", actions=["x:reply"], days=30,
                  observe_identity=True)
        account_identity.reset_reader()
        self.addCleanup(account_identity.reset_reader)
        self.queue_path = self.dir / "queues" / "switch-campaign.json"
        self.queue_path.parent.mkdir(parents=True)
        self.items = [
            {"id": "q-a", "target": "https://x.com/target/status/1",
             "body": "account A offline reply"},
            {"id": "q-b", "target": "https://x.com/target/status/2",
             "body": "account B offline reply"},
        ]
        self.queue_path.write_text(json.dumps({
            "campaign": "switch-campaign", "channel": "x", "action": "reply",
            "account": "", "items": self.items,
        }, ensure_ascii=False), encoding="utf-8")

    def _reader(self, observations):
        remaining = list(observations)

        def read(channel):
            # 队列级观测 A、第一条动作仍 A、第二条动作切到 B；之后保持最后值。
            user_id, handle = remaining.pop(0) if len(remaining) > 1 else remaining[0]
            state = {"user_id": user_id, "handle": handle}
            (self.vendor / "current-account.json").write_text(
                json.dumps(state), encoding="utf-8")
            return AccountIdentityProof(
                channel=channel, user_id=user_id, handle=handle,
                browser_context="offline:context:%s" % user_id,
                source_ref="offline:proof:%s" % user_id)

        return read

    def _patches(self):
        return (
            mock.patch("channels.x.adapter.VENDOR", self.vendor),
            mock.patch("core.session_gate.check_login",
                       return_value={"status": "logged_in"}),
            mock.patch.object(cdp_identity, "assert_identity_endpoint_matches"),
            mock.patch.dict(os.environ, {
                "MKT_CDP_IDENTITY_URL": "http://127.0.0.1:9223",
                "X_CDP_URL": "http://127.0.0.1:9223",
            }),
        )

    def _run_queue(self):
        patches = self._patches()
        with patches[0], patches[1], patches[2], patches[3]:
            return run_queue(
                self.queue_path, data_dir=self.dir, go=True, routine=True,
                operator="offline-test", run_id="run-switch",
                log=lambda *a, **k: None)

    def _vendor_calls(self):
        path = self.vendor / "called.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(
            encoding="utf-8").splitlines() if line.strip()]

    def test_two_consecutive_actions_switch_a_to_b_without_crossing_receipts(self):
        account_identity.set_reader(self._reader([
            ("100001", "account_a"), ("100001", "account_a"),
            ("100002", "account_b"),
        ]))
        records = self._run_queue()
        self.assertEqual(["committed", "committed"], [r["result"] for r in records])
        self.assertEqual(2, len(self._vendor_calls()))

        ledger_rows = Ledger(self.dir / "ledger.jsonl").all()
        ops_rows = operations_log.OperationsLog(self.dir).all()
        self.assertEqual(2, len(ledger_rows))
        self.assertEqual(2, len(ops_rows))
        expected = [("100001", "account_a"), ("100002", "account_b")]
        for index, (user_id, handle) in enumerate(expected):
            ledger_row, ops_row, item = ledger_rows[index], ops_rows[index], self.items[index]
            for row in (ledger_row, records[index]):
                self.assertEqual(user_id,
                                 row["identity"]["observed_platform_identity"])
                self.assertEqual(handle, row["identity"]["handle"])
                self.assertIn("/%s/" % handle, row["receipt"]["url"])
            self.assertEqual(user_id, ops_row["account"]["platform_user_id"])
            self.assertEqual(handle, ops_row["account"]["handle"])
            self.assertEqual("offline:proof:%s" % user_id,
                             ops_row["account"]["proof_ref"])
            self.assertEqual(ledger_row["ref"], ops_row["operation_id"])
            self.assertEqual(ledger_row["receipt"], ops_row["receipt"])
            self.assertIn("/%s/" % handle, ops_row["receipt"]["url"])
            self.assertEqual(operations_log.content_sha256(item["body"]),
                             ops_row["content_sha256"])
            self.assertEqual(operations_log.content_sha256(item["body"]),
                             ops_row["receipt"]["readback_sha256"])
            self.assertEqual(item["id"], ops_row["queue_item_id"])
            self.assertEqual("switch-campaign", ops_row["campaign"])
            self.assertEqual("run-switch", ops_row["run_id"])
            self.assertEqual("routine-policy", ops_row["approval_ref"])
            self.assertEqual(64, len(ops_row["policy_sha256"]))
            self.assertTrue(ops_row["started_at"])
            self.assertTrue(ops_row["finished_at"])
            self.assertTrue(ops_row["artifacts"], "vendor 原始回执必须有哈希引用")
        self.assertNotIn("switched_from", ops_rows[0]["account"])
        self.assertEqual("100001", ops_rows[1]["account"]["switched_from"][
            "platform_user_id"])
        self.assertNotEqual(ops_rows[0]["operation_id"], ops_rows[1]["operation_id"])

        # 重跑同一 operation intent：原账本幂等门拒绝，vendor/账本/操作日志不增加。
        with self.assertRaises(QueueRunError) as ctx:
            self._run_queue()
        self.assertTrue("幂等" in str(ctx.exception) or "防重发" in str(ctx.exception))
        self.assertEqual(2, len(self._vendor_calls()))
        self.assertEqual(2, len(Ledger(self.dir / "ledger.jsonl").all()))
        self.assertEqual(2, len(operations_log.OperationsLog(self.dir).all()))

    def test_missing_identity_fails_closed_before_vendor(self):
        account_identity.set_reader(lambda channel: None)
        patches = self._patches()
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaises(action_runner.ActionError) as ctx:
                action_runner.run_action(
                    channel="x", action="reply", data_dir=self.dir,
                    campaign="switch-campaign", target=self.items[0]["target"],
                    text=self.items[0]["body"], go=True, routine=True,
                    operator="offline-test", log=lambda *a, **k: None)
        self.assertIn("identity_unknown", str(ctx.exception))
        self.assertEqual([], self._vendor_calls(), "缺身份绝不能触达 vendor")
        self.assertEqual([], Ledger(self.dir / "ledger.jsonl").all())
        self.assertEqual([], operations_log.OperationsLog(self.dir).all())


if __name__ == "__main__":
    unittest.main()
