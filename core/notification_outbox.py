"""Persistent job-change events with explicit, receipt-bound delivery."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .ledger import _append_line, _read_jsonl_fail_loud
from .storage import FileLock


def _path(data_dir):
    return Path(data_dir) / "state" / "notification-outbox.jsonl"


def _rows(data_dir):
    return _read_jsonl_fail_loud(_path(data_dir), kind="notification outbox")


def record(data_dir, change):
    data_dir = Path(data_dir)
    change = dict(change)
    event_id = str(change.get("event_id") or "")
    if not event_id:
        event_id = hashlib.sha256(json.dumps(
            change, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not change.get("id") or not change.get("status"):
        raise ValueError("notification event requires id and status")
    with FileLock(_path(data_dir).with_suffix(".lock")):
        if any(row.get("event_id") == event_id and row.get("kind") == "event"
               for row in _rows(data_dir)):
            return {"created": False, "event_id": event_id}
        row = dict(change, kind="event", event_id=event_id,
                   recorded_at=datetime.now(timezone.utc).isoformat())
        _append_line(_path(data_dir), json.dumps(row, ensure_ascii=False) + "\n")
    return {"created": True, "event_id": event_id}


def pending(data_dir):
    delivered = {str(row.get("event_id") or "") for row in _rows(data_dir)
                 if row.get("kind") == "delivery" and row.get("status") == "delivered"}
    return [row for row in _rows(data_dir)
            if row.get("kind") == "event" and row.get("event_id") not in delivered]


def deliver_pending(data_dir, sender):
    data_dir = Path(data_dir)
    delivered = 0
    failed = 0
    with FileLock(_path(data_dir).with_suffix(".delivery.lock")):
        for event in pending(data_dir):
            row = {"kind": "delivery", "event_id": event["event_id"],
                   "at": datetime.now(timezone.utc).isoformat()}
            try:
                receipt = sender(dict(event))
                if not str(receipt or "").strip():
                    raise ValueError("delivery receipt is required")
            except Exception as exc:
                row.update(status="failed", error="%s: %s" % (type(exc).__name__, str(exc)[:200]))
                failed += 1
            else:
                row.update(status="delivered", receipt=str(receipt or ""))
                delivered += 1
            _append_line(_path(data_dir), json.dumps(row, ensure_ascii=False) + "\n")
    return {"delivered": delivered, "failed": failed,
            "pending": len(pending(data_dir)), "delivery_adapter": "injected"}
