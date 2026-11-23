"""Persistent dependency-aware cycle jobs; schedulers wake ticks, never edit queues."""
from __future__ import annotations

import datetime as dt
import fcntl
import json
from pathlib import Path
from typing import Any, Dict
from .job_kernel import JobStateMachine, JobStore, TERMINAL, DEPENDENCY_OK


def state_path(data_dir) -> Path:
    return Path(data_dir) / "state" / "cycle-jobs-state.json"


def _now(value=None):
    if isinstance(value, dt.datetime):
        return value
    if value:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.datetime.now().astimezone()


def _load_config(path) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("作业配置必须包含非空 jobs 数组")
    ids = [str(row.get("id") or "") for row in rows if isinstance(row, dict)]
    if len(ids) != len(rows) or any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("每个作业必须有唯一 id")
    for row in rows:
        when = dt.datetime.fromisoformat(str(row.get("at") or "").replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError("作业 at 必须包含时区：%s" % row["id"])
    return payload


def _load_state(data_dir) -> Dict[str, Any]:
    payload = JobStore(state_path(data_dir)).load()
    if not isinstance(payload.get("jobs"), dict):
        raise ValueError("作业状态文件格式错误")
    return payload


def _save_state(data_dir, state):
    JobStore(state_path(data_dir)).save(state)


def _default_executor(config_path, data_dir):
    config_path = Path(config_path)
    data_dir = Path(data_dir)

    def execute(job, go):
        from . import engagement_cycle as cycle
        kind = job.get("kind")
        if kind == "wave":
            return cycle.run(data_dir, go=go,
                             wave_id=str(job.get("wave") or job["id"]),
                             budget_sec=int(job.get("budget_sec", 3000)),
                             session=str(job.get("session") or ""))
        if kind == "inbox-update":
            data_dir = Path(job.get("data_dir") or data_dir)
            file = Path(job["file"])
            if not file.is_absolute():
                file = config_path.parent / file
            payload = json.loads(file.read_text(encoding="utf-8"))
            return cycle.update_inbox(data_dir, payload, by=str(job.get("by") or ""))
        if kind == "inbox-collect":
            data_dir = Path(job.get("data_dir") or data_dir)
            rows = cycle.collect_inbox(data_dir, session=str(job.get("session") or ""))
            return cycle.merge_inbox(data_dir, rows)
        if kind == "receipts":
            from . import receipt_audit
            data_dir = Path(job.get("data_dir") or data_dir)
            return receipt_audit.audit(data_dir, night=str(job["night"]),
                                       scheduled_only=bool(job.get("scheduled_only", False)),
                                       live=bool(job.get("live", False)))
        if kind == "readiness":
            from . import supply_readiness
            data_dir = Path(job.get("data_dir") or data_dir)
            return supply_readiness.assess(data_dir, cycle.load_policy(data_dir),
                                           waves=int(job.get("waves", 3)))
        raise ValueError("未知作业类型：%s" % kind)
    return execute


def _record_status(job, result):
    return JobStateMachine.transition(str(job.get("kind") or ""), result)


def _view(data_dir, config, now):
    state = _load_state(data_dir)
    current = _now(now)
    rows = []
    for job in config["jobs"]:
        stored = state["jobs"].get(job["id"]) or {}
        status = stored.get("status") or "pending"
        when = dt.datetime.fromisoformat(str(job["at"]).replace("Z", "+00:00"))
        if status not in TERMINAL and current < when:
            status = "pending"
        rows.append(dict(job, status=status, state=stored))
    return state, rows


def status(data_dir, config_path, *, now=None) -> Dict[str, Any]:
    config = _load_config(config_path)
    _, rows = _view(data_dir, config, now)
    counts = {}
    output = []
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        output.append({key: value for key, value in row.items() if key != "state"})
    return {"ok": not counts.get("needs_reconciliation"), "counts": counts,
            "jobs": output, "state_path": str(state_path(data_dir))}


def tick(data_dir, config_path, *, now=None, go: bool = False, executor=None, max_jobs: int = 1):
    config = _load_config(config_path)
    current = _now(now)
    if not go:
        _, rows = _view(data_dir, config, current)
        output = []
        for row in rows:
            state = row["status"]
            if state not in TERMINAL:
                dependencies = {other["id"]: other["status"] for other in rows}
                if any(dependencies.get(dep) not in DEPENDENCY_OK for dep in row.get("requires") or []):
                    state = "blocked"
                else:
                    state = "dry-run"
            output.append({key: value for key, value in dict(row, status=state).items() if key != "state"})
        return {"ok": True, "apply": False, "jobs": output, "executed": 0}

    lock_file = Path(data_dir) / "state" / "cycle-scheduler.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_file.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return {"ok": False, "reason": "scheduler_already_running", "jobs": [], "executed": 0}
    try:
        state, rows = _view(data_dir, config, current)
        execute = executor or _default_executor(config_path, data_dir)
        executed = 0
        output = []
        for row in rows:
            current_status = row["status"]
            if current_status in TERMINAL:
                output.append({key: value for key, value in row.items() if key != "state"})
                continue
            when = dt.datetime.fromisoformat(str(row["at"]).replace("Z", "+00:00"))
            if current < when:
                output.append({key: value for key, value in row.items() if key != "state"})
                continue
            ttl = row.get("expire_after_sec")
            if ttl is not None and (current - when).total_seconds() > max(0, int(ttl)):
                current_status = "expired"
                result = {"ok": False, "reason": "job_expired"}
            else:
                dependencies = {other["id"]: other["status"] for other in rows}
                missing = [dep for dep in row.get("requires") or []
                           if dependencies.get(dep) not in DEPENDENCY_OK]
                if missing or executed >= max(1, int(max_jobs)):
                    current_status = "blocked" if missing else "deferred"
                    result = {"ok": False, "reason": "dependency_not_succeeded" if missing else "tick_job_limit"}
                else:
                    result = execute(row, True)
                    executed += 1
                    current_status, reason = _record_status(row, result)
                    result = dict(result, reason=reason)
            stored = dict(state["jobs"].get(row["id"]) or {})
            stored.update(status=current_status, updated_at=current.isoformat(timespec="seconds"),
                          reason=str(result.get("reason") or ""))
            if current_status not in {"blocked", "deferred"} or row["status"] != current_status:
                stored.setdefault("history", []).append({
                    "at": current.isoformat(timespec="seconds"), "status": current_status,
                    "reason": str(result.get("reason") or ""),
                    "summary_path": str(result.get("summary_path") or ""),
                })
            state["jobs"][row["id"]] = stored
            row["status"] = current_status
            row["state"] = stored
            output.append({key: value for key, value in row.items() if key != "state"})
        _save_state(data_dir, state)
        return {"ok": all(row["status"] not in {"failed", "needs_reconciliation"} for row in output),
                "apply": True, "jobs": output, "executed": executed,
                "state_path": str(state_path(data_dir))}
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
