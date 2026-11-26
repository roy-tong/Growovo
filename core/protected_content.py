"""Protected X account detection and durable per-handle suppression."""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from .gates import load_suppressions
from .storage import AtomicJSONStore, FileLock, atomic_write_text

_MARKERS = (
    "these posts are protected",
    "this account is protected",
    "approved followers",
    "only approved followers",
    "这些帖子受到保护",
    "此账号受到保护",
    "批准的关注者",
    "经批准的关注者",
    "若要申请访问，点击关注",
)


def handle_for_target(target) -> str:
    text = str(target or "").strip()
    match = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)(?:/|$)", text)
    if match:
        return match.group(1)
    return text.lstrip("@") if re.fullmatch(r"@?[A-Za-z0-9_]{1,15}", text) else ""


def is_protected_text(text) -> bool:
    lowered = str(text or "").casefold()
    return any(marker.casefold() in lowered for marker in _MARKERS)


def is_target_suppressed(data_dir, target) -> bool:
    suppressed = load_suppressions(data_dir)
    handle = handle_for_target(target)
    candidates = {str(target or "").strip(), handle, "@" + handle}
    return (str(target or "").strip() in suppressed or
            bool(handle and {value.casefold() for value in candidates} &
                 {value.casefold() for value in suppressed}))


def mark_protected(data_dir, target, *, evidence=""):
    data_dir = Path(data_dir)
    handle = handle_for_target(target)
    if not handle:
        return {"ok": False, "reason": "handle_missing"}
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    path = data_dir / "state" / "protected-accounts.json"
    with FileLock(path.with_suffix(".lock")):
        store = AtomicJSONStore(path, default={"accounts": {}})
        payload = store.load()
        accounts = payload.setdefault("accounts", {})
        key = handle.casefold()
        is_new = key not in accounts
        if is_new:
            accounts[key] = {"handle": handle, "reason": "protected_account",
                             "first_seen_at": now, "evidence": str(evidence or "")[:300]}
        store.save(payload)
        suppress_path = data_dir / "suppress.txt"
        existing = set()
        if suppress_path.is_file():
            existing = {line.strip() for line in suppress_path.read_text(encoding="utf-8").splitlines()
                        if line.strip()}
        additions = [value for value in (handle, "@" + handle, str(target or "").strip())
                     if value and value not in existing]
        if additions:
            atomic_write_text(suppress_path, "\n".join(sorted(existing | set(additions))) + "\n")
    return {"ok": True, "handle": handle, "reason": "protected_account", "added": is_new}
