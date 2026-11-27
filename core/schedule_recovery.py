"""Explain how to recover a scheduled job without cloning it by default."""
from __future__ import annotations


def _result(action, reason, *, new_job_allowed=False):
    return {"action": action, "reason": reason, "new_job_allowed": new_job_allowed}


def decide(job, stored, *, approval_valid=False, original_scope_valid=False):
    status = str(stored.get("status") or "pending")
    reason = str(stored.get("reason") or "")
    if status in {"pending", "in_progress", "running"}:
        return _result("wait", "job_not_terminal")
    if reason == "job_expired" or status == "expired":
        if original_scope_valid and job.get("retry_of"):
            return _result("new_recovery_job", reason or "job_expired", new_job_allowed=True)
        return _result("manual_review", "original_scope_or_window_not_valid")
    if reason == "source_version_changed":
        return _result("diagnose_build", reason)
    if reason == "wave_approval_required":
        return _result("retry_same_job" if approval_valid else "approve_same_job", reason)
    if reason == "scheduled_job_changed":
        return _result("reload_job", reason)
    if status == "needs_reconciliation" or reason in {"dispatch_state_unknown", "indeterminate"}:
        return _result("reconcile", reason or status)
    if status == "blocked" and reason == "supply_not_ready":
        return _result("prepare_same_job", reason)
    if status in {"succeeded", "completed_with_errors"}:
        return _result("inspect_results", status)
    if status in {"blocked", "deferred"}:
        return _result("retry_same_job", reason or status)
    return _result("manual_review", reason or status)
