#!/usr/bin/env python3
"""Receiver-accepted cross-role handoffs with SLA and explicit rework."""
from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .deliverables import DeliverableStore
from .event_store import EventStore
from .permissions import require_actor


class HandoffError(ValueError):
    pass


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class HandoffStore:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.events = EventStore(self.data_dir / "work" / "handoffs.jsonl", "handoff")

    def get(self, handoff_id: str) -> Dict[str, Any]:
        current = None
        for event in self.events.events(handoff_id):
            if event["event_type"] == "handoff.created":
                current = copy.deepcopy(event["payload"]["handoff"])
            elif event["event_type"] in ("handoff.accepted", "handoff.rejected"):
                current["state"] = event["payload"]["state"]
                current["receiver_id"] = event["by"]
                current["decision_at"] = event["ts"]
                if event["payload"]["state"] == "rejected":
                    current["rejection"] = {
                        "reason": event["payload"]["reason"],
                        "return_step_id": event["payload"]["return_step_id"],
                    }
        if current is None:
            raise HandoffError(f"找不到 Handoff: {handoff_id}")
        return current

    def list(self) -> List[Dict[str, Any]]:
        ids = []
        for event in self.events.all():
            if event["entity_id"] not in ids:
                ids.append(event["entity_id"])
        return [self.get(handoff_id) for handoff_id in ids]

    def create(self, spec: Dict[str, Any], *, by: str) -> Dict[str, Any]:
        required = ("handoff_id", "work_order_id", "from_role", "to_role", "receiver_id", "sla_due")
        missing = [key for key in required if not str(spec.get(key) or "").strip()]
        if missing:
            raise HandoffError("Handoff 缺必填字段: " + ",".join(missing))
        if any(h["handoff_id"] == spec["handoff_id"] for h in self.list()):
            raise HandoffError(f"Handoff 已存在: {spec['handoff_id']}")
        artifacts = spec.get("artifact_refs")
        if not isinstance(artifacts, list) or not artifacts:
            raise HandoffError("Handoff 至少一个 artifact_refs")
        store = DeliverableStore(self.data_dir)
        for ref in artifacts:
            item = store.get(ref.get("id", ""), ref.get("version"))
            if item["state"] != "accepted":
                raise HandoffError(f"交付物 {ref.get('id')}@{ref.get('version')} 未 accepted")
        if not spec.get("acceptance_rules"):
            raise HandoffError("Handoff 必须有接收方 acceptance_rules")
        handoff = copy.deepcopy(spec)
        handoff["state"] = "pending"
        handoff["rejection"] = None
        self.events.append("created", handoff["handoff_id"], {"handoff": handoff}, by=by)
        return self.get(handoff["handoff_id"])

    def accept(self, handoff_id: str, *, by: str, actor_type: str = "human") -> Dict[str, Any]:
        handoff = self.get(handoff_id)
        if handoff["state"] != "pending":
            raise HandoffError(f"只有 pending 可接收，当前 {handoff['state']}")
        try:
            require_actor(actor_id=by, actor_type=actor_type, operation="handoff.accept",
                          receiver_id=handoff["receiver_id"])
        except ValueError as exc:
            raise HandoffError(str(exc)) from exc
        self.events.append("accepted", handoff_id, {"state": "accepted"}, by=by)
        return self.get(handoff_id)

    def reject(self, handoff_id: str, *, by: str, reason: str, return_step_id: str,
               actor_type: str = "human") -> Dict[str, Any]:
        handoff = self.get(handoff_id)
        if handoff["state"] != "pending":
            raise HandoffError(f"只有 pending 可拒收，当前 {handoff['state']}")
        try:
            require_actor(actor_id=by, actor_type=actor_type, operation="handoff.reject",
                          receiver_id=handoff["receiver_id"])
        except ValueError as exc:
            raise HandoffError(str(exc)) from exc
        if not reason.strip() or not return_step_id.strip():
            raise HandoffError("拒收必须写 reason 与 return_step_id")
        self.events.append("rejected", handoff_id, {
            "state": "rejected", "reason": reason, "return_step_id": return_step_id,
        }, by=by)
        return self.get(handoff_id)

    def pending(self, *, now: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
        current_time = _time(now) if now else datetime.now().astimezone()
        pending: List[Dict[str, Any]] = []
        expired: List[Dict[str, Any]] = []
        for handoff in self.list():
            if handoff["state"] != "pending":
                continue
            try:
                is_expired = _time(handoff["sla_due"]) < current_time
            except ValueError:
                is_expired = True
            (expired if is_expired else pending).append(handoff)
        return {"pending": pending, "expired": expired}
