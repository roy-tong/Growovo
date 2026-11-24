"""Shared job result semantics and atomic history storage for scheduler adapters."""
from __future__ import annotations

from .storage import AtomicJSONStore

TERMINAL = {"succeeded", "completed_with_errors", "failed", "needs_reconciliation", "expired"}
DEPENDENCY_OK = {"succeeded", "completed_with_errors"}


class JobStore(AtomicJSONStore):
    def __init__(self, path):
        super().__init__(path, default={"jobs": {}})


class JobStateMachine:
    TERMINAL = TERMINAL
    DEPENDENCY_OK = DEPENDENCY_OK

    @staticmethod
    def transition(kind: str, result):
        if not isinstance(result, dict):
            return "failed", "executor_result_not_object"
        results = result.get("results") if isinstance(result.get("results"), list) else []
        states = [str(row.get("ledger_result") or row.get("result") or "") for row in results
                  if isinstance(row, dict)]
        if "indeterminate" in states:
            return "needs_reconciliation", "indeterminate_requires_manual_resolution"
        if result.get("ok"):
            if result.get("rejected"):
                return "failed", "draft_update_rejected"
            if int(result.get("target") or 0) > 0 and not results:
                return "completed_with_errors", "target_shortfall"
            if any(state != "committed" for state in states):
                return "completed_with_errors", "non_committed_results"
            return "succeeded", str(result.get("reason") or "")
        if result.get("reason") == "wave_already_running":
            return "deferred", "wave_already_running"
        if kind == "receipts":
            return "needs_reconciliation", str(result.get("reason") or "receipt_audit_failed")
        return "failed", str(result.get("reason") or result.get("error") or "job_failed")

    @staticmethod
    def legacy_results(results):
        return "needs_review" if any(str(result) in {"indeterminate", "failed", "rejected"}
                                     for result in results) else "ok"

    @staticmethod
    def interrupted(status):
        return "needs_review" if status == "running" else status

    @staticmethod
    def dependency_satisfied(requires, statuses):
        return all(statuses.get(ref) in DEPENDENCY_OK for ref in requires or [])
