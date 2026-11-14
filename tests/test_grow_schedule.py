"""配额、时间窗与供给水位的契约测试（纯函数，不联网、不真等）。"""
import unittest
from datetime import datetime

from core import grow_schedule as gs


class WeekSplitTests(unittest.TestCase):
    def test_split_sums_to_plan_and_remainder_goes_first(self):
        for plan in (0, 1, 7, 8, 20, 25):
            with self.subTest(plan=plan):
                days = gs.split_week(plan, "2026-11-14")
                self.assertEqual(7, len(days))
                self.assertEqual(plan, sum(sum(v.values()) for v in days.values()))
        days = gs.split_week(20, "2026-11-14")
        counts = [days[d]["reply"] for d in sorted(days)]
        self.assertEqual([3, 3, 3, 3, 3, 3, 2], counts)

    def test_action_weights_split_and_sum(self):
        days = gs.split_week(20, "2026-11-14", {"reply": 0.7, "publish": 0.3})
        self.assertEqual(20, sum(sum(v.values()) for v in days.values()))
        total = {a: 0 for a in ("reply", "publish")}
        for row in days.values():
            for act, n in row.items():
                total[act] += n
        self.assertEqual(14, total["reply"])
        self.assertEqual(6, total["publish"])

    def test_week_start_anchor_and_cross_week(self):
        self.assertEqual("2026-11-14", gs.week_start_of("2026-11-14", "2026-11-14"))
        self.assertEqual("2026-11-14", gs.week_start_of("2026-11-20", "2026-11-14"))
        self.assertEqual("2026-11-21", gs.week_start_of("2026-11-21", "2026-11-14"))
        with self.assertRaises(gs.ScheduleError):
            gs.week_start_of("2026-11-01", "2026-11-14")


class WindowTests(unittest.TestCase):
    def test_us_west_evening_window(self):
        # 04:00Z = 美西前一天 20:00，落在晚间黄金窗
        self.assertTrue(gs.window_allows("20:00-23:00@us-west",
                                         datetime(2026, 11, 14, 4, 0)))
        # 03:00Z = 美西 19:00，不在 20-23 窗
        self.assertFalse(gs.window_allows("20:00-23:00@us-west",
                                          datetime(2026, 11, 14, 3, 0)))

    def test_dead_zone_blocks_even_if_window_covers(self):
        # 10:00Z = 美西 02:00：死区，即使有人把窗口写成 0-24 也不发
        self.assertTrue(gs.in_dead_zone("us-west", datetime(2026, 11, 14, 10, 0)))
        self.assertFalse(gs.window_allows("00:00-24:00@us-west",
                                          datetime(2026, 11, 14, 10, 0)))

    def test_empty_window_means_no_restriction(self):
        self.assertIsNone(gs.parse_window(""))
        self.assertTrue(gs.window_allows("", datetime(2026, 11, 14, 10, 0)))
        self.assertTrue(gs.window_allows(None, datetime(2026, 11, 14, 10, 0)))

    def test_asia_window_conversion(self):
        # 12:00Z = 北京 20:00
        self.assertTrue(gs.window_allows("20:00-23:00@cn",
                                         datetime(2026, 11, 14, 12, 0)))
        self.assertFalse(gs.window_allows("20:00-23:00@cn",
                                          datetime(2026, 11, 14, 4, 0)))

    def test_unknown_audience_raises(self):
        with self.assertRaises(gs.ScheduleError):
            gs.window_allows("20:00-23:00@mars", datetime(2026, 11, 14, 4, 0))


class DueTests(unittest.TestCase):
    def test_item_due_by_date_and_window(self):
        item = {"id": "a", "scheduled_for": "2026-11-14",
                "window": "20:00-23:00@us-west"}
        inside = datetime(2026, 11, 14, 4, 0)
        outside = datetime(2026, 11, 14, 3, 0)
        self.assertTrue(gs.item_due(item, today="2026-11-14", now_utc=inside))
        self.assertFalse(gs.item_due(item, today="2026-11-14", now_utc=outside))
        future = dict(item, scheduled_for="2026-11-15")
        self.assertFalse(gs.item_due(future, today="2026-11-14", now_utc=inside))
        self.assertTrue(gs.item_due(item, today="2026-11-14", now_utc=outside,
                                    allow_out_of_window=True))


class SupplyTests(unittest.TestCase):
    def _queues(self):
        return [{"path": "q1", "queue": {"channel": "x", "action": "reply", "items": [
            {"id": "a", "target": "tweet:https://x.com/a/1",
             "scheduled_for": "2026-11-14"},
            {"id": "b", "target": "tweet:https://x.com/a/2",
             "scheduled_for": "2026-11-15"},
        ]}}]

    def test_gap_and_coverage(self):
        state = gs.supply_status(self._queues(), today="2026-11-14",
                                 days=3, daily_quota=2)
        self.assertEqual(2, state["staged"])
        self.assertEqual(0, state["coverage_days"])
        self.assertEqual(4, state["gap"])       # 3 天 × 2 条 - 已有 2 条
        self.assertEqual({"2026-11-14": 1, "2026-11-15": 1}, state["by_day"])

    def test_committed_targets_excluded(self):
        state = gs.supply_status(self._queues(), today="2026-11-14", days=3,
                                 daily_quota=1,
                                 committed_targets={"tweet:https://x.com/a/1"})
        self.assertEqual(1, state["staged"])
        # 今天（11-14）已被 committed 排除 → 当天覆盖为 0，连续覆盖从今天算起
        self.assertEqual(0, state["coverage_days"])
        self.assertEqual(2, state["gap"])

    def test_done_today_covers_today_not_a_gap(self):
        state = gs.supply_status(self._queues(), today="2026-11-14", days=3,
                                 daily_quota=1,
                                 committed_targets={"tweet:https://x.com/a/1"},
                                 done_today=1)
        # 今天已 committed 完成 1 条 → 当天算覆盖，11-14/11-15 连续覆盖，只剩 11-16 缺 1
        self.assertEqual(2, state["coverage_days"])
        self.assertEqual(1, state["gap"])

    def test_full_coverage(self):
        items = []
        for day in ("2026-11-14", "2026-11-15", "2026-11-16"):
            for i in range(2):
                items.append({"id": f"{day}-{i}",
                              "target": f"tweet:https://x.com/{day}/{i}",
                              "scheduled_for": day})
        state = gs.supply_status([{"path": "q", "queue": {"items": items}}],
                                 today="2026-11-14", days=3, daily_quota=2)
        self.assertEqual(3, state["coverage_days"])
        self.assertEqual(0, state["gap"])


if __name__ == "__main__":
    unittest.main()
