#!/usr/bin/env python3
"""执行结果只读投影：平台动作、任务结果与业务结果分层且同范围。

事实源分别是 action ledger、OutcomeLedger、Followups；本模块只负责投影，
不把“发送成功”推导为“任务解决”，也不把历史对象当成本轮进展。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

SENT = ("committed", "sent", "ok", "success", "resolved_committed", "resolved_sent")
BAD = ("failed", "rejected", "refused", "bounced")
UNKNOWN = ("unknown", "indeterminate", "needs_review")
SUPPRESSED = ("suppressed", "no-dm", "no_dm", "dnc")


def platform_layer(ledger_entries: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    sent = bad = unknown = suppressed = 0
    for e in ledger_entries:
        r = str(e.get("result") or e.get("status") or "").lower()
        if r in SENT:
            sent += 1
        elif r in BAD:
            bad += 1
        elif r in UNKNOWN:
            unknown += 1
        elif r in SUPPRESSED:
            suppressed += 1
        else:
            # 无法识别的执行状态只能进入未知，不能默认为成功或失败。
            unknown += 1
    return {"sent": sent, "failed": bad, "unknown": unknown,
            "suppressed": suppressed,
            "total": sent + bad + unknown + suppressed}


def task_layer(followups=None, intent_summary: Optional[Dict[str, Any]] = None,
               task_outcome_records=None, *, task_outcomes=None) -> Dict[str, int]:
    """Project followups and canonical task-outcome records without cross-layer inference.

    ``task_outcomes`` is a compatibility-friendly alias for
    ``task_outcome_records``. A record's ``action_result`` is not counted as a
    platform action here, and its ``business_outcome`` is not a value event.
    """
    out = {"objects": 0, "replied": 0, "asked": 0, "moved": 0,
           "records": 0, "resolved": 0, "partial": 0, "unresolved": 0,
           "unknown": 0, "needs_human": 0}
    if followups is not None:
        stats = followups.stats()
        out["objects"] = stats.get("substantive", 0)
        for r in followups.all():
            for t in r.get("touches", []):
                if t.get("result") == "replied":
                    out["replied"] += 1
                elif t.get("result") == "asked":
                    out["asked"] += 1
    if intent_summary:
        out["moved"] = intent_summary.get("moved", 0)

    records = task_outcome_records
    if records is None:
        records = task_outcomes
    elif task_outcomes is not None:
        records = list(records) + list(task_outcomes)
    for record in records or ():
        if not isinstance(record, dict):
            continue
        status = str(record.get("task_outcome") or "").strip()
        if status in ("resolved", "partial", "unresolved", "unknown", "needs_human"):
            out[status] += 1
            out["records"] += 1
    return out


def _verified_event(event: Dict[str, Any]) -> bool:
    """同时兼容当前 OutcomeLedger 与旧测试/导入格式，默认不放宽。"""
    if event.get("approximated") is True or event.get("is_approximation") is True:
        return False
    verification = event.get("verification")
    if isinstance(verification, dict):
        return verification.get("status") == "verified"
    if "verified" in event:
        return event.get("verified") is True
    # 没有核验状态的价值事件只能作为候选，不计入业务结果。
    return False


def business_layer(events: Iterable[Dict[str, Any]],
                   value_events: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    allowed = {str(x) for x in value_events} if value_events else None
    by_type: Dict[str, int] = {}
    for e in events:
        if not _verified_event(e):
            continue
        t = str(e.get("event_type") or e.get("type") or "").strip()
        if not t or (allowed is not None and t not in allowed):
            continue
        by_type[t] = by_type.get(t, 0) + 1
    out: Dict[str, Any] = {}
    out.update({k: by_type.get(k, 0) for k in ("signup", "activation", "paid")})
    out["by_type"] = by_type
    return out


def check_criteria(criteria: Iterable[Dict[str, Any]],
                   business: Dict[str, Any], task: Dict[str, int],
                   platform: Dict[str, int]) -> List[Dict[str, Any]]:
    """可选学习指标：取不到数就是未测，不写成 0，也不产生产品生死门。"""
    rows = []
    by_type = business.get("by_type", {})
    for c in criteria:
        metric = c.get("metric", "")
        target = c.get("target")
        if metric in by_type or metric in ("paid", "signup", "activation"):
            actual = by_type.get(metric, business.get(metric, 0))
        elif metric in ("asked", "replied", "objects", "moved", "resolved",
                        "partial", "unresolved", "needs_human"):
            actual = task.get(metric, 0)
        elif metric in ("sent", "failed", "unknown", "suppressed"):
            actual = platform.get(metric, 0)
        else:
            rows.append({**c, "actual": None, "ok": None, "note": "这个指标目前取不到数"})
            continue
        try:
            ok = actual >= float(target) if target is not None else None
        except (TypeError, ValueError):
            ok = None
        rows.append({**c, "actual": actual, "ok": ok,
                     "note": "" if ok is not None else "这是观察项，未设置可计算目标"})
    return rows


def scoped_task_outcomes(records: Iterable[Dict[str, Any]],
                         scope: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Apply campaign/subject/time scope to canonical records and metadata.

    action_runner stores campaign/target inside metadata.  Flatten only for
    matching; the canonical record remains unchanged in the returned copy.
    """
    from .scope import filter_rows
    rows = []
    for record in records:
        if not isinstance(record, dict):
            continue
        row = dict(record)
        metadata = record.get("metadata") or {}
        for key in ("campaign", "target", "subject"):
            if key not in row and metadata.get(key) not in (None, ""):
                row[key] = metadata[key]
        rows.append(row)
    return filter_rows(rows, scope)


def collect(*, ledger_entries=(), followups=None, intent_summary=None,
            outcome_events=(), criteria=(), scope: Optional[Dict[str, Any]] = None,
            value_events: Optional[Iterable[str]] = None,
            task_outcome_records=None, task_outcomes=None) -> Dict[str, Any]:
    p = platform_layer(ledger_entries)
    t = task_layer(followups, intent_summary, task_outcome_records,
                   task_outcomes=task_outcomes)
    b = business_layer(outcome_events, value_events=value_events)
    rows = check_criteria(criteria, b, t, p)

    task_activity = (t["objects"] + t["replied"] + t["asked"] + t["moved"]
                     + t.get("records", 0))
    if not any((p["total"], task_activity, sum(b["by_type"].values()))):
        observation, why = "数据不足", "当前范围没有动作、任务结果或可核验价值事件"
    elif b["by_type"]:
        observation, why = "已观察到价值事件", "业务事件与动作/任务结果分层记录，不互相推导"
    elif p["total"] or task_activity:
        observation, why = "部分观察", "已有动作或任务进展，但尚未观察到可核验价值事件"
    else:
        observation, why = "数据不足", "当前范围没有可核验结果"

    return {"platform": p, "task": t, "business": b,
            "criteria": rows, "observation": observation,
            # verdict 仅作旧调用方兼容字段，语义已变为观察状态，不再是产品判决。
            "verdict": observation, "why": why,
            "scope": dict(scope or {})}


def render(r: Dict[str, Any]) -> str:
    from .narrative import table
    lines = ["# 执行结果", ""]
    p, t, b = r["platform"], r["task"], r["business"]
    scope = r.get("scope") or {}
    if scope:
        lines.append("范围：" + " · ".join(f"{k}={v}" for k, v in scope.items() if v))
        lines.append("")
    lines.append("## 三层分开算")
    lines.append("")
    business_bits = [f"{name} {count}" for name, count in b.get("by_type", {}).items()]
    task_bits = (f"任务记录 {t.get('records', 0)}｜解决 {t.get('resolved', 0)}｜"
                 f"部分解决 {t.get('partial', 0)}｜未解决 {t.get('unresolved', 0)}｜"
                 f"未知 {t.get('unknown', 0)}｜需人工 {t.get('needs_human', 0)}｜"
                 f"有回应的人 {t['objects']}｜回话 {t['replied']}｜提问 {t['asked']}｜"
                 f"转一对一 {t['moved']}")
    lines.append(table(["层", "数字", "说明"], [
        ["平台动作", f"发出 {p['sent']}｜失败 {p['failed']}｜未知 {p['unknown']}｜抑制 {p['suppressed']}",
         "动作成功不等于有转化"],
        ["任务结果", task_bits,
         "任务完成有独立记录；不等于平台动作或业务转化"],
        ["业务结果", "｜".join(business_bits) if business_bits else "暂无可核验价值事件",
         "只计已核验且非近似事件，不能代替完整用户旅程"],
    ]))
    lines.append("")
    if r["criteria"]:
        lines.append("## 可选学习指标（不是产品生死门）")
        lines.append("")
        lines.append(table(["指标", "参考目标", "实际", "观察"],
                           [[c.get("name", c.get("metric")), c.get("target") if c.get("target") is not None else "—",
                             c.get("actual") if c.get("actual") is not None else "取不到",
                             "达到" if c["ok"] else ("未达到" if c["ok"] is False else "未测")]
                            for c in r["criteria"]]))
        lines.append("")
    lines.append(f"**观察：{r['observation']}。** {r['why']}。")
    return "\n".join(lines)
