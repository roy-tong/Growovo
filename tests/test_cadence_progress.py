"""落地设计：四周动作表，每件事都有验收物。"""
import unittest

from core import cadence, progress, strategy


def _strategy():
    return strategy.build({"name": "Verovo"}, focus={"primary": "github"}, analysis={})


class CadenceTests(unittest.TestCase):
    def test_four_weeks_with_acceptance(self):
        plan = cadence.build(_strategy(), start="2026-11-04", actions_per_week=20)
        self.assertEqual([w["week"] for w in plan["weeks"]], [1, 2, 3, 4])
        for w in plan["weeks"]:
            for t in w["tasks"]:
                self.assertTrue(t["accept"], t["what"])
                self.assertIn(t["who"], ("human", "machine"))
        self.assertGreater(plan["total_human_minutes"], 60)

    def test_residency_platform_gets_a_nursing_week(self):
        s = strategy.build({"name": "x"}, focus={"primary": "reddit"}, analysis={})
        plan = cadence.build(s, start="2026-11-04")
        week1 = " ".join(t["what"] for t in plan["weeks"][0]["tasks"])
        self.assertIn("养号", week1)

    def test_no_residency_platform_skips_nursing(self):
        s = strategy.build({"name": "x"}, focus={"primary": "email"}, analysis={})
        plan = cadence.build(s, start="2026-11-04")
        week1 = " ".join(t["what"] for t in plan["weeks"][0]["tasks"])
        self.assertNotIn("养号", week1)
        self.assertIn("名单", week1)

    def test_last_week_produces_next_iteration_not_product_verdict(self):
        plan = cadence.build(_strategy(), start="2026-11-04")
        last = " ".join(t["what"] for t in plan["weeks"][-1]["tasks"])
        self.assertIn("复盘", last)
        self.assertNotIn("通 / 不确定 / 不通", last)

    def test_render_lists_tables(self):
        md = cadence.render(cadence.build(_strategy(), start="2026-11-04"))
        self.assertIn("四周动作表", md)
        self.assertIn("验收物", md)
        self.assertIn("没有验收物的任务不算做完", md)


class ProgressTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from core.followups import Followups
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fu = Followups(Path(self.tmp.name) / "f.jsonl")

    def test_daily_flags_slipped_objects(self):
        self.fu.add("github", "a/b", on="2026-10-01")
        d = progress.daily(self.fu, on="2026-11-03")
        self.assertGreater(d["overdue"], 0)
        self.assertTrue(any("跟丢" in b for b in d["brief"]))
        self.assertIn("日报", progress.render_daily(d))

    def test_weekly_compares_two_weeks(self):
        r = self.fu.add("github", "a/b", on="2026-10-20")
        self.fu.touch(r["id"], "asked", on="2026-10-21")
        w = progress.weekly(self.fu, on="2026-10-25")
        self.assertEqual(w["this_week"]["touches"], 2)
        self.assertEqual(w["this_week"]["replies"], 1)
        self.assertIn("周报", progress.render_weekly(w))

    def test_weekly_says_so_when_idle(self):
        w = progress.weekly(self.fu, on="2026-11-03")
        self.assertEqual(w["this_week"]["touches"], 0)
        self.assertTrue(any("停摆" in v for v in w["verdict"]))


if __name__ == "__main__":
    unittest.main()
