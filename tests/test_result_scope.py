"""真实结果读模型与统一范围。"""
import tempfile
import unittest
from pathlib import Path

from core import result, revise
from core.followups import Followups
from core.scope import ScopedFollowups, filter_rows, make_scope


class ResultScopeTests(unittest.TestCase):
    def test_real_outcome_schema_and_dynamic_value_events(self):
        events = [
            {"event_type": "activated", "approximated": False,
             "verification": {"status": "verified", "method": "api", "ref": "run-1"}},
            {"event_type": "paid", "approximated": True,
             "verification": {"status": "verified"}},
            {"event_type": "paid", "approximated": False},
            {"type": "signup", "verified": True},
        ]
        b = result.business_layer(events)
        self.assertEqual(1, b["by_type"]["activated"])
        self.assertEqual(1, b["signup"])
        self.assertNotIn("paid", b["by_type"])

    def test_scope_never_falls_back_to_all_campaigns(self):
        rows = [{"campaign": "a", "ts": "2026-11-01"},
                {"campaign": "b", "ts": "2026-11-02"},
                {"ts": "2026-11-03"}]
        self.assertEqual([], filter_rows(rows, make_scope(campaign="missing")))
        self.assertEqual(1, len(filter_rows(rows, make_scope(campaign="a"))))
        self.assertEqual(1, len(filter_rows(rows, make_scope(since="2026-11-02", until="2026-11-02"))))

    def test_scoped_followups_filters_campaign_subject_and_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            fu = Followups(Path(tmp) / "followups.jsonl")
            fu.add("github", "alice", campaign="a", on="2026-11-01")
            fu.add("github", "bob", campaign="b", on="2026-11-02")
            fu.touch("fu-0001", "replied", on="2026-11-03")
            fu.touch("fu-0002", "asked", on="2026-11-04")
            self.assertEqual(1, len(ScopedFollowups(fu, make_scope(campaign="a")).all()))
            self.assertEqual(1, len(ScopedFollowups(fu, make_scope(subject="bob")).all()))
            self.assertEqual(2, len(ScopedFollowups(fu, make_scope(since="2026-11-03")).all()))
            self.assertEqual(0, len(ScopedFollowups(fu, make_scope(campaign="missing")).all()))

    def test_zero_current_actions_is_data_insufficient_not_a_growth_verdict(self):
        r = result.collect(ledger_entries=[], outcome_events=[], scope=make_scope(campaign="new"))
        self.assertEqual("数据不足", r["observation"])
        diff = revise.propose(r, {"actions_per_week": 20})
        self.assertEqual("范围校验", diff[0]["evidence_status"])
        self.assertNotIn("30", str(diff[0]["to"]))


if __name__ == "__main__":
    unittest.main()
