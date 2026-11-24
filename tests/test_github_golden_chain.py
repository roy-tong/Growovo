import hashlib
import hmac
import json
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock

from core import github_events, growth_engagement, growth_execution, growth_runtime, growth_service
from tests.test_growth_goals import goal


class GitHubGoldenChainTest(unittest.TestCase):
    def test_signed_webhook_to_reply_receipt_and_duplicate_submission(self):
        with tempfile.TemporaryDirectory() as data:
            service = growth_service.start(data, goal())
            sid = service["id"]
            payload = {
                "action": "created", "installation": {"id": 456},
                "repository": {"id": 123, "full_name": "o/r"},
                "issue": {"number": 7, "id": 70},
                "comment": {"id": 99, "body": "Which file should I change?",
                            "created_at": "2026-11-24T00:02:00Z",
                            "html_url": "https://github.com/o/r/issues/7#issuecomment-99",
                            "user": {"id": 11, "login": "bob"}},
            }
            raw = json.dumps(payload).encode("utf-8")
            signature = "sha256=" + hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
            server = github_events.create_server(data, sid, b"secret", host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_port}/github/webhook", data=raw, method="POST",
                    headers={"X-GitHub-Event": "issue_comment", "X-GitHub-Delivery": "delivery-1",
                             "X-Hub-Signature-256": signature, "Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(200, response.status)
                    accepted = json.load(response)
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
            self.assertTrue(accepted["stored"])
            conversations = growth_engagement.list_conversations(data, sid)
            self.assertEqual(1, len(conversations))
            conversation = conversations[0]
            text = "Use the config path shown in the reproduction steps."
            growth_engagement.draft(data, sid, conversation["id"], text, by="tester")
            receipt = "https://github.com/o/r/issues/7#issuecomment-100"
            execution = {"target": "issue:o/r#7", "text": text,
                         "channels": {"github": {"api": True}}}
            with mock.patch("core.action_runner.run_action",
                            return_value={"result": "committed", "evidence": receipt}) as send:
                first = growth_execution.run(data, sid, "github-engage", by="worker-a", payload=execution)
                second = growth_execution.run(data, sid, "github-engage", by="worker-b", payload=execution)
            self.assertEqual("completed", first["status"])
            self.assertEqual("completed", second["status"])
            self.assertEqual(1, send.call_count)
            growth_engagement.record_terminal(
                data, sid, conversation["id"], "committed", by="tester", receipt_ref=receipt)
            attempts = growth_runtime.attempts(data, sid)
            self.assertEqual(1, len(attempts))
            self.assertEqual("completed", attempts[0]["status"])
            self.assertEqual(0, github_events.replay(data, sid)["processed"])
            final = growth_engagement.list_conversations(data, sid)[0]
            self.assertEqual("committed", final["status"])
            self.assertEqual(receipt, final["receipt_ref"])


if __name__ == "__main__":
    unittest.main()
