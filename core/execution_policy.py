#!/usr/bin/env python3
"""Workspace-scoped standing authorization for routine marketing writes.

A routine policy only replaces the per-run plan approval.  It never weakens
pause, suppression/DNC, quota, readback, channel-verification, or result
reconciliation gates.  The policy is deliberately not exposed as an MCP tool:
a human must call :func:`authorize` outside the agent execution path.

Account scope is strict.  A non-empty ``allow_accounts`` requires an exact,
case-sensitive match.  An empty/missing list authorizes only an unspecified
account (``""``) or the literal ``"default"``; it never means "any account".
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = 1
POLICY_RELATIVE_PATH = Path("policies") / "routine-marketing.json"

# This is a closed allowlist, not a list of examples.  High-risk operations
# (payments, contracts, discounts, crisis response, etc.) cannot be added by
# editing the JSON policy because allows() checks this set as well.
ROUTINE_ACTIONS = frozenset({
    "email:message",
    "x:publish", "x:reply", "x:message", "x:engage",
    "reddit:publish", "reddit:reply",
    "hn:publish", "hn:reply",
    "linkedin:message",
    "github:reply",
})


def list_routine_actions():
    """Return every action eligible for standing authorization, sorted."""
    return sorted(ROUTINE_ACTIONS)


def policy_path(data_dir):
    return Path(data_dir) / POLICY_RELATIVE_PATH


def _as_datetime(value, *, field="timestamp"):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field} 不是有效 ISO 时间: {value!r}") from exc
    raise ValueError(f"{field} 必须是 datetime 或 ISO 时间字符串")


def _now(value=None):
    return value if value is not None else datetime.now(timezone.utc)


def _current_for(reference=None):
    """Compare naive legacy timestamps naively and aware timestamps in UTC."""
    if reference is not None and reference.tzinfo is None:
        return datetime.now()
    return datetime.now(timezone.utc)


def _normalise_actions(actions):
    if actions is None:
        return list_routine_actions()
    if isinstance(actions, (str, bytes)) or not isinstance(actions, (list, tuple, set, frozenset)):
        raise ValueError("actions 必须是 channel:action 字符串列表")
    selected = set()
    for value in actions:
        if not isinstance(value, str) or value not in ROUTINE_ACTIONS:
            raise ValueError(
                f"动作不可常驻授权: {value!r}；只允许 {', '.join(list_routine_actions())}")
        selected.add(value)
    if not selected:
        raise ValueError("常驻授权至少需要一个明确的 routine 动作")
    return sorted(selected)


def _normalise_accounts(accounts):
    if accounts is None:
        return []
    if isinstance(accounts, (str, bytes)) or not isinstance(accounts, (list, tuple, set, frozenset)):
        raise ValueError("accounts 必须是账号 ID 列表；空列表仅允许 default/未指定账号")
    selected = set()
    for value in accounts:
        if not isinstance(value, str) or not value or value.strip() != value:
            raise ValueError("账号 ID 必须是非空、无首尾空白的字符串（精确匹配）")
        selected.add(value)
    return sorted(selected)


def _write(data_dir, policy):
    path = policy_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same-directory replace prevents readers from observing a partial policy.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)
    return policy


def authorize(data_dir, by, actions=None, accounts=None, days=None, now=None):
    """Create/replace the workspace routine policy and return its dict.

    ``by`` is mandatory audit attribution.  ``days=None`` means no expiry;
    otherwise it must be a positive integer.  ``now`` accepts a datetime or
    ISO timestamp and exists to make authorization deterministic in tests and
    administrative tooling.
    """
    if not isinstance(by, str) or not by.strip():
        raise ValueError("常驻授权必须由授权人署名（by）")
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or days <= 0):
        raise ValueError("days 必须是正整数或 None")
    current = _as_datetime(_now(now), field="now")
    policy = {
        "version": VERSION,
        "enabled": True,
        "allow_actions": _normalise_actions(actions),
        "allow_accounts": _normalise_accounts(accounts),
        "authorized_by": by.strip(),
        "authorized_at": current.isoformat(timespec="seconds"),
        "expires_at": ((current + timedelta(days=days)).isoformat(timespec="seconds")
                       if days is not None else None),
        "revoked": False,
    }
    return _write(data_dir, policy)


def get_policy(data_dir):
    """Return the stored policy, or ``None`` when no policy file exists.

    Malformed/non-object JSON raises ``ValueError``; execution callers use
    allows(), which fails closed on all such policy errors.
    """
    path = policy_path(data_dir)
    if not path.is_file():
        return None
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"routine policy JSON 无效: {path}") from exc
    if not isinstance(policy, dict):
        raise ValueError("routine policy 必须是 JSON 对象")
    return policy


def revoke(data_dir, by, now=None):
    """Revoke the existing policy in place, preserving its audit scope."""
    if not isinstance(by, str) or not by.strip():
        raise ValueError("撤销常驻授权必须由操作人署名（by）")
    policy = get_policy(data_dir)
    if policy is None:
        raise ValueError("没有可撤销的 routine policy")
    current = _as_datetime(_now(now), field="now")
    policy.update({
        "revoked": True,
        "revoked_by": by.strip(),
        "revoked_at": current.isoformat(timespec="seconds"),
    })
    return _write(data_dir, policy)


def _valid_policy(policy):
    """Return (policy, expiry) only for a structurally valid active policy."""
    if not isinstance(policy, dict):
        return None
    if policy.get("version") != VERSION or isinstance(policy.get("version"), bool):
        return None
    if policy.get("enabled") is not True or policy.get("revoked") is not False:
        return None
    if not isinstance(policy.get("authorized_by"), str) or not policy["authorized_by"].strip():
        return None
    try:
        _as_datetime(policy.get("authorized_at"), field="authorized_at")
        expires = policy.get("expires_at")
        expiry = _as_datetime(expires, field="expires_at") if expires else None
    except ValueError:
        return None
    actions = policy.get("allow_actions")
    accounts = policy.get("allow_accounts", [])
    if not isinstance(actions, list) or not actions:
        return None
    if any(not isinstance(a, str) or a not in ROUTINE_ACTIONS for a in actions):
        return None
    if not isinstance(accounts, list):
        return None
    if any(not isinstance(a, str) or not a or a.strip() != a for a in accounts):
        return None
    if expiry is not None and _current_for(expiry) >= expiry:
        return None
    return policy, expiry


def allows(data_dir, channel, action, account=''):
    """Return whether an active policy covers exactly this action/account.

    Missing, malformed, disabled, expired, revoked, broadened, or otherwise
    invalid policies all fail closed.  Callers must still run every non-
    approval safety gate after this function returns True.
    """
    channel = str(channel or "").strip().lower()
    action = str(action or "").strip().lower()
    operation = f"{channel}:{action}"
    if operation not in ROUTINE_ACTIONS:
        return False
    try:
        valid = _valid_policy(get_policy(data_dir))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if valid is None:
        return False
    policy, _ = valid
    if operation not in policy["allow_actions"]:
        return False

    allowed_accounts = policy.get("allow_accounts", [])
    # Keep account matching exact: no trimming, case folding, prefixes, or
    # wildcard expansion.  Empty scope is deliberately default-only.
    if account is None:
        account = ""
    elif not isinstance(account, str):
        return False
    if allowed_accounts:
        return account in allowed_accounts
    return account in ("", "default")
