#!/usr/bin/env python3
"""执行进度：把账本、跟进台账、四周战术合成「今天该关心什么」。

日报和周报的区别不是篇幅，是问题：
  日报问「今天有没有该动没动的」
  周报问「这一周比上一周好还是差」

所有数字来自本地文件；没有数据就明说没有，不编。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


def _date(iso: str) -> date:
    return date.fromisoformat(iso)


def daily(followups, intent=None, on: Optional[str] = None,
          ledger_entries: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    on = on or date.today().isoformat()
    due = followups.due(on) if followups else []
    overdue = followups.overdue(on) if followups else []
    stats = followups.stats(on) if followups else {"total": 0, "open": 0, "substantive": 0}

    yesterday = _date(on) - timedelta(days=1)
    touched_yesterday = 0
    for r in (followups.all() if followups else []):
        for t in r.get("touches", []):
            if t.get("at") == yesterday.isoformat():
                touched_yesterday += 1

    phase = None
    if intent is not None:
        try:
            phase = intent.summary(on)
        except Exception:
            phase = None

    lines: List[str] = []
    if overdue:
        lines.append(f"{len(overdue)} 个人已经跟丢超过 7 天，先处理这些")
    if due:
        lines.append(f"今天该跟 {len(due)} 个")
    if touched_yesterday == 0 and stats.get("open"):
        lines.append("昨天没有任何跟进动作——这是最常见的中断点")
    if phase and phase.get("weeks_behind_quota"):
        wk = "、".join(map(str, phase["weeks_behind_quota"]))
        lines.append(f"第 {wk} 周未达每周配额")
    if not lines:
        lines.append("没有需要立刻处理的事")

    return {"date": on, "due": len(due), "overdue": len(overdue),
            "touched_yesterday": touched_yesterday,
            "open_objects": stats.get("open", 0),
            "phase": phase, "brief": lines}


def weekly(followups, intent=None, on: Optional[str] = None) -> Dict[str, Any]:
    """本周 vs 上周：动作、回应、转场。"""
    on = on or date.today().isoformat()
    end = _date(on)
    cur_start = end - timedelta(days=6)
    prev_start = cur_start - timedelta(days=7)
    prev_end = cur_start - timedelta(days=1)

    def bucket(a: date, b: date) -> Dict[str, int]:
        touches = replies = 0
        for r in (followups.all() if followups else []):
            for t in r.get("touches", []):
                d = t.get("at") or ""
                if a.isoformat() <= d <= b.isoformat():
                    touches += 1
                    if t.get("result") in ("replied", "asked", "used"):
                        replies += 1
        return {"touches": touches, "replies": replies}

    cur = bucket(cur_start, end)
    prev = bucket(prev_start, prev_end)
    stats = followups.stats(on) if followups else {}

    delta_t = cur["touches"] - prev["touches"]
    delta_r = cur["replies"] - prev["replies"]

    verdict: List[str] = []
    if cur["touches"] == 0:
        verdict.append("这一周没有任何跟进动作，渠道处于停摆状态")
    elif delta_t > 0 and delta_r >= 0:
        verdict.append("动作量和回应都在上升，保持")
    elif delta_t > 0 and delta_r < 0:
        verdict.append("动作变多但回应变少——多半是话术问题，不是渠道问题")
    elif delta_t < 0:
        verdict.append("动作量在下降，先确认是不是人手不够")

    stage = "未开始"
    if stats.get("substantive"):
        stage = "被问到"
    if stats.get("conversations_after_45d"):
        stage = "被用上"

    return {"week_start": cur_start.isoformat(), "week_end": end.isoformat(),
            "this_week": cur, "last_week": prev, "delta": {"touches": delta_t, "replies": delta_r},
            "objects": stats.get("total", 0), "open": stats.get("open", 0),
            "stage": stage, "verdict": verdict}


def render_daily(d: Dict[str, Any]) -> str:
    lines = [f"# 日报 · {d['date']}", ""]
    lines.append(f"对象 {d['open_objects']} 个进行中｜今天到期 {d['due']}｜逾期 {d['overdue']}｜"
                 f"昨天动作 {d['touched_yesterday']}")
    lines.append("")
    for b in d["brief"]:
        lines.append(f"- {b}")
    if d.get("phase"):
        p = d["phase"]
        lines.append("")
        lines.append(f"四周战术：第 {p['week']} 周·{p['phase']}——{p['todo']}")
    return "\n".join(lines)


def render_weekly(w: Dict[str, Any]) -> str:
    lines = [f"# 周报 · {w['week_start']} → {w['week_end']}", ""]
    lines.append(f"动作 {w['this_week']['touches']}（上周 {w['last_week']['touches']}）｜"
                 f"回应 {w['this_week']['replies']}（上周 {w['last_week']['replies']}）")
    lines.append("")
    lines.append(f"当前走到：**{w['stage']}**。对象 {w['objects']} 个，进行中 {w['open']}。")
    lines.append("")
    for v in w["verdict"]:
        lines.append(f"- {v}")
    return "\n".join(lines)
