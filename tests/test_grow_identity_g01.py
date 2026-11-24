"""G-01 反例冻结：登录界面标记 ≠ 授权账号身份，真实发送前必须绑定实际账号。

- 授权账号身份为 x:uid-a、浏览器实际观测到 uid-b → 队列整体拒绝，零发送；
- 观测不到身份（reader 返回 None 或抛异常）→ identity_unknown；
- 观测过期（>300s）→ identity_stale；跨渠道 proof → identity_channel_mismatch；
- 只按账号名授权、无身份记录（旧式授权）→ authorization_missing_identity，
  要求重新授权后才能真实发送；
- 身份匹配 → 放行，回执记录 authorized_account 与 observed_platform_identity；
- session_gate 的 300 秒登录标记缓存不能替代身份门禁；
- 未绑定账号的 legacy default 授权不因本门禁改变行为（门禁不介入）。
本文件先于修复编写，针对「登录态即身份」的现状应当失败。
"""
import json
import tempfile
import time as _time
import unittest
from pathlib import Path
from unittest import mock

import core.account_identity as account_identity
from core import session_gate
from core.execution_policy import authorize
from core.queue_runner import QueueRunError, run_queue


def _proof(user_id, *, channel="x", age_seconds=0.0):
    observed = _time.strftime("%Y-%m-%dT%H:%M:%S",
                              _time.gmtime(_time.time() - age_seconds)) + "+00:00"
    return account_identity.AccountIdentityProof(
        channel=channel, user_id=user_id, handle=f"h-{user_id}",
        observed_at=observed, browser_context="ctx-1", source_ref="test")


def _queue_file(tmp: Path, *, account="acct-a"):
    items = [{"id": "q1-0", "target": "tweet:https://x.com/u/2026-11-15/0",
              "body": "针对该楼的具体回复，没有链接。",
              "scheduled_for": "2026-11-15"}]
    path = tmp / "queues" / "q1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"campaign": "q1", "channel": "x", "action": "reply",
         "account": account, "items": items}, ensure_ascii=False),
        encoding="utf-8")
    return path


class IdentityGateG01Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        authorize(self.dir, by="owner", actions=["x:reply"], days=30,
                  accounts=["acct-a"], identities=["acct-a=x:uid-a"],
                  require_identity=True)
        account_identity.reset_reader()
        self.addCleanup(account_identity.reset_reader)
        self.queue_path = _queue_file(self.dir)

    def _run(self):
        """执行队列，返回 (结果文本, 实际发送条数)；run_action 换成计数探针。"""
        sent = []

        def fake_run_action(**kwargs):
            sent.append(kwargs)
            return {"result": "committed", "detail": ""}

        with mock.patch("core.queue_runner.run_action",
                        side_effect=fake_run_action), \
             mock.patch("core.queue_runner.precheck",
                        lambda *a, **k: "routine-policy"):
            try:
                run_queue(self.queue_path, data_dir=self.dir, go=True,
                          routine=True, legacy_cdp=True, log=lambda *a, **k: None)
                outcome = "ok"
            except QueueRunError as exc:
                outcome = str(exc)
        return outcome, len(sent)

    # ---- 反例：身份不一致 / 缺失 / 过期 → 零发送 --------------------

    def test_browser_account_b_blocked_with_zero_sends(self):
        """授权 uid-a、浏览器实际是 uid-b：拒绝且零发送。"""
        account_identity.set_reader(lambda ch: _proof("uid-b"))
        outcome, sends = self._run()
        self.assertIn("identity_mismatch", outcome)
        self.assertEqual(0, sends)

    def test_unobservable_identity_blocks(self):
        """读不到实际身份（reader None）：identity_unknown，零发送。"""
        account_identity.set_reader(lambda ch: None)
        outcome, sends = self._run()
        self.assertIn("identity_unknown", outcome)
        self.assertEqual(0, sends)

    def test_probe_failure_is_identity_unknown(self):
        """探测器抛异常同样按身份未知处理，不得冒充或放行。"""

        def boom(channel):
            raise RuntimeError("cdp down")

        account_identity.set_reader(boom)
        outcome, sends = self._run()
        self.assertIn("identity_unknown", outcome)
        self.assertEqual(0, sends)

    def test_stale_proof_blocks(self):
        """观测超过 300s 视为过期，必须重探。"""
        account_identity.set_reader(lambda ch: _proof("uid-a", age_seconds=600))
        outcome, sends = self._run()
        self.assertIn("identity_stale", outcome)
        self.assertEqual(0, sends)

    def test_wrong_channel_proof_blocks(self):
        """gmail 的 proof 不能给 x 的发送背书。"""
        account_identity.set_reader(lambda ch: _proof("uid-a", channel="gmail"))
        outcome, sends = self._run()
        self.assertIn("identity_channel_mismatch", outcome)
        self.assertEqual(0, sends)

    def test_account_without_identity_records_requires_reauth(self):
        """只有账号名、没有身份记录的旧式授权：要求重新授权（AC H）。"""
        authorize(self.dir, by="owner", actions=["x:reply"], days=30,
                  accounts=["acct-a"])
        account_identity.set_reader(lambda ch: _proof("uid-a"))
        outcome, sends = self._run()
        self.assertIn("authorization_missing_identity", outcome)
        self.assertEqual(0, sends)

    # ---- 正例与边界 -------------------------------------------------

    def test_matching_identity_allows_and_receipt_carries_identity(self):
        """身份匹配：放行，回执记录授权账号与实际观测身份。"""
        account_identity.set_reader(lambda ch: _proof("uid-a"))
        records = []

        def fake_run_action(**kwargs):
            records.append(kwargs)
            return {"result": "committed", "detail": ""}

        with mock.patch("core.queue_runner.run_action",
                        side_effect=fake_run_action), \
             mock.patch("core.queue_runner.precheck",
                        lambda *a, **k: "routine-policy"):
            results = run_queue(self.queue_path, data_dir=self.dir, go=True,
                                routine=True, legacy_cdp=True, log=lambda *a, **k: None)
        self.assertEqual(1, len(records))  # 真的执行了
        identity = results[0].get("identity")
        self.assertIsNotNone(identity)
        self.assertEqual("acct-a", identity["authorized_account"])
        self.assertEqual("uid-a", identity["observed_platform_identity"])

    def test_login_marker_cache_does_not_satisfy_identity_gate(self):
        """session_gate 缓存的「已登录」标记不能充当身份证明。"""
        session_gate._CACHE["x"] = (
            _time.monotonic(),
            {"channel": "x", "status": session_gate.LOGGED_IN})
        self.addCleanup(session_gate.clear_cache)
        account_identity.set_reader(lambda ch: None)
        outcome, sends = self._run()
        self.assertIn("identity_unknown", outcome)
        self.assertEqual(0, sends)

    def test_legacy_unbound_policy_blocks_real_send(self):
        """AC-H：旧授权没声明具体平台账号 → 真实发送拒绝，仍不探测身份。

        G-01b 收紧了 G-01 的默认：此前未绑定授权完全不经过门禁，等于新门禁
        只保护新授权。现在问题归因在授权侧（缺 require_identity / accounts /
        account_identities），所以先拒绝、再谈观测。
        """
        authorize(self.dir, by="owner", actions=["x:reply"], days=30)
        self.queue_path = _queue_file(self.dir, account="")

        def must_not_probe(channel):
            raise AssertionError("门禁不应在未绑定授权下探测身份")

        account_identity.set_reader(must_not_probe)
        outcome, sends = self._run()
        self.assertIn("AUTH_REQUIRED", outcome)
        self.assertIn("authorization_not_identity_bound", outcome)
        self.assertIn("migrate-identity", outcome)
        self.assertEqual(0, sends)

    def test_legacy_403_hint_does_not_claim_logged_out(self):
        probe = {"reason": "identity_missing",
                 "detail": "verify_credentials HTTP 403；twid cookie 无数字 ID",
                 "endpoint": "http://127.0.0.1:9222"}
        with mock.patch("core.cdp_identity.enabled", return_value=True), \
                mock.patch("core.account_identity.observe", return_value=None), \
                mock.patch("core.account_identity.last_probe", return_value=probe):
            got = account_identity.identity_status(self.dir, env={"MKT_CDP_IDENTITY": "1"})
        hint = got["next_required_action"]
        self.assertIn("browser-attach", hint)
        self.assertIn("403 不能单独证明未登录", hint)
        self.assertNotIn("确认已在调试浏览器里登录", hint)

    def test_action_path_gate_blocks_mismatch(self):
        """单动作路径（mkt x / MCP 共用的 run_action）同样被门禁拦截。"""
        from core.action_runner import ActionError, gate_account_identity
        account_identity.set_reader(lambda ch: _proof("uid-b"))
        with self.assertRaises(ActionError) as ctx:
            gate_account_identity("x", self.dir, "acct-a")
        self.assertIn("identity_mismatch", str(ctx.exception))


class IdentityRealPathIntegrationTests(unittest.TestCase):
    """不 mock run_action 的真实路径集成（复审问题 1/2 的回归）。

    - 队列：require_identity 授权 + 观测身份匹配 → 逐条执行必须真的跑通。
      此前队列级门禁通过后，逐条 precheck 因缺 platform_user_id 被人审
      门禁全拒（被 mock 掩盖）。
    - CLI adhoc（mkt x --go）：绑定身份授权下必须先过身份门禁，未绑定
      授权不介入。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        authorize(self.dir, by="owner", actions=["x:reply"], days=30,
                  accounts=["acct-a"], identities=["acct-a=x:uid-a"],
                  require_identity=True)
        account_identity.reset_reader()
        self.addCleanup(account_identity.reset_reader)
        self.queue_path = _queue_file(self.dir)
        login = mock.patch("core.session_gate.check_login",
                           return_value={"status": "logged_in"})
        login.start()
        self.addCleanup(login.stop)

    def _vendor(self, calls):
        def fake_run(*args, **kwargs):
            calls.append((args, kwargs))
            return 0
        return fake_run

    def _x_args(self, *, account="acct-a"):
        from types import SimpleNamespace
        return SimpleNamespace(action="reply", extra=[], file=None,
                               thread=False, go=True, adhoc=True,
                               campaign="c1", account=account,
                               handle="", record=False,
                               legacy_cdp=True, cdp_url="",
                               text=None, tweet_url=None)

    def test_require_identity_queue_real_path_commits(self):
        """复审问题 1 回归：require_identity 下队列真实路径可发送且回执带身份。"""
        account_identity.set_reader(lambda ch: _proof("uid-a"))
        calls = []
        with mock.patch("channels.x.adapter.run",
                        side_effect=self._vendor(calls)):
            results = run_queue(self.queue_path, data_dir=self.dir, go=True,
                                routine=True, legacy_cdp=True, log=lambda *a, **k: None)
        self.assertEqual(1, len(calls))  # vendor 真实到达（不是 mock 断言）
        self.assertEqual("committed", results[0]["result"])
        self.assertEqual("acct-a",
                         results[0]["identity"]["authorized_account"])
        self.assertEqual("uid-a",
                         results[0]["identity"]["observed_platform_identity"])

    def test_cli_adhoc_go_blocked_when_identity_unknown(self):
        """复审问题 2 回归：mkt x --go --adhoc 不得绕过身份门禁。"""
        import mkt
        account_identity.set_reader(lambda ch: None)
        calls = []
        with mock.patch.object(mkt, "DATA", self.dir), \
             mock.patch("channels.x.adapter.run",
                        side_effect=self._vendor(calls)):
            rc = mkt.cmd_x(self._x_args())
        self.assertEqual(1, rc)
        self.assertEqual(0, len(calls))  # vendor 未到达

    def test_cli_adhoc_go_allowed_with_matching_identity(self):
        import mkt
        account_identity.set_reader(lambda ch: _proof("uid-a"))
        calls = []
        with mock.patch.object(mkt, "DATA", self.dir), \
             mock.patch("channels.x.adapter.run",
                        side_effect=self._vendor(calls)):
            rc = mkt.cmd_x(self._x_args())
        self.assertEqual(0, rc)
        self.assertEqual(1, len(calls))

    def test_cli_adhoc_unbound_policy_is_refused(self):
        """AC-H 在 CLI 直通路径同样生效：旧授权 → 拒绝，vendor 不被调用。"""
        import mkt
        authorize(self.dir, by="owner", actions=["x:reply"], days=30)  # 无绑定

        def must_not_probe(channel):
            raise AssertionError("未绑定授权不应探测身份")

        account_identity.set_reader(must_not_probe)
        calls = []
        with mock.patch.object(mkt, "DATA", self.dir), \
             mock.patch("channels.x.adapter.run",
                        side_effect=self._vendor(calls)):
            rc = mkt.cmd_x(self._x_args(account=""))
        self.assertEqual(1, rc)
        self.assertEqual(0, len(calls))


if __name__ == "__main__":
    unittest.main()
