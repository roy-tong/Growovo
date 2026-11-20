"""Offline Growovo evidence export: preserve facts, never inflate them."""
import json
import tempfile
import unittest
from pathlib import Path

from core import operations_log
from core.verovo_handoff import HandoffError, build_handoff, write_handoff


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


if __name__ == "__main__":
    unittest.main()
