from __future__ import annotations

from . import action_resolution, growth_engagement, growth_experiments, growth_opportunities, growth_service


def _candidates(data_dir, service_id, channels):
    rows = []
    for source in (growth_engagement, growth_opportunities, growth_service):
        row = source.next_action(data_dir, service_id)
        if row:
            rows.append(row)
    result = []
    for row in rows:
        candidate = dict(row)
        if candidate.get("kind") == "channel":
            resolution = action_resolution.resolve(candidate, {"channels": channels})
            if resolution["execution_mode"] == "manual_handoff":
                candidate["human_level"] = "L2"
                candidate["manual_minutes"] = int(candidate.get("human_minutes") or 15)
            else:
                candidate["manual_minutes"] = 0
        else:
            candidate["manual_minutes"] = int(candidate.get("human_minutes") or 0)
        result.append(candidate)
    return sorted(result, key=lambda row: -row["priority"])


def allocate(data_dir, service_id, human_minutes=0, channels=None):
    if not isinstance(human_minutes, int) or human_minutes < 0:
        raise ValueError("human_minutes 必须是非负整数")
    selected = []
    deferred = []
    used = 0
    for row in _candidates(data_dir, service_id, channels or {}):
        cost = int(row.pop("manual_minutes", 0))
        if row.get("human_level") == "L3" or used + cost > human_minutes:
            deferred.append(dict(row, reason="human_decision_required" if row.get("human_level") == "L3"
                                 else "human_budget_exceeded"))
            continue
        used += cost
        selected.append(row)
    return {
        "service_id": service_id,
        "selected": selected,
        "deferred": deferred,
        "manual_minutes_allocated": used,
        "human_minutes_budget": human_minutes,
    }


def advise(data_dir, service_id):
    experiments = growth_experiments.list_experiments(data_dir, service_id)
    if experiments:
        latest = experiments[-1]
        result = growth_experiments.diagnose(data_dir, service_id, latest["id"])
    else:
        result = {"kind": "no_data", "next_action": "先完成一个带测量合同的实验"}
    return dict(result, requires_human=True,
                auto_scope_change=False,
                note="只建议下一实验；渠道、账号、配额、抑制名单和授权变化由人决定")


def compare_baseline(manual, assisted):
    for payload in (manual, assisted):
        for key in ("definition", "human_minutes", "accepted_tasks"):
            if key not in payload:
                raise ValueError(f"baseline.{key} 必填")
    if manual["definition"] != assisted["definition"]:
        raise ValueError("两组 definition 必须一致")
    before = float(manual["human_minutes"])
    after = float(assisted["human_minutes"])
    reduction = ((before - after) / before * 100) if before > 0 else None
    manual_quality = manual.get("quality_score")
    assisted_quality = assisted.get("quality_score")
    return {
        "definition": manual["definition"],
        "time_reduction_percent": round(reduction, 2) if reduction is not None else None,
        "manual": manual,
        "assisted": assisted,
        "quality_not_below": (assisted_quality >= manual_quality
                              if manual_quality is not None and assisted_quality is not None else None),
        "accepted_task_delta": assisted["accepted_tasks"] - manual["accepted_tasks"],
        "major_errors": assisted.get("major_errors", 0),
        "causality": "not_claimed",
    }


def brief(data_dir, service_id):
    service = growth_service.load(data_dir, service_id)
    next_rows = [growth_engagement.next_action(data_dir, service_id),
                 growth_opportunities.next_action(data_dir, service_id),
                 growth_service.next_action(data_dir, service_id)]
    next_rows = [row for row in next_rows if row]
    return {
        "service_id": service_id,
        "status": service["status"],
        "metrics": service["metrics"],
        "next_action": max(next_rows, key=lambda row: row["priority"]) if next_rows else None,
        "latest_evaluation": service["evaluations"][-1] if service["evaluations"] else None,
        "human_interruption_cost": {
            "minutes": service["metrics"]["human_minutes_total"],
            "count": service["metrics"]["human_interruptions"],
            "minutes_per_accepted": service["metrics"]["human_minutes_per_accepted"],
        },
    }
