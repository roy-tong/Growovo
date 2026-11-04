"""G1：宿主语义候选的确定性校验、去重、对抗输入与 Opportunity 集成。"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import opportunity, signal_interpretation
from mcp_server import _call_tool


ROOT = Path(__file__).resolve().parents[1]


def candidate(identifier="candidate-1"):
    return {
        "id": identifier,
        "source": {"kind": "post", "ref": "forum/posts/101"},
        "subject": {"id": "user-101", "role": "product-manager"},
        "semantics": {
            "discussion_type": "problem",
            "problem": "访谈证据无法回溯到原话",
            "problem_evidence_ref": "forum/posts/101#problem",
            "tool_seeking": {"status": "seeking", "evidence_ref": "forum/posts/101#tool"},
            "scenario": "整理用户访谈",
        },
        "alternative": {
            "name": "手工表格", "status": "unsatisfied",
            "unsatisfied_reasons": [{"reason": "来源 ID 在复制时丢失", "evidence_ref": "forum/posts/101#alternative"}],
        },
        "contact_eligibility": {
            "status": "eligible", "basis": "允许在原公开线程回答",
            "evidence_ref": "forum/rules#participation",
        },
        "product_fit": {"value_ids": ["research-evidence"], "audience_ids": ["product-managers"]},
    }


def facts():
    return {
        "value": [{"id": "research-evidence", "text": "研究证据可复现"}],
        "target_audience": [{"id": "product-managers", "text": "产品经理"}],
        "strengths": [{"id": "traceable", "text": "来源可回溯"}],
    }


class SignalInterpretationTests(unittest.TestCase):
    def test_qualified_candidate_separates_tool_seeking_from_commercial_intent(self):
        row = candidate()
        snapshot = copy.deepcopy(row)
        result = signal_interpretation.interpret(row)
        self.assertEqual(snapshot, row)
        self.assertEqual("qualified", result["status"])
        self.assertEqual("seeking", result["semantics"]["tool_seeking"]["status"])
        self.assertEqual("not_observed", result["commercial_intent"]["status"])
        self.assertEqual("unsatisfied", result["alternative"]["status"])
        self.assertEqual("eligible", result["contact_eligibility"]["status"])

    def test_technical_discussion_is_not_need_even_with_tool_and_purchase_words(self):
        row = candidate("tech-1")
        row["semantics"]["discussion_type"] = "technical_discussion"
        row["semantics"]["problem"] = "讨论 Python 依赖注入与购买工具"
        row["purchase_intent"] = True
        result = signal_interpretation.interpret(row)
        self.assertEqual("rejected", result["status"])
        self.assertIsNone(result["opportunity_signal"])
        self.assertTrue(any("技术讨论" in issue for issue in result["issues"]))

    def test_false_or_self_declared_purchase_intent_never_upgrades(self):
        row = candidate("intent-1")
        row["purchase_intent"] = True
        row["commercial_evidence"] = {
            "kind": "purchase_intent", "observed": True, "source_ref": "forum/posts/101#buy",
        }
        result = signal_interpretation.interpret(row)
        self.assertEqual("self_declared", result["commercial_intent"]["status"])
        self.assertEqual([], result["commercial_intent"]["evidence_refs"])
        self.assertTrue(any("self-declared" in risk for risk in result["risks"]))

    def test_independent_commercial_observation_requires_traceable_evidence(self):
        row = candidate()
        row["commercial_evidence"] = {
            "kind": "budget_confirmed", "observed": True, "source_ref": "crm/budget/42",
        }
        result = signal_interpretation.interpret(row)
        self.assertEqual("observed", result["commercial_intent"]["status"])
        self.assertEqual(["crm/budget/42"], result["commercial_intent"]["evidence_refs"])

    def test_uncontactable_subject_is_rejected_and_not_an_executable_opportunity(self):
        row = candidate("blocked-1")
        row["contact_eligibility"] = {"status": "dnc", "basis": "明确拒绝联系", "evidence_ref": "dnc/1"}
        result = signal_interpretation.interpret(row)
        self.assertEqual("rejected", result["status"])
        self.assertEqual("ineligible", result["contact_eligibility"]["status"])
        discovered = opportunity.discover(facts(), {"semantic_candidates": [row]})
        self.assertEqual([], discovered["candidates"])
        self.assertEqual(1, discovered["summary"]["semantic_rejected"])

    def test_alternative_claim_requires_specific_traceable_dissatisfaction(self):
        row = candidate()
        row["alternative"]["unsatisfied_reasons"] = []
        result = signal_interpretation.interpret(row)
        self.assertEqual("needs_review", result["status"])
        self.assertTrue(any("具体原因" in issue for issue in result["issues"]))
        row["alternative"] = {"name": "现有工具", "status": "satisfied"}
        self.assertEqual("rejected", signal_interpretation.interpret(row)["status"])

    def test_missing_source_subject_or_contact_evidence_stays_unverified(self):
        row = candidate()
        row["source"] = {}
        row["semantics"]["problem_evidence_ref"] = ""
        row["semantics"]["tool_seeking"]["evidence_ref"] = ""
        row["subject"]["id"] = ""
        row["contact_eligibility"]["evidence_ref"] = ""
        result = signal_interpretation.interpret(row)
        self.assertEqual("needs_review", result["status"])
        for text in ("source_ref", "subject.id", "接触资格"):
            self.assertTrue(any(text in issue for issue in result["issues"]), text)

    def test_deterministic_dedup_does_not_merge_independent_sources(self):
        first = candidate("same-a")
        second = copy.deepcopy(first)
        second["id"] = "same-b"
        independent = candidate("independent")
        independent["source"] = {"kind": "ticket", "ref": "support/tickets/900"}
        result = signal_interpretation.interpret_many([first, second, independent])
        self.assertEqual(3, result["summary"]["input"])
        self.assertEqual(2, result["summary"]["unique"])
        self.assertEqual(1, result["summary"]["duplicates"])
        duplicate = next(row for row in result["candidates"] if row["duplicate_ids"])
        self.assertEqual(["same-b"], duplicate["duplicate_ids"])

    def test_opportunity_consumes_only_qualified_interpreted_candidates(self):
        good = candidate()
        tech = candidate("tech-2")
        tech["source"] = {"ref": "forum/posts/102"}
        tech["semantics"]["discussion_type"] = "technical_discussion"
        result = opportunity.discover(facts(), {"semantic_candidates": [good, tech]})
        self.assertEqual(1, len(result["candidates"]))
        evidence = result["candidates"][0]["evidence"]
        self.assertEqual("host_interpreted_candidate", evidence["demand_status"])
        self.assertEqual("candidate-1", evidence["interpretation_id"])
        self.assertEqual("matched", result["candidates"][0]["demand_match"]["status"])
        self.assertEqual(1, result["signal_interpretation"]["summary"]["rejected"])


class SignalSurfaceTests(unittest.TestCase):
    def test_readonly_cli_and_mcp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.json"
            path.write_text(json.dumps({"semantic_candidates": [candidate()]}), encoding="utf-8")
            before = set(Path(tmp).iterdir())
            proc = subprocess.run(
                [sys.executable, str(ROOT / "mkt.py"), "signal-interpret", "--candidates", str(path), "--json"],
                cwd=ROOT, env=dict(os.environ, MKT_HOME=tmp), text=True, capture_output=True,
            )
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(1, payload["summary"]["qualified"])
            self.assertEqual(before, set(Path(tmp).iterdir()))  # 只读，不落状态文件
        out = _call_tool("mkt_signal_interpretation", {"candidates": [candidate()]})
        self.assertIn('"qualified": 1', out["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
