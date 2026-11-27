"""Validate replacement actions without widening the failed action's scope."""
from __future__ import annotations


REPLACEABLE_RESULTS = {"failed", "skipped", "unavailable_or_unrenderable", "rejected"}


def _target(item):
    target = item.get("target")
    if isinstance(target, dict):
        return str(target.get("url") or target.get("handle") or "").strip().casefold()
    return str(target or "").strip().casefold()


def validate(recoveries, originals, results):
    original_by_id = {str(row.get("id") or ""): row for row in originals}
    result_by_id = {str(row.get("queue_item_id") or row.get("id") or ""): row for row in results}
    recovery_ids = {str(row.get("id") or "") for row in recoveries}
    touched = {_target(row) for row in originals} | {
        _target(row) for row in results
        if str(row.get("queue_item_id") or row.get("id") or "") not in recovery_ids}
    touched.discard("")
    replaced = set()
    errors = []

    def add(code):
        if code not in errors:
            errors.append(code)

    for recovery in recoveries:
        original_id = str(recovery.get("replaces") or "")
        if not original_id:
            add("recovery_original_required")
            continue
        original = original_by_id.get(original_id)
        if not original:
            add("recovery_original_missing")
            continue
        if original_id in replaced:
            add("duplicate_recovery_original")
        replaced.add(original_id)
        result = (result_by_id.get(original_id) or {}).get("result")
        if result not in REPLACEABLE_RESULTS:
            add("recovery_original_not_replaceable")
        if any(recovery.get(key) != original.get(key) for key in ("product", "platform", "action")):
            add("recovery_scope_mismatch")
        target = _target(recovery)
        if not target:
            add("recovery_target_required")
        elif target in touched:
            add("recovery_target_already_touched")
        touched.add(target)
    return errors
