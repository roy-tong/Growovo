"""跟进引擎：三次原则、到期提醒、实质回应判定。"""
import tempfile
import unittest
from pathlib import Path

from core.followups import Followups, MAX_ACTIONS


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fu = Followups(Path(self.tmp.name) / "followups.jsonl")

    def test_add_sets_first_due_in_three_days(self):
        rec = self.fu.add("reddit", "u/alice", source="https://reddit.com/r/x/1", on="2026-11-01")
        self.assertEqual(rec["stage"], "contacted")
        self.assertEqual(rec["next_due"], "2026-11-04")
        self.assertEqual(len(rec["touches"]), 1)

    def test_duplicate_open_target_rejected(self):
        self.fu.add("reddit", "u/alice", on="2026-11-01")
        with self.assertRaises(ValueError):
            self.fu.add("reddit", "u/alice", on="2026-11-02")

    def test_no_reply_walks_cadence_then_drops(self):
        rec = self.fu.add("reddit", "u/bob", on="2026-11-01")
        self.assertEqual(rec["next_due"], "2026-11-04")
        rec = self.fu.touch(rec["id"], "no_reply", on="2026-11-04")
        self.assertEqual(rec["next_due"], "2026-11-18")   # +14
        rec = self.fu.touch(rec["id"], "no_reply", on="2026-11-18")
        self.assertEqual(rec["next_due"], "2027-01-02")   # +45
        rec = self.fu.touch(rec["id"], "no_reply", on="2027-01-02")
        self.assertEqual(rec["stage"], "dropped")
        self.assertEqual(rec["status"], "closed")
        self.assertIsNone(rec["next_due"])
        self.assertEqual(len(rec["touches"]), MAX_ACTIONS)

    def test_substantive_reply_keeps_relation_open(self):
        rec = self.fu.add("reddit", "u/carol", on="2026-11-01")
        rec = self.fu.touch(rec["id"], "asked", note="问能不能用在流式", on="2026-11-04")
        self.assertEqual(rec["stage"], "asked")
        self.assertEqual(rec["status"], "open")
        self.assertEqual(rec["next_due"], "2026-11-07")
        # 有实质回应时不触发三次上限
        for _ in range(5):
            rec = self.fu.touch(rec["id"], "replied", on="2026-11-10")
        self.assertEqual(rec["status"], "open")

    def test_declined_waits_45_days_not_nagging(self):
        rec = self.fu.add("reddit", "u/dave", on="2026-11-01")
        rec = self.fu.touch(rec["id"], "declined", note="说太贵", on="2026-11-04")
        self.assertEqual(rec["stage"], "declined")
        self.assertEqual(rec["next_due"], "2026-12-19")

    def test_bad_result_rejected(self):
        rec = self.fu.add("reddit", "u/eve", on="2026-11-01")
        with self.assertRaises(ValueError):
            self.fu.touch(rec["id"], "maybe", on="2026-11-02")

    def test_due_only_returns_arrived_items(self):
        self.fu.add("reddit", "u/a", on="2026-11-01")   # due 11-04
        self.fu.add("reddit", "u/b", on="2026-11-10")   # due 11-13
        self.assertEqual(len(self.fu.due("2026-11-03")), 0)
        self.assertEqual([r["target"] for r in self.fu.due("2026-11-04")], ["u/a"])
        self.assertEqual(len(self.fu.due("2026-11-20")), 2)

    def test_overdue_marks_those_slipped_past_a_week(self):
        rec = self.fu.add("reddit", "u/slow", on="2026-11-01")  # due 11-04
        self.assertEqual(self.fu.overdue("2026-11-10"), [])
        self.assertEqual(len(self.fu.overdue("2026-11-12")), 1)
        self.assertEqual(rec["target"], "u/slow")

    def test_stats_counts_substantive_and_45_day_conversations(self):
        # a：3 天内回了一句就再没动 —— 不算"活过 45 天"
        a = self.fu.add("reddit", "u/a", on="2026-09-01")
        self.fu.touch(a["id"], "asked", on="2026-09-04")
        # b：三个月里一直在说话 —— 这才算
        b = self.fu.add("reddit", "u/b", on="2026-08-01")
        self.fu.touch(b["id"], "asked", on="2026-10-20")
        c = self.fu.add("reddit", "u/c", on="2026-11-01")
        self.fu.touch(c["id"], "no_reply", on="2026-11-04")
        s = self.fu.stats("2026-11-06")
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["substantive"], 2)
        self.assertEqual(s["conversations_after_45d"], 1)

    def test_close_stops_reminders(self):
        rec = self.fu.add("reddit", "u/x", on="2026-11-01")
        self.fu.close(rec["id"], "不需要")
        self.assertEqual(self.fu.due("2026-11-30"), [])

    def test_platform_breakdown_ranks_by_substantive(self):
        a = self.fu.add("github", "roy/verovo", on="2026-11-01")
        self.fu.touch(a["id"], "replied", on="2026-11-02")
        self.fu.add("reddit", "u/z", on="2026-11-01")
        rows = self.fu.platform_breakdown()
        self.assertEqual(rows[0]["platform"], "github")
        self.assertEqual(rows[0]["substantive"], 1)


if __name__ == "__main__":
    unittest.main()
