"""复盘分析：渠道账、浪费账、冲突账、规则推导。"""
import unittest

from core import retro


def _claims():
    return [
        {"file": "a.md", "line": 1, "channel": "x", "metric": "action", "value": 199, "date": "2026-09-02"},
        {"file": "a.md", "line": 1, "channel": "x", "metric": "response", "value": 3, "date": "2026-09-02"},
        {"file": "a.md", "line": 2, "channel": "email", "metric": "action", "value": 209, "date": "2026-09-03"},
        {"file": "a.md", "line": 2, "channel": "email", "metric": "response", "value": 6, "date": "2026-09-03"},
        {"file": "b.md", "line": 9, "channel": "github", "metric": "action", "value": 77, "date": "2026-09-04"},
        {"file": "b.md", "line": 9, "channel": "github", "metric": "outcome", "value": 30, "date": "2026-09-04"},
        {"file": "c.md", "line": 3, "channel": "x", "metric": "action", "value": 120, "date": "2026-10-21"},
    ]


class RetroTests(unittest.TestCase):
    def test_consolidate_keeps_max_and_flags_conflict(self):
        cons = retro.consolidate(_claims())
        self.assertEqual(cons["x"]["metrics"]["action"]["value"], 199)
        self.assertEqual(cons["x"]["metrics"]["action"]["support"], 1)
        self.assertTrue(cons["x"]["metrics"]["action"]["conflicting"])
        self.assertEqual(cons["x"]["metrics"]["action"]["distinct"], [120, 199])

    def test_rates_and_best_channel(self):
        a = retro.analyze(_claims())
        rows = {r["channel"]: r for r in a["channels"]}
        self.assertAlmostEqual(rows["x"]["response_per_100"], 3 / 199 * 100, places=2)
        self.assertAlmostEqual(rows["email"]["response_per_100"], 6 / 209 * 100, places=2)
        self.assertIsNotNone(rows["github"]["outcome_per_100"])

    def test_waste_flags_volume_without_outcome(self):
        a = retro.analyze(_claims())
        names = [w["name"] for w in a["waste"]]
        self.assertIn("X", names)
        self.assertNotIn("GitHub", names)

    def test_conflicts_surface_before_conclusions(self):
        a = retro.analyze(_claims())
        self.assertTrue(a["conflicts"])
        self.assertEqual(a["conflicts"][0]["metric"], "动作")

    def test_rules_are_derived_from_numbers(self):
        a = retro.analyze(_claims())
        rules = retro.derive_rules(a)
        self.assertTrue(rules)
        for r in rules:
            self.assertTrue(any(ch.isdigit() for ch in r["basis"]), r["basis"])

    def test_timeline(self):
        t = retro.timeline(_claims())
        self.assertEqual(t["start"], "2026-09-02")
        self.assertEqual(t["end"], "2026-10-21")
        self.assertEqual(t["active_days"], 4)


if __name__ == "__main__":
    unittest.main()
