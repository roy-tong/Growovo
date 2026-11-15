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
        block = self._explain()["delegation"]
        self.assertEqual("BLOCKED", block["task_state"])
        self.assertTrue(block["recovery_entry"])
        self.assertEqual("REVIEW_REQUIRED", block["blocked_reason_code"])


if __name__ == "__main__":
    unittest.main()
