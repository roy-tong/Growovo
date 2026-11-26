"""C-01-Lite 合同：grow status --explain 必须给出最小委托投影。

契约：ai输出/ovo-C01-Lite-最小委托契约-v1-20261115.md
"""
import json
import tempfile
import unittest
from pathlib import Path

from core import grow_runbook as rb
from core.execution_policy import authorize
from core.grow_v2 import GrowRunV2

ACTIONS = ["x:reply", "email:message"]
STATES = {"NEEDS_INPUT", "READY", "NEEDS_REVIEW", "BLOCKED", "PARTIAL", "DELIVERED"}
CODES = {"INPUT_REQUIRED", "AUTH_REQUIRED", "REVIEW_REQUIRED", "UNSUPPORTED_SCOPE",
         "POLICY_DENIED", "RATE_LIMITED", "STALE_SOURCE",
         "INDETERMINATE_EXTERNAL_ACTION", "ARTIFACT_PARTIAL", "INTERNAL_ERROR"}


def _facts(tmp: Path) -> Path:
    facts = {
        "product": {"name": "Verovo", "what": "AI 账期分摊",
                    "url": "https://example.invalid", "stage": "launched",
                    "product_type": "devtool"},
        "icp": [{"hypothesis": "开发者", "tags": ["developers"],
                 "evidence_label": "hypothesis", "evidence": "待验证"}],
        "baseline": {"followers": 120},
        "value_events": [{"name": "激活", "source": "产品事件",
                          "is_approximation": False}],
        "constraints": {"red_lines": ["不做群发"]},
        "owned_assets": {"platforms": ["x", "email"]},
    }
    path = tmp / "facts.json"
    path.write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
    return path


class DelegationContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        authorize(self.dir, by="tester", actions=ACTIONS, days=90)
        self.path = self.dir / "grow" / "g1.json"
        GrowRunV2.launch(self.path, run_id="g1", facts_path=str(_facts(self.dir)),
                         goal_id="awareness.followers", channels=["x", "email"],
                         target=20, actions_per_week=7, started="2026-11-01",
                         handle="acct-a", data_dir=self.dir, skip_probe=True)
        self.run = GrowRunV2(self.path)

    def _explain(self):
        return rb.explain_status(self.dir, self.path, run_id="g1", on="2026-11-03")

    def test_delegation_block_is_complete(self):
        payload = self._explain()
        self.assertIn("delegation", payload)
        block = payload["delegation"]
        for key in ("task_id", "task_state", "next_actor", "next_required_action",
                    "blocked_reason_code", "result_ref", "authorization_scope",
                    "recovery_entry", "delegation_boundary"):
            self.assertIn(key, block, f"{key} missing")
        self.assertEqual("g1", block["task_id"])
        self.assertIn(block["task_state"], STATES)
        self.assertIn(block["next_actor"], ("human", "agent", "system"))
        self.assertTrue(block["next_required_action"])
        self.assertTrue(block["delegation_boundary"])

    def test_gated_states_always_name_a_human(self):
        """契约：NEEDS_INPUT / NEEDS_REVIEW / BLOCKED 时责任人必须是人。"""
        block = self._explain()["delegation"]
        if block["task_state"] in ("NEEDS_INPUT", "NEEDS_REVIEW", "BLOCKED"):
            self.assertEqual("human", block["next_actor"])
        self.assertIn(block["next_actor"], ("human", "agent", "system"))
        self.assertIn(block["blocked_reason_code"], CODES | {None})

    def test_paused_run_reports_blocked_and_recovery(self):
        self.run.arm(by="roy")
        self.run.pause()
        payload = self._explain()
        block = payload["delegation"]
        self.assertEqual("BLOCKED", block["task_state"])
        self.assertTrue(block["recovery_entry"])
        self.assertEqual("REVIEW_REQUIRED", block["blocked_reason_code"])
        self.assertIn("task_result", payload)
        self.assertEqual("needs_decision", payload["task_result"]["status"])
        self.assertTrue(payload["task_result"]["user_decision_required"])
        self.assertTrue(payload["task_result"]["next_actions"][0]["command"])

    def test_task_result_keeps_activation_data_gap_scoped(self):
        payload = self._explain()
        self.assertIn("task_result", payload)
        result = payload["task_result"]
        self.assertEqual("ovo.task-result/1", result["schema"])
        self.assertEqual("growovo", result["product"])
        self.assertEqual("g1", result["task_id"])
        self.assertEqual("g1", result["resume_ref"])
        gap = next(row for row in result["missing_conditions"]
                   if row["code"] == "PRODUCT_EVENT_SOURCE_MISSING")
        self.assertEqual("activation_retention", gap["scope"])
        self.assertFalse(gap["blocking"])
        self.assertNotEqual("unable_to_complete", result["status"])

    def test_ready_to_arm_requires_a_user_decision(self):
        self.assertTrue(hasattr(rb, "task_result_block"))
        result = rb.task_result_block(
            run_id="g-ready", canonical_status="ready_to_arm", next_actor="human",
            next_action="review and arm", next_command="mkt grow arm g-ready",
            blocked_code="AUTH_REQUIRED", artifacts=["grow/g-ready.json"])
        self.assertEqual("needs_decision", result["status"])
        self.assertTrue(result["user_decision_required"])
        self.assertEqual("mkt grow arm g-ready", result["next_actions"][0]["command"])

    def test_cancelled_run_is_unable_to_complete_with_a_recovery_entry(self):
        self.assertTrue(hasattr(rb, "task_result_block"))
        result = rb.task_result_block(
            run_id="g-cancelled", canonical_status="cancelled", next_actor="human",
            next_action="create a new run", next_command="mkt grow status g-cancelled",
            blocked_code="POLICY_DENIED", artifacts=[])
        self.assertEqual("unable_to_complete", result["status"])
        self.assertFalse(result["user_decision_required"])
        self.assertTrue(result["next_actions"])


if __name__ == "__main__":
    unittest.main()
