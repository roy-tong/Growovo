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


class HandoffError(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_handoff(data_dir, run_id: str) -> Dict[str, Any]:
    data_dir = Path(data_dir)
    run_id = _text(run_id)
    if not run_id:
        raise HandoffError("run_id 不能为空")
    operations = [copy.deepcopy(row) for row in operations_log.OperationsLog(data_dir).all()
                  if run_id in (_text(row.get("run_id")), _text(row.get("campaign")))]
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
        "business_events": events,
        "event_qualifications": [copy.deepcopy(row) for row in
                                 outcome_layers.read_qualifications(data_dir)
                                 if any(row.get("event_id") == event.get("event_id")
                                        for event in events)],
        "outcome_projection": outcome_layers.layer_summary(data_dir, run_id=run_id),
        "askovo_contexts": contexts,
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
            "integrity_sha256": payload["integrity_sha256"],
            "booking_status": payload["booking_status"]}
