from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import operations_log

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "mkt.py"


class ProofExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.out = Path(self.tmp.name) / "proof"
        self.log = operations_log.OperationsLog(self.data)

    def _operation(self, operation_id="op-1", *, channel="email", result="committed",
                   receipt=None, content="authorized message"):
        content_hash = operations_log.content_sha256(content)
        self.log.append({
            "operation_id": operation_id, "run_id": "run-1", "campaign": "run-1",
            "queue_item_id": "item-1", "channel": channel, "action": "message",
            "target": "private-recipient@example.com", "content_sha256": content_hash,
            "browser_context_ref": None,
            "account": {"platform_user_id": "synthetic-user", "handle": "private-handle",
                        "observed_at": "2026-11-22T10:00:00+00:00", "proof_ref": "synthetic:identity"},
            "operator": "tester", "approval_ref": "synthetic-approval",
            "policy_sha256": "b" * 64,
            "started_at": "2026-11-22T10:00:00+00:00",
            "finished_at": "2026-11-22T10:00:01+00:00", "result": result,
            "receipt": receipt or {"id": "receipt-1"}, "artifacts": [], "error": None,
        })
        return content_hash

    def _readback(self, operation_id="op-1", *, content="authorized message", account=None):
        self.log.append({
            "kind": "readback", "operation_id": operation_id + "::readback",
            "run_id": "run-1", "campaign": "run-1", "queue_item_id": "item-1",
            "channel": "x", "action": "message", "target": "private-recipient@example.com",
            "content_sha256": operations_log.content_sha256(content), "browser_context_ref": None,
            "account": account or {"platform_user_id": "synthetic-user", "handle": "private-handle",
                                   "observed_at": "2026-11-22T10:00:02+00:00",
                                   "proof_ref": "synthetic:identity"},
            "operator": "independent-reader", "approval_ref": None, "policy_sha256": None,
            "started_at": "2026-11-22T10:00:02+00:00",
            "finished_at": "2026-11-22T10:00:03+00:00", "result": "verified",
            "receipt": {"id": "status-1", "url": "https://x.com/private/status/1",
                        "readback_sha256": operations_log.content_sha256(content)},
            "artifacts": [], "error": None,
            "readback": {"status": "matched", "source": "cdp", "note": "independent observation"},
        })

    def _export(self, classification="synthetic"):
        return subprocess.run([
            sys.executable, str(CLI), "--workspace", str(self.data),
            "proof", "export", "run-1", "--out", str(self.out), "--by", "proof-reviewer",
            "--classification", classification,
        ], capture_output=True, text=True)

    def _checks(self):
        doc = json.loads((self.out / "checks.json").read_text(encoding="utf-8"))
        return {item["check_id"]: item for item in doc["checks"]}

    def test_committed_is_p0_not_business_success(self):
        self._operation()
        completed = self._export()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        checks = self._checks()
        self.assertEqual("PASS", checks["execution:op-1"]["status"])
        self.assertEqual("P0", checks["execution:op-1"]["level"])
        self.assertEqual("UNCHECKED", checks["final-state:op-1"]["status"])
        self.assertEqual("UNCHECKED", checks["external-business-events"]["status"])
        self.assertNotEqual("PASS", json.loads(completed.stdout)["proof_status"])

    def test_full_readback_supports_p2_without_copying_private_content(self):
        content_hash = self._operation(channel="x", receipt={
            "id": "status-1", "url": "https://x.com/private/status/1",
        })
        self._readback()
        completed = self._export()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        checks = self._checks()
        self.assertEqual("PASS", checks["final-state:op-1"]["status"])
        self.assertEqual("P2", checks["final-state:op-1"]["level"])
        exported = "\n".join(path.read_text(encoding="utf-8") for path in self.out.rglob("*") if path.is_file())
        self.assertNotIn("private-recipient@example.com", exported)
        self.assertNotIn("private-handle", exported)
        self.assertNotIn("authorized message", exported)
        self.assertIn(content_hash, exported)

    def test_executor_receipt_hash_alone_does_not_prove_independent_p2(self):
        self._operation(channel="x", receipt={
            "id": "status-1", "url": "https://x.com/private/status/1",
            "readback_sha256": operations_log.content_sha256("authorized message"),
        })
        completed = self._export()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual("UNCHECKED", self._checks()["final-state:op-1"]["status"])

    def test_public_redaction_omits_platform_ids_and_replay_is_unavailable(self):
        self._operation(receipt={"id": "receipt-private-1", "url": "https://example.invalid/private"})
        completed = self._export("public-redacted")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        exported = "\n".join(path.read_text(encoding="utf-8") for path in self.out.rglob("*") if path.is_file())
        self.assertNotIn("receipt-private-1", exported)
        self.assertNotIn("https://example.invalid/private", exported)
        proof = json.loads((self.out / "proof.json").read_text(encoding="utf-8"))
        replay = json.loads((self.out / "replay/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("UNAVAILABLE", proof["replay"]["status"])
        self.assertEqual("UNAVAILABLE", replay["status"])
        self.assertTrue(replay["missing"])

    def test_indeterminate_stays_distinct_from_failure_and_success(self):
        self._operation(result="indeterminate", receipt={"id": ""})
        completed = self._export()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        check = self._checks()["execution:op-1"]
        self.assertEqual("INDETERMINATE", check["status"])
        self.assertEqual("BLOCKED", json.loads(completed.stdout)["proof_status"])

    def test_verified_external_event_is_p4_without_causal_credit(self):
        self._operation()
        events = self.data / "outcomes" / "product_events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_text(json.dumps({
            "event_id": "event-1", "event_type": "activation", "subject_id": "private-subject",
            "campaign_id": "run-1", "occurred_at": "2026-11-22T11:00:00+00:00",
            "source_system": "product-db", "source_ref": "private-event-ref",
            "verification_state": "verified", "verified_by": "product-db",
            "evidence_hash": "c" * 64,
        }) + "\n", encoding="utf-8")
        completed = self._export()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        check = self._checks()["external:event-1"]
        self.assertEqual("PASS", check["status"])
        self.assertEqual("P4", check["level"])
        proof = json.loads((self.out / "proof.json").read_text(encoding="utf-8"))
        self.assertIn("causality", "\n".join(proof["limitations"]))
        self.assertNotIn("private-subject", json.dumps(proof))

    def test_trace_uses_shared_execution_vocabulary_without_losing_result(self):
        self._operation()
        completed = self._export()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        steps = [json.loads(line) for line in (self.out / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual("ok", steps[0]["status"])
        summary = json.loads((self.out / "artifacts/run-summary.json").read_text(encoding="utf-8"))
        self.assertEqual("committed", summary["operations"][0]["result"])

    def test_events_only_run_keeps_a_nonempty_trace(self):
        events = self.data / "outcomes" / "product_events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_text(json.dumps({
            "event_id": "event-only", "event_type": "activation", "subject_id": "private-subject",
            "campaign_id": "run-1", "occurred_at": "2026-11-22T11:00:00+00:00",
            "source_system": "product-db", "source_ref": "private-event-ref",
            "verification_state": "verified", "verified_by": "product-db", "evidence_hash": "c" * 64,
        }) + "\n", encoding="utf-8")
        completed = self._export()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        steps = [json.loads(line) for line in (self.out / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(1, len(steps))
        self.assertEqual("export_proof", steps[0]["action"])
        self.assertEqual("PASS", self._checks()["external:event-only"]["status"])

    def test_vendor_declaration_does_not_become_p4_pass(self):
        self._operation()
        events = self.data / "outcomes" / "product_events.jsonl"
        events.parent.mkdir(parents=True, exist_ok=True)
        events.write_text(json.dumps({
            "event_id": "vendor-claim", "event_type": "paid", "subject_id": "private-subject",
            "campaign_id": "run-1", "occurred_at": "2026-11-22T11:00:00+00:00",
            "source_system": "vendor", "source_ref": "vendor-claim-1",
            "verification_state": "unverified", "verified_by": "vendor", "evidence_hash": "d" * 64,
        }) + "\n", encoding="utf-8")
        completed = self._export()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual("INDETERMINATE", self._checks()["external:vendor-claim"]["status"])

    def test_existing_output_is_never_overwritten(self):
        self._operation()
        self.out.mkdir()
        (self.out / "keep").write_text("keep", encoding="utf-8")
        completed = self._export()
        self.assertEqual(2, completed.returncode)
        self.assertEqual("keep", (self.out / "keep").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
