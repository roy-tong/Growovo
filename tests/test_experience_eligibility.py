"""Track-specific experience eligibility, harvest provenance, and voice retention."""
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import experience, platforms, voice
from scripts import build_experience, harvest_bilibili, harvest_experience

ON = "2026-11-04"


def record(kind):
    return {
        "platform": "reddit",
        "claim": "一条待验证记录",
        "action": "按限定场景复现",
        "knowledge_type": kind,
        "canonical_url": "https://www.reddit.com/rules?utm_source=test#section",
        "rights": {
            "status": "official_terms", "basis": "平台条款允许本限定场景引用",
            "confirmed_by": "rights-reviewer", "confirmed_at": "2026-11-03T10:00:00+08:00",
            "evidence_ref": "reviews/rights/record-1",
        },
        "relevance": "high",
        "human_reviewed": True,
        "human_review": {
            "status": "approved", "reviewer": "strategy-reviewer",
            "reviewed_at": "2026-11-03T11:00:00+08:00", "record_ref": "reviews/strategy/record-1",
            "source_domains": ["www.reddit.com", "a.example", "b.example"],
        },
        "asr": False,
        "published": "2026-11-01",
        "support": 1,
    }


def official():
    row = record(experience.OFFICIAL_RULE)
    row.update({
        "official_authority": "Reddit",
        "currently_effective": True,
        "status": "current",
        "independent_sources": ["https://www.reddit.com/rules"],
    })
    return row


def reproducible():
    row = record(experience.REPRODUCIBLE_METHOD)
    row.update({
        "mechanism": "排序信号改变了内容曝光",
        "support": 2,
        "independent_sources": ["https://a.example/method", "https://b.example/method"],
        "independent_reproductions": ["https://lab.example/reproduction"],
    })
    return row


class EligibilityTests(unittest.TestCase):
    def test_official_rule_requires_one_current_authority(self):
        row = official()
        self.assertTrue(experience.eligible(row, on=ON))
        detail = experience.assess_eligibility(row, on=ON)
        self.assertEqual(detail["canonical_url"], "https://www.reddit.com/rules")
        self.assertTrue(detail["generalizable"])

        second = dict(row, independent_sources=row["independent_sources"] + ["https://help.reddit.com/rule"])
        self.assertFalse(experience.eligible(second, on=ON))
        self.assertFalse(experience.eligible(dict(row, currently_effective=False, status="superseded"), on=ON))
        self.assertFalse(experience.eligible(dict(row, currently_effective=False, status="current"), on=ON))
        self.assertFalse(experience.eligible(dict(row, valid_until="2026-11-03"), on=ON))
        self.assertFalse(experience.eligible(dict(row, official_authority=""), on=ON))

    def test_reproducible_method_requires_mechanism_sources_and_reproduction(self):
        row = reproducible()
        self.assertTrue(experience.eligible(row, on=ON))
        self.assertFalse(experience.eligible(dict(row, mechanism=""), on=ON))
        self.assertFalse(experience.eligible(dict(row, support=1,
                                                     independent_sources=row["independent_sources"][:1]), on=ON))
        self.assertFalse(experience.eligible(dict(row, independent_reproductions=[]), on=ON))
        self.assertFalse(experience.eligible(dict(row, independent_sources=[row["independent_sources"][0]] * 2), on=ON))

    def test_single_expert_can_generate_experiment_not_general_strategy(self):
        row = record(experience.SINGLE_EXPERT)
        row.update({
            "author": {"name": "真实作者", "profile_url": "https://example.com/author", "verified": True},
            "scenario": {"description": "2026 年一个小团队的真实发布场景", "verified": True},
        })
        self.assertTrue(experience.eligible(row, purpose="experiment", on=ON))
        self.assertFalse(experience.eligible(row, on=ON))
        self.assertFalse(experience.eligible(row, purpose="generalization", on=ON))
        self.assertFalse(experience.eligible(dict(row, author_verified=False,
                                                   author={"name": "未核验", "verified": False}),
                                              purpose="experiment", on=ON))
        self.assertFalse(experience.eligible(dict(row, scenario=""), purpose="experiment", on=ON))

    def test_short_term_tactic_decays_rapidly_and_support_one_is_experiment_only(self):
        row = record(experience.SHORT_TERM_TACTIC)
        row.update({"published": "2026-10-30", "support": 2,
                    "independent_sources": ["https://a.example/tip", "https://b.example/tip"]})
        self.assertTrue(experience.eligible(row, on=ON))
        self.assertFalse(experience.eligible(dict(row, published="2026-10-01"), on=ON))
        one = dict(row, support=1, independent_sources=[row["canonical_url"]])
        self.assertFalse(experience.eligible(one, on=ON))
        self.assertTrue(experience.eligible(one, purpose="experiment", on=ON))
        self.assertFalse(experience.eligible(dict(row, freshness_days=60), on=ON))

    def test_first_party_test_requires_action_result_sample_and_cost(self):
        row = record(experience.FIRST_PARTY_TEST)
        row.update({"result": 0, "sample": 3, "cost": 0, "tested_at": "2026-11-03"})
        self.assertTrue(experience.eligible(row, on=ON))
        detail = experience.assess_eligibility(row, on=ON)
        self.assertFalse(detail["generalizable"])
        self.assertFalse(experience.eligible(row, purpose="generalization", on=ON))
        for changes in ({"result": None}, {"sample": 0}, {"cost": None}, {"action": ""}):
            self.assertFalse(experience.eligible(dict(row, **changes), on=ON), changes)

    def test_common_gates_cannot_be_bypassed_by_kind_or_support(self):
        row = reproducible()
        for changes in (
            {"canonical_url": ""}, {"canonical_url": "not-a-url"},
            {"canonical_url": "https://user:password@example.com/evidence"},
            {"rights": "unknown"}, {"relevance": "unrelated"},
            {"human_reviewed": False}, {"published": "2020-01-01"},
        ):
            self.assertFalse(experience.eligible(dict(row, **changes), on=ON), changes)
        # Even a high support count cannot replace provenance and review.
        self.assertFalse(experience.eligible(dict(row, support=99, human_reviewed=False), on=ON))

    def test_asr_provenance_is_explicit(self):
        row = official()
        row["origin"] = "transcript-import"
        row.pop("asr")
        detail = experience.assess_eligibility(row, on=ON)
        self.assertFalse(detail["eligible"])
        self.assertTrue(any("ASR" in x for x in detail["reasons"]))
        row["asr"] = True
        self.assertTrue(experience.eligible(row, on=ON))
        self.assertTrue(experience.assess_eligibility(row, on=ON)["asr"])

    def test_eligible_query_filters_rows_track_platform_and_purpose(self):
        off = official()
        single = record(experience.SINGLE_EXPERT)
        single.update({"platform": "github", "author": "作者", "author_verified": True,
                       "scenario": "真实项目", "scenario_verified": True})
        rows = [off, single, reproducible()]
        self.assertEqual(experience.eligible(rows=rows, on=ON), [off, rows[2]])
        self.assertEqual(experience.eligible(rows=rows, purpose="experiment", pid="github", on=ON), [single])
        self.assertEqual(experience.eligible(rows=rows, knowledge_type="official", on=ON), [off])
        self.assertEqual(experience.eligible(rows=rows, knowledge_type="unknown", on=ON), [])

    def test_bundled_records_are_preserved_but_not_promoted(self):
        rows = experience.load()
        self.assertGreaterEqual(len(rows), 108)
        self.assertEqual(experience.eligible(rows=rows, on=ON), [])
        self.assertEqual(experience.as_prompt_lines("xiaohongshu"), [])
        # Legacy display remains available for human review.
        self.assertTrue(experience.graded("xiaohongshu")["rule"])

    def test_legacy_boolean_flags_cannot_restore_formal_eligibility(self):
        row = reproducible()
        row["rights"] = "licensed"
        row["rights_cleared"] = True
        row["human_reviewed"] = True
        row.pop("human_review")
        detail = experience.assess_eligibility(row, on=ON)
        self.assertFalse(detail["eligible"])
        self.assertEqual("unreviewed", detail["review_grade"])
        self.assertEqual("internal_self_report", detail["rights_review"]["grade"])
        self.assertTrue(any("确认人" in reason for reason in detail["reasons"]))
        self.assertTrue(any("复核记录" in reason for reason in detail["reasons"]))

    def test_internal_self_report_is_graded_and_experiment_only(self):
        row = official()
        row["human_review"] = {
            "status": "approved", "reviewer": "internal-author",
            "reviewed_at": "2026-11-03T12:00:00+08:00",
            "source_domains": ["www.reddit.com"],
        }
        strategy = experience.assess_eligibility(row, on=ON)
        self.assertFalse(strategy["eligible"])
        self.assertEqual("internal_self_report", strategy["review_grade"])
        self.assertTrue(strategy["experiment_only"])
        self.assertTrue(experience.eligible(row, purpose="experiment", on=ON))

    def test_traceable_review_requires_time_record_and_all_source_domains(self):
        row = reproducible()
        base = dict(row["human_review"])
        for changes in (
            {"reviewer": ""}, {"reviewed_at": ""}, {"record_ref": ""},
            {"reviewed_at": "2026-11-05T00:00:00+08:00"},
            {"source_domains": ["www.reddit.com", "a.example"]},
        ):
            changed = dict(row, human_review=dict(base, **changes))
            detail = experience.assess_eligibility(changed, on=ON)
            self.assertFalse(detail["eligible"], changes)
            self.assertNotEqual("traceable_review", detail["review_grade"])

    def test_review_records_are_returned_and_legacy_raw_data_stays_ineligible(self):
        row = official()
        record = row.pop("human_review")
        row["review_records"] = [record]
        detail = experience.assess_eligibility(row, on=ON)
        self.assertTrue(detail["eligible"])
        self.assertEqual("traceable_review", detail["review_grade"])
        self.assertEqual("reviews/strategy/record-1", detail["review_records"][0]["record_ref"])
        self.assertEqual(["www.reddit.com"], detail["source_domains"])
        legacy = {"rights_cleared": True, "human_reviewed": True}
        self.assertFalse(experience.eligible(legacy, on=ON))


class HarvestSafetyTests(unittest.TestCase):
    def assert_tls_verified(self, module):
        contexts = []

        class DummyOpener:
            def __init__(self):
                self.addheaders = []

            def open(self, *args, **kwargs):
                raise OSError("test: no network")

        def capture(*args, **kwargs):
            contexts.append(kwargs.get("context"))
            return object()

        with mock.patch.object(module.urllib.request, "HTTPSHandler", side_effect=capture), \
                mock.patch.object(module.urllib.request, "build_opener", return_value=DummyOpener()):
            module.opener()
        self.assertEqual(len(contexts), 1)
        self.assertTrue(contexts[0].check_hostname)
        self.assertEqual(contexts[0].verify_mode, ssl.CERT_REQUIRED)

    def test_harvesters_keep_tls_certificate_and_hostname_verification(self):
        self.assert_tls_verified(harvest_experience)
        self.assert_tls_verified(harvest_bilibili)

    def test_corpus_asr_marker_is_read_and_extraction_stays_unreviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "article.txt"
            path.write_text(
                "TITLE: 视频\nURL: https://www.bilibili.com/video/BV1\nPUBLISHED: 2026-11-01\n"
                "ASR: true\nASR_ENGINE: vosk\nHUMAN_REVIEWED: false\n\n正文", encoding="utf-8")
            article = build_experience.parse_article(path)
        self.assertTrue(article["asr"])
        self.assertEqual(article["asr_engine"], "vosk")
        evidence = build_experience.evidence_fields([article], article, "reproducible_method")
        self.assertEqual(evidence["canonical_url"], article["url"])
        self.assertEqual(evidence["independent_sources"], [article["url"]])
        self.assertEqual(evidence["rights"], "unknown")
        self.assertEqual(evidence["relevance"], "unreviewed")
        self.assertFalse(evidence["human_reviewed"])
        self.assertTrue(evidence["asr"])


class VoicePreservationTests(unittest.TestCase):
    SOURCE = ("Disclosure: I built this tool. I tested 12 cases in 3 days. "
              "Results: https://example.com/results")

    def test_all_37_platform_profiles_are_preserved(self):
        self.assertEqual(len(platforms.PLATFORMS), 37)
        cov = voice.coverage()
        self.assertEqual(len(cov["bespoke"] + cov["fallback"]), 37)

    def test_facts_and_disclosure_survive_rewrite(self):
        candidate = "Disclosure: I built this tool. 12 cases over 3 days: https://example.com/results"
        result = voice.check_preservation(self.SOURCE, candidate)
        self.assertTrue(result["ok"], result["issues"])

    def test_missing_fact_url_or_disclosure_is_reported(self):
        result = voice.check_preservation(self.SOURCE, "12 cases looked useful")
        self.assertFalse(result["ok"])
        self.assertIn("3days", result["missing_facts"])
        self.assertIn("https://example.com/results", result["missing_facts"])
        self.assertTrue(result["missing_disclosures"])
        problems = voice.lint("reddit", "12 cases looked useful", source_text=self.SOURCE)
        self.assertTrue(any("事实未保持" in x for x in problems))
        self.assertTrue(any("披露未保持" in x for x in problems))

    def test_explicit_non_numeric_fact_and_disclosure_checklist(self):
        result = voice.check_preservation(
            "源文", "改写", facts=["仅在 Linux v2 复现"], disclosures=["我维护这个项目"])
        self.assertEqual(result["missing_facts"], ["仅在 Linux v2 复现"])
        self.assertEqual(result["missing_disclosures"], ["我维护这个项目"])

    def test_stale_experience_is_not_rendered_as_current_playbook(self):
        self.assertEqual(voice.current_experience("xiaohongshu"), [])
        md = voice.render("xiaohongshu")
        self.assertIn("不能写成这个平台的当前做法", md)
        self.assertNotIn("当前有", md)


if __name__ == "__main__":
    unittest.main()
