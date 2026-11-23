"""Durable ownership state for inbound replies; no network or platform writes."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Iterable, Optional

TERMINAL_STATES = {
    "committed", "indeterminate", "failed", "target_rejected",
    "unavailable_or_unrenderable", "dismissed",
}
OPEN_STATES = {"needs_draft", "drafted", "planned", "needs_reconciliation"}


def _now(value=None) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    if value:
        got = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return got if got.tzinfo else got.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return dt.datetime.now().astimezone()


def _iso(value=None) -> str:
    return _now(value).isoformat(timespec="seconds")


def store_path(data_dir) -> Path:
    return Path(data_dir) / "state" / "inbox-followups.json"


def identity(row: Dict[str, Any]) -> str:
    existing = str(row.get("id") or "").strip()
    if existing:
        return existing
    raw = "%s|%s|%s|%s" % (
        row.get("channel") or row.get("platform") or "",
        row.get("action") or "reply",
        row.get("thread_ref") or "",
        row.get("target") or row.get("thread_ref") or "",
    )
    return "inbox:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _empty() -> Dict[str, Any]:
    return {"schema_version": "growovo.inbox-followups/1", "items": {}}


def load(data_dir) -> Dict[str, Any]:
    path = store_path(data_dir)
    if not path.is_file():
        return _empty()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("入站状态库不可读：%s" % exc)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), dict):
        raise ValueError("入站状态库格式错误：items 必须是对象")
    return payload


def save(data_dir, payload: Dict[str, Any]) -> Path:
    path = store_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


def _transition(item: Dict[str, Any], status: str, *, at: str, reason: str = "",
                wave: str = "", by: str = "", ref: str = "") -> None:
    previous = str(item.get("status") or "")
    item["status"] = status
    item["updated_at"] = at
    if reason:
        item["reason"] = reason
    if wave:
        item["wave"] = wave
    if by:
        item["updated_by"] = by
    if ref:
        item["receipt_ref"] = ref
    event = {"at": at, "status": status}
    if reason:
        event["reason"] = reason
    if wave:
        event["wave"] = wave
    if by:
        event["by"] = by
    if ref:
        event["ref"] = ref
    if previous != status or reason or ref:
        item.setdefault("history", []).append(event)


def observe(data_dir, rows: Iterable[Dict[str, Any]], *, now=None) -> Dict[str, Any]:
    at = _iso(now)
    payload = load(data_dir)
    observed = 0
    for row in rows:
        if str(row.get("source") or "") != "inbox":
            continue
        observed += 1
        key = identity(row)
        item = payload["items"].get(key)
        if item is None:
            item = {
                "key": key, "queue_item_id": str(row.get("id") or key),
                "channel": str(row.get("channel") or row.get("platform") or ""),
                "action": str(row.get("action") or "reply"),
                "target": str(row.get("target") or row.get("thread_ref") or ""),
                "thread_ref": str(row.get("thread_ref") or ""),
                "actor": str(row.get("actor") or row.get("author") or ""),
                "their_text": str(row.get("their_text") or "")[:4000],
                "received_at": str(row.get("received_at") or at),
                "first_seen_at": at, "history": [],
            }
            payload["items"][key] = item
        item["last_seen_at"] = at
        if row.get("their_text") and not item.get("their_text"):
            item["their_text"] = str(row.get("their_text") or "")[:4000]
        if str(item.get("status") or "") in TERMINAL_STATES:
            continue
        status = "drafted" if str(row.get("text") or "").strip() else "needs_draft"
        _transition(item, status, at=at)
    if observed:
        save(data_dir, payload)
    return {"observed": observed}


def terminal_identities(data_dir) -> set:
    return {key for key, item in load(data_dir)["items"].items()
            if str(item.get("status") or "") in TERMINAL_STATES}


def is_terminal(data_dir, row: Dict[str, Any]) -> bool:
    return identity(row) in terminal_identities(data_dir)


def mark_planned(data_dir, rows: Iterable[Dict[str, Any]], *, wave: str, now=None) -> None:
    at = _iso(now)
    payload = load(data_dir)
    changed = False
    for row in rows:
        if str(row.get("source") or "") != "inbox":
            continue
        item = payload["items"].get(identity(row))
        if item is None or item.get("status") in TERMINAL_STATES:
            continue
        _transition(item, "planned", at=at, wave=wave)
        changed = True
    if changed:
        save(data_dir, payload)


def mark_result(data_dir, row: Dict[str, Any], result: str, *, ref: str = "",
                wave: str = "", now=None, reason: str = "") -> None:
    state = "committed" if result == "success" else str(result or "unknown")
    payload = load(data_dir)
    item = payload["items"].get(identity(row))
    if item is None:
        observe(data_dir, [row], now=now)
        payload = load(data_dir)
        item = payload["items"].get(identity(row))
    _transition(item, state, at=_iso(now), wave=wave, ref=ref, reason=reason)
    save(data_dir, payload)


def mark_updated(data_dir, row: Dict[str, Any], status: str, *, by: str,
                 reason: str = "", now=None) -> None:
    payload = load(data_dir)
    item = payload["items"].get(identity(row))
    if item is None:
        observe(data_dir, [row], now=now)
        payload = load(data_dir)
        item = payload["items"].get(identity(row))
    _transition(item, status, at=_iso(now), by=by, reason=reason)
    save(data_dir, payload)


def _parse_time(value: Any, now: dt.datetime) -> Optional[dt.datetime]:
    try:
        got = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return got if got.tzinfo else got.replace(tzinfo=now.tzinfo)


def status_report(data_dir, queue_path=None, *, now=None, stale_minutes: int = 60) -> Dict[str, Any]:
    current = _now(now)
    queue_file = Path(queue_path) if queue_path else Path(data_dir) / "state" / "engagement-queue.json"
    from .engagement_cycle import load_queue
    queue = [row for row in load_queue(queue_file) if str(row.get("source") or "") == "inbox"]
    queued = {identity(row): row for row in queue}
    stored = load(data_dir)["items"]
    items = []
    for key in sorted(set(stored) | set(queued)):
        raw = dict(stored.get(key) or {})
        row = queued.get(key)
        if not raw and row is not None:
            raw = {
                "key": key, "queue_item_id": str(row.get("id") or key),
                "channel": str(row.get("channel") or ""), "action": str(row.get("action") or "reply"),
                "target": str(row.get("target") or ""), "actor": str(row.get("actor") or ""),
                "their_text": str(row.get("their_text") or "")[:4000],
                "received_at": str(row.get("received_at") or ""),
                "first_seen_at": str(row.get("received_at") or ""), "history": [],
                "tracked": False,
            }
        recorded = str(raw.get("status") or ("drafted" if row and row.get("text") else "needs_draft"))
        effective = recorded
        reconciliation = ""
        if recorded not in TERMINAL_STATES and row is None:
            effective = "needs_reconciliation"
            reconciliation = "queue_missing"
        elif recorded in TERMINAL_STATES and row is not None:
            effective = "needs_reconciliation"
            reconciliation = "terminal_still_queued"
        elif row is not None and recorded not in TERMINAL_STATES:
            if recorded == "planned" and str(row.get("text") or "").strip():
                effective = "planned"
            else:
                effective = "drafted" if str(row.get("text") or "").strip() else "needs_draft"
        started = _parse_time(raw.get("received_at") or raw.get("first_seen_at"), current)
        age = max(0, int((current - started).total_seconds() // 60)) if started else None
        overdue = effective in {"needs_draft", "drafted", "needs_reconciliation"} \
            and age is not None and age >= max(1, int(stale_minutes))
        items.append({
            "key": key, "queue_item_id": raw.get("queue_item_id") or key,
            "channel": raw.get("channel") or "", "action": raw.get("action") or "reply",
            "target": raw.get("target") or "", "actor": raw.get("actor") or "",
            "their_text": str(raw.get("their_text") or "")[:400],
            "received_at": raw.get("received_at") or "",
            "status": effective, "recorded_status": recorded,
            "reason": raw.get("reason") or "", "wave": raw.get("wave") or "",
            "receipt_ref": raw.get("receipt_ref") or "",
            "updated_by": raw.get("updated_by") or "",
            "updated_at": raw.get("updated_at") or "",
            "reconciliation_reason": reconciliation,
            "age_minutes": age, "overdue": overdue,
            "tracked": raw.get("tracked", True),
        })
    counts: Dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    pending = sum(counts.get(state, 0) for state in OPEN_STATES)
    return {
        "schema_version": "growovo.inbox-status/1",
        "ok": not counts.get("needs_reconciliation"),
        "counts": dict(sorted(counts.items())),
        "pending_count": pending,
        "overdue_count": sum(1 for item in items if item["overdue"]),
        "stale_minutes": max(1, int(stale_minutes)),
        "items": items,
        "store_path": str(store_path(data_dir)),
        "queue_path": str(queue_file),
        "note": "committed 是平台动作回执；dismissed 必须有署名和原因；历史未追踪消息不会伪造终态。",
    }
