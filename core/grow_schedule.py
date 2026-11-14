#!/usr/bin/env python3
"""配额、时间窗与供给水位 —— 结果闭环 v2 的调度基础（纯函数，可注入时钟）。

三件事：
  1. 把一周计划量精确摊到 7 天（周计划 = 执行量，不再只是周卡上的数字）；
  2. 判断一条内容现在能不能发（受众本地黄金窗，死区一律不发）；
  3. 算未来几天有没有货（供给水位），缺货提前暴露。

全部函数不读写文件、不发网络请求、不 sleep；now 一律外部传入。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import timezone_windows as tw

# 默认受众与窗口：没有显式指定时按美西工作人群处理，宁可保守。
DEFAULT_AUDIENCE = "us-west"
DEFAULT_WINDOWS = {"audience": DEFAULT_AUDIENCE,
                   "local": [[20.0, 23.0], [8.0, 10.0], [12.0, 13.5]]}


class ScheduleError(ValueError):
    pass


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ScheduleError(f"日期格式应为 YYYY-MM-DD：{value!r}") from exc


def week_start_of(day: str, started: str) -> str:
    """以 started 为锚，返回 day 所在 7 天窗口的第一天。"""
    anchor = _parse_day(started)
    target = _parse_day(day)
    if target < anchor:
        raise ScheduleError(f"{day} 早于开始日 {started}")
    offset = (target - anchor).days
    return (anchor + timedelta(days=(offset // 7) * 7)).isoformat()


def week_days(week_start: str) -> List[str]:
    start = _parse_day(week_start)
    return [(start + timedelta(days=i)).isoformat() for i in range(7)]


def split_week(actions_per_week: int, week_start: str,
               action_weights: Optional[Dict[str, float]] = None
               ) -> Dict[str, Dict[str, int]]:
    """把周计划摊到 7 天，保证总和恰等于 actions_per_week。

    无权重：base = plan // 7，余数从第 1 天起每天 +1（20 → 3,3,3,3,3,3,2）。
    有权重：先按最大余数法把总数拆到动作，再把每个动作的量摊到 7 天。
    """
    total = int(actions_per_week)
    if total < 0:
        raise ScheduleError("周计划量不能为负")
    days = week_days(week_start)
    weights = {str(k): float(v) for k, v in (action_weights or {"reply": 1.0}).items()
               if float(v) > 0}
    if not weights:
        raise ScheduleError("动作权重为空")
    weight_sum = sum(weights.values())

    # 1) 总数按权重拆到动作：先取整，余数给小数部分最大的动作
    exact = {act: total * w / weight_sum for act, w in weights.items()}
    per_action = {act: int(v) for act, v in exact.items()}
    remainder = total - sum(per_action.values())
    order = sorted(exact, key=lambda a: (-(exact[a] - per_action[a]), a))
    for act in order[:max(0, remainder)]:
        per_action[act] += 1

    # 2) 每个动作摊到 7 天：base + 前 r 天 +1
    out: Dict[str, Dict[str, int]] = {day: {} for day in days}
    for act, amount in per_action.items():
        base, extra = divmod(amount, len(days))
        for i, day in enumerate(days):
            out[day][act] = base + (1 if i < extra else 0)
    return out


def parse_window(window: Any) -> Optional[Tuple[Optional[str], List[Tuple[float, float]]]]:
    """解析 "20:00-23:00@us-west"；空值返回 None（不限窗）。"""
    if not window:
        return None
    text = str(window).strip()
    if not text:
        return None
    audience = None
    if "@" in text:
        text, audience = text.rsplit("@", 1)
        audience = audience.strip() or None
    windows: List[Tuple[float, float]] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" not in part:
            raise ScheduleError(f"时间窗格式应为 HH:MM-HH:MM[@受众]：{window!r}")
        lo_s, hi_s = part.split("-", 1)
        lo, hi = _parse_hour(lo_s), _parse_hour(hi_s)
        windows.append((lo, hi))
    if not windows:
        return None
    return audience, windows


def _parse_hour(value: str) -> float:
    value = value.strip()
    if ":" in value:
        hh, mm = value.split(":", 1)
        return int(hh) + int(mm) / 60.0
    return float(value)


def _local_hour(now_utc: datetime, audience: Optional[str]) -> float:
    key = audience or DEFAULT_AUDIENCE
    spec = tw.AUDIENCES.get(key)
    if spec is None:
        raise ScheduleError(f"未知受众 {key}；可选：{sorted(tw.AUDIENCES)}")
    utc_hour = now_utc.hour + now_utc.minute / 60.0
    return (utc_hour + float(spec["tz"])) % 24


def in_dead_zone(audience: str, now_utc: datetime) -> bool:
    """受众本地 00:00–06:00 为死区（11-05 尸检：凌晨发帖零互动）。"""
    return tw.in_dead_zone(_local_hour(now_utc, audience))


def window_allows(window: Any, now_utc: datetime) -> bool:
    """窗口外不放行；窗口内但落在死区也不放行；空窗口视为不限。"""
    parsed = parse_window(window)
    if parsed is None:
        return True
    audience, windows = parsed
    local = _local_hour(now_utc, audience)
    if tw.in_dead_zone(local):
        return False
    return any(lo <= local < hi for lo, hi in windows)


def item_due(item: Dict[str, Any], *, today: str, now_utc: datetime,
             allow_out_of_window: bool = False) -> bool:
    """条目是否该在本轮发：排期已到且窗口允许。"""
    scheduled = str(item.get("scheduled_for") or today)
    if scheduled > today:
        return False
    if allow_out_of_window:
        return True
    return window_allows(item.get("window"), now_utc)


def supply_status(queues: Sequence[Dict[str, Any]], *, today: str, days: int = 3,
                  daily_quota: int, committed_targets: Optional[set] = None,
                  done_today: int = 0
                  ) -> Dict[str, Any]:
    """未来 days 天的供给水位与缺口。

    queues: [{"path": str, "queue": {...}}, ...]
    committed_targets: 已 committed 的 target/id 集合（不再计入供给）。
    done_today: 今天已 committed 完成的条数——已完成的配额算今天的覆盖，
    不再被 committed_targets 的排除误报成「今天缺 1 条」。
    """
    horizon = max(1, int(days))
    quota = max(0, int(daily_quota))
    done = set(committed_targets or ())
    start = _parse_day(today)
    by_day: Dict[str, int] = {}
    staged = 0
    for entry in queues:
        queue = entry.get("queue") if isinstance(entry, dict) and "queue" in entry \
            else (entry or {})
        for item in (queue or {}).get("items") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("target") or item.get("id") or "")
            if key and key in done:
                continue
            scheduled = str(item.get("scheduled_for") or today)
            if scheduled < today:
                continue  # 过期未发不占未来供给，交给缺口暴露
            if scheduled >= (start + timedelta(days=horizon)).isoformat():
                continue
            by_day[scheduled] = by_day.get(scheduled, 0) + 1
            staged += 1
    if int(done_today) > 0:
        by_day[today] = by_day.get(today, 0) + int(done_today)
    coverage = 0
    gap = 0
    for i in range(horizon):
        day = (start + timedelta(days=i)).isoformat()
        have = by_day.get(day, 0)
        gap += max(0, quota - have)
        if have >= quota and all(
                by_day.get((start + timedelta(days=j)).isoformat(), 0) >= quota
                for j in range(i + 1)):
            coverage = i + 1
    note = ("供给充足" if gap == 0 else
            f"未来 {horizon} 天缺 {gap} 条（日配额 {quota}）")
    return {"staged": staged, "upcoming": staged, "coverage_days": coverage,
            "gap": gap, "daily_quota": quota, "horizon_days": horizon,
            "by_day": by_day, "note": note}


def next_window_start(window: Any, now_utc: datetime) -> Optional[datetime]:
    """下一次窗口开启的 UTC 时间；不限窗返回 None。用于「等到窗口再发」的提示。"""
    parsed = parse_window(window)
    if parsed is None:
        return None
    audience, windows = parsed
    for add_minutes in range(0, 24 * 60, 5):
        candidate = now_utc + timedelta(minutes=add_minutes)
        if window_allows(window, candidate):
            return candidate
    return None
