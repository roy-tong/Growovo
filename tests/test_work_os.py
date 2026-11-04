#!/usr/bin/env python3
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import metrics
from core.sop import SOPRegistry, SOPRunStore, lint_sop
from core.work import ContextStore, ObjectiveStore, WorkError, WorkStore, lint_work_order

ROOT = Path(__file__).resolve().parents[1]


class WorkOSCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.context = {
            "context_id": "ctx-example-product",
            "version": "2026-11-04.1",
            "source": {"kind": "user_declared", "ref": "fixture:user-input"},
            "product_facts": [{"fact": "example API"}],
            "value_event_definitions": [{"event": "first_successful_api_call"}],
            "non_goals": ["no autonomous publishing"],
            "approved_by": "human-owner",
            "content_sha256": "fixture-sha",
        }
        ContextStore(self.data).import_snapshot(self.context, by="human-owner")
        self.objective = {
            "objective_id": "obj-first-success-001",
            "version": "1",
            "context_ref": {"context_id": self.context["context_id"], "version": self.context["version"]},
            "business_question": "开发者能否独立完成首次成功调用",
            "subject_type": "developer",
            "primary_metric_ref": {"metric_id": "developer_activation_rate", "version": "1.0.0"},
            "window": "2026-11-05/2026-11-21",
            "owner": "person-product-growth-owner",
        }
        ObjectiveStore(self.data).create(self.objective, by="human-owner")
        self.order = json.loads((ROOT / "docs/gtm-os/examples/work-order.example.json").read_text(encoding="utf-8"))

    def test_context_is_immutable_and_user_declared(self):
        store = ContextStore(self.data)
        with self.assertRaises(WorkError):
            store.import_snapshot(self.context, by="human-owner")
        loaded = store.get(self.context["context_id"], self.context["version"])
        self.assertEqual("user_declared", loaded["source"]["kind"])

    def test_work_order_requires_accepted_inputs_before_ready(self):
        store = WorkStore(self.data)
        invalid = copy.deepcopy(self.order)
        invalid["input_bindings"][0]["acceptance_status"] = "submitted"
        store.create(invalid, by="owner")
        with self.assertRaises(WorkError):
            store.ready(invalid["work_order_id"], by="owner")
        self.assertEqual("blocked", store.get(invalid["work_order_id"])["state"])
        self.assertTrue(any("未验收" in e for e in lint_work_order(invalid)))

    def test_ready_start_and_complete_sop_with_evidence(self):
        store = WorkStore(self.data)
        store.create(self.order, by="owner")
        self.assertEqual([], lint_work_order(self.order))
        store.ready(self.order["work_order_id"], by="owner")
        runs = SOPRunStore(self.data)
        run = runs.start(self.order["work_order_id"], by="owner")
        self.assertEqual("running", store.get(self.order["work_order_id"])["state"])
        sop = SOPRegistry(self.data).get(self.order["sop_version_ref"]["sop_id"], self.order["sop_version_ref"]["version"])
        for index, step in enumerate(sop["steps"]):
            outputs = [{"deliverable_type": o["deliverable_type"],
                        "artifact_id": f"artifact-{index}",
                        "version": "1", "evidence_ref": f"fixture:output:{index}"}
                       for o in step["outputs"] if o.get("required", True)]
            results = [{"rule_id": d["rule_id"], "status": "pass",
                        "evidence_ref": f"fixture:dod:{index}"} for d in step["dod"]]
            approval = {"by": "human-reviewer", "scope": "fixture"} if step.get("approval", {}).get("required") else None
            run = runs.complete_step(run["run_id"], step["step_id"], outputs=outputs,
                                     dod_results=results, by="operator", approval=approval)
            if index + 1 < len(sop["steps"]):
                self.assertEqual(sop["steps"][index + 1]["step_id"], run["current_step_id"])
                self.assertEqual("ready", run["steps"][sop["steps"][index + 1]["step_id"]]["state"])
        self.assertEqual("done", run["state"])
        # Fresh projection has exactly the same state; no in-memory-only updates.
        self.assertEqual("done", runs.get(run["run_id"])["state"])

    def test_sop_rejects_missing_dod_evidence(self):
        WorkStore(self.data).create(self.order, by="owner")
        WorkStore(self.data).ready(self.order["work_order_id"], by="owner")
        runs = SOPRunStore(self.data)
        run = runs.start(self.order["work_order_id"], by="owner")
        sop = SOPRegistry(self.data).get(run["sop_id"], run["sop_version"])
        step = sop["steps"][0]
        outputs = [{"deliverable_type": o["deliverable_type"], "artifact_id": "a1",
                    "version": "1", "evidence_ref": "fixture:out"} for o in step["outputs"]]
        results = [{"rule_id": d["rule_id"], "status": "pass", "evidence_ref": ""} for d in step["dod"]]
        with self.assertRaisesRegex(ValueError, "evidence_ref"):
            runs.complete_step(run["run_id"], step["step_id"], outputs=outputs,
                               dod_results=results, by="operator",
                               approval={"by": "human", "scope": "technical"})

    def test_sop_lint_rejects_vague_or_short_process(self):
        self.assertTrue(lint_sop({"sop_id": "bad", "version": "1", "capability_id": "C18", "steps": []}))

    def test_dependency_must_be_accepted(self):
        store = WorkStore(self.data)
        first = copy.deepcopy(self.order)
        store.create(first, by="owner")
        second = copy.deepcopy(self.order)
        second["work_order_id"] = "wo-dependent"
        second["dependencies"] = [first["work_order_id"]]
        store.create(second, by="owner")
        with self.assertRaisesRegex(WorkError, "依赖"):
            store.ready(second["work_order_id"], by="owner")


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self.registry = metrics.MetricRegistry(Path(tempfile.mkdtemp()))
        self.metric = self.registry.get("developer_activation_rate", "1.0.0")
        self.events = [
            {"event_type": "eligible_tutorial_cohort_entry", "developer_id": "d1"},
            {"event_type": "eligible_tutorial_cohort_entry", "developer_id": "d2"},
            {"event_type": "first_successful_api_call", "developer_id": "d1", "verified": True, "approximated": False},
            {"event_type": "first_successful_api_call", "developer_id": "d1", "verified": True, "approximated": False},
            {"event_type": "first_successful_api_call", "developer_id": "d2", "verified": False, "approximated": False},
        ]

    def test_dedupes_and_does_not_count_unverified(self):
        result = metrics.measure(self.metric, self.events)
        self.assertEqual(2, result["denominator"])
        self.assertEqual(1, result["numerator"])
        self.assertEqual(0.5, result["value"])

    def test_zero_denominator_is_null_not_zero_percent(self):
        result = metrics.measure(self.metric, [])
        self.assertIsNone(result["value"])
        self.assertEqual("insufficient_denominator", result["status"])

    def test_pending_maturity_is_not_failure(self):
        result = metrics.measure(self.metric, self.events,
                                 cohort_end="2026-11-01T00:00:00+08:00",
                                 as_of="2026-11-02T00:00:00+08:00")
        self.assertEqual("pending_maturity", result["status"])
        self.assertIsNone(result["value"])

    def test_scope_has_no_full_history_fallback(self):
        result = metrics.measure(self.metric, self.events, scope={"campaign": "current-only"})
        self.assertEqual("insufficient_denominator", result["status"])

    def test_date_only_until_includes_the_whole_day(self):
        events = [
            {"event_type": "eligible_tutorial_cohort_entry", "developer_id": "d1", "ts": "2026-11-14T23:59:00+08:00"},
            {"event_type": "first_successful_api_call", "developer_id": "d1", "verified": True,
             "approximated": False, "ts": "2026-11-14T23:59:30+08:00"},
        ]
        result = metrics.measure(self.metric, events, scope={"since": "2026-11-14", "until": "2026-11-14"})
        self.assertEqual(1.0, result["value"])


if __name__ == "__main__":
    unittest.main()
