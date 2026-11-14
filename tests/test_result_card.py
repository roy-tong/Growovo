"""结果闭环：周结果卡的三件套聚合与诚实缺数规则。"""
import json
import tempfile
import unittest
from pathlib import Path

from core import result_card
from core.attribution import OutcomeLedger
from core.ledger import Ledger


class TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)


class WeekBoundsTests(TmpDir):
    def test_windows_anchor_at_start(self):
        windows = result_card.week_bounds("2026-11-01", "2026-11-10")
        self.assertEqual([(1, "2026-11-01", "2026-11-07"),
                          (2, "2026-11-08", "2026-11-10")], windows)

    def test_before_start_is_empty(self):
        self.assertEqual([], result_card.week_bounds("2026-11-10", "2026-11-01"))


class BuildCardTests(TmpDir):
    def _run(self):
        return {"id": "g1", "started": "2026-11-01",
                "goal": {"metric": "followers", "name": "社媒关注者增长",
                         "baseline": 120, "target": 320, "target_text": "从 120 到 320"},
                "plan": {"actions_per_week": 20}, "adjustments": []}

    def _seed(self, *, with_counts=True):
        ledger = Ledger(self.dir / "ledger.jsonl")
        for i in range(12):
            ledger.append(channel="x", action="reply", target=f"tweet:https://x.com/a/{i}",
                          result="committed", campaign="g1-w1", ts="2026-11-03T10:00:00")
        for i in range(8):
            ledger.append(channel="email", action="message", target="u@example.com",
                          result="committed", campaign="g1-w1", ts="2026-11-04T10:00:00")
        ledger.append(channel="email", action="message", target="b@example.com",
                      result="bounced", campaign="g1-w1", ts="2026-11-05T10:00:00")
        ledger.append(channel="x", action="reply", target="tweet:https://x.com/a/old",
                      result="committed", campaign="other-run", ts="2026-11-03T10:00:00")
        if with_counts:
            ol = OutcomeLedger(self.dir)
            ol.add_count(metric="x_followers", value=120, window="2026-10-31",
                         source_ref="seed", approximated=True)
            ol.add_count(metric="x_followers", value=153, window="2026-11-05",
                         source_ref="seed", approximated=True)
            for day, value in (("2026-11-02", 100), ("2026-11-03", 200), ("2026-11-04", 300)):
                ol.add_count(metric="x_impressions", value=value, window=day,
                             source_ref="seed", approximated=True)

    def test_three_metrics_with_counts(self):
        self._seed()
        card = result_card.build(self.dir, self._run(), "2026-11-01", "2026-11-07")
        self.assertEqual(20, card["published"]["total"])
        self.assertEqual(12, card["published"]["by_channel"]["x"]["reply"])
        self.assertEqual(8, card["published"]["by_channel"]["email"]["message"])
        self.assertEqual(1, card["email"]["bounced"])
        self.assertEqual(200, card["impressions"]["avg_daily"])
        self.assertEqual(3, card["impressions"]["days_read"])
        self.assertEqual(33, card["followers"]["new"])
        text = result_card.render(card)
        self.assertIn("结果单", text)
        self.assertIn("20", text)
        self.assertIn("新增粉丝", text)
        self.assertIn("退信", text)
        self.assertIn("不等于业务转化", text)  # 边界声明必须在

    def test_campaign_prefix_does_not_leak_across_runs(self):
        ledger = Ledger(self.dir / "ledger.jsonl")
        for campaign in ("g1-w1", "g1", "g10-w1", "g100-w1"):
            ledger.append(channel="x", action="reply",
                          target=f"tweet:https://x.com/{campaign}/{campaign}",
                          result="committed", campaign=campaign,
                          ts="2026-11-03T10:00:00")
        card = result_card.build(self.dir, self._run(), "2026-11-01", "2026-11-07")
        self.assertEqual(2, card["published"]["total"])  # 只有 g1 / g1-w1

    def test_dry_run_week_is_labeled_not_zero_success(self):
        ledger = Ledger(self.dir / "ledger.jsonl")
        for i in range(3):
            ledger.append(channel="x", action="reply",
                          target=f"tweet:https://x.com/dry/{i}",
                          result="dry-run", campaign="g1-w1",
                          ts="2026-11-03T10:00:00")
        card = result_card.build(self.dir, self._run(), "2026-11-01", "2026-11-07")
        self.assertEqual(0, card["published"]["total"])
        self.assertEqual(3, card["dry_run"])
        text = result_card.render(card)
        self.assertIn("干跑", text)
        self.assertIn("未开闸", text)

    def test_missing_counts_read_as_unavailable_not_zero(self):
        self._seed(with_counts=False)
        card = result_card.build(self.dir, self._run(), "2026-11-01", "2026-11-07")
        self.assertIsNone(card["impressions"]["avg_daily"])
        self.assertIsNone(card["followers"]["new"])
        self.assertEqual({}, card["impressions"]["by_day"])
        text = result_card.render(card)
        self.assertIn("取不到", text)
        self.assertNotIn("| 日均曝光 | 0", text)

    def test_window_filters_out_of_week_rows(self):
        self._seed()
        card = result_card.build(self.dir, self._run(), "2026-11-08", "2026-11-14")
        self.assertEqual(0, card["published"]["total"])
        self.assertEqual(0, card["impressions"]["days_read"])


if __name__ == "__main__":
    unittest.main()
