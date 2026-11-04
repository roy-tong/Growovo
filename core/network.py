#!/usr/bin/env python3
"""Proxy environment references bound to authorized accounts.

Only environment variable *names* are stored. This module never reads their
values, opens sockets, changes platform quotas, evades restrictions, or alters
account authorization.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SECRET_NAME = re.compile(r"(?:PASSWORD|PASSWD|TOKEN|SECRET|COOKIE|CREDENTIAL|PRIVATE_KEY)", re.I)
FORBIDDEN_USE = re.compile(
    r"(?:increase[_ -]?quota|raise[_ -]?quota|quota[_ -]?increase|"
    r"bypass|circumvent|evad(?:e|ing|ing_ban)|ban[_ -]?evasion|avoid[_ -]?ban|"
    r"override[_ -]?authorization|forged?_?authorization|unauthorized|"
    r"提高配额|提升配额|绕过|绕封|规避封禁|封禁|改授权|伪造授权|未授权)", re.I)
UNCHANGED_EFFECTS = {
    "quota_multiplier": 1,
    "quota_increase": False,
    "ban_evasion": False,
    "authorization_override": False,
    "account_permissions": "unchanged",
}
SECRET_KEYS = {"password", "passwd", "token", "secret", "cookie", "credential", "credentials",
               "proxy", "proxy_url", "url", "uri", "value"}


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


def _env_name(value: Any) -> str:
    name = str(value or "").strip()
    if not ENV_NAME.fullmatch(name) or SECRET_NAME.search(name):
        raise ValueError("代理只允许普通环境变量名；不能保存 URL、凭据或凭据变量")
    return name


def _assert_reference_only(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in {re.sub(r"[^a-z0-9]", "", x) for x in SECRET_KEYS}:
                # The structured proxy descriptor is the one safe exception:
                # it contains only type=environment_variable and a variable name.
                if normalized != "proxy" or not _safe_descriptor(item):
                    raise ValueError(f"网络配置不保存凭据或代理值字段：{key}")
            _assert_reference_only(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_reference_only(item)


def _safe_descriptor(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == {"type", "name"}
            and value.get("type") == "environment_variable"
            and bool(ENV_NAME.fullmatch(str(value.get("name") or ""))))


def validate_use(purpose: str = "authorized_access", *, increase_quota: bool = False,
                 bypass_ban: bool = False, change_authorization: bool = False,
                 bypass_restrictions: bool = False) -> Dict[str, Any]:
    """Reject prohibited intent; successful validation still changes no quota."""
    if any((increase_quota, bypass_ban, change_authorization, bypass_restrictions)):
        raise ValueError("代理不能提高配额、绕封禁或改变授权")
    text = str(purpose or "").strip()
    if not text:
        raise ValueError("代理绑定必须记录合法用途")
    if FORBIDDEN_USE.search(text):
        raise ValueError("代理不能用于提高配额、绕封禁、规避限制或改变授权")
    return copy.deepcopy(UNCHANGED_EFFECTS)


class ProxyRegistry:
    """Audited local mapping from account ids to environment variable names."""

    def __init__(self, path: Optional[Union[str, Path]] = None):
        self.path = Path(path) if path else None
        self._bindings: Dict[str, Dict[str, Any]] = {}
        self._audit: List[Dict[str, Any]] = []
        if self.path and self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            _assert_reference_only(raw)
            self._bindings = {x["account_id"]: x for x in raw.get("bindings", [])}
            self._audit = list(raw.get("audit", []))

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "bindings": list(self._bindings.values()), "audit": self._audit}
        _assert_reference_only(payload)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    def _event(self, event: str, *, account_id: str, actor: str, at: Any = None,
               **details: Any) -> None:
        _assert_reference_only(details)
        self._audit.append({
            "seq": len(self._audit) + 1, "at": _timestamp(at), "event": event,
            "account_id": account_id, "actor": str(actor or "local-human"),
            "details": copy.deepcopy(details),
        })

    def bind(self, account_id: str, env_var: str, *, platform: str = "",
             purpose: str = "authorized_access", actor: str = "local-human",
             at: Any = None, account_registry: Any = None) -> Dict[str, Any]:
        aid = str(account_id or "").strip()
        if not aid:
            raise ValueError("代理必须绑定明确 account_id")
        if account_registry is not None:
            account = account_registry.get(aid)
            if platform and account["platform"] != platform:
                raise ValueError("代理绑定的平台与账号平台不一致")
            platform = account["platform"]
        name = _env_name(env_var)
        effects = validate_use(purpose)
        created = _timestamp(at)
        previous = self._bindings.get(aid)
        binding = {
            "account_id": aid,
            "platform": str(platform or "").strip(),
            "env_var": name,
            "proxy": {"type": "environment_variable", "name": name},
            "purpose": purpose.strip(),
            "effects": effects,
            "created_at": created,
        }
        _assert_reference_only(binding)
        self._bindings[aid] = binding
        self._event("proxy_bound" if previous is None else "proxy_rebound",
                    account_id=aid, actor=actor, at=at, env_var=name,
                    previous_env_var=(previous or {}).get("env_var"), purpose=purpose)
        self._save()
        return copy.deepcopy(binding)

    def get(self, account_id: str) -> Dict[str, Any]:
        if account_id not in self._bindings:
            raise KeyError(f"账号没有代理环境变量绑定：{account_id}")
        return copy.deepcopy(self._bindings[account_id])

    def all(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(list(self._bindings.values()))

    def unbind(self, account_id: str, *, actor: str = "local-human", reason: str = "",
               at: Any = None) -> Dict[str, Any]:
        if account_id not in self._bindings:
            raise KeyError(f"账号没有代理环境变量绑定：{account_id}")
        previous = self._bindings.pop(account_id)
        self._event("proxy_unbound", account_id=account_id, actor=actor, at=at,
                    env_var=previous["env_var"], reason=str(reason or ""))
        self._save()
        return copy.deepcopy(previous)

    def audit(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return copy.deepcopy([x for x in self._audit
                              if account_id is None or x["account_id"] == account_id])

    def export(self) -> Dict[str, Any]:
        """Safe serialization containing references, never environment values."""
        return {"bindings": self.all(), "audit": self.audit(),
                "effects": copy.deepcopy(UNCHANGED_EFFECTS)}


def bind_proxy(account_id: str, env_var: str, *, registry: Optional[ProxyRegistry] = None,
               **kwargs: Any) -> Dict[str, Any]:
    if any(key in kwargs for key in ("proxy_url", "url", "password", "token", "secret", "value")):
        raise ValueError("bind_proxy 只接受环境变量引用，不接受代理值或凭据")
    return (registry or ProxyRegistry()).bind(account_id, env_var, **kwargs)


def assert_permitted_use(binding: Dict[str, Any], purpose: Optional[str] = None,
                         **intent: Any) -> Dict[str, Any]:
    """Validate a stored reference and return its fixed no-escalation effects."""
    _assert_reference_only(binding)
    if not _safe_descriptor(binding.get("proxy")):
        raise ValueError("绑定缺少安全的环境变量引用")
    if binding.get("env_var") != binding["proxy"]["name"]:
        raise ValueError("环境变量引用不一致")
    return validate_use(purpose or binding.get("purpose") or "authorized_access", **intent)


def use_allowed(purpose: str = "authorized_access", **intent: Any) -> bool:
    try:
        validate_use(purpose, **intent)
        return True
    except ValueError:
        return False
