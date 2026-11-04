"""意图信号战术：四周节奏、周配额、三选一判定。"""
import tempfile
import unittest
from pathlib import Path

from core.intent import IntentCampaign, phase_of


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _start(self, started="2026-11-02"):
        path = Path(self.tmp.name) / "reddit-1.json"
        IntentCampaign.start(path, "reddit-1", subs=["r/SideProject"], started=started)
        return IntentCampaign(path)

    def test_phase_progression(self):
        self.assertEqual(phase_of("2026-11-02", "2026-11-03")["phase"], "居民期")
        self.assertEqual(phase_of("2026-11-02", "2026-11-10")["phase"], "触达")
        self.assertEqual(phase_of("2026-11-02", "2026-11-17")["phase"], "跟进")
        self.assertEqual(phase_of("2026-11-02", "2026-11-24")["phase"], "复盘与下一轮")
        # 实验结束后停在复盘与下一轮，不继续数下去
        self.assertEqual(phase_of("2026-11-02", "2027-01-01")["week"], 4)

    def test_phase_rejects_date_before_start(self):
        with self.assertRaises(ValueError):
            phase_of("2026-11-02", "2026-11-01")

    def test_log_rejects_impossible_numbers(self):
        c = self._start()
        with self.assertRaises(ValueError):
            c.log(sent=5, deleted=6)
        with self.assertRaises(ValueError):
            c.log(sent=-1)
        with self.assertRaises(ValueError):
            c.log(sent=1, on="2026-10-01")

    def test_summary_rates_and_weekly_quota(self):
        c = self._start()
        c.log(sent=20, deleted=1, replied=4, moved=1, on="2026-11-10")
        c.log(sent=20, deleted=2, replied=3, moved=0, on="2026-11-17")
        s = c.summary("2026-11-18")
        self.assertEqual(s["sent"], 40)
        self.assertEqual(s["deleted_rate"], 0.075)
        self.assertEqual(s["response_rate"], 0.175)
        self.assertEqual(s["by_week"][2]["sent"], 20)
        self.assertEqual(s["weeks_behind_quota"], [])

    def test_custom_learning_experiment_window_and_quota(self):
        path = Path(self.tmp.name) / "custom.json"
        IntentCampaign.start(path, "custom", started="2026-11-02", weeks=2, weekly_target=5)
        c = IntentCampaign(path)
        c.log(sent=3, on="2026-11-10")
        self.assertEqual(c.summary("2026-11-11")["weeks_behind_quota"], [2, 3])

    def test_verdict_refuses_to_judge_on_thin_sample(self):
        c = self._start()
        c.log(sent=10, deleted=0, replied=3, moved=1, on="2026-11-10")
        v = c.verdict("2026-11-18")
        self.assertEqual(v["verdict"], "样本不足")
        self.assertIn("不要提前下结论", v["reason"])

    def test_verdict_observes_interaction_without_scaling(self):
        c = self._start()
        c.log(sent=40, deleted=2, replied=6, moved=2, on="2026-11-17")
        v = c.verdict("2026-11-18")
        self.assertEqual(v["verdict"], "观察到互动")
        self.assertIn("不自动扩量", v["reason"])

    def test_verdict_low_response_is_not_channel_death(self):
        c = self._start()
        c.log(sent=60, deleted=3, replied=3, moved=0, on="2026-11-17")
        self.assertEqual(c.verdict("2026-11-18")["verdict"], "观察到互动")

    def test_verdict_flags_safety_anomaly(self):
        c = self._start()
        c.log(sent=40, deleted=10, replied=8, moved=3, on="2026-11-17")
        v = c.verdict("2026-11-18")
        self.assertEqual(v["verdict"], "安全异常")
        self.assertIn("被删率", v["reason"])

    def test_verdict_no_interaction_is_not_channel_death(self):
        c = self._start()
        c.log(sent=60, deleted=1, replied=0, moved=0, on="2026-11-17")
        v = c.verdict("2026-11-18")
        self.assertEqual(v["verdict"], "尚无有效互动")
        self.assertIn("不自动换渠道", v["reason"])


if __name__ == "__main__":
    unittest.main()
