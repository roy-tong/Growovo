#!/usr/bin/env python3
"""Authorized account registry and audit model.

This module deliberately has no login, session, browser, or send operation. It
only records who authorized an identity, whether the platform permits another
account, and whether a human may use it in the current lifecycle state.
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

REAL_AUTHORIZED = "real_authorized"
PSEUDONYMOUS = "pseudonymous"
BRAND = "brand"
IDENTITY_TYPES = (REAL_AUTHORIZED, PSEUDONYMOUS, BRAND)
IDENTITY_ALIASES = {
    "real": REAL_AUTHORIZED, "authorized": REAL_AUTHORIZED, "真实授权身份": REAL_AUTHORIZED,
    "pseudonym": PSEUDONYMOUS, "alias": PSEUDONYMOUS, "化名": PSEUDONYMOUS,
    "brand_account": BRAND, "品牌账号": BRAND,
}
STATUSES = ("candidate", "warming", "eligible", "limited", "paused", "retired")
TRANSITIONS = {
    "candidate": {"warming", "retired"},
    "warming": {"eligible", "limited", "paused", "retired"},
    "eligible": {"limited", "paused", "retired"},
    "limited": {"eligible", "paused", "retired"},
    "paused": {"warming", "eligible", "limited", "retired"},
    "retired": set(),
}
SECRET_KEYS = {
    "password", "passwd", "token", "secret", "cookie", "credential", "credentials",
    "session", "session_key", "access_key", "private_key", "proxy_url",
}


def _timestamp(value: Optional[Union[str, datetime]] = None) -> str:
    if value is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("时间必须是 ISO 日期时间") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _datetime(value: Any) -> datetime:
    return datetime.fromisoformat(_timestamp(value).replace("Z", "+00:00"))


def _assert_no_secrets(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in {re.sub(r"[^a-z0-9]", "", x) for x in SECRET_KEYS}:
                raise ValueError(f"账号登记不保存凭据字段：{key}")
            _assert_no_secrets(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_no_secrets(item)


def _evidence(value: Any) -> List[Any]:
    if value in (None, "", [], ()):
        return []
    values = value if isinstance(value, (list, tuple)) else [value]
    out = []
    for item in values:
        _assert_no_secrets(item)
        if isinstance(item, dict):
            if any(str(v or "").strip() for v in item.values()):
                out.append(copy.deepcopy(item))
        elif str(item or "").strip():
            out.append(str(item).strip())
    return out


def normalize_identity(value: Any) -> str:
    key = str(value or "").strip().lower()
    key = IDENTITY_ALIASES.get(key, key)
    if key not in IDENTITY_TYPES:
        raise ValueError(f"identity_type 必须是 {', '.join(IDENTITY_TYPES)}")
    return key


def transition_allowed(current: str, new: str) -> bool:
    return new in TRANSITIONS.get(current, set())


def cooling_down(account: Dict[str, Any], now: Optional[Union[str, datetime]] = None) -> bool:
    until = account.get("cooldown_until")
    if not until:
        return False
    return _datetime(until) > (_datetime(now) if now else datetime.now(timezone.utc))


class AccountRegistry:
    """Local account metadata store; every mutation produces an audit event."""

    def __init__(self, path: Optional[Union[str, Path]] = None):
        self.path = Path(path) if path else None
        self._accounts: Dict[str, Dict[str, Any]] = {}
        self._audit: List[Dict[str, Any]] = []
        if self.path and self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            _assert_no_secrets(raw)
            self._accounts = {x["id"]: x for x in raw.get("accounts", [])}
            self._audit = list(raw.get("audit", []))

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "accounts": list(self._accounts.values()), "audit": self._audit}
        _assert_no_secrets(payload)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    def _event(self, event: str, *, account_id: str, actor: str, reason: str = "",
               at: Optional[Union[str, datetime]] = None, **details: Any) -> Dict[str, Any]:
        _assert_no_secrets(details)
        item = {
            "seq": len(self._audit) + 1,
            "at": _timestamp(at),
            "event": event,
            "account_id": account_id,
            "actor": str(actor or "local-human"),
            "reason": str(reason or ""),
            "details": copy.deepcopy(details),
        }
        self._audit.append(item)
        return item

    def register(self, platform: str, handle: str, identity_type: str, *,
                 account_id: Optional[str] = None, owner: str = "",
                 ownership_evidence: Any = None, authorization_evidence: Any = None,
                 multi_account_allowed: bool = False, multi_account_evidence: Any = None,
                 disclosure: str = "", actor: str = "local-human",
                 at: Optional[Union[str, datetime]] = None) -> Dict[str, Any]:
        platform, handle = str(platform or "").strip(), str(handle or "").strip()
        if not platform or not handle:
            raise ValueError("platform 和 handle 不能为空")
        kind = normalize_identity(identity_type)
        ownership = _evidence(ownership_evidence)
        authorization = _evidence(authorization_evidence)
        multi_evidence = _evidence(multi_account_evidence)
        if not ownership:
            raise ValueError("必须提供账号归属证据")
        if kind in (REAL_AUTHORIZED, BRAND) and not authorization:
            raise ValueError("真实授权身份和品牌账号必须提供授权证据")
        if kind in (REAL_AUTHORIZED, BRAND) and not str(owner or "").strip():
            raise ValueError("真实授权身份和品牌账号必须记录归属主体")
        if multi_account_allowed is not True and multi_account_allowed is not False:
            raise ValueError("multi_account_allowed 必须显式为布尔值")
        if multi_account_allowed and not multi_evidence:
            raise ValueError("声明平台允许多账号时必须附平台规则证据")
        others = [a for a in self._accounts.values()
                  if a["platform"] == platform and a["status"] != "retired"]
        if others and not multi_account_allowed:
            raise ValueError("已有同平台账号；没有平台多账号许可不能再登记")
        if others and not multi_evidence:
            raise ValueError("追加同平台账号必须附平台多账号许可证据")
        aid = str(account_id or uuid.uuid4().hex)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}", aid):
            raise ValueError("account_id 格式无效")
        if aid in self._accounts:
            raise ValueError("account_id 已存在")
        created = _timestamp(at)
        account = {
            "id": aid,
            "platform": platform,
            "handle": handle,
            "identity_type": kind,
            "owner": str(owner or "").strip(),
            "ownership_evidence": ownership,
            "authorization_evidence": authorization,
            "multi_account_allowed": multi_account_allowed,
            "multi_account_evidence": multi_evidence,
            "disclosure": str(disclosure or "").strip(),
            "status": "candidate",
            "health": {"score": 100.0, "state": "healthy", "updated_at": created, "evidence": []},
            "cooldown_until": None,
            "human_takeover": {"required": False, "reason": "", "requested_at": None},
            "created_at": created,
            "updated_at": created,
        }
        _assert_no_secrets(account)
        self._accounts[aid] = account
        self._event("account_registered", account_id=aid, actor=actor, at=at,
                    identity_type=kind, platform=platform, handle=handle)
        self._save()
        return copy.deepcopy(account)

    # Familiar aliases for callers; all still use the same audited path.
    create = register
    create_account = register

    def get(self, account_id: str) -> Dict[str, Any]:
        if account_id not in self._accounts:
            raise KeyError(f"未知账号：{account_id}")
        return copy.deepcopy(self._accounts[account_id])

    def all(self, *, platform: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        return [copy.deepcopy(a) for a in self._accounts.values()
                if (not platform or a["platform"] == platform)
                and (not status or a["status"] == status)]

    def transition(self, account_id: str, new: str, *, reason: str = "",
                   evidence: Any = None, reviewer: str = "", actor: str = "local-human",
                   at: Optional[Union[str, datetime]] = None) -> Dict[str, Any]:
        account = self._accounts.get(account_id)
        if account is None:
            raise KeyError(f"未知账号：{account_id}")
        current = account["status"]
        if new not in STATUSES:
            raise ValueError(f"未知账号状态：{new}")
        if not transition_allowed(current, new):
            raise ValueError(f"不允许账号状态跳转：{current} → {new}")
        if new in ("limited", "paused", "retired") and not str(reason or "").strip():
            raise ValueError(f"进入 {new} 必须记录原因")
        proof = _evidence(evidence)
        if new == "eligible":
            if not proof or not str(reviewer or "").strip():
                raise ValueError("进入 eligible 必须有养号复核证据和复核人")
            if current == "paused" and not account.get("warming_review"):
                raise ValueError("人工接管不能绕过 warming；先恢复 warming 并完成复核")
            if account["human_takeover"]["required"]:
                raise ValueError("人工接管尚未解除，不能进入 eligible")
            if cooling_down(account, at):
                raise ValueError("冷却期内不能进入 eligible")
            if float(account["health"]["score"]) < 60:
                raise ValueError("健康度低于 60，不能进入 eligible")
            account["warming_review"] = {"reviewer": reviewer.strip(), "evidence": proof,
                                         "reviewed_at": _timestamp(at)}
        account["status"] = new
        account["updated_at"] = _timestamp(at)
        self._event("account_transitioned", account_id=account_id, actor=actor,
                    reason=reason, at=at, previous=current, new=new,
                    reviewer=str(reviewer or ""), evidence=proof)
        self._save()
        return copy.deepcopy(account)

    def record_health(self, account_id: str, score: float, *, evidence: Any,
                      reason: str = "", actor: str = "local-human",
                      at: Optional[Union[str, datetime]] = None) -> Dict[str, Any]:
        account = self._accounts.get(account_id)
        if account is None:
            raise KeyError(f"未知账号：{account_id}")
        if isinstance(score, bool):
            raise ValueError("健康度必须是 0–100 的数字")
        try:
            value = float(score)
        except (TypeError, ValueError) as exc:
            raise ValueError("健康度必须是 0–100 的数字") from exc
        if not 0 <= value <= 100:
            raise ValueError("健康度必须在 0–100 之间")
        proof = _evidence(evidence)
        if not proof:
            raise ValueError("健康度变化必须附观察证据")
        state = "healthy" if value >= 80 else "watch" if value >= 60 else "at_risk"
        account["health"] = {"score": value, "state": state,
                             "updated_at": _timestamp(at), "evidence": proof}
        account["updated_at"] = _timestamp(at)
        self._event("health_recorded", account_id=account_id, actor=actor,
                    reason=reason, at=at, score=value, state=state, evidence=proof)
        if value < 40 and account["status"] == "eligible":
            account["status"] = "limited"
            self._event("account_transitioned", account_id=account_id, actor=actor,
                        reason=reason or "健康度低于 40，自动限制", at=at,
                        previous="eligible", new="limited", reviewer="", evidence=proof)
        self._save()
        return copy.deepcopy(account)

    def start_cooldown(self, account_id: str, *, until: Optional[Union[str, datetime]] = None,
                       seconds: Optional[int] = None, reason: str = "人工设置冷却",
                       actor: str = "local-human", at: Optional[Union[str, datetime]] = None) -> Dict[str, Any]:
        account = self._accounts.get(account_id)
        if account is None:
            raise KeyError(f"未知账号：{account_id}")
        if account["status"] == "retired":
            raise ValueError("已退休账号不能设置冷却")
        if (until is None) == (seconds is None):
            raise ValueError("冷却必须且只能提供 until 或 seconds")
        now = _datetime(at) if at else datetime.now(timezone.utc)
        if seconds is not None:
            if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
                raise ValueError("冷却秒数必须是正整数")
            end = now + timedelta(seconds=seconds)
        else:
            end = _datetime(until)
        if end <= now:
            raise ValueError("冷却结束时间必须晚于当前时间")
        account["cooldown_until"] = _timestamp(end)
        account["updated_at"] = _timestamp(now)
        self._event("cooldown_started", account_id=account_id, actor=actor,
                    reason=reason, at=now, cooldown_until=account["cooldown_until"])
        self._save()
        return copy.deepcopy(account)

    def clear_cooldown(self, account_id: str, *, reviewer: str, reason: str = "",
                       at: Optional[Union[str, datetime]] = None) -> Dict[str, Any]:
        if not str(reviewer or "").strip():
            raise ValueError("提前解除冷却需要复核人")
        account = self._accounts.get(account_id)
        if account is None:
            raise KeyError(f"未知账号：{account_id}")
        previous = account.get("cooldown_until")
        account["cooldown_until"] = None
        account["updated_at"] = _timestamp(at)
        self._event("cooldown_cleared", account_id=account_id, actor=reviewer,
                    reason=reason, at=at, previous=previous)
        self._save()
        return copy.deepcopy(account)

    def request_human_takeover(self, account_id: str, reason: str, *,
                               actor: str = "local-human",
                               at: Optional[Union[str, datetime]] = None) -> Dict[str, Any]:
        if not str(reason or "").strip():
            raise ValueError("人工接管必须说明原因")
        account = self._accounts.get(account_id)
        if account is None:
            raise KeyError(f"未知账号：{account_id}")
        if account["status"] == "retired":
            raise ValueError("已退休账号不能再接管")
        previous = account["status"]
        account["status"] = "paused"
        account["human_takeover"] = {"required": True, "reason": reason.strip(),
                                     "requested_at": _timestamp(at)}
        account["updated_at"] = _timestamp(at)
        self._event("human_takeover_requested", account_id=account_id, actor=actor,
                    reason=reason, at=at, previous=previous, new="paused")
        self._save()
        return copy.deepcopy(account)

    def resolve_human_takeover(self, account_id: str, *, reviewer: str, note: str,
                               at: Optional[Union[str, datetime]] = None) -> Dict[str, Any]:
        if not str(reviewer or "").strip() or not str(note or "").strip():
            raise ValueError("解除人工接管需要复核人和处理记录")
        account = self._accounts.get(account_id)
        if account is None:
            raise KeyError(f"未知账号：{account_id}")
        if not account["human_takeover"]["required"]:
            raise ValueError("该账号没有待处理的人工接管")
        account["human_takeover"].update({
            "required": False, "resolved_by": reviewer.strip(),
            "resolution": note.strip(), "resolved_at": _timestamp(at),
        })
        # Resolving the incident does not silently restore eligibility.
        account["status"] = "paused"
        account["updated_at"] = _timestamp(at)
        self._event("human_takeover_resolved", account_id=account_id, actor=reviewer,
                    reason=note, at=at, status="paused")
        self._save()
        return copy.deepcopy(account)

    def operational(self, account_id: str, *, now: Optional[Union[str, datetime]] = None) -> Dict[str, Any]:
        account = self.get(account_id)
        reasons = []
        if account["status"] != "eligible":
            reasons.append(f"生命周期状态为 {account['status']}")
        if account["human_takeover"]["required"]:
            reasons.append("需要人工接管")
        if cooling_down(account, now):
            reasons.append("仍在冷却期")
        if float(account["health"]["score"]) < 60:
            reasons.append("健康度低于 60")
        return {"eligible": not reasons, "reasons": reasons, "account_id": account_id,
                "note": "仅代表本地授权与状态检查，不执行任何账号操作"}

    def can_operate(self, account_id: str, *, now: Optional[Union[str, datetime]] = None) -> bool:
        return self.operational(account_id, now=now)["eligible"]

    def audit(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return copy.deepcopy([x for x in self._audit
                              if account_id is None or x["account_id"] == account_id])


def create_account(data: Optional[Dict[str, Any]] = None, *, registry: Optional[AccountRegistry] = None,
                   **kwargs: Any) -> Dict[str, Any]:
    """Convenience constructor using the same validation as AccountRegistry."""
    payload = dict(data or {})
    payload.update(kwargs)
    _assert_no_secrets(payload)
    return (registry or AccountRegistry()).register(**payload)
