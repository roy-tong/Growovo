#!/usr/bin/env python3
"""周结果卡：把「这一周交付了什么」从账本和计数里拼出来，给人直接读。

三件套口径（2026-11-14 结果契约 v1）：
  发布量   账本 committed（发出且已确认），按渠道×动作分桶；
  日均曝光 x_impressions 日计数（平台后台读数，approximated 口径）；
  新增粉丝 x_followers 日计数的窗口差值。

纪律（继承 result.py / attribution.py 的既有规矩）：
  - 缺数写 None、渲染成「取不到」，不写 0、不造数；
  - committed 只证明发出，不证明转化；平台自报数一律标 approximated；
  - 三层不互相推导：发布量、曝光、涨粉各说各的事实。

v2 追加（2026-11-14 契约 §5，只增不改）：
  业务事件 business —— OutcomeLedger 漏斗阶段计数，比值口径、不证明因果；
  护栏与供给 guardrails/supply —— 本周 telemetry 读数（缺省 0/None）与供给水位；
  下周动作 next_week —— 本周 adjustments 转成下周要做的事。
  render() 追加「业务事件」「护栏与供给」「下周动作」三节，原三件套
  表格、既有口径说明与边界声明一字不改。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WEEK_DAYS = 7

# 业务漏斗阶段（与 attribution.FUNNEL 同序）；比例只是记账口径，不是因果。
FUNNEL_STAGES = ("signal", "signup", "activation", "paid")
FUNNEL_LABELS = {"signal": "信号/访问", "signup": "注册",
                 "activation": "激活", "paid": "付费"}
# 本周护栏读数：优先取 run.telemetry.weeks[week_start]，缺省见 _guardrails。
GUARDRAIL_COUNTERS = ("failed", "indeterminate", "duplicates",
                      "suppressed", "readings_days")


class ResultCardError(RuntimeError):
    pass


def _today() -> str:
    return date.today().isoformat()


def week_bounds(started: str, on: Optional[str] = None) -> List[Tuple[int, str, str]]:
    """按开始日切四周窗口：[(第几周, 起, 止)]；止=起+6 天。"""
    on = on or _today()
    start = date.fromisoformat(started)
    today = date.fromisoformat(on)
    if today < start:
        return []
    out = []
    week = 1
    cur = start
    while cur <= today:
        end = cur + timedelta(days=WEEK_DAYS - 1)
        out.append((week, cur.isoformat(), min(end, today).isoformat()))
        cur = end + timedelta(days=1)
        week += 1
    return out


def _count_by_day(records: List[Dict[str, Any]], metric: str) -> Dict[str, int]:
    days: Dict[str, int] = {}
    for rec in records:
        if rec.get("kind") != "count" or rec.get("metric") != metric:
            continue
        w = str(rec.get("window") or "")
        if w and w not in days:  # 同日多次读数取第一条（首读口径稳定）
            days[w] = int(rec.get("value") or 0)
    return dict(sorted(days.items()))


def _latest_at_or_before(by_day: Dict[str, int], day: str) -> Optional[int]:
    hits = [v for d, v in by_day.items() if d <= day]
    return hits[-1] if hits else None


def _belongs_to(campaign: str, run_id: str) -> bool:
    """campaign 归属：等于 run id 或以「run id-」开头。
    裸前缀会把 g1 的账算进 g10（或反过来），本周发布量就串了。"""
    return campaign == run_id or campaign.startswith(run_id + "-")


def _as_int_or_none(value) -> Optional[int]:
    """把整数/整数值浮点/数字串收敛成 int；其余（None、bool、乱码）返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    text = str(value).strip()
    if text.lstrip("+-").isdigit():
        return int(text)
    return None


def _as_int(value, default: int = 0) -> int:
    """同 _as_int_or_none，取不到时给默认值（护栏缺省用 0）。"""
    number = _as_int_or_none(value)
    return default if number is None else number


def _business(ol, run_id: str) -> Dict[str, Any]:
    """业务漏斗：按 run id 读 Outcomes 事件，映射成四个阶段的主体数。

    available=False 表示这个 run 名下没有业务事件——不是「业务为 0」，
    读数侧渲染成「取不到（未接入数据源）」；阶段计数只做比值口径，
    不证明渠道因果。"""
    funnel = {stage: 0 for stage in FUNNEL_STAGES}
    payload = ol.funnel(campaign=run_id)
    for row in payload.get("funnel") or []:
        if not isinstance(row, dict):
            continue
        stage = str(row.get("stage") or "")
        if stage in funnel:
            funnel[stage] = _as_int(row.get("subjects"))
    available = any(funnel.values())
    if available:
        note = str(payload.get("note") or "") or "阶段计数是比值口径，不证明因果"
    else:
        note = "取不到（未接入数据源）：该 run 名下没有业务事件"
    return {"available": available, "funnel": funnel, "note": note}


def _guardrails(run: Dict[str, Any], week_start: str) -> Dict[str, Any]:
    """本周护栏读数：优先 run["telemetry"]["weeks"][week_start]。

    缺 telemetry/缺该周/缺字段时按契约给缺省 0/None——这是「没有读数」的
    占位，不是伪造计数；account_health 没有就是 None，渲染成「取不到」。"""
    telemetry = run.get("telemetry")
    weeks = telemetry.get("weeks") if isinstance(telemetry, dict) else None
    row = weeks.get(week_start) if isinstance(weeks, dict) else None
    row = row if isinstance(row, dict) else {}
    out: Dict[str, Any] = {}
    for name in GUARDRAIL_COUNTERS:
        out[name] = _as_int(row.get(name))
    out["account_health"] = _as_int_or_none(row.get("account_health"))
    return out


def _supply(run: Dict[str, Any]) -> Dict[str, Any]:
    """下周供给水位：直接取 run["supply_state"]（grow_schedule.supply_status 的快照）。"""
    supply = run.get("supply_state")
    return dict(supply) if isinstance(supply, dict) else {}


def _next_week(run: Dict[str, Any], adjustments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """下周动作：计划量沿用 run.plan.actions_per_week（没有就 None），
    changes 取本期 adjustments 的 detail——本周做过的调整就是下周的待办。"""
    plan = run.get("plan")
    plan = plan if isinstance(plan, dict) else {}
    actions = _as_int_or_none(plan.get("actions_per_week"))
    if actions == 0:
        actions = None
    changes = [{"detail": str(a.get("detail") or "")}
               for a in adjustments if isinstance(a, dict)]
    return {"actions_per_week": actions, "changes": changes}


def build(data_dir, run: Dict[str, Any], week_start: str, week_end: str,
          on: Optional[str] = None) -> Dict[str, Any]:
    """拼一张周结果卡。run 是 data/grow/<id>.json 的内容。"""
    from .attribution import OutcomeLedger
    from .ledger import Ledger

    run_id = run["id"]
    data = Path(data_dir)

    # 发布量：账本 committed，campaign 以 run_id 开头，落在周窗内
    ledger = Ledger(data / "ledger.jsonl")
    committed: Dict[str, Dict[str, int]] = {}
    bounced = 0
    dry_run = 0
    for rec in ledger.all():
        campaign = str(rec.get("campaign") or "")
        if not _belongs_to(campaign, run_id):
            continue
        day = str(rec.get("ts") or "")[:10]
        if not (week_start <= day <= week_end):
            continue
        result = rec.get("result")
        if result == "committed":
            ch = committed.setdefault(str(rec.get("channel") or "?"), {})
            act = str(rec.get("action") or "?")
            ch[act] = ch.get(act, 0) + 1
        elif result == "bounced":
            bounced += 1
        elif result == "dry-run":
            dry_run += 1
    published_total = sum(n for ch in committed.values() for n in ch.values())

    # 曝光与粉丝：日计数快照
    ol = OutcomeLedger(data)
    records = ol.records()
    impressions_by_day = _count_by_day(records, "x_impressions")
    followers_by_day = _count_by_day(records, "x_followers")
    window_days = [d for d in impressions_by_day if week_start <= d <= week_end]
    impressions_values = [impressions_by_day[d] for d in window_days]
    avg_impressions = (round(sum(impressions_values) / len(impressions_values))
                       if impressions_values else None)
    followers_base = _latest_at_or_before(followers_by_day, week_start)
    followers_window = {d: v for d, v in followers_by_day.items()
                        if week_start <= d <= week_end}
    followers_latest = (list(followers_window.values())[-1] if followers_window else None)
    new_followers = (followers_latest - followers_base
                     if followers_latest is not None and followers_base is not None
                     else None)

    planned = int((run.get("plan") or {}).get("actions_per_week") or 0)
    adjustments = [a for a in run.get("adjustments") or []
                   if week_start <= str(a.get("on") or "") <= week_end]

    business = _business(ol, run_id)
    guardrails = _guardrails(run, week_start)
    supply = _supply(run)
    next_week = _next_week(run, adjustments)

    return {
        "schema": "growovo.result-card/1",
        "run": run_id,
        "goal": run.get("goal") or {},
        "week_start": week_start,
        "week_end": week_end,
        "published": {"by_channel": committed, "total": published_total},
        "planned_actions": planned,
        "impressions": {"avg_daily": avg_impressions,
                        "days_read": len(window_days),
                        "days_in_week": WEEK_DAYS,
                        "by_day": {d: impressions_by_day[d] for d in window_days}},
        "followers": {"base": followers_base, "latest": followers_latest,
                      "new": new_followers},
        "email": {"bounced": bounced},
        "dry_run": dry_run,
        "adjustments": adjustments,
        "business": business,
        "guardrails": guardrails,
        "supply": supply,
        "next_week": next_week,
        "notes": [],
    }


def _fmt_int(value) -> str:
    if value is None or isinstance(value, bool):
        return "取不到"
    if isinstance(value, float):
        value = int(value) if value.is_integer() else round(value, 1)
    return f"{value:,}"


def _render_business(card: Dict[str, Any]) -> List[str]:
    """业务事件一节：漏斗表格 + 比值口径声明；没接入就写取不到，不写 0。"""
    business = card.get("business") or {}
    lines = ["", "## 业务事件", ""]
    if not business.get("available"):
        lines.append("取不到（未接入数据源）")
        return lines
    funnel = business.get("funnel") or {}
    lines += ["| 阶段 | 数量 |", "|---|---|"]
    for stage in FUNNEL_STAGES:
        lines.append(f"| {stage}（{FUNNEL_LABELS[stage]}） | "
                     f"{_fmt_int(funnel.get(stage))} |")
    lines += ["", "比值口径，不证明因果；committed 只证明发出，不证明转化。"]
    return lines


def _supply_line(supply: Dict[str, Any]) -> Optional[str]:
    """供给水位一行：下周已暂存 X/Y（缺口 Z）。

    staged 是唯一必需项；总量优先 target/planned/actions_per_week，
    没有就用 staged+gap 反推，两者都取不到时如实写「缺口取不到」。"""
    if not isinstance(supply, dict) or not supply:
        return None
    staged = _as_int_or_none(supply.get("staged"))
    if staged is None:
        return None
    gap = _as_int_or_none(supply.get("gap"))
    total = None
    for key in ("target", "planned", "actions_per_week"):
        total = _as_int_or_none(supply.get(key))
        if total is not None:
            break
    if total is None and gap is not None:
        total = staged + gap
    if gap is None and total is not None:
        gap = total - staged
    if total is None:
        return f"下周已暂存 {_fmt_int(staged)}（缺口取不到）"
    if gap is None:
        return f"下周已暂存 {_fmt_int(staged)}/{_fmt_int(total)}（缺口取不到）"
    return (f"下周已暂存 {_fmt_int(staged)}/{_fmt_int(total)}"
            f"（缺口 {_fmt_int(gap)}）")


def _render_guardrails(card: Dict[str, Any]) -> List[str]:
    """护栏与供给：有读数（含账号健康）或供给快照才渲染。"""
    guardrails = card.get("guardrails") or {}
    supply = card.get("supply") or {}
    counters = {name: _as_int(guardrails.get(name)) for name in GUARDRAIL_COUNTERS}
    health = _as_int_or_none(guardrails.get("account_health"))
    has_guardrails = any(counters.values()) or health is not None
    supply_line = _supply_line(supply)
    if not has_guardrails and supply_line is None:
        return []
    lines = ["", "## 护栏与供给", ""]
    if has_guardrails:
        lines.append(
            f"- 失败 {_fmt_int(counters['failed'])}"
            f"｜未知 {_fmt_int(counters['indeterminate'])}"
            f"｜重复拦截 {_fmt_int(counters['duplicates'])}"
            f"｜抑制跳过 {_fmt_int(counters['suppressed'])}"
            f"｜读数 {_fmt_int(counters['readings_days'])}/{WEEK_DAYS} 天"
            f"｜账号健康 {_fmt_int(health) if health is not None else '取不到'}")
    if supply_line:
        lines.append(f"- {supply_line}")
    return lines


def _render_next_week(card: Dict[str, Any]) -> List[str]:
    """下周动作：本周 adjustments 的 detail 逐条列出；没有就不渲染该节。"""
    changes = (card.get("next_week") or {}).get("changes") or []
    details = [str(c.get("detail") or "").strip()
               for c in changes if isinstance(c, dict)]
    details = [detail for detail in details if detail]
    if not details:
        return []
    lines = ["", "## 下周动作", ""]
    for detail in details:
        lines.append(f"- {detail}")
    return lines


def render(card: Dict[str, Any]) -> str:
    """人读版结果单。这一页就是交付物：数字、证据口径、边界声明。"""
    goal = card.get("goal") or {}
    pub = card.get("published") or {}
    imp = card.get("impressions") or {}
    fol = card.get("followers") or {}
    lines = [
        f"# 结果单：{card.get('run')}（{card.get('week_start')} ~ {card.get('week_end')}）",
        "",
        (f"目标：{goal.get('name') or goal.get('metric', '')}"
         f"（{goal.get('target_text')}）"
         if goal.get("target_text") else
         f"目标：{goal.get('name') or goal.get('metric', '')}"
         f"（基线 {_fmt_int(goal.get('baseline'))}"
         f" → 目标 {_fmt_int(goal.get('target'))}）"),
        "",
        "## 这一周交付了什么",
        "",
        "| 口径 | 数值 | 证据口径 |",
        "|---|---|---|",
        (f"| 发布量（committed） | {_fmt_int(pub.get('total'))}"
         f"（计划 {card.get('planned_actions') or 0}） | 账本已确认发出，可逐条回查 |"),
        (f"| 日均曝光 | {_fmt_int(imp.get('avg_daily'))}"
         f"（{imp.get('days_read', 0)}/{imp.get('days_in_week', 7)} 天有读数）"
         " | 平台后台自报数，approximated |"),
        (f"| 新增粉丝 | {_fmt_int(fol.get('new'))}"
         f"（{fol.get('base', '取不到')} → {fol.get('latest', '取不到')}）"
         " | 粉丝数日快照差值，approximated |"),
    ]
    email = card.get("email") or {}
    if email.get("bounced"):
        lines.append(f"| 退信 | {email['bounced']} | 账本 bounced 终态 |")
    if card.get("dry_run"):
        lines.append(f"| 干跑（未发送） | {card['dry_run']} | 闭环未开闸，不计发布量 |")
        if not (pub.get("total") or 0):
            lines.append("")
            lines.append("本周全部是干跑：先 `mkt grow arm` 开闸，再让 tick 跑一周。")
    by_channel = pub.get("by_channel") or {}
    if by_channel:
        detail = "；".join(
            f"{ch}：" + "、".join(f"{act} {n}" for act, n in sorted(acts.items()))
            for ch, acts in sorted(by_channel.items()))
        lines += ["", f"发布明细：{detail}"]
    adj = card.get("adjustments") or []
    if adj:
        lines += ["", "## 本周自动调整", ""]
        for a in adj:
            lines.append(f"- [{a.get('kind')}] {a.get('detail', '')}")
    # v2 追加三节：业务事件 / 护栏与供给 / 下周动作（原三件套与边界声明不动）
    lines += _render_business(card)
    lines += _render_guardrails(card)
    lines += _render_next_week(card)
    lines += [
        "",
        "## 边界声明",
        "",
        "- 发布量是「已发出且确认」，不等于业务转化；",
        "- 曝光与粉丝是平台自报数（首周人工对数，UNVERIFIED 起步）；",
        "- 缺数写「取不到」，不写 0；三层口径互不推导。",
        "",
    ]
    return "\n".join(lines)
