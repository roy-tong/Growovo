from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path

from . import execution_capabilities, growth_runtime
from .growth_goals import compile_goal
from .storage import AppendOnlyJSONL, AtomicJSONStore


STATUSES = {"pending", "in_progress", "waiting_manual", "waiting_verification", "completed", "blocked"}


def _now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _path(data_dir, service_id):
    if not service_id or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for ch in service_id):
        raise ValueError("service_id 格式不正确")
    return Path(data_dir) / "state" / "growth-services" / f"{service_id}.json"


def _events(data_dir):
    return AppendOnlyJSONL(Path(data_dir) / "state" / "growth-events.jsonl")


def _save(data_dir, service):
    snapshot = copy.deepcopy(service)

    def replace(current):
        current.clear()
        current.update(snapshot)
        return current

    return growth_runtime.update_service(data_dir, service["id"], replace)


def load(data_dir, service_id):
    _path(data_dir, service_id)
    return growth_runtime.load_service(data_dir, service_id)


def _metrics(service):
    actions = service["actions"]
    completed = [row for row in actions if row["status"] == "completed"]
    denominator = [row for row in actions if row["id"] != "next-experiment"]
    autonomous = [row for row in completed if row.get("human_level") in {"L0", "L1"}]
    minutes = int(service["metrics"].get("human_minutes_total") or 0)
    service["metrics"].update({
        "accepted_tasks": len(completed),
        "autonomous_completed": len(autonomous),
        "autonomous_delivery_rate": len(autonomous) / max(1, len(denominator)),
        "human_minutes_per_accepted": (minutes / len(completed)) if completed else None,
        "blocked_actions": [row["id"] for row in actions if row["status"] == "blocked"],
    })
    return service["metrics"]


def start(data_dir, goal):
    plan = compile_goal(goal) if goal.get("schema") == "growovo.growth-goal/1" else copy.deepcopy(goal)
    if plan.get("schema") != "growovo.business-action-plan/1":
        raise ValueError("start 需要 GrowthGoalContract 或 Business Action Plan")
    service_id = plan["service"]["id"]
    account = str(plan["goal"]["constraints"].get("account") or "default")
    has_capabilities = bool(execution_capabilities.view(data_dir, account=account))
    readiness = (execution_capabilities.plan_readiness(data_dir, plan, account=account)
                 if has_capabilities else {"mode": "legacy_explicit_availability", "actions": []})
    service = {
        "schema": "growovo.growth-service/1",
        "id": service_id,
        "status": "active",
        "account": account,
        "capability_readiness": readiness,
        "goal": plan["goal"],
        "service": copy.deepcopy(plan["service"]),
        "actions": copy.deepcopy(plan["actions"]),
        "deferred_actions": copy.deepcopy(plan.get("deferred_actions") or []),
        "feasibility": copy.deepcopy(plan.get("feasibility") or {}),
        "measurement": copy.deepcopy(plan.get("measurement") or {}),
        "metrics": {
            "human_minutes_total": 0,
            "human_interruptions": 0,
            "accepted_tasks": 0,
            "autonomous_completed": 0,
            "autonomous_delivery_rate": 0.0,
            "human_minutes_per_accepted": None,
            "blocked_actions": [],
        },
        "evaluations": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    created = growth_runtime.create_service(data_dir, service)
    if created.get("capability_readiness") != readiness:
        def refresh(current):
            current["capability_readiness"] = copy.deepcopy(readiness)
            current["account"] = account
            return current

        return growth_runtime.update_service(data_dir, service_id, refresh)
    return created


def next_action(data_dir, service_id):
    growth_runtime.recover_expired(data_dir)
    service = load(data_dir, service_id)
    actions = service["actions"]
    done = {row["id"] for row in actions if row["status"] == "completed"}
    ready = [row for row in actions if row["status"] == "pending"
             and all(dep in done for dep in row.get("depends_on") or [])]
    if not ready:
        return None
    return sorted(ready, key=lambda row: -row["priority"])[0]


def record_action(data_dir, service_id, action_id, status, by, note="", minutes=0, evidence=None):
    if status not in STATUSES:
        raise ValueError(f"status 必须是 {sorted(STATUSES)}")
    if not str(by or "").strip():
        raise ValueError("by 必填")
    if not isinstance(minutes, int) or minutes < 0:
        raise ValueError("minutes 必须是非负整数")
    def mutate(service):
        action = next((row for row in service["actions"] if row["id"] == action_id), None)
        if action is None:
            raise ValueError(f"服务 {service_id} 没有动作 {action_id}")
        action.update(status=status, note=str(note or ""), evidence=copy.deepcopy(evidence))
        service["metrics"]["human_minutes_total"] += minutes
        if action.get("human_level") in {"L2", "L3"}:
            service["metrics"]["human_interruptions"] += 1
        _metrics(service)
        if all(row["status"] == "completed" for row in service["actions"]):
            service["status"] = "completed"
        elif service["status"] == "completed":
            service["status"] = "active"
        return service

    event = {
        "at": _now(), "kind": "action_recorded", "service_id": service_id,
        "action_id": action_id, "status": status, "by": str(by), "note": str(note or ""),
        "minutes": minutes, "evidence": copy.deepcopy(evidence),
    }
    return growth_runtime.update_service(data_dir, service_id, mutate, event)


def observe(data_dir, service_id, phase, metrics, by, evidence=None):
    if phase not in {"baseline", "final"}:
        raise ValueError("phase 必须是 baseline/final")
    if not isinstance(metrics, dict):
        raise ValueError("metrics 必须是对象")
    if not str(by or "").strip():
        raise ValueError("by 必填")
    def mutate(service):
        service["measurement"][phase] = copy.deepcopy(metrics)
        for action in service["actions"]:
            if action["id"] == phase + "-observe":
                action["status"] = "completed"
                action["evidence"] = {"metrics": copy.deepcopy(metrics), "by": str(by),
                                      "evidence": copy.deepcopy(evidence)}
        _metrics(service)
        return service

    event = {"at": _now(), "kind": "measurement_observed",
             "service_id": service_id, "phase": phase, "by": str(by)}
    return growth_runtime.update_service(data_dir, service_id, mutate, event)


def _outcome_result(outcome, baseline, final):
    actual = final.get(outcome["metric"])
    result = {"id": outcome["id"], "metric": outcome["metric"],
              "baseline": baseline.get(outcome["metric"]), "final": actual,
              "delta": None, "target": outcome.get("target", outcome.get("target_delta"))}
    if outcome.get("target_delta") is not None:
        before = baseline.get(outcome["metric"])
        if isinstance(before, int) and isinstance(actual, int):
            result["delta"] = actual - before
            result["status"] = "achieved" if result["delta"] >= outcome["target_delta"] else "not_achieved"
        else:
            result["status"] = "unknown"
    elif actual is None:
        result["status"] = "unknown"
    else:
        result["status"] = "achieved" if actual >= outcome["target"] else "not_achieved"
    return result


def evaluate(data_dir, service_id, metrics, by):
    if not str(by or "").strip():
        raise ValueError("by 必填")
    if not isinstance(metrics, dict):
        raise ValueError("metrics 必须是对象")
    baseline = metrics.get("baseline") or {}
    final = metrics.get("final") or metrics
    if not isinstance(baseline, dict) or not isinstance(final, dict):
        raise ValueError("baseline/final 必须是对象")
    evaluation_id = "eval-" + hashlib.sha256(
        json.dumps({"service": service_id, "baseline": baseline, "final": final},
                   sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:12]
    created = []

    def mutate(service):
        existing = next((row for row in service["evaluations"] if row["id"] == evaluation_id), None)
        if existing:
            return existing
        outcomes = [_outcome_result(row, baseline, final) for row in service["goal"]["outcomes"]]
        primary = next(row for row in outcomes
                       if row["id"] == next(o["id"] for o in service["goal"]["outcomes"]
                                            if o["role"] == "primary"))
        primary_success = primary["status"] == "achieved"
        if primary["status"] == "unknown":
            recommendation = {"kind": "fix_measurement",
                              "action": "补齐主目标的同源 baseline/final；不要增加动作量"}
        elif not primary_success:
            recommendation = {"kind": "inspect_activation_path",
                              "action": "检查人群、资产和首次成功路径，不增加相似触达"}
        elif any(row["status"] == "not_achieved" for row in outcomes if row["id"] != primary["id"]):
            recommendation = {"kind": "review_secondary_assets_not_volume",
                              "action": "主目标已达成；复盘辅助目标的资产与受众，不拉票、不加量"}
        else:
            recommendation = {"kind": "repeat_small_canary",
                              "action": "按同一合同再复现一次，再决定是否放大"}
        evaluation = {
            "id": evaluation_id,
            "service_id": service_id,
            "outcomes": outcomes,
            "primary_success": primary_success,
            "recommendation": recommendation,
            "by": str(by),
            "at": _now(),
            "causality": "not_claimed",
        }
        service["evaluations"].append(evaluation)
        service["measurement"]["baseline"] = copy.deepcopy(baseline)
        service["measurement"]["final"] = copy.deepcopy(final)
        for action in service["actions"]:
            if action["id"] in {"final-observe", "goal-evaluate"}:
                action["status"] = "completed"
        _metrics(service)
        created.append(True)
        return evaluation

    def event(result):
        if not created:
            return None
        return {"at": result["at"], "kind": "goal_evaluated", "service_id": service_id,
                "evaluation_id": result["id"], "by": str(by)}

    return growth_runtime.update_service(data_dir, service_id, mutate, event)
