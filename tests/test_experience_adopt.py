"""旧经验补分类：多源实践 vs 单源线索，以及两级资格边界。"""
import unittest

from core import experience


ON = "2026-11-17"


def raw(claim="结论", support=2, sources=("源甲", "源乙"), **extra):
    row = {"platform": "xiaohongshu", "claim": claim, "action": "照做",
           "support": support, "sources": list(sources), "kind": "rule",
           "origin": "manual"}
    row.update(extra)
    return row


class AdoptTests(unittest.TestCase):
    def test_multi_source_and_lead_split(self):
        rows = [raw(collected_at=ON),
                raw("单源", support=1, sources=("源甲",), collected_at=ON)]
        out, report = experience.adopt_rows(rows, on=ON)
        self.assertEqual(report["multi_source"], 1)
        self.assertEqual(report["lead"], 1)
        self.assertEqual(out[0]["knowledge_type"], experience.MULTI_SOURCE_PRACTICE)
        self.assertEqual(out[1]["knowledge_type"], experience.UNVERIFIED_LEAD)
        self.assertEqual(out[0]["independent_sources"], ["源乙", "源甲"])

    def test_missing_date_blocks_multi_source(self):
        out, report = experience.adopt_rows([raw()], on=ON)
        self.assertEqual(report["multi_source"], 0)
        self.assertEqual(out[0]["knowledge_type"], experience.UNVERIFIED_LEAD)
        self.assertEqual(out[0]["ingest"]["reason"], "missing_date")

    def test_dates_mapping_records_its_basis(self):
        out, report = experience.adopt_rows(
            [raw()], on=ON, dates={"结论": "2026-11-04"})
        self.assertEqual(report["multi_source"], 1)
        self.assertEqual(out[0]["observed_at"], "2026-11-04")
        self.assertEqual(out[0]["ingest"]["date_basis"], "first_recorded_in_git")

    def test_idempotent(self):
        out, _ = experience.adopt_rows([raw()], on=ON,
                                       dates={"结论": "2026-11-04"})
        again, report = experience.adopt_rows(out, on=ON)
        self.assertEqual(report["already_typed"], 1)
        self.assertEqual(report["multi_source"], 0)
        self.assertEqual(again[0]["knowledge_type"], experience.MULTI_SOURCE_PRACTICE)

    def test_revisit_promotes_lead_after_second_source(self):
        lead, _ = experience.adopt_rows([raw(support=1, sources=("源甲",))], on=ON)
        self.assertEqual(lead[0]["knowledge_type"], experience.UNVERIFIED_LEAD)
        grown = dict(lead[0], support=2, sources=["源甲", "源乙"])
        out, report = experience.adopt_rows([grown], on=ON, revisit_leads=True,
                                            dates={"结论": "2026-11-04"})
        self.assertEqual(report["multi_source"], 1)
        self.assertEqual(out[0]["knowledge_type"], experience.MULTI_SOURCE_PRACTICE)


class EligibilityBoundaryTests(unittest.TestCase):
    def _multi(self):
        out, _ = experience.adopt_rows([raw()], on=ON,
                                       dates={"结论": "2026-11-04"})
        return out[0]

    def test_multi_source_is_craft_usable_but_not_strategy(self):
        row = self._multi()
        craft = experience.assess_eligibility(row, purpose="craft", on=ON)
        self.assertTrue(craft["eligible"], craft["reasons"])
        self.assertEqual(craft["review_grade"], "ingest_verified")
        self.assertFalse(craft["generalizable"])
        strategy = experience.assess_eligibility(row, purpose="strategy", on=ON)
        self.assertFalse(strategy["eligible"])
        self.assertTrue(any("人工批量签核" in x for x in strategy["reasons"]))

    def test_lead_never_passes(self):
        out, _ = experience.adopt_rows([raw(support=1, sources=("源甲",))], on=ON)
        for purpose in ("craft", "experiment", "strategy", "generalization"):
            self.assertFalse(experience.eligible(out[0], purpose=purpose, on=ON), purpose)

    def test_single_source_short_term_tactic_cannot_enter_craft(self):
        row = raw("小技巧", support=1, sources=("源甲",), collected_at=ON,
                  knowledge_type=experience.SHORT_TERM_TACTIC)
        self.assertFalse(experience.eligible(row, purpose="craft", on=ON))

    def test_multi_source_without_ingest_is_not_craft_eligible(self):
        row = self._multi()
        row.pop("ingest")
        detail = experience.assess_eligibility(row, purpose="craft", on=ON)
        self.assertFalse(detail["eligible"])
        self.assertTrue(any("ingest" in x for x in detail["reasons"]))


if __name__ == "__main__":
    unittest.main()
