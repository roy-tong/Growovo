#!/usr/bin/env python3
"""机器可读交付合同（评审 2026-11-17 §4.2/§7 P0）+ 首次交互一致性。

docs/delivery-contract.json 是 Growovo 的交付合同正本；mkt init 首次交互
从同一份文件渲染边界提示，文档与 CLI 不允许各说各话。
"""
import json
import tempfile
import unittest
from pathlib import Path

from core import operations_log
from core import onboarding

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "delivery-contract.json"

TERMINAL_STATES = ("DELIVERED", "DELIVERED_WITH_GAP", "BLOCKED_USER_INPUT",
                   "UNSUPPORTED", "FAILED_UNRESOLVED")

EVIDENCE_GRADES = ("synthetic", "local_live_read", "live_external_read",
                   "live_external_write", "receipt_verified",
                   "business_outcome_observed")


class ContractFileTests(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_required_sections(self):
        for key in ("product", "version", "supported_intentions",
                    "required_inputs", "terminal_states", "business_goal", "exclusions",
                    "requires_procurement_template",
                    "operations_log_minimum_fields", "evidence_grades",
                    "live_status"):
            self.assertIn(key, self.contract, f"交付合同缺 {key}")

    def test_terminal_states_use_review_vocabulary(self):
        self.assertEqual(sorted(TERMINAL_STATES),
                         sorted(self.contract["terminal_states"]))

    def test_business_goal_does_not_equate_sends_with_business_success(self):
        goal = self.contract["business_goal"]
        self.assertEqual("持续执行已授权动作并交付每日结果", goal["product_goal"])
        self.assertEqual(["qualified_interaction", "activation", "value_event"],
                         goal["business_outcomes"])
        self.assertEqual(["independent", "verified"], goal["required_evidence"])
        for item in ("committed", "发送量", "日结果单"):
            self.assertIn(item, goal["not_business_success"])
        self.assertEqual("not_observed",
                         goal["unobserved_outcome"]["outcome_status"])
        self.assertEqual("unknown", goal["unobserved_outcome"]["reading"])
        self.assertIsNone(goal["unobserved_outcome"]["value"])

    def test_evidence_grades_use_review_vocabulary(self):
        self.assertEqual(list(EVIDENCE_GRADES), self.contract["evidence_grades"])

    def test_operations_log_fields_match_code(self):
        self.assertEqual(list(operations_log.MINIMUM_FIELDS),
                         self.contract["operations_log_minimum_fields"],
                         "合同的最低字段清单必须与代码同一份")

    def test_procurement_template_matches_module(self):
        template = self.contract["requires_procurement_template"]
        self.assertEqual("BLOCKED_USER_INPUT", template["terminal_state"])
        self.assertEqual("requires_procurement", template["reason"])
        self.assertIs(False, template["transaction_executed"])

    def test_exclusions_name_the_hard_boundaries(self):
        joined = "；".join(self.contract["exclusions"])
        for must in ("采购", "付款", "涨粉", "挑战页", "自动重发"):
            self.assertIn(must, joined)

    def test_live_status_is_honest(self):
        self.assertIn("live_status", self.contract)
        self.assertNotIn("live-proven",
                         json.dumps(self.contract["live_status"], ensure_ascii=False),
                         "live 未验收前不得标 live-proven")


class InitConsistencyTests(unittest.TestCase):
    def test_init_render_includes_contract_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = onboarding.init(Path(tmp))
        self.assertIn("delivery_contract", info,
                      "mkt init 必须带出交付合同摘要")
        rendered = onboarding.render_init(info)
        for state in TERMINAL_STATES:
            self.assertIn(state, rendered)
        for exclusion in info["delivery_contract"]["exclusions"]:
            self.assertIn(exclusion, rendered)
        self.assertIn("requires_procurement", rendered)
        # 首次交互必须声明默认 dry-run、真实发送需要显式授权
        self.assertIn("dry-run", rendered)
        self.assertIn("授权", rendered)


if __name__ == "__main__":
    unittest.main()
