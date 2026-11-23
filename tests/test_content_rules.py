#!/usr/bin/env python3
"""内容规则：外链位置、入站上下文、集中目标排序与读数口径。

只做纯计算；不打开浏览器、不发真实消息。
"""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import content_rules
from core import engagement_cycle as cycle
from core.ledger import Ledger

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 11, 22, 20, 5, tzinfo=TZ)


def row(channel, action, target="", text="t", actor="", source="queue", **extra):
    out = {"channel": channel, "action": action, "target": target, "text": text,
           "actor": actor, "source": source, "result": "committed",
           "transport": "browser-attach"}
    out.update(extra)
    return out


class ContentRuleTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.ledger = Ledger(self.data / "ledger.jsonl")
        self.policy = json.loads(json.dumps(cycle.DEFAULT_POLICY))
        self.policy["content_rules"] = {"focus": {"targets_file": "state/focus-targets.json"}}
        state = self.data / "state"
        state.mkdir(parents=True)
        (state / "focus-targets.json").write_text(json.dumps({
            "targets": [
                {"channel": "x", "key": "marginsystems", "why": "open question"},
                {"channel": "github", "key": "DanielH2018/dotfiles", "why": "open thread"},
            ]
        }), encoding="utf-8")

    def plan(self, rows):
        return cycle.plan(rows, self.policy, ledger=self.ledger, now=NOW,
                          data_dir=self.data)

    def test_x_publish_with_link_is_rejected(self):
        bad = row("x", "publish", text="read https://github.com/roy-tong/Verovo")
        good = row("x", "publish", text="no link here")
        got = self.plan([bad, good])
        self.assertEqual([r["text"] for r in got["scheduled"]], ["no link here"])
        self.assertEqual([s["reason"] for s in got["skipped"]], ["x_link_in_body"])

    def test_inbox_reply_without_context_is_rejected(self):
        bad = row("x", "reply", "https://x.com/a/status/1", actor="a", source="inbox")
        got = self.plan([bad])
        self.assertEqual(got["scheduled"], [])
        self.assertEqual([s["reason"] for s in got["skipped"]], ["reply_context_missing"])

    def test_inbox_reply_with_context_passes(self):
        good = row("x", "reply", "https://x.com/a/status/1", actor="a", source="inbox",
                   their_text="how do you count it?")
        got = self.plan([good])
        self.assertEqual(len(got["scheduled"]), 1)
        self.assertEqual(got["skipped"], [])

    def test_non_inbox_reply_without_context_is_rejected(self):
        bad = row("x", "reply", "https://x.com/a/status/1", actor="a", source="queue")
        got = self.plan([bad])
        self.assertEqual(got["scheduled"], [])
        self.assertEqual([s["reason"] for s in got["skipped"]], ["reply_context_missing"])

    def test_incomplete_context_is_not_accepted_as_context(self):
        bad = row("x", "reply", "https://x.com/a/status/1", actor="a", source="inbox",
                  their_text="only a fragment", context_incomplete=True)
        got = self.plan([bad])
        self.assertEqual(got["scheduled"], [])
        self.assertEqual([s["reason"] for s in got["skipped"]], ["reply_context_incomplete"])

    def test_focus_replies_are_ordered_first(self):
        other = row("x", "reply", "https://x.com/b/status/2", actor="b", source="inbox",
                    their_text="hi")
        focus = row("x", "reply", "https://x.com/marginsystems/status/3",
                    actor="marginsystems", source="inbox", their_text="track it monthly?")
        got = self.plan([other, focus])
        self.assertEqual(got["scheduled"][0]["actor"], "marginsystems")

    def test_focus_share_counts_reply_rows_only(self):
        replies = [
            row("x", "reply", "https://x.com/marginsystems/status/1",
                actor="marginsystems", source="inbox", their_text="q"),
            row("x", "reply", "https://x.com/stranger/status/2",
                actor="stranger", source="inbox", their_text="q"),
        ]
        posts = [row("x", "publish", text="p%d" % i) for i in range(8)]
        summary = content_rules.summarize(replies + posts, self.policy,
                                          data_dir=self.data)
        self.assertEqual(summary["focus_actions"], 2)
        self.assertEqual(summary["focus_hit"], 1)
        self.assertEqual(summary["focus_share"], 0.5)
        self.assertTrue(summary["focus_floor_met"])
        self.assertIn("x:stranger", summary["focus_missing"])

    def test_link_in_first_self_reply_is_not_a_publish_row(self):
        reply = row("x", "reply", "https://x.com/me/status/9",
                    text="details: https://github.com/roy-tong/Verovo",
                    their_text="original post")
        self.assertEqual(content_rules.check_row(reply, self.policy), "")
        self.assertEqual(len(content_rules.artifact_links(reply["text"])), 1)

    def test_x_publish_artifact_counts_named_product_without_link(self):
        named = row("x", "publish", text="Verovo checks an AI bill before renewal")
        plain = row("x", "publish", text="a quiet night of shipping")
        summary = content_rules.summarize([named, plain], self.policy,
                                          data_dir=self.data)
        self.assertEqual(summary["x_publish_total"], 2)
        self.assertEqual(summary["x_publish_with_artifact"], 1)
        self.assertTrue(summary["x_publish_artifact_floor_met"])
        self.assertTrue(content_rules.publish_has_artifact("Growovo logged every step"))
        self.assertFalse(content_rules.publish_has_artifact(plain["text"]))

    def test_x_publish_link_still_rejected_while_named(self):
        bad = row("x", "publish", text="Verovo https://github.com/roy-tong/Verovo")
        got = self.plan([bad])
        self.assertEqual(got["scheduled"], [])
        self.assertEqual([s["reason"] for s in got["skipped"]], ["x_link_in_body"])


if __name__ == "__main__":
    unittest.main()
