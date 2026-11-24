#!/usr/bin/env python3
"""夜间互动循环：窗口、交错间隔、额度、对象上限与只读计划。

这些用例只做纯计算与临时账本，不接触浏览器、网络或真实账号。
"""
import datetime as dt
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import browser_attach
from core import browser_write
from core import engagement_cycle as cycle
from core import platform_compliance
from core.ledger import Ledger
from core import supply_pool

TZ = dt.timezone(dt.timedelta(hours=8))


def at(text):
    got = dt.datetime.fromisoformat(text)
    return got.replace(tzinfo=TZ) if got.tzinfo is None else got.astimezone(TZ)


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def row(channel, action, target="", text="t", actor="", source="queue", ts=None,
        result="committed", transport="browser-attach"):
    out = {"channel": channel, "action": action, "target": target, "text": text,
           "actor": actor, "source": source, "result": result, "transport": transport}
    if action == "reply":
        out.setdefault("their_text", "peer context")
    if ts:
        out["ts"] = ts
    return out


class WindowTest(unittest.TestCase):
    def test_evening_and_after_midnight_share_one_night(self):
        policy = cycle.DEFAULT_POLICY
        evening = cycle.night_start(at("2026-11-20T22:05:00+08:00"), policy)
        after = cycle.night_start(at("2026-11-21T02:00:00+08:00"), policy)
        self.assertEqual(evening.isoformat(), "2026-11-20T22:00:00+08:00")
        self.assertEqual(after.isoformat(), "2026-11-20T22:00:00+08:00")
        self.assertEqual(cycle.night_key(evening), cycle.night_key(after))

    def test_daytime_is_outside_window(self):
        self.assertIsNone(cycle.night_start(at("2026-11-20T12:00:00+08:00"),
                                           cycle.DEFAULT_POLICY))

    def test_morning_boundary_is_exclusive(self):
        self.assertIsNotNone(cycle.night_start(at("2026-11-21T06:59:00+08:00"),
                                              cycle.DEFAULT_POLICY))
        self.assertIsNone(cycle.night_start(at("2026-11-21T07:00:00+08:00"),
                                           cycle.DEFAULT_POLICY))


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.ledger = Ledger(self.data / "ledger.jsonl")
        self.now = at("2026-11-20T22:05:00+08:00")

    def plan(self, rows, **kwargs):
        policy = kwargs.pop("policy", cycle.DEFAULT_POLICY)
        return cycle.plan(rows, policy, ledger=self.ledger,
                          now=kwargs.pop("now", self.now), **kwargs)

    def test_inbox_first_and_platforms_interleaved(self):
        rows = [
            row("discord", "publish", "1573250833871536161", source="discord-daily"),
            row("x", "reply", "https://x.com/a/status/1", actor="a", source="inbox"),
            row("github", "reply", "roy-tong/Growovo#12", actor="c", source="inbox"),
            row("x", "publish", "", source="x-original"),
        ]
        got = self.plan(rows)
        seq = [(r["channel"], r["action"]) for r in got["scheduled"]]
        self.assertEqual(seq[0], ("x", "reply"))
        self.assertEqual(seq[1], ("github", "reply"))
        self.assertEqual(got["scheduled"][0]["delay_sec"], 0)
        self.assertEqual(got["scheduled"][1]["delay_sec"], 15)
        self.assertGreaterEqual(got["scheduled"][2]["delay_sec"], 90)

    def test_global_gap_keeps_any_two_actions_apart(self):
        rows = [row("github", "reply", "a/b#%d" % i, actor="u%d" % i) for i in range(5)]
        got = self.plan(rows)
        delays = [r["delay_sec"] for r in got["scheduled"]]
        self.assertEqual(delays, sorted(delays))
        for earlier, later in zip(delays, delays[1:]):
            self.assertGreaterEqual(later - earlier, 15)

    def test_same_platform_respects_min_gap(self):
        rows = [row("x", "reply", "https://x.com/a/status/%d" % i, actor="u%d" % i)
                for i in range(3)]
        got = self.plan(rows)
        delays = [r["delay_sec"] for r in got["scheduled"]]
        self.assertEqual(delays, [0, 90, 180])

    def test_actor_cap_two_per_night(self):
        write_rows(self.data / "ledger.jsonl", [
            row("github", "reply", "a/b#1", actor="sam", ts="2026-11-20T22:01:00+08:00"),
            row("github", "reply", "a/b#2", actor="sam", ts="2026-11-20T22:02:00+08:00"),
        ])
        got = self.plan([row("github", "reply", "a/b#3", actor="sam")])
        self.assertEqual(got["scheduled"], [])
        self.assertEqual([s["reason"] for s in got["skipped"]], ["actor_cap"])

    def test_own_publish_does_not_consume_actor_cap(self):
        write_rows(self.data / "ledger.jsonl", [
            row("x", "publish", actor="owner", ts="2026-11-20T22:01:00+08:00"),
            row("x", "publish", actor="owner", ts="2026-11-20T22:02:00+08:00"),
        ])
        got = self.plan([row("x", "publish", actor="owner")])
        self.assertEqual(got["skipped"], [])
        self.assertEqual(len(got["scheduled"]), 1)

    def test_hour_quota_exhausted_by_committed_rows(self):
        write_rows(self.data / "ledger.jsonl", [
            row("x", "publish", ts="2026-11-20T22:0%d:00+08:00" % i, actor="u%d" % i)
            for i in range(10)
        ])
        got = self.plan([row("x", "publish")])
        self.assertEqual([s["reason"] for s in got["skipped"]], ["hour_quota_exhausted"])

    def test_calendar_hour_allows_the_next_full_wave(self):
        policy = json.loads(json.dumps(cycle.DEFAULT_POLICY))
        policy["quota"] = {"hour_mode": "calendar"}
        write_rows(self.data / "ledger.jsonl", [
            row("x", "publish", ts="2026-11-20T22:0%d:00+08:00" % i)
            for i in range(10)
        ])
        got = self.plan([row("x", "publish")], now=at("2026-11-20T23:02:00+08:00"),
                        policy=policy)
        self.assertEqual(got["skipped"], [])
        self.assertEqual(len(got["scheduled"]), 1)
        self.assertEqual(got["usage"]["x/publish"]["night"], 10)
        self.assertEqual(got["usage"]["x/publish"]["hour"], 0)

    def test_failed_and_non_browser_rows_do_not_consume_quota(self):
        write_rows(self.data / "ledger.jsonl", [
            row("x", "publish", ts="2026-11-20T22:01:00+08:00", result="failed"),
            row("x", "publish", ts="2026-11-20T22:02:00+08:00", transport="cdp"),
            row("x", "publish", ts="2026-11-20T22:03:00+08:00", result="indeterminate"),
        ])
        got = self.plan([row("x", "publish")])
        self.assertEqual(len(got["scheduled"]), 1)

    def test_already_replied_thread_is_skipped(self):
        write_rows(self.data / "ledger.jsonl", [
            row("x", "reply", "https://x.com/a/status/1", actor="a",
                ts="2026-11-20T22:01:00+08:00"),
        ])
        got = self.plan([row("x", "reply", "https://x.com/a/status/1", actor="a")])
        self.assertEqual([s["reason"] for s in got["skipped"]], ["already_done"])

    def test_invalid_rows_rejected(self):
        policy = json.loads(json.dumps(cycle.DEFAULT_POLICY))
        policy["channels"]["discord_allowed_ids"] = ["1573250833871536161"]
        got = cycle.plan([
            row("x", "reply", ""),
            row("x", "reply", "https://x.com/a/status/2", text=""),
            row("x", "engage", "https://x.com/a/status/3"),
            row("discord", "publish", "999"),
        ], policy, ledger=self.ledger, now=self.now)
        reasons = {s["reason"] for s in got["skipped"]}
        self.assertEqual(reasons,
                         {"target_required", "text_empty", "unsupported",
                          "channel_not_allowed"})

    def test_wave_budget_rejects_late_rows(self):
        rows = [row("x", "reply", "https://x.com/a/status/%d" % i, actor="u%d" % i)
                for i in range(3)]
        got = self.plan(rows, budget_sec=100)
        self.assertEqual(len(got["scheduled"]), 2)
        self.assertEqual([s["reason"] for s in got["skipped"]], ["wave_budget"])

    def test_plan_has_no_side_effects(self):
        queue_file = self.data / "queue.json"
        rows = [row("x", "publish")]
        queue_file.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        before = queue_file.read_text(encoding="utf-8")
        self.plan(rows)
        self.assertEqual(queue_file.read_text(encoding="utf-8"), before)
        self.assertFalse((self.data / "ledger.jsonl").exists())

    def test_stale_draft_is_rejected_by_age(self):
        stale = row("x", "publish", text="old")
        stale["queued_at"] = "2026-11-20T09:00:00+08:00"
        fresh = row("x", "publish", text="new")
        fresh["queued_at"] = "2026-11-20T21:30:00+08:00"
        got = self.plan([stale, fresh])
        self.assertEqual([s["reason"] for s in got["skipped"]], ["stale_row"])
        self.assertEqual([r["text"] for r in got["scheduled"]], ["new"])

    def test_campaign_date_is_the_fallback_clock(self):
        old = row("discord", "publish", "1573250833871536161", text="old")
        old["campaign"] = "night-20261119"
        got = self.plan([old])
        self.assertEqual([s["reason"] for s in got["skipped"]], ["stale_row"])

    def test_inbox_replies_and_undated_rows_are_never_stale(self):
        inbox = row("x", "reply", "https://x.com/a/status/1", actor="a", source="inbox")
        inbox["received_at"] = "2026-11-01T00:00:00+08:00"
        undated = row("x", "publish", text="no clock")
        got = self.plan([inbox, undated])
        self.assertEqual([r["source"] for r in got["scheduled"]], ["inbox", "queue"])
        self.assertEqual(got["skipped"], [])

    def test_same_reply_target_is_deduped_even_when_text_changes(self):
        rows = [row("x", "reply", "https://x.com/a/status/1", text="first", actor="a"),
                row("x", "reply", "https://x.com/a/status/1", text="second", actor="a")]
        got = self.plan(rows)
        self.assertEqual(len(got["scheduled"]), 1)
        self.assertEqual(got["scheduled"][0]["text"], "first")
        self.assertEqual([s["reason"] for s in got["skipped"]], ["duplicate_in_queue"])

    def test_supply_gap_is_separate_from_quota(self):
        policy = json.loads(json.dumps(cycle.DEFAULT_POLICY))
        policy["supply"] = {"wave_targets": {"x/publish": 2, "discord/publish": 1}}
        got = cycle.plan([row("x", "publish")], policy, ledger=self.ledger, now=self.now)
        self.assertEqual(got["supply"]["x/publish"]["gap"], 1)
        self.assertEqual(got["supply"]["x/publish"]["queued"], 1)
        self.assertEqual(got["supply"]["discord/publish"]["gap"], 1)
        self.assertEqual(got["supply"]["discord/publish"]["queued"], 0)
        self.assertEqual(got["supply"]["x/publish"]["scheduled"], 1)

    def test_supply_effective_target_is_capped_by_daily_budget(self):
        policy = json.loads(json.dumps(cycle.DEFAULT_POLICY))
        policy["supply"] = {"wave_targets": {"x/publish": 10}}
        throttle = mock.Mock()
        throttle.remaining.return_value = 3
        throttle.policy.return_value = (45, 90, 50, 48)
        throttle.daily_used.return_value = 45
        got = cycle.plan([row("x", "publish")], policy, ledger=self.ledger,
                         now=self.now, throttle=throttle)
        supply = got["supply"]["x/publish"]

        self.assertEqual(supply["configured"], 10)
        self.assertEqual(supply["effective"], 3)
        self.assertTrue(supply["capped_by_daily_budget"])
        self.assertEqual(supply["gap"], 2)
        self.assertEqual(supply["configured_gap"], 9)
        self.assertEqual(got["budget"]["x/publish"]["day_left"], 3)

    def test_night_target_is_reduced_to_platform_daily_cap(self):
        policy = json.loads(json.dumps(cycle.DEFAULT_POLICY))
        policy["platforms"]["x"]["publish"]["per_night"] = 120
        throttle = mock.Mock()
        throttle.policy.return_value = (45, 90, 50, 48)
        throttle.daily_used.return_value = 0
        budget = cycle.budget_rows(policy, used={"night": {}}, throttle=throttle)
        row_budget = budget["x/publish"]

        self.assertEqual(row_budget["configured_per_night"], 120)
        self.assertEqual(row_budget["effective_per_night"], 48)
        self.assertTrue(row_budget["capped_by_daily_budget"])
        self.assertEqual(row_budget["night_left"], 48)

    def test_discord_same_channel_three_texts_are_all_scheduled(self):
        rows = [row("discord", "publish", "1573250833871536161", text=text)
                for text in ("a", "b", "c")]
        got = self.plan(rows)
        self.assertEqual([r["text"] for r in got["scheduled"]], ["a", "b", "c"])
        self.assertEqual(got["skipped"], [])

    def test_discord_channels_rotate_within_a_wave(self):
        channels = ["1573251023500476427", "1573251156744863786", "1573251185480306709"]
        policy = json.loads(json.dumps(cycle.DEFAULT_POLICY))
        policy["channels"]["discord_allowed_ids"] = channels
        policy["platforms"]["discord"]["publish"]["per_hour"] = 9
        rows = [row("discord", "publish", channel, text="%s-%d" % (channel[-4:], i))
                for channel in channels for i in range(4)]
        got = self.plan(rows, policy=policy)
        targets = [r["target"] for r in got["scheduled"]]
        self.assertEqual(len(targets), 9)
        self.assertEqual({channel: targets.count(channel) for channel in channels},
                         {channel: 3 for channel in channels})
        self.assertEqual(sorted(targets[:3]), sorted(channels))

    def test_isolated_target_is_skipped_before_already_done(self):
        url = "https://x.com/a/status/1"
        write_rows(self.data / "ledger.jsonl", [
            row("x", "reply", url, result="target_rejected",
                ts="2026-11-20T22:01:00+08:00"),
        ])
        got = self.plan([row("x", "reply", url, text="another try", actor="a")])
        self.assertEqual(got["scheduled"], [])
        self.assertEqual([s["reason"] for s in got["skipped"]], ["target_isolated"])

    def test_daily_quota_exhausted_skips_before_scheduling(self):
        throttle = mock.Mock()
        throttle.remaining.return_value = 0
        got = self.plan([row("x", "publish")], throttle=throttle)
        self.assertEqual(got["scheduled"], [])
        self.assertEqual([s["reason"] for s in got["skipped"]],
                         ["daily_quota_exhausted"])

    def test_enqueued_rows_get_stable_id_and_dedupe(self):
        queue_file = self.data / "state" / "queue.json"
        first = cycle.enqueue(self.data, [row("x", "publish", text="hello")],
                              queue_path=queue_file, now=self.now)
        again = cycle.enqueue(self.data, [row("x", "publish", text="hello")],
                              queue_path=queue_file, now=self.now)
        saved = cycle.load_queue(queue_file)

        self.assertEqual(first["added"], 1)
        self.assertEqual(again["added"], 0)
        self.assertEqual(len(again["duplicates"]), 1)
        self.assertTrue(saved[0]["id"].startswith("q:"))
        self.assertEqual(len(saved), 1)

    def test_enqueue_accepts_discord_reply_target_and_checks_its_channel(self):
        queue_file = self.data / "state" / "discord-reply.json"
        good = row("discord", "reply", "111:222", text="answer")
        good["their_text"] = "question"
        got = cycle.enqueue(self.data, [good], queue_path=queue_file, now=self.now)
        self.assertEqual(got["added"], 1, got)
        self.assertEqual(got["rejected"], [])

    def test_legacy_rows_without_id_get_one_on_load(self):
        queue_file = self.data / "legacy.json"
        queue_file.write_text(json.dumps([row("x", "reply")], ensure_ascii=False),
                              encoding="utf-8")

        loaded = cycle.load_queue(queue_file)

        self.assertTrue(loaded[0]["id"].startswith("q:"))

    def test_reply_id_does_not_drift_when_text_is_rewritten(self):
        queue_file = self.data / "drift.json"
        queue_file.write_text(json.dumps([row("x", "reply", text="draft")],
                                         ensure_ascii=False), encoding="utf-8")
        before = cycle.load_queue(queue_file)[0]["id"]
        queue_file.write_text(json.dumps([row("x", "reply", text="final wording")],
                                         ensure_ascii=False), encoding="utf-8")

        self.assertEqual(cycle.load_queue(queue_file)[0]["id"], before)


        queue_file.write_text(json.dumps([row("discord", "publish", "1573250833871536161")], ensure_ascii=False),
                              encoding="utf-8")
        with mock.patch.object(cycle, "_now", return_value=self.now):
            got = cycle.run(self.data, go=False, queue_path=queue_file, wave_id="2205",
                            log=lambda *_: None)
        self.assertTrue(got["dry"])
        self.assertEqual(got["planned"], 1)
        self.assertFalse((self.data / "ledger.jsonl").exists())


class DeferredChannelTest(unittest.TestCase):
    """标签页不可见时整渠道推迟：不发送、不占额度、不出队。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.queue_file = self.data / "state" / "engagement-queue.json"
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        self.queue_file.write_text(json.dumps([
            row("github", "reply", "roy-tong/Growovo#1", actor="a"),
            row("discord", "publish", "1573250833871536161"),
            row("github", "reply", "roy-tong/Growovo#2", actor="b"),
        ], ensure_ascii=False), encoding="utf-8")

    def fake_execute(self, channel, action, data_dir, **_kwargs):
        if channel == "github":
            raise browser_write.ActionBlocked("tab_hidden", "后台标签页")
        return {"ok": True, "result": "success", "ledger_result": "committed", "ref": "r1"}

    def test_hidden_tab_defers_channel_and_keeps_queue(self):
        with mock.patch.object(cycle, "_now",
                               return_value=at("2026-11-20T22:05:00+08:00")), \
                mock.patch.object(cycle.time, "sleep"), \
                mock.patch("core.action_runner.run_action",
                                  side_effect=self.fake_execute):
            got = cycle.run(self.data, go=True, queue_path=self.queue_file,
                            wave_id="2205", log=lambda *_: None)

        self.assertEqual(got["committed"], 1)
        self.assertEqual(got["deferred_channels"], ["github"])
        self.assertEqual([d["channel"] for d in got["deferred"]], ["github", "github"])
        self.assertEqual(got["consumed"], 1)
        self.assertEqual(got["queue_left"], 2)
        left = json.loads(self.queue_file.read_text(encoding="utf-8"))
        self.assertEqual([item["channel"] for item in left], ["github", "github"])


class MissingTabTest(unittest.TestCase):
    """某渠道没有标签页时只推迟该渠道；其他渠道照常发完，不整波中断。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.queue_file = self.data / "state" / "engagement-queue.json"
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        self.queue_file.write_text(json.dumps([
            row("github", "reply", "roy-tong/Growovo#1", text="hello"),
            row("discord", "publish", "1573250833871536161"),
            row("github", "reply", "roy-tong/Growovo#2", text="second"),
        ], ensure_ascii=False), encoding="utf-8")

    def fake_execute(self, channel, action, data_dir, **_kwargs):
        if channel == "discord":
            raise browser_attach.BridgeError(
                "tab_not_found", "浏览器里没有已打开的 discord 标签页")
        return {"ok": True, "result": "success", "ledger_result": "committed", "ref": "r1"}

    def run_wave(self):
        with mock.patch.object(cycle, "_now",
                               return_value=at("2026-11-20T22:05:00+08:00")), \
                mock.patch.object(cycle.time, "sleep"), \
                mock.patch("core.action_runner.run_action",
                           side_effect=self.fake_execute):
            return cycle.run(self.data, go=True, queue_path=self.queue_file,
                             wave_id="2205", log=lambda *_: None)

    def test_missing_discord_tab_does_not_kill_the_wave(self):
        got = self.run_wave()

        self.assertTrue(got["ok"])
        self.assertEqual(got["committed"], 2)
        self.assertEqual(got["stopped"], {})
        self.assertEqual(got["deferred_channels"], ["discord"])
        self.assertEqual([d["reason"] for d in got["deferred"]], ["tab_not_found"])
        self.assertEqual(got["queue_left"], 1)
        left = json.loads(self.queue_file.read_text(encoding="utf-8"))
        self.assertEqual([item["channel"] for item in left], ["discord"])

    def test_missing_tab_leaves_no_stop_file(self):
        self.run_wave()

        night = "2026-11-20"
        self.assertFalse(cycle.stop_path(self.data, night, "discord").exists())
        self.assertFalse(cycle.is_stopped(self.data, night, "discord"))


class StaleQueueTest(unittest.TestCase):
    """上一夜没发出去的草稿不能留到下一夜：超过 12 小时即丢弃，dry-run 只报告不动文件。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.queue_file = self.data / "state" / "engagement-queue.json"
        self.queue_file.parent.mkdir(parents=True, exist_ok=True)
        self.stale = row("discord", "publish", "1573250833871536161", text="yesterday")
        self.stale["campaign"] = "night-20261119"
        self.fresh = row("github", "reply", "roy-tong/Growovo#1", text="tonight")
        self.fresh["queued_at"] = "2026-11-20T21:30:00+08:00"
        self.queue_file.write_text(json.dumps([self.stale, self.fresh], ensure_ascii=False),
                                   encoding="utf-8")

    def test_dry_run_reports_stale_but_keeps_file(self):
        before = self.queue_file.read_text(encoding="utf-8")
        with mock.patch.object(cycle, "_now",
                               return_value=at("2026-11-20T22:05:00+08:00")):
            got = cycle.run(self.data, go=False, queue_path=self.queue_file, wave_id="2205",
                            log=lambda *_: None)
        self.assertEqual(got["planned"], 1)
        self.assertEqual(got["pruned_stale"], 0)
        self.assertEqual([s["channel"] for s in got["stale"]], ["discord"])
        self.assertEqual(self.queue_file.read_text(encoding="utf-8"), before)

    def test_go_prunes_stale_and_keeps_deferred_rows(self):
        def fake_execute(channel, action, data_dir, **_kwargs):
            raise browser_write.ActionBlocked("tab_hidden", "后台标签页")

        with mock.patch.object(cycle, "_now",
                               return_value=at("2026-11-20T22:05:00+08:00")), \
                mock.patch.object(cycle.time, "sleep"), \
                mock.patch("core.action_runner.run_action", side_effect=fake_execute):
            got = cycle.run(self.data, go=True, queue_path=self.queue_file, wave_id="2205",
                            log=lambda *_: None)

        self.assertEqual(got["pruned_stale"], 1)
        self.assertEqual(got["consumed"], 0)
        self.assertEqual(got["queue_left"], 1)
        left = json.loads(self.queue_file.read_text(encoding="utf-8"))
        self.assertEqual([item["text"] for item in left], ["tonight"])


class WaveReliabilityTest(unittest.TestCase):
    """真实波次的锁、原子写、执行期限和账本统计；不连接浏览器。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.queue_file = self.data / "state" / "engagement-queue.json"
        cycle.save_queue(self.queue_file, [row("discord", "publish", "1573250833871536161", text="hello")])
        self.now = at("2026-11-20T22:05:00+08:00")

    def test_second_real_wave_exits_without_touching_queue(self):
        before = self.queue_file.read_text(encoding="utf-8")
        lock_file = cycle._lock_path(self.data)
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_file.open("a+")
        cycle.fcntl.flock(handle.fileno(), cycle.fcntl.LOCK_EX | cycle.fcntl.LOCK_NB)
        try:
            got = cycle.run(self.data, go=True, queue_path=self.queue_file,
                            wave_id="2205", log=lambda *_: None)
        finally:
            cycle.fcntl.flock(handle.fileno(), cycle.fcntl.LOCK_UN)
            handle.close()
        self.assertEqual(got["reason"], "wave_already_running")
        self.assertEqual(self.queue_file.read_text(encoding="utf-8"), before)

    def test_atomic_queue_write_cleans_temp_file_on_failure(self):
        before = self.queue_file.read_text(encoding="utf-8")
        with mock.patch.object(cycle.os, "replace", side_effect=OSError("disk busy")):
            with self.assertRaises(OSError):
                cycle.save_queue(self.queue_file, [row("x", "publish", text="new")])
        self.assertEqual(self.queue_file.read_text(encoding="utf-8"), before)
        self.assertEqual(list(self.queue_file.parent.glob("*.tmp")), [])

    def test_enqueue_fills_inbox_draft_and_dedupes_reply_target(self):
        blank = row("x", "reply", "https://x.com/a/status/1", text="", actor="a",
                    source="inbox")
        blank["their_text"] = "question"
        cycle.save_queue(self.queue_file, [blank])
        drafts = [
            dict(row("x", "reply", "https://x.com/a/status/1", text="answer", actor="a"),
                 their_text="question"),
            row("x", "publish", text="original"),
            row("x", "reply", "https://x.com/a/status/1", text="changed", actor="a"),
        ]
        got = cycle.enqueue(self.data, drafts, queue_path=self.queue_file, now=self.now)
        self.assertTrue(got["ok"])
        self.assertEqual(got["added"], 1)
        self.assertEqual(got["filled"], 1)
        self.assertEqual(len(got["duplicates"]), 1)
        saved = cycle.load_queue(self.queue_file)
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]["text"], "answer")
        self.assertEqual(saved[0]["their_text"], "question")
        self.assertEqual(saved[0]["source"], "inbox")
        self.assertEqual(saved[0]["queued_at"], self.now.isoformat(timespec="seconds"))

    def test_budget_deadline_defers_without_starting_action(self):
        with mock.patch.object(cycle, "_now", return_value=self.now), \
                mock.patch("core.action_runner.run_action") as execute:
            got = cycle.run(self.data, go=True, queue_path=self.queue_file,
                            wave_id="2205", budget_sec=0, log=lambda *_: None)
        execute.assert_not_called()
        self.assertTrue(got["budget_exhausted"])
        self.assertEqual(got["deferred"][0]["reason"], "wave_budget")
        self.assertEqual(got["queue_left"], 1)
        self.assertEqual(got["committed"], 0)

    def test_wave_counts_prefer_ledger_result(self):
        fake = {"ok": True, "result": "success", "ledger_result": "failed", "ref": "r1"}
        with mock.patch.object(cycle, "_now", return_value=self.now), \
                mock.patch("core.action_runner.run_action", return_value=fake) as execute:
            got = cycle.run(self.data, go=True, queue_path=self.queue_file,
                            wave_id="2205", log=lambda *_: None)
        self.assertEqual(got["committed"], 0)
        self.assertEqual(got["failed"], 1)
        self.assertEqual(got["queue_left"], 1)
        self.assertEqual(list((self.data / "state").glob("**/*.tmp")), [])
        kwargs = execute.call_args.kwargs
        self.assertEqual(kwargs["transport"], "browser-attach")
        self.assertEqual(kwargs["operator"], "cycle:2205")
        self.assertEqual(kwargs["run_id"], got["campaign"])


    def test_run_marks_pool_row_used_after_commit(self):
        merged = supply_pool.merge(
            self.data, [{"channel": "discord", "action": "publish",
                         "target": "1573250833871536161",
                         "key": "content:discord:publish:test1",
                         "text": "pooled", "source": "test"}], source="test")
        key = merged["added_keys"][0]
        pooled = row("discord", "publish", "1573250833871536161",
                     text="pooled", source="supply")
        pooled["supply_key"] = key
        cycle.save_queue(self.queue_file, [pooled])
        fake = {"ok": True, "result": "success", "ledger_result": "committed",
                "ref": "r1"}
        with mock.patch.object(cycle, "_now", return_value=self.now), \
                mock.patch("core.action_runner.run_action", return_value=fake):
            got = cycle.run(self.data, go=True, queue_path=self.queue_file,
                            wave_id="2205", log=lambda *_: None)

        self.assertEqual(got["supply_updates"]["used"], 1)
        self.assertEqual(supply_pool.load(self.data)["items"][key]["status"], "used")

    def test_result_rows_carry_queue_item_id(self):
        queued = row("discord", "publish", "1573250833871536161", text="hello")
        queued["id"] = "q:test123"
        cycle.save_queue(self.queue_file, [queued])
        fake = {"ok": True, "result": "success", "ledger_result": "committed",
                "ref": "r1"}
        with mock.patch.object(cycle, "_now", return_value=self.now), \
                mock.patch("core.action_runner.run_action", return_value=fake):
            got = cycle.run(self.data, go=True, queue_path=self.queue_file,
                            wave_id="2205", log=lambda *_: None)

        self.assertEqual(got["results"][0]["queue_item_id"], "q:test123")

    def test_skipped_result_keeps_its_reason(self):
        fake = {"ok": True, "skipped": True, "result": "skipped",
                "reason": "already_done_content", "ref": "r1"}
        with mock.patch.object(cycle, "_now", return_value=self.now), \
                mock.patch("core.action_runner.run_action", return_value=fake):
            got = cycle.run(self.data, go=True, queue_path=self.queue_file,
                            wave_id="2205", log=lambda *_: None)

        self.assertEqual(got["results"][0]["result"], "skipped")
        self.assertEqual(got["results"][0]["reason"], "already_done_content")

    def test_preflight_skips_logged_out_channel_without_counting_failure(self):
        policy_file = self.data / "policies" / "engagement-cycle.json"
        policy_file.parent.mkdir(parents=True, exist_ok=True)
        policy_file.write_text(json.dumps(
            {"wave_preflight": {"enabled": True}}), encoding="utf-8")
        cycle.save_queue(self.queue_file, [
            row("github", "reply", "roy-tong/Growovo#1", text="hello"),
            row("discord", "publish", "1573251023500476427", text="dc"),
        ])
        checked = {"ok": False, "channels": {
            "github": {"site": "github", "ok": True, "handle": "me"},
            "discord": {"site": "discord", "ok": False, "reason": "login_required"},
        }}
        fake = {"ok": True, "result": "success", "ledger_result": "committed",
                "ref": "r1"}
        with mock.patch.object(cycle, "_now", return_value=self.now), \
                mock.patch("core.wave_preflight.check", return_value=checked), \
                mock.patch("core.action_runner.run_action", return_value=fake) as execute:
            got = cycle.run(self.data, go=True, queue_path=self.queue_file,
                            wave_id="2205", log=lambda *_: None)

        self.assertTrue(got["preflight"]["enabled"])
        self.assertEqual(got["preflight_skipped"], {"discord": "login_required"})
        self.assertEqual(got["preflight_skipped_rows"], 1)
        self.assertEqual(got["failed"], 0)
        self.assertEqual(got["committed"], 1)
        self.assertEqual(got["queue_left"], 1)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.kwargs["channel"], "github")


class InboxConcurrencyTest(unittest.TestCase):
    def test_inbox_merge_respects_wave_lock_and_preserves_queue(self):
        import mkt

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            queue_file = data / "state" / "engagement-queue.json"
            cycle.save_queue(queue_file, [row("x", "publish", text="queued")])
            before = queue_file.read_text(encoding="utf-8")
            lock_file = cycle._lock_path(data)
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_file.open("a+")
            cycle.fcntl.flock(handle.fileno(), cycle.fcntl.LOCK_EX | cycle.fcntl.LOCK_NB)
            try:
                args = SimpleNamespace(action="inbox", lookback_hours=0, session="",
                                       queue=str(queue_file))
                output = io.StringIO()
                with mock.patch.object(mkt, "DATA", data), \
                        mock.patch.object(cycle, "collect_inbox", return_value=[
                            row("x", "reply", "https://x.com/a/status/1", actor="a")]), \
                        redirect_stdout(output):
                    code = mkt.cmd_cycle(args)
            finally:
                cycle.fcntl.flock(handle.fileno(), cycle.fcntl.LOCK_UN)
                handle.close()

            got = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            self.assertFalse(got["ok"])
            self.assertEqual(got["reason"], "wave_already_running")
            self.assertEqual(queue_file.read_text(encoding="utf-8"), before)


class QuotaTest(unittest.TestCase):
    def test_legacy_committed_rows_consume_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            write_rows(data / "ledger.jsonl", [
                row("x", "reply", "https://x.com/a/status/1", actor="a",
                    ts="2026-11-20T22:01:00+08:00", transport=""),
            ])
            got = cycle.quota_snapshot(data, now=at("2026-11-20T22:05:00+08:00"))
            self.assertEqual(got["quotas"]["x/reply"]["night_used"], 1)
            self.assertEqual(got["quotas"]["x/reply"]["hour_used"], 1)

    def test_empty_ledger_reports_full_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = cycle.quota_snapshot(Path(tmp), now=at("2026-11-20T22:05:00+08:00"))
        self.assertIn("/", "/".join(got["quotas"]))
        self.assertEqual(got["quotas"]["x/publish"]["night_left"], 90)
        self.assertEqual(got["quotas"]["x/reply"]["night_left"], 180)
        self.assertTrue(got["in_window"])

    def test_quota_snapshot_marks_configured_target_capped_by_daily_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = cycle.quota_snapshot(Path(tmp), now=at("2026-11-20T22:05:00+08:00"))
        row = got["quotas"]["x/publish"]

        self.assertEqual(row["night_cap"], 90)
        self.assertEqual(row["effective_night_cap"], 48)
        self.assertTrue(row["capped_by_daily_budget"])
        self.assertEqual(row["day_cap"], 48)


class OwnerAttestedGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.identity = {
            "enforced": True, "mode": "cdp_observed",
            "observed_platform_identity": "x:1", "handle": "someone",
            "identity_proof_ref": "browser-attach:12@now",
            "browser_context": "webbridge-session:test", "observed_at": "now",
        }

    def write_auth(self, **overrides):
        now = dt.datetime.now().astimezone()
        platform = overrides.pop("platform", "x")
        payload = {
            "route": "owner_attested_session",
            "approved_by": "test",
            "approved_at": now.isoformat(timespec="seconds"),
            "window_start": (now - dt.timedelta(hours=1)).isoformat(timespec="seconds"),
            "window_end": (now + dt.timedelta(hours=1)).isoformat(timespec="seconds"),
            "scopes": ["%s:publish" % platform, "%s:reply" % platform],
            "stop_conditions": ["captcha"],
            "revoked": False,
        }
        payload.update(overrides)
        path = platform_compliance.owner_attested_path(self.data, platform)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def check(self, **kwargs):
        params = dict(go=True, actual_transport="browser-attach",
                      identity=self.identity)
        params.update(kwargs)
        return platform_compliance.require_owner_attested_browser_write(
            params.pop("platform", "x"), params.pop("action", "reply"),
            self.data, **params)

    def test_missing_authorization_fails_closed(self):
        with self.assertRaises(platform_compliance.ComplianceError) as ctx:
            self.check(platform="github")
        self.assertEqual(ctx.exception.code, "authorization_missing")

    def test_x_owner_attestation_authorizes_scoped_browser_write(self):
        self.write_auth()
        permit = self.check()
        self.assertEqual("owner_attested_session", permit["route"])
        self.assertIs(False, permit["platform_permission"])
        self.assertEqual("browser-attach", permit["transport"])

    def test_x_owner_attestation_still_limits_scope_and_window(self):
        self.write_auth(scopes=["x:publish"])
        with self.assertRaises(platform_compliance.ComplianceError) as ctx:
            self.check(action="reply")
        self.assertEqual(ctx.exception.code, "authorization_scope_missing")
        self.write_auth(revoked=True)
        with self.assertRaises(platform_compliance.ComplianceError) as ctx:
            self.check()
        self.assertEqual(ctx.exception.code, "authorization_revoked")

    def test_x_without_attestation_fails_closed(self):
        with self.assertRaises(platform_compliance.ComplianceError) as ctx:
            self.check()
        self.assertEqual(ctx.exception.code, "authorization_missing")

    def test_scope_and_transport_are_enforced(self):
        self.write_auth(platform="github", scopes=["github:publish"])
        with self.assertRaises(platform_compliance.ComplianceError) as ctx:
            self.check(platform="github", action="reply")
        self.assertEqual(ctx.exception.code, "authorization_scope_missing")
        with self.assertRaises(platform_compliance.ComplianceError) as ctx:
            self.check(platform="github", actual_transport="playwright")
        self.assertEqual(ctx.exception.code, "transport_mismatch")

    def test_revoked_and_closed_window_reject(self):
        self.write_auth(platform="github", revoked=True)
        with self.assertRaises(platform_compliance.ComplianceError) as ctx:
            self.check(platform="github")
        self.assertEqual(ctx.exception.code, "authorization_revoked")
        past = dt.datetime.now().astimezone() - dt.timedelta(days=2)
        self.write_auth(platform="github",
                        window_start=(past - dt.timedelta(hours=1)).isoformat(),
                        window_end=(past + dt.timedelta(hours=1)).isoformat())
        with self.assertRaises(platform_compliance.ComplianceError) as ctx:
            self.check(platform="github")
        self.assertEqual(ctx.exception.code, "authorization_window_closed")

    def test_identity_observation_is_mandatory(self):
        self.write_auth(platform="github")
        with self.assertRaises(platform_compliance.ComplianceError) as ctx:
            self.check(platform="github", identity=None)
        self.assertEqual(ctx.exception.code, "identity_observation_required")
        with self.assertRaises(platform_compliance.ComplianceError) as ctx:
            self.check(platform="github", identity=dict(self.identity, mode="self_reported"))
        self.assertEqual(ctx.exception.code, "identity_mode_mismatch")

    def test_autonomous_browser_write_returns_non_blocking_risk(self):
        self.write_auth()
        got = platform_compliance.require_autonomous_write(
            "x", "publish", self.data, go=True, actual_transport="browser-attach")
        self.assertEqual(got["risk_code"], "non_api_write_forbidden")
        self.assertIn("不阻断", got["risk_warning"])


class CyclePlatformPermissionTest(unittest.TestCase):
    def test_real_wave_skips_x_before_action_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            queue_file = data / "queue.json"
            cycle.save_queue(queue_file, [
                row("x", "publish", text="synthetic x"),
                row("discord", "publish", "1573250833871536161", text="synthetic discord"),
            ])
            with mock.patch.object(cycle, "_now", return_value=at("2026-11-20T22:05:00+08:00")), \
                    mock.patch.object(cycle.time, "sleep"), \
                    mock.patch("core.action_runner.run_action", return_value={
                        "ok": True, "result": "success", "ledger_result": "committed"
                    }) as execute:
                got = cycle.run(data, go=True, queue_path=queue_file,
                                wave_id="2205", log=lambda *_: None)

        self.assertEqual([call.kwargs["channel"] for call in execute.call_args_list],
                         ["discord"])
        self.assertEqual(got["committed"], 1)
        self.assertEqual([r["reason"] for r in got["skipped"] if r["channel"] == "x"],
                         ["account_authorization_required"])

    def test_real_wave_runs_x_with_owner_attestation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            now = at("2026-11-20T22:05:00+08:00")
            auth = {
                "route": "owner_attested_session", "approved_by": "test",
                "approved_at": now.isoformat(),
                "window_start": (now - dt.timedelta(hours=1)).isoformat(),
                "window_end": (now + dt.timedelta(hours=1)).isoformat(),
                "scopes": ["x:publish", "x:reply"], "stop_conditions": ["captcha"],
                "revoked": False,
            }
            path = platform_compliance.owner_attested_path(data, "x")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(auth), encoding="utf-8")
            queue_file = data / "queue.json"
            cycle.save_queue(queue_file, [
                row("x", "publish", text="synthetic x"),
                row("discord", "publish", "1573250833871536161", text="synthetic discord"),
            ])
            with mock.patch.object(cycle, "_now", return_value=now), \
                    mock.patch.object(cycle.time, "sleep"), \
                    mock.patch("core.action_runner.run_action", return_value={
                        "ok": True, "result": "success", "ledger_result": "committed"
                    }) as execute:
                got = cycle.run(data, go=True, queue_path=queue_file,
                                wave_id="2205", log=lambda *_: None)

        channels = sorted(call.kwargs["channel"] for call in execute.call_args_list)
        self.assertEqual(channels, ["discord", "x"])
        self.assertEqual(got["committed"], 2)


class BridgeSessionTest(unittest.TestCase):
    """attach 与 run 必须共用同一会话；否则执行时看不到已检查过的标签页。"""

    def test_missing_session_falls_back_to_empty(self):
        self.assertEqual(cycle.bridge_session({}), "")
        self.assertEqual(cycle.bridge_session({"bridge": {}}), "")

    def test_policy_session_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "policies").mkdir()
            (data / "policies" / "engagement-cycle.json").write_text(
                json.dumps({"bridge": {"session": "ovo-night-run"}}), encoding="utf-8")
            self.assertEqual(cycle.bridge_session(cycle.load_policy(data)), "ovo-night-run")

    def test_dry_run_applies_session_and_reports_it(self):
        from core.bridge import wb
        before = wb.SESSION
        try:
            with tempfile.TemporaryDirectory() as tmp:
                data = Path(tmp)
                (data / "policies").mkdir()
                (data / "policies" / "engagement-cycle.json").write_text(
                    json.dumps({"bridge": {"session": "ovo-night-run"}}), encoding="utf-8")
                with mock.patch.object(cycle, "_now",
                                       return_value=at("2026-11-21T02:02:00")):
                    got = cycle.run(data, go=False, queue_path=data / "queue.json",
                                    log=lambda *_: None)
                    self.assertEqual(got["bridge_session"], "ovo-night-run")
                    self.assertEqual(wb.SESSION, "ovo-night-run")
                    override = cycle.run(data, go=False, queue_path=data / "queue.json",
                                         session="explicit-session", log=lambda *_: None)
                    self.assertEqual(override["bridge_session"], "explicit-session")
                    self.assertEqual(wb.SESSION, "explicit-session")
        finally:
            wb.set_session(before)


class StopRecoveryTest(unittest.TestCase):
    """渠道熔断冷却后只放一条试探：成功恢复，失败重新计时。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.queue_file = self.data / "state" / "engagement-queue.json"
        cycle.save_queue(self.queue_file, [
            row("github", "reply", "roy-tong/Growovo#1", text="first"),
            row("github", "reply", "roy-tong/Growovo#2", text="second"),
            row("discord", "publish", "1573250833871536161", text="hello"),
        ])
        self.now = at("2026-11-20T22:05:00+08:00")
        self.night = "2026-11-20"

    def write_stop(self, stamp):
        path = cycle.stop_path(self.data, self.night, "github")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"channel": "github", "night": self.night,
                                    "reason": "窗口内 3 个不同目标连续 422",
                                    "at": stamp}, ensure_ascii=False), encoding="utf-8")
        return path

    def run_wave(self, execute):
        with mock.patch.object(cycle, "_now", return_value=self.now), \
                mock.patch.object(cycle.time, "sleep"), \
                mock.patch("core.action_runner.run_action", side_effect=execute):
            return cycle.run(self.data, go=True, queue_path=self.queue_file,
                             wave_id="2205", log=lambda *_: None)

    def ok(self, channel, action, data_dir, **_kwargs):
        return {"ok": True, "result": "success", "ledger_result": "committed", "ref": "r1"}

    def test_no_stop_file_is_closed(self):
        self.assertEqual(cycle.stop_state(self.data, self.night, "github", now=self.now), "closed")

    def test_fresh_stop_is_open_and_channel_is_skipped(self):
        self.write_stop("2026-11-20T21:50:00+08:00")
        got = self.run_wave(self.ok)

        self.assertEqual(got["stop_states"]["github"], "open")
        self.assertEqual(got["half_open"], [])
        self.assertEqual(got["committed"], 1)
        self.assertEqual([d["channel"] for d in got["deferred"]], [])

    def test_expired_stop_allows_one_probe_and_clears_on_success(self):
        path = self.write_stop("2026-11-20T21:20:00+08:00")
        got = self.run_wave(self.ok)

        self.assertEqual(got["half_open"], ["github"])
        self.assertEqual(got["stop_recovery"], {"github": "probe_ok"})
        self.assertFalse(path.exists())
        self.assertEqual(got["committed"], 2)
        self.assertEqual([d["reason"] for d in got["deferred"]], ["stop_probe_pending"])
        self.assertEqual(got["queue_left"], 1)

    def test_failed_probe_restamps_the_stop(self):
        def blocked(channel, action, data_dir, **_kwargs):
            if channel == "github":
                raise browser_write.StopChannel("github", "页面出现 '422'")
            return self.ok(channel, action, data_dir)

        path = self.write_stop("2026-11-20T21:20:00+08:00")
        got = self.run_wave(blocked)

        self.assertEqual(got["stop_recovery"], {"github": "probe_failed"})
        self.assertTrue(path.exists())
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["at"], self.now.isoformat(timespec="seconds"))
        self.assertEqual(cycle.stop_state(self.data, self.night, "github", now=self.now), "open")
        self.assertEqual(got["committed"], 1)

    def test_stop_without_timestamp_stays_open(self):
        self.write_stop("")
        got = self.run_wave(self.ok)

        self.assertEqual(got["stop_states"]["github"], "open")
        self.assertEqual(got["half_open"], [])

    def test_all_channels_open_reports_states(self):
        for name in ("x", "github", "discord"):
            path = cycle.stop_path(self.data, self.night, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"at": "2026-11-20T21:50:00+08:00"},
                                       ensure_ascii=False), encoding="utf-8")
        got = self.run_wave(self.ok)

        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "all_channels_stopped")
        self.assertEqual(got["stop_states"], {"x": "open", "github": "open", "discord": "open"})


class IndeterminateStopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.queue_file = self.data / "state" / "engagement-queue.json"
        cycle.save_queue(self.queue_file, [
            row("discord", "publish", "1573250833871536161", text="first"),
            row("discord", "publish", "1573250833871536161", text="must-not-send"),
        ])
        self.now = at("2026-11-20T22:05:00+08:00")

    def test_unknown_result_defers_rest_and_blocks_next_wave(self):
        def execute(channel, action, data_dir, **_kwargs):
            return {"ok": False, "result": "indeterminate", "ledger_result": "indeterminate",
                    "ref": "unknown-1"}
        with mock.patch.object(cycle, "_now", return_value=self.now), \
                mock.patch.object(cycle.time, "sleep"), \
                mock.patch("core.action_runner.run_action", side_effect=execute) as run_action:
            got = cycle.run(self.data, go=True, queue_path=self.queue_file,
                            wave_id="2205", log=lambda *_: None)
            self.assertEqual(1, run_action.call_count)
            self.assertEqual(got["deferred_channels"], ["discord"])
            self.assertEqual(got["queue_left"], 1)
            self.assertEqual(got["stopped"]["discord"], "indeterminate_pending_review")
            record = cycle.stop_record(self.data, "2026-11-20", "discord")
            self.assertTrue(record["requires_human_resolution"])
            self.assertEqual("unknown-1", record["unresolved_ref"])
            next_wave = cycle.run(self.data, go=True, queue_path=self.queue_file,
                                  wave_id="2206", log=lambda *_: None)
            self.assertEqual(1, run_action.call_count)
            self.assertEqual("open", next_wave["stop_states"]["discord"], next_wave)
            self.assertEqual(0, next_wave["planned"])


class BlockedResultTest(unittest.TestCase):
    """额度等阻断也要在波次摘要里写清原因，不能只留一个 code。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.queue_file = self.data / "state" / "engagement-queue.json"
        cycle.save_queue(self.queue_file, [
            row("discord", "publish", "1573250833871536161", text="hello"),
            row("x", "publish", text="hello"),
        ])
        self.now = at("2026-11-20T22:05:00+08:00")

    def test_blocked_row_carries_reason_and_error(self):
        def execute(channel, action, data_dir, **_kwargs):
            if channel == "discord":
                raise browser_write.ActionBlocked("quota_exhausted", "Discord 当日额度已用完")
            return {"ok": True, "result": "success", "ledger_result": "committed", "ref": "r1"}

        with mock.patch.object(cycle, "_now", return_value=self.now), \
                mock.patch.object(cycle.time, "sleep"), \
                mock.patch("core.action_runner.run_action", side_effect=execute):
            got = cycle.run(self.data, go=True, queue_path=self.queue_file,
                            wave_id="2205", log=lambda *_: None)

        blocked = [r for r in got["results"] if r["result"] == "blocked"]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["channel"], "discord")
        self.assertEqual(blocked[0]["code"], "quota_exhausted")
        self.assertEqual(blocked[0]["reason"], "quota_exhausted")
        self.assertIn("额度已用完", blocked[0]["error"])
        self.assertEqual(blocked[0]["ledger_result"], "blocked")
        self.assertFalse(blocked[0]["ok"])
        self.assertEqual(got["blocked"], 1)


if __name__ == "__main__":
    unittest.main()
