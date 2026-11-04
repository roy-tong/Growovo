#!/usr/bin/env python3
"""Small append-only JSONL event store used by the GTM work OS.

Events are facts; current entity states are projections.  The writer uses one
append under an advisory lock and never rewrites history.  Readers fail loudly
on malformed JSON instead of silently dropping business evidence.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from uuid import uuid4

SCHEMA_VERSION = "1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_jsonl(path: Path | str, record: Dict[str, Any]) -> Dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    # One write with O_APPEND keeps concurrent writers from seeking over each
    # other.  The advisory lock also prevents interleaving on supported hosts.
    fd = os.open(str(target), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        os.write(fd, line.encode("utf-8"))
    finally:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        os.close(fd)
    return record


def read_jsonl(path: Path | str) -> List[Dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{source}:{number} 不是合法 JSONL：{exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{source}:{number} 事件必须是 JSON object")
            rows.append(row)
    return rows


class EventStore:
    """Namespaced append-only events with a conventional envelope."""

    def __init__(self, path: Path | str, namespace: str):
        self.path = Path(path)
        self.namespace = str(namespace).strip()
        if not self.namespace:
            raise ValueError("event namespace 不能为空")

    def append(self, event_type: str, entity_id: str, payload: Dict[str, Any], *,
               by: str, ts: Optional[str] = None) -> Dict[str, Any]:
        if not str(by or "").strip():
            raise ValueError("业务事件必须署名 by")
        if not str(entity_id or "").strip():
            raise ValueError("业务事件必须关联 entity_id")
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_id": uuid4().hex,
            "event_type": f"{self.namespace}.{event_type}",
            "entity_id": str(entity_id),
            "ts": ts or utc_now(),
            "by": str(by),
            "payload": dict(payload or {}),
        }
        return append_jsonl(self.path, event)

    def events(self, entity_id: Optional[str] = None) -> Iterator[Dict[str, Any]]:
        prefix = f"{self.namespace}."
        for row in read_jsonl(self.path):
            if not str(row.get("event_type", "")).startswith(prefix):
                continue
            if entity_id is not None and row.get("entity_id") != entity_id:
                continue
            yield row

    def all(self) -> List[Dict[str, Any]]:
        return list(self.events())
