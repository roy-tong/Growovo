from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re


SCHEMA = "growovo.growth-goal/1"
INTENTS = {"preview", "live", "live_with_manual_fallback"}
CHANNEL_ACTIONS = {
    "x": ("x-publish", "publish", 15, "x_followers"),
    "github": ("github-engage", "reply", 15, "github_stars"),
    "email": ("email-followup", "message", 15, "activated_users"),
}


def _instant(value, label):
    text = str(value or "").strip()
    try:
        stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{label} 必须是 ISO 8601 时间")
    if stamp.tzinfo is None:
        raise ValueError(f"{label} 必须包含时区")
    return stamp


def validate_goal(payload):
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ValueError(f"目标合同 schema 必须是 {SCHEMA}")
    product = payload.get("product")
    if not isinstance(product, dict) or not str(product.get("name") or "").strip():
        raise ValueError("product.name 必填")
    raw_window = payload.get("window") or {}
    start = _instant(raw_window.get("start"), "window.start")
    end = _instant(raw_window.get("end"), "window.end")
    if start >= end:
        raise ValueError("window.start 必须早于 window.end")
    outcomes = payload.get("outcomes")
    if not isinstance(outcomes, list) or not outcomes:
        raise ValueError("outcomes 至少包含一个目标")
    ids = []
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            raise ValueError("outcome 必须是对象")
        oid = str(outcome.get("id") or "").strip()
        role = str(outcome.get("role") or "").strip()
        if not oid or role not in {"primary", "secondary"}:
            raise ValueError("每个 outcome 需要 id 和 primary/secondary role")
        if not str(outcome.get("metric") or "").strip():
            raise ValueError(f"outcome {oid} 缺少 metric")
        if outcome.get("target") is None and outcome.get("target_delta") is None:
            raise ValueError(f"outcome {oid} 需要 target 或 target_delta")
        ids.append(oid)
    if len(set(ids)) != len(ids):
        raise ValueError("outcome id 不能重复")
    if ids.count("activation") > 1:
        raise ValueError("activation id 冲突")
    if sum(1 for row in outcomes if row.get("role") == "primary") != 1:
        raise ValueError("必须恰好一个 primary outcome")
    intent = payload.get("execution_intent") or "live"
    if intent not in INTENTS:
        raise ValueError(f"execution_intent 必须是 {sorted(INTENTS)}")
    mode = payload.get("mode") or "operator"
    if mode not in {"operator", "autopilot", "advisor"}:
        raise ValueError("mode 必须是 operator/autopilot/advisor")
    constraints = payload.get("constraints") or {}
    minutes = constraints.get("human_minutes", 0)
    if not isinstance(minutes, int) or minutes < 0:
        raise ValueError("constraints.human_minutes 必须是非负整数")
    channels = constraints.get("channels") or []
    if not isinstance(channels, list) or any(ch not in CHANNEL_ACTIONS for ch in channels):
        raise ValueError(f"channels 仅支持 {sorted(CHANNEL_ACTIONS)}")
    assets = payload.get("assets") or []
    if not isinstance(assets, list):
        raise ValueError("assets 必须是数组")
    got = copy.deepcopy(payload)
    got["mode"] = mode
    got["execution_intent"] = intent
    got["on_channel_blocked"] = payload.get("on_channel_blocked") or "manual_handoff"
    got["constraints"] = dict(constraints, human_minutes=minutes, channels=list(channels))
    got["window"] = dict(got["window"], duration_seconds=int((end - start).total_seconds()))
    return got


def _service_id(goal):
    name = re.sub(r"[^a-z0-9]+", "-", goal["product"]["name"].lower()).strip("-") or "product"
    raw = json.dumps(goal, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"svc-{name}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:10]}"


def _action(action_id, kind, service_id, goal, goal_ids, *, channel="", action="",
            depends_on=None, human_level="L0", human_minutes=0, priority=50):
    primary = next(row for row in goal["outcomes"] if row["role"] == "primary")
    assets = goal.get("assets") or []
    asset = assets[0] if assets else {}
    return {
        "id": action_id,
        "kind": kind,
        "goal_ids": goal_ids,
        "hypothesis_id": "h-" + primary["id"],
        "segment": copy.deepcopy(goal.get("audience") or {}),
        "asset_ref": str(asset.get("id") or "generated-value-asset"),
        "asset_sha256": str(asset.get("sha256") or ""),
        "cta": str(asset.get("cta") or primary.get("cta") or "complete_first_value"),
        "channel": channel,
        "action": action or kind,
        "conversion_event": {
            "baseline-observe": "baseline_recorded",
            "diagnose-product": "product_diagnosed",
            "asset-prepare": "asset_reproduced",
            "final-observe": "final_recorded",
            "goal-evaluate": "goal_evaluated",
            "next-experiment": "next_experiment_ready",
        }.get(action_id, primary["metric"] + "_observed"),
        "measurement_ref": service_id + "/outcomes",
        "depends_on": list(depends_on or []),
        "human_level": human_level,
        "human_minutes": int(human_minutes),
        "priority": int(priority),
        "status": "pending",
        "execution": {
            "intent": goal["execution_intent"],
            "on_channel_blocked": goal["on_channel_blocked"],
            "preferred_transports": {
                "x": ["official_api", "cdp", "manual_handoff"],
                "github": ["github_app", "api", "cdp", "manual_handoff"],
                "email": ["gmail_api", "smtp", "manual_handoff"],
                "": ["local"],
            }[channel],
        },
    }


def compile_goal(payload):
    goal = validate_goal(payload)
    service_id = _service_id(goal)
    outcome_ids = [row["id"] for row in goal["outcomes"]]
    primary = next(row for row in goal["outcomes"] if row["role"] == "primary")
    actions = [
        _action("diagnose-product", "diagnose", service_id, goal, outcome_ids,
                depends_on=[], priority=90),
        _action("baseline-observe", "observe", service_id, goal, outcome_ids,
                depends_on=["diagnose-product"], priority=85),
        _action("asset-prepare", "asset", service_id, goal, outcome_ids,
                depends_on=["diagnose-product"], priority=80),
    ]
    deferred = []
    budget = goal["constraints"]["human_minutes"]
    used = 0
    channel_ids = []
    for channel in goal["constraints"]["channels"]:
        action_id, verb, minutes, metric = CHANNEL_ACTIONS[channel]
        supported = [row["id"] for row in goal["outcomes"]
                     if row["role"] == "primary" or row["metric"] == metric]
        row = _action(action_id, "channel", service_id, goal, supported,
                      channel=channel, action=verb,
                      depends_on=["baseline-observe", "asset-prepare"],
                      human_level="L1", human_minutes=minutes, priority=60)
        if goal["mode"] == "advisor":
            deferred.append(dict(row, status="deferred", reason="advisor_mode"))
            continue
        if used + minutes > budget:
            deferred.append(dict(row, status="deferred", reason="human_budget_exceeded"))
            continue
        used += minutes
        channel_ids.append(action_id)
        actions.append(row)
    actions.extend([
        _action("final-observe", "observe", service_id, goal, outcome_ids,
                depends_on=channel_ids, priority=45),
        _action("goal-evaluate", "evaluate", service_id, goal, outcome_ids,
                depends_on=["final-observe"], priority=40),
        _action("next-experiment", "experiment", service_id, goal, [primary["id"]],
                depends_on=["goal-evaluate"], priority=35),
    ])
    return {
        "schema": "growovo.business-action-plan/1",
        "service": {
            "id": service_id,
            "product": copy.deepcopy(goal["product"]),
            "window": copy.deepcopy(goal["window"]),
            "outcomes": copy.deepcopy(goal["outcomes"]),
            "mode": goal["mode"],
            "status": "active",
        },
        "goal": goal,
        "actions": actions,
        "deferred_actions": deferred,
        "feasibility": {
            "blocked": bool(deferred) and goal["mode"] != "advisor",
            "human_minutes_budget": budget,
            "human_minutes_allocated": used,
            "reason": "human_budget_exceeded" if deferred else "",
        },
        "measurement": {
            "ref": service_id + "/outcomes",
            "baseline": None,
            "final": None,
            "missing": "UNKNOWN",
            "causality": "not_claimed",
        },
    }
