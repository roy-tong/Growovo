#!/usr/bin/env python3
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.approval import execution_fingerprint
from core.deliverables import DeliverableError, DeliverableStore, _rubrics
from core.handoffs import HandoffError, HandoffStore
from core.sop import SOPRegistry, SOPRunStore
from core.templates import get_template
from core.work import ContextStore, ObjectiveStore, WorkStore
from core.work_actions import WorkActionError, materialize

ROOT = Path(__file__).resolve().parents[1]


def fields_for(template_id, deliverable_id="del-1", work_order_id="wo-devrel-tutorial-001"):
    template = get_template(template_id)
    fields = {slot: f"fixture:{slot}" for slot in template["required_slots"]}
    fields["deliverable_id"] = deliverable_id
    fields["work_order_id"] = work_order_id
    fields["objective_id"] = "obj-first-success-001"
    fields["evidence_refs"] = [{"source_id": "fixture-source", "locator": "fixture://evidence/1"}]
    return fields


def review_results(template_id, verdict="pass"):
    return [{"rubric_id": row["rubric_id"], "verdict": verdict,
             "evidence_ref": f"fixture:review:{row['rubric_id']}",
             "note": "fixture"} for row in _rubrics(get_template(template_id))]


class DeliverableCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.store = DeliverableStore(self.data)
        self.spec = {
            "deliverable_id": "del-tutorial",
            "version": "1",
            "work_order_id": "wo-devrel-tutorial-001",
            "template_id": "T30",
            "template_version": "1.0.0",
            "deliverable_type": "developer_tutorial",
            "fields": fields_for("T30", "del-tutorial"),
            "risk_class": "R3",
        }

    def test_submitted_is_not_accepted_and_author_cannot_self_review(self):
        self.store.create(self.spec, by="author")
        self.store.submit("del-tutorial", by="author")
        self.assertEqual("submitted", self.store.get("del-tutorial")["state"])
        with self.assertRaisesRegex(DeliverableError, "不能终审"):
            self.store.review("del-tutorial", results=review_results("T30"), by="author")
        with self.assertRaisesRegex(DeliverableError, "Agent"):
            self.store.review("del-tutorial", results=review_results("T30"), by="reviewer", actor_type="agent")
        reviewed = self.store.review("del-tutorial", results=review_results("T30"), by="reviewer")
        self.assertEqual("accepted", reviewed["state"])

    def test_missing_evidence_or_rubric_blocks_acceptance(self):
        bad = copy.deepcopy(self.spec)
        bad["fields"]["evidence_refs"] = []
        self.store.create(bad, by="author")
        with self.assertRaisesRegex(DeliverableError, "evidence_refs"):
            self.store.submit("del-tutorial", by="author")
        good = copy.deepcopy(self.spec)
        good["deliverable_id"] = "del-good"
        good["fields"] = fields_for("T30", "del-good")
        self.store.create(good, by="author")
        self.store.submit("del-good", by="author")
        with self.assertRaisesRegex(DeliverableError, "缺 QA"):
            self.store.review("del-good", results=[], by="reviewer")

    def test_handoff_requires_accepted_artifact_and_receiver(self):
        handoffs = HandoffStore(self.data)
        spec = {"handoff_id": "ho-1", "work_order_id": "wo-devrel-tutorial-001",
                "from_role": "46", "to_role": "24", "receiver_id": "receiver",
                "sla_due": "2026-11-30T00:00:00+08:00",
                "artifact_refs": [{"id": "del-tutorial", "version": "1"}],
                "acceptance_rules": ["技术接收人确认教程可复现"]}
        self.store.create(self.spec, by="author")
        with self.assertRaisesRegex(HandoffError, "accepted"):
            handoffs.create(spec, by="author")
        self.store.submit("del-tutorial", by="author")
        self.store.review("del-tutorial", results=review_results("T30"), by="reviewer")
        handoffs.create(spec, by="author")
        with self.assertRaisesRegex(HandoffError, "指定接收人"):
            handoffs.accept("ho-1", by="not-receiver")
        with self.assertRaisesRegex(HandoffError, "Agent"):
            handoffs.accept("ho-1", by="receiver", actor_type="agent")
        self.assertEqual("accepted", handoffs.accept("ho-1", by="receiver")["state"])

    def test_rejection_returns_to_explicit_step(self):
        self.store.create(self.spec, by="author")
        self.store.submit("del-tutorial", by="author")
        self.store.review("del-tutorial", results=review_results("T30"), by="reviewer")
        handoffs = HandoffStore(self.data)
        handoffs.create({"handoff_id": "ho-2", "work_order_id": "wo-devrel-tutorial-001",
                         "from_role": "46", "to_role": "24", "receiver_id": "receiver",
                         "sla_due": "2026-11-30T00:00:00+08:00",
                         "artifact_refs": [{"id": "del-tutorial", "version": "1"}],
                         "acceptance_rules": ["可复现"]}, by="author")
        rejected = handoffs.reject("ho-2", by="receiver", reason="缺环境版本", return_step_id="build-and-reproduce")
        self.assertEqual("rejected", rejected["state"])
        self.assertEqual("build-and-reproduce", rejected["rejection"]["return_step_id"])


class WorkActionCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        context = {"context_id": "ctx-example-product", "version": "2026-11-04.1",
                   "source": {"kind": "user_declared", "ref": "fixture"},
                   "value_event_definitions": [{"event": "first_successful_api_call"}],
                   "non_goals": ["no sends"], "approved_by": "human", "content_sha256": "fixture"}
        ContextStore(self.data).import_snapshot(context, by="human")
        ObjectiveStore(self.data).create({
            "objective_id": "obj-first-success-001", "version": "1",
            "context_ref": {"context_id": context["context_id"], "version": context["version"]},
            "business_question": "能否首次成功", "subject_type": "developer",
            "primary_metric_ref": {"metric_id": "developer_activation_rate", "version": "1.0.0"},
            "window": "2026-11", "owner": "human"}, by="human")
        self.order = json.loads((ROOT / "docs/gtm-os/examples/work-order.example.json").read_text(encoding="utf-8"))
        WorkStore(self.data).create(self.order, by="human")
        WorkStore(self.data).ready(self.order["work_order_id"], by="human")
        self.runs = SOPRunStore(self.data)
        self.run = self.runs.start(self.order["work_order_id"], by="human")
        self.deliverables = DeliverableStore(self.data)
        self.deliverable_spec = {
            "deliverable_id": "del-action", "version": "1",
            "work_order_id": self.order["work_order_id"], "template_id": "T30",
            "template_version": "1.0.0", "deliverable_type": "distribution_and_followup_pack",
            "fields": fields_for("T30", "del-action"), "risk_class": "R3"}

    def _advance_to_authorization(self):
        sop = SOPRegistry(self.data).get(self.run["sop_id"], self.run["sop_version"])
        for index, step in enumerate(sop["steps"][:3]):
            outputs = [{"deliverable_type": o["deliverable_type"], "artifact_id": f"a-{index}",
                        "version": "1", "evidence_ref": "fixture:output"} for o in step["outputs"]]
            dod = [{"rule_id": d["rule_id"], "status": "pass", "evidence_ref": "fixture:dod"} for d in step["dod"]]
            approval = {"by": "tech-reviewer", "scope": "fixture"} if step.get("approval", {}).get("required") else None
            self.run = self.runs.complete_step(self.run["run_id"], step["step_id"], outputs=outputs,
                                               dod_results=dod, by="operator", approval=approval)
        self.assertEqual("authorize-execution", self.run["current_step_id"])

    def _materialize(self):
        return materialize(
            self.data, work_order_id=self.order["work_order_id"], step_id="authorize-execution",
            deliverable_id="del-action", deliverable_version="1",
            strategy={"primary": {"id": "github", "name": "GitHub"}},
            targets=[{"target": "issue:example/repo#1", "body": "经审批的教程评论"}],
            campaign="work-action-demo", account="demo-account", by="operator")

    def test_accepted_deliverable_materializes_plan_queue_without_sending(self):
        self._advance_to_authorization()
        self.deliverables.create(self.deliverable_spec, by="author")
        self.deliverables.submit("del-action", by="author")
        self.deliverables.review("del-action", results=review_results("T30"), by="reviewer")
        result = self._materialize()
        self.assertFalse((self.data / "ledger.jsonl").exists())
        plan = json.loads(Path(result["plan"]).read_text(encoding="utf-8"))
        self.assertEqual(self.order["work_order_id"], plan["provenance"]["work_order_id"])
        self.assertFalse(plan["review"]["human_approved"])
        before = execution_fingerprint(plan, data_dir=self.data)
        plan["provenance"]["deliverable_ref"]["content_sha256"] = "tampered"
        after = execution_fingerprint(plan, data_dir=self.data)
        self.assertNotEqual(before, after)

    def test_unaccepted_deliverable_cannot_materialize(self):
        self._advance_to_authorization()
        self.deliverables.create(self.deliverable_spec, by="author")
        with self.assertRaisesRegex(WorkActionError, "accepted"):
            self._materialize()
        self.assertFalse((self.data / "plans" / "work-action-demo.json").exists())


if __name__ == "__main__":
    unittest.main()
