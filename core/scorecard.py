#!/usr/bin/env python3
"""Operations scorecard: what the ledger and outcome events can actually support.

Rates here are descriptive bookkeeping, not conversion or attribution claims:

- ``committed_rate`` counts executed receipts whose platform result is known;
- ``unknown_rate`` makes the cost of indeterminate results visible;
- business outcomes come from the separate outcome ledger and are reported as
  counts, never blended into a channel "success" score.

No model calls, no external requests, no writes.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .attribution import OutcomeLedger
from .ledger import Ledger

COMMITTED = {"committed", "resolved_committed", "resolved_sent"}
UNKNOWN = {"indeterminate"}
FAILED = {"failed", "rejected", "no-dm", "bounced", "suppressed"}
SKIPPED = {"dry-run"}


def _bucket(result: str) -> str:
    if result in COMMITTED:
        return "committed"
    if result in UNKNOWN:
        return "unknown"
    if result in FAILED:
        return "failed"
    if result in SKIPPED:
        return "dry_run"
    return "other"


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def build(data_dir: Path, *, campaign: Optional[str] = None,
          since: Optional[str] = None) -> Dict[str, Any]:
    data_dir = Path(data_dir)
    records = Ledger(data_dir / "ledger.jsonl").all()
    rows = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if campaign is not None and record.get("campaign") != campaign:
            continue
        if since and str(record.get("ts") or "") < since:
            continue
        rows.append(record)

    groups: Dict[tuple, Dict[str, Any]] = {}
    for record in rows:
        channel = str(record.get("channel") or "unknown")
        action = str(record.get("action") or "unknown")
        key = (channel, action, str(record.get("campaign") or ""))
        group = groups.setdefault(key, {
            "channel": channel, "action": action, "campaign": key[2],
            "receipts": 0, "committed": 0, "unknown": 0, "failed": 0,
            "dry_run": 0, "other": 0, "with_evidence": 0,
            "first_at": None, "last_at": None,
        })
        group["receipts"] += 1
        group[_bucket(str(record.get("result") or ""))] += 1
        if str(record.get("evidence") or "").strip():
            group["with_evidence"] += 1
        ts = str(record.get("ts") or "")
        if ts:
            group["first_at"] = ts if group["first_at"] is None else min(group["first_at"], ts)
            group["last_at"] = ts if group["last_at"] is None else max(group["last_at"], ts)

    summary: List[Dict[str, Any]] = []
    for group in groups.values():
        attempted = group["committed"] + group["unknown"] + group["failed"] + group["other"]
        group["attempted"] = attempted
        group["committed_rate"] = _rate(group["committed"], attempted)
        group["unknown_rate"] = _rate(group["unknown"], attempted)
        group["failed_rate"] = _rate(group["failed"], attempted)
        group["evidence_rate"] = _rate(group["with_evidence"], group["committed"])
        summary.append(group)
    summary.sort(key=lambda item: (item["channel"], item["action"], item["campaign"]))

    funnel: Dict[str, Any] = {}
    try:
        funnel = OutcomeLedger(data_dir).funnel(campaign)
    except (OSError, ValueError, KeyError):
        funnel = {}

    warnings: List[Dict[str, Any]] = []
    totals = {name: sum(group[name] for group in summary)
              for name in ("attempted", "committed", "unknown", "failed", "dry_run")}
    if totals["unknown"]:
        warnings.append({"id": "unknown_results", "severity": "high",
                         "detail": f"{totals['unknown']} 条回执结果未知；先裁决再继续放量"})
    if totals["failed"]:
        warnings.append({"id": "failed_results", "severity": "high",
                         "detail": f"{totals['failed']} 条执行失败/被拒；修复后再跑"})
    if totals["attempted"] == 0 and totals["dry_run"]:
        warnings.append({"id": "dry_run_only", "severity": "medium",
                         "detail": "账本只有 dry-run；尚未发生真实执行"})
    for group in summary:
        if group["attempted"] >= 5 and group["committed_rate"] < 0.5:
            warnings.append({
                "id": f"low_committed_rate:{group['channel']}/{group['action']}",
                "severity": "medium",
                "detail": (f"{group['channel']}/{group['action']} 已执行 {group['attempted']} 次，"
                           f"确认 {group['committed']} 次；先查定位器/平台限制"),
            })
    has_outcomes = any(int(item.get("subjects") or 0) for item in funnel.get("funnel") or [])
    if not has_outcomes:
        warnings.append({"id": "no_business_outcomes", "severity": "medium",
                         "detail": "没有业务结果事件；平台动作成功不等于客户激活或付费"})

    return {
        "schema_version": "1.0",
        "kind": "growovo.scorecard",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filter": {"campaign": campaign, "since": since},
        "totals": totals,
        "groups": summary,
        "outcomes": funnel,
        "warnings": warnings,
        "boundary": ("账本比率只描述已记录的回执；业务结果来自独立事件台账，"
                     "两者都不证明因果或渠道效果。"),
    }


def markdown(payload: Dict[str, Any]) -> str:
    lines = [
        "# 运营记分卡",
        "",
        f"- 生成：{payload['generated_at']}",
        f"- 过滤：{payload['filter']}",
        "",
        "| 渠道 | 动作 | campaign | 执行 | 确认 | 未知 | 失败 | 确认率 | 未知率 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for group in payload["groups"]:
        lines.append(
            f"| {group['channel']} | {group['action']} | {group['campaign'] or '-'} | "
            f"{group['attempted']} | {group['committed']} | {group['unknown']} | "
            f"{group['failed']} | {group['committed_rate']:.0%} | {group['unknown_rate']:.0%} |"
        )
    lines.extend(["", "## 警告", ""])
    lines.extend(f"- [{item['severity']}] {item['detail']}" for item in payload["warnings"])
    if not payload["warnings"]:
        lines.append("- 无")
    lines.extend(["", "## 边界", "", payload["boundary"], ""])
    return "\n".join(lines)
