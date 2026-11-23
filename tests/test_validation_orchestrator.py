import json
import tempfile
import unittest
from pathlib import Path

from core import validation_orchestrator
from core.operations_log import OperationsLog
from tests.test_operations_log import _full_entry


def task(task_id="task-1", product="growovo", run_id="run-1", go=False):
    return {
        "schema": "growovo.validation-task/1",
        "task_id": task_id,
        "product": product,
        "kind": "expert_review",
        "owner": "decision-owner",
        "goal": "obtain one reviewable external outcome",
        "inputs": {
            "required": [],
            "schedule_ids": ["schedule-1"],
            "plan_ids": ["plan-1"],
            "run_id": run_id,
            "second_run_id": run_id + "-second",
        },
        "execution": {"go": go},
        "metrics": {
            "first_value_seconds": 60,
            "human_minutes": 5,
            "major_errors": 0,
            "disposition": "accepted",
        },
    }


class ValidationOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        (self.data / "schedules").mkdir(parents=True)
        (self.data / "plans").mkdir()

    def write_plan(self, config, second_batch_proof=False):
        path = self.root / "plan.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def write_schedule(self, task, go=False):
        (self.data / "schedules" / "schedule-1.json").write_text(json.dumps({
            "id": "schedule-1", "go": go, "waves_done": 0, "status": "active",
        }), encoding="utf-8")
        (self.data / "plans" / "plan-1.json").write_text(json.dumps({
            "review": {"human_approved": go, "approved_at": "2026-11-23T10:00:00+08:00" if go else None},
        }), encoding="utf-8")

    def add_operation(self, run_id, naive=False):
        timestamp = "2026-11-23T10:00:00" if naive else "2026-11-23T10:00:00+08:00"
        OperationsLog(self.data).append(_full_entry(
            run_id=run_id, campaign=run_id, result="committed",
            started_at=timestamp, finished_at=timestamp))

    def test_dry_run_and_missing_real_inputs_stay_explicit(self):
        current = task()
        supplier = task(task_id="supplier-1", product="verovo")
        supplier["inputs"]["required"] = ["invoice", "contract"]
        self.write_schedule(current)
        path = self.write_plan({"schema": "growovo.validation-plan/1",
                                "workspace": "data", "tasks": [current, supplier]})
        result = validation_orchestrator.run(path)
        states = {row["task_id"]: row["status"] for row in result["tasks"]}
        self.assertEqual({"task-1": "dry_run", "supplier-1": "needs_input"}, states)
        self.assertEqual("NOT_ACCEPTED", result["business_verdict"]["status"])
        self.assertEqual("none", result["external_action"])
        self.assertEqual("PASS", result["contract_validation"]["status"])

    def test_complete_external_evidence_can_reach_accepted(self):
        current = task(go=True)
        self.write_schedule(current, go=True)
        self.add_operation("run-1")
        self.add_operation("run-1-second")
        (self.data / "review-feedback").mkdir()
        (self.data / "review-feedback" / "reply.json").write_text("{}", encoding="utf-8")
        (self.data / "state").mkdir(exist_ok=True)
        (self.data / "state" / "contributions.jsonl").write_text(json.dumps({
            "contribution_id": "c1", "status": "merged", "change_ref": "commit:1",
        }) + "\n", encoding="utf-8")
        path = self.write_plan({"schema": "growovo.validation-plan/1",
                                "workspace": "data", "tasks": [current]})
        result = validation_orchestrator.run(path)
        self.assertEqual("accepted", result["tasks"][0]["status"])
        self.assertEqual("ACCEPTED", result["business_verdict"]["status"])
        self.assertEqual(1.0, result["metrics"]["effective_autonomous_delivery_rate"])

    def test_second_batch_self_declaration_is_not_evidence(self):
        current = task(go=True)
        current["metrics"]["second_independent_batch"] = True
        self.write_schedule(current, go=True)
        self.add_operation("run-1")
        (self.data / "review-feedback").mkdir()
        (self.data / "review-feedback" / "reply.json").write_text("{}", encoding="utf-8")
        (self.data / "state").mkdir(exist_ok=True)
        (self.data / "state" / "contributions.jsonl").write_text(json.dumps({
            "contribution_id": "c1", "status": "merged", "change_ref": "commit:1",
        }) + "\n", encoding="utf-8")
        path = self.write_plan({"schema": "growovo.validation-plan/1",
                                "workspace": "data", "tasks": [current]})
        result = validation_orchestrator.run(path)
        self.assertEqual("material", result["tasks"][0]["status"])
        self.assertEqual("NOT_ACCEPTED", result["business_verdict"]["status"])

    def test_naive_operation_time_blocks_final_acceptance(self):
        current = task(go=True)
        self.write_schedule(current, go=True)
        self.add_operation("run-1", naive=True)
        self.add_operation("run-1-second")
        (self.data / "review-feedback").mkdir()
        (self.data / "review-feedback" / "reply.json").write_text("{}", encoding="utf-8")
        (self.data / "state").mkdir(exist_ok=True)
        (self.data / "state" / "contributions.jsonl").write_text(json.dumps({
            "contribution_id": "c1", "status": "merged", "change_ref": "commit:1",
        }) + "\n", encoding="utf-8")
        path = self.write_plan({"schema": "growovo.validation-plan/1",
                                "workspace": "data", "tasks": [current]})
        result = validation_orchestrator.run(path)
        self.assertEqual("UNCERTAIN", result["time_semantics"]["status"])
        self.assertEqual("NOT_ACCEPTED", result["business_verdict"]["status"])


if __name__ == "__main__":
    unittest.main()
