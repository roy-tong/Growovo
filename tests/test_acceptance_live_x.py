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

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "acceptance_live_x.py"


def run_script(*args, timeout=120):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=timeout)


class DryRunChainTests(unittest.TestCase):
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
    def test_preflight_blocks_missing_authorization_profile_and_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_script("--preflight", "--workspace", tmp,
                              "--cdp-url", "http://127.0.0.1:9231",
                              "--target-url", "https://x.com/growovo_test/status/1")
            self.assertEqual(2, proc.returncode)
            payload = json.loads(proc.stdout)
            self.assertFalse(payload["ready"])
            checks = {item["check"]: item for item in payload["checks"]}
            self.assertFalse(checks["authorized_by"]["ok"])
            self.assertFalse(checks["isolated_profile"]["ok"])
            self.assertFalse(checks["browser_endpoint"]["ok"])
            self.assertFalse((Path(tmp) / "operations.jsonl").exists())
            self.assertFalse((Path(tmp) / "ledger.jsonl").exists())

    def test_preflight_refuses_campaign_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "profile"
            profile.mkdir()
            proc = run_script("--preflight", "--workspace", tmp,
                              "--authorize-by", "tester",
                              "--profile-dir", str(profile),
                              "--cdp-url", "http://127.0.0.1:9222",
                              "--target-url", "https://x.com/growovo_test/status/1")
            self.assertEqual(2, proc.returncode)
            payload = json.loads(proc.stdout)
            checks = {item["check"]: item for item in payload["checks"]}
            self.assertFalse(checks["independent_cdp_endpoint"]["ok"])
            self.assertIn("9222", checks["independent_cdp_endpoint"]["detail"])


if __name__ == "__main__":
    unittest.main()
