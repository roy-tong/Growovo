"""时区窗口策略的单元测试。"""
import unittest

from core import timezone_windows as tw


class LocalHourTests(unittest.TestCase):
    def test_utc_to_jp(self):
        self.assertEqual(tw.local_hour(0, 9), 9)      # UTC 0点 = 东京 9点
        self.assertEqual(tw.local_hour(15, 9), 0)     # UTC 15点 = 东京 24点 → 0
        self.assertEqual(tw.local_hour(23.5, 11), 10.5)  # UTC 23:30 = 悉尼 10:30

    def test_us_wrap(self):
        self.assertEqual(tw.local_hour(5, -8), 21)    # UTC 5点 = 美西 21点


class WindowRuleTests(unittest.TestCase):
    def test_dead_zone(self):
        self.assertTrue(tw.in_dead_zone(3))
        self.assertTrue(tw.in_dead_zone(0))
        self.assertFalse(tw.in_dead_zone(6))
        self.assertFalse(tw.in_dead_zone(23.99))

    def test_golden_morning(self):
        self.assertTrue(tw.in_golden(8.5))
        self.assertFalse(tw.in_golden(11))

    def test_golden_evening_strongest(self):
        self.assertTrue(tw.in_golden(22))


class ScoreTests(unittest.TestCase):
    def test_cn_morning_hits_jp_cn_sg(self):
        # 北京 08:00 = UTC 0点：日本 9 点、中国/新加坡 8 点（均黄金）、悉尼 11 点（非窗）
        w = tw.window_score(0, ["jp", "cn", "sg", "au-east"])
        self.assertEqual(w["score"], 3)
        self.assertIn("jp", w["golden_audiences"])
        self.assertIn("cn", w["golden_audiences"])
        self.assertIn("sg", w["golden_audiences"])
        self.assertNotIn("au-east", w["golden_audiences"])

    def test_us_night_is_dead_for_us(self):
        # UTC 6点 = 美东 1点（死区）、欧洲 7点（非窗）、东京 15点（非窗）
        w = tw.window_score(6, ["us-east", "eu-west", "jp"])
        self.assertEqual(w["score"], 0)
        self.assertIn("us-east", w["dead_audiences"])

    def test_evening_overlap(self):
        # UTC 13点 = 悉尼 24→0(死区边缘前一刻为0.0? 13+11=24→0 死区) 东京22(黄金) 北京21(黄金)
        w = tw.window_score(13, ["jp", "cn", "au-east"])
        self.assertIn("jp", w["golden_audiences"])
        self.assertIn("cn", w["golden_audiences"])
        self.assertIn("au-east", w["dead_audiences"])


class PlanningTests(unittest.TestCase):
    def test_best_hours_returns_ranked(self):
        ranked = tw.best_hours(["jp", "cn", "us-east"], top=3)
        self.assertLessEqual(len(ranked), 3)
        scores = [r["score"] for r in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_next_window_within_day(self):
        w = tw.next_window(["jp", "eu-west", "us-east"])
        self.assertIsNotNone(w["at_utc"])
        self.assertGreaterEqual(w["score"], 1)

    def test_content_hint(self):
        hint = tw.content_hint("jp")
        self.assertEqual(hint["lang"], "ja")
        self.assertIn("测试文化", hint["topics"])

    def test_unknown_audience_raises(self):
        with self.assertRaises(KeyError):
            tw.content_hint("mars")

    def test_language_mix_keeps_english_fallback(self):
        mix = tw.plan_language_mix(["jp", "au-east", "us-west"])
        self.assertIn("ja", mix)
        self.assertGreater(mix.count("en"), 0)


if __name__ == "__main__":
    unittest.main()
