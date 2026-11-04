"""Task-outcome projection keeps action, task, and business facts independent."""
import json
import tempfile
import unittest
from pathlib import Path

from core import execute, planlint, result, strategy, task_outcome


def make_record(task_id, *, action_result="acked", task_status="unknown",
                business_status="inconclusive"):
    return task_outcome.build_record(
        task_id=task_id,
        channel="github",
        action_taken="offline fixture action",
        action_result=action_result,
        task_outcome=task_status,
        business_outcome=business_status,
        decision_basis="offline result projection test",
        verification_method="comment_url",
        evidence_refs=[f"https://example.invalid/tasks/{task_id}"],
        outcome_evidence_refs=["outcomes/events.jsonl#independent-business-proof"],
        escalation_reason="human review required" if task_status == "needs_human" else "",
    )


class TaskOutcomeResultTests(unittest.TestCase):
    def test_committed_adds_only_platform_action(self):
        report = result.collect(ledger_entries=[{"result": "committed"}])
        self.assertEqual(1, report["platform"]["sent"])
        self.assertEqual(0, report["task"]["records"])
        self.assertEqual(0, report["task"]["resolved"])
        self.assertEqual(0, report["task"]["unknown"])
        self.assertEqual(0, report["task"]["moved"])
        self.assertEqual({}, report["business"]["by_type"])

    def test_task_records_do_not_fabricate_action_or_business_layers(self):
        records = [
            make_record("resolved-1", task_status="resolved", business_status="observed"),
            make_record("unknown-1", action_result="sent", task_status="unknown"),
        ]
        report = result.collect(task_outcome_records=records)
        self.assertEqual(0, report["platform"]["total"])
        self.assertEqual(2, report["task"]["records"])
        self.assertEqual(1, report["task"]["resolved"])
        self.assertEqual(1, report["task"]["unknown"])
        self.assertEqual({}, report["business"]["by_type"])
        self.assertEqual("部分观察", report["observation"])
        rendered = result.render(report)
        self.assertIn("任务记录 2", rendered)
        self.assertIn("解决 1", rendered)

    def test_three_layers_require_three_independent_facts(self):
        records = [make_record("resolved-1", task_status="resolved", business_status="observed")]
        report = result.collect(
            ledger_entries=[{"result": "committed"}],
            task_outcome_records=records,
            outcome_events=[{"event_type": "paid", "verified": True}],
        )
        self.assertEqual(1, report["platform"]["sent"])
        self.assertEqual(1, report["task"]["resolved"])
        self.assertEqual(1, report["business"]["paid"])
        self.assertEqual(1, report["business"]["by_type"]["paid"])

    def test_scope_reads_metadata_without_full_history_fallback(self):
        current = make_record("current", task_status="resolved")
        current["metadata"] = {"campaign": "current", "target": "subject-1"}
        other = make_record("other", task_status="resolved")
        other["metadata"] = {"campaign": "other", "target": "subject-2"}
        rows = result.scoped_task_outcomes([current, other], {"campaign": "current", "subject": "subject-1"})
        self.assertEqual(1, len(rows))
        self.assertEqual("current", rows[0]["task_id"])

    def test_old_task_layer_call_and_record_alias_remain_compatible(self):
        record = make_record("partial-1", task_status="partial")
        old_call = result.task_layer(None, {"moved": 2}, [record])
        self.assertEqual(2, old_call["moved"])
        self.assertEqual(1, old_call["partial"])
        alias = result.collect(task_outcomes=[record])
        self.assertEqual(1, alias["task"]["records"])
        self.assertEqual(1, alias["task"]["partial"])


class ExecuteEvidenceTests(unittest.TestCase):
    def test_generated_plan_is_unverified_and_still_passes_planlint(self):
        with tempfile.TemporaryDirectory() as tmp:
            built = strategy.build({"name": "X"}, focus={"primary": "email"}, analysis={})
            output = execute.build(
                built,
                [{"target": "user@example.com", "body": "offline test body"}],
                campaign="unverified-evidence",
                data_dir=Path(tmp),
            )
            plan = json.loads(Path(output["plan"]).read_text(encoding="utf-8"))
            self.assertEqual("unverified", plan["audience"]["evidence_grade"])
            self.assertNotEqual("B", plan["audience"]["evidence_grade"])
            self.assertEqual([], planlint.lint_plan(plan))


if __name__ == "__main__":
    unittest.main()
