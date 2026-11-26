"""Read-only periodic business projection over growth cycles and lifecycle evidence."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import growth_cycle, growth_lifecycle, growth_service
from .storage import AtomicJSONStore

SCHEMA = "growovo.growth-period-report/1"
CYCLE_STATES = ("open", "proposed", "executed", "waiting_verification",
                "waiting_manual", "preview", "blocked", "expired")
EXECUTION_STATES = ("executed", "completed", "waiting_verification", "waiting_manual",
                    "manual_handoff", "preview", "in_progress", "pending",
                    "refused", "blocked", "mapping_required")


def _instant(value, label):
    try:
        parsed = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("%s 必须是 ISO 8601 时间" % label) from exc
    if parsed.tzinfo is None:
        raise ValueError("%s 必须包含时区" % label)
    return parsed


def _load_cycles(data_dir, service_id):
    folder = Path(data_dir) / "state" / "growth-cycles" / service_id
    if not folder.is_dir():
        return []
    rows = []
    for file_path in sorted(folder.glob("*.json")):
        cycle = AtomicJSONStore(file_path).load()
        if cycle.get("schema") != growth_cycle.CYCLE_SCHEMA:
            raise ValueError("cycle schema 不正确：%s" % file_path)
        if cycle.get("service_id") != service_id:
            raise ValueError("cycle service_id 不匹配：%s" % file_path)
        _instant(cycle.get("created_at"), "cycle.created_at")
        rows.append(cycle)
    return rows


def _decisions(cycles, lifecycle):
    rows = []
    for cycle in cycles:
        status = cycle.get("status")
        if status == "open":
            rows.append({"kind": "open_cycle", "cycle_id": cycle["cycle_id"],
                         "reason": "host proposal required", "question": cycle.get("question") or ""})
        elif status == "proposed":
            refused = [row for row in cycle.get("decisions") or [] if not row.get("allowed")]
            rows.append({
                "kind": "proposal_review", "cycle_id": cycle["cycle_id"],
                "reason": "accepted proposals await named execution" if not refused else "refused proposals need review",
                "refusal_reasons": sorted({str(row.get("reason") or "unknown") for row in refused}),
            })
        elif status == "blocked":
            rows.append({
                "kind": "blocked_execution", "cycle_id": cycle["cycle_id"],
                "reason": "execution did not complete; inspect receipts before retrying",
                "execution_states": sorted({str(row.get("execution") or "unknown")
                                            for row in cycle.get("executions") or []}),
            })
    queue = lifecycle.get("followup_queue") or []
    if queue:
        rows.append({
            "kind": "followup_review", "cycle_id": "", "reason": "relationship follow-ups require review",
            "count": len(queue), "refs": [row.get("ref") for row in queue[:20]],
            "truncated": len(queue) > 20,
        })
    return sorted(rows, key=lambda row: (row["kind"], row.get("cycle_id") or ""))


def build(data_dir, service_id, *, start, end, identity_map=None):
    growth_service.load(data_dir, service_id)
    start_at = _instant(start, "start")
    end_at = _instant(end, "end")
    if end_at <= start_at:
        raise ValueError("end 必须晚于 start")
    loaded = _load_cycles(data_dir, service_id)
    cycles = [row for row in loaded if start_at <= _instant(row["created_at"], "created_at") < end_at]
    cycles.sort(key=lambda row: (row["created_at"], row["cycle_id"]))
    cycle_counts = {state: 0 for state in CYCLE_STATES}
    cycle_counts["other"] = 0
    for cycle in cycles:
        state = str(cycle.get("status") or "")
        cycle_counts[state if state in CYCLE_STATES else "other"] += 1

    execution_counts = {state: 0 for state in EXECUTION_STATES}
    execution_counts["other"] = 0
    result_statuses = {}
    undated_executions = 0
    for cycle in loaded:
        executions = cycle.get("executions") or []
        executed_at = cycle.get("executed_at")
        if not executed_at:
            undated_executions += len(executions)
            continue
        if not start_at <= _instant(executed_at, "executed_at") < end_at:
            continue
        for execution in executions:
            state = str(execution.get("execution") or "")
            execution_counts[state if state in EXECUTION_STATES else "other"] += 1
            result = execution.get("result")
            if isinstance(result, dict):
                result_status = str(result.get("status") or "unknown")
                result_statuses[result_status] = result_statuses.get(result_status, 0) + 1
    execution_counts["executed"] += sum(execution_counts[state] for state in (
        "completed", "waiting_verification", "waiting_manual", "manual_handoff", "preview"))

    lifecycle = growth_lifecycle.project(
        data_dir, service_id, identity_map=identity_map or {})
    business = {
        "scope": "current_lifecycle_records_not_period_delta",
        "as_of": lifecycle["as_of"],
        "contacts_total": len(lifecycle["contacts"]),
        "unqualified_events": sum(len(row.get("unqualified_events") or [])
                                  for row in lifecycle["contacts"]),
        "actionable_followups": lifecycle["counts"]["actionable_followups"],
        "suppressed_followups": lifecycle["counts"]["suppressed_followups"],
        "interpretation": "descriptive_not_causal",
    }
    for stage in ("engagement", "trial", "activation", "retention", "payment"):
        business[stage + "_contacts"] = sum(
            1 for contact in lifecycle["contacts"]
            if any(evidence.get("stage") == stage for evidence in contact.get("evidence") or []))

    return {
        "schema": SCHEMA,
        "service_id": service_id,
        "window": {"start": start_at.isoformat(), "end": end_at.isoformat(), "end_exclusive": True},
        "cycles": dict(cycle_counts, total=len(cycles),
                       cycle_refs=[row["cycle_id"] for row in cycles],
                       loaded_cycle_files=len(loaded)),
        "execution": dict(execution_counts,
                          undated_records=undated_executions,
                          completed_action_results=result_statuses.get("completed", 0),
                          action_result_statuses=dict(sorted(result_statuses.items())),
                          interpretation="platform_or_local_execution_not_business_growth"),
        "business_observations": business,
        "user_decisions": _decisions(cycles, lifecycle),
        "growth_verdict": "not_judged_by_this_report",
        "boundary": "Execution receipts, current lifecycle observations and user decisions are separate. Current lifecycle counts are not new in-window conversions or causal growth.",
    }


def render(report):
    cycles = report["cycles"]
    execution = report["execution"]
    business = report["business_observations"]
    lines = [
        "# 增长运营周期报告", "",
        "窗口：%s 至 %s（结束不含）。" % (report["window"]["start"], report["window"]["end"]),
        "", "## 本期执行", "",
        "- 创建 cycle：%d；已执行 %d；待提案 %d；待处理提案 %d；阻塞 %d。" % (
            cycles["total"], cycles["executed"], cycles["open"],
            cycles["proposed"], cycles["blocked"]),
        "- 已分派执行 %d；其中返回 completed %d；被拒绝 %d；执行阻塞 %d。" % (
            execution["executed"], execution["completed_action_results"],
            execution["refused"], execution["blocked"]),
        "", "## 当前业务观察（不是本期新增）", "",
        "- 有效互动联系人：%d；试用：%d；激活：%d；留存：%d；支付：%d。" % (
            business["engagement_contacts"], business["trial_contacts"],
            business["activation_contacts"], business["retention_contacts"],
            business["payment_contacts"]),
        "- 待跟进 %d；拒联/退信抑制 %d；不合格事件 %d。" % (
            business["actionable_followups"], business["suppressed_followups"],
            business["unqualified_events"]),
        "", "## 需要你决定", "",
    ]
    if report["user_decisions"]:
        for row in report["user_decisions"]:
            target = row.get("cycle_id") or row.get("kind")
            lines.append("- %s：%s" % (target, row["reason"]))
    else:
        lines.append("- 当前投影没有待决定事项。")
    lines.extend(["", "## 边界", "", "- " + report["boundary"], ""])
    return "\n".join(lines)
