from __future__ import annotations

import copy
import datetime as dt

from . import growth_runtime


OPERATIONS = {"read", "write", "verify"}
IDENTITY = ("account", "platform", "action", "route")
SENSITIVE = {"token", "access_token", "refresh_token", "private_key", "secret", "authorization_header"}
SENSITIVE_MARKERS = ("token", "secret", "password", "private_key", "api_key", "credential")


def _instant(value, label):
    if not value:
        return None
    stamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError(f"{label} 必须包含时区")
    return stamp


def _assert_no_credentials(value):
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in SENSITIVE or any(marker in normalized for marker in SENSITIVE_MARKERS):
                raise ValueError(f"能力记录不得包含凭据字段 {key}")
            _assert_no_credentials(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_credentials(item)


def upsert(data_dir, record, by):
    if not str(by or "").strip():
        raise ValueError("by 必填")
    if not isinstance(record, dict):
        raise ValueError("capability 必须是对象")
    _assert_no_credentials(record)
    value = copy.deepcopy(record)
    for key in IDENTITY:
        if not str(value.get(key) or "").strip():
            raise ValueError(f"capability.{key} 必填")
        value[key] = str(value[key]).strip().lower()
    value["account"] = value["account"] or "default"
    for key in ("implemented", "verified", "available", "required"):
        rows = value.get(key) or []
        if not isinstance(rows, list) or any(row not in OPERATIONS for row in rows):
            raise ValueError(f"capability.{key} 只能包含 read/write/verify")
        value[key] = sorted(set(rows))
    if not set(value["verified"]).issubset(value["implemented"]):
        raise ValueError("verified 必须是 implemented 的子集")
    if not set(value["available"]).issubset(value["implemented"]):
        raise ValueError("available 必须是 implemented 的子集")
    evidence = value.get("verification_evidence") or {}
    if not isinstance(evidence, dict):
        raise ValueError("verification_evidence 必须是对象")
    for operation in value["verified"]:
        if not str(evidence.get(operation) or "").strip():
            raise ValueError(f"{operation} 标记实测必须提供 verification evidence")
    if value["verified"]:
        _instant(value.get("last_verified_at"), "last_verified_at")
        if not value.get("last_verified_at"):
            raise ValueError("标记实测需要 last_verified_at")
    authorization = value.get("authorization") or {}
    if authorization.get("status") not in {"active", "revoked", "paused"}:
        raise ValueError("authorization.status 必须是 active/revoked/paused")
    if not isinstance(authorization.get("scope"), list) or not authorization["scope"]:
        raise ValueError("authorization.scope 必须是非空数组")
    _instant(authorization.get("expires_at"), "authorization.expires_at")
    value["authorization"] = copy.deepcopy(authorization)
    value.pop("operations", None)
    value.pop("missing", None)
    value.pop("executable", None)
    value.pop("authorized_now", None)
    growth_runtime.upsert_execution_capability(data_dir, value, str(by))
    return next(row for row in view(data_dir, account=value["account"], platform=value["platform"])
                if all(row[key] == value[key] for key in IDENTITY))


def _derive(record, now=None):
    row = copy.deepcopy(record)
    moment = now or dt.datetime.now().astimezone()
    authorization = row.get("authorization") or {}
    expires = _instant(authorization.get("expires_at"), "authorization.expires_at")
    authorized = authorization.get("status") == "active" and bool(authorization.get("scope"))
    if expires is not None and expires <= moment:
        authorized = False
    row["authorized_now"] = authorized
    operations = {}
    for operation in sorted(OPERATIONS):
        evidence = (row.get("verification_evidence") or {}).get(operation) or ""
        operations[operation] = {
            "implemented": operation in (row.get("implemented") or []),
            "available": operation in (row.get("available") or []),
            "verified": operation in (row.get("verified") or []),
            "verified_at": row.get("last_verified_at") if operation in (row.get("verified") or []) else "",
            "evidence_ref": evidence,
        }
    row["operations"] = operations
    missing = []
    for operation in row.get("required") or []:
        state = operations[operation]
        if not state["implemented"]:
            missing.append(f"{operation}_not_implemented")
        elif not state["available"]:
            missing.append(f"{operation}_unavailable")
        if not state["verified"]:
            missing.append(f"{operation}_not_verified")
    if authorization.get("status") == "revoked":
        missing.append("authorization_revoked")
    elif authorization.get("status") != "active":
        missing.append("authorization_inactive")
    elif expires is not None and expires <= moment:
        missing.append("authorization_expired")
    if row.get("paused_reason"):
        missing.append("capability_paused")
    row["missing"] = list(dict.fromkeys(missing))
    row["executable"] = authorized and not row.get("paused_reason") and not row["missing"]
    return row


def view(data_dir, account=None, platform=None, now=None):
    if now is not None:
        now = _instant(now, "now")
    rows = growth_runtime.execution_capability_records(data_dir, account=account, platform=platform)
    return [_derive(row, now=now) for row in rows]


def plan_readiness(data_dir, plan, account="default", now=None):
    rows = view(data_dir, account=account, now=now)
    output = []
    for action in plan.get("actions") or []:
        if action.get("kind") != "channel":
            continue
        matches = [row for row in rows if row["platform"] == action.get("channel")
                   and row["action"] == action.get("action")]
        ready_routes = [row for row in matches if row["executable"]]
        missing = []
        if not matches:
            missing = ["capability_record_missing"]
        else:
            for row in matches:
                missing.extend(row["missing"])
        output.append({
            "id": action["id"], "platform": action["channel"], "action": action["action"],
            "ready": bool(ready_routes), "routes": [row["route"] for row in ready_routes],
            "missing": list(dict.fromkeys(missing)),
        })
    return {"mode": "structured", "account": account, "actions": output,
            "ready": all(row["ready"] for row in output)}
