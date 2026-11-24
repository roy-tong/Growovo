import os
import tempfile
import unittest
from unittest import mock

from channels import github
from core import action_runner, github_app, github_events, growth_engagement


def discussion_payload():
    return {
        "action": "created", "installation": {"id": 456},
        "repository": {"id": 123, "full_name": "o/r"},
        "discussion": {"number": 8, "id": "D8", "title": "How to configure?",
                       "body": "Which config is required?", "created_at": "2026-11-24T00:00:00Z",
                       "html_url": "https://github.com/o/r/discussions/8",
                       "user": {"id": 10, "login": "alice"}},
    }


def comment_payload():
    value = discussion_payload()
    value["comment"] = {"id": "DC99", "body": "Can you show an example?",
                        "created_at": "2026-11-24T00:02:00Z",
                        "html_url": "https://github.com/o/r/discussions/8#discussioncomment-99",
                        "user": {"id": 11, "login": "bob"}}
    return value


class GitHubDiscussionSourceTest(unittest.TestCase):
    def test_webhook_and_comment_share_one_conversation(self):
        with tempfile.TemporaryDirectory() as data:
            github_events.ingest(data, "svc-test", "discussion", discussion_payload(), "delivery-d")
            github_events.ingest(data, "svc-test", "discussion_comment", comment_payload(), "delivery-c")
            rows = growth_engagement.list_conversations(data, "svc-test")
        self.assertEqual(1, len(rows))
        self.assertEqual("o/r/discussion#8", rows[0]["conversation_key"])
        self.assertEqual({"discussion-8", "discussion-comment-DC99"}, set(rows[0]["interaction_ids"]))

    def test_graphql_sync_maps_discussion_and_comments(self):
        discussion = {"id": "D8", "number": 8, "title": "How to configure?",
                      "body": "Which config?", "url": "https://github.com/o/r/discussions/8",
                      "createdAt": "2026-11-24T00:00:00Z", "closed": False,
                      "author": {"login": "alice"}}
        comment = {"id": "DC99", "body": "Example?", "url": "https://github.com/o/r/discussions/8#discussioncomment-99",
                   "createdAt": "2026-11-24T00:02:00Z", "author": {"login": "bob"}}
        calls = []

        def transport(method, path, token=None, payload=None):
            calls.append(payload["query"])
            if "discussions(first: 100" in payload["query"]:
                return {"data": {"repository": {"discussions": {
                    "nodes": [discussion], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}
            return {"data": {"repository": {"discussion": dict(
                discussion, comments={"nodes": [comment],
                                      "pageInfo": {"hasNextPage": False, "endCursor": None}})}}}

        with mock.patch.dict(os.environ, {"MKT_GITHUB_APP_INSTALLATION_TOKEN": "installation-token"}):
            rows = github_app.GitHubAppClient(transport=transport).sync_discussion_interactions("o/r")
        self.assertEqual(2, len(rows))
        self.assertEqual({"discussion-8", "discussion-comment-DC99"}, {row["external_id"] for row in rows})
        self.assertTrue(all(row["conversation_key"] == "o/r/discussion#8" for row in rows))
        self.assertEqual(2, len(calls))


class GitHubDiscussionReplyTest(unittest.TestCase):
    def test_reply_uses_pipeline_and_independent_comment_readback(self):
        text = "Use the example in the configuration guide."
        url = "https://github.com/o/r/discussions/8#discussioncomment-100"
        response = {"id": "DC100", "url": url, "body": text, "author": {"login": "growovo-app[bot]"}}
        client = mock.Mock()
        client.discussion.return_value = {"id": "D8", "number": 8, "closed": False}
        client.add_discussion_comment.return_value = response
        client.discussion_comment.return_value = response
        with tempfile.TemporaryDirectory() as data, \
                mock.patch("channels.github.Throttle.interval", return_value=0):
            record = github.discussion_reply(
                "o/r", 8, text, go=True, adhoc=True, campaign="test", data_dir=data,
                token="installation-token", client=client)
        self.assertEqual("committed", record["result"])
        self.assertEqual(url, record["evidence"])
        client.add_discussion_comment.assert_called_once_with("o/r", 8, text, token="installation-token")
        client.discussion_comment.assert_called_once_with("o/r", 8, "DC100", token="installation-token")

    def test_action_runner_parses_discussion_target(self):
        with tempfile.TemporaryDirectory() as data, \
                mock.patch("channels.github.discussion_reply", return_value={"result": "dry-run"}) as reply:
            action_runner._dispatch_action_inner(
                channel="github", action="reply", data_dir=data, campaign="test", go=False,
                target="discussion:o/r#8", text="answer", github_token="installation-token")
        self.assertEqual("installation-token", reply.call_args.kwargs["token"])
        self.assertEqual(("o/r", 8, "answer"), reply.call_args.args[:3])


if __name__ == "__main__":
    unittest.main()
