"""G-14：X 非 API 自动写必须是执行门禁，不是提示。

官方规则：脚本操作网站做非 API 自动化属违规，AI 自动回复需书面批准。
因此 CDP 写通道只能只读/人工用，自主写（go=True）默认拒绝。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from channels.x import adapter
from core import platform_caps, platform_compliance, platform_strategy

AUTH_SCHEMA = platform_compliance.AUTHORIZATION_SCHEMA


def _write_auth(root: Path, **overrides):
    payload = {
        "schema": AUTH_SCHEMA,
        "platform": "x",
        "route": "official_api",
        "transport": "api",
        "approved_by": "roy",
        "approved_at": "2026-11-18T09:00:00+08:00",
        "scopes": ["reply", "publish", "message"],
        "recipient_consent_policy": "仅联系公开表示愿意接收私信的人；每封含退订方式",
        "opt_out_path": "回复 stop 即加入不重试名单",
        "automated_reply_approval_ref": "x-approval-2026-11-18",
    }
    payload.update(overrides)
    path = root / platform_compliance.AUTHORIZATION_NAME
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def gate(self, action="reply", go=True, transport="cdp"):
        return platform_compliance.require_autonomous_write(
            "x", action, self.root, go=go, actual_transport=transport)

    def test_dry_run_and_reads_are_not_gated(self):
        self.assertIsNone(self.gate(go=False))
        self.assertIsNone(self.gate(action="search"))

    def test_missing_authorization_blocks_real_write(self):
        with self.assertRaises(platform_compliance.ComplianceError) as ctx:
            self.gate()
        self.assertEqual("non_api_write_forbidden", ctx.exception.code)
        self.assertIn("官方 API", str(ctx.exception))

    def test_api_authorization_cannot_run_on_cdp(self):
        _write_auth(self.root)
        with self.assertRaises(platform_compliance.ComplianceError) as ctx:
            self.gate(transport="cdp")
        self.assertEqual("transport_mismatch", ctx.exception.code)

    def test_browser_route_is_never_authorizable(self):
        _write_auth(self.root, route="browser")
        with self.assertRaises(platform_compliance.ComplianceError) as ctx:
            self.gate(transport="api")
        self.assertEqual("non_official_route", ctx.exception.code)

    def test_dm_needs_consent_and_opt_out(self):
        for field in ("recipient_consent_policy", "opt_out_path"):
            with self.subTest(field=field):
                _write_auth(self.root, **{field: ""})
                with self.assertRaises(platform_compliance.ComplianceError) as ctx:
                    self.gate(action="message", transport="api")
                self.assertEqual("consent_missing", ctx.exception.code)

    def test_automated_reply_needs_x_written_approval(self):
        _write_auth(self.root, automated_reply_approval_ref="")
        with self.assertRaises(platform_compliance.ComplianceError) as ctx:
            self.gate(action="reply", transport="api")
        self.assertEqual("approval_missing", ctx.exception.code)

    def test_action_must_be_in_scope(self):
        _write_auth(self.root, scopes=["publish"])
        with self.assertRaises(platform_compliance.ComplianceError) as ctx:
            self.gate(action="reply", transport="api")
        self.assertEqual("action_out_of_scope", ctx.exception.code)

    def test_complete_official_api_authorization_passes(self):
        _write_auth(self.root)
        payload = self.gate(transport="api")
        self.assertEqual("roy", payload["approved_by"])


class AdapterGateTests(unittest.TestCase):
    """门禁必须在触碰 vendor 之前生效：拒绝时不起子进程。"""

    def test_real_x_write_refused_before_vendor(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(adapter.subprocess, "run") as runner:
                with self.assertRaises(SystemExit) as ctx:
                    adapter.run("reply", ["--tweet-url", "https://x.com/a/status/1",
                                          "--text", "hi"],
                                go=True, adhoc=True, campaign="c", data_dir=tmp,
                                log=lambda *_a, **_k: None)
        self.assertIn("平台许可", str(ctx.exception))
        self.assertFalse(runner.called, "被平台规则拒绝时不得启动 vendor")

    @staticmethod
    def observed_identity():
        return {
            "enforced": True, "mode": "cdp_observed",
            "observed_platform_identity": "uid-current-browser",
            "handle": "current_browser_account", "identity_proof_ref": "offline:identity-proof",
            "browser_context": "cdp:http://127.0.0.1:9223",
            "observed_at": "2026-11-18T12:00:00+00:00",
        }

    def test_user_visible_cdp_requires_complete_observed_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(platform_compliance.ComplianceError) as ctx:
                platform_compliance.require_user_visible_cdp(
                    "x", "reply", tmp, go=True, actual_transport="cdp", identity=None)
            self.assertEqual("identity_observation_required", ctx.exception.code)

    def test_user_visible_cdp_does_not_extend_to_message_or_engage(self):
        with tempfile.TemporaryDirectory() as tmp:
            for action in ("message", "engage"):
                with self.subTest(action=action):
                    with self.assertRaises(platform_compliance.ComplianceError) as ctx:
                        platform_compliance.require_user_visible_cdp(
                            "x", action, tmp, go=True, actual_transport="cdp",
                            identity=self.observed_identity())
                    self.assertEqual("action_out_of_scope", ctx.exception.code)

    def test_user_visible_cdp_reaches_vendor_only_after_observed_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = mock.Mock(returncode=0)
            with mock.patch.object(adapter.subprocess, "run", return_value=proc) as runner:
                adapter.run("reply", ["--tweet-url", "https://x.com/a/status/1",
                                      "--text", "single user-approved reply"],
                            go=True, adhoc=True, campaign="c", data_dir=tmp,
                            identity=self.observed_identity(),
                            execution_mode="user_visible_cdp",
                            log=lambda *_a, **_k: None)
        self.assertTrue(runner.called, "单次用户确认且账号已观测时，CDP 路径应可执行")

    def test_dry_run_still_reaches_vendor(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = mock.Mock(returncode=0)
            with mock.patch.object(adapter.subprocess, "run", return_value=proc) as runner:
                adapter.run("reply", ["--tweet-url", "https://x.com/a/status/1",
                                      "--text", "hi"],
                            go=False, campaign="c", data_dir=tmp,
                            log=lambda *_a, **_k: None)
        argv = runner.call_args.args[0]
        self.assertIn("--dry-run", argv)


class PolicyMetadataTests(unittest.TestCase):
    def test_matrix_marks_x_autonomous_write_as_forbidden(self):
        row = platform_caps.classify("x")
        self.assertFalse(row["policy"]["autonomous_write"])
        self.assertEqual("non_api_write_forbidden", row["policy"]["code"])
        self.assertTrue(platform_caps.classify("email")["policy"]["autonomous_write"])

    def test_x_gate_in_strategy_is_error_level(self):
        gates = [g for g in platform_strategy.gates("x", scope="execution")
                 if g["code"] == "non_api_write"]
        self.assertEqual(1, len(gates))
        self.assertEqual("error", gates[0]["level"])


if __name__ == "__main__":
    unittest.main()
