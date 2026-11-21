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
    return {"channel": channel, "action": action, "target": target, "text": text,
            "actor": actor, "source": source}


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


class CoverageTest(unittest.TestCase):
    def test_coverage_does_not_claim_private_messages_or_discord_native_reply(self):
        got = inbox_replies.capabilities()
        self.assertTrue(got["x"]["native_reply"])
        self.assertFalse(got["x"]["direct_message"])
        self.assertTrue(got["github"]["issue_comment"])
        self.assertEqual(got["github"]["direct_message"], "not_applicable")
        self.assertTrue(got["discord"]["channel_post_mention"])
        self.assertFalse(got["discord"]["native_reply"])
        self.assertFalse(got["discord"]["direct_message"])


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


if __name__ == "__main__":
    unittest.main()
