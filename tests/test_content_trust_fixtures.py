#!/usr/bin/env python3
"""G-18 反例 fixture：形式合法但内容不可用的交付物必须在 submit 与 review 两道门都被拒绝。

覆盖用户 P1 要求的三类反例：
- 形式合法、内容无用：占位符、模板套句、空泛建议、过短文本。
- 对象正确、版本过期：产品事实包版本标错或未标注。
- 目标/受众/事实错位：错工单、错接收人、错产品事实包。

人工可用性评审清单（每份被 `accepted` 的交付物在人工抽样时必须逐条通过，
自动门只证明"不是明显不可用"，不证明内容真的有价值）：
1. 内容确实可执行：接收人照做能完成动作，不需要再回来问作者。
2. 事实与产品一致：产品、客户、数字均与 evidence_refs 的来源对得上，未知项没有编造。
3. 受众与语气正确：称呼、背景假设、行动请求与工单指定接收人一致。
4. 无残留占位：没有 raft:、TODO、模板句复制、待补充。
5. 有可核对证据引用：关键结论都能落到具体 source_id + locator。
6. 版本与对象正确：引用的产品事实包、目标、工单与当前有效版本一致。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.deliverables import ContentUnusableError, PlaceholderContentError, _rubrics
from core.templates import get_template
from tests import test_content_trust_gate as gate_module

REPEATED_TEXT = "面向新开发者完成首次成功调用，覆盖对象、动作与验收方式"


class ContentTrustFixtureCase(gate_module.ContentTrustGateCase):
    def _review_results(self):
        return [
            {"rubric_id": row["rubric_id"], "verdict": "pass",
             "evidence_ref": f"gate:review:{row['rubric_id']}"}
            for row in _rubrics(get_template("T30"))
        ]

    def _check_payload(self, exc, code):
        payload = exc.payload
        if "codes" in payload:
            self.assertEqual("CONTENT_UNUSABLE", payload["reason_code"])
            self.assertIn(code, payload["codes"])
        else:
            self.assertEqual(code, payload["reason_code"])

    def _assert_both_gates_reject(self, code, *, exc_type=ContentUnusableError, **overrides):
        """同一内容缺陷：submit 拒绝，已处于 submitted 的记录在 review 时也必须再次拒绝。"""
        with self.assertRaises(exc_type) as submitted:
            self._submit(f"fx-{code.lower()}-submit", **overrides)
        self._check_payload(submitted.exception, code)

        deliverable_id = f"fx-{code.lower()}-review"
        self.store.create(self._spec(deliverable_id, **overrides), by="gate-author")
        # 模拟历史导入或状态被篡改的记录：状态已是 submitted，但内容没有经过内容门。
        self.store.events.append(
            "submitted", deliverable_id,
            {"version": "1", "content_gate": {"ok": True, "work_order_unresolved": False}},
            by="legacy-import",
        )
        with self.assertRaises(exc_type) as reviewed:
            self.store.review(deliverable_id, results=self._review_results(),
                              by="gate-reviewer")
        self._check_payload(reviewed.exception, code)
        return reviewed.exception.payload

    def test_raft_slot_is_rejected_at_both_gates(self):
        self._assert_both_gates_reject(
            "PLACEHOLDER_CONTENT", exc_type=PlaceholderContentError,
            reproduction_commands="raft:reproduction_commands")

    def test_copied_template_sentence_is_rejected_at_both_gates(self):
        payload = self._assert_both_gates_reject(
            "TEMPLATE_REPEAT", subject=REPEATED_TEXT, scope=REPEATED_TEXT,
            exclusions=REPEATED_TEXT)
        self.assertIn("subject", payload["problems"][0]["field"])

    def test_two_repeated_slots_are_still_accepted(self):
        """阈值边界：两处重复不足以判定模板套句，避免误伤正常复用措辞。"""
        self._submit("fx-two-repeats", subject=REPEATED_TEXT, scope=REPEATED_TEXT)
        self.assertEqual("submitted", self.store.get("fx-two-repeats")["state"])

    def test_vague_advice_is_rejected_at_both_gates(self):
        self._assert_both_gates_reject("VAGUE_ADVICE", outcome_status="持续优化，提升效果")

    def test_wrong_receiver_is_rejected_at_both_gates(self):
        self._assert_both_gates_reject(
            "WRONG_AUDIENCE", receiver="person-sales-ops（销售运营）")

    def test_wrong_product_facts_are_rejected_at_both_gates(self):
        self._assert_both_gates_reject(
            "WRONG_FACTS", business_context_ref="ctx-other-product v2026-01-01.1")

    def test_wrong_objective_is_rejected_at_both_gates(self):
        self._assert_both_gates_reject(
            "WRONG_TARGET", objective_id="obj-some-other-bet")

    def test_thin_text_is_rejected_at_both_gates(self):
        self._assert_both_gates_reject("TOO_THIN", scope="短")

    def test_stale_context_version_is_rejected_at_both_gates(self):
        self._assert_both_gates_reject(
            "STALE_REF", business_context_ref="ctx-example-product v2020-01-01.1")

    def test_missing_context_version_is_rejected_at_both_gates(self):
        self._assert_both_gates_reject(
            "STALE_REF", business_context_ref="ctx-example-product")


if __name__ == "__main__":
    unittest.main()
