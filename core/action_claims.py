from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .ledger import _append_line, _read_jsonl_fail_loud

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None


class ClaimError(RuntimeError):
    pass


class DuplicateAction(ClaimError):
    def __init__(self, key: str, state: str):
        super().__init__("action already claimed; no external write was attempted: %s" % key)
        self.key = key
        self.state = state


def stable_key(channel: str, action: str, target: Any, content: Any, account: Any = "") -> str:
    payload = json.dumps({
        "channel": str(channel or "").lower(), "action": str(action or "").lower(),
        "target": str(target or ""), "content": str(content or ""),
        "account": str(account or ""),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / "state" / "action-claims.jsonl"


def claims(data_dir: Path) -> list[Dict[str, Any]]:
    return _read_jsonl_fail_loud(_path(Path(data_dir)), kind="action claims")


@contextmanager
def _locked(data_dir: Path):
    state = Path(data_dir) / "state"
    state.mkdir(parents=True, exist_ok=True)
    handle = (state / "action-claims.lock").open("a+b")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows
            handle.seek(0)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            raise ClaimError("no supported file lock; refusing an unsafe action claim")
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()


def claim(data_dir: Path, *, key: str) -> Dict[str, Any]:
    if not isinstance(key, str) or len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
        raise ClaimError("action claim key must be a sha256 hex digest")
    with _locked(Path(data_dir)):
        existing = [row for row in claims(data_dir) if row.get("key") == key]
        if existing:
            latest = existing[-1]
            state = "indeterminate" if latest.get("status") == "claimed" else str(
                latest.get("status") or "indeterminate")
            raise DuplicateAction(key, state)
        row = {
            "schema": "growovo.action-claim/1", "key": key, "status": "claimed",
            "claimed_at": datetime.now(timezone.utc).isoformat(),
            "recovery": "a claimed action without a final record is indeterminate; inspect before any retry",
        }
        _append_line(_path(Path(data_dir)), json.dumps(row, ensure_ascii=False) + "\n")
        return row


def finish(data_dir: Path, *, key: str, result: str) -> Dict[str, Any]:
    if result not in ("committed", "failed", "indeterminate", "rejected", "skipped",
                      "target_rejected", "unavailable_or_unrenderable", "deferred"):
        raise ClaimError("unsupported action claim result: %s" % result)
    row = {"schema": "growovo.action-claim/1", "key": key, "status": result,
           "finished_at": datetime.now(timezone.utc).isoformat()}
    _append_line(_path(Path(data_dir)), json.dumps(row, ensure_ascii=False) + "\n")
    return row
