#!/usr/bin/env python3
"""Canonical task-outcome and judgment-quality record.

Every record keeps three layers separate, and no layer implies the next:

1. ``action_result`` — what the platform did with the operation:
   ``sent`` | ``acked`` | ``failed`` | ``unknown``.  "sent" is not completion:
   a sent or acknowledged action can still leave the task unresolved.
2. ``task_outcome`` — what happened to the task itself:
   ``resolved`` | ``partial`` | ``unresolved`` | ``unknown`` | ``needs_human``.
   Completion is not business conversion: a resolved support thread, merged
   pull request, or answered question says nothing about activation, retention,
   or paid.
3. ``business_outcome`` — what was actually observed in the business, with its
   own evidence: ``observed`` | ``none`` | ``inconclusive``.

An ``unknown`` action result means the platform may or may not have applied the
action.  It requires an ``escalation_reason``, forbids claiming a resolved or
partial task, and never authorizes a retry: reconcile with platform evidence
first.

The record also carries the judgment around the action: ``action_taken``,
``decision_basis``, ``alternatives_considered``, ``risk_class`` (``low`` |
``medium`` | ``high`` | ``irreversible``), the verification method plus
``evidence_refs``, ``outcome_evidence_refs`` for an observed business outcome,
and ``escalation_reason`` when a human must take over.

Validation rejects contradictions, for example:

* ``action_result=sent``/``acked`` plus ``task_outcome=resolved`` without a
  verification method and at least one evidence ref;
* ``action_result=unknown`` without an ``escalation_reason``, or with a
  resolved/partial task outcome;
* ``business_outcome=observed`` without a ref in ``outcome_evidence_refs``;
* ``task_outcome=needs_human`` without an ``escalation_reason``;
* ``action_result=failed`` plus ``task_outcome=resolved``.

Validation never upgrades one layer into the next; it only rejects records that
claim more than their evidence supports.

Storage follows the existing workspace convention.  Callers pass the data
directory resolved by :func:`core.workspace.resolve`; :func:`append_outcome`
appends one JSON object per line to ``<data_dir>/outcomes/records.jsonl`` — the
same ``outcomes/`` subdirectory used by ``core.attribution.OutcomeLedger`` for
``events.jsonl``.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

SCHEMA_VERSION = "1.0"
KIND = "growovo.task_outcome"

ACTION_RESULTS = ("sent", "acked", "failed", "unknown")
TASK_OUTCOMES = ("resolved", "partial", "unresolved", "unknown", "needs_human")
BUSINESS_OUTCOMES = ("observed", "none", "inconclusive")
RISK_CLASSES = ("low", "medium", "high", "irreversible")

REF_FIELDS = ("alternatives_considered", "evidence_refs", "outcome_evidence_refs")
TEXT_FIELDS = ("task_id", "channel", "action_taken", "action_result",
               "task_outcome", "business_outcome", "decision_basis",
               "risk_class", "verification_method", "escalation_reason")
REQUIRED_TEXT_FIELDS = ("task_id", "action_taken", "decision_basis")


class TaskOutcomeError(ValueError):
    """The record is malformed or claims a combination its evidence cannot support."""


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _coerce_refs(value: Any) -> Optional[List[str]]:
    """Return a clean ref list, or ``None`` when the value is not ref-shaped."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return None


def _refs(value: Any, field: str) -> List[str]:
    refs = _coerce_refs(value)
    if refs is None:
        raise TaskOutcomeError(f"{field} must be a string or a list of strings")
    return refs


def _problems(record: Mapping[str, Any]) -> List[str]:
    if not isinstance(record, dict):
        return ["record must be a JSON object"]
    problems: List[str] = []

    for field in REQUIRED_TEXT_FIELDS:
        if not _text(record.get(field)):
            problems.append(f"{field} is required")

    action_result = _text(record.get("action_result"))
    task_outcome = _text(record.get("task_outcome"))
    business_outcome = _text(record.get("business_outcome"))
    risk_class = _text(record.get("risk_class"))

    if action_result not in ACTION_RESULTS:
        problems.append(f"action_result must be one of {ACTION_RESULTS}")
    if task_outcome not in TASK_OUTCOMES:
        problems.append(f"task_outcome must be one of {TASK_OUTCOMES}")
    if business_outcome not in BUSINESS_OUTCOMES:
        problems.append(f"business_outcome must be one of {BUSINESS_OUTCOMES}")
    if risk_class not in RISK_CLASSES:
        problems.append(f"risk_class must be one of {RISK_CLASSES}")

    for field in REF_FIELDS:
        value = record.get(field)
        if not isinstance(value, list):
            problems.append(f"{field} must be a list of strings")
        elif any(not isinstance(item, str) or not item.strip() for item in value):
            problems.append(f"{field} entries must be non-empty strings")

    for field in ("verification_method", "escalation_reason", "ts"):
        if field in record and record.get(field) is not None \
                and not isinstance(record.get(field), str):
            problems.append(f"{field} must be a string")

    reason = _text(record.get("escalation_reason"))
    method = _text(record.get("verification_method"))
    evidence_refs = record.get("evidence_refs")
    refs_ok = bool(evidence_refs) if isinstance(evidence_refs, list) else False
    outcome_refs = record.get("outcome_evidence_refs")
    outcome_refs_ok = bool(outcome_refs) if isinstance(outcome_refs, list) else False

    # Unknown platform result: reconcile with a human, never retry silently.
    if action_result == "unknown":
        if not reason:
            problems.append(
                "action_result=unknown requires escalation_reason "
                "(reconcile with platform evidence; do not retry)")
        if task_outcome in ("resolved", "partial"):
            problems.append(
                f"action_result=unknown cannot support task_outcome={task_outcome}; "
                "an unknown result is not completion")

    if task_outcome == "needs_human" and not reason:
        problems.append("task_outcome=needs_human requires escalation_reason")

    if action_result == "failed" and task_outcome == "resolved":
        problems.append(
            "action_result=failed cannot be recorded as task_outcome=resolved; "
            "record the action that actually resolved the task separately")

    if task_outcome in ("resolved", "partial") and (not method or not refs_ok):
        problems.append(
            f"task_outcome={task_outcome} requires verification_method and at least "
            f"one evidence_ref; action_result={action_result or '?'} alone is not completion")

    # A business result has its own layer and its own evidence.
    if business_outcome == "observed" and not outcome_refs_ok:
        problems.append(
            "business_outcome=observed requires at least one outcome_evidence_ref; "
            "a completed task is not business conversion")

    return problems


def validate_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a canonical record; return it unchanged or raise TaskOutcomeError.

    The validator is intentionally conservative: it rejects claims that the
    evidence cannot support and it refuses to derive a higher layer from a
    lower one.  It does not repair records; callers decide what the record
    should say.
    """
    problems = _problems(record)
    if problems:
        raise TaskOutcomeError("; ".join(problems))
    return record


def build_record(
    *,
    task_id: str,
    action_taken: str,
    action_result: str,
    task_outcome: str,
    business_outcome: str = "inconclusive",
    decision_basis: str,
    alternatives_considered: Sequence[str] | str = (),
    risk_class: str = "low",
    verification_method: str = "",
    evidence_refs: Sequence[str] | str = (),
    outcome_evidence_refs: Sequence[str] | str = (),
    escalation_reason: str = "",
    channel: str = "",
    ts: Optional[str] = None,
    verification: Optional[Mapping[str, Any]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build and validate one canonical task-outcome record.

    ``verification`` is an optional nested ``{"method": ..., "refs": [...]}``
    object accepted for callers that already group verification data; it is
    normalised into the flat ``verification_method`` / ``evidence_refs`` fields.
    Do not pass both forms at once.

    The returned record is a plain dict so it can be written to JSONL directly.
    Building never infers one layer from another: an ``acked`` action keeps
    ``task_outcome`` at whatever the caller says, and a ``resolved`` task keeps
    ``business_outcome`` at ``inconclusive`` unless the caller supplies its own
    outcome evidence.
    """
    if verification is not None:
        if _text(verification_method) or _coerce_refs(evidence_refs):
            raise TaskOutcomeError(
                "pass either verification=... or verification_method/evidence_refs, not both")
        if not isinstance(verification, dict):
            raise TaskOutcomeError("verification must be an object")
        method = _text(verification.get("method"))
        refs = _refs(verification.get("refs", verification.get("ref")), "verification.refs")
    else:
        method = _text(verification_method)
        refs = _refs(evidence_refs, "evidence_refs")

    if metadata is not None and not isinstance(metadata, dict):
        raise TaskOutcomeError("metadata must be an object")

    record: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "ts": _text(ts) or datetime.now().isoformat(timespec="seconds"),
        "task_id": _text(task_id),
        "channel": _text(channel),
        "action_taken": _text(action_taken),
        "action_result": _text(action_result),
        "task_outcome": _text(task_outcome),
        "business_outcome": _text(business_outcome),
        "decision_basis": _text(decision_basis),
        "alternatives_considered": _refs(alternatives_considered, "alternatives_considered"),
        "risk_class": _text(risk_class),
        "verification_method": method,
        "evidence_refs": refs,
        "outcome_evidence_refs": _refs(outcome_evidence_refs, "outcome_evidence_refs"),
        "escalation_reason": _text(escalation_reason),
    }
    if metadata is not None:
        record["metadata"] = dict(metadata)
    return validate_record(record)


def records_path(data_dir: Path | str) -> Path:
    """``<data_dir>/outcomes/records.jsonl`` — the existing outcomes subdirectory."""
    return Path(data_dir) / "outcomes" / "records.jsonl"


def append_outcome(data_dir: Path | str, record: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate and append one record to ``<data_dir>/outcomes/records.jsonl``.

    ``data_dir`` is the workspace data directory (the value returned by
    :func:`core.workspace.resolve`), not the repository root.  The function
    creates ``outcomes/`` when missing, normalises ref strings to lists, and
    validates before writing, so a malformed record never reaches the file.
    Appending is append-only; re-appending the same record writes a second line
    rather than mutating history.
    """
    if not isinstance(record, dict):
        raise TaskOutcomeError("record must be a JSON object")
    rec: Dict[str, Any] = dict(record)
    rec.setdefault("schema_version", SCHEMA_VERSION)
    rec.setdefault("kind", KIND)
    if not _text(rec.get("ts")):
        rec["ts"] = datetime.now().isoformat(timespec="seconds")

    for field in TEXT_FIELDS:
        if field in rec and rec.get(field) is not None:
            rec[field] = _text(rec[field])
    for field in REF_FIELDS:
        if field in rec:
            rec[field] = _refs(rec.get(field), field)
        else:
            rec[field] = []

    nested = rec.get("verification")
    if isinstance(nested, dict):
        if not rec.get("verification_method"):
            rec["verification_method"] = _text(nested.get("method"))
        if not rec.get("evidence_refs"):
            nested_refs = _coerce_refs(nested.get("refs", nested.get("ref"))) or []
            rec["evidence_refs"] = nested_refs
        rec.pop("verification", None)

    validate_record(rec)
    path = records_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read_records(data_dir: Path | str) -> List[Dict[str, Any]]:
    """Read back ``records.jsonl`` in append order; a corrupt line raises."""
    path = records_path(data_dir)
    if not path.is_file():
        return []
    records: List[Dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise TaskOutcomeError(f"{path}:{lineno}: invalid JSON ({exc.msg})") from exc
    return records
