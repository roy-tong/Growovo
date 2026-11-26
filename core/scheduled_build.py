"""Content-addressed snapshot of the installed Python modules used by the wave worker."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def _files(root):
    # Match the wheel's executable modules and bundled worker data.
    files = [root / "mkt.py", root / "mcp_server.py"]
    for pattern in ("core/**/*.py", "channels/**/*.py", "core/*_data.json",
                    "core/work_order_demo.json", "channels/x/vendor/*.sh"):
        files.extend(root.glob(pattern))
    return sorted({path for path in files if path.is_file()})


def fingerprint(root):
    sha = hashlib.sha256()
    for path in _files(root):
        sha.update(path.relative_to(root).as_posix().encode() + b"\0")
        sha.update(hashlib.sha256(path.read_bytes()).digest())
    return sha.hexdigest()


def freeze(data):
    root = Path(__file__).resolve().parents[1]
    sha = fingerprint(root)
    parent = Path(data) / "state" / "scheduled-builds"
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / sha
    if not target.exists():
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            staged = Path(tmp) / "worker"
            for path in _files(root):
                dest = staged / path.relative_to(root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
                dest.chmod(path.stat().st_mode & 0o555)
            if fingerprint(staged) != sha:
                raise ValueError("source_changed_during_freeze")
            try:
                staged.rename(target)
            except OSError:
                if not target.is_dir():
                    raise
    verify(data, sha)
    return sha


def verify(data, sha):
    if not re.fullmatch(r"[a-f0-9]{64}", str(sha or "")):
        raise ValueError("invalid_scheduled_build")
    root = Path(data).resolve() / "state" / "scheduled-builds" / sha
    if not root.is_dir() or root.is_symlink() or fingerprint(root) != sha:
        raise ValueError("scheduled_build_changed")
    allowed = set(_files(root))
    if any(path.is_symlink() or (path.is_file() and path not in allowed)
           for path in root.rglob("*")):
        raise ValueError("unexpected_scheduled_build_file")
    return root


def tick(data, config, sha, args):
    root = verify(data, sha)
    if root == Path(__file__).resolve().parents[1]:
        return None
    # Isolated mode prevents the caller's cwd and PYTHONPATH from shadowing the worker.
    bootstrap = ("import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); "
                 "runpy.run_module('mkt',run_name='__main__')")
    action = "jobs-serve" if getattr(args, "action", "") == "jobs-serve" else "jobs-tick"
    command = [sys.executable, "-I", "-B", "-c", bootstrap, str(root),
               "--workspace", str(Path(data).resolve()), "cycle", action,
               "--file", str(Path(config).resolve()), "--max-jobs", str(args.max_jobs)]
    if action == "jobs-serve":
        command.extend(["--poll-sec", str(args.poll_sec)])
        if args.once:
            command.append("--once")
    if getattr(args, "job_id", ""):
        command.extend(["--job-id", args.job_id])
    if args.now:
        command.extend(["--now", args.now])
    return subprocess.run(command, check=False).returncode
