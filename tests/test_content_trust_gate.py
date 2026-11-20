#!/usr/bin/env python3
"""G-18：字段非空之外，内容必须可用、对象必须正确。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.deliverables import (
    ContentUnusableError,
    DeliverableError,
    DeliverableStore,
    _rubrics,
)
from core.templates import get_template
from core.work import ContextStore, ObjectiveStore, WorkStore

ROOT = Path(__file__).resolve().parents[1]
SLOT_TEXT = "{slot}：写入可核对的业务内容，覆盖对象、动作与验收方式"


def usable_fields(template_id, deliverable_id, order):
    template = get_template(template_id)
    fields = {slot: SLOT_TEXT.format(slot=slot) for slot in template["required_slots"]}
    fields["deliverable_id"] = deliverable_id
    fields["work_order_id"] = order["work_order_id"]
    fields["objective_id"] = order["objective_ref"]["objective_id"]
    fields["business_context_ref"] = (
        f"{order['context_ref']['context_id']} v{order['context_ref']['version']}")
    fields["receiver"] = order["receiver"]["actor_id"]
    fields["evidence_refs"] = [{"source_id": "gate-source", "locator": "gate://evidence/1"}]
    return fields


class ContentTrustGateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.order = json.loads((ROOT / "core/work_order_demo.json").read_text(encoding="utf-8"))
        self.order["work_order_id"] = "wo-gate-001"
        context = {
            "context_id": "ctx-example-product", "version": "2026-11-04.1",
            "source": {"kind": "user_declared", "ref": "gate:user-input"},
            "value_event_definitions": [{"event": "first_successful_api_call"}],
            "non_goals": ["不自动发布"], "approved_by": "gate-human",
            "content_sha256": "gate-context-sha",
        }
        ContextStore(self.data).import_snapshot(context, by="gate-human")
        ObjectiveStore(self.data).create({
            "objective_id": "obj-first-success-001", "version": "1",
            "context_ref": {"context_id": context["context_id"], "version": context["version"]},
            "business_question": "开发者能否独立完成首次成功调用",
            "subject_type": "developer",
            "primary_metric_ref": {"metric_id": "developer_activation_rate", "version": "1.0.0"},
            "window": "2026-11", "owner": "gate-owner",
        }, by="gate-human")
        work = WorkStore(self.data)
        work.create(self.order, by="gate-human")
        work.ready(self.order["work_order_id"], by="gate-human")
        self.store = DeliverableStore(self.data)

    def _spec(self, deliverable_id, **overrides):
        fields = usable_fields("T30", deliverable_id, self.order)
        fields.update(overrides)
        return {
            "deliverable_id": deliverable_id,
            "version": "1",
            "work_order_id": self.order["work_order_id"],
            "template_id": "T30",
            "template_version": "1.0.0",
            "deliverable_type": "developer_tutorial",
            "fields": fields,
            "risk_class": "R3",
        }

    def _submit(self, deliverable_id, **overrides):
        self.store.create(self._spec(deliverable_id, **overrides), by="gate-author")
        return self.store.submit(deliverable_id, by="gate-author")

    def _assert_unusable(self, code, **overrides):
        with self.assertRaises(ContentUnusableError) as caught:
            self._submit(f"del-gate-{code.lower()}", **overrides)
        payload = caught.exception.payload
        self.assertEqual("CONTENT_UNUSABLE", payload["reason_code"])
        self.assertIn(code, payload["codes"])
        self.assertTrue(payload["problems"])
        return payload

    def test_usable_content_is_accepted_and_records_gate(self):
        self._submit("del-gate-ok")
        reviewed = self.store.review(
            "del-gate-ok", results=[
                {"rubric_id": row["rubric_id"], "verdict": "pass",
                 "evidence_ref": f"gate:review:{row['rubric_id']}"}
                for row in _rubrics(get_template("T30"))], by="gate-reviewer")
        self.assertEqual("accepted", reviewed["state"])
        events = self.store.events.all()
        submitted = [e for e in events if e["event_type"] == "deliverable.submitted"][-1]
        self.assertEqual({"ok": True, "work_order_unresolved": False},
                         submitted["payload"]["content_gate"])

    def test_raft_placeholder_is_rejected(self):
        with self.assertRaises(DeliverableError) as caught:
            self._submit("del-gate-raft", reproduction_commands="raft:reproduction_commands")
        self.assertEqual("PLACEHOLDER_CONTENT", caught.exception.payload["reason_code"])
        self.assertIn("reproduction_commands", caught.exception.payload["fields"])

    def test_template_residue_is_rejected(self):
        self._assert_unusable("TEMPLATE_RESIDUE", expected_result="此处填写真实预期结果")

    def test_thin_text_is_rejected(self):
        self._assert_unusable("TOO_THIN", scope="短")

    def test_vague_advice_is_rejected(self):
        self._assert_unusable("VAGUE_ADVICE", outcome_status="持续优化，提升效果")

    def test_wrong_receiver_is_rejected(self):
        self._assert_unusable("WRONG_AUDIENCE", receiver="person-sales-ops（销售运营）")

    def test_wrong_product_facts_are_rejected(self):
        self._assert_unusable(
            "WRONG_FACTS", business_context_ref="ctx-other-product v2026-01-01.1")

    def test_wrong_objective_is_rejected(self):
        self._assert_unusable("WRONG_TARGET", objective_id="obj-some-other-bet")

    def test_short_identifier_leaves_are_accepted(self):
        """source_id/locator/version 是标识符，不按散文长度判定。"""
        self._submit(
            "del-gate-ids",
            evidence_refs=[{"source_id": "src-1", "locator": "file://f.json",
                            "version": "1"}])
        item = self.store.get("del-gate-ids")
        self.assertEqual("submitted", item["state"])

    def test_empty_identifier_placeholder_is_rejected(self):
        self._assert_unusable(
            "TOO_THIN",
            evidence_refs=[{"source_id": "n/a", "locator": "file://f.json"}])


if __name__ == "__main__":
    unittest.main()
