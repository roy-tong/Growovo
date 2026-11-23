#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path

from core.job_kernel import JobStateMachine, JobStore
from core.storage import StorageError


class JobKernelTests(unittest.TestCase):
    def test_modern_and_legacy_results_share_terminal_semantics(self):
        self.assertEqual("needs_reconciliation", JobStateMachine.transition(
            "wave", {"ok": True, "results": [{"result": "indeterminate"}]})[0])
        self.assertEqual("deferred", JobStateMachine.transition(
            "wave", {"ok": False, "reason": "wave_already_running"})[0])
        self.assertEqual("completed_with_errors", JobStateMachine.transition(
            "wave", {"ok": True, "results": [{"result": "failed"}]})[0])
        self.assertEqual("needs_review", JobStateMachine.legacy_results(["committed", "failed"]))
        self.assertEqual("ok", JobStateMachine.legacy_results(["committed"]))
        self.assertEqual("needs_review", JobStateMachine.interrupted("running"))

    def test_dependencies_accept_completed_with_errors_but_not_reconciliation(self):
        self.assertTrue(JobStateMachine.dependency_satisfied(["a"], {"a": "completed_with_errors"}))
        self.assertFalse(JobStateMachine.dependency_satisfied(["a"], {"a": "needs_reconciliation"}))

    def test_job_store_is_atomic_and_fail_loud(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jobs.json"
            store = JobStore(path)
            self.assertEqual({"jobs": {}}, store.load())
            store.save({"jobs": {"a": {"status": "succeeded"}}})
            self.assertEqual("succeeded", store.load()["jobs"]["a"]["status"])
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(StorageError):
                store.load()


if __name__ == "__main__":
    unittest.main()
