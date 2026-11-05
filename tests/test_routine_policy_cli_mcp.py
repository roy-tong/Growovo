#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcp_server

ROOT = Path(__file__).resolve().parents[1]


class TestRoutinePolicyCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(ROOT / "mkt.py"), "--workspace", str(self.data), *args],
                                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(expected, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_policy_lifecycle_and_strict_default_account(self):
        self.cli("policy", "check", "--channel", "email", "--action-name", "message", expected=2)
        self.cli("policy", "enable", "--by", "human-owner", "--actions", "email:message")
        self.assertTrue(self.cli("policy", "check", "--channel", "email", "--action-name", "message")["allowed"])
        self.assertTrue(self.cli("policy", "check", "--channel", "email", "--action-name", "message",
                                 "--account", "default")["allowed"])
        self.cli("policy", "check", "--channel", "email", "--action-name", "message",
                 "--account", "other-account", expected=2)
        self.cli("policy", "check", "--channel", "x", "--action-name", "publish", expected=2)
        self.cli("policy", "disable", "--by", "human-owner")
        self.cli("policy", "check", "--channel", "email", "--action-name", "message", expected=2)

    def test_enable_without_actions_defaults_to_email_only(self):
        policy = self.cli("policy", "enable", "--by", "human-owner")
        self.assertEqual(["email:message"], policy["allow_actions"])
        self.cli("policy", "check", "--channel", "x", "--action-name", "publish", expected=2)
        self.assertTrue(self.cli("policy", "check", "--channel", "email",
                                 "--action-name", "message")["allowed"])

    def test_enable_defaults_to_30_day_expiry(self):
        policy = self.cli("policy", "enable", "--by", "human-owner", "--actions", "email:message")
        self.assertTrue(policy["expires_at"], policy)
        shown = self.cli("policy", "show")
        self.assertTrue(shown["expires_at"])
        self.assertNotIn("warning", shown)

    def test_enable_with_zero_days_is_explicit_permanent(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "mkt.py"), "--workspace", str(self.data),
             "policy", "enable", "--by", "human-owner", "--actions", "email:message", "--days", "0"],
            cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("不过期", result.stderr)
        policy = json.loads(result.stdout)
        self.assertIsNone(policy["expires_at"])
        shown = self.cli("policy", "show")
        self.assertIn("warning", shown)

    def test_negative_days_is_rejected(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "mkt.py"), "--workspace", str(self.data),
             "policy", "enable", "--by", "human-owner", "--actions", "email:message", "--days", "-1"],
            cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(2, result.returncode)
        self.assertIn("授权失败", result.stderr)

    def test_identity_binding_is_enforced_by_policy_check(self):
        policy = self.cli("policy", "enable", "--by", "human-owner",
                          "--actions", "x:publish", "--accounts", "brand-main",
                          "--identity", "brand-main=x:42", "--require-identity")
        self.assertTrue(policy["account_identities"]["brand-main"])
        self.assertTrue(policy["require_identity"])
        self.assertTrue(self.cli("policy", "check", "--channel", "x", "--action-name", "publish",
                                 "--account", "brand-main",
                                 "--platform-user-id", "42")["allowed"])
        self.cli("policy", "check", "--channel", "x", "--action-name", "publish",
                 "--account", "brand-main", "--platform-user-id", "43", expected=2)
        self.cli("policy", "check", "--channel", "x", "--action-name", "publish",
                 "--account", "brand-main", expected=2)

    def test_named_account_must_match_exactly(self):
        self.cli("policy", "enable", "--by", "human-owner", "--actions", "x:publish,linkedin:message",
                 "--accounts", "brand-main")
        self.assertTrue(self.cli("policy", "check", "--channel", "x", "--action-name", "publish",
                                 "--account", "brand-main")["allowed"])
        self.cli("policy", "check", "--channel", "x", "--action-name", "publish",
                 "--account", "Brand-Main", expected=2)


class TestRoutineMCPPassThrough(unittest.TestCase):
    def test_mcp_exposes_execution_flag_not_policy_management(self):
        names = {tool["name"] for tool in mcp_server.TOOLS}
        self.assertNotIn("mkt_policy", names)
        self.assertNotIn("mkt_authorize_routine", names)
        queue = next(tool for tool in mcp_server.TOOLS if tool["name"] == "mkt_run_queue")
        self.assertIn("routine", queue["inputSchema"]["properties"])

    def test_queue_routine_and_account_reach_existing_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "queues").mkdir()
            path = data / "queues/campaign.json"
            path.write_text(json.dumps({"campaign": "campaign", "channel": "email", "action": "message",
                                        "account": "queue-account",
                                        "items": [{"id": "1", "target": "a@example.com", "body": "fixture"}]}), encoding="utf-8")
            with patch.object(mcp_server, "DATA", data), \
                    patch("channels.email_smtp.send_queue", return_value=[]) as sender:
                response = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "mkt_run_queue", "arguments": {
                        "queue_path": str(path), "go": True, "routine": True}}})
            self.assertFalse(response["result"].get("isError", False), response)
            kwargs = sender.call_args.kwargs
            self.assertTrue(kwargs["routine"])
            self.assertEqual("queue-account", kwargs["account"])
            self.assertTrue(kwargs["go"])


if __name__ == "__main__":
    unittest.main()
