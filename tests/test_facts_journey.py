from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from core import gtm, orchestration, work


class FactsJourneyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "facts.json"
        self.source.write_text(json.dumps({
            "context_id": "ctx-cleaning-robot", "version": "2026-11-19.1",
            "source": {"kind": "askovo", "ref": "A-02/study"},
            "product_facts": [
                {"fact": "产品是家用扫地机器人，主打自动集尘基站"},
                {"fact": "目标用户是 80–120 平米、有娃或养宠的家庭"},
                {"fact": "自动集尘版本比普通版贵 300 元"},
            ],
            "value_event_definitions": [
                {"event": "first_auto_empty_cycle", "meaning": "用户第一次完成自动集尘"},
                {"event": "week4_retention", "meaning": "第 4 周仍在每周使用 ≥3 次"},
            ],
            "non_goals": ["不做商用清洁", "不承诺完全免维护"], "approved_by": "roy",
        }, ensure_ascii=False), encoding="utf-8")

    def test_legacy_context_becomes_one_named_facts_pack(self) -> None:
        self.assertTrue(hasattr(work, "facts_document_from_file"))
        document = work.facts_document_from_file(self.source, name="家用扫地机器人")
        gtm._validate_facts(document)
        self.assertEqual("家用扫地机器人", document["product"]["name"])
        self.assertEqual("hypothesis", document["icp"][0]["evidence_label"])
        self.assertEqual({"insufficient_evidence"}, {fact["claim_status"] for fact in document["product_facts"]})
        self.assertEqual("sales_development", orchestration.infer_motion(
            document["product"], "先拿 20 个有效线索", "建立线索资格标准并列出可授权触达名单"))
        candidate = orchestration.orchestrate(
            document, "先拿 20 个有效线索", "建立线索资格标准并列出可授权触达名单")
        product_line = next(row for row in orchestration.render_markdown(candidate).splitlines()
                            if "产品：" in row)
        self.assertIn("家用扫地机器人", product_line)
        self.assertNotIn("未命名", product_line)
        candidate["validation"] = {"warnings": work.evidence_warnings(document)}
        rendered = orchestration.render_markdown(candidate)
        self.assertIn("未证事实", rendered)
        self.assertIn("300 元", rendered)
        warnings = work.evidence_warnings(document)
        self.assertEqual(3, len(warnings))
        self.assertTrue(any("300 元" in warning for warning in warnings))

    def test_b2b_user_is_not_mislabeled_as_a_consumer(self) -> None:
        snapshot = work.context_snapshot_from_file(self.source)
        snapshot["product_facts"][1]["fact"] = "中小品牌的售后主管"
        document = work.facts_pack_from_context(snapshot, name="售后工单 SaaS")
        self.assertEqual(["b2b-buyers"], document["icp"][0]["tags"])

    def test_unproven_price_cannot_silently_become_an_objective_fact(self) -> None:
        snapshot = work.context_snapshot_from_file(self.source)
        store = work.ContextStore(self.root / "data")
        store.import_snapshot(snapshot, by="human-owner")
        objective = {
            "objective_id": "obj-price", "version": "1",
            "context_ref": {"context_id": snapshot["context_id"], "version": snapshot["version"]},
            "business_question": "自动集尘版本比普通版贵 300 元，先拿 20 个有效线索",
            "subject_type": "lead", "window": "2026-11-23/2026-11-29", "owner": "person-growth-owner",
            "primary_metric_ref": {"metric_id": "valid_leads", "version": "1.0.0"},
        }
        errors = work.lint_objective(objective, context_snapshot=snapshot)
        self.assertTrue(any("未证事实" in error for error in errors), errors)
        objective["fact_assertions"] = [{
            "statement": "自动集尘版本比普通版贵 300 元", "status": "hypothesis",
            "source_ref": str(self.source) + "#product_facts[2]",
        }]
        self.assertEqual([], work.lint_objective(objective, context_snapshot=snapshot))


if __name__ == "__main__":
    unittest.main()
