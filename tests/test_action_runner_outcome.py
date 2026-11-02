#!/usr/bin/env python3
"""Action runner -> task-outcome recording: a send is not a completed task."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import action_runner, task_outcome  # noqa: E402


def ledger_record(result: str, **extra) -> dict:
    record = {
        "ts": "2026-11-02T11:00:00",
        "channel": "github",
        "action": "reply",
        "target": "acme/ledger#42",
        "result": result,
        "campaign": "support-week-1",
    }
    record.update(extra)
    return record


class LedgerOutcomeTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)

    def test_committed_is_sent_and_unknown(self) -> None:
        payload = action_runner.record_ledger_outcome(self.data, ledger_record("committed"),
                                                      log=lambda *_: None)
        self.assertEqual("sent", payload["action_result"])
        self.assertEqual("unknown", payload["task_outcome"])
        self.assertEqual("inconclusive", payload["business_outcome"])
        stored = task_outcome.read_records(self.data)
        self.assertEqual(1, len(stored))
        self.assertEqual("committed", stored[0]["metadata"]["ledger_result"])

    def test_indeterminate_requires_escalation_and_forbids_retry(self) -> None:
        payload = action_runner.record_ledger_outcome(self.data, ledger_record("indeterminate"),
                                                      log=lambda *_: None)
        self.assertEqual("unknown", payload["action_result"])
        self.assertEqual("unknown", payload["task_outcome"])
        self.assertIn("reconcile", payload["escalation_reason"])

    def test_failed_maps_to_unresolved(self) -> None:
        payload = action_runner.record_ledger_outcome(self.data, ledger_record("failed"),
                                                      log=lambda *_: None)
        self.assertEqual("failed", payload["action_result"])
        self.assertEqual("unresolved", payload["task_outcome"])

    def test_dry_run_is_not_recorded(self) -> None:
        self.assertIsNone(action_runner.record_ledger_outcome(self.data, ledger_record("dry-run"),
                                                              log=lambda *_: None))
        self.assertEqual([], task_outcome.read_records(self.data))

    def test_run_action_records_after_a_real_go(self) -> None:
        original = action_runner._dispatch_action

        def fake_dispatch(**kwargs):
            return ledger_record("committed")

        action_runner._dispatch_action = fake_dispatch
        self.addCleanup(setattr, action_runner, "_dispatch_action", original)
        record = action_runner.run_action(channel="github", action="reply", go=True,
                                          data_dir=str(self.data), log=lambda *_: None)
        self.assertEqual("committed", record["result"])
        stored = task_outcome.read_records(self.data)
        self.assertEqual(1, len(stored))
        self.assertEqual("sent", stored[0]["action_result"])
        self.assertEqual("unknown", stored[0]["task_outcome"])


if __name__ == "__main__":
    unittest.main()
