#!/usr/bin/env python3
"""Task-outcome record: three independent layers, validation, JSONL append."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import task_outcome  # noqa: E402
from core import workspace  # noqa: E402


def record(**overrides):
    values = dict(
        task_id="gh-42",
        channel="github",
        action_taken="replied with the documented workaround",
        action_result="acked",
        task_outcome="resolved",
        business_outcome="none",
        decision_basis="reproduction confirmed and the workaround was tested",
        alternatives_considered=["ask for a smaller reproduction"],
        risk_class="low",
        verification_method="comment_url",
        evidence_refs=["https://github.com/acme/ledger-cli/issues/42#issuecomment-1"],
    )
    values.update(overrides)
    return task_outcome.build_record(**values)


class TestBuildAndValidate(unittest.TestCase):
    def test_docstring_states_the_layer_boundary(self):
        doc = task_outcome.__doc__ or ""
        self.assertIn('"sent" is not completion', doc)
        self.assertIn("completion is not business conversion", doc.lower())
        self.assertIn("outcomes/records.jsonl", doc)

    def test_valid_record_keeps_all_three_layers(self):
        rec = record()
        self.assertEqual("growovo.task_outcome", rec["kind"])
        self.assertEqual("acked", rec["action_result"])
        self.assertEqual("resolved", rec["task_outcome"])
        self.assertEqual("none", rec["business_outcome"])
        self.assertEqual(["ask for a smaller reproduction"], rec["alternatives_considered"])
        self.assertEqual(["https://github.com/acme/ledger-cli/issues/42#issuecomment-1"],
                         rec["evidence_refs"])
        self.assertTrue(rec["ts"])
        self.assertIs(rec, task_outcome.validate_record(rec))

    def test_sent_does_not_imply_completion(self):
        rec = record(action_result="sent", task_outcome="unknown",
                     business_outcome="inconclusive",
                     verification_method="", evidence_refs=[])
        self.assertEqual("sent", rec["action_result"])
        self.assertEqual("unknown", rec["task_outcome"])
        self.assertEqual("inconclusive", rec["business_outcome"])

    def test_action_success_plus_resolution_requires_evidence(self):
        for action_result in ("sent", "acked"):
            with self.subTest(action_result=action_result):
                with self.assertRaises(task_outcome.TaskOutcomeError) as ctx:
                    record(action_result=action_result, task_outcome="resolved",
                           verification_method="", evidence_refs=[])
                self.assertIn("verification_method", str(ctx.exception))
                self.assertIn("not completion", str(ctx.exception))

    def test_resolution_with_evidence_is_allowed(self):
        rec = record(action_result="sent", task_outcome="resolved")
        self.assertEqual("resolved", rec["task_outcome"])

    def test_partial_also_needs_verification(self):
        with self.assertRaises(task_outcome.TaskOutcomeError):
            record(task_outcome="partial", verification_method="", evidence_refs=[])

    def test_unknown_action_result_requires_escalation(self):
        with self.assertRaises(task_outcome.TaskOutcomeError) as ctx:
            record(action_result="unknown", task_outcome="unknown", escalation_reason="")
        self.assertIn("escalation_reason", str(ctx.exception))
        rec = record(action_result="unknown", task_outcome="unknown",
                     escalation_reason="gateway returned no receipt; reconcile before any retry")
        self.assertEqual("unknown", rec["task_outcome"])

    def test_unknown_action_result_cannot_claim_resolution(self):
        with self.assertRaises(task_outcome.TaskOutcomeError) as ctx:
            record(action_result="unknown", task_outcome="resolved",
                   escalation_reason="receipt missing; human must reconcile")
        self.assertIn("unknown", str(ctx.exception))

    def test_failed_action_cannot_be_recorded_as_resolved(self):
        with self.assertRaises(task_outcome.TaskOutcomeError) as ctx:
            record(action_result="failed", task_outcome="resolved")
        self.assertIn("failed", str(ctx.exception))

    def test_needs_human_requires_escalation_reason(self):
        with self.assertRaises(task_outcome.TaskOutcomeError):
            record(task_outcome="needs_human", escalation_reason="")
        rec = record(task_outcome="needs_human",
                     escalation_reason="requires account-owner approval")
        self.assertEqual("needs_human", rec["task_outcome"])

    def test_observed_business_outcome_needs_its_own_evidence(self):
        with self.assertRaises(task_outcome.TaskOutcomeError) as ctx:
            record(business_outcome="observed", outcome_evidence_refs=[])
        self.assertIn("outcome_evidence_ref", str(ctx.exception))
        rec = record(business_outcome="observed",
                     outcome_evidence_refs=["outcomes/events.jsonl#signup-42"])
        self.assertEqual("observed", rec["business_outcome"])

    def test_layers_are_independent_not_derived(self):
        # A resolved task with an inconclusive business layer is valid.
        rec = record(task_outcome="resolved", business_outcome="inconclusive")
        self.assertEqual("inconclusive", rec["business_outcome"])
        # An unresolved task can still have an observed business outcome, with
        # its own evidence.  The two layers do not force each other.
        rec = record(action_result="acked", task_outcome="unresolved",
                     business_outcome="observed",
                     outcome_evidence_refs=["crm:deal-17"],
                     verification_method="", evidence_refs=[])
        self.assertEqual("unresolved", rec["task_outcome"])
        self.assertEqual("observed", rec["business_outcome"])

    def test_invalid_enums_and_missing_fields_are_rejected(self):
        with self.assertRaises(task_outcome.TaskOutcomeError):
            record(action_result="delivered")
        with self.assertRaises(task_outcome.TaskOutcomeError):
            record(task_outcome="done")
        with self.assertRaises(task_outcome.TaskOutcomeError):
            record(business_outcome="converted")
        with self.assertRaises(task_outcome.TaskOutcomeError):
            record(risk_class="critical")
        with self.assertRaises(task_outcome.TaskOutcomeError):
            record(decision_basis="")
        with self.assertRaises(task_outcome.TaskOutcomeError):
            record(task_id="")

    def test_nested_verification_and_string_alternatives_are_normalised(self):
        rec = record(
            verification={"method": "screenshot", "refs": ["shots/approval.png"]},
            verification_method="", evidence_refs=[],
            alternatives_considered="do nothing",
        )
        self.assertEqual("screenshot", rec["verification_method"])
        self.assertEqual(["shots/approval.png"], rec["evidence_refs"])
        self.assertEqual(["do nothing"], rec["alternatives_considered"])
        with self.assertRaises(task_outcome.TaskOutcomeError):
            record(verification={"method": "x", "refs": ["y"]},
                   verification_method="m", evidence_refs=["r"])

    def test_validate_does_not_repair(self):
        rec = record()
        broken = dict(rec)
        broken["decision_basis"] = ""
        with self.assertRaises(task_outcome.TaskOutcomeError):
            task_outcome.validate_record(broken)
        self.assertEqual("", broken["decision_basis"])


class TestAppend(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name) / "data"
        self.data.mkdir()

    def test_append_uses_the_existing_outcomes_subdirectory(self):
        self.assertIn("outcomes", workspace.SUBDIRS)
        path = task_outcome.records_path(self.data)
        self.assertEqual(self.data / "outcomes" / "records.jsonl", path)
        self.assertFalse(path.exists())
        written = task_outcome.append_outcome(self.data, record())
        self.assertTrue(path.is_file())
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(1, len(lines))
        self.assertEqual(written, json.loads(lines[0]))

    def test_append_is_append_only_and_round_trips(self):
        first = record(task_id="gh-1")
        second = record(task_id="gh-2", action_result="sent", task_outcome="unknown",
                        business_outcome="inconclusive", verification_method="",
                        evidence_refs=[], escalation_reason="no receipt; reconcile")
        task_outcome.append_outcome(self.data, first)
        task_outcome.append_outcome(self.data, second)
        loaded = task_outcome.read_records(self.data)
        self.assertEqual(["gh-1", "gh-2"], [item["task_id"] for item in loaded])
        self.assertEqual(first["ts"], loaded[0]["ts"])

    def test_append_normalises_manual_records(self):
        rec = record()
        rec.pop("ts")
        rec["evidence_refs"] = rec["evidence_refs"][0]
        rec["outcome_evidence_refs"] = "crm:deal-17"
        written = task_outcome.append_outcome(self.data, rec)
        self.assertTrue(written["ts"])
        self.assertEqual(["https://github.com/acme/ledger-cli/issues/42#issuecomment-1"],
                         written["evidence_refs"])
        self.assertEqual(["crm:deal-17"], written["outcome_evidence_refs"])

    def test_invalid_record_never_reaches_the_file(self):
        path = task_outcome.records_path(self.data)
        with self.assertRaises(task_outcome.TaskOutcomeError):
            task_outcome.append_outcome(self.data, record(decision_basis=""))
        self.assertFalse(path.exists())

    def test_utf8_is_not_escaped(self):
        rec = record(evidence_refs=["证据/截图-1.png"])
        task_outcome.append_outcome(self.data, rec)
        raw = task_outcome.records_path(self.data).read_text(encoding="utf-8")
        self.assertIn("证据/截图-1.png", raw)
        self.assertNotIn("\\u8bc1", raw)

    def test_read_missing_file_is_empty(self):
        self.assertEqual([], task_outcome.read_records(self.data))


if __name__ == "__main__":
    unittest.main()
