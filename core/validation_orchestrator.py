"""Read-only orchestration for real-user validation tasks and their evidence."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List

from .operations_log import OperationsLog


PLAN_SCHEMA = "growovo.validation-plan/1"
TASK_SCHEMA = "growovo.validation-task/1"
PRODUCTS = {"askovo", "growovo", "verovo"}
STATES = ("needs_input", "draft", "eligible", "approved", "dry_run", "sent",
          "replied", "material", "second_batch", "accepted")


def _load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("%s must contain a JSON object" % path)
    return value


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _time_semantics(task: Dict[str, Any], operations: List[Dict[str, Any]]) -> Dict[str, Any]:
    uncertain = set()

    def walk(value, path=""):
        if isinstance(value, dict):
            for key, item in value.items():
                child = "%s.%s" % (path, key) if path else key
                if key in {"started_at", "finished_at", "observed_at", "verified_at"} \
                        and isinstance(item, str) and "T" in item:
                    try:
                        parsed = dt.datetime.fromisoformat(item.replace("Z", "+00:00"))
                    except ValueError:
                        parsed = None
                    if parsed is not None and parsed.tzinfo is None:
                        uncertain.add(child)
                walk(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, "%s[%d]" % (path, index))

    walk(task, "task")
    walk(operations, "operations")
    return {"status": "UNCERTAIN" if uncertain else "EXPLICIT",
            "timezone_assumed": "UTC" if uncertain else None,
            "paths": sorted(uncertain)}


def _operations(data_dir: Path, run_id: str) -> List[Dict[str, Any]]:
    return [row for row in OperationsLog(data_dir).all()
            if run_id and run_id in {row.get("run_id"), row.get("campaign")}]


def _approved(data_dir: Path, plan_ids: List[str]) -> bool:
    if not plan_ids:
        return False
    for plan_id in plan_ids:
        path = data_dir / "plans" / (plan_id + ".json")
        if not path.is_file():
            return False
        review = _load_json(path).get("review") or {}
        if not review.get("human_approved") or not review.get("approved_at"):
            return False
    return True


def _has_material(data_dir: Path) -> bool:
    rows = _read_jsonl(data_dir / "state" / "contributions.jsonl")
    return any(row.get("change_ref") and row.get("status") in {
        "patch_proposed", "reviewed", "merged", "credited", "followed_up",
    } for row in rows)


def _has_replies(data_dir: Path) -> bool:
    directory = data_dir / "review-feedback"
    return directory.is_dir() and any(directory.glob("*.json"))


def _next_action(status: str, missing: List[str], go: bool) -> Dict[str, Any]:
    if status == "needs_input":
        action = "supply_real_inputs"
    elif status == "draft":
        action = "create_reviewed_schedule"
    elif status == "eligible" and go:
        action = "obtain_named_plan_approval"
    elif status in {"eligible", "approved"}:
        action = "complete_dry_run"
    elif status == "dry_run":
        action = "complete_supervised_first_execution_after_approval"
    elif status == "sent":
        action = "collect_external_replies"
    elif status == "replied":
        action = "record_feedback_contribution_with_change_ref"
    elif status == "material":
        action = "run_second_independent_batch_with_proof"
    elif status == "second_batch":
        action = "record_disposition_and_all_metrics"
    else:
        action = "none"
    return {"action": action, "missing_inputs": missing,
            "automatic_external_write": False}


def _project_task(data_dir: Path, task: Dict[str, Any]) -> Dict[str, Any]:
    task_id = str(task.get("task_id") or "unknown")
    inputs = task.get("inputs") if isinstance(task.get("inputs"), dict) else {}
    execution = task.get("execution") if isinstance(task.get("execution"), dict) else {}
    metrics = task.get("metrics") if isinstance(task.get("metrics"), dict) else {}
    required = inputs.get("required") if isinstance(inputs.get("required"), list) else []
    missing = [str(item) for item in required if str(item).strip()]
    for field in ("owner", "goal"):
        if not str(task.get(field) or "").strip():
            missing.append(field)
    if task.get("product") not in PRODUCTS:
        missing.append("supported_product")
    if missing:
        status = "needs_input"
    else:
        schedule_ids = inputs.get("schedule_ids") if isinstance(inputs.get("schedule_ids"), list) else []
        schedules = []
        for schedule_id in schedule_ids:
            path = data_dir / "schedules" / (str(schedule_id) + ".json")
            if path.is_file():
                schedules.append(_load_json(path))
        if not schedules:
            status = "draft"
        else:
            run_id = str(inputs.get("run_id") or "")
            operations = _operations(data_dir, run_id)
            committed = any(row.get("result") == "committed" for row in operations)
            go = bool(execution.get("go") or any(row.get("go") for row in schedules))
            approved = _approved(data_dir, inputs.get("plan_ids") or [])
            if committed:
                status = "sent"
            elif go and not approved:
                status = "eligible"
            elif approved and not operations:
                status = "approved"
            else:
                status = "dry_run"
            if committed and _has_replies(data_dir):
                status = "replied"
            if committed and _has_replies(data_dir) and _has_material(data_dir):
                status = "material"
            second_operations = _operations(data_dir, str(inputs.get("second_run_id") or ""))
            second_proof = bool(inputs.get("second_batch_proof"))
            second_batch = second_proof or any(
                row.get("result") == "committed" for row in second_operations)
            if status == "material" and second_batch:
                status = "second_batch"
            complete_metrics = (metrics.get("first_value_seconds") is not None
                                and metrics.get("human_minutes") is not None
                                and metrics.get("major_errors") == 0
                                and bool(str(metrics.get("disposition") or "").strip()))
            time_state = _time_semantics(task, operations + second_operations)
            if status == "second_batch" and complete_metrics \
                    and time_state["status"] == "EXPLICIT":
                status = "accepted"
    result = dict(task)
    result["status"] = status
    result["next_required_action"] = _next_action(status, missing, bool(execution.get("go")))
    result["evidence"] = {
        "operations": len(_operations(data_dir, str(inputs.get("run_id") or ""))),
        "second_batch_operations": len(_operations(data_dir, str(inputs.get("second_run_id") or ""))),
        "has_replies": _has_replies(data_dir),
        "has_material": _has_material(data_dir),
        "approved": _approved(data_dir, inputs.get("plan_ids") or []),
    }
    return result


def run(config_path: str | Path) -> Dict[str, Any]:
    config_path = Path(config_path)
    config = _load_json(config_path)
    errors = []
    if config.get("schema") != PLAN_SCHEMA:
        errors.append({"code": "SCHEMA", "path": "schema",
                       "message": "must be %s" % PLAN_SCHEMA})
    raw_tasks = config.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        errors.append({"code": "TASKS_REQUIRED", "path": "tasks",
                       "message": "at least one validation task is required"})
        raw_tasks = []
    data_dir = config_path.parent / str(config.get("workspace") or "data")
    tasks = []
    for index, raw in enumerate(raw_tasks):
        try:
            task = _load_json(config_path.parent / raw) if isinstance(raw, str) else raw
            if not isinstance(task, dict) or task.get("schema") != TASK_SCHEMA:
                raise ValueError("task schema must be %s" % TASK_SCHEMA)
            tasks.append(_project_task(data_dir, task))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append({"code": "INVALID_TASK", "path": "tasks[%d]" % index,
                           "message": str(exc)})
    valid = [row for row in tasks if row["status"] != "needs_input"]
    accepted = [row for row in valid if row["status"] == "accepted"]
    business_accepted = bool(valid) and len(accepted) == len(valid) and not errors
    human_values = [row.get("metrics", {}).get("human_minutes") for row in valid]
    first_values = {row["task_id"]: row.get("metrics", {}).get("first_value_seconds")
                    for row in valid}
    return {
        "schema": "growovo.validation-report/1",
        "ok": business_accepted,
        "read_only": True,
        "external_action": "none",
        "contract_validation": {"status": "FAIL" if errors else "PASS", "errors": errors},
        "business_verdict": {"status": "ACCEPTED" if business_accepted else "NOT_ACCEPTED"},
        "time_semantics": _time_semantics(
            raw_tasks, [row for task in tasks
                        for row in _operations(data_dir, str(
                            (task.get("inputs") or {}).get("run_id") or ""))]),
        "tasks": tasks,
        "metrics": {
            "effective_autonomous_delivery_rate": (
                len(accepted) / len(valid) if valid else None),
            "first_value_seconds": first_values,
            "human_minutes_total": (sum(human_values)
                                    if human_values and all(value is not None for value in human_values)
                                    else None),
            "major_error_rate": (
                sum(1 for row in valid if row.get("metrics", {}).get("major_errors"))
                / len(valid) if valid else None),
        },
        "next_actions": [row["next_required_action"] for row in tasks
                         if row["status"] != "accepted"],
    }
