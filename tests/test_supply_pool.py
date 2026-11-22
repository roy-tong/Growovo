#!/usr/bin/env python3
"""候选储备与挖掘：纯离线用例。

这些用例只读写临时目录，不碰浏览器、网络、gh CLI 或真实账号。
"""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import supply_mine
from core import supply_pool
from core.ledger import Ledger

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 11, 21, 12, 0, tzinfo=TZ)


def at(text):
    got = dt.datetime.fromisoformat(text)
    return got.replace(tzinfo=TZ) if got.tzinfo is None else got.astimezone(TZ)


class NormalizeTest(unittest.TestCase):
    def test_reply_without_target_rejected(self):
        with self.assertRaises(supply_pool.PoolError):
            supply_pool.normalize({"channel": "x", "action": "reply",
                                   "context": "hello"}, now=NOW)

    def test_publish_without_target_requires_key(self):
        with self.assertRaises(supply_pool.PoolError):
            supply_pool.normalize({"channel": "x", "action": "publish",
                                   "context": "hello"}, now=NOW)
        row = supply_pool.normalize({"channel": "x", "action": "publish",
                                     "key": "content:x:publish:a",
                                     "context": "hello"}, now=NOW)
        self.assertEqual(row["key"], "content:x:publish:a")

    def test_empty_context_and_title_rejected(self):
        with self.assertRaises(supply_pool.PoolError):
            supply_pool.normalize({"channel": "github", "action": "reply",
                                   "target": "https://github.com/a/b/issues/1"},
                                  now=NOW)

    def test_unknown_channel_and_status_rejected(self):
        with self.assertRaises(supply_pool.PoolError):
            supply_pool.normalize({"channel": "email", "action": "reply",
                                   "target": "x", "context": "y"}, now=NOW)
        with self.assertRaises(supply_pool.PoolError):
            supply_pool.normalize({"channel": "x", "action": "reply",
                                   "target": "https://x.com/a/status/1",
                                   "context": "y", "status": "queued"}, now=NOW)


class CandidateKeyTest(unittest.TestCase):
    def test_github_url(self):
        key = supply_pool.candidate_key(
            {"channel": "github", "action": "reply",
             "target": "https://github.com/acme/tool/issues/42"})
        self.assertEqual(key, "github:acme/tool#42")

    def test_x_url(self):
        key = supply_pool.candidate_key(
            {"channel": "x", "action": "reply",
             "target": "https://x.com/someone/status/123456"})
        self.assertEqual(key, "x:someone/123456")

    def test_explicit_key_wins(self):
        key = supply_pool.candidate_key(
            {"channel": "discord", "action": "publish", "key": "content:discord:publish:a",
             "target": "1573"})
        self.assertEqual(key, "content:discord:publish:a")


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_merge_dedupes_and_keeps_status(self):
        row = {"channel": "github", "action": "reply",
               "target": "https://github.com/acme/tool/issues/7",
               "title": "How do you track token cost", "context": "body"}
        first = supply_pool.merge(self.data, [row], now=NOW)
        self.assertEqual(first["added"], 1)
        supply_pool.mark(self.data, "github:acme/tool#7", "used", now=NOW)
        second = supply_pool.merge(self.data, [dict(row, title="updated title")], now=NOW)
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(second["added"], 0)
        item = supply_pool.load(self.data)["items"]["github:acme/tool#7"]
        self.assertEqual(item["status"], "used")
        self.assertEqual(item["title"], "updated title")

    def test_merge_skips_touched_reply_targets(self):
        ledger = Ledger(self.data / "ledger.jsonl")
        ledger.append(channel="github", action="reply",
                      target="https://github.com/acme/tool/issues/7",
                      result="committed")
        got = supply_pool.merge(self.data, [{
            "channel": "github", "action": "reply",
            "target": "https://github.com/acme/tool/issues/7",
            "title": "t", "context": "c"}], ledger=ledger, now=NOW)
        self.assertEqual(got["excluded_touched"], 1)
        self.assertEqual(got["added"], 0)

    def test_merge_allows_repeat_publish_to_same_channel(self):
        ledger = Ledger(self.data / "ledger.jsonl")
        ledger.append(channel="discord", action="publish",
                      target="1573250833871536161", result="committed")
        got = supply_pool.merge(self.data, [{
            "channel": "discord", "action": "publish",
            "key": "content:discord:publish:a", "target": "1573250833871536161",
            "title": "t", "context": "c"}], ledger=ledger, now=NOW)
        self.assertEqual(got["excluded_touched"], 0)
        self.assertEqual(got["added"], 1)

    def test_rejected_rows_are_reported_not_stored(self):
        got = supply_pool.merge(self.data, [
            {"channel": "x", "action": "reply", "context": "no target"},
            {"channel": "x", "action": "reply",
             "target": "https://x.com/a/status/1", "context": "ok"},
        ], now=NOW)
        self.assertEqual(got["added"], 1)
        self.assertEqual(len(got["rejected"]), 1)
        self.assertIn("target", got["rejected"][0]["reason"])

    def test_pool_file_is_valid_json_after_write(self):
        supply_pool.merge(self.data, [{
            "channel": "x", "action": "reply",
            "target": "https://x.com/a/status/1", "context": "c"}], now=NOW)
        raw = (self.data / "state" / "supply-pool.json").read_text(encoding="utf-8")
        payload = json.loads(raw)
        self.assertEqual(payload["version"], supply_pool.VERSION)
        self.assertEqual(len(payload["items"]), 1)


class PickMarkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        supply_pool.merge(self.data, [
            {"channel": "x", "action": "reply", "target": "https://x.com/a/status/1",
             "context": "low", "score": 10},
            {"channel": "x", "action": "reply", "target": "https://x.com/b/status/2",
             "context": "high", "score": 80},
            {"channel": "x", "action": "reply", "target": "https://x.com/c/status/3",
             "context": "mid", "score": 40},
        ], now=NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pick_sorts_by_score_desc(self):
        rows = supply_pool.pick(self.data, "x", "reply", 2)
        self.assertEqual([row["score"] for row in rows], [80, 40])

    def test_pick_respects_min_score_and_exclude(self):
        rows = supply_pool.pick(self.data, "x", "reply", 5, min_score=40,
                                exclude=["x:b/2"])
        self.assertEqual([row["score"] for row in rows], [40])

    def test_pick_skips_non_new(self):
        supply_pool.mark(self.data, "x:b/2", "used", now=NOW)
        rows = supply_pool.pick(self.data, "x", "reply", 5)
        self.assertEqual([row["score"] for row in rows], [40, 10])

    def test_mark_unknown_key_raises(self):
        with self.assertRaises(supply_pool.PoolError):
            supply_pool.mark(self.data, "x:nope/0", "used", now=NOW)

    def test_mark_rejects_unknown_status(self):
        with self.assertRaises(supply_pool.PoolError):
            supply_pool.mark(self.data, "x:a/1", "queued", now=NOW)


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.policy = {
            "window": {"waves_per_night": 9},
            "supply": {"wave_targets": {"x/publish": 10, "x/reply": 20,
                                        "github/reply": 15, "discord/publish": 6}},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_need_and_depth(self):
        supply_pool.merge(self.data, [
            {"channel": "github", "action": "reply",
             "target": "https://github.com/acme/tool/issues/%d" % i,
             "context": "c", "score": 50} for i in range(1, 28)
        ], now=NOW)
        got = supply_pool.stats(self.data, self.policy, nights=3, now=NOW)
        by = {(row["channel"], row["action"]): row for row in got["channels"]}
        self.assertEqual(by[("github", "reply")]["need_per_night"], 135)
        self.assertEqual(by[("github", "reply")]["need_total"], 405)
        self.assertEqual(by[("github", "reply")]["available"], 27)
        self.assertEqual(by[("github", "reply")]["shortfall"], 378)
        self.assertEqual(by[("github", "reply")]["depth_nights"], 0.2)
        self.assertEqual(got["totals"]["need_total"], 1377)
        self.assertEqual(got["totals"]["shortfall"], 1350)

    def test_stats_counts_statuses(self):
        supply_pool.merge(self.data, [{
            "channel": "x", "action": "reply",
            "target": "https://x.com/a/status/1", "context": "c"}], now=NOW)
        supply_pool.mark(self.data, "x:a/1", "used", now=NOW)
        got = supply_pool.stats(self.data, self.policy, nights=1, now=NOW)
        row = [r for r in got["channels"] if r["channel"] == "x" and r["action"] == "reply"][0]
        self.assertEqual(row["available"], 0)
        self.assertEqual(row["used"], 1)


class KocTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_merge_accumulates_hits_without_overwriting_engagement(self):
        supply_pool.koc_merge(self.data, [
            {"handle": "@alice", "followers": 4200, "bio": "builds devtools"},
            {"handle": "bob"},
        ], now=NOW)
        supply_pool.koc_merge(self.data, [{"handle": "alice"}], now=NOW)
        got = supply_pool.koc_load(self.data)["accounts"]
        self.assertEqual(got["alice"]["hits"], 2)
        self.assertEqual(got["alice"]["followers"], 4200)
        self.assertEqual(got["bob"]["hits"], 1)

    def test_stats_counts_engaged(self):
        supply_pool.koc_merge(self.data, [{"handle": "alice"}, {"handle": "bob"}], now=NOW)
        registry = supply_pool.koc_load(self.data)
        registry["accounts"]["alice"]["touches"] = 2
        supply_pool.koc_save(self.data, registry)
        got = supply_pool.koc_stats(self.data)
        self.assertEqual(got["accounts"], 2)
        self.assertEqual(got["engaged"], 1)
        self.assertEqual(got["untouched"], 1)


class GhMineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_queries_carry_window_and_issue_filters(self):
        got = supply_mine.gh_queries(14, today=dt.date(2026, 11, 21))
        self.assertEqual(len(got), len(supply_mine.GH_TOPICS))
        self.assertTrue(all("is:issue" in q and "is:open" in q for q in got))
        self.assertTrue(all("created:>2026-11-07" in q for q in got))

    def test_mine_filters_pr_self_and_promo(self):
        items = [
            {"repo": "acme/tool", "number": 1, "title": "Track LLM cost per task",
             "url": "https://github.com/acme/tool/issues/1", "comments": 1,
             "created_at": "2026-11-20T00:00:00Z", "author": "dev",
             "body_len": 20, "body": "we need token accounting", "is_pr": False},
            {"repo": "acme/tool", "number": 2, "title": "PR: add cost export",
             "url": "https://github.com/acme/tool/pull/2", "comments": 0,
             "created_at": "2026-11-20T00:00:00Z", "author": "dev",
             "body_len": 10, "body": "diff", "is_pr": True},
            {"repo": "roy-tong/Growovo", "number": 3, "title": "own repo",
             "url": "https://github.com/roy-tong/Growovo/issues/3", "comments": 0,
             "created_at": "2026-11-20T00:00:00Z", "author": "roy-tong",
             "body_len": 10, "body": "self", "is_pr": False},
            {"repo": "acme/tool", "number": 4, "title": "Buy followers now",
             "url": "https://github.com/acme/tool/issues/4", "comments": 0,
             "created_at": "2026-11-20T00:00:00Z", "author": "spam",
             "body_len": 10, "body": "best growth hack dm me", "is_pr": False},
        ]
        calls = []

        def fake_search(query, limit):
            calls.append((query, limit))
            return {"ok": True, "total": len(items), "items": items}

        got = supply_mine.mine_github(
            self.data, queries=["is:issue test"], per_query=20, gap=0,
            profile=None, sleep=lambda seconds: None, search=fake_search, now=NOW)
        self.assertTrue(got["ok"])
        self.assertEqual(got["rows"], 1)
        self.assertEqual(got["queries"][0]["skipped_pr"], 1)
        stored = supply_pool.load(self.data)["items"]
        self.assertIn("github:acme/tool#1", stored)
        self.assertNotIn("github:acme/tool#2", stored)
        self.assertNotIn("github:roy-tong/Growovo#3", stored)
        self.assertEqual(calls, [("is:issue test", 20)])

    def test_mine_reports_rate_limit_without_crashing(self):
        def fake_search(query, limit):
            return {"ok": False, "err": "API rate limit exceeded"}

        got = supply_mine.mine_github(
            self.data, queries=["q"], gap=0, sleep=lambda seconds: None,
            search=fake_search, now=NOW)
        self.assertFalse(got["queries"][0]["ok"])
        self.assertIn("rate limit", got["queries"][0]["error"])
        self.assertEqual(got["rows"], 0)


class XRowsTest(unittest.TestCase):
    def test_filters_empty_and_promo(self):
        rows = supply_mine.x_rows([
            {"handle": "alice", "url": "https://x.com/alice/status/1",
             "text": "how do you track llm cost per request", "at": "2026-11-20T10:00:00Z",
             "replies": 3, "likes": 12},
            {"handle": "", "url": "https://x.com/a/status/2", "text": "no handle"},
            {"handle": "spam", "url": "https://x.com/spam/status/3",
             "text": "best growth hack, dm me for details"},
        ], "llm cost")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target"], "https://x.com/alice/status/1")
        self.assertEqual(rows[0]["engagement"]["likes"], 12)

    def test_drops_crypto_noise(self):
        """X 上 "token" 多半指代币；只有 AI 侧词才算候选。"""
        rows = supply_mine.x_rows([
            {"handle": "coin", "url": "https://x.com/coin/status/1",
             "text": "Transparent fee accounting will be essential if agent activity "
                     "supports long-term token utility."},
            {"handle": "trader", "url": "https://x.com/trader/status/2",
             "text": "nasdaq plunged and nvidia lost 14% on friday"},
            {"handle": "dev", "url": "https://x.com/dev/status/3",
             "text": "our inference bill doubled; now we track token usage per request"},
        ], "token accounting")
        self.assertEqual([row["actor"] for row in rows], ["dev"])

    def test_on_topic_gate_cases(self):
        self.assertTrue(supply_mine.x_is_on_topic("Prompt caching cut our API bill by 40%"))
        self.assertTrue(supply_mine.x_is_on_topic("Run llm evals before shipping prompts"))
        self.assertFalse(supply_mine.x_is_on_topic("stablecoin decimals matter more than users think"))
        self.assertFalse(supply_mine.x_is_on_topic(""))


class ContentAngleTest(unittest.TestCase):
    def test_keys_stable_and_discord_uses_allowed_ids(self):
        rows = supply_mine.content_angles(discord_ids=["111", "222"])
        keys = [row["key"] for row in rows]
        self.assertEqual(len(keys), len(set(keys)))
        discord = [row for row in rows if row["channel"] == "discord"]
        self.assertEqual({row["target"] for row in discord}, {"111", "222"})
        self.assertTrue(all("channel-id-unverified" in row["tags"] for row in discord))

    def test_no_discord_rows_without_allowed_ids(self):
        rows = supply_mine.content_angles(discord_ids=[])
        self.assertFalse([row for row in rows if row["channel"] == "discord"])

    def test_mine_content_merges_into_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            policy = {"channels": {"discord_allowed_ids": ["111"]}}
            got = supply_mine.mine_content(data, policy=policy, now=NOW)
            self.assertTrue(got["ok"])
            self.assertGreater(got["merge"]["added"], 0)
            stored = supply_pool.load(data)["items"]
            self.assertTrue(all("content:" in key for key in stored))


if __name__ == "__main__":
    unittest.main()


class WavePlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.policy = {"suite": "test", "platforms": {
            "x": {"publish": {"per_hour": 2}, "reply": {"per_hour": 2}},
            "github": {"reply": {"per_hour": 2}},
            "discord": {"publish": {"per_hour": 2}},
        }}

    def tearDown(self):
        self.tmp.cleanup()

    def _seed(self):
        rows = []
        for i in range(6):
            rows.append({"channel": "x", "action": "reply",
                         "target": "https://x.com/a/status/%d" % i,
                         "context": "llm cost per request question %d" % i})
        for i in range(4):
            rows.append({"channel": "github", "action": "reply",
                         "target": "https://github.com/acme/repo%d/issues/1" % i,
                         "context": "issue about token usage %d" % i})
        rows.append({"channel": "github", "action": "reply",
                     "target": "https://github.com/acme/tool/issues/9",
                     "context": "same repo second issue"})
        for cid in ("111", "222", "333"):
            rows.append({"channel": "discord", "action": "publish", "target": cid,
                         "key": "content:discord:publish:%s" % cid,
                         "title": "angle %s" % cid, "context": "angle body"})
        supply_pool.merge(self.data, rows, source="test", now=NOW)

    def test_limits_and_unique_repo_per_wave(self):
        self._seed()
        got = supply_pool.wave_plan(self.data, self.policy, wave=1, now=NOW)
        self.assertEqual(got["counts"]["github/reply"], 2)
        targets = [r["target"] for r in got["items"]["github/reply"]]
        repos = {t.split("/")[3] + "/" + t.split("/")[4] for t in targets}
        self.assertEqual(len(repos), 2)
        self.assertEqual(got["counts"]["x/reply"], 2)
        self.assertEqual(len({r["target"] for r in got["items"]["x/reply"]}), 2)
        self.assertEqual(got["counts"]["discord/publish"], 2)
        self.assertTrue(Path(got["out"]).is_file())

    def test_reports_shortfall_when_pool_thin(self):
        supply_pool.merge(self.data, [{"channel": "discord", "action": "publish",
                                       "target": "111", "key": "c:1",
                                       "title": "t", "context": "c"}],
                          source="test", now=NOW)
        got = supply_pool.wave_plan(self.data, self.policy, wave=2, now=NOW)
        self.assertEqual(got["counts"]["discord/publish"], 1)
        self.assertEqual(got["shortfall"]["discord/publish"], 1)
        self.assertEqual(got["shortfall"]["x/reply"], 2)

    def test_ignores_used_rows(self):
        self._seed()
        first = supply_pool.wave_plan(self.data, self.policy, wave=1, now=NOW)
        used_key = first["items"]["x/reply"][0]["key"]
        supply_pool.mark(self.data, used_key, "used")
        second = supply_pool.wave_plan(self.data, self.policy, wave=1, now=NOW)
        self.assertNotIn(used_key, [r["key"] for r in second["items"]["x/reply"]])
