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
#: 低于这个有效样本数时，学习建议只能算噪声（与 grow_learning.MIN_SAMPLE 同口径）。
MIN_LEARNING_SAMPLE = 3

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


def _business(ol, run_id: str, data_dir=None) -> Dict[str, Any]:
    """业务漏斗：按 run id 读 Outcomes 事件，映射成四个阶段的主体数。

    available=False 表示这个 run 名下没有业务事件——不是「业务为 0」，
    读数侧渲染成「取不到（未接入数据源）」；阶段计数只做比值口径，
    不证明渠道因果。

    G-03：一旦这个 run 有经授权导入的产品事件（``outcomes/product_events.jsonl``），
    注册/激活/付费改用三层账本的 L3 口径——它会应用人工资格判定与事件核验
    状态，避免同一页上「业务事件 1 个激活」和「L3 0 个激活」两套数。signal
    仍只来自既有 outcome 台账。"""
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
    if data_dir is not None:
        try:
            from . import outcome_layers
            if outcome_layers.has_authorized_imports(data_dir, run_id=run_id):
                summary = outcome_layers.layer_summary(data_dir, run_id=run_id)
                for stage in ("signup", "activation", "paid"):
                    funnel[stage] = _as_int(summary["L3"].get(stage))
                available = True  # 来源已接入：全 0 也要如实展示，不写「取不到」
                note = ("阶段计数是比值口径，不证明因果；L3 已按事件核验状态与"
                        "人工资格判定调整")
        except Exception:  # noqa: BLE001 三层账本读不到时保留既有口径
            pass
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


def _goal_status(run: Dict[str, Any], records: List[Dict[str, Any]],
                 week_end: str) -> Dict[str, Any]:
    """头部主目标状态：达标 / 不足 / 无数据（G-03 结果卡第一层）。

    只读同一份 outcome 计数快照；没有读数就是 ``no_data``，不拿发布量或互动
    冒充主目标进展。delta 目标（从基线往上加）按增量判断。"""
    goal = run.get("goal") or {}
    metric = str(goal.get("metric") or "")
    label = str(goal.get("metric_label") or metric or "主目标")
    by_day: Dict[str, int] = {}
    if metric == "followers":
        by_day = _count_by_day(records, "x_followers")
    elif metric in ("impressions", "visibility", "reach"):
        by_day = _count_by_day(records, "x_impressions")
    current = _latest_at_or_before(by_day, week_end) if by_day else None
    baseline = goal.get("baseline")
    target = goal.get("target")
    base = {"metric": metric, "label": label, "current": current,
            "baseline": baseline, "target": target,
            "target_kind": goal.get("target_kind"),
            "target_text": goal.get("target_text")}
    if current is None or not isinstance(target, (int, float)) or not target:
        return dict(base, state="no_data", state_label="无数据",
                    text=(f"主目标（{label}）：无数据——这个指标还没有可核验读数，"
                          "不能报达标，也不能报不足"))
    if goal.get("target_kind") == "delta" and isinstance(baseline, (int, float)):
        reached = current - baseline
        met = reached >= target
        detail = f"本周读数 {current}，相比基线 {baseline} 增加 {reached}（目标 +{target}）"
    else:
        met = current >= target
        detail = f"当前 {current}（目标 {target}）"
    return dict(base, state="met" if met else "short",
                state_label="达标" if met else "不足",
                text=f"主目标（{label}）：{'达标' if met else '不足'}——{detail}")


def _human_minutes(run: Dict[str, Any], week_start: str,
                   week_end: str) -> Optional[int]:
    """本周人工分钟：优先周 telemetry 行，其次按日相加；没有就是 None。"""
    telemetry = run.get("telemetry") if isinstance(run.get("telemetry"), dict) else {}
    weeks = telemetry.get("weeks") if isinstance(telemetry.get("weeks"), dict) else {}
    row = weeks.get(week_start) if isinstance(weeks, dict) else None
    value = _as_int_or_none(row.get("human_minutes")) if isinstance(row, dict) else None
    if value is not None:
        return value
    days = telemetry.get("days") if isinstance(telemetry.get("days"), dict) else {}
    total = 0
    seen = False
    for day, entry in days.items():
        if not (week_start <= str(day) <= week_end) or not isinstance(entry, dict):
            continue
        number = _as_int_or_none(entry.get("human_minutes"))
        if number is None:
            continue
        seen = True
        total += number
    return total if seen else None


def _layers(data_dir, run_id: str, week_start: str,
            week_end: str) -> Dict[str, Any]:
    """三层结果的只读视图；读不到就整块 UNKNOWN，绝不当成 0。"""
    try:
        from . import outcome_layers
        return outcome_layers.layer_summary(
            data_dir, run_id=run_id,
            window={"start": week_start, "end": week_end})
    except Exception as exc:  # noqa: BLE001 账本坏了不能把结果单变成 0
        from . import outcome_layers
        return outcome_layers.unknown_summary(
            "三层账本读取失败：%s: %s" % (type(exc).__name__, exc))


def _evidence(run: Dict[str, Any], layers: Dict[str, Any], *,
              published_total: int, readings_by_day: Dict[str, int],
              guardrails: Dict[str, Any], week_start: str,
              week_end: str) -> Dict[str, Any]:
    """证据覆盖与盲点：这一周哪些数有来源、哪些只能写「取不到」。"""
    reading_days = sorted(d for d in readings_by_day
                          if week_start <= d <= week_end)
    l3 = layers.get("L3") or {}
    coverage = {
        "ledger_actions": published_total,
        "reading_days": len(reading_days),
        "days_in_week": WEEK_DAYS,
        "l3_events": _as_int(l3.get("events")),
        "l3_source_connected": bool(l3.get("source_connected")),
    }
    blind_spots: List[str] = []
    if layers.get("degraded"):
        blind_spots.append("三层账本读取失败：先修数据源再谈三层口径，不要当成「没有结果」")
    elif not coverage["l3_source_connected"]:
        blind_spots.append("产品业务事件未接入：激活、付费没有可核验来源，不能报转化")
    elif _as_int(l3.get("events")) == 0:
        blind_spots.append("产品业务事件已接入，但本周窗口内没有可核验事件")
    if coverage["reading_days"] == 0:
        blind_spots.append("本周没有平台读数：曝光与新增粉丝取不到，不是 0")
    failed = _as_int(guardrails.get("failed"))
    indeterminate = _as_int(guardrails.get("indeterminate"))
    if failed or indeterminate:
        blind_spots.append(
            f"有 {failed} 次失败、{indeterminate} 次未知回执：需要人工裁决，不能当成功")
    if _as_int(l3.get("unverified")):
        blind_spots.append(
            f"有 {len(l3.get('unverified') or [])} 条来源方宣称未核验：一律不计入")
    if not blind_spots:
        blind_spots.append("本周覆盖内没发现明显盲点；committed 仍只证明发出")
    stops: List[str] = []
    status = str(run.get("status") or "")
    if status == "needs_attention":
        stops.append("运行停在 needs_attention：先处理未知/失败结果再继续")
    elif status in ("paused", "cancelled"):
        stops.append(f"运行状态是 {status}：本周没有新的自动执行")
    rejected: List[str] = []
    production = (run.get("automation") or {}) if isinstance(
        run.get("automation"), dict) else {}
    last = production.get("last_result") if isinstance(production.get("last_result"), dict) else {}
    for item in last.get("rejected") or []:
        rejected.append(str(item)[:200])
    return {"coverage": coverage, "blind_spots": blind_spots,
            "abnormal_stops": stops, "rejected": rejected}


def _recommendation(goal_status: Dict[str, Any], layers: Dict[str, Any],
                    evidence: Dict[str, Any],
                    run: Optional[Dict[str, Any]] = None) -> str:
    """下一建议：先说最缺的证据，再说要不要动计划量。

    G-04：样本不足或需要复审时，明确写「不调权重/不加额度」——建议不能
    被读成「已证明这条策略有效」。"""
    learning = (run or {}).get("learning")
    learning = learning if isinstance(learning, dict) else {}
    if learning.get("review_required"):
        return ("学习复审：" + "；".join(learning.get("review_reasons") or [])
                + "。复审前不调文案权重，也不加发布额度。")
    sample = _as_int_or_none(learning.get("sample"))
    if sample is not None and sample < MIN_LEARNING_SAMPLE:
        return (f"有效样本 {sample} 条：先采集，不据此调整文案权重或配额；"
                "样本太少时任何「更好」都只是噪声。")
    l3 = layers.get("L3") or {}
    coverage = evidence.get("coverage") or {}
    if layers.get("degraded"):
        return "先修三层账本的读取错误：读不到不等于没发生，修好再看结论。"
    if l3.get("state") == "UNKNOWN":
        return ("先把产品激活事件接进来：导出经授权的产品事件，用 "
                "`mkt grow events import` 导入。没有业务事件时，这周的发布和互动"
                "只证明做了什么，不能证明用户用起来了。")
    if coverage.get("reading_days", 0) == 0:
        return "先补平台读数：没有读数时分不清「没效果」和「没数据」，先跑 tick 或人工对数。"
    if _as_int(l3.get("unverified")):
        return "先核验未确认的业务事件（要支付记录/买方确认），核验前不要写进付费。"
    if goal_status.get("state") == "short":
        return ("主目标不足：先看证据覆盖有没有缺口，再决定是否减量或换内容；"
                "不要用一次同期增长解释成渠道有效。")
    if goal_status.get("state") == "met":
        return "主目标已达标：维持计划量，继续积累可核验样本，别把本周数据当成长期结论。"
    return "维持当前计划量，继续积累可核验样本；发布与互动的变化不能直接当成激活。"


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

    business = _business(ol, run_id, data)
    guardrails = _guardrails(run, week_start)
    supply = _supply(run)
    next_week = _next_week(run, adjustments)

    # G-03 结果卡新信息结构：主目标状态 / 三层分列 / 证据覆盖与盲点 /
    # 人工分钟 / 下一建议。全部只读，缺数写 UNKNOWN 或 None，不写 0。
    goal_status = _goal_status(run, records, week_end)
    layers = _layers(data, run_id, week_start, week_end)
    reading_days = dict(followers_by_day)
    reading_days.update(impressions_by_day)
    evidence = _evidence(run, layers, published_total=published_total,
                         readings_by_day=reading_days,
                         guardrails=guardrails, week_start=week_start,
                         week_end=week_end)
    human_minutes = _human_minutes(run, week_start, week_end)
    recommendation = _recommendation(goal_status, layers, evidence, run)

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
        "goal_status": goal_status,
        "layers": layers,
        "evidence": evidence,
        "human_minutes": human_minutes,
        "recommendation": recommendation,
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


def _render_goal_and_layers(card: Dict[str, Any]) -> List[str]:
    """主目标 + 三层分列：执行 / 目标人群有价值互动 / 产品业务事件。"""
    status = card.get("goal_status") or {}
    layers = card.get("layers") or {}
    lines = ["", "## 主目标", "",
             f"- {status.get('state_label') or '无数据'}：{status.get('text', '')}"]
    if status.get("state") == "no_data":
        lines.append("- 说明：无数据不是「没有增长」，是这个指标这周没有可核验读数")
    lines += ["", "## 三层结果", ""]
    labels = {"L1": "本周执行", "L2": "目标人群有价值互动",
              "L3": "产品业务事件"}
    for key in ("L1", "L2", "L3"):
        block = layers.get(key) or {}
        lines.append(f"- {key}（{labels[key]}｜{block.get('state', 'UNKNOWN')}）："
                     f"{block.get('text', '')}")
        if block.get("note"):
            lines.append(f"  - 口径：{block['note']}")
    if (layers.get("L3") or {}).get("source_connected"):
        lines.append("  - 口径：「业务事件」一节是本 run 累计（按 campaign 归集），"
                     "三层是本周窗口，两个口径不要相加。")
    attribution = layers.get("attribution") or {}
    counts = attribution.get("summary") or {}
    lines += ["", "激活归因（非因果，只是证据链能追到哪一步）："
              f"direct_receipt {_fmt_int(counts.get('direct_receipt', 0))}｜"
              f"assisted_observation {_fmt_int(counts.get('assisted_observation', 0))}｜"
              f"unattributed {_fmt_int(counts.get('unattributed', 0))}"]
    for row in (attribution.get("activations") or [])[:5]:
        lines.append(f"- {row.get('subject')}：{row.get('attribution')}"
                     f"（{len(row.get('touches') or [])} 条触达；"
                     f"causal_credit={row.get('causal_credit')}）")
    return lines


def _render_evidence(card: Dict[str, Any]) -> List[str]:
    """证据覆盖与盲点、异常停机、被拒内容、人工分钟与下一建议。"""
    evidence = card.get("evidence") or {}
    coverage = evidence.get("coverage") or {}
    lines = ["", "## 证据覆盖与盲点", ""]
    source = "已接入" if coverage.get("l3_source_connected") else "未接入"
    lines.append(
        f"- 覆盖：账本已确认动作 {_fmt_int(coverage.get('ledger_actions'))} 条｜"
        f"有读数 {_fmt_int(coverage.get('reading_days'))}/"
        f"{_fmt_int(coverage.get('days_in_week'))} 天｜"
        f"L3 事件 {_fmt_int(coverage.get('l3_events'))} 条（来源{source}）")
    for item in evidence.get("blind_spots") or []:
        lines.append(f"- 盲点：{item}")
    for item in evidence.get("abnormal_stops") or []:
        lines.append(f"- 异常停机：{item}")
    for item in evidence.get("rejected") or []:
        lines.append(f"- 被拒内容：{item}")
    minutes = card.get("human_minutes")
    lines += ["", f"- 人工分钟：{_fmt_int(minutes) if minutes is not None else '取不到'}"
                  "（本周人工投入，缺记就是缺记，不估算）"]
    advice = card.get("recommendation")
    if advice:
        lines += ["", f"- 下一建议：{advice}"]
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
    # G-03 追加：主目标 + 三层分列 / 证据覆盖与盲点、人工分钟与下一建议
    lines += _render_goal_and_layers(card)
    lines += _render_evidence(card)
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
