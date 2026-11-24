from __future__ import annotations

import copy
import datetime as dt
import re

from . import growth_runtime


OPEN = {"needs_draft", "drafted", "planned", "needs_reconciliation"}
TERMINAL = {"committed", "dismissed"}


def _now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _instant(value):
    stamp = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("received_at 必须包含时区")
    return stamp


def _path(data_dir, service_id):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-_]*", str(service_id or "")):
        raise ValueError("service_id 格式不正确")
    from pathlib import Path
    return Path(data_dir) / "state" / "growth-engagement" / f"{service_id}.json"


def _load(data_dir, service_id):
    return growth_runtime.load_engagement(data_dir, service_id)


def _save(data_dir, service_id, payload):
    snapshot = copy.deepcopy(payload)

    def replace(current):
        current.clear()
        current.update(snapshot)
        return current

    return growth_runtime.update_domain_state(
        data_dir, "growth_engagement", service_id, _path(data_dir, service_id), replace)


def _conversation_id(row):
    source = re.sub(r"[^a-z0-9]+", "-", row["source"].lower()).strip("-")
    external_value = str(row.get("conversation_key") or row["external_id"])
    external = re.sub(r"[^a-zA-Z0-9_-]+", "-", external_value).strip("-")
    return f"conv-{source}-{external}"


def _message(raw):
    message = {}
    for key in ("external_id", "author", "actor_id", "body", "url", "received_at",
                "source_event_id", "external_object_type"):
        if raw.get(key) is not None:
            message[key] = copy.deepcopy(raw[key])
    return message


def import_interactions(data_dir, service_id, rows, connection=None):
    if not isinstance(rows, list):
        raise ValueError("interactions 必须是数组")

    def mutate(payload):
        added = 0
        for raw in rows:
            for key in ("source", "external_id", "author", "body", "received_at"):
                if not isinstance(raw, dict) or not raw.get(key):
                    raise ValueError(f"interaction.{key} 必填")
            _instant(raw["received_at"])
            cid = _conversation_id(raw)
            existing = next((row for row in payload["conversations"] if row["id"] == cid), None)
            if existing:
                interaction_ids = set(existing.get("interaction_ids") or [existing["external_id"]])
                if raw["external_id"] in interaction_ids:
                    continue
                existing["interaction_ids"] = sorted(interaction_ids | {raw["external_id"]})
                existing.setdefault("messages", []).append(_message(raw))
                for key in ("external_id", "author", "body", "url", "received_at"):
                    existing[key] = copy.deepcopy(raw.get(key) or "")
                existing["status"] = "needs_draft"
                existing["draft"] = ""
                existing["history"].append({"at": _now(), "status": "needs_draft",
                                            "reason": "new_interaction", "external_id": raw["external_id"]})
                added += 1
                continue
            conversation = {
                "id": cid, "service_id": service_id, "source": raw["source"],
                "external_id": raw["external_id"], "parent_id": str(raw.get("parent_id") or ""),
                "author": raw["author"], "body": raw["body"], "url": str(raw.get("url") or ""),
                "received_at": raw["received_at"], "existing_user": raw.get("existing_user") is True,
                "status": "needs_draft", "draft": "", "interaction_ids": [raw["external_id"]],
                "messages": [_message(raw)],
                "history": [{"at": _now(), "status": "needs_draft", "reason": "imported"}],
            }
            for key in ("repository", "installation_id", "actor_id", "source_event_id",
                        "external_object_type", "trust", "conversation_key", "email_account",
                        "thread_id", "rfc_message_id", "subject"):
                if raw.get(key) is not None:
                    conversation[key] = copy.deepcopy(raw[key])
            payload["conversations"].append(conversation)
            added += 1
        return {"added": added, "duplicates": len(rows) - added}

    return growth_runtime.update_engagement(
        data_dir, service_id, mutate, connection=connection)


def list_conversations(data_dir, service_id):
    return _load(data_dir, service_id)["conversations"]


def _get(data_dir, service_id, conversation_id):
    payload = _load(data_dir, service_id)
    row = next((row for row in payload["conversations"] if row["id"] == conversation_id), None)
    if row is None:
        raise ValueError(f"未知会话 {conversation_id}")
    return payload, row


def next_action(data_dir, service_id):
    rows = [row for row in list_conversations(data_dir, service_id) if row["status"] in OPEN]
    if not rows:
        return None
    rows.sort(key=lambda row: (not row["existing_user"], row["received_at"], row["id"]))
    row = rows[0]
    return {
        "id": "inbox:" + row["id"],
        "kind": "inbox",
        "conversation_id": row["id"],
        "priority": 120 if row["existing_user"] else 100,
        "next_step": "draft" if row["status"] == "needs_draft" else row["status"],
        "human_level": "L1",
        "body": row["body"],
        "author": row["author"],
        "url": row["url"],
        "execution": {"intent": "live"},
    }


def draft(data_dir, service_id, conversation_id, text, by):
    if not str(text or "").strip():
        raise ValueError("回复正文不能为空")
    if not str(by or "").strip():
        raise ValueError("by 必填")
    def mutate(payload):
        row = next((item for item in payload["conversations"] if item["id"] == conversation_id), None)
        if row is None:
            raise ValueError(f"未知会话 {conversation_id}")
        if row["status"] in TERMINAL:
            raise ValueError(f"终态会话 {conversation_id} 不能再补写")
        row["draft"] = str(text).strip()
        row["status"] = "drafted"
        row["history"].append({"at": _now(), "status": "drafted", "by": by})
        return copy.deepcopy(row)

    return growth_runtime.update_engagement(data_dir, service_id, mutate)


def record_terminal(data_dir, service_id, conversation_id, status, by,
                    reason="", receipt_ref=""):
    if status not in {"committed", "dismissed", "needs_reconciliation"}:
        raise ValueError("status 必须是 committed/dismissed/needs_reconciliation")
    def mutate(payload):
        row = next((item for item in payload["conversations"] if item["id"] == conversation_id), None)
        if row is None:
            raise ValueError(f"未知会话 {conversation_id}")
        if status == "committed":
            if not row.get("draft"):
                raise ValueError("没有已审正文，不能记 committed")
            if not str(receipt_ref or "").strip():
                raise ValueError("committed 必须提供真实 receipt_ref")
        elif not str(reason or "").strip():
            raise ValueError("reason 必填")
        row["status"] = status
        if status == "dismissed":
            row["not_replied_reason"] = reason
        if receipt_ref:
            row["receipt_ref"] = receipt_ref
        row["history"].append({"at": _now(), "status": status, "by": by,
                               "reason": reason, "receipt_ref": receipt_ref})
        return copy.deepcopy(row)

    return growth_runtime.update_engagement(data_dir, service_id, mutate)


def status(data_dir, service_id, now=None, stale_minutes=60):
    moment = _instant(now) if now else dt.datetime.now().astimezone()
    counts = {}
    stale = []
    for row in list_conversations(data_dir, service_id):
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        if row["status"] in OPEN:
            age = (moment - _instant(row["received_at"])).total_seconds() / 60
            if age > stale_minutes:
                stale.append(row["id"])
    return {"counts": counts, "stale": stale, "stale_minutes": stale_minutes}
