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

    def execute(job, go, data_dir=data_dir):
        from . import engagement_cycle as cycle
        data_dir = Path(data_dir)
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
        if kind in {"goal-compile", "baseline-observe", "final-observe", "goal-evaluate",
                    "next-experiment", "asset-prepare", "opportunity-scan", "inbox-resolve", "github-sync",
                    "operator-tick"}:
            def input_file(name="file"):
                file = Path(job[name])
                return file if file.is_absolute() else config_path.parent / file
            service_id = str(job.get("service_id") or "")
            operator = str(job.get("by") or "cycle-scheduler")
            if kind == "operator-tick":
                from . import growth_worker
                result = growth_worker.run_once(
                    data_dir, service_id, github_repo=job.get("repo"),
                    gmail_sync=bool(job.get("gmail_sync")), watch_topic=job.get("watch_topic"),
                    pubsub_subscription=job.get("pubsub_subscription"),
                    account=str(job.get("account") or "default"))
                result["ok"] = all(row.get("ok") for row in result["components"].values())
                return result
            if kind == "goal-compile":
                from . import growth_goals, growth_service
                payload = json.loads(input_file().read_text(encoding="utf-8"))
                if job.get("start_service", True):
                    return dict(growth_service.start(data_dir, payload), ok=True)
                return dict(growth_goals.compile_goal(payload), ok=True)
            if kind in {"baseline-observe", "final-observe"}:
                from . import growth_service
                phase = "baseline" if kind == "baseline-observe" else "final"
                if job.get("repos") or job.get("x_handle"):
                    from . import growth_metrics
                    repos = job.get("repos") or []
                    if isinstance(repos, str):
                        repos = [row.strip() for row in repos.split(",") if row.strip()]
                    return growth_metrics.observe_live(
                        data_dir, service_id, phase, repos,
                        str(job.get("x_handle") or ""), by=operator)
                metrics = json.loads(input_file().read_text(encoding="utf-8"))
                return dict(growth_service.observe(
                    data_dir, service_id, phase, metrics, by=operator), ok=True)
            if kind == "goal-evaluate":
                from . import growth_service
                metrics = json.loads(input_file().read_text(encoding="utf-8"))
                return dict(growth_service.evaluate(
                    data_dir, service_id, metrics, by=operator), ok=True)
            if kind == "next-experiment":
                from . import growth_execution
                result = growth_execution.run(
                    data_dir, service_id, "next-experiment", by=operator)
                return dict(result, ok=result.get("status") == "completed")
            if kind == "asset-prepare":
                from . import growth_assets
                spec = json.loads(input_file().read_text(encoding="utf-8"))
                asset = growth_assets.create(data_dir, service_id, spec)
                return dict(asset, ok=bool(asset.get("reproduced")))
            if kind == "github-sync":
                from . import github_app, growth_engagement
                rows = github_app.GitHubAppClient.from_env().sync_community_interactions(str(job["repo"]))
                result = growth_engagement.import_interactions(data_dir, service_id, rows)
                from . import github_events
                result["replayed"] = github_events.replay(data_dir, service_id)["processed"]
                return dict(result, ok=True)
            if kind == "opportunity-scan":
                from . import growth_opportunities
                rows = json.loads(input_file().read_text(encoding="utf-8"))
                return dict(growth_opportunities.import_opportunities(
                    data_dir, service_id, rows), ok=True)
            from . import growth_engagement
            rows = json.loads(input_file().read_text(encoding="utf-8"))
            return dict(growth_engagement.import_interactions(
                data_dir, service_id, rows), ok=True)
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
                    from . import growth_runtime
                    ticket = growth_runtime.claim_scheduled_job(
                        data_dir, row, external=row.get("kind") in {"wave", "asset-prepare"})
                    if ticket["execute"]:
                        result = execute(row, True)
                        executed += 1
                    else:
                        result = ticket["result"]
                    current_status, reason = _record_status(row, result)
                    result = dict(result, reason=reason)
                    if ticket["execute"] and current_status not in {"deferred", "blocked"}:
                        growth_runtime.finish_scheduled_job(data_dir, row, current_status, result)
                    elif ticket["execute"]:
                        growth_runtime.release_scheduled_job(data_dir, row)
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
