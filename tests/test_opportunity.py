"""Opportunity Discovery：确定性匹配、证据边界与产品问题优先级。"""
import copy
import unittest

from core import opportunity


def _facts():
    return {
        "value": [{"id": "research-evidence", "text": "把用户研究证据整理成可复现结论",
                   "tags": ["research", "evidence"]}],
        "target_audience": [{"id": "product-managers", "text": "产品经理",
                             "tags": ["product-manager"]}],
        "strengths": [{"id": "traceable", "text": "每条判断可回溯出处",
                       "tags": ["traceable"]}],
        "limitations": [{"id": "manual-review", "text": "语义判断仍需人工复核"}],
    }


def _question(**extra):
    row = {
        "id": "q1", "question": "如何让用户研究证据可复现",
        "scenario": "整理访谈结论", "audience": "product-manager",
        "tags": ["research", "evidence", "product-manager", "traceable"],
        "source_ref": "support/questions#q1", "occurrences": 4,
    }
    row.update(extra)
    return row


class OpportunityTests(unittest.TestCase):
    def test_empty_data_returns_no_candidates(self):
        result = opportunity.discover({}, {})
        self.assertEqual([], result["candidates"])
        self.assertEqual(0, result["summary"]["signals"])
        self.assertIsNone(result["summary"]["priority_action"])
        self.assertIn("不是购买", result["boundary"])

    def test_repeated_question_generates_explainable_asset_candidate(self):
        original = {"repeated_questions": [_question()]}
        snapshot = copy.deepcopy(original)
        result = opportunity.discover(_facts(), original)
        self.assertEqual(snapshot, original)  # 纯函数，不改调用方数据
        candidate = result["candidates"][0]
        self.assertEqual("matched", candidate["demand_match"]["status"])
        self.assertEqual(100, candidate["demand_match"]["score"])
        self.assertEqual(["research-evidence"], candidate["demand_match"]["value_ids"])
        self.assertEqual("generate_asset", candidate["next_action"]["code"])
        self.assertEqual("task_completed", candidate["validation"]["value_event"])
        self.assertIn("impression", candidate["validation"]["not_a_success_metric"])
        self.assertTrue(candidate["cost"]["drivers"])
        self.assertTrue(candidate["risks"])
        self.assertIn("support/questions#q1", candidate["evidence"]["refs"])

    def test_complaint_is_not_purchase_intent(self):
        result = opportunity.discover(_facts(), {"competitor_limits": [{
            "id": "complaint-1", "text": "竞品的研究证据整理太难用了，我要吐槽",
            "tags": ["research", "evidence"], "source_ref": "forum/post-1",
        }]})
        commercial = result["candidates"][0]["commercial_intent"]
        self.assertEqual("not_observed", commercial["status"])
        self.assertIn("不是购买意愿", commercial["note"])

    def test_explicit_commercial_evidence_requires_source(self):
        signal = _question(commercial_evidence={
            "kind": "purchase_intent", "observed": True,
        })
        result = opportunity.discover(_facts(), {"repeated_questions": [signal]})
        self.assertEqual("not_observed", result["candidates"][0]["commercial_intent"]["status"])
        signal["commercial_evidence"]["source_ref"] = "interview/42#price"
        result = opportunity.discover(_facts(), {"repeated_questions": [signal]})
        self.assertEqual("not_observed", result["candidates"][0]["commercial_intent"]["status"])
        signal["commercial_evidence"]["kind"] = "budget_confirmed"
        result = opportunity.discover(_facts(), {"repeated_questions": [signal]})
        self.assertEqual("observed", result["candidates"][0]["commercial_intent"]["status"])

    def test_product_issue_precedes_asset_generation_and_acquisition(self):
        result = opportunity.discover(_facts(), {
            "repeated_questions": [_question()],
            "inactive_trials": [{
                "id": "trial-9", "problem": "导入研究数据后无法完成首次分析",
                "tags": ["research", "evidence"], "source_ref": "product/trial-9",
                "product_issue": True, "severity": "high", "blocks_activation": True,
            }],
        })
        first = result["candidates"][0]
        self.assertEqual("trial-9", first["signal_id"])
        self.assertTrue(first["product_issue"])
        self.assertEqual("pause_acquisition", first["next_action"]["code"])
        self.assertEqual("pause_acquisition", result["summary"]["priority_action"]["code"])
        self.assertEqual("activation", first["validation"]["value_event"])

    def test_inactive_trial_without_cause_needs_evidence_not_product_blame(self):
        result = opportunity.discover(_facts(), {"inactive_trials": [{
            "id": "trial-10", "text": "注册后三天未激活，原因未知",
            "tags": ["research", "evidence"], "source_ref": "product/trial-10",
        }]})
        candidate = result["candidates"][0]
        self.assertFalse(candidate["product_issue"])
        self.assertEqual("collect_evidence", candidate["next_action"]["code"])
        self.assertIn("不能判定", candidate["next_action"]["reason"])

    def test_unsourced_or_unmatched_signal_stays_evidence_task(self):
        result = opportunity.discover(_facts(), {"help_requests": [
            {"id": "h1", "text": "想要一个完全不相关的记账功能"},
            {"id": "h2", "text": "研究证据怎么回溯", "tags": ["research"]},
        ]})
        self.assertTrue(all(c["next_action"]["code"] == "collect_evidence"
                            for c in result["candidates"]))
        self.assertTrue(any("缺少" in risk for c in result["candidates"] for risk in c["risks"]))

    def test_generic_signal_list_and_stable_order(self):
        payload = {"signals": [
            {"type": "help_request", **_question(id="q2", occurrences=1)},
            {"type": "repeated_question", **_question(id="q1")},
        ]}
        first = opportunity.discover(_facts(), payload)
        second = opportunity.discover(_facts(), payload)
        self.assertEqual(first, second)
        self.assertEqual(2, len(first["candidates"]))


if __name__ == "__main__":
    unittest.main()
