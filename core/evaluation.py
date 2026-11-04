#!/usr/bin/env python3
"""能力、竞争与进化评估：保留可比较证据，不制造综合优胜分。"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

SAME_CONDITION_FIELDS = ("data_ref", "budget", "time_window", "authorization")
METRICS = ("quality", "human_minutes", "cost", "verified_outcomes")


def _condition(run: Dict[str, Any], name: str) -> Dict[str, Any]:
    conditions = run.get("conditions") or {}
    missing = [key for key in SAME_CONDITION_FIELDS if not conditions.get(key)]
    if missing:
        raise ValueError(f"{name} 缺少同条件字段: {', '.join(missing)}")
    return {key: conditions[key] for key in SAME_CONDITION_FIELDS}


def compare_runs(ours: Dict[str, Any], alternative: Dict[str, Any]) -> Dict[str, Any]:
    """同数据/预算/时间/授权下逐项比较，不自动宣布总分赢家。"""
    left, right = _condition(ours, "ours"), _condition(alternative, "alternative")
    if left != right:
        raise ValueError("对照条件不一致：data_ref/budget/time_window/authorization 必须相同")
    task_id = ours.get("task_id") or alternative.get("task_id")
    if not task_id or ours.get("task_id") != alternative.get("task_id"):
        raise ValueError("对照双方必须是同一个 task_id")
    rows = []
    for metric in METRICS:
        a, b = ours.get("metrics", {}).get(metric), alternative.get("metrics", {}).get(metric)
        if a is None or b is None:
            outcome = "unknown"
        elif metric in ("human_minutes", "cost"):
            outcome = "ours_lower" if a < b else ("alternative_lower" if a > b else "same")
        else:
            outcome = "ours_higher" if a > b else ("alternative_higher" if a < b else "same")
        rows.append({"metric": metric, "ours": a, "alternative": b, "comparison": outcome})
    return {
        "kind": "growovo.competition_evaluation", "task_id": task_id,
        "conditions": left, "metrics": rows,
        "boundary": "逐项比较不是总分排名；unknown 不得当成 ours 更优，商业结果仍需独立核验。",
    }


def assess_evolution(previous_failures: Iterable[Dict[str, Any]],
                     current_results: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """比较旧失败与新案例，区分修复、仍失败、未知，不证明泛化完成。"""
    before = {row["case_id"]: row for row in previous_failures}
    after = {row["case_id"]: row for row in current_results}
    if not before or not after:
        raise ValueError("进化评估需要 previous_failures 与 current_results")
    changes = []
    for case_id, old in before.items():
        new = after.get(case_id)
        if new is None:
            changes.append({"case_id": case_id, "change": "missing", "note": "当前版本没有该案例"})
            continue
        old_status = old.get("status", "failed")
        new_status = new.get("status", "unknown")
        if new_status == "unknown":
            change = "unknown"
        elif old_status != "failed" and new_status == "passed":
            change = "improved"
        elif old_status == "failed" and new_status == "passed":
            change = "fixed"
        elif new_status == "failed":
            change = "unchanged_failure"
        else:
            change = "changed"
        changes.append({"case_id": case_id, "before": old_status, "after": new_status, "change": change})
    new_cases = [case_id for case_id in after if case_id not in before]
    for case_id in new_cases:
        changes.append({"case_id": case_id, "before": "unseen", "after": after[case_id].get("status", "unknown"),
                        "change": "new_case"})
    return {
        "kind": "growovo.evolution_evaluation", "changes": changes,
        "summary": {key: sum(1 for row in changes if row["change"] == key)
                    for key in ("fixed", "improved", "unchanged_failure", "unknown", "new_case", "missing", "changed")},
        "boundary": "通过固定回归不等于泛化；必须结合外部未见案例与失败原因，不能把状态变化写成能力证明。",
    }


def validate_external_case(case: Dict[str, Any]) -> List[str]:
    """外部未见任务的最低契约；返回缺失/不合格项，不代替授权审查。"""
    problems = []
    required = {
        "business_question": "真实业务问题",
        "not_self_owned": "非自家任务",
        "uncurated_data": "未专门整理的数据",
        "independent_evaluation": "独立结果评价",
        "existing_human_workflow": "现有人工作业流程",
        "authorized": "授权依据",
        "no_custom_interface": "未专门修改输入接口",
        "strongest_alternative": "最强合理替代",
        "same_conditions": "同数据/预算/时间/授权对照条件",
    }
    for key, label in required.items():
        value = case.get(key)
        if value in (None, "", [], {}):
            problems.append(f"缺少{label}: {key}")
    if case.get("cherry_picked") is True:
        problems.append("不能选择最容易成功的 cherry-picked 样例")
    return problems
