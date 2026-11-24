"""Immutable per-attempt runtime fingerprints for real engagement waves."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Dict


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def source_fingerprint(source_dir) -> Dict[str, Any]:
    root = Path(source_dir)
    files = [root / "mkt.py"]
    files.extend((root / "core").rglob("*.py"))
    files.extend((root / "channels").rglob("*.py"))
    digest = hashlib.sha256()
    count = 0
    for path in sorted({p for p in files if p.is_file() and "__pycache__" not in p.parts}):
        relative = str(path.relative_to(root))
        raw = path.read_bytes()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(str(len(raw)).encode("ascii") + b"\0")
        digest.update(raw)
        count += 1
    return {"algorithm": "sha256-path-length-content", "sha256": digest.hexdigest(),
            "file_count": count}


def git_snapshot(source_dir) -> Dict[str, Any]:
    root = Path(source_dir)
    result = {"available": False, "head": None, "dirty": None,
              "status_sha256": None, "status_entries": None}
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root),
                              capture_output=True, text=True, timeout=10, check=False)
        if head.returncode != 0:
            return result
        status = subprocess.run(["git", "status", "--porcelain"], cwd=str(root),
                                capture_output=True, text=True, timeout=10, check=False)
        if status.returncode != 0:
            return result
        lines = [line for line in status.stdout.splitlines() if line.strip()]
        result.update(available=True, head=head.stdout.strip(), dirty=bool(lines),
                      status_sha256=_sha256_bytes(status.stdout.encode("utf-8")),
                      status_entries=len(lines))
    except (OSError, subprocess.TimeoutExpired):
        return result
    return result


def write_manifest(data_dir, *, source_dir, queue_path, policy: Dict[str, Any],
                   night: str, wave: str, campaign: str, session: str,
                   preflight: Dict[str, Any], now=None, attempt_id: str = "") -> Dict[str, Any]:
    data_dir = Path(data_dir)
    source_dir = Path(source_dir)
    queue_path = Path(queue_path)
    current = now or dt.datetime.now().astimezone()
    attempt = attempt_id or current.strftime("%Y%m%dT%H%M%S%f")
    safe_attempt = re.sub(r"[^A-Za-z0-9_.-]", "-", attempt)
    safe_wave = re.sub(r"[^A-Za-z0-9_.-]", "-", wave)
    policy_path = data_dir / "policies" / "engagement-cycle.json"
    policy_sha = _file_sha256(policy_path) if policy_path.is_file() else _sha256_bytes(
        json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    from .effective_policy import EffectivePolicy
    git = git_snapshot(source_dir)
    source = source_fingerprint(source_dir)
    auth_files = []
    for path in sorted((data_dir / "policies").glob("owner-attested-*.json")):
        auth_files.append({"name": path.name, "sha256": _file_sha256(path)})
    if git["dirty"] is True:
        live_status = "unreleased_workspace"
    elif git["dirty"] is False:
        live_status = "committed_workspace"
    else:
        live_status = "git_state_unknown"
    manifest = {
        "schema_version": "growovo.run-manifest/1",
        "attempt_id": attempt,
        "created_at": current.isoformat(timespec="seconds"),
        "night": night, "wave": wave, "campaign": campaign,
        "bridge_session": session,
        "code": dict(git, source=source, live_status=live_status),
        "source_sha256": source["sha256"],
        "policy_sha256": policy_sha,
        "effective_policy_sha256": EffectivePolicy.compile(policy).sha256,
        "queue_sha256": _file_sha256(queue_path) if queue_path.is_file() else None,
        "authorization_files": auth_files,
        "preflight": preflight,
        "note": "指纹证明运行输入的一致性，不证明平台许可、外部交付或业务转化；未保存授权正文。",
    }
    directory = data_dir / "state" / ("engagement-%s" % night)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("manifest-%s-%s.json" % (safe_wave, safe_attempt))
    from .engagement_cycle import _atomic_write_text
    _atomic_write_text(path, json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True))
    manifest["manifest_path"] = str(path)
    return manifest


class SourceVersionChanged(RuntimeError):
    def __init__(self, expected, actual):
        super().__init__("source code changed between waves")
        self.expected = expected
        self.actual = actual


def assert_source_stable(data_dir, *, night, source_dir):
    from .storage import AtomicJSONStore, atomic_write_text
    data_dir = Path(data_dir)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(night or "")):
        raise ValueError("night 格式不正确")
    current = source_fingerprint(source_dir)["sha256"]
    path = data_dir / "state" / ("night-source-%s.json" % night)
    if path.is_file():
        expected = AtomicJSONStore(path).load().get("source_sha256")
        if expected != current:
            raise SourceVersionChanged(expected, current)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps({"night": night, "source_sha256": current},
                                          ensure_ascii=False, sort_keys=True))
    return current
