"""G-10 冻结：身份探测与真实执行的 CDP 端点/上下文必须绑定。

先于修复编写。修复前身份读取器按
``MKT_CDP_IDENTITY_URL > MKT_CDP_IDENTITY_PORT > MKT_CDP_URL > 9222`` 选端点，
而 X vendor 用 ``X_CDP_URL``（默认同样 9222）——两者没有强制相等。多浏览器实例
下可能"在 A 上验证身份、在 B 上真实发送"。

冻结的契约：

1. 身份端点与执行端点不一致 → 拒绝真实发送（``identity_endpoint_mismatch``），
   零 vendor 调用。
2. 两者一致 → 正常通过，回执记录端点。
3. 同一个调试浏览器里出现多个 browser context 的 X 页面 → 拒绝（不猜）。
4. dry-run 不探测身份，但要报告端点不一致这个阻塞项。
"""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from core import account_identity, cdp_identity
from core.action_runner import ActionError, gate_account_identity
from core.execution_policy import authorize

ACTIONS = ["x:reply", "email:message"]


def _proof(user_id="uid-a", context="cdp:127.0.0.1:9222:T1:ctx-1"):
    return account_identity.AccountIdentityProof(
        channel="x", user_id=user_id, handle="acct-a",
        browser_context=context, source_ref="test:reader")


class EndpointBindingTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        authorize(self.dir, by="tester", actions=ACTIONS, days=90,
                  accounts=["acct-a"], identities=["acct-a=x:uid-a"],
                  require_identity=True)
        self.addCleanup(account_identity.set_reader, None)

    def test_endpoint_mismatch_is_refused_before_sending(self):
        account_identity.set_reader(lambda ch, **kw: _proof())
        env = {"MKT_CDP_IDENTITY_URL": "http://127.0.0.1:9223",
               "X_CDP_URL": "http://127.0.0.1:9222"}
        with mock.patch.dict(os.environ, env, clear=False), \
                self.assertRaises(ActionError) as ctx:
            gate_account_identity("x", self.dir, "acct-a")
        self.assertIn("identity_endpoint_mismatch", str(ctx.exception))

    def test_matching_endpoints_pass(self):
        account_identity.set_reader(lambda ch, **kw: _proof())
        env = {"MKT_CDP_IDENTITY_URL": "http://127.0.0.1:9222",
               "X_CDP_URL": "http://127.0.0.1:9222"}
        with mock.patch.dict(os.environ, env, clear=False):
            evidence = gate_account_identity("x", self.dir, "acct-a")
        self.assertTrue(evidence["enforced"])
        self.assertEqual("uid-a", evidence["observed_platform_identity"])

    def test_default_endpoints_agree(self):
        account_identity.set_reader(lambda ch, **kw: _proof())
        env = {k: v for k, v in os.environ.items()
               if k not in ("MKT_CDP_IDENTITY_URL", "MKT_CDP_IDENTITY_PORT",
                            "X_CDP_URL", "MKT_CDP_URL")}
        with mock.patch.dict(os.environ, env, clear=True):
            evidence = gate_account_identity("x", self.dir, "acct-a")
        self.assertTrue(evidence["enforced"])

    def test_multiple_x_contexts_are_ambiguous(self):
        payload = [
            {"type": "page", "id": "T1", "url": "https://x.com/home",
             "browserContextId": "ctx-1",
             "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/T1"},
            {"type": "page", "id": "T2", "url": "https://x.com/home2",
             "browserContextId": "ctx-2",
             "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/T2"},
        ]
        with self.assertRaises(cdp_identity._CdpError) as ctx:
            cdp_identity._select_target(payload, "http://127.0.0.1:9222")
        self.assertEqual(cdp_identity.TARGET_AMBIGUOUS, ctx.exception.reason)


if __name__ == "__main__":
    unittest.main()
