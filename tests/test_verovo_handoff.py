"""Offline Growovo evidence export: preserve facts, never inflate them."""
import json
import tempfile
import unittest
from pathlib import Path

from core import operations_log
from core.verovo_handoff import HandoffError, _fingerprint, build_handoff, write_handoff


class VerovoHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def test_empty_run_is_refused(self):
        with self.assertRaises(HandoffError):
            build_handoff(self.data, "absent")

    def test_export_is_atomic_and_never_overwrites(self):
        log = operations_log.OperationsLog(self.data)
        log.append({
            "operation_id": "op-handoff-1", "run_id": "handoff-run",
            "campaign": "handoff-run", "queue_item_id": "item-1",
            "channel": "email", "action": "message", "target": "user@example.com",
            "content_sha256": "a" * 64, "browser_context_ref": None,
            "account": {"platform_user_id": "synthetic", "handle": "sender",
                        "observed_at": "2026-11-18T10:00:00+00:00",
                        "proof_ref": "synthetic:identity"},
            "operator": "test-role", "approval_ref": "synthetic-approval",
            "policy_sha256": "b" * 64,
            "started_at": "2026-11-18T10:00:00+00:00",
            "finished_at": "2026-11-18T10:00:01+00:00", "result": "committed",
            "receipt": {"id": "synthetic-message-1"}, "artifacts": [], "error": None,
        })
        out = self.data / "handoff.json"
        result = write_handoff(self.data, "handoff-run", out)
        self.assertEqual(1, result["operations"])
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual("evidence_only_not_canonical", payload["booking_status"])
        self.assertFalse(payload["external_action_executed_by_export"])
        with self.assertRaises(HandoffError):
            write_handoff(self.data, "handoff-run", out)

    def test_premises_travel_with_the_package(self):
        log = operations_log.OperationsLog(self.data)
        log.append({
            "operation_id": "op-premises-1", "run_id": "premises-run",
            "campaign": "premises-run", "queue_item_id": "item-1",
            "channel": "email", "action": "message", "target": "user@example.com",
            "content_sha256": "c" * 64, "browser_context_ref": None,
            "account": {"platform_user_id": "synthetic", "handle": "sender",
                        "observed_at": "2026-11-18T10:00:00+00:00",
                        "proof_ref": "synthetic:identity"},
            "operator": "test-role", "approval_ref": "synthetic-approval",
            "policy_sha256": "d" * 64,
            "started_at": "2026-11-18T10:00:00+00:00",
            "finished_at": "2026-11-18T10:00:01+00:00", "result": "committed",
            "receipt": {"id": "synthetic-message-2"}, "artifacts": [], "error": None,
        })
        payload = build_handoff(self.data, "premises-run")
        premises = payload["premises"]
        self.assertEqual("growovo.handoff-premises/1", premises["schema"])
        self.assertEqual("premises-run", premises["source_objects"]["run_id"])
        self.assertEqual(["email"], premises["scope"]["channels"])
        self.assertTrue(any(ref["kind"] == "operation" and ref["id"] == "op-premises-1"
                            for ref in premises["evidence_refs"]))
        self.assertEqual("not_reviewed", premises["manual_status"]["state"])
        # Premises are covered by the fingerprint: strip the two excluded keys
        # and recompute exactly what a receiver recomputes.
        body = dict(payload)
        body.pop("integrity_sha256")
        body.pop("generated_at")
        self.assertEqual(payload["integrity_sha256"], _fingerprint(body))

    def test_task_bindings_expose_stable_ids_for_operations_and_events(self):
        log = operations_log.OperationsLog(self.data)
        log.append({
            "operation_id": "op-bind-1", "run_id": "bind-run",
            "campaign": "bind-run", "queue_item_id": "item-1",
            "channel": "email", "action": "message", "target": "user@example.com",
            "content_sha256": "1" * 64, "browser_context_ref": None,
            "account": {"platform_user_id": "synthetic", "handle": "sender",
                        "observed_at": "2026-11-18T10:00:00+00:00",
                        "proof_ref": "synthetic:identity"},
            "operator": "test-role", "approval_ref": "synthetic-approval",
            "policy_sha256": "2" * 64,
            "started_at": "2026-11-18T10:00:00+00:00",
            "finished_at": "2026-11-18T10:00:01+00:00", "result": "committed",
            "receipt": {"id": "synthetic-message-9"}, "artifacts": [], "error": None,
        })
        events = self.data / "outcomes" / "product_events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_text(json.dumps({
            "event_id": "ev-bind-1", "event_type": "signup",
            "subject_id": "subject-1", "campaign_id": "bind-run",
            "occurred_at": "2026-11-18T11:00:00+00:00",
            "source_system": "product-owner", "source_ref": "synthetic:event-1",
        }, ensure_ascii=False) + "\n", encoding="utf-8")

        payload = build_handoff(self.data, "bind-run")
        bindings = {row["task_id"]: row for row in payload["task_bindings"]}
        self.assertEqual(2, len(bindings))
        operation = bindings["growovo:bind-run/op/op-bind-1"]
        self.assertEqual("operation", operation["kind"])
        self.assertEqual("op-bind-1", operation["operation_id"])
        self.assertEqual("committed", operation["result"])
        self.assertEqual("bind-run", operation["run_id"])
        event = bindings["growovo:bind-run/ev/ev-bind-1"]
        self.assertEqual("product_event", event["kind"])
        self.assertEqual("ev-bind-1", event["event_id"])
        self.assertTrue(any("task_id is a stable identifier" in line
                            for line in payload["premises"]["assumptions"]))

        # task_bindings travel inside the fingerprint: rewriting one breaks it.
        stored = payload["integrity_sha256"]
        payload["task_bindings"][0]["task_id"] = "growovo:bind-run/op/other"
        body = dict(payload)
        body.pop("integrity_sha256")
        body.pop("generated_at")
        self.assertNotEqual(stored, _fingerprint(body))

    def test_tampering_with_premises_breaks_integrity(self):
        log = operations_log.OperationsLog(self.data)
        log.append({
            "operation_id": "op-premises-2", "run_id": "premises-tamper",
            "campaign": "premises-tamper", "queue_item_id": "item-1",
            "channel": "email", "action": "message", "target": "user@example.com",
            "content_sha256": "e" * 64, "browser_context_ref": None,
            "account": {"platform_user_id": "synthetic", "handle": "sender",
                        "observed_at": "2026-11-18T10:00:00+00:00",
                        "proof_ref": "synthetic:identity"},
            "operator": "test-role", "approval_ref": "synthetic-approval",
            "policy_sha256": "f" * 64,
            "started_at": "2026-11-18T10:00:00+00:00",
            "finished_at": "2026-11-18T10:00:01+00:00", "result": "committed",
            "receipt": {"id": "synthetic-message-3"}, "artifacts": [], "error": None,
        })
        payload = build_handoff(self.data, "premises-tamper")
        stored = payload["integrity_sha256"]
        payload["premises"]["assumptions"].append("verified by the sender, trust it")
        body = dict(payload)
        body.pop("integrity_sha256")
        body.pop("generated_at")
        self.assertNotEqual(stored, _fingerprint(body))


if __name__ == "__main__":
    unittest.main()
