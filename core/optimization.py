#!/usr/bin/env python3
"""Optimization proposals: deterministic next moves from the scorecard.

The engine compares recorded operating facts and proposes what to fix or test
next. It does not run anything, does not score "channel effectiveness", and
marks every proposal ``automatic_apply: false``: the operator still decides,
because a committed action is not a business outcome.
"""
from __future__ import annotations

from typing import Any, Dict, List

MAX_PROPOSALS = 12


def _proposal(proposal_id: str, severity: str, statement: str,
              evidence: Dict[str, Any], next_step: str, *,
              kind: str = "fix") -> Dict[str, Any]:
    return {
        "id": proposal_id,
        "kind": kind,
        "severity": severity,
        "statement": statement,
        "evidence": evidence,
        "next_step": next_step,
        "requires_human": True,
        "automatic_apply": False,
    }


def propose(scorecard: Dict[str, Any], *, memory: Dict[str, Any] | None = None) -> Dict[str, Any]:
    proposals: List[Dict[str, Any]] = []
    groups = [group for group in scorecard.get("groups") or [] if isinstance(group, dict)]
    totals = scorecard.get("totals") or {}

    unknown = sum(int(group.get("unknown") or 0) for group in groups)
    if unknown:
        proposals.append(_proposal(
            "reconcile-unknown", "high",
            f"{unknown} 条结果未知；在此之前增加放量只会放大不可核对的发送",
            {"unknown": unknown, "groups": [g["channel"] + "/" + g["action"] for g in groups if g.get("unknown")]},
            "逐条核对平台后台，用 `mkt ledger resolve` 记回结果",
            kind="safety",
        ))

    failed = int(totals.get("failed") or 0)
    if failed:
        proposals.append(_proposal(
            "repair-failures", "high",
            f"{failed} 条失败/被拒；先修复定位器、门禁或平台限制，不要重试轰炸",
            {"failed": failed},
            "查看 mkt report 的 needs_attention，修复后再排下一波",
            kind="fix",
        ))

    if int(totals.get("attempted") or 0) == 0 and int(totals.get("dry_run") or 0):
        proposals.append(_proposal(
            "start-execution", "medium",
            "账本只有 dry-run，尚无真实执行数据；现在谈优化没有依据",
            {"dry_run": int(totals.get("dry_run") or 0)},
            "先跑一波已审批的真实队列，再回来看记分卡",
            kind="data",
        ))

    executed = [group for group in groups if int(group.get("attempted") or 0) >= 5]
    if len(executed) >= 2:
        ranked = sorted(executed, key=lambda g: (-float(g.get("committed_rate") or 0),
                                                 -int(g.get("attempted") or 0)))
        best, rest = ranked[0], ranked[1:]
        worst = rest[-1]
        if float(best.get("committed_rate") or 0) - float(worst.get("committed_rate") or 0) >= 0.2:
            proposals.append(_proposal(
                "shift-volume", "medium",
                (f"{best['channel']}/{best['action']} 的确认率 {best['committed_rate']:.0%} 高于 "
                 f"{worst['channel']}/{worst['action']} 的 {worst['committed_rate']:.0%}；"
                 "这是描述性差异，不是渠道效果证明"),
                {"best": {"channel": best["channel"], "action": best["action"],
                          "rate": best["committed_rate"], "attempted": best["attempted"]},
                 "worst": {"channel": worst["channel"], "action": worst["action"],
                           "rate": worst["committed_rate"], "attempted": worst["attempted"]}},
                "下一波把少量预算移向高确认率渠道，同时保留对照与预注册指标",
                kind="experiment",
            ))

    for group in groups:
        rate = float(group.get("committed_rate") or 0)
        if int(group.get("attempted") or 0) >= 5 and rate < 0.5:
            proposals.append(_proposal(
                f"low-rate:{group['channel']}/{group['action']}", "medium",
                (f"{group['channel']}/{group['action']} 确认率 {rate:.0%}（{group['committed']}/"
                 f"{group['attempted']}）；先查平台限制与定位器，不要用模板硬发"),
                {"attempted": group["attempted"], "committed": group["committed"],
                 "unknown": group["unknown"], "failed": group["failed"]},
                "dry-run 复核定位器与门禁；必要时把该渠道标回 UNVERIFIED",
                kind="fix",
            ))

    outcomes = scorecard.get("outcomes") or {}
    funnel = outcomes.get("funnel") or []
    subjects = sum(int(item.get("subjects") or 0) for item in funnel if isinstance(item, dict))
    if subjects == 0:
        proposals.append(_proposal(
            "instrument-outcomes", "medium",
            "没有业务结果事件；平台动作完成不等于客户激活或付费",
            {"funnel": [{"stage": item.get("stage"), "subjects": item.get("subjects")}
                        for item in funnel]},
            "接入注册/激活/付费事件（或手工导入 CSV），再评估业务效果",
            kind="data",
        ))

    for pattern in (memory or {}).get("patterns") or []:
        if not isinstance(pattern, dict) or pattern.get("candidate_for_review"):
            continue
        proposals.append(_proposal(
            f"memory-blocker:{pattern.get('channel')}/{pattern.get('action')}", "low",
            (f"{pattern.get('channel')}/{pattern.get('action')} 的回执模式有阻断项："
             + "；".join(pattern.get("review_blockers") or [])),
            {"receipts": pattern.get("receipt_count"),
             "blockers": pattern.get("review_blockers")},
            "先解决回执里的未知/失败/授权问题，再考虑复用该流程",
            kind="review",
        ))

    proposals.sort(key=lambda item: ({"high": 0, "medium": 1, "low": 2}.get(item["severity"], 9),
                                     item["id"]))
    return {
        "schema_version": "1.0",
        "kind": "growovo.optimization",
        "proposals": proposals[:MAX_PROPOSALS],
        "counts": {severity: sum(1 for item in proposals if item["severity"] == severity)
                   for severity in ("high", "medium", "low")},
        "boundary": ("建议来自已记录回执与业务事件的描述性比较；不证明渠道效果或因果关系，"
                     "不自动改预算、不自动重放，全部需要人工决定。"),
    }


def markdown(payload: Dict[str, Any]) -> str:
    lines = ["# 运营优化建议", ""]
    lines.append(f"- 高危 {payload['counts']['high']}；中 {payload['counts']['medium']}；低 {payload['counts']['low']}")
    lines.append("")
    for proposal in payload["proposals"]:
        lines.append(f"## [{proposal['severity']}] {proposal['statement']}")
        lines.append(f"- 证据：`{proposal['evidence']}`")
        lines.append(f"- 下一步：{proposal['next_step']}")
        lines.append("- 需要人工决定；不会自动执行。")
        lines.append("")
    if not payload["proposals"]:
        lines.append("- 没有可提出的建议。")
        lines.append("")
    lines.extend(["## 边界", "", payload["boundary"], ""])
    return "\n".join(lines)
