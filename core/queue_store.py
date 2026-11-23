"""Durable engagement queue snapshots plus local consume-event recovery."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import uuid
from typing import Any, Dict, List

from .storage import AppendOnlyJSONL, FileLock, StorageError, atomic_write_text


class QueueStore:
    def __init__(self, path):
        self.path = Path(path)
        self.journal_path = self.path.with_suffix(self.path.suffix + ".events.jsonl")
        self.journal = AppendOnlyJSONL(self.journal_path)

    def _snapshot(self) -> List[Dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError("engagement queue 不可读 %s：%s" % (self.path, exc))
        if isinstance(payload, dict):
            payload = payload.get("items") or []
        if not isinstance(payload, list):
            raise StorageError("engagement queue 必须是数组或 items 对象")
        rows = [dict(row) for row in payload if isinstance(row, dict)]
        from .engagement_cycle import stable_id
        for row in rows:
            row.setdefault("id", stable_id(row))
        return rows

    def load(self) -> List[Dict[str, Any]]:
        rows = self._snapshot()
        consumed = {str(event.get("queue_item_id") or "") for event in self.journal.all()
                    if event.get("kind") == "consume"}
        return [row for row in rows if str(row.get("id") or "") not in consumed]

    def save(self, items: List[Dict[str, Any]]):
        with FileLock(self.path.with_suffix(self.path.suffix + ".lock")):
            atomic_write_text(self.path, json.dumps(items, ensure_ascii=False, indent=1))
            atomic_write_text(self.journal_path, "")
        return self.path

    def consume(self, items: List[Dict[str, Any]], row: Dict[str, Any], *, action_id: str = ""):
        from .engagement_cycle import stable_id
        queue_item_id = str(row.get("id") or stable_id(row))
        remaining = [old for old in items if str(old.get("id") or stable_id(old)) != queue_item_id]
        self.journal.append({"schema_version": "growovo.queue-event/1", "kind": "consume",
                             "event_id": "event:" + uuid.uuid4().hex[:16],
                             "queue_item_id": queue_item_id, "action_id": action_id,
                             "at": dt.datetime.now().astimezone().isoformat(timespec="seconds")})
        return remaining
