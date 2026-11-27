"""Persistent dependency-aware cycle jobs; schedulers wake ticks, never edit queues."""
from __future__ import annotations

import datetime as dt
import fcntl
import json
from pathlib import Path
from typing import Any, Dict
from .job_kernel import JobStateMachine, JobStore, TERMINAL, DEPENDENCY_OK
from .business_result import build_business_result
from . import scheduled_scope
from . import preparation_runtime
from . import schedule_recovery
from . import business_progress


def _wave_log(message):
    import sys
    print(message, file=sys.stderr)


def state_path(data_dir) -> Path:
    return Path(data_dir) / "state" / "cycle-jobs-state.json"


def _now(value=None):
    if isinstance(value, dt.datetime):
        return value
    if value:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.datetime.now().astimezone()


def _select_job(rows, job_id):
    if not job_id:
        return rows
    selected = [row for row in rows if row.get("id") == job_id]
    if not selected:
        raise ValueError("作业配置中不存在 job_id：%s" % job_id)
    return selected


def _load_config(path) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("作业配置必须包含非空 jobs 数组")
    ids = [str(row.get("id") or "") for row in rows if isinstance(row, dict)]
    if len(ids) != len(rows) or any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("每个作业必须有唯一 id")
    hourly = payload.get("cadence") == "hourly"
    base = _now(payload["start_at"]) if hourly and payload.get("start_at") else None
    for row in rows:
        if payload.get("strategy") and row.get("kind") == "wave":
            row.setdefault("strategy", payload["strategy"])
            if payload.get("strategy_sha256"):
                row.setdefault("strategy_sha256", payload["strategy_sha256"])
        if hourly and row.get("kind") == "wave":
            slot = row.get("slot")
            if type(slot) is not int or slot < 0:
                raise ValueError("hourly wave 需要非负整数 slot")
            if base is None and row.get("at"):
                base = _now(row["at"]) - dt.timedelta(hours=slot)
            if base is None or base.tzinfo is None:
                raise ValueError("hourly 模式需要含时区的 start_at 或 at")
            expected = base + dt.timedelta(hours=slot)
            when = _now(row["at"]) if row.get("at") else expected
            if when != expected:
                raise ValueError("hourly 波次的 at 必须与 slot 的小时间隔一致")
            row.setdefault("at", when.isoformat())
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
            try:
                with scheduled_scope.activate(data_dir, config_path, job) as (queue, capacity):
                    result = cycle.run(data_dir, go=go, queue_path=queue,
                                     wave_id=str(job.get("wave") or job["id"]),
                                     budget_sec=int(job.get("budget_sec", 3000)),
                                     session=str(job.get("session") or ""),
                                     auto_inbox=False, prefetch=False, log=_wave_log)
                    return dict(result, accepted=sum(capacity["eligible"].values()),
                                approved_targets=capacity["targets"])
            except scheduled_scope.ScopeError as exc:
                return {"ok": False, "reason": str(exc)}
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
                    account=str(job.get("account") or "default"),
                    host_adapter=job.get("host_adapter"),
                    execute_accepted_by=job.get("execute_accepted_by"))
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


def _dependencies_ok(job):
    """依赖只要求上游「跑完并给出结算」（succeeded / completed_with_errors）。

    这里一度收紧成波次必须 succeeded：一条 429 就让第 1 波变成
    completed_with_errors，后面所有波次整夜 blocked——一次限流废掉整晚。
    未知结果（needs_reconciliation）仍然拦住下游，因为那需要人工先裁决。
    """
    return DEPENDENCY_OK


def _record_status(job, result):
    targets = ((job.get("ovo") or {}).get("business_targets") or result.get("approved_targets")
               or job.get("targets") or {})
    if job.get("kind") == "wave" and targets:
        card = build_business_result(job, result)
        result = dict(result, target=sum(int(value) for value in targets.values()),
                      target_shortfall=bool(card["execution"]["gap"]))

    return JobStateMachine.transition(str(job.get("kind") or ""), result)


def _view(data_dir, config, now):
    state = _load_state(data_dir)
    current = _now(now)
    rows = []
    for job in config["jobs"]:
        stored = state["jobs"].get(job["id"]) or {}
        status = stored.get("status") or "pending"
        if stored.get("intent") and stored["intent"] != scheduled_scope.digest(scheduled_scope.job_spec(job)):
            raise ValueError("已登记作业内容发生变化，请用新的作业 ID 并引用 retry_of：%s" % job["id"])
        when = dt.datetime.fromisoformat(str(job["at"]).replace("Z", "+00:00"))
        if status not in TERMINAL and current < when:
            status = "pending"
        row = dict(job, status=status, state=stored)
        if stored.get("business_result"):
            row["business_result"] = stored["business_result"]
        rows.append(row)
    return state, rows


def status(data_dir, config_path, *, now=None) -> Dict[str, Any]:
    config = _load_config(config_path)
    _, rows = _view(data_dir, config, now)
    counts = {}
    output = []
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        projected = {key: value for key, value in row.items() if key != "state"}
        stored = row.get("state") or {}
        approval_valid = (stored.get("reason") == "wave_approval_required"
                          and scheduled_scope.has_approval(data_dir, config_path, row))
        projected["recovery"] = schedule_recovery.decide(
            row, stored, approval_valid=approval_valid)
        output.append(projected)
    due = [row for row in output if row["kind"] == "wave" and _now(row["at"]) <= _now(now)]
    terminal = [row for row in due if row["status"] in TERMINAL]
    settled = [row for row in terminal if row.get("business_result")]
    cards = [row["business_result"]["execution"] for row in settled]
    # 旧构建写下的终态没有结算卡；不能因此把它算成「还在等」，那会让整晚报告永远失败。
    settlement = {"due": len(due), "terminal": len(terminal), "settled": len(settled),
                  "unmeasured": [row["id"] for row in terminal if not row.get("business_result")],
                  "waiting": [row["id"] for row in due if row["status"] not in TERMINAL],
                  "target": sum(sum(((row.get("ovo") or {}).get("business_targets") or
                                     row.get("targets") or {}).values()) or
                                ((row.get("business_result") or {}).get("execution") or {}).get("target", 0)
                                for row in due),
                  "committed": sum(card["committed"] for card in cards),
                  "indeterminate": sum(card["indeterminate"] for card in cards)}
    return {"ok": not any(counts.get(key) for key in ("failed", "needs_reconciliation", "expired", "completed_with_errors")), "counts": counts, "settlement": settlement,
            "notifications": _notifications(data_dir),
            "jobs": output, "state_path": str(state_path(data_dir))}


def _notifications(data_dir):
    """未确认投递的作业变化条数；outbox 不可读时如实说不可读，不让报表跟着崩。"""
    from . import notification_outbox
    try:
        return {"pending": len(notification_outbox.pending(data_dir))}
    except (OSError, ValueError) as exc:
        return {"pending": None, "error": str(exc)[:200]}


def _preparation_summary(preparation):
    if not preparation:
        return None
    keys = ("status", "wave_id", "wave_status", "shortfall", "surplus", "scope_errors", "executed",
            "external_writes", "state_path", "eligible", "reason")
    summary = {key: preparation[key] for key in keys if key in preparation}
    snapshot = preparation.get("snapshot")
    if snapshot:
        summary["snapshot"] = {key: snapshot[key] for key in ("sha256", "path") if key in snapshot}
        summary["snapshot"]["item_count"] = len(snapshot.get("items") or [])
    if "tasks" in preparation:
        summary["tasks"] = [{
            "id": task.get("id"), "status": task.get("status"), "runner": task.get("runner"),
            "depends_on": task.get("depends_on") or [], "supply_keys": task.get("supply_keys") or [],
            "attempt_count": len(task.get("attempts") or []),
        } for task in preparation["tasks"]]
    return summary


def _advance_preparation(data_dir, config_path, config, current):
    if not config.get("strategy"):
        return None
    settings = config.get("preparation") or {}
    if not settings.get("enabled"):
        return None
    path = Path(config_path).parent / config["strategy"]
    strategy = json.loads(path.read_text(encoding="utf-8"))
    summary = _preparation_summary(preparation_runtime.advance(
        data_dir, strategy, current,
        max_tasks=max(1, int(settings.get("max_tasks_per_tick") or 2))))
    wave_id = str(summary.get("wave_id") or "")
    job = next((row for row in config["jobs"] if row.get("kind") == "wave"
                and str(row.get("wave") or row.get("id") or "") == wave_id), None)
    if job:
        summary["job_id"] = str(job["id"])
    return summary


def _record_business_progress(data_dir, event, events, errors, now):
    try:
        row = business_progress.record(data_dir, event, now=now)
        if row.get("recorded"):
            events.append(row)
    except (OSError, RuntimeError, ValueError) as exc:
        errors.append(str(exc)[:300])


def tick(data_dir, config_path, *, now=None, go: bool = False, executor=None, max_jobs: int = 1,
         job_id: str = ""):
    config = _load_config(config_path)
    current = _now(now)
    if go:
        issue = scheduled_scope.schedule_issue(data_dir, config_path, config)
        if issue:
            return {"ok": False, "reason": issue, "executed": 0, "jobs": []}
    if not go:
        preparation = None
        if config.get("strategy") and (config.get("preparation") or {}).get("enabled"):
            strategy = json.loads((Path(config_path).parent / config["strategy"]).read_text(encoding="utf-8"))
            preparation = _preparation_summary(preparation_runtime.status(data_dir, strategy))
        _, rows = _view(data_dir, config, current)
        all_rows = rows
        rows = _select_job(rows, job_id)
        output = []
        for row in rows:
            state = row["status"]
            if state not in TERMINAL and current >= _now(row["at"]):
                dependencies = {other["id"]: other["status"] for other in all_rows}
                if any(dependencies.get(dep) not in _dependencies_ok(row) for dep in row.get("requires") or []):
                    state = "blocked"
                else:
                    state = "dry-run"
            output.append({key: value for key, value in dict(row, status=state).items() if key != "state"})
        return {"ok": True, "apply": False, "jobs": output, "executed": 0,
                "preparation": preparation}

    lock_file = Path(data_dir) / "state" / "cycle-scheduler.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_file.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return {"ok": False, "reason": "scheduler_already_running", "jobs": [], "executed": 0}
    try:
        progress_events = []
        progress_errors = []
        preparation = _advance_preparation(data_dir, config_path, config, current)
        if preparation:
            _record_business_progress(
                data_dir, business_progress.from_preparation(preparation),
                progress_events, progress_errors, current.isoformat())
        state, rows = _view(data_dir, config, current)
        all_rows = rows
        rows = _select_job(rows, job_id)
        execute = executor or _default_executor(config_path, data_dir)
        executed = 0
        output = []
        changes = []
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
            if ttl is not None and (current - when).total_seconds() >= max(0, int(ttl)):
                current_status = "expired"
                result = {"ok": False, "reason": "job_expired"}
            else:
                dependencies = {other["id"]: other["status"] for other in all_rows}
                missing = [dep for dep in row.get("requires") or []
                           if dependencies.get(dep) not in _dependencies_ok(row)]
                preparation_status = str((preparation or {}).get("wave_status") or "")
                supply_blocked = (row.get("kind") == "wave" and config.get("strategy")
                                  and preparation_status in {"preparing", "supply_scope_invalid"})
                approval_blocked = (row.get("kind") == "wave" and config.get("strategy")
                                    and preparation_status == "waiting_wave_approval"
                                    and not scheduled_scope.has_approval(data_dir, config_path, row))
                if supply_blocked or approval_blocked:
                    current_status = "blocked"
                    result = {"ok": False, "reason": (
                        preparation_status if preparation_status == "supply_scope_invalid"
                        else "supply_not_ready" if supply_blocked else "wave_approval_required")}
                elif missing or executed >= max(1, int(max_jobs)):
                    current_status = "blocked" if missing else "deferred"
                    result = {"ok": False, "reason": "dependency_not_succeeded" if missing else "tick_job_limit"}
                else:
                    from . import growth_runtime
                    ticket = growth_runtime.claim_scheduled_job(
                        data_dir, row, external=row.get("kind") in {"wave", "asset-prepare"})
                    if ticket["execute"]:
                        try:
                            result = execute(row, True)
                        except Exception as exc:
                            result = {"ok": False, "reason": "executor_error", "error": str(exc)}
                            if row.get("kind") == "wave":
                                result["reason"] = "dispatch_state_unknown"
                                result["results"] = [{"result": "indeterminate"}]
                        executed += 1
                    else:
                        result = ticket["result"]
                    if row.get("kind") == "wave" and config.get("strategy") and ticket["execute"]:
                        strategy = json.loads((Path(config_path).parent / config["strategy"]).read_text(encoding="utf-8"))
                        unknown = bool(result.get("indeterminate")) or any(
                            isinstance(item, dict) and item.get("result") == "indeterminate"
                            for item in result.get("results") or [])
                        if result.get("ok") or unknown:
                            preparation_runtime.mark_wave_sent(
                                data_dir, strategy, str(row.get("wave") or row["id"]), unknown=unknown)
                            if not unknown:
                                preparation_runtime.mark_wave_reconciled(
                                    data_dir, strategy, str(row.get("wave") or row["id"]))
                    current_status, reason = _record_status(row, result)
                    result = dict(result, job_status=current_status,
                                  exit_code=0 if current_status in {"succeeded", "deferred", "blocked"} else 2)
                    business_result = build_business_result(row, result, data_dir)
                    result = dict(result, reason=reason, business_result=business_result)
                    if ticket["execute"] and current_status not in {"deferred", "blocked"}:
                        growth_runtime.finish_scheduled_job(data_dir, row, current_status, result)
                    elif ticket["execute"]:
                        growth_runtime.release_scheduled_job(data_dir, row)
                    row["business_result"] = business_result
            if "business_result" not in result:
                result = dict(result, job_status=current_status,
                              exit_code=0 if current_status in {"succeeded", "deferred", "blocked"} else 2)
                result["business_result"] = build_business_result(row, result, data_dir)
                row["business_result"] = result["business_result"]
            stored = dict(state["jobs"].get(row["id"]) or {})
            stored.update(intent=scheduled_scope.digest(scheduled_scope.job_spec(row)), status=current_status, updated_at=current.isoformat(timespec="seconds"),
                          reason=str(result.get("reason") or ""),
                          business_result=result["business_result"])
            if current_status not in {"blocked", "deferred"} or row["status"] != current_status:
                change = {"id": row["id"], "status": current_status,
                          "event_id": "%s@%s" % (row["id"], current.isoformat(timespec="seconds")),
                          "previous_status": row["status"],
                          "reason": stored["reason"], "next_action": result["business_result"]["next_action"]}
                from . import notification_outbox
                notification_outbox.record(data_dir, change)
                changes.append(change)
                stored.setdefault("history", []).append({
                    "at": current.isoformat(timespec="seconds"), "status": current_status,
                    "reason": str(result.get("reason") or ""),
                    "summary_path": str(result.get("summary_path") or ""),
                    "business_result": result["business_result"],
                })
            state["jobs"][row["id"]] = stored
            row["status"] = current_status
            _save_state(data_dir, state)
            if row.get("kind") == "wave":
                approval_valid = (stored.get("reason") == "wave_approval_required"
                                  and scheduled_scope.has_approval(data_dir, config_path, row))
                recovery = schedule_recovery.decide(row, stored, approval_valid=approval_valid)
                _record_business_progress(
                    data_dir, business_progress.from_job(row, result["business_result"], recovery),
                    progress_events, progress_errors, current.isoformat())
            row["state"] = stored
            output.append({key: value for key, value in row.items() if key != "state"})
        return {"ok": all(row["status"] not in {
                    "failed", "needs_reconciliation", "expired", "completed_with_errors"
                } for row in output),
                "apply": True, "jobs": output, "executed": executed, "changes": changes,
                "preparation": preparation,
                "business_progress": progress_events,
                "business_progress_path": str(Path(data_dir) / business_progress.FILENAME),
                "business_progress_errors": progress_errors,
                "state_path": str(state_path(data_dir))}
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def serve(data_dir, config_path, *, go=True, once=False, poll_sec=5.0, max_jobs=1,
          executor=None, clock=None, sleeper=None):
    """Run due jobs without a chat session; external schedulers may still invoke --once."""
    import time
    started = time.monotonic()
    progress_events = []
    progress_errors = []

    def finish(payload):
        return dict(payload, business_progress=progress_events,
                    business_progress_path=str(Path(data_dir) / business_progress.FILENAME),
                    business_progress_errors=progress_errors,
                    operations={
                        "host_agent": "not_used", "scheduler_token_usage": 0,
                        "other_token_usage": "not_measured",
                        "elapsed_sec": round(time.monotonic() - started, 3)})

    clock = clock or (lambda: dt.datetime.now().astimezone())
    sleeper = sleeper or time.sleep
    poll_sec = float(poll_sec)
    if poll_sec <= 0:
        raise ValueError("poll_sec 必须大于 0")
    if not go:
        return finish(tick(data_dir, config_path, now=clock(), go=False, executor=executor,
                           max_jobs=max_jobs))
    changes = []
    executed = 0
    while True:
        current = clock()
        got = tick(data_dir, config_path, now=current, go=True, executor=executor,
                   max_jobs=max_jobs)
        changes.extend(got.get("changes") or [])
        progress_events.extend(got.get("business_progress") or [])
        progress_errors.extend(got.get("business_progress_errors") or [])
        executed += int(got.get("executed") or 0)
        if once:
            view = status(data_dir, config_path, now=current)
            finished = all(row["status"] in TERMINAL for row in view["jobs"])
            return finish(dict(got, reason="schedule_finished" if finished else "tick_finished",
                               jobs=view["jobs"] if finished else got["jobs"], changes=changes,
                               executed=executed))
        if got.get("reason") == "scheduler_already_running":
            return finish(got)
        settings = _load_config(config_path)
        if str(settings.get("plan_status", "")).startswith("STOPPED"):
            return finish(dict(got, ok=False, reason="schedule_stopped", changes=changes,
                               executed=executed))
        view = status(data_dir, config_path, now=current)
        rows = view["jobs"]
        if all(row["status"] in TERMINAL for row in rows):
            return finish(dict(got, ok=view["ok"], reason="schedule_finished", jobs=rows,
                               changes=changes, executed=executed))
        blocked = [row for row in rows
                   if row["status"] == "blocked" and _now(row["at"]) <= current]
        preparation_progress = int(((got.get("preparation") or {}).get("executed")) or 0)
        if blocked and not got.get("executed") and not preparation_progress:
            return finish(dict(got, ok=False, reason="no_runnable_jobs", jobs=rows,
                               changes=changes, executed=executed))
        sleeper(poll_sec)
