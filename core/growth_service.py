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

# R3-G-01：渠道动作的 completed 必须能区分「已验证完成」与「人工声明完成」。
COMPLETION_KINDS = {"verified", "attested_manual"}
REQUIRED_RECEIPT_FIELDS = ("action_id", "channel", "sent_at", "idempotency_key", "readback")
SATISFIED_STATES = {"verified", "attested_manual", "internal"}


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
    return _annotate_completions(growth_runtime.load_service(data_dir, service_id))


def _annotate_completions(service):
    """给旧记录补完成类型视图；不静默改状态，明确标 legacy。

    - 渠道动作有 evidence：按人工声明完成（不满足「已验证」，但可解除内部依赖）
    - 渠道动作无 evidence：按 declared，不满足任何依赖
    - 非渠道动作：内部完成，直接满足依赖
    """
    for action in service.get("actions") or []:
        if action.get("status") != "completed" or action.get("completion"):
            continue
        if action.get("kind") == "channel":
            action["completion"] = {
                "kind": "attested_manual" if action.get("evidence") else "declared",
                "verified": False,
                "legacy": True,
                "reason": "legacy completion recorded before R3-G-01",
                "at": action.get("updated_at") or service.get("updated_at") or "",
                "by": "",
            }
        else:
            action["completion"] = {"kind": "internal", "verified": True, "legacy": True}
    return service


def _dependency_state(action):
    if action.get("status") != "completed":
        return "pending"
    if action.get("kind") != "channel":
        return "internal"
    return str((action.get("completion") or {}).get("kind") or "declared")


def _completion_block(action, completion, *, by, note):
    if action.get("kind") != "channel":
        return {"kind": "internal", "verified": True, "at": _now(), "by": str(by)}
    payload = completion if isinstance(completion, dict) else {}
    kind = str(payload.get("kind") or "").strip()
    if kind not in COMPLETION_KINDS:
        raise ValueError(
            "渠道动作标记 completed 需要 completion.kind = verified 或 attested_manual；"
            "没有回执时用 attested_manual 并说明原因")
    if kind == "verified":
        receipt = payload.get("receipt")
        if not isinstance(receipt, dict):
            raise ValueError("verified 完成需要 receipt 对象")
        missing = [field for field in REQUIRED_RECEIPT_FIELDS
                   if not str(receipt.get(field) or "").strip()]
        if missing:
            raise ValueError("verified 回执缺少字段: " + ", ".join(missing))
        if str(receipt.get("action_id")) != str(action.get("id")):
            raise ValueError("verified 回执的 action_id 与动作不一致")
        if str(receipt.get("channel")).strip().lower() != str(action.get("channel") or "").strip().lower():
            raise ValueError("verified 回执的 channel 与动作不一致")
        return {"kind": "verified", "verified": True,
                "receipt": copy.deepcopy(receipt), "at": _now(), "by": str(by)}
    reason = str(payload.get("reason") or note or "").strip()
    if not reason:
        raise ValueError("attested_manual 完成需要说明人工完成原因")
    return {"kind": "attested_manual", "verified": False, "reason": reason,
            "at": _now(), "by": str(by)}


def _metrics(service):
    actions = service["actions"]
    completed = [row for row in actions if row["status"] == "completed"]
    denominator = [row for row in actions if row["id"] != "next-experiment"]
    autonomous = [row for row in completed if row.get("human_level") in {"L0", "L1"}]
    minutes = int(service["metrics"].get("human_minutes_total") or 0)
    verified = [row for row in completed if _dependency_state(row) == "verified"]
    attested = [row for row in completed if _dependency_state(row) == "attested_manual"]
    declared = [row for row in completed if _dependency_state(row) == "declared"]
    service["metrics"].update({
        "accepted_tasks": len(completed),
        "completed_verified": len(verified),
        "completed_attested": len(attested),
        "completed_declared": len(declared),
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
    by_id = {row["id"]: row for row in actions}
    ready = []
    for row in actions:
        if row["status"] != "pending":
            continue
        deps = [by_id.get(dep) or {} for dep in row.get("depends_on") or []]
        if all(_dependency_state(dep) in SATISFIED_STATES for dep in deps):
            if any(_dependency_state(dep) == "attested_manual" for dep in deps):
                row = dict(row, dependency_quality="attested")
            ready.append(row)
    if not ready:
        return None
    return sorted(ready, key=lambda row: -row["priority"])[0]


def record_action(data_dir, service_id, action_id, status, by, note="", minutes=0,
                  evidence=None, completion=None):
    if status not in STATUSES:
        raise ValueError(f"status 必须是 {sorted(STATUSES)}")
    if not str(by or "").strip():
        raise ValueError("by 必填")
    if not isinstance(minutes, int) or minutes < 0:
        raise ValueError("minutes 必须是非负整数")
    if status != "completed" and completion is not None:
        raise ValueError("completion 只在 status=completed 时使用")

    def mutate(service):
        _annotate_completions(service)
        action = next((row for row in service["actions"] if row["id"] == action_id), None)
        if action is None:
            raise ValueError(f"服务 {service_id} 没有动作 {action_id}")
        block = _completion_block(action, completion, by=by, note=note) if status == "completed" else None
        action.update(status=status, note=str(note or ""), evidence=copy.deepcopy(evidence))
        if block is not None:
            action["completion"] = block
        elif action.get("completion") and status != "completed":
            action.pop("completion", None)
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
        "completion": copy.deepcopy(completion),
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


MEASUREMENT_SOURCES = {"owner_declared": 0, "observed": 1, "independently_verified": 2}


def _measurement(entry):
    """拆出数值与来源。裸数字按 owner_declared 处理，不冒充观测。"""
    if isinstance(entry, dict) and "value" not in entry:
        raise ValueError("metric 取值必须是数字，或带 value/source 的对象")
    if isinstance(entry, dict):
        source = str(entry.get("source") or "owner_declared").strip().lower()
        if source not in MEASUREMENT_SOURCES:
            raise ValueError(f"metric source 必须是 {sorted(MEASUREMENT_SOURCES)}")
        return entry.get("value"), {
            "source": source,
            "window": str(entry.get("window") or ""),
            "definition_hash": str(entry.get("definition_hash") or ""),
            "collected_by": str(entry.get("collected_by") or ""),
            "evidence_refs": list(entry.get("evidence_refs") or []),
        }
    return entry, {"source": "owner_declared", "window": "", "definition_hash": "",
                   "collected_by": "", "evidence_refs": []}


def _same_basis(baseline_prov, final_prov):
    """同源同口径：来源不能是自报，两侧都要有窗口，指标定义必须一致。

    baseline 与 final 处于不同时间段是正常的，因此不要求窗口字符串相等。
    """
    if baseline_prov["source"] == "owner_declared" or final_prov["source"] == "owner_declared":
        return False
    if not baseline_prov["window"] or not final_prov["window"]:
        return False
    if not baseline_prov["definition_hash"] or not final_prov["definition_hash"]:
        return False
    return baseline_prov["definition_hash"] == final_prov["definition_hash"]


def _weaker_source(baseline_prov, final_prov):
    return min((baseline_prov["source"], final_prov["source"]),
               key=lambda name: MEASUREMENT_SOURCES[name])


def _outcome_result(outcome, baseline, final):
    actual, final_prov = _measurement(final.get(outcome["metric"]))
    before, baseline_prov = _measurement(baseline.get(outcome["metric"]))
    result = {"id": outcome["id"], "metric": outcome["metric"],
              "baseline": before, "final": actual,
              "delta": None, "target": outcome.get("target", outcome.get("target_delta")),
              "baseline_source": baseline_prov["source"], "final_source": final_prov["source"]}
    if outcome.get("target_delta") is not None:
        if isinstance(before, int) and isinstance(actual, int):
            result["delta"] = actual - before
            result["status"] = "achieved" if result["delta"] >= outcome["target_delta"] else "not_achieved"
        else:
            result["status"] = "unknown"
    elif actual is None:
        result["status"] = "unknown"
    else:
        result["status"] = "achieved" if actual >= outcome["target"] else "not_achieved"
    same_basis = _same_basis(baseline_prov, final_prov)
    grade = _weaker_source(baseline_prov, final_prov) if same_basis else "owner_declared"
    result.update({
        "same_basis": same_basis,
        "verification_grade": grade,
        "verified": result["status"] == "achieved" and same_basis and grade != "owner_declared",
    })
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
        target_met = primary["status"] == "achieved"
        verified_success = bool(primary.get("verified"))
        if primary["status"] == "unknown":
            recommendation = {"kind": "fix_measurement",
                              "action": "补齐主目标的同源 baseline/final；不要增加动作量"}
        elif not target_met:
            recommendation = {"kind": "inspect_activation_path",
                              "action": "检查人群、资产和首次成功路径，不增加相似触达"}
        elif not verified_success:
            recommendation = {"kind": "verify_measurement",
                              "action": "数字达标但来源不自证：补齐同源证据、窗口与口径后再决定是否复现"}
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
            "primary_success": target_met,
            "primary_verified": verified_success,
            "verification": {
                "grade": primary.get("verification_grade") or "owner_declared",
                "same_basis": bool(primary.get("same_basis")),
                "baseline_source": primary.get("baseline_source") or "owner_declared",
                "final_source": primary.get("final_source") or "owner_declared",
            },
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
                action["completion"] = {"kind": "internal", "verified": True,
                                        "at": _now(), "by": str(by)}
        _metrics(service)
        created.append(True)
        return evaluation

    def event(result):
        if not created:
            return None
        return {"at": result["at"], "kind": "goal_evaluated", "service_id": service_id,
                "evaluation_id": result["id"], "by": str(by)}

    return growth_runtime.update_service(data_dir, service_id, mutate, event)
