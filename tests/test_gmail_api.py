import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import action_runner, gmail_api, gmail_events, growth_engagement, growth_runtime


def encoded(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).rstrip(b"=").decode("ascii")


def message(message_id, thread_id, body, history_id="10", labels=None):
    return {
        "id": message_id, "threadId": thread_id, "historyId": history_id,
        "labelIds": labels or ["INBOX"], "internalDate": "1795480000000",
        "payload": {
            "headers": [{"name": "From", "value": "alice@example.com"},
                        {"name": "To", "value": "owner@example.com"},
                        {"name": "Subject", "value": "Config question"},
                        {"name": "Message-ID", "value": f"<{message_id}@example.com>"}],
            "body": {"data": encoded(body)},
        },
    }


class GmailOAuthFileTest(unittest.TestCase):
    def test_from_env_loads_local_oauth_files_without_exposing_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "token.json"
            client_path = Path(tmp) / "client.json"
            token_path.write_text(json.dumps({"access_token": "access-from-file"}),
                                  encoding="utf-8")
            client_path.write_text(json.dumps({"installed": {
                "client_id": "client-id-from-file", "client_secret": "client-secret-from-file"}}),
                encoding="utf-8")
            env = {"MKT_GMAIL_OAUTH_TOKEN_PATH": str(token_path),
                   "MKT_GMAIL_OAUTH_CLIENT_PATH": str(client_path)}
            with mock.patch.dict(os.environ, env, clear=True):
                client = gmail_api.GmailClient.from_env()
                self.assertEqual("access-from-file", client.access_token)
                self.assertNotIn("MKT_GMAIL_REFRESH_TOKEN", os.environ)
                self.assertEqual("client-id-from-file", os.environ["MKT_GMAIL_CLIENT_ID"])
                self.assertEqual("client-secret-from-file", os.environ["MKT_GMAIL_CLIENT_SECRET"])

    def test_refresh_credentials_take_precedence_over_stored_access_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "token.json"
            client_path = Path(tmp) / "client.json"
            token_path.write_text(json.dumps({
                "access_token": "stale-access", "refresh_token": "refresh-from-file"}),
                encoding="utf-8")
            client_path.write_text(json.dumps({"installed": {
                "client_id": "client-id-from-file", "client_secret": "client-secret-from-file"}}),
                encoding="utf-8")
            env = {"MKT_GMAIL_OAUTH_TOKEN_PATH": str(token_path),
                   "MKT_GMAIL_OAUTH_CLIENT_PATH": str(client_path)}
            with mock.patch.dict(os.environ, env, clear=True):
                client = gmail_api.GmailClient.from_env()
                self.assertEqual("", client.access_token)
                self.assertEqual("refresh-from-file", os.environ["MKT_GMAIL_REFRESH_TOKEN"])


class GmailSyncTest(unittest.TestCase):
    def test_full_incremental_sync_and_history_404_recovery(self):
        messages = {"m1": message("m1", "thread-1", "First question", history_id="10"),
                    "m2": message("m2", "thread-2", "Second question", history_id="11")}

        def transport(method, path, token=None, params=None, payload=None):
            self.assertEqual("access-token", token)
            if path == "/gmail/v1/users/me/messages" and method == "GET":
                return {"messages": [{"id": "m1"}]}
            if path == "/gmail/v1/users/me/messages/m1":
                return messages["m1"]
            if path == "/gmail/v1/users/me/messages/m2":
                return messages["m2"]
            if path == "/gmail/v1/users/me/history":
                if params["startHistoryId"] == "old":
                    raise gmail_api.GmailAPIError(404, "history expired")
                if params["startHistoryId"] == "10":
                    return {"history": [{"messagesAdded": [{"message": {"id": "m2", "threadId": "thread-2"}}]}],
                            "historyId": "11"}
                return {"historyId": params["startHistoryId"]}
            raise AssertionError((method, path, params))

        with tempfile.TemporaryDirectory() as data:
            client = gmail_api.GmailClient(access_token="access-token", transport=transport)
            first = gmail_events.sync(data, "svc-test", client=client)
            self.assertEqual("10", first["history_id"])
            second = gmail_events.sync(data, "svc-test", client=client)
            self.assertEqual("11", second["history_id"])
            self.assertEqual(2, len(growth_engagement.list_conversations(data, "svc-test")))

            growth_runtime.save_connector_cursor(
                data, "gmail", "default", "svc-test", {"history_id": "old"})
            recovered = gmail_events.sync(data, "svc-test", client=client)
            self.assertTrue(recovered["full_sync"])
            self.assertEqual("10", recovered["history_id"])

    def test_watch_and_pubsub_ack_follow_successful_sync(self):
        with tempfile.TemporaryDirectory() as data:
            client = mock.Mock()
            client.watch.return_value = {"historyId": "10", "expiration": "1795489999999"}
            watch = gmail_events.renew_watch(data, "svc-test", client, "projects/p/topics/t")
            self.assertEqual("1795489999999", watch["expiration"])
            cursor = growth_runtime.get_connector_cursor(data, "gmail", "default", "svc-test")
            self.assertEqual("1795489999999", cursor["watch_expiration"])

            notification = {"emailAddress": "owner@example.com", "historyId": "11"}
            client.pubsub_pull.return_value = {"receivedMessages": [{
                "ackId": "ack-1", "message": {"data": encoded(json.dumps(notification))}}]}
            with mock.patch("core.gmail_events.sync", return_value={"added": 1}):
                got = gmail_events.pull_notifications(data, "svc-test", client, "projects/p/subscriptions/s")
            self.assertEqual(1, got["acknowledged"])
            client.pubsub_acknowledge.assert_called_once_with("projects/p/subscriptions/s", ["ack-1"])

            client.pubsub_acknowledge.reset_mock()
            with mock.patch("core.gmail_events.sync", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    gmail_events.pull_notifications(data, "svc-test", client, "projects/p/subscriptions/s")
            client.pubsub_acknowledge.assert_not_called()


class GmailReplyTest(unittest.TestCase):
    def test_new_message_uses_returned_thread_and_independent_readback(self):
        from channels import gmail
        text = "GROWOVO-RUNTIME-CANARY-MESSAGE"
        calls = []

        def transport(method, path, token=None, params=None, payload=None):
            calls.append((method, path, payload))
            if method == "POST":
                return {"id": "message-new", "threadId": "thread-new"}
            return message("message-new", "thread-new", text, labels=["SENT"])

        client = gmail_api.GmailClient(access_token="access-token", transport=transport)
        with tempfile.TemporaryDirectory() as data, \
                mock.patch("channels.gmail.Throttle.interval", return_value=0):
            record = gmail.reply(
                "", "owner@example.com", text, go=True, adhoc=True,
                campaign="test", data_dir=data, client=client, subject="Runtime canary")
        self.assertEqual("committed", record["result"], record)
        self.assertEqual("gmail://threads/thread-new/messages/message-new", record["evidence"])
        self.assertNotIn("threadId", calls[0][2])

    def test_reply_requires_independent_message_readback(self):
        from channels import gmail
        text = "Use the configuration example in the guide."
        client = mock.Mock()
        client.send_message.return_value = {"id": "message-1", "threadId": "thread-1"}
        client.get_message.return_value = message(
            "message-1", "thread-1", text, labels=["SENT"])
        with tempfile.TemporaryDirectory() as data, \
                mock.patch("channels.gmail.Throttle.interval", return_value=0):
            record = gmail.reply(
                "thread-1", "owner@example.com", text, go=True, adhoc=True,
                campaign="test", data_dir=data, token="access-token", client=client)
        self.assertEqual("committed", record["result"])
        self.assertEqual("gmail://threads/thread-1/messages/message-1", record["evidence"])
        client.send_message.assert_called_once()
        client.get_message.assert_called_once_with("message-1")

    def test_action_runner_forwards_gmail_route(self):
        from channels import gmail
        with tempfile.TemporaryDirectory() as data, \
                mock.patch("channels.gmail.reply", return_value={"result": "dry-run"}) as reply:
            action_runner._dispatch_action_inner(
                channel="email", action="message", data_dir=data, campaign="test", go=False,
                target="owner@example.com", text="answer", transport="gmail-api",
                thread_id="thread-1", gmail_token="access-token")
        self.assertEqual("access-token", reply.call_args.kwargs["token"])
        self.assertEqual("thread-1", reply.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
