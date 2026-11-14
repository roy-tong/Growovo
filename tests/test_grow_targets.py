"""回复目标发现：解析、过滤、落盘；不联网。"""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core import grow_targets as gt


class ParseTests(unittest.TestCase):
    def test_parse_engagement_chinese_and_english(self):
        rows = gt.parse_engagement(["1 回复。回复", "0 次转帖。转帖",
                                    "12 喜欢次数。喜欢", "分享帖子"])
        self.assertEqual({"replies": 1, "reposts": 0, "likes": 12}, rows)
        rows = gt.parse_engagement(["3 replies", "2 reposts", "1.2K likes"])
        self.assertEqual({"replies": 3, "reposts": 2, "likes": 1200}, rows)

    def test_parse_status_url(self):
        self.assertEqual({"handle": "roy", "tweet_id": "123",
                          "url": "https://x.com/roy/status/123"},
                         gt.parse_status_url("/roy/status/123"))
        self.assertIsNone(gt.parse_status_url("/roy"))
        self.assertIsNone(gt.parse_status_url(""))

    def test_age_hours(self):
        now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
        self.assertAlmostEqual(2.0, gt.age_hours("2026-11-14T10:00:00.000Z", now=now))
        self.assertIsNone(gt.age_hours("not-a-date", now=now))


class FilterTests(unittest.TestCase):
    def _rows(self):
        return [
            {"handle": "own", "url": "https://x.com/own/status/1", "age_hours": 1,
             "engagement": {"likes": 5, "replies": 1}},
            {"handle": "done", "url": "https://x.com/done/status/2", "age_hours": 1,
             "engagement": {"likes": 5, "replies": 1}},
            {"handle": "old", "url": "https://x.com/old/status/3", "age_hours": 100,
             "engagement": {"likes": 5, "replies": 1}},
            {"handle": "low", "url": "https://x.com/low/status/4", "age_hours": 2,
             "engagement": {"likes": 0, "replies": 0}},
            {"handle": "good", "url": "https://x.com/good/status/5", "age_hours": 3,
             "engagement": {"likes": 20, "replies": 2}},
        ]

    def test_filters_exclusions_age_and_engagement(self):
        rows = gt.filter_candidates(
            self._rows(), max_age_hours=48, min_likes=1, min_replies=1,
            exclude_handles=["own"], exclude_urls=["https://x.com/done/status/2"])
        self.assertEqual(["good"], [r["handle"] for r in rows])

    def test_sorted_by_recency(self):
        rows = gt.filter_candidates(self._rows(), max_age_hours=48, min_likes=0)
        ages = [r["age_hours"] for r in rows]
        self.assertEqual(sorted(ages), ages)

    def test_empty(self):
        self.assertEqual([], gt.filter_candidates([], max_age_hours=1))


class PersistTests(unittest.TestCase):
    def test_save_load_and_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = [{"handle": "a", "url": "https://x.com/a/status/1",
                     "text": "hello", "age_hours": 1, "engagement": {"likes": 2}}]
            path = gt.save_candidates(tmp, "g1", rows, on="2026-11-14")
            self.assertTrue(path.is_file())
            loaded = gt.load_candidates(tmp, "g1", on="2026-11-14")
            self.assertEqual(rows, loaded)
            text = gt.render(loaded)
            self.assertIn("@a", text)
            self.assertIn("hello", text)
            self.assertIn("https://x.com/a/status/1", text)
            self.assertIn("没有符合条件", gt.render([]))

    def test_saved_json_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = gt.save_candidates(tmp, "g1", [], on="2026-11-14")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual({"run": "g1", "on": "2026-11-14",
                              "count": 0, "candidates": []}, data)


if __name__ == "__main__":
    unittest.main()
