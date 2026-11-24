import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from channels import github
from core import github_app


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class GitHubAppClientTest(unittest.TestCase):
    def test_installation_token_uses_app_jwt_without_exposing_private_key(self):
        calls = []

        def transport(method, path, token=None, payload=None):
            calls.append((method, path, token, payload))
            return {"token": "installation-token", "expires_at": "2026-11-24T01:00:00Z"}

        client = github_app.GitHubAppClient(
            app_id="123", installation_id="456", private_key_path="/private/app.pem",
            transport=transport, jwt_factory=lambda: "app-jwt")
        self.assertEqual("installation-token", client.installation_token())
        self.assertEqual(("POST", "/app/installations/456/access_tokens", "app-jwt", None), calls[0])

    def test_sync_issues_and_comments_maps_stable_conversation_ids(self):
        issue = {
            "number": 7, "title": "Config failed", "body": "Cannot read config",
            "state": "open", "created_at": "2026-11-24T00:00:00Z",
            "updated_at": "2026-11-24T00:01:00Z", "html_url": "https://github.com/o/r/issues/7",
            "user": {"id": 10, "login": "alice"}, "comments": 1,
        }
        comment = {
            "id": 99, "body": "Which file should I change?", "created_at": "2026-11-24T00:02:00Z",
            "html_url": "https://github.com/o/r/issues/7#issuecomment-99",
            "user": {"id": 11, "login": "bob"},
        }

        def transport(method, path, token=None, payload=None):
            self.assertEqual("installation-token", token)
            if path == "/repos/o/r/issues?state=open&per_page=100&page=1":
                return [issue]
            if path == "/repos/o/r/issues/7/comments?per_page=100&page=1":
                return [comment]
            if path.endswith("page=2"):
                return []
            raise AssertionError(path)

        with mock.patch.dict(os.environ, {"MKT_GITHUB_APP_INSTALLATION_TOKEN": "installation-token"}):
            client = github_app.GitHubAppClient(transport=transport)
            rows = client.sync_issue_interactions("o/r")
        self.assertEqual(2, len(rows))
        self.assertEqual({"issue-7", "issue-comment-99"}, {row["external_id"] for row in rows})
        self.assertTrue(all(row["source"] == "github_app" for row in rows))
        self.assertTrue(all(row["repository"] == "o/r" for row in rows))


class GitHubIssueCreateTest(unittest.TestCase):
    def test_action_gateway_creates_issue_and_independently_reads_it_back(self):
        from core import action_gateway
        self.assertTrue(hasattr(github, "create_issue"))
        self.assertTrue(hasattr(github_app.GitHubAppClient, "create_issue"))
        text = "A dedicated canary body."
        title = "Dedicated runtime canary"
        issue = {"number": 7, "html_url": "https://github.com/o/r/issues/7",
                 "title": title, "body": text, "state": "open"}
        calls = []

        def transport(method, path, token=None, payload=None):
            self.assertEqual("installation-token", token)
            calls.append((method, path, payload))
            if (method, path) == ("GET", "/repos/o/r"):
                return {"full_name": "o/r"}
            if (method, path) == ("POST", "/repos/o/r/issues"):
                return issue
            if (method, path) == ("GET", "/repos/o/r/issues/7"):
                return issue
            raise AssertionError((method, path))

        client = github_app.GitHubAppClient(transport=transport)
        with tempfile.TemporaryDirectory() as data, \
                mock.patch.dict(os.environ, {"MKT_GITHUB_APP_INSTALLATION_TOKEN": "installation-token"}), \
                mock.patch("core.github_app.GitHubAppClient.from_env", return_value=client), \
                mock.patch("core.action_runner.enforce_approval", return_value="plan"), \
                mock.patch("core.planlint.enforce_approval", return_value="plan"), \
                mock.patch("channels.github.Throttle.interval", return_value=0):
            record = action_gateway.execute(
                channel="github", action="publish", target="o/r", title=title, text=text,
                data_dir=data, campaign="canary", go=True, allow_unverified=True,
                github_token="installation-token")
        self.assertEqual("committed", record["result"], record)
        self.assertEqual(issue["html_url"], record["evidence"])
        self.assertIn(("POST", "/repos/o/r/issues", {"title": title, "body": text}), calls)
        self.assertIn(("GET", "/repos/o/r/issues/7", None), calls)


class GitHubReplyVerificationTest(unittest.TestCase):
    def test_reply_requires_independent_comment_readback_and_records_receipt(self):
        text = "Use the config path shown in the reproduction steps."
        comment_url = "https://github.com/o/r/issues/7#issuecomment-99"
        response = {"id": 99, "html_url": comment_url, "body": text,
                    "user": {"id": 555, "login": "growovo-app[bot]"}}
        observed = dict(response, issue_url="https://api.github.com/repos/o/r/issues/7")

        def fake_api(path, *, token=None, base=github.API):
            self.assertEqual("installation-token", token)
            if path == "/repos/o/r/issues/7":
                return {"state": "open", "created_at": datetime.now(timezone.utc).isoformat()}
            if path == "/repos/o/r/issues/comments/99":
                return observed
            raise AssertionError(path)

        with tempfile.TemporaryDirectory() as data, \
                mock.patch("channels.github._api", side_effect=fake_api), \
                mock.patch("channels.github._token", return_value="installation-token"), \
                mock.patch("channels.github.Throttle.interval", return_value=0), \
                mock.patch("channels.github.urllib.request.urlopen",
                           return_value=Response(json.dumps(response).encode("utf-8"))):
            record = github.reply("o/r", 7, text, go=True, adhoc=True,
                                  campaign="test", data_dir=data, token="installation-token")
        self.assertEqual("committed", record["result"])
        self.assertEqual(comment_url, record["evidence"])

    def test_action_runner_forwards_app_token_to_github_adapter(self):
        from core import action_runner
        with tempfile.TemporaryDirectory() as data, \
                mock.patch("channels.github.reply", return_value={"result": "dry-run"}) as reply:
            action_runner._dispatch_action_inner(
                channel="github", action="reply", data_dir=data, campaign="test", go=False,
                target="issue:o/r#7", text="answer", github_token="installation-token")
        self.assertEqual("installation-token", reply.call_args.kwargs["token"])

    def test_find_replies_returns_only_exact_body_matches(self):
        rows = [{"id": 1, "body": "answer", "html_url": "https://github.com/o/r/issues/7#issuecomment-1"},
                {"id": 2, "body": "other", "html_url": "https://github.com/o/r/issues/7#issuecomment-2"}]
        with mock.patch("channels.github._api", return_value=rows):
            got = github.find_replies("o/r", 7, "answer", token="installation-token")
        self.assertEqual([rows[0]], got)


if __name__ == "__main__":
    unittest.main()
