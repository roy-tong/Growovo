"""Risk-tiered standing authorization for growth actions.

This is separate from the legacy routine-marketing policy.  It answers one
question before a write: may this class of action run without asking again,
for this scope, within this quota and before this expiry?  Missing, expired,
revoked or out-of-scope grants fail closed.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from .storage import AtomicJSONStore, FileLock, StorageError


VERSION = 1
CLASSES = (
    "observe", "analyze", "draft", "routine_reply", "new_outreach",
    "public_statement", "new_account_channel", "money_contract",
)
DEFAULTS = {
    "observe": "auto",
    "analyze": "auto",
    "draft": "auto",
    "routine_reply": "quota",
    "new_outreach": "grant",
    "public_statement": "review",
    "new_account_channel": "first_grant",
    "money_contract": "review",
}
# ``auto`` means no standing grant is needed.  ``grant``/``first_grant`` need
# an active grant.  ``quota`` needs an active grant with a remaining quota.
# ``review`` always needs a named human approval and cannot be granted broadly.
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _now():
    return dt.datetime.now().astimezone()


def _instant(value, label):
    text = str(value or "").strip()
    try:
        stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("%s 必须是 ISO 8601 时间" % label) from exc
    if stamp.tzinfo is None:
        raise ValueError("%s 必须包含时区" % label)
    return stamp


def path(data_dir, service_id):
    if not _ID.fullmatch(str(service_id or "")):
        raise ValueError("service_id 格式不正确")
    return Path(data_dir) / "state" / "risk-authorizations" / (service_id + ".json")


def _load(data_dir, service_id):
    return AtomicJSONStore(path(data_dir, service_id)).load()


def _save(data_dir, service_id, payload):
    return AtomicJSONStore(path(data_dir, service_id)).save(payload)


def _validate_grant(grant):
    errors = []
    if not isinstance(grant, dict):
        return ["grant 必须是对象"]
    if not _ID.fullmatch(str(grant.get("grant_id") or "")):
        errors.append("grant_id 格式不正确")
    if grant.get("action_class") not in CLASSES:
        errors.append("action_class 必须是 %s" % " / ".join(CLASSES))
    if grant.get("action_class") in ("observe", "analyze", "draft"):
        errors.append("自动类别不需要授权")
    if grant.get("action_class") in ("public_statement", "money_contract"):
        errors.append("该类别必须逐次具名审核，不能常驻授权")
    if not str(grant.get("granted_by") or "").strip():
        errors.append("granted_by 不能为空")
    try:
        _instant(grant.get("granted_at"), "granted_at")
    except ValueError as exc:
        errors.append(str(exc))
    if grant.get("expires_at"):
        try:
            _instant(grant["expires_at"], "expires_at")
        except ValueError as exc:
            errors.append(str(exc))
    quota = grant.get("quota")
    if quota is not None and (not isinstance(quota, int) or isinstance(quota, bool) or quota < 0):
        errors.append("quota 必须是 >= 0 的整数或 null")
    scope = grant.get("scope")
    if scope is not None and not isinstance(scope, dict):
        errors.append("scope 必须是对象")
    return errors


def grant(data_dir, service_id, *, action_class, granted_by, scope=None, quota=None,
          days=None, now=None):
    if action_class not in CLASSES:
        raise ValueError("未知 action_class：%s" % action_class)
    if action_class in ("observe", "analyze", "draft"):
        raise ValueError("%s 默认自动，不需要授权" % action_class)
    if action_class in ("public_statement", "money_contract"):
        raise ValueError("%s 必须逐次具名审核，不能常驻授权" % action_class)
    if not str(granted_by or "").strip():
        raise ValueError("granted_by 不能为空")
    current = now or _now()
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or days <= 0):
        raise ValueError("days 必须是正整数或 None")
    with FileLock(path(data_dir, service_id).with_suffix(".lock")):
        payload = _load(data_dir, service_id)
        record = {
            "grant_id": "grant-" + current.strftime("%Y%m%d-%H%M%S") + "-" + str(len(payload.get("grants") or []) + 1),
            "action_class": action_class,
            "scope": scope or {},
            "quota": quota,
            "used": 0,
            "granted_by": str(granted_by).strip(),
            "granted_at": current.isoformat(timespec="seconds"),
            "expires_at": ((current + dt.timedelta(days=days)).isoformat(timespec="seconds")
                           if days is not None else ""),
            "revoked_at": "",
        }
        errors = _validate_grant(record)
        if errors:
            raise ValueError("授权无效：" + "；".join(errors))
        payload.setdefault("schema", "growovo.risk-authorizations/1")
        payload.setdefault("service_id", service_id)
        payload.setdefault("grants", []).append(record)
        _save(data_dir, service_id, payload)
        return record


def revoke(data_dir, service_id, grant_id, *, by):
    if not str(by or "").strip():
        raise ValueError("撤销必须署名")
    with FileLock(path(data_dir, service_id).with_suffix(".lock")):
        payload = _load(data_dir, service_id)
        for item in payload.get("grants") or []:
            if item.get("grant_id") == grant_id:
                if item.get("revoked_at"):
                    return {"status": "unchanged", "grant": item}
                item["revoked_at"] = _now().isoformat(timespec="seconds")
                item["revoked_by"] = str(by).strip()
                _save(data_dir, service_id, payload)
                return {"status": "revoked", "grant": item}
    raise ValueError("没有授权 %s" % grant_id)


def list_grants(data_dir, service_id):
    return _load(data_dir, service_id).get("grants") or []


def _match_scope(grant_scope, request_scope):
    for key, expected in (grant_scope or {}).items():
        actual = (request_scope or {}).get(key)
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True


def explain(data_dir, service_id, *, action_class, scope=None, now=None):
    current = now or _now()
    if action_class not in CLASSES:
        return {"allowed": False, "reason": "unknown_action_class",
                "default": None, "grant": None}
    default = DEFAULTS[action_class]
    if default == "auto":
        return {"allowed": True, "reason": "default_auto", "default": default,
                "grant": None, "requires": "none"}
    if default == "review":
        return {"allowed": False, "reason": "named_review_required", "default": default,
                "grant": None, "requires": "named_review"}
    if default == "first_grant":
        return {"allowed": False, "reason": "first_grant_required", "default": default,
                "grant": None, "requires": "grant"}
    active = []
    for item in list_grants(data_dir, service_id):
        if item.get("action_class") != action_class:
            continue
        if item.get("revoked_at"):
            continue
        if item.get("expires_at"):
            try:
                if current >= _instant(item["expires_at"], "expires_at"):
                    continue
            except ValueError:
                continue
        if not _match_scope(item.get("scope") or {}, scope or {}):
            continue
        quota = item.get("quota")
        if quota is not None and int(item.get("used") or 0) >= int(quota):
            continue
        active.append(item)
    if not active:
        return {"allowed": False, "reason": "no_active_grant", "default": default,
                "grant": None, "requires": "grant"}
    active.sort(key=lambda row: row.get("granted_at") or "")
    return {"allowed": True, "reason": "grant_active", "default": default,
            "grant": active[-1], "requires": "grant"}


def consume(data_dir, service_id, grant_id, *, amount=1, action_class=None, scope=None, now=None,
            reservation_id=""):
    if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
        raise ValueError("amount 必须是正整数")
    try:
        with FileLock(path(data_dir, service_id).with_suffix(".lock")):
            payload = _load(data_dir, service_id)
            for item in payload.get("grants") or []:
                if item.get("grant_id") != grant_id:
                    continue
                if item.get("revoked_at"):
                    raise ValueError("授权已撤销")
                current = now or _now()
                if item.get("expires_at") and current >= _instant(item["expires_at"], "expires_at"):
                    raise ValueError("授权已过期")
                if action_class is not None and item.get("action_class") != action_class:
                    raise ValueError("授权类别不匹配")
                if not _match_scope(item.get("scope") or {}, scope or {}):
                    raise ValueError("授权范围不匹配")
                reservations = item.setdefault("quota_reservations", [])
                if reservation_id and any(row.get("attempt_id") == reservation_id for row in reservations):
                    return item
                quota = item.get("quota")
                used = int(item.get("used") or 0)
                if quota is not None and used + amount > int(quota):
                    raise ValueError("授权配额不足：%s/%s" % (used, quota))
                item["used"] = used + amount
                item["last_used_at"] = current.isoformat(timespec="seconds")
                if reservation_id:
                    reservations.append({"attempt_id": reservation_id, "amount": amount,
                                         "state": "reserved", "at": current.isoformat(timespec="seconds")})
                _save(data_dir, service_id, payload)
                return item
    except StorageError as exc:
        raise ValueError("授权锁不可用：%s" % exc) from exc
    raise ValueError("没有授权 %s" % grant_id)



def settle(data_dir, service_id, grant_id, *, reservation_id, outcome, now=None):
    if outcome not in {"released", "settled", "unknown"}:
        raise ValueError("未知额度结算结果")
    current = now or _now()
    with FileLock(path(data_dir, service_id).with_suffix(".lock")):
        payload = _load(data_dir, service_id)
        for item in payload.get("grants") or []:
            if item.get("grant_id") != grant_id:
                continue
            for reservation in item.get("quota_reservations") or []:
                if reservation.get("attempt_id") != reservation_id:
                    continue
                if reservation.get("state") != "reserved":
                    return reservation
                if outcome == "released":
                    item["used"] = max(0, int(item.get("used") or 0) - int(reservation.get("amount") or 1))
                reservation["state"] = outcome
                reservation["settled_at"] = current.isoformat(timespec="seconds")
                _save(data_dir, service_id, payload)
                return reservation
        raise ValueError("没有对应的额度预留")
