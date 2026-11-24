import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from core import growth_execution, growth_service
from tests.test_growth_goals import goal


class GrowthExecutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.service = growth_service.start(self.data, goal())
        self.sid = self.service["id"]

    def test_run_local_diagnose_completes_real_work(self):
        got = growth_execution.run(self.data, self.sid, "diagnose-product", by="tester")
        self.assertEqual("completed", got["status"])
        loaded = growth_service.load(self.data, self.sid)
        action = next(row for row in loaded["actions"] if row["id"] == "diagnose-product")
        self.assertEqual("completed", action["status"])
        self.assertEqual("Growovo", action["evidence"]["diagnosis"]["product"]["name"])

    def test_manual_channel_handoff_is_not_marked_as_delivered(self):
        for action_id in ("diagnose-product", "baseline-observe", "asset-prepare"):
            growth_service.record_action(self.data, self.sid, action_id, "completed", by="tester")
        got = growth_execution.run(
            self.data, self.sid, "x-publish", by="tester",
            payload={"text": "Try the reproducible demo", "channels": {}})
        self.assertEqual("manual_handoff", got["status"])
        self.assertTrue(Path(got["handoff"]["path"]).is_file())
        loaded = growth_service.load(self.data, self.sid)
        action = next(row for row in loaded["actions"] if row["id"] == "x-publish")
        self.assertEqual("waiting_manual", action["status"])
        self.assertEqual("github-engage", growth_service.next_action(self.data, self.sid)["id"])

    def test_identity_preflight_failure_returns_agent_recovery_not_unknown_write(self):
        from core.browser_write import ActionBlocked
        payload = {"text": "A specific answer", "channels": {"x": {"cdp": True}}}
        with mock.patch("core.action_gateway.execute",
                        side_effect=ActionBlocked("identity_unknown", "verify_credentials HTTP 403")):
            got = growth_execution.run(self.data, self.sid, "x-publish", by="tester", payload=payload)
        self.assertEqual("blocked", got["status"])
        recovery = got["record"].get("recovery") or {}
        self.assertEqual("browser-attach", recovery.get("route"))
        self.assertEqual("ensure_managed_tab", recovery.get("steps", [{}])[0].get("operation"))
        self.assertFalse(recovery.get("retry_write", True))

    def test_indeterminate_write_is_not_executed_twice(self):
        payload = {"target": "issue:owner/repo#1", "text": "A specific answer",
                   "channels": {"github": {"api": True}}}
        with mock.patch("core.action_runner.run_action",
                        return_value={"result": "indeterminate", "evidence": ""}) as send:
            first = growth_execution.run(self.data, self.sid, "github-engage", by="worker-a", payload=payload)
            second = growth_execution.run(self.data, self.sid, "github-engage", by="worker-b", payload=payload)
        self.assertEqual("waiting_verification", first["status"])
        self.assertEqual("reconcile_receipt",
                         first["record"]["recovery"]["steps"][0]["operation"])
        self.assertFalse(first["record"]["recovery"]["retry_write"])
        self.assertEqual("waiting_verification", second["status"])
        self.assertEqual(1, send.call_count)
        loaded = growth_service.load(self.data, self.sid)
        action = next(row for row in loaded["actions"] if row["id"] == "github-engage")
        self.assertEqual("waiting_verification", action["status"])

    def test_two_entry_points_claim_only_one_external_write(self):
        entered = threading.Event()
        release = threading.Event()
        payload = {"target": "issue:owner/repo#1", "text": "A specific answer",
                   "channels": {"github": {"api": True}}}

        def send(**kwargs):
            entered.set()
            release.wait(timeout=5)
            return {"result": "committed", "evidence": "https://github.com/owner/repo/issues/1#issuecomment-9"}

        with mock.patch("core.action_runner.run_action", side_effect=send) as action:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(growth_execution.run, self.data, self.sid,
                                    "github-engage", "worker-a", payload)
                self.assertTrue(entered.wait(timeout=5))
                second = pool.submit(growth_execution.run, self.data, self.sid,
                                     "github-engage", "worker-b", payload)
                release.set()
                results = [first.result(), second.result()]
        self.assertEqual(1, action.call_count)
        self.assertIn("completed", [row["status"] for row in results])
        loaded = growth_service.load(self.data, self.sid)
        current = next(row for row in loaded["actions"] if row["id"] == "github-engage")
        self.assertEqual("completed", current["status"])

    def test_reconcile_unique_github_comment_completes_original_action(self):
        payload = {"target": "issue:owner/repo#1", "text": "A specific answer",
                   "channels": {"github": {"api": True}}}
        with mock.patch("core.action_runner.run_action",
                        return_value={"result": "indeterminate", "evidence": ""}):
            growth_execution.run(self.data, self.sid, "github-engage", by="worker-a", payload=payload)
        comment = {"id": 9, "body": payload["text"],
                   "html_url": "https://github.com/owner/repo/issues/1#issuecomment-9"}
        with mock.patch("channels.github.find_replies", return_value=[comment]) as find:
            got = growth_execution.reconcile(
                self.data, self.sid, "github-engage", by="tester", payload=payload)
        self.assertEqual("completed", got["status"])
        self.assertEqual(comment["html_url"], got["evidence"]["receipt"])
        find.assert_called_once_with("owner/repo", 1, payload["text"], token=None)
        from core import growth_runtime
        attempts = growth_runtime.attempts(self.data, self.sid)
        self.assertEqual("completed", attempts[0]["status"])
        self.assertEqual(comment["html_url"], attempts[0]["evidence"]["receipt"])

    def test_reconcile_discussion_comment_uses_graphql_reader(self):
        payload = {"target": "discussion:owner/repo#8", "text": "A specific answer",
                   "channels": {"github": {"api": True}}}
        with mock.patch("core.action_runner.run_action",
                        return_value={"result": "indeterminate", "evidence": ""}):
            growth_execution.run(self.data, self.sid, "github-engage", by="worker-a", payload=payload)
        comment = {"id": "DC9", "body": payload["text"],
                   "url": "https://github.com/owner/repo/discussions/8#discussioncomment-9"}
        with mock.patch("channels.github.find_discussion_replies", return_value=[comment]) as find:
            got = growth_execution.reconcile(
                self.data, self.sid, "github-engage", by="tester", payload=payload)
        self.assertEqual("completed", got["status"])
        self.assertEqual(comment["url"], got["evidence"]["receipt"])
        find.assert_called_once_with("owner/repo", 8, payload["text"], token=None)

    def test_github_app_route_passes_installation_token_only_to_executor(self):
        client = mock.Mock()
        client.installation_token.return_value = "installation-token"
        payload = {"target": "issue:owner/repo#1", "text": "A specific answer",
                   "channels": {"github": {"github_app": True}}}
        with mock.patch("core.github_app.GitHubAppClient.from_env", return_value=client), \
                mock.patch("core.action_runner.run_action",
                           return_value={"result": "committed", "evidence": "receipt"}) as send:
            got = growth_execution.run(self.data, self.sid, "github-engage", by="tester", payload=payload)
        self.assertEqual("completed", got["status"])
        self.assertEqual("installation-token", send.call_args.kwargs["github_token"])
        self.assertNotIn("installation-token", str(got))

    def test_run_uses_persisted_capabilities_without_boolean_override(self):
        from core import execution_capabilities
        from tests.test_execution_capabilities import capability
        execution_capabilities.upsert(
            self.data, capability(verified=["read", "write", "verify"]), by="tester")
        client = mock.Mock()
        client.installation_token.return_value = "installation-token"
        with mock.patch("core.github_app.GitHubAppClient.from_env", return_value=client), \
                mock.patch("core.action_runner.run_action",
                           return_value={"result": "committed", "evidence": "receipt"}) as send:
            got = growth_execution.run(
                self.data, self.sid, "github-engage", by="tester",
                payload={"target": "issue:owner/repo#1", "text": "A specific answer",
                         "channels": {"github": {"api": True}}})
        self.assertEqual("completed", got["status"])
        self.assertEqual("installation-token", send.call_args.kwargs["github_token"])

    def test_reconcile_gmail_sent_message_completes_original_action(self):
        email_goal = goal()
        email_goal["constraints"]["channels"] = ["email"]
        service = growth_service.start(self.data, email_goal)
        payload = {"target": "owner@example.com", "text": "A specific answer",
                   "thread_id": "thread-1", "channels": {"email": {"gmail_api": True}}}
        with mock.patch("core.action_runner.run_action",
                        return_value={"result": "indeterminate", "evidence": ""}):
            growth_execution.run(self.data, service["id"], "email-followup", by="worker-a", payload=payload)
        sent = {"id": "message-1", "threadId": "thread-1"}
        with mock.patch("channels.gmail.find_sent_replies", return_value=[sent]) as find:
            got = growth_execution.reconcile(
                self.data, service["id"], "email-followup", by="tester", payload=payload)
        self.assertEqual("completed", got["status"])
        self.assertEqual("gmail://threads/thread-1/messages/message-1", got["evidence"]["receipt"])
        find.assert_called_once_with("thread-1", "owner@example.com", payload["text"], token=None)

    def test_run_asset_uses_real_reproduction(self):
        got = growth_execution.run(
            self.data, self.sid, "asset-prepare", by="tester",
            payload={"asset": {
                "id": "demo", "title": "Replay", "scenario": "Agent says done",
                "audience": "developers", "value": "Inspect the receipt",
                "cta": {"kind": "run_demo", "url": "https://example.invalid/demo"},
                "steps": ["Run it"], "expected_output": "A result",
                "reproduction": {"command": [sys.executable, "-c", "print('ok')"],
                                 "authorized": True, "by": "tester"},
            }})
        self.assertEqual("completed", got["status"])
        self.assertTrue(got["asset"]["reproduced"])


if __name__ == "__main__":
    unittest.main()
