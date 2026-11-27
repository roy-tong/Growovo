from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path

from . import output_strategy
from .storage import atomic_write_text


def _now(value):
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("调度时间必须包含时区")
    return parsed


def _path(data_dir, strategy):
    campaign = str((strategy.get("campaign") or {}).get("id") or "campaign")
    digest = output_strategy.strategy_digest({key: value for key, value in strategy.items()
                                               if key != "strategy_sha256"})[:16]
    return Path(data_dir) / "state" / ("preparation-%s-%s.json" % (campaign, digest))


def _actual_digest(strategy):
    return output_strategy.strategy_digest({key: value for key, value in strategy.items()
                                             if key != "strategy_sha256"})


def _initial(strategy):
    tasks = {}
    for row in (strategy.get("preparation") or {}).get("tasks") or []:
        tasks[row["id"]] = dict(row, attempts=[])
    supply = {}
    for item in strategy.get("items") or []:
        if item.get("wave_id"):
            supply.setdefault(item["wave_id"], {}).setdefault("items", []).append(copy.deepcopy(item))
    return {
        "schema": "growovo.preparation-state/1",
        "strategy_sha256": _actual_digest(strategy),
        "tasks": tasks,
        "supply": supply,
        "waves": {row["id"]: {"status": "pending"} for row in strategy.get("waves") or []},
        "safety_stop": None,
    }


def load_state(data_dir, strategy):
    path = _path(data_dir, strategy)
    actual = _actual_digest(strategy)
    if not path.is_file():
        campaign = str((strategy.get("campaign") or {}).get("id") or "campaign")
        siblings = list(path.parent.glob("preparation-%s-*.json" % campaign)) if path.parent.is_dir() else []
        if siblings:
            raise ValueError("strategy_changed：备料状态不能接到另一个策略版本")
        return _initial(strategy)
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("strategy_sha256") != actual:
        raise ValueError("strategy_changed：备料状态不能接到另一个策略版本")
    for task in (state.get("tasks") or {}).values():
        if task.get("status") == "running":
            task["status"] = "ready"
    return state


def save_state(data_dir, strategy, state):
    path = _path(data_dir, strategy)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, sort_keys=True))
    return path


def _wave(strategy, wave_id):
    return next((row for row in strategy.get("waves") or [] if row["id"] == wave_id), None)


def _current_wave(strategy, state, now):
    candidates = []
    for wave in strategy.get("waves") or []:
        status = (state.get("waves") or {}).get(wave["id"], {}).get("status", "pending")
        if status not in {"reconciled", "completed"}:
            candidates.append(wave)
    if not candidates:
        return None
    due = [wave for wave in candidates if _now(wave["at"]) <= now]
    return due[0] if due else candidates[0]


def _eligible_items(strategy, state, wave_id):
    wave = _wave(strategy, wave_id)
    if not wave:
        return []
    items = ((state.get("supply") or {}).get(wave_id) or {}).get("items") or []
    return [copy.deepcopy(item) for item in items if not output_strategy._item_errors(item, strategy)]


def _shortfall(strategy, state, wave_id):
    wave = _wave(strategy, wave_id)
    if not wave:
        return {}
    items = _eligible_items(strategy, state, wave_id)
    counts = {}
    for item in items:
        key = (item["product"], "%s/%s" % (item.get("platform") or item.get("channel"), item["action"]))
        counts[key] = counts.get(key, 0) + 1
    gaps = {}
    for product, allocation in wave["allocations"].items():
        for action, required in allocation.items():
            missing = required - counts.get((product, action), 0)
            if missing > 0:
                gaps["%s/%s" % (product, action)] = missing
    return gaps


def _surplus(strategy, state, wave_id):
    wave = _wave(strategy, wave_id)
    if not wave:
        return {}
    counts = {}
    for item in _eligible_items(strategy, state, wave_id):
        key = (item["product"], "%s/%s" % (item.get("platform") or item.get("channel"), item["action"]))
        counts[key] = counts.get(key, 0) + 1
    surplus = {}
    for (product, action), actual in counts.items():
        required = (wave["allocations"].get(product) or {}).get(action, 0)
        if actual > required:
            surplus["%s/%s" % (product, action)] = actual - required
    return surplus


def _scope_errors(strategy, state):
    counts = {}
    active = set()
    for wave_id, bucket in (state.get("supply") or {}).items():
        wave_status = (state.get("waves") or {}).get(wave_id, {}).get("status", "pending")
        for item in bucket.get("items") or []:
            if output_strategy._item_errors(item, strategy) or item.get("action") not in {"reply", "message"}:
                continue
            target = item.get("target") or {}
            destination = (target.get("handle") if item.get("action") == "message"
                           else target.get("url") or target.get("handle"))
            identity = (item.get("platform") or item.get("channel"), item.get("action"),
                        str(destination or "").lstrip("@").casefold())
            counts[identity] = counts.get(identity, 0) + 1
            if wave_status not in {"sent", "waiting_verification", "reconciled", "completed"}:
                active.add(identity)
    return ["duplicate_target"] if any(counts[identity] > 1 for identity in active) else []


def _queue_row(item):
    row = copy.deepcopy(item)
    target = item.get("target") or {}
    content = item.get("content") or {}
    action = item.get("action")
    if action == "message":
        destination = target.get("handle") or target.get("url") or ""
    else:
        destination = target.get("url") or target.get("handle") or target.get("audience") or ""
    row["strategy_target"] = copy.deepcopy(target)
    row["target"] = str(destination)
    row["text"] = str(content.get("text") or "")
    row["actor"] = str(target.get("handle") or target.get("actor") or target.get("audience") or "")
    row["their_text"] = str(target.get("context") or "")
    row["source"] = "execution-strategy"
    row["queued_at"] = dt.datetime.now().astimezone().isoformat()
    return row


def _freeze(data_dir, strategy, state, wave_id):
    items = [_queue_row(item) for item in _eligible_items(strategy, state, wave_id)]
    campaign_id = str(strategy["campaign"]["id"])
    for row in items:
        row["strategy_item_id"] = row["id"]
        row["id"] = "%s:%s" % (campaign_id, row["id"])
        row["campaign_id"] = campaign_id
    snapshot = {
        "schema": "growovo.wave-supply-snapshot/1",
        "campaign_id": strategy["campaign"]["id"],
        "wave_id": wave_id,
        "strategy_sha256": _actual_digest(strategy),
        "items": items,
    }
    snapshot["sha256"] = output_strategy.strategy_digest(snapshot)
    path = Path(data_dir) / "state" / "strategy-waves" / strategy["campaign"]["id"] / (wave_id + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
    snapshot["path"] = str(path)
    state.setdefault("waves", {}).setdefault(wave_id, {})["snapshot"] = copy.deepcopy(snapshot)
    return snapshot


def _result(data_dir, strategy, state, wave, wave_status, executed=0):
    wave_id = wave["id"] if wave else ""
    shortfall = _shortfall(strategy, state, wave_id) if wave else {}
    snapshot = ((state.get("waves") or {}).get(wave_id) or {}).get("snapshot")
    return {
        "status": "ok",
        "wave_id": wave_id,
        "wave_status": wave_status,
        "shortfall": shortfall,
        "surplus": _surplus(strategy, state, wave_id) if wave else {},
        "scope_errors": _scope_errors(strategy, state) if wave else [],
        "snapshot": copy.deepcopy(snapshot),
        "executed": executed,
        "external_writes": 0,
        "state": state,
        "state_path": str(_path(data_dir, strategy)),
    }


def _ready_tasks(state, missing_keys):
    tasks = (state.get("tasks") or {}).values()
    completed = {task_id for task_id, row in (state.get("tasks") or {}).items()
                 if row.get("status") == "completed"}
    rows = []
    for task in tasks:
        if task.get("status") not in {"ready", "pending"}:
            continue
        if any(dependency not in completed for dependency in task.get("depends_on") or []):
            continue
        if missing_keys and not set(task.get("supply_keys") or []) & missing_keys:
            continue
        rows.append(task)
    rows.sort(key=lambda row: list((state.get("tasks") or {})).index(row["id"]))
    return rows


def _run_miner(data_dir, task, strategy):
    from . import supply_mine
    config = task.get("config") or {}
    sources = config.get("sources") or []
    reports = {}
    for source in sources:
        if source == "github":
            queries = supply_mine.gh_queries()[:max(1, int(config.get("max_queries") or 3))]
            reports[source] = supply_mine.mine_github(
                data_dir, queries=queries, per_query=max(1, int(config.get("per_query") or 10)))
        elif source == "x":
            reports[source] = supply_mine.mine_x(
                data_dir, limit=max(1, int(config.get("per_query") or 10)))
        elif source == "content":
            reports[source] = supply_mine.mine_content(data_dir, channels=("x",))
    if not reports:
        return {"ok": False, "waiting_input": True, "reason": "miner_source_not_configured"}
    return {"ok": all(report.get("ok") for report in reports.values()), "reports": reports, "items": []}


def _run_host(data_dir, task, strategy, state):
    from . import growth_host
    config = task.get("config") or {}
    adapter_value = config.get("adapter") or config.get("adapter_path")
    if not adapter_value:
        return {"ok": False, "waiting_input": True, "reason": "host_adapter_not_configured"}
    spec = growth_host.load_adapter(adapter_value)
    attempt_id = task.get("_attempt_id") or "attempt"
    root = Path(data_dir) / "state" / "preparation-work" / task["id"] / attempt_id
    root.mkdir(parents=True, exist_ok=True)
    result_path = root / "result.json"
    if result_path.is_file():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if (result.get("schema") != "growovo.preparation-result/1"
                or result.get("producer_id") != spec["producer_id"]):
            return {"ok": False, "reason": "host_result_invalid"}
        if not isinstance(result.get("items"), list):
            return {"ok": False, "reason": "host_items_invalid"}
        return dict(result, ok=True, recovered=True)
    request = {
        "schema": "growovo.preparation-request/1",
        "task": {key: value for key, value in task.items() if not key.startswith("_")},
        "strategy_sha256": strategy["strategy_sha256"],
        "attempt_id": attempt_id,
    }
    request_path = root / "request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    import subprocess
    completed = subprocess.run(
        growth_host._argv(spec, request_path, root, task["id"]), cwd=root,
        env=growth_host._environment(spec), text=True, capture_output=True,
        timeout=min(600, max(1, int(spec.get("timeout_seconds", 60)))))
    (root / "stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (root / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode:
        return {"ok": False, "reason": "host_exit_%s" % completed.returncode}
    result_path = root / "result.json"
    if not result_path.is_file():
        return {"ok": False, "reason": "host_result_missing"}
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if (result.get("schema") != "growovo.preparation-result/1"
            or result.get("producer_id") != spec["producer_id"]):
        return {"ok": False, "reason": "host_result_invalid"}
    if not isinstance(result.get("items"), list):
        return {"ok": False, "reason": "host_items_invalid"}
    return dict(result, ok=True)


def _default_runner(data_dir):
    def run(task, strategy, state):
        runner = task.get("runner")
        if runner == "manual":
            return {"ok": False, "waiting_input": True, "reason": "manual_input_required"}
        if runner == "local":
            return {"ok": True, "items": []}
        if runner == "miner":
            return _run_miner(data_dir, task, strategy)
        if runner == "host_adapter":
            return _run_host(data_dir, task, strategy, state)
        return {"ok": False, "waiting_input": True, "reason": "runner_not_configured"}
    return run


def advance(data_dir, strategy, now, runner=None, max_tasks=2):
    current = _now(now)
    state = load_state(data_dir, strategy)
    wave = _current_wave(strategy, state, current)
    if not wave:
        return _result(data_dir, strategy, state, None, "completed")
    if state.get("safety_stop"):
        return _result(data_dir, strategy, state, wave, "stopped")
    wave_id = wave["id"]
    wave_state = state.setdefault("waves", {}).setdefault(wave_id, {"status": "pending"})
    shortfall = _shortfall(strategy, state, wave_id)
    if _surplus(strategy, state, wave_id) or _scope_errors(strategy, state):
        wave_state["status"] = "supply_scope_invalid"
        save_state(data_dir, strategy, state)
        return _result(data_dir, strategy, state, wave, "supply_scope_invalid")
    if not shortfall and not wave_state.get("snapshot"):
        wave_state["snapshot"] = _freeze(data_dir, strategy, state, wave_id)
    if wave_state.get("status") in {"sent", "waiting_verification"}:
        save_state(data_dir, strategy, state)
        return _result(data_dir, strategy, state, wave, "waiting_reconciliation")
    due = _now(wave["at"]) <= current
    if not due and not shortfall:
        next_deficient = None
        for candidate in strategy.get("waves") or []:
            if candidate["id"] == wave_id:
                continue
            candidate_state = (state.get("waves") or {}).get(candidate["id"], {})
            if candidate_state.get("status") in {"reconciled", "completed", "sent", "waiting_verification"}:
                continue
            if _shortfall(strategy, state, candidate["id"]):
                next_deficient = candidate
                break
        if next_deficient is None:
            save_state(data_dir, strategy, state)
            return _result(data_dir, strategy, state, wave, "ready")
        wave = next_deficient
        wave_id = wave["id"]
        wave_state = state.setdefault("waves", {}).setdefault(wave_id, {"status": "pending"})
        shortfall = _shortfall(strategy, state, wave_id)
    if due and not shortfall:
        auto_approve = bool(strategy["campaign"].get("auto_approve_materialized_waves"))
        wave_state["status"] = "ready_to_send" if auto_approve else "waiting_wave_approval"
        for task in state["tasks"].values():
            if task.get("status") in {"ready", "pending"}:
                task["status"] = "paused_for_wave"
        save_state(data_dir, strategy, state)
        return _result(data_dir, strategy, state, wave, wave_state["status"])
    if wave_state.get("status") in {"ready_to_send", "waiting_wave_approval"}:
        save_state(data_dir, strategy, state)
        return _result(data_dir, strategy, state, wave, wave_state["status"])

    missing_actions = {key.split("/", 1)[1] for key in shortfall}
    tasks = _ready_tasks(state, missing_actions)
    execute = runner or _default_runner(data_dir)
    executed = 0
    for task in tasks:
        if executed >= max(1, int(max_tasks)):
            break
        attempt_id = "%s-%04d" % (task["id"], len(task.get("attempts") or []) + 1)
        task["_attempt_id"] = attempt_id
        task["status"] = "running"
        task.setdefault("attempts", []).append({"id": attempt_id, "status": "running", "at": current.isoformat()})
        save_state(data_dir, strategy, state)
        try:
            result = execute(task, strategy, state)
        except Exception as exc:
            result = {"ok": False, "reason": "runner_error: %s" % str(exc)[:200]}
        task.pop("_attempt_id", None)
        task["attempts"][-1].update(status="completed" if result.get("ok") else "failed",
                                    reason=str(result.get("reason") or ""))
        if result.get("waiting_input"):
            task["status"] = "waiting_input"
        elif not result.get("ok"):
            task["status"] = "blocked"
        else:
            task["status"] = "completed"
        for item in result.get("items") or []:
            target_wave = item.get("wave_id") or wave_id
            item = copy.deepcopy(item)
            item.setdefault("wave_id", target_wave)
            bucket = state.setdefault("supply", {}).setdefault(target_wave, {}).setdefault("items", [])
            if not any(existing.get("id") == item.get("id") for existing in bucket):
                bucket.append(item)
        executed += 1
        save_state(data_dir, strategy, state)
    save_state(data_dir, strategy, state)
    return _result(data_dir, strategy, state, wave, "preparing", executed)


def status(data_dir, strategy, wave_id=""):
    state = load_state(data_dir, strategy)
    wave = _wave(strategy, wave_id) if wave_id else next(iter(strategy.get("waves") or []), None)
    wave_id = wave["id"] if wave else ""
    eligible = len(_eligible_items(strategy, state, wave_id)) if wave else 0
    return {
        "status": "ok",
        "wave_id": wave_id,
        "wave_status": ((state.get("waves") or {}).get(wave_id) or {}).get("status", "pending"),
        "eligible": eligible,
        "shortfall": _shortfall(strategy, state, wave_id) if wave else {},
        "snapshot": copy.deepcopy(((state.get("waves") or {}).get(wave_id) or {}).get("snapshot")),
        "tasks": list((state.get("tasks") or {}).values()),
        "state_path": str(_path(data_dir, strategy)),
    }


def mark_wave_sent(data_dir, strategy, wave_id, *, unknown=False):
    state = load_state(data_dir, strategy)
    wave_state = (state.get("waves") or {}).get(wave_id)
    if not wave_state or not wave_state.get("snapshot"):
        raise ValueError("wave_snapshot_required")
    wave_state["status"] = "waiting_verification" if unknown else "sent"
    save_state(data_dir, strategy, state)
    return _result(data_dir, strategy, state, _wave(strategy, wave_id), wave_state["status"])


def mark_wave_reconciled(data_dir, strategy, wave_id):
    state = load_state(data_dir, strategy)
    wave_state = (state.get("waves") or {}).get(wave_id)
    if not wave_state or wave_state.get("status") not in {"sent", "waiting_verification"}:
        return {"status": "error", "reason": "wave_not_reconciled", "wave_id": wave_id}
    if wave_state.get("status") == "waiting_verification":
        return {"status": "error", "reason": "receipt_still_unknown", "wave_id": wave_id}
    wave_state["status"] = "reconciled"
    wave_state["reconciled_at"] = dt.datetime.now().astimezone().isoformat()
    for task in state.get("tasks", {}).values():
        if task.get("status") == "paused_for_wave":
            task["status"] = "pending" if task.get("depends_on") else "ready"
    save_state(data_dir, strategy, state)
    next_wave = _current_wave(strategy, state, dt.datetime.now().astimezone())
    return _result(data_dir, strategy, state, next_wave, "preparing")
