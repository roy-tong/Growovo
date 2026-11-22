#!/usr/bin/env python3
"""Read-only Growovo → Verovo evidence handoff.

The package preserves action/account/receipt facts and product-event claims. It
never converts a send into delivery acceptance or a business event into booked
revenue. Verovo must import it as evidence-only until a buyer maps and reviews
the relevant canonical sources.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from . import operations_log, outcome_layers
from .work import ContextStore

SCHEMA = "growovo.verovo-handoff/1"
PREMISES_SCHEMA = "growovo.handoff-premises/1"
PREMISES_BOUNDARY = (
    "Premises travel with the package so the receiver can see the source "
    "objects, version, scope, assumptions and limits. They do not upgrade "
    "evidence grade: the receiver must re-check them against its own "
    "canonical sources before any booking or acceptance.")


class HandoffError(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def operation_task_id(run_id: str, operation_id: str) -> str:
    return "growovo:%s/op/%s" % (_text(run_id), _text(operation_id))


def event_task_id(run_id: str, event_id: str) -> str:
    return "growovo:%s/ev/%s" % (_text(run_id), _text(event_id))


def _task_bindings(run_id: str, operations, events) -> list:
    bindings = []
    for row in operations:
        operation_id = _text(row.get("operation_id"))
        if not operation_id:
            continue
        bindings.append({
            "task_id": operation_task_id(run_id, operation_id),
            "kind": "operation",
            "operation_id": operation_id,
            "run_id": run_id,
            "result": _text(row.get("result")) or None,
        })
    for row in events:
        event_id = _text(row.get("event_id"))
        if not event_id:
            continue
        bindings.append({
            "task_id": event_task_id(run_id, event_id),
            "kind": "product_event",
            "event_id": event_id,
            "run_id": run_id,
        })
    return bindings


def _premises(run_id: str, operations, events, contexts) -> Dict[str, Any]:
    channels = sorted({_text(row.get("channel")) for row in operations
                       if _text(row.get("channel"))})
    evidence_refs = []
    for row in operations:
        ref = _text(row.get("operation_id"))
        if ref:
            evidence_refs.append({"kind": "operation", "id": ref,
                                  "content_sha256": _text(row.get("content_sha256")) or None})
    for row in events:
        ref = _text(row.get("event_id"))
        if ref:
            evidence_refs.append({"kind": "product_event", "id": ref,
                                  "content_sha256": _text(row.get("content_sha256")) or None})
    for row in contexts:
        ref = _text(row.get("context_id"))
        if ref:
            evidence_refs.append({"kind": "askovo_context", "id": ref,
                                  "version": row.get("version"),
                                  "content_sha256": row.get("content_sha256")})
    assumptions = [
        "product events are source-declared by the product owner; Growovo does not verify them independently",
        "a committed operation records what the channel returned, not delivery acceptance or conversion",
        "task_id is a stable identifier inside this handoff package, not a buyer business task number",
    ]
    if contexts:
        assumptions.append(
            "askovo context binding is a workspace snapshot, not proof that this run used that version")
    return {
        "schema": PREMISES_SCHEMA,
        "source_objects": {
            "run_id": run_id,
            "campaign": run_id,
            "operations": len(operations),
            "business_events": len(events),
            "contexts": [{"context_id": row.get("context_id"),
                          "version": row.get("version"),
                          "content_sha256": row.get("content_sha256")}
                         for row in contexts],
        },
        "version": {"handoff_schema": SCHEMA, "premises_schema": PREMISES_SCHEMA},
        "scope": {
            "run_or_campaign": run_id,
            "channels": channels,
            "covers": ("action and receipt facts recorded in this workspace for "
                       "the run, plus source-declared product events"),
            "does_not_cover": ("delivery acceptance, booked revenue, payment "
                               "authorization, supplier performance, externally "
                               "observed growth"),
        },
        "assumptions": assumptions,
        "limits": [
            "evidence grade is preserved from the source and not re-derived here",
            "no charge, booking or revenue fact is implied by this package",
        ],
        "manual_status": {"state": "not_reviewed",
                          "note": "no human review is recorded in this export"},
        "evidence_refs": evidence_refs,
        "boundary": PREMISES_BOUNDARY,
    }


def build_handoff(data_dir, run_id: str) -> Dict[str, Any]:
    data_dir = Path(data_dir)
    run_id = _text(run_id)
    if not run_id:
        raise HandoffError("run_id 不能为空")
    records = [copy.deepcopy(row) for row in operations_log.OperationsLog(data_dir).all()
               if run_id in (_text(row.get("run_id")), _text(row.get("campaign")))]
    operations = [row for row in records if row.get("kind", "operation") == "operation"]
    readbacks = [row for row in records if row.get("kind") == "readback"]
    events = [copy.deepcopy(row) for row in outcome_layers.read_product_events(data_dir)
              if _text(row.get("campaign_id")) == run_id]
    if not operations and not events:
        raise HandoffError("该 run/campaign 没有操作日志或产品事件，拒绝导出空交接包")
    contexts = []
    for row in ContextStore(data_dir).list():
        source = copy.deepcopy(row.get("source") or {})
        contexts.append({
            "context_id": row.get("context_id"), "version": row.get("version"),
            "source": source, "content_sha256": row.get("content_sha256"),
            "binding": "workspace_snapshot_not_proof_of_run_binding",
        })
    body = {
        "schema": SCHEMA,
        "run_id": run_id,
        "operations": operations,
        "readbacks": readbacks,
        "business_events": events,
        "task_bindings": _task_bindings(run_id, operations, events),
        "event_qualifications": [copy.deepcopy(row) for row in
                                 outcome_layers.read_qualifications(data_dir)
                                 if any(row.get("event_id") == event.get("event_id")
                                        for event in events)],
        "outcome_projection": outcome_layers.layer_summary(data_dir, run_id=run_id),
        "askovo_contexts": contexts,
        "premises": _premises(run_id, operations, events, contexts),
        "evidence_grade": "preserves_source_grades_not_live_proof",
        "external_action_executed_by_export": False,
        "booking_status": "evidence_only_not_canonical",
        "boundary": ("Action committed is not delivery acceptance or business "
                     "conversion. Product-event verification is source-declared; "
                     "this handoff does not book charges, recognize revenue, accept "
                     "supplier delivery or authorize payment."),
    }
    body["integrity_sha256"] = _fingerprint(body)
    body["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return body


def write_handoff(data_dir, run_id: str, output) -> Dict[str, Any]:
    path = Path(output)
    if path.exists():
        raise HandoffError("交接包已存在，拒绝覆盖：%s" % path)
    payload = build_handoff(data_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    temp.replace(path)
    return {"path": str(path), "run_id": payload["run_id"],
            "operations": len(payload["operations"]),
            "business_events": len(payload["business_events"]),
            "task_bindings": len(payload.get("task_bindings") or []),
            "integrity_sha256": payload["integrity_sha256"],
            "booking_status": payload["booking_status"]}
