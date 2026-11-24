#!/usr/bin/env python3
"""live X 验收脚本（评审 §6/§9 交付物 2）——脚本级纪律测试。

真实运行绝不进 CI/单测；这里只验证：
- 干跑模式全链离线绿（不碰网络/浏览器/9222/profiles）；
- live 模式没有 --authorize-by 时拒绝启动；
- live 模式默认/强制拒绝 9222（campaign 占用端口）与共享 profiles/；
- 干跑输出保持诚实（不标 live-proven）。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import scripts.acceptance_live_x as live_x

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "acceptance_live_x.py"


def run_script(*args, timeout=120):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=timeout)


class DryRunChainTests(unittest.TestCase):
    def test_default_acceptance_never_selects_legacy_cdp(self):
        from core import action_gateway
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("core.action_gateway.resolve_transport",
                            wraps=action_gateway.resolve_transport) as route:
                rc = live_x.main(["--dry-run", "--workspace", tmp])
        self.assertEqual(0, rc)
        request = route.call_args.args[0]
        self.assertFalse(request.get("routine", False))
        self.assertFalse(request.get("legacy_cdp", False))
        self.assertEqual("browser-attach", request["transport"])

    def test_publish_preview_uses_the_same_managed_route(self):
        from core import action_gateway
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("core.action_gateway.resolve_transport",
                            wraps=action_gateway.resolve_transport) as route:
                rc = live_x.main(["--dry-run", "--action", "publish", "--workspace", tmp])
        self.assertEqual(0, rc)
        self.assertEqual("publish", route.call_args.args[0]["action"])
        self.assertEqual("browser-attach", route.call_args.args[0]["transport"])

    def test_dry_run_full_chain_offline_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_script("--dry-run", "--workspace", tmp)
            self.assertEqual(0, proc.returncode,
                             f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
            out = proc.stdout
            # 六方分离表
            for party in ("授权人", "操作者", "审批", "执行账号",
                          "浏览器上下文", "回读"):
                self.assertIn(party, out, f"六方分离表缺 {party}")
            # 诚实口径：干跑不是 live
            self.assertNotIn("live-proven", out)
            self.assertIn("dry-run", out)
            # 操作日志已落全字段
            ops = json.loads((Path(tmp) / "operations.jsonl")
                             .read_text(encoding="utf-8").splitlines()[-1])
            for field in ("operation_id", "channel", "action", "target",
                          "content_sha256", "account", "receipt", "result"):
                self.assertIn(field, ops)
            # 读数或 UNKNOWN
            self.assertIn("UNKNOWN", out)

    def test_dry_run_readback_mismatch_rehearses_halt(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_script("--dry-run", "--workspace", tmp,
                              "--simulate-readback-mismatch")
            self.assertEqual(1, proc.returncode, "回读不一致必须停机（非零退出）")
            self.assertIn("indeterminate", proc.stdout)
            self.assertIn("禁止自动重发", proc.stdout)
            # 账本落 indeterminate：no_retry 兜底生效
            rows = [json.loads(ln) for ln
                    in (Path(tmp) / "ledger.jsonl").read_text(
                        encoding="utf-8").splitlines() if ln.strip()]
            self.assertTrue(any(r.get("result") == "indeterminate" for r in rows))


class ManagedBrowserPreflightTests(unittest.TestCase):
    def test_preflight_uses_managed_tab_identity_and_owner_authorization(self):
        args = SimpleNamespace(
            authorize_by="roy", target_url="https://x.com/elliwoodtong/status/1",
            text="controlled acceptance", workspace="", cdp_url="http://127.0.0.1:9223",
            profile_dir="")
        attach = mock.Mock()
        attach.ensure_managed_tab.return_value = 11
        attach.observe_identity.return_value = {
            "enforced": True, "mode": "cdp_observed",
            "observed_platform_identity": "x:elliwoodtong", "handle": "elliwoodtong",
            "identity_proof_ref": "browser-attach:11@now",
            "browser_context": "webbridge-session:test", "observed_at": "now",
        }
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("core.browser_attach.BrowserAttach", return_value=attach), \
                mock.patch("core.platform_compliance.require_owner_attested_browser_write",
                           return_value={"route": "owner_attested_session"}) as permit, \
                mock.patch("core.cdp_identity.probe",
                           side_effect=AssertionError("default preflight must not use legacy CDP")):
            args.workspace = tmp
            rc = live_x.preflight(args)
        self.assertEqual(0, rc)
        attach.ensure_managed_tab.assert_called_once()
        permit.assert_called_once()
        self.assertEqual("browser-attach", permit.call_args.kwargs["actual_transport"])


class LiveGuardTests(unittest.TestCase):
    def test_live_requires_authorize_by(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_script("--workspace", tmp,
                              "--target-url", "https://x.com/a/status/1")
            self.assertEqual(2, proc.returncode)
            self.assertIn("--authorize-by", proc.stdout + proc.stderr)

    def test_live_refuses_campaign_port_9222(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_script("--workspace", tmp, "--authorize-by", "roy",
                              "--cdp-url", "http://127.0.0.1:9222",
                              "--target-url", "https://x.com/a/status/1")
            self.assertEqual(2, proc.returncode)
            self.assertIn("9222", proc.stdout + proc.stderr)

    def test_live_refuses_shared_profiles_dir(self):
        shared = ROOT / "profiles"
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_script("--workspace", tmp, "--authorize-by", "roy",
                              "--profile-dir", str(shared),
                              "--target-url", "https://x.com/a/status/1")
            self.assertEqual(2, proc.returncode)
            self.assertIn("profiles", proc.stdout + proc.stderr)

    def test_dry_run_never_touches_network_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_script("--dry-run", "--workspace", tmp)
            self.assertEqual(0, proc.returncode)
            self.assertIn("offline", proc.stdout.lower())


class PreflightTests(unittest.TestCase):
    def _args(self, tmp, **overrides):
        values = dict(authorize_by="", action="reply", target_url="",
                      text="controlled acceptance", workspace=tmp)
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_preflight_reports_managed_failure_and_agent_recovery(self):
        from core.browser_attach import BridgeError
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("scripts.acceptance_live_x._managed_browser",
                           side_effect=BridgeError("tab_not_found", "missing")):
            proc = run_script("--preflight", "--workspace", tmp)
            self.assertEqual(2, proc.returncode)
            payload = json.loads(proc.stdout)
            self.assertFalse(payload["ready"])
            self.assertEqual("browser-attach", payload["transport"])
            checks = {item["check"]: item for item in payload["checks"]}
            self.assertFalse(checks["authorized_by"]["ok"])
            self.assertFalse(checks["managed_browser"]["ok"])
            self.assertNotIn("independent_cdp_endpoint", checks)
            self.assertNotIn("isolated_profile", checks)
            self.assertEqual("browser-attach", payload["recovery"]["route"])
            self.assertFalse((Path(tmp) / "operations.jsonl").exists())
            self.assertFalse((Path(tmp) / "ledger.jsonl").exists())

    def test_preflight_rejects_someone_elses_target(self):
        identity = {
            "enforced": True, "mode": "cdp_observed",
            "observed_platform_identity": "x:elliwoodtong", "handle": "elliwoodtong",
            "identity_proof_ref": "browser-attach:11@now",
            "browser_context": "webbridge-session:test", "observed_at": "now",
        }
        attach = mock.Mock(session="test")
        permit = {"route": "owner_attested_session", "window_end": "tomorrow"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("scripts.acceptance_live_x._managed_browser",
                           return_value=(attach, 11, identity, permit)):
            args = self._args(tmp, authorize_by="tester",
                              target_url="https://x.com/someone_else/status/1")
            self.assertEqual(2, live_x.preflight(args))


if __name__ == "__main__":
    unittest.main()
