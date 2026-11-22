#!/usr/bin/env python3
"""每波自动收件与优先回复：临时队列、模拟收集与发送，不触网。"""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import engagement_cycle as cycle
from core import inbox_replies

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 11, 21, 22, 5, tzinfo=TZ)


def row(channel, action, target="", text="t", actor="", source="queue"):
    out = {"channel": channel, "action": action, "target": target, "text": text,
           "actor": actor, "source": source}
    if action == "reply":
        out["their_text"] = "peer context"
    return out


class AutoInboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        (self.data / "policies").mkdir()
        (self.data / "policies" / "engagement-cycle.json").write_text(json.dumps({
            "inbox": {"auto_collect": True}}), encoding="utf-8")
        self.queue = self.data / "queue.json"

    def collect_report(self, items):
        return {"items": items, "errors": {},
                "coverage": inbox_replies.capabilities()}

    def test_real_wave_collects_before_planning_and_sends_ready_inbox_first(self):
        incoming = row("x", "reply", "https://x.com/a/status/1",
                       text="specific answer", actor="a", source="inbox")
        incoming["their_text"] = "a concrete question"
        self.queue.write_text(json.dumps([row("x", "publish", text="regular")]),
                              encoding="utf-8")
        calls = []
        fake = {"ok": True, "result": "success", "ledger_result": "committed"}

        def execute(**kwargs):
            calls.append(kwargs)
            return fake

        with mock.patch.object(cycle, "_now", return_value=NOW), \
                mock.patch.object(cycle, "collect_inbox_report",
                                  return_value=self.collect_report([incoming])) as collect, \
                mock.patch.object(cycle.time, "sleep"), \
                mock.patch("core.action_runner.run_action", side_effect=execute):
            got = cycle.run(self.data, go=True, queue_path=self.queue,
                            wave_id="2205", log=lambda *_: None)
        collect.assert_called_once()
        self.assertEqual(calls[0]["target"], "https://x.com/a/status/1")
        self.assertEqual(got["inbox"]["collected"], 1)
        self.assertEqual(got["inbox"]["pending_text"], 0)
        self.assertEqual(got["committed"], 2)

    def test_new_unwritten_inbox_is_recorded_but_never_auto_sent(self):
        incoming = row("github", "reply", "a/b#1", text="", actor="a", source="inbox")
        incoming["their_text"] = "a concrete question"
        self.queue.write_text(json.dumps([row("x", "publish", text="regular")]),
                              encoding="utf-8")
        fake = {"ok": True, "result": "success", "ledger_result": "committed"}
        with mock.patch.object(cycle, "_now", return_value=NOW), \
                mock.patch.object(cycle, "collect_inbox_report",
                                  return_value=self.collect_report([incoming])), \
                mock.patch("core.action_runner.run_action", return_value=fake) as execute:
            got = cycle.run(self.data, go=True, queue_path=self.queue,
                            wave_id="2205", log=lambda *_: None)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.kwargs["channel"], "x")
        self.assertEqual(got["inbox"]["pending_text"], 1)
        left = json.loads(self.queue.read_text(encoding="utf-8"))
        self.assertEqual(len(left), 1)
        self.assertEqual(left[0]["their_text"], "a concrete question")

    def test_inbox_writer_can_fill_only_collected_rows_before_planning(self):
        incoming = row("x", "reply", "https://x.com/a/status/1",
                       text="", actor="a", source="inbox")
        incoming["their_text"] = "question"
        self.queue.write_text(json.dumps([row("x", "publish", text="regular")]),
                              encoding="utf-8")
        seen = []

        def writer(pending):
            seen.extend(pending)
            return [dict(incoming, text="specific answer")]

        fake = {"ok": True, "result": "success", "ledger_result": "committed"}
        with mock.patch.object(cycle, "_now", return_value=NOW), \
                mock.patch.object(cycle, "collect_inbox_report",
                                  return_value=self.collect_report([incoming])), \
                mock.patch.object(cycle.time, "sleep"), \
                mock.patch("core.action_runner.run_action", return_value=fake) as execute:
            got = cycle.run(self.data, go=True, queue_path=self.queue,
                            inbox_writer=writer, log=lambda *_: None)
        self.assertEqual(len(seen), 1)
        self.assertEqual(got["inbox"]["written"], 1)
        self.assertEqual(got["inbox"]["pending_text"], 0)
        self.assertEqual(execute.call_args_list[0].kwargs["target"],
                         "https://x.com/a/status/1")

    def test_dry_run_does_not_collect_or_merge(self):
        before = [row("x", "publish", text="regular")]
        self.queue.write_text(json.dumps(before), encoding="utf-8")
        with mock.patch.object(cycle, "_now", return_value=NOW), \
                mock.patch.object(cycle, "collect_inbox_report") as collect:
            got = cycle.run(self.data, go=False, queue_path=self.queue,
                            log=lambda *_: None)
        collect.assert_not_called()
        self.assertFalse(got["inbox"]["auto_collect"])
        self.assertEqual(json.loads(self.queue.read_text(encoding="utf-8")), before)

    def test_cli_override_disables_policy_auto_collection(self):
        self.queue.write_text(json.dumps([row("x", "publish")]), encoding="utf-8")
        fake = {"ok": True, "result": "success", "ledger_result": "committed"}
        with mock.patch.object(cycle, "_now", return_value=NOW), \
                mock.patch.object(cycle, "collect_inbox_report") as collect, \
                mock.patch("core.action_runner.run_action", return_value=fake):
            got = cycle.run(self.data, go=True, queue_path=self.queue,
                            auto_inbox=False, log=lambda *_: None)
        collect.assert_not_called()
        self.assertFalse(got["inbox"]["auto_collect"])


    def test_received_private_messages_are_reported_but_not_queued(self):
        dm = {"platform": "x", "action": "message",
              "thread_ref": "https://x.com/i/chat/111-222",
              "target": "https://x.com/i/chat/111-222", "author": "Dana",
              "their_text": "about your reply",
              "capability": "direct_message_collect_only", "sendable": False}
        self.queue.write_text(json.dumps([]), encoding="utf-8")
        report = self.collect_report([])
        report["dm"] = [dm]
        with mock.patch.object(cycle, "_now", return_value=NOW), \
                mock.patch.object(cycle, "collect_inbox_report", return_value=report):
            got = cycle.run(self.data, go=True, queue_path=self.queue,
                            wave_id="2205", log=lambda *_: None)
        self.assertEqual(got["inbox"]["dm_count"], 1)
        self.assertEqual(got["inbox"]["dm_pending"][0]["thread_ref"],
                         "https://x.com/i/chat/111-222")
        self.assertEqual(json.loads(self.queue.read_text(encoding="utf-8")), [])


class CoverageTest(unittest.TestCase):
    def test_coverage_reports_discord_reply_unverified_and_private_messages_collect_only(self):
        got = inbox_replies.capabilities()
        self.assertTrue(got["x"]["native_reply"])
        self.assertEqual(got["x"]["direct_message"], "collect_only")
        self.assertTrue(got["github"]["issue_comment"])
        self.assertEqual(got["github"]["direct_message"], "not_applicable")
        self.assertTrue(got["discord"]["channel_post_mention"])
        self.assertTrue(got["discord"]["native_reply"])
        self.assertFalse(got["discord"]["native_reply_verified"])
        self.assertEqual(got["discord"]["direct_message"], "collect_only")


class GithubContextTest(unittest.TestCase):
    def test_latest_comment_body_and_author_replace_subject_only_context(self):
        notification = {
            "reason": "comment", "updated_at": "2026-11-21T13:00:00Z",
            "repository": {"owner": {"login": "repo-owner"}},
            "subject": {
                "title": "Cost accounting", "url": "https://api.github.com/repos/a/b/issues/7",
                "latest_comment_url": "https://api.github.com/repos/a/b/issues/comments/99",
            },
        }

        def api(args, stdin=None):
            if "notifications?all=false&per_page=50" in args:
                return {"ok": True, "out": json.dumps([notification])}
            return {"ok": True, "out": json.dumps({
                "body": "How should cached tokens be counted?",
                "user": {"login": "actual-commenter"},
                "updated_at": "2026-11-21T13:01:00Z",
                "html_url": "https://github.com/a/b/issues/7#issuecomment-99",
            })}

        with mock.patch.object(inbox_replies.gh_post, "run", side_effect=api), \
                mock.patch.object(inbox_replies, "_now", return_value=NOW):
            got = inbox_replies.collect_github("")
        self.assertEqual(got[0]["author"], "actual-commenter")
        self.assertEqual(got[0]["their_text"], "How should cached tokens be counted?")
        self.assertEqual(got[0]["thread_ref"],
                         "https://github.com/a/b/issues/7#issuecomment-99")
        self.assertEqual(got[0]["target"], "a/b#7")

    def test_notification_without_comment_author_falls_back_to_repo_owner(self):
        notification = {
            "reason": "mention", "updated_at": "2026-11-21T13:00:00Z",
            "repository": {"owner": {"login": "repo-owner"}},
            "subject": {"title": "Ping", "url": "https://api.github.com/repos/a/b/issues/7"},
        }

        def api(args, stdin=None):
            return {"ok": True, "out": json.dumps([notification])}

        with mock.patch.object(inbox_replies.gh_post, "run", side_effect=api), \
                mock.patch.object(inbox_replies, "_now", return_value=NOW):
            got = inbox_replies.collect_github("")
        self.assertEqual(got[0]["author"], "repo-owner")
        self.assertTrue(got[0]["context_incomplete"])

    def test_notification_payload_that_is_not_a_list_returns_empty(self):
        with mock.patch.object(inbox_replies.gh_post, "run",
                               return_value={"ok": True,
                                             "out": json.dumps({"message": "Bad credentials"})}), \
                mock.patch.object(inbox_replies, "_now", return_value=NOW):
            self.assertEqual(inbox_replies.collect_github(""), [])

    def test_non_dict_notification_rows_are_skipped(self):
        with mock.patch.object(inbox_replies.gh_post, "run",
                               return_value={"ok": True,
                                             "out": json.dumps(["junk", None, 7])}), \
                mock.patch.object(inbox_replies, "_now", return_value=NOW):
            self.assertEqual(inbox_replies.collect_github(""), [])

    def test_string_repository_and_user_do_not_crash_collection(self):
        notification = {
            "reason": "mention", "updated_at": "2026-11-21T13:00:00Z",
            "repository": "oops",
            "subject": {"title": "Ping", "url": "https://api.github.com/repos/a/b/issues/7",
                        "latest_comment_url":
                            "https://api.github.com/repos/a/b/issues/comments/99"},
        }

        def api(args, stdin=None):
            if "notifications?all=false&per_page=50" in args:
                return {"ok": True, "out": json.dumps([notification])}
            return {"ok": True, "out": json.dumps({
                "body": "context", "user": "oops",
                "html_url": "https://github.com/a/b/issues/7#issuecomment-99"})}

        with mock.patch.object(inbox_replies.gh_post, "run", side_effect=api), \
                mock.patch.object(inbox_replies, "_now", return_value=NOW):
            got = inbox_replies.collect_github("")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["author"], "")
        self.assertEqual(got[0]["target"], "a/b#7")


class XScrollCollectTest(unittest.TestCase):
    def test_collect_x_scrolls_and_merges_unique_statuses(self):
        one = {"url": "https://x.com/a/status/1", "handle": "a", "text": "one",
               "received_at": "2026-11-21T13:00:00Z"}
        two = {"url": "https://x.com/b/status/2", "handle": "b", "text": "two",
               "received_at": "2026-11-21T12:00:00Z"}
        screens = [{"items": [one]}, {"items": [one, two]}, {}, {}, {}]
        state = {"i": 0, "scrolls": 0}

        def evaluate(tab, code):
            if code == inbox_replies._JS_X_SCROLL:
                state["scrolls"] += 1
                return {}
            item = screens[min(state["i"], len(screens) - 1)]
            state["i"] += 1
            return item

        attach = mock.Mock()
        attach.ensure_tab.return_value = 7
        attach.eval.side_effect = evaluate
        with mock.patch.object(inbox_replies.wb, "nav"), \
                mock.patch.object(inbox_replies.wb.time, "sleep"), \
                mock.patch.object(inbox_replies, "_now", return_value=NOW):
            got = inbox_replies.collect_x(attach, "roy")
        self.assertEqual([r["target"] for r in got],
                         ["https://x.com/a/status/1", "https://x.com/b/status/2"])
        self.assertEqual(state["scrolls"], 3)
        self.assertEqual(len(got), len({r["target"] for r in got}))


class XDirectMessageTest(unittest.TestCase):
    def test_conversation_list_is_collected_but_never_sendable(self):
        payload = {"ready": True, "requests": "私信请求 2",
                   "items": [
                       {"href": "/i/chat/111-222", "author": "Dana",
                        "text": "about your reply", "received_at": "2026-11-21T13:00:00Z"},
                       {"href": "/i/chat/111-222", "author": "Dana", "text": "duplicate"},
                       {"href": "/i/chat/requests", "author": "stranger", "text": "ignore"},
                       {"href": "/i/chat/333-444", "author": "Me", "text": "你: thanks"},
                   ]}
        attach = mock.Mock()
        attach.ensure_tab.return_value = 7
        attach.eval.return_value = payload
        with mock.patch.object(inbox_replies.wb, "nav") as nav, \
                mock.patch.object(inbox_replies.wb.time, "sleep"):
            got = inbox_replies.collect_x_dms(attach, log=lambda *_: None)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0]["thread_ref"], "https://x.com/i/chat/111-222")
        self.assertEqual(got[0]["action"], "message")
        self.assertEqual(got[0]["capability"], "direct_message_collect_only")
        self.assertFalse(got[0]["sendable"])
        self.assertTrue(got[0]["pending_reply"])
        self.assertEqual(got[0]["their_text"], "about your reply")
        self.assertEqual(got[1]["thread_ref"], "https://x.com/i/chat/333-444")
        self.assertFalse(got[1]["pending_reply"])
        self.assertTrue(got[1]["last_from_me"])
        self.assertEqual(got[1]["their_text"], "")
        self.assertEqual([call.args[1] for call in nav.call_args_list],
                         ["https://x.com/i/chat",
                          "https://x.com/notifications/mentions"])

    def test_dm_failure_is_reported_separately_and_does_not_block_replies(self):
        attach = mock.Mock()
        attach.observe_identity.return_value = {"handle": "roy"}
        attach.ensure_tab.return_value = 7
        with mock.patch.object(inbox_replies, "collect_x", return_value=[]), \
                mock.patch.object(inbox_replies, "collect_github", return_value=[]), \
                mock.patch.object(inbox_replies, "collect_discord", return_value=[]), \
                mock.patch.object(inbox_replies, "collect_x_dms",
                                  side_effect=RuntimeError("shadow dom gone")):
            report = inbox_replies.collect_detailed(".", attach=attach,
                                                    log=lambda *_: None)
        self.assertEqual(report["items"],
                         {"x": [], "github": [], "discord": [], "dm": []})
        self.assertIn("x_dm", report["errors"])
        self.assertNotIn("x", report["errors"])


class GonePostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        (self.data / "policies").mkdir()
        (self.data / "policies" / "engagement-cycle.json").write_text(json.dumps({
            "inbox": {"auto_collect": False}}), encoding="utf-8")
        self.queue = self.data / "queue.json"

    def test_gone_post_leaves_queue_instead_of_retrying_every_wave(self):
        self.queue.write_text(json.dumps([
            row("x", "reply", "https://x.com/a/status/1", text="answer", actor="a")]),
            encoding="utf-8")
        fake = {"ok": False, "result": "failed", "ledger_result": "failed",
                "engine": {"err": "post gone"}}
        with mock.patch.object(cycle, "_now", return_value=NOW), \
                mock.patch("core.action_runner.run_action", return_value=fake):
            got = cycle.run(self.data, go=True, queue_path=self.queue,
                            auto_inbox=False, wave_id="2205", log=lambda *_: None)
        self.assertEqual(got["gone"], 1)
        self.assertEqual(json.loads(self.queue.read_text(encoding="utf-8")), [])


if __name__ == "__main__":
    unittest.main()
