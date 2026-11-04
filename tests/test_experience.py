"""用户经验库：带来源、带样本量，不许把单篇当规律。"""
import json
import tempfile
import unittest
from pathlib import Path

from core import experience, platforms


class ExperienceTests(unittest.TestCase):
    def test_bundled_library_loads(self):
        rows = experience.load()
        self.assertGreaterEqual(len(rows), 20)
        for r in rows:
            for f in ("platform", "claim", "action", "support", "sources"):
                self.assertIn(f, r)

    def test_every_claim_has_a_source(self):
        for r in experience.load():
            self.assertTrue(r.get("sources"), r["claim"])
            self.assertGreaterEqual(len(r["sources"]), 1)

    def test_support_grading_splits_rules_from_hints(self):
        g = experience.graded("xiaohongshu")
        self.assertTrue(g["rule"])
        for r in g["rule"]:
            self.assertEqual(r["kind"], "rule")
        for r in g.get("verbatim", []):
            self.assertGreaterEqual(r["support"], 2)
        for r in g.get("topic", []):
            self.assertTrue(r.get("topic_articles"))

    def test_order_is_by_support(self):
        rows = experience.for_platform("zhihu")
        self.assertEqual(rows, sorted(rows, key=lambda r: -r["support"]))

    def test_chinese_name_resolves(self):
        self.assertTrue(experience.for_platform("小红书"))

    def test_unknown_platform_returns_empty_not_error(self):
        self.assertEqual(experience.for_platform("not-a-platform"), [])
        self.assertIn("别编", experience.render("not-a-platform"))

    def test_render_separates_rule_and_hint(self):
        md = experience.render("xiaohongshu")
        self.assertIn("人工提炼", md)
        self.assertIn("篇提到", md)

    def test_platforms_with_experience_exist_in_registry(self):
        ids = {p["id"] for p in platforms.PLATFORMS}
        for pid in experience.coverage():
            self.assertIn(pid, ids)


if __name__ == "__main__":
    unittest.main()


class FreshnessTests(unittest.TestCase):
    """用户经验会过时：新鲜度与维护计划必须是代码里的硬约束，不是备注。"""

    def test_window_and_support_are_constants(self):
        self.assertIsInstance(experience.FRESH_DAYS, int)
        self.assertGreaterEqual(experience.MIN_SUPPORT, 2)

    def test_freshness_counts_dated_claims(self):
        f = experience.freshness()
        self.assertEqual(f["claims"], len(experience.load()))
        self.assertEqual(f["dated"] + f["undated"], f["claims"])
        self.assertEqual(f["window_days"], experience.FRESH_DAYS)
        self.assertIsInstance(f["meets_window"], bool)

    def test_stale_flags_anything_older_than_window(self):
        for row in experience.stale():
            if row["newest_days"] is None:
                self.assertTrue(row["needs_refresh"], row["platform"])
            elif row["newest_days"] > experience.FRESH_DAYS:
                self.assertTrue(row["needs_refresh"], row["platform"])

    def test_maintenance_plan_states_target_and_command(self):
        plan = experience.maintenance_plan()
        self.assertEqual(plan["target_per_platform"], 50)
        self.assertIn("harvest_experience.py", plan["command"])
        self.assertLess(plan["refresh_cadence_days"], plan["window_days"] + 1)

    def test_every_claim_records_its_origin(self):
        origins = {r.get("origin") for r in experience.load()}
        self.assertTrue(origins)
        for r in experience.load():
            self.assertTrue(r.get("origin"), r["claim"])
