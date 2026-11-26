"""夜间互动循环：读通知回回复 → 实时选路 → 额度与间隔 → 逐条写。

分工：
  inbox_replies  只读收集通知里的回复（不发送）
  engagement_cycle（本模块）排序、限流、调度，逐条调用 action_runner
  action_runner  统一回执、任务结果与操作日志，选择 browser-attach 传输
  browser_write  owner-attested 单条门禁 + 执行 + 回读 + 账本（不重复记账）

三条硬约束贯穿全模块：
  1. 只有回读命中的 committed 才算成功；失败/未知不重试。
  2. 每个外部对象每夜最多 per_actor_cap 次动作（默认 2）。
  3. 平台出现风控信号立即停该渠道，不换马甲、不降间隔。

一条可见性约束：X 时间线与发帖框只在可见标签页渲染（后台标签页或锁屏时
visibilityState 为 hidden）。browser_write 对 X 抛 tab_hidden 时，本模块把
整个 X 渠道推迟到后续波次：不发送、不占额度、不写账本，队列原样保留；
Discord 与 GitHub 不受影响。
"""
from __future__ import annotations

import datetime as _dt
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .ledger import Ledger, TARGET_BLOCKED
from .throttle import Throttle
from . import content_rules
from . import supply_pool

DEFAULT_POLICY = {
    "window": {"start_hour": 22, "end_hour": 7, "waves_per_night": 9},
    "platforms": {
        "x": {
            "publish": {"min_gap_sec": 90, "per_hour": 10, "per_night": 90},
            "reply": {"min_gap_sec": 90, "per_hour": 20, "per_night": 180},
        },
        "github": {"reply": {"min_gap_sec": 20, "per_hour": 15, "per_night": 135}},
        "discord": {"publish": {"min_gap_sec": 25, "per_hour": 9, "per_night": 108},
                    "reply": {"min_gap_sec": 25, "per_hour": 6, "per_night": 60}},
    },
    "interleave": {"priority": ["x", "github", "discord"], "min_gap_sec": 15,
                   "max_actions_per_wave": 51},
    "inbox": {"per_actor_cap": 2, "lookback_hours": 36, "auto_collect": False,
              "order": ["x", "github", "discord"]},
    "queue": {"max_age_hours": 12},
    "quota": {"hour_mode": "rolling"},
    "supply": {
        "wave_targets": {"x/publish": 10, "x/reply": 20,
                         "github/reply": 15, "discord/publish": 9},
        "prefetch": {"enabled": False, "lookahead_waves": 2,
                     "sources": ["github", "content"], "max_queries": 3,
                     "per_query": 10, "gap_sec": 15, "days": 14},
    },
    "channels": {"discord_allowed_ids": []},
    "bridge": {"session": ""},
    "wave_stops": {"cooldown_sec": 1800, "probe_actions": 1},
}


def _now(tz: Optional[_dt.tzinfo] = None) -> _dt.datetime:
    return _dt.datetime.now(tz or _dt.datetime.now().astimezone().tzinfo)


def _parse(value: Any, tz: Optional[_dt.tzinfo] = None) -> Optional[_dt.datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        got = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if got.tzinfo is None:
        got = got.replace(tzinfo=tz or _dt.datetime.now().astimezone().tzinfo)
    return got


def load_policy(data_dir) -> Dict[str, Any]:
    """读 data/policies/engagement-cycle.json；缺失字段回落到保守缺省。"""
    path = Path(data_dir) / "policies" / "engagement-cycle.json"
    got: Dict[str, Any] = {}
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                got = payload
        except (OSError, json.JSONDecodeError):
            got = {}
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    for key, value in got.items():
        if isinstance(value, dict) and isinstance(policy.get(key), dict):
            for sub, sub_value in value.items():
                if isinstance(sub_value, dict) and isinstance(policy[key].get(sub), dict):
                    policy[key][sub].update(sub_value)
                else:
                    policy[key][sub] = sub_value
        else:
            policy[key] = value
    return policy


def bridge_session(policy: Dict[str, Any]) -> str:
    """本轮使用的 WebBridge 会话名；空值表示沿用环境变量或默认 growovo。

    会话名决定能看见哪一组标签页。cycle attach 与 cycle run 必须用同一个会话，
    否则 attach 检查到的可见 X 页在执行时不在视野里，只会得到 tab_hidden。
    """
    return str(((policy.get("bridge") or {}).get("session")) or "").strip()


def _use_session(session: str) -> str:
    """把会话名落到 wb 模块全局；之后 BrowserAttach() 的无参构造会沿用它。"""
    name = str(session or "").strip()
    if name:
        from .bridge import wb
        wb.set_session(name)
    return name


def night_start(now: _dt.datetime, policy: Dict[str, Any]) -> Optional[_dt.datetime]:
    """当前时段所属夜间的开始时间；不在窗口内返回 None。"""
    window = policy.get("window") or {}
    if window.get("start_at") or window.get("end_at"):
        start = _parse(window.get("start_at"), now.tzinfo)
        end = _parse(window.get("end_at"), now.tzinfo)
        if not start or not end or end <= start:
            return None
        return start if start <= now < end else None
    windows = window.get("windows")
    if windows:
        starts = [night_start(now, {"window": row}) for row in windows]
        valid = [value for value in starts if value is not None]
        return max(valid) if valid else None
    start_hour = int((policy.get("window") or {}).get("start_hour", 22))
    end_hour = int((policy.get("window") or {}).get("end_hour", 7))
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if end_hour > start_hour:
        candidate = today.replace(hour=start_hour)
        return candidate if candidate <= now < today.replace(hour=end_hour) else None
    if now.hour >= start_hour:
        return today.replace(hour=start_hour)
    if now.hour < end_hour:
        return (today - _dt.timedelta(days=1)).replace(hour=start_hour)
    return None


def night_key(start: Optional[_dt.datetime]) -> str:
    return start.date().isoformat() if start else ""


def action_spec(policy: Dict[str, Any], channel: str, action: str) -> Optional[Dict[str, Any]]:
    return ((policy.get("platforms") or {}).get(channel) or {}).get(action)


def channel_gap(policy: Dict[str, Any], channel: str) -> int:
    """渠道级间隔取该渠道各动作 min_gap_sec 的最大值。"""
    rows = (policy.get("platforms") or {}).get(channel) or {}
    gaps = [int((row or {}).get("min_gap_sec") or 0) for row in rows.values()
            if isinstance(row, dict)]
    return max(gaps) if gaps else 60


def duration_stats(data_dir) -> Dict[str, Dict[str, Any]]:
    """从统一操作日志读取实测耗时；按 channel/action 做 EMA，坏读数不参与。"""
    from .operations_log import OperationsLog

    stats: Dict[str, Dict[str, Any]] = {}
    try:
        rows = OperationsLog(data_dir).all()
    except Exception:
        return {}
    for row in rows:
        if str(row.get("result") or "") in {"", "dry-run", "rejected", "skipped"}:
            continue
        channel = str(row.get("channel") or "")
        action = str(row.get("action") or "")
        started = _parse(row.get("started_at"), _dt.timezone.utc)
        finished = _parse(row.get("finished_at"), _dt.timezone.utc)
        if not channel or not action or not started or not finished:
            continue
        seconds = (finished - started).total_seconds()
        if seconds < 0 or seconds > 86400:
            continue
        key = "%s/%s" % (channel, action)
        old = stats.setdefault(key, {"samples": 0, "seconds": 0.0})
        old["seconds"] = (seconds if not old["samples"]
                          else round(old["seconds"] * 0.75 + seconds * 0.25, 3))
        old["samples"] += 1
    return stats


def expected_duration(durations: Dict[str, Any], channel: str, action: str) -> float:
    row = durations.get("%s/%s" % (channel, action)) or {}
    return max(0.0, float(row.get("seconds") or 0.0))


def choose_next(rows: List[Dict[str, Any]], policy: Dict[str, Any], *, now: _dt.datetime,
                last_channel: Dict[str, float], last_any: float,
                durations: Dict[str, Any]) -> Tuple[int, float, Dict[str, Any]]:
    """在硬间隔内实时选下一条：最早可执行，通知优先，同分取实测较短任务。"""
    global_gap = int((policy.get("interleave") or {}).get("min_gap_sec") or 0)
    best: Optional[Tuple[int, float, Dict[str, Any]]] = None
    for index, raw in enumerate(rows):
        row = _normalize(raw)
        channel = row["channel"]
        previous = float(last_channel.get(channel) or 0.0)
        channel_ready = previous + channel_gap(policy, channel) if previous else 0.0
        global_ready = float(last_any) + global_gap if last_any else 0.0
        ready = max(channel_ready, global_ready)
        decision = {"channel": channel, "action": row["action"],
                    "target": row["target"], "ready_at": ready,
                    "wait_sec": max(0, int(ready - now.timestamp())),
                    "expected_duration_sec": expected_duration(
                        durations, channel, row["action"]),
                    "reason": "earliest_ready"}
        if best is None:
            best = (index, ready, decision)
            continue
        old_index, old_ready, old = best
        if ready < old_ready:
            best = (index, ready, decision)
            continue
        if ready > old_ready:
            continue
        inbox = row["source"] == "inbox"
        old_inbox = str(rows[old_index].get("source") or "") == "inbox"
        if inbox != old_inbox:
            decision["reason"] = "inbox_first"
            if inbox:
                best = (index, ready, decision)
            continue
        duration = decision["expected_duration_sec"]
        old_duration = old["expected_duration_sec"]
        if duration > 0 and (old_duration <= 0 or duration < old_duration):
            decision["reason"] = "shortest_observed_duration"
            best = (index, ready, decision)
    if best is None:
        raise ValueError("没有可选择的队列行")
    return best


def hour_mode(policy: Dict[str, Any]) -> str:
    """整点小时用于对齐小时波次；未配置时保留滚动 60 分钟。"""
    configured = str((policy.get("quota") or {}).get("hour_mode") or "rolling")
    return "calendar" if configured == "calendar" else "rolling"


def usage(ledger: Ledger, policy: Dict[str, Any], *, now: _dt.datetime) -> Dict[str, Any]:
    """从账本统计当前夜间与小时窗口的已用额度，以及每对象动作数。"""
    start = night_start(now, policy)
    tz = now.tzinfo
    hour_start = now.replace(minute=0, second=0, microsecond=0) \
        if hour_mode(policy) == "calendar" else now - _dt.timedelta(hours=1)
    out = {
        "night": {}, "hour": {}, "last": {}, "actors": {},
        "by_key_actors": {}, "night_start": start.isoformat() if start else "",
    }
    for row in ledger.all():
        if str(row.get("transport") or "") not in {"", "browser-attach"}:
            continue
        if str(row.get("result") or "") != "committed":
            continue
        channel = str(row.get("channel") or "")
        action = str(row.get("action") or "")
        if not action_spec(policy, channel, action):
            continue
        when = _parse(row.get("ts"), tz)
        if not when:
            continue
        key = "%s/%s" % (channel, action)
        if start and when >= start:
            out["night"][key] = out["night"].get(key, 0) + 1
            actor = str(row.get("actor") or "").strip().lower()
            if actor and action == "reply":
                out["actors"][actor] = out["actors"].get(actor, 0) + 1
                bucket = out["by_key_actors"].setdefault(key, {})
                bucket[actor] = bucket.get(actor, 0) + 1
        if when >= hour_start:
            out["hour"][key] = out["hour"].get(key, 0) + 1
        last = out["last"].get(key)
        if last is None or when > last:
            out["last"][key] = when
    return out


def quota_enforced(policy: Dict[str, Any]) -> bool:
    """配额上限是否拦截业务。

    Owner 2026-11-22：删除配额上限，配额计算的偏差不该让业务发不出去。
    策略里显式写 quota.enforce=false 时只记账、不拒绝；缺省保持旧行为，
    以便回滚和测试默认口径不变。
    """
    quota = policy.get("quota") or {}
    value = quota.get("enforce", True)
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off")
    return bool(value)


def budget_rows(policy: Dict[str, Any], *, used: Dict[str, Any],
                throttle=None) -> Dict[str, Dict[str, Any]]:
    """每个动作的夜间配置目标与今晚真正可达的目标。

    夜间目标必须装得进平台日额度：跨过本地午夜不等于平台重新给了一天的
    额度，所以 effective_per_night = min(per_night, daily_cap)。这样报告
    不会再拿 120/夜 去对比一个只有 48 额度的账号，制造虚假缺口。

    quota.enforce=false 时不再展示日额度与压减（只保留已用计数），
    effective 就是配置目标，避免报表继续用配额口径解释发不出去。
    """
    enforced = quota_enforced(policy)
    rows: Dict[str, Dict[str, Any]] = {}
    for channel, actions in (policy.get("platforms") or {}).items():
        for action, spec in (actions or {}).items():
            if not isinstance(spec, dict):
                continue
            key = "%s/%s" % (channel, action)
            configured = int(spec.get("per_night") or 0)
            night_used = int((used.get("night") or {}).get(key, 0))
            day_cap = day_used = day_left = None
            if throttle is not None and enforced:
                try:
                    _, _, _, cap = throttle.policy(channel, action)
                    if cap is not None:
                        day_cap = int(cap)
                        day_used = int(throttle.daily_used(channel, action))
                        day_left = max(0, day_cap - day_used)
                except Exception:  # 只读展示：读不到就按没有日额度处理
                    day_cap = day_used = day_left = None
            effective = configured
            if configured and day_cap is not None:
                effective = min(configured, day_cap)
            elif day_cap is not None:
                effective = day_cap
            rows[key] = {
                "configured_per_night": configured,
                "effective_per_night": effective,
                "capped_by_daily_budget": bool(configured and day_cap is not None
                                               and configured > day_cap),
                "night_used": night_used,
                # 非强制配额时 night_left 只表示“距配置目标还差多少”，
                # 不能作为 plan 的压减依据，否则删掉配额上限只是表面生效。
                "night_left": max(0, effective - night_used) if enforced else None,
                "day_cap": day_cap,
                "day_used": day_used,
                "day_left": day_left,
            }
    return rows


def safety_rows(policy: Dict[str, Any], *, used: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Independent hard safety caps; quota.enforce=false never disables this layer."""
    quota = policy.get("quota") or {}
    safety = quota.get("safety") or {}
    enforced = bool(safety.get("enforced", False))
    caps = safety.get("per_night") or {}
    rows = {}
    for channel, actions in (policy.get("platforms") or {}).items():
        for action in (actions or {}):
            key = "%s/%s" % (channel, action)
            raw_cap = caps.get(key)
            cap = int(raw_cap) if raw_cap is not None else None
            consumed = int((used.get("night") or {}).get(key, 0))
            rows[key] = {
                "status": "enforced" if enforced and cap is not None else (
                    "missing" if enforced else "disabled"),
                "enforced": enforced, "cap": cap, "used": consumed,
                "left": max(0, cap - consumed) if enforced and cap is not None else None,
            }
    return rows


def _queue_key(item: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (str(item.get("channel") or ""), str(item.get("action") or ""),
            str(item.get("target") or ""), str(item.get("text") or "")[:60])


def _normalize(item: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(item)
    row["channel"] = str(row.get("channel") or row.get("platform") or "").lower()
    row["action"] = str(row.get("action") or "reply").lower()
    row["target"] = str(row.get("target") or row.get("thread_ref") or "")
    row["text"] = str(row.get("text") or "").strip()
    row["actor"] = str(row.get("actor") or row.get("handle") or row.get("author") or "").strip()
    row["source"] = str(row.get("source") or "queue")
    return row


def _candidate_key(item: Dict[str, Any]) -> Tuple[str, str, str, str]:
    """回复按目标去重；原创按目标与正文去重，避免改文案后重复回复同一线程。"""
    row = _normalize(item)
    if row["action"] == "reply":
        return (row["channel"], row["action"], row["target"], "")
    return _queue_key(row)


def stable_id(item: Dict[str, Any]) -> str:
    """队列行稳定标识，与 _candidate_key 同语义：回复看目标，原创看目标+正文。

    回复行的 id 不随正文改动而变——补正文、改措辞之后仍是同一条动作记录，
    否则操作日志和账本会断成两行。原创按正文前 60 字区分，因为同一频道
    会连发多条不同内容。
    """
    row = _normalize(item)
    text_part = "" if row["action"] == "reply" else row["text"][:60]
    raw = "%s|%s|%s|%s" % (row["channel"], row["action"], row["target"], text_part)
    return "q:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


_CAMPAIGN_DATE = re.compile(r"night-(\d{4})(\d{2})(\d{2})")


def _row_queued_at(row: Dict[str, Any], tz: _dt.tzinfo) -> Optional[_dt.datetime]:
    """队列行入队时间：优先 queued_at；缺失时从 campaign 的 night-YYYYMMDD 按当天 00:00 兜底。

    兜底只服务旧队列行。新写入的行必须带 queued_at（ISO 8601，含时区），
    否则跨夜判断会以日期为粒度，凌晨新写的行也可能被当成前一天的。
    """
    got = _parse(row.get("queued_at"), tz)
    if got:
        return got
    match = _CAMPAIGN_DATE.match(str(row.get("campaign") or ""))
    if not match:
        return None
    try:
        return _dt.datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)),
                            tzinfo=tz)
    except ValueError:
        return None


def _stale(row: Dict[str, Any], *, tz: _dt.tzinfo, now: _dt.datetime,
           limit_sec: int) -> bool:
    """通知回复行永不超龄；其余行超过 limit_sec 即视为过时草稿。缺时间视为新鲜。"""
    if row.get("source") == "inbox":
        return False
    queued = _row_queued_at(row, tz)
    if not queued:
        return False
    return (now - queued).total_seconds() > limit_sec


def stale_rows(queue: List[Dict[str, Any]], policy: Dict[str, Any], *,
               now: _dt.datetime) -> List[Dict[str, Any]]:
    """返回队列里已过时的草稿（归一化后），供 run 丢弃、plan 拒绝。"""
    out: List[Dict[str, Any]] = []
    for raw in queue:
        row = _normalize(raw)
        if _stale(row, tz=now.tzinfo, now=now, limit_sec=_stale_limit(policy)):
            out.append(row)
    return out


def _stale_limit(policy: Dict[str, Any]) -> int:
    return int(float((policy.get("queue") or {}).get("max_age_hours", 12)) * 3600)


def _age_hours(row: Dict[str, Any], *, now: _dt.datetime) -> Optional[float]:
    queued = _row_queued_at(row, now.tzinfo)
    if not queued:
        return None
    return round((now - queued).total_seconds() / 3600, 1)


def _interleave_by_target(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一渠道的多目标行按目标轮转，避免整波只发一个目标。

    队列按批次写入时同一目标的正文常连成一段；顺序取行会让一波全落在
    第一个目标上。按目标轮流取一条，保持各目标内部原顺序。
    """
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for row in rows:
        key = str(row.get("target") or "")
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(row)
    out: List[Dict[str, Any]] = []
    while any(buckets[key] for key in order):
        for key in order:
            if buckets[key]:
                out.append(buckets[key].pop(0))
    return out


def _channel_holds(policy: Dict[str, Any]) -> Dict[str, _dt.datetime]:
    """频道暂停：到点前整个渠道不排程，不消耗队列、不计发送失败。"""
    out: Dict[str, _dt.datetime] = {}
    for name, stamp in (policy.get("channel_holds") or {}).items():
        try:
            when = _dt.datetime.fromisoformat(str(stamp))
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=_dt.timezone.utc)
        out[str(name)] = when
    return out


def plan(queue: List[Dict[str, Any]], policy: Dict[str, Any], *, ledger: Ledger,
         now: _dt.datetime, budget_sec: int = 3000, max_actions: int = 0,
         campaign: str = "", throttle=None, data_dir=None, accounts=None, runtime_gates=False) -> Dict[str, Any]:
    """把候选队列排成带 scheduled_at 的执行计划；纯计算，无副作用。

    throttle 传 Throttle 实例时，日预算不足的行在计划阶段就判
    daily_quota_exhausted：不排进波次，避免发到一半撞本地日上限。
    data_dir 传数据目录时读取集中目标清单，命中的行优先排程。
    """
    start = night_start(now, policy)
    from .effective_policy import EffectivePolicy
    effective_policy = EffectivePolicy.compile(policy, now, data_dir=data_dir)
    used = usage(ledger, policy, now=now)
    from .ledger_index import LedgerIndex
    ledger_index = LedgerIndex.build(ledger.all())
    cap = int((policy.get("inbox") or {}).get("per_actor_cap", 2))
    focus = content_rules.focus_targets(data_dir, policy) if data_dir is not None else {}
    allowed_dc = {str(x) for x in ((policy.get("channels") or {}).get("discord_allowed_ids") or [])}
    priority = [str(x) for x in ((policy.get("interleave") or {}).get("priority")
                                 or ["x", "github", "discord"])]
    limit = int(max_actions or (policy.get("interleave") or {}).get("max_actions_per_wave") or 0)
    enforced = quota_enforced(policy)
    targets = (policy.get("supply") or {}).get("wave_targets") or {}
    holds = _channel_holds(policy)
    safety = safety_rows(policy, used=used)

    scheduled: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    seen: set = set()
    last_at: Dict[str, float] = {}
    last_any = 0.0
    for key, when in (used.get("last") or {}).items():
        channel = key.split("/")[0]
        stamp = when.timestamp()
        last_at[channel] = max(last_at.get(channel, 0.0), stamp)
        last_any = max(last_any, stamp)
    global_gap = int((policy.get("interleave") or {}).get("min_gap_sec") or 0)
    planned: Dict[str, int] = {}
    actors: Dict[str, int] = dict(used["actors"])

    def _reject(row: Dict[str, Any], reason: str) -> None:
        skipped.append({"channel": row["channel"], "action": row["action"],
                        "target": row["target"], "reason": reason})
        effective_policy.record(row, effective_policy.from_legacy_reason(reason))

    def _candidate_rows() -> List[Dict[str, Any]]:
        inbox: List[Dict[str, Any]] = []
        rest: Dict[str, List[Dict[str, Any]]] = {name: [] for name in priority}
        for raw in queue:
            row = _normalize(raw)
            if row["source"] == "inbox":
                inbox.append(row)
            elif row["channel"] in rest:
                rest[row["channel"]].append(row)
        order = [str(x) for x in ((policy.get("inbox") or {}).get("order") or priority)]
        by_channel: Dict[str, List[Dict[str, Any]]] = {}
        for row in inbox:
            by_channel.setdefault(row["channel"], []).append(row)
        if focus:
            for name, items in by_channel.items():
                by_channel[name] = sorted(
                    items, key=lambda r: 0 if content_rules.in_focus(r, focus) else 1)
        chans = [c for c in order if c in by_channel] \
            + [c for c in by_channel if c not in order]
        mixed: List[Dict[str, Any]] = []
        while any(by_channel[c] for c in chans):
            for name in chans:
                if by_channel[name]:
                    mixed.append(by_channel[name].pop(0))
        if focus:
            for name in priority:
                rest[name] = sorted(rest.get(name) or [],
                                    key=lambda r: 0 if content_rules.in_focus(r, focus) else 1)
        if rest.get("discord"):
            rest["discord"] = _interleave_by_target(rest["discord"])
        # 非通知行按动作键逐条轮转：X 的 90 秒间隔足以让 GitHub/Discord
        # 插进空档，不会像整块轮转那样把非 X 渠道挤到 50 分钟预算之外。
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        key_order: List[str] = []
        for name in priority:
            for row in rest.get(name) or []:
                key = "%s/%s" % (row["channel"], row["action"])
                if key not in buckets:
                    buckets[key] = []
                    key_order.append(key)
                buckets[key].append(row)
        while any(buckets[key] for key in key_order):
            for key in key_order:
                if buckets[key]:
                    mixed.append(buckets[key].pop(0))
        return mixed

    from . import platform_compliance, scheduled_scope
    from .throttle import ThrottleError
    channel_accounts = accounts or {}
    scoped_throttles = {}
    runtime_blockers = {}
    if runtime_gates and data_dir is not None:
        for channel in policy.get("platforms", {}):
            if start is None:
                runtime_blockers[channel] = "outside_window"
            elif stop_state(data_dir, night_key(start), channel, now=now,
                            cooldown=int((policy.get("wave_stops") or {}).get("cooldown_sec", STOP_COOLDOWN_SEC))) == "open":
                runtime_blockers[channel] = "channel_stopped"
            elif not platform_compliance.owner_attested_active(data_dir, channel, now=now):
                runtime_blockers[channel] = "account_authorization_required"
    for row in _candidate_rows():
        channel = row["channel"]
        if channel in runtime_blockers:
            _reject(row, runtime_blockers[channel])
            continue
        account = channel_accounts.get(channel) or scheduled_scope.account(channel)
        row_throttle = throttle
        if account and data_dir is not None:
            if channel not in scoped_throttles:
                scoped_throttles[channel] = Throttle(Path(data_dir) / "state", account=account, strict=True)
            row_throttle = scoped_throttles[channel]
            try:
                row_throttle.validate_identity(channel, row["action"])
            except ThrottleError as exc:
                _reject(row, str(exc).split("：")[0])
                continue
        hold_until = holds.get(row["channel"])
        if hold_until and now < hold_until:
            _reject(row, "channel_hold")
            continue
        if _stale(row, tz=now.tzinfo, now=now, limit_sec=_stale_limit(policy)):
            _reject(row, "stale_row")
            continue
        if data_dir is not None and channel == "x" and row["target"]:
            from .protected_content import is_target_suppressed
            if is_target_suppressed(data_dir, row["target"]):
                _reject(row, "protected_account")
                continue
        spec = action_spec(policy, row["channel"], row["action"])
        if not spec:
            _reject(row, "unsupported")
            continue
        if not row["text"]:
            _reject(row, "text_empty")
            continue
        bad_content = content_rules.check_row(row, policy)
        if bad_content:
            _reject(row, bad_content)
            continue
        if row["action"] == "reply" and not row["target"]:
            _reject(row, "target_required")
            continue
        if row["channel"] == "discord":
            # 原生回复的 target 是 channel_id:message_id，按频道 ID 判白名单。
            dc_id = str(row.get("channel_id") or str(row["target"]).split(":")[0])
            if allowed_dc and dc_id not in allowed_dc:
                _reject(row, "channel_not_allowed")
                continue
        key = _candidate_key(row)
        if key in seen:
            _reject(row, "duplicate_in_queue")
            continue
        seen.add(key)

        lookup = "%s/%s" % (row["channel"], row["action"])
        if row["action"] == "reply" and row["target"] and ledger_index.blocked(
                row["channel"], row["target"]):
            _reject(row, "target_isolated")
            continue
        if row["action"] == "reply" and ledger_index.committed(row["channel"], row["target"]):
            _reject(row, "already_done")
            continue
        night_used = used["night"].get(lookup, 0) + planned.get(lookup, 0)
        if (enforced or account) and night_used >= int(spec.get("per_night") or 0):
            _reject(row, "night_quota_exhausted")
            continue
        hour_used = used["hour"].get(lookup, 0) + planned.get(lookup, 0)
        if (enforced or account) and hour_used >= int(spec.get("per_hour") or 0):
            _reject(row, "hour_quota_exhausted")
            continue
        if (enforced or account) and row_throttle is not None:
            left = row_throttle.remaining(row["channel"], row["action"])
            quota_actions = getattr(row_throttle, "quota_actions", None)
            shared_actions = (quota_actions(row["channel"], row["action"])
                              if callable(quota_actions) else [row["action"]])
            if not isinstance(shared_actions, (list, tuple, set)):
                shared_actions = [row["action"]]
            planned_shared = sum(
                planned.get("%s/%s" % (row["channel"], item), 0)
                for item in shared_actions)
            if left is not None and left - planned_shared <= 0:
                _reject(row, "daily_quota_exhausted")
                continue
        safety_row = safety.get(lookup) or {"status": "missing", "left": None}
        if safety_row["status"] == "missing":
            _reject(row, "safety_cap_missing")
            continue
        if safety_row["status"] == "enforced" and \
                int(safety_row["left"]) - planned.get(lookup, 0) <= 0:
            _reject(row, "safety_cap_exhausted")
            continue
        wave_target = int(targets.get(lookup) or 0)
        if wave_target and planned.get(lookup, 0) >= wave_target:
            # 波次构成目标（10/20/15/9），不是配额上限：先保证每个动作键拿到
            # 自己的份额，X 的 90 秒间隔才不会把 GitHub/Discord 挤出预算。
            _reject(row, "wave_target_met")
            continue
        actor_key = row["actor"].lower() if row["action"] == "reply" else ""
        if actor_key and row.get("source") != "inbox" and actors.get(actor_key, 0) >= cap:
            _reject(row, "actor_cap")
            continue
        if limit and len(scheduled) >= limit:
            _reject(row, "wave_cap")
            continue

        gap = channel_gap(policy, row["channel"])
        earliest = max(now.timestamp(), last_at.get(row["channel"], 0.0) + gap)
        if global_gap:
            earliest = max(earliest, last_any + global_gap)
        if runtime_gates and not night_start(_dt.datetime.fromtimestamp(earliest, tz=now.tzinfo), policy):
            _reject(row, "outside_window")
            continue
        if earliest - now.timestamp() > budget_sec:
            _reject(row, "wave_budget")
            continue
        at = _dt.datetime.fromtimestamp(earliest, tz=now.tzinfo)
        scheduled.append(dict(row, scheduled_at=at.isoformat(timespec="seconds"),
                              delay_sec=int(earliest - now.timestamp()),
                              quota_key=lookup))
        effective_policy.allow(row)
        last_at[row["channel"]] = earliest
        last_any = earliest
        planned[lookup] = planned.get(lookup, 0) + 1
        if actor_key:
            actors[actor_key] = actors.get(actor_key, 0) + 1

    queued_by_key: Dict[str, int] = {}
    for raw in queue:
        row = _normalize(raw)
        key = "%s/%s" % (row["channel"], row["action"])
        if action_spec(policy, row["channel"], row["action"]):
            queued_by_key[key] = queued_by_key.get(key, 0) + 1
    rejected_by_key: Dict[str, int] = {}
    for row in skipped:
        key = "%s/%s" % (row["channel"], row["action"])
        rejected_by_key[key] = rejected_by_key.get(key, 0) + 1
    action_keys = ["%s/%s" % (channel, action)
                   for channel, actions in (policy.get("platforms") or {}).items()
                   for action in (actions or {})]
    budget = budget_rows(policy, used=used, throttle=throttle)
    supply = {}
    for key in sorted(set(action_keys) | set(targets)):
        target = int(targets.get(key) or 0)
        queued = queued_by_key.get(key, 0)
        scheduled_count = planned.get(key, 0)
        row_budget = budget.get(key) or {}
        night_left = row_budget.get("night_left")
        day_left = row_budget.get("day_left")
        effective = target
        if night_left is not None:
            effective = min(effective, int(night_left))
        if day_left is not None:
            effective = min(effective, int(day_left))
        supply[key] = {
            "target": target, "configured": target, "effective": effective,
            "capped_by_daily_budget": bool(
                day_left is not None and int(day_left) < target),
            "capped_by_night_budget": bool(
                night_left is not None and int(night_left) < target),
            "queued": queued, "scheduled": scheduled_count,
            "rejected": rejected_by_key.get(key, 0),
            "gap": max(0, effective - scheduled_count),
            "configured_gap": max(0, target - scheduled_count),
            "unscheduled": max(0, queued - scheduled_count),
            "reasons": sorted({row["reason"] for row in skipped
                               if "%s/%s" % (row["channel"], row["action"]) == key}),
        }

    content = content_rules.summarize(scheduled, policy, data_dir=data_dir, focus=focus)
    content_failures = content_rules.gate_failures(content, policy)
    content_blocked = bool(content_failures)
    if content_blocked:
        for row in scheduled:
            _reject(row, "content_gate_blocked")
        scheduled = []
        planned = {}
        for key, item in supply.items():
            item.update(scheduled=0, gap=item["effective"], configured_gap=item["configured"],
                        unscheduled=item["queued"])
            if item["queued"]:
                item["reasons"] = sorted(set(item["reasons"]) | {"content_gate_blocked"})
    for key, row in safety.items():
        allocated = int(planned.get(key, 0))
        row["planned"] = allocated
        if row["left"] is not None:
            row["left"] = max(0, int(row["left"]) - allocated)
    return {
        "scheduled": scheduled,
        "skipped": skipped,
        "window": {"in_window": start is not None,
                   "night_start": start.isoformat() if start else "",
                   "night_key": night_key(start)},
        "usage": {key: {"night": value, "hour": used["hour"].get(key, 0)}
                  for key, value in used["night"].items()},
        "planned": planned,
        "supply": supply,
        "budget": budget,
        "safety": safety,
        "effective_policy": effective_policy.explain(),
        "content": content,
        "content_gate": {"enforced": bool((policy.get("content_rules") or {}).get("enforce", False)),
                         "blocked": content_blocked, "failures": content_failures},
        "content_text": content_rules.render(content),
    }


def load_queue(path) -> List[Dict[str, Any]]:
    """Compatibility facade: read the snapshot and replay local consume events."""
    from .queue_store import QueueStore
    return QueueStore(path).load()


def _atomic_write_text(path, text: str) -> Path:
    """Compatibility facade over the shared atomic storage port."""
    from .storage import atomic_write_text
    return atomic_write_text(path, text)


def save_queue(path, items: List[Dict[str, Any]]) -> Path:
    from .queue_store import QueueStore
    return QueueStore(path).save(items)


def _lock_path(data_dir) -> Path:
    return Path(data_dir) / "state" / "engagement-cycle.lock"


def _merge_inbox_rows(queue_path, fresh, *, data_dir=None, now=None) -> Dict[str, Any]:
    path = Path(queue_path)
    existing = load_queue(path)
    merged = [dict(row) for row in existing]
    known = {_candidate_key(row) for row in merged}
    terminal_skipped = 0
    terminal_ids = set()
    if data_dir is not None:
        from . import inbox_tracking
        terminal_ids = inbox_tracking.terminal_identities(data_dir)
    for raw in fresh:
        row = _normalize(raw)
        if data_dir is not None and inbox_tracking.identity(row) in terminal_ids:
            terminal_skipped += 1
            continue
        key = _candidate_key(row)
        if key in known:
            for old in merged:
                if _candidate_key(old) == key:
                    if not old.get("text"):
                        old.setdefault("their_text", row.get("their_text") or "")
                        old.setdefault("actor", row.get("actor") or "")
                    old.setdefault("id", row.get("id") or stable_id(old))
            continue
        known.add(key)
        row.setdefault("id", stable_id(row))
        merged.append(row)
    save_queue(path, merged)
    if data_dir is not None:
        from . import inbox_tracking
        inbox_tracking.observe(data_dir, merged, now=now)
    return {"ok": True, "count": len(fresh), "items": fresh,
            "queue_path": str(path), "queue_size": len(merged),
            "terminal_skipped": terminal_skipped,
            "next": "用 cycle inbox-update 补 text 或署名忽略，再跑 cycle plan"}


def _inbox_identity(row: Dict[str, Any]) -> Tuple[Any, ...]:
    normalized = _normalize(row)
    if normalized["source"] == "inbox" and normalized.get("thread_ref"):
        return (normalized["channel"], normalized["action"],
                str(normalized.get("thread_ref") or ""))
    return _candidate_key(normalized)


def _fill_inbox_rows(queue_path, drafts, *, data_dir=None, now=None) -> Dict[str, Any]:
    if isinstance(drafts, dict):
        drafts = drafts.get("items") or []
    if not isinstance(drafts, list):
        raise ValueError("inbox_writer 必须返回草稿数组或 items 数组")
    path = Path(queue_path)
    existing = load_queue(path)
    written = 0
    for raw in drafts:
        if not isinstance(raw, dict) or not str(raw.get("text") or "").strip():
            continue
        identity = _inbox_identity(raw)
        for old in existing:
            if (str(old.get("source") or "") == "inbox"
                    and not str(old.get("text") or "").strip()
                    and _inbox_identity(old) == identity):
                old["text"] = str(raw.get("text") or "").strip()
                written += 1
                break
    if written:
        save_queue(path, existing)
        if data_dir is not None:
            from . import inbox_tracking
            inbox_tracking.observe(data_dir, existing, now=now)
    return {"written": written, "queue_size": len(existing)}


def merge_inbox(data_dir, fresh, *, queue_path=None) -> Dict[str, Any]:
    """在同一执行锁内合并通知草稿；已有正文不覆盖，同一线程不重复。"""
    data_dir = Path(data_dir)
    lock_file = _lock_path(data_dir)
    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_file.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return {"ok": False, "reason": "wave_already_running"}
    except OSError as exc:
        return {"ok": False, "reason": "wave_lock_unavailable", "error": str(exc)}
    try:
        path = Path(queue_path) if queue_path else data_dir / "state" / "engagement-queue.json"
        return _merge_inbox_rows(path, fresh, data_dir=data_dir)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def update_inbox(data_dir, updates, *, queue_path=None, by: str = "", now=None) -> Dict[str, Any]:
    """Fill or dismiss existing blank inbox rows under the wave lock; never sends."""
    data_dir = Path(data_dir)
    now = now or _now()
    if not str(by or "").strip():
        raise ValueError("入站补写/忽略必须署名（--by）")
    if isinstance(updates, dict):
        updates = updates.get("items") or []
    if not isinstance(updates, list):
        raise ValueError("入站更新必须是 JSON 数组或包含 items 数组的对象")
    lock_file = _lock_path(data_dir)
    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_file.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return {"ok": False, "reason": "wave_already_running"}
    except OSError as exc:
        return {"ok": False, "reason": "wave_lock_unavailable", "error": str(exc)}
    try:
        path = Path(queue_path) if queue_path else data_dir / "state" / "engagement-queue.json"
        existing = load_queue(path)
        policy = load_policy(data_dir)
        written_rows = []
        dismissed_rows = []
        rejected = []
        for index, raw in enumerate(updates):
            if not isinstance(raw, dict):
                rejected.append({"index": index, "reason": "row_not_object"})
                continue
            target = str(raw.get("target") or raw.get("thread_ref") or "").strip()
            supplied_id = str(raw.get("id") or raw.get("queue_item_id") or "").strip()
            matches = []
            for old in existing:
                if str(old.get("source") or "") != "inbox" or str(old.get("text") or "").strip():
                    continue
                if supplied_id and str(old.get("id") or "") == supplied_id:
                    matches.append(old)
                    continue
                old_target = str(old.get("target") or old.get("thread_ref") or "")
                if target and target not in (old_target, str(old.get("thread_ref") or "")):
                    continue
                if raw.get("channel") and str(raw.get("channel")).lower() != str(old.get("channel") or ""):
                    continue
                if raw.get("action") and str(raw.get("action")).lower() != str(old.get("action") or "reply"):
                    continue
                if target:
                    matches.append(old)
            if len(matches) != 1:
                rejected.append({"index": index, "target": target,
                                 "reason": "not_blank_inbox" if len(matches) == 0 else "ambiguous_inbox"})
                continue
            old = matches[0]
            text = str(raw.get("text") or "").strip()
            has_dismiss = "not_replied_reason" in raw
            dismiss_reason = str(raw.get("not_replied_reason") or "").strip()
            if text and has_dismiss:
                rejected.append({"index": index, "target": target, "reason": "ambiguous_update"})
                continue
            if has_dismiss and not dismiss_reason:
                rejected.append({"index": index, "target": target, "reason": "dismiss_reason_required"})
                continue
            if not text and not has_dismiss:
                rejected.append({"index": index, "target": target, "reason": "update_required"})
                continue
            if has_dismiss:
                dismissed_rows.append((old, dismiss_reason))
                continue
            candidate = dict(old, text=text)
            reason = content_rules.check_row(candidate, policy)
            if reason:
                rejected.append({"index": index, "target": target, "reason": reason})
                continue
            old["text"] = text
            written_rows.append(old)
        if written_rows or dismissed_rows:
            dismissed_ids = {id(old) for old, _ in dismissed_rows}
            remaining = [old for old in existing if id(old) not in dismissed_ids]
            save_queue(path, remaining)
            from . import inbox_tracking
            for old in written_rows:
                inbox_tracking.mark_updated(data_dir, old, "drafted", by=by, now=now)
            for old, reason in dismissed_rows:
                inbox_tracking.mark_updated(data_dir, old, "dismissed", by=by,
                                            reason=reason, now=now)
            existing = remaining
        return {"ok": True, "written": len(written_rows),
                "dismissed": len(dismissed_rows), "rejected": rejected,
                "queue_path": str(path), "queue_size": len(existing),
                "by": by, "sent": 0}
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def enqueue(data_dir, drafts, *, queue_path=None, now=None) -> Dict[str, Any]:
    """校验并原子合并草稿；可补全 inbox 空白行，不执行任何平台动作。"""
    data_dir = Path(data_dir)
    now = now or _now()
    if isinstance(drafts, dict):
        drafts = drafts.get("items") or []
    if not isinstance(drafts, list):
        raise ValueError("草稿必须是 JSON 数组或包含 items 数组的对象")
    lock_file = _lock_path(data_dir)
    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_file.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return {"ok": False, "reason": "wave_already_running"}
    except OSError as exc:
        return {"ok": False, "reason": "wave_lock_unavailable", "error": str(exc)}
    try:
        path = Path(queue_path) if queue_path else data_dir / "state" / "engagement-queue.json"
        existing = load_queue(path)
        known = {_candidate_key(row) for row in existing}
        policy = load_policy(data_dir)
        allowed_dc = {str(x) for x in ((policy.get("channels") or {}).get(
            "discord_allowed_ids") or [])}
        added: List[Dict[str, Any]] = []
        filled: List[Dict[str, Any]] = []
        duplicates: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        for idx, raw in enumerate(drafts):
            if not isinstance(raw, dict):
                rejected.append({"index": idx, "reason": "row_not_object"})
                continue
            row = _normalize(raw)
            reason = ""
            if not action_spec(policy, row["channel"], row["action"]):
                reason = "unsupported"
            elif not row["text"]:
                reason = "text_empty"
            elif row["action"] == "reply" and not row["target"]:
                reason = "target_required"
            elif row["channel"] == "discord" and (
                    (row["action"] == "reply" and not re.fullmatch(r"\d+:\d+", row["target"]))
                    or (row["action"] != "reply" and not row["target"].isdigit())):
                reason = "target_invalid"
            elif row["channel"] == "discord" and allowed_dc and row["target"] not in allowed_dc:
                reason = "channel_not_allowed"
            else:
                reason = content_rules.check_row(row, policy)
            if reason:
                rejected.append({"index": idx, "channel": row["channel"],
                                 "action": row["action"], "reason": reason})
                continue
            row["queued_at"] = now.isoformat(timespec="seconds")
            row.setdefault("id", stable_id(row))
            key = _candidate_key(row)
            old_idx = next((i for i, old in enumerate(existing)
                            if _candidate_key(old) == key), None)
            if old_idx is not None and not str(existing[old_idx].get("text") or "").strip():
                merged = dict(existing[old_idx])
                merged.update(row)
                merged["source"] = str(existing[old_idx].get("source") or row["source"])
                merged["id"] = str(existing[old_idx].get("id") or row["id"])
                existing[old_idx] = merged
                filled.append(merged)
                continue
            if key in known:
                duplicates.append({"index": idx, "channel": row["channel"],
                                   "action": row["action"], "target": row["target"],
                                   "reason": "duplicate_in_queue"})
                continue
            known.add(key)
            existing.append(row)
            added.append(row)
        if added or filled:
            save_queue(path, existing)
            from . import inbox_tracking
            inbox_tracking.observe(data_dir, added + filled, now=now)
        return {"ok": True, "added": len(added), "filled": len(filled),
                "duplicates": duplicates, "rejected": rejected,
                "queue_path": str(path), "queue_size": len(existing),
                "items": added + filled}
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def stop_path(data_dir, night: str, channel: str) -> Path:
    return Path(data_dir) / "state" / "stops" / ("%s-%s.json" % (night, channel))


STOP_COOLDOWN_SEC = 1800
STOP_PROBE_ACTIONS = 1


def is_stopped(data_dir, night: str, channel: str) -> bool:
    return stop_path(data_dir, night, channel).is_file()


def stop_record(data_dir, night: str, channel: str) -> Optional[Dict[str, Any]]:
    path = stop_path(data_dir, night, channel)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _rate_limit_open(record):
    if "manual_resume_required" in record:
        return bool(record["manual_resume_required"])
    return bool(re.search(r"\b429\b", str(record.get("reason") or "")))


def stop_state(data_dir, night: str, channel: str, *, now: Optional[_dt.datetime] = None,
               cooldown: int = STOP_COOLDOWN_SEC) -> str:
    """closed=没停过；open=仍在冷却；half_open=冷却已过，可放一条试探。

    停止文件没有可读时间戳时按 open 处理：无法证明冷却已过就继续停，
    宁可少发，不拿真账号试错。
    """
    # Outstanding unknown writes survive the calendar boundary, including legacy records.
    from . import scheduled_scope
    account = scheduled_scope.account(channel)
    history = stop_path(data_dir, night, channel).parent
    for previous in sorted(history.glob("????-??-??-%s.json" % channel)):
        previous_night = previous.name[:10]
        if previous_night >= night:
            continue
        older = stop_record(data_dir, previous_night, channel)
        if older and account and older.get("account") not in {None, account}:
            continue
        if older and _rate_limit_open(older):
            return "open"
        if older and older.get("requires_human_resolution"):
            ref = older.get("unresolved_ref")
            if not ref or not any(row.get("ref") == ref and row.get("result") in {"committed", "failed", "rejected"}
                                  for row in Ledger(Path(data_dir) / "ledger.jsonl").results()):
                return "open"
    record = stop_record(data_dir, night, channel)
    if record is None or (account and record.get("account") not in {None, account}):
        return "closed"
    if _rate_limit_open(record):
        return "open"
    if record.get("requires_human_resolution"):
        ref = str(record.get("unresolved_ref") or "")
        if not ref:
            return "open"
        rows = Ledger(Path(data_dir) / "ledger.jsonl").results()
        original = next((row for row in rows if row.get("ref") == ref), None)
        if original is None or original.get("result") == "indeterminate":
            return "open"
    at = _parse(record.get("at"))
    if at is None:
        return "open"
    when = now or _now()
    if (when - at).total_seconds() >= max(0, int(cooldown)):
        return "half_open"
    return "open"


def acknowledge_rate_limit(data_dir, night, channel, *, by, evidence):
    _dt.date.fromisoformat(night)
    if channel not in {"x", "github", "discord"} or not by.strip() or not evidence.strip():
        raise ValueError("需要原停止日期、渠道、负责人和账号恢复证据")
    from .storage import FileLock
    with FileLock(_lock_path(data_dir)):
        record = stop_record(data_dir, night, channel) or {}
        if record.get("requires_human_resolution") or not _rate_limit_open(record):
            raise ValueError("只能处置具名限流记录，未知回执需另行裁决")
        record.update(manual_resume_required=False, resumed_by=by, resume_evidence=evidence,
                      resumed_at=_now().isoformat())
        _atomic_write_text(stop_path(data_dir, night, channel), json.dumps(record, ensure_ascii=False))
    return {"ok": True, "writes": 0, "channel": channel, "night": night}


def clear_stopped(data_dir, night: str, channel: str) -> bool:
    try:
        stop_path(data_dir, night, channel).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def mark_stopped(data_dir, night: str, channel: str, reason: str,
                 requires_human_resolution: bool = False, unresolved_ref: str = "") -> Path:
    path = stop_path(data_dir, night, channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"channel": channel, "night": night, "reason": reason,
               "at": _now().isoformat(timespec="seconds")}
    from . import scheduled_scope
    if scheduled_scope.account(channel):
        payload["account"] = scheduled_scope.account(channel)
    if re.search(r"\b429\b", reason):
        payload["manual_resume_required"] = True
    if requires_human_resolution:
        payload["requires_human_resolution"] = True
        payload["unresolved_ref"] = unresolved_ref
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False))
    return path


def run(data_dir, *, go: bool = False, queue_path=None, max_actions: int = 0,
        wave_id: str = "", budget_sec: int = 3000, session: str = "",
        prefetch: Optional[bool] = None, auto_inbox: Optional[bool] = None,
        preflight: Optional[bool] = None, inbox_writer=None,
        log=print, now=None) -> Dict[str, Any]:
    """串行执行真实波次；dry-run 保持只读且不占用执行锁。"""
    if not go:
        return _run(data_dir, go=False, queue_path=queue_path, max_actions=max_actions,
                    wave_id=wave_id, budget_sec=budget_sec, session=session,
                    prefetch=prefetch, auto_inbox=auto_inbox, preflight=preflight,
                    inbox_writer=inbox_writer, log=log, now=now)
    lock_file = _lock_path(data_dir)
    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_file.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return {"ok": False, "reason": "wave_already_running",
                    "lock_path": str(lock_file)}
    except OSError as exc:
        return {"ok": False, "reason": "wave_lock_unavailable", "error": str(exc)}
    try:
        return _run(data_dir, go=True, queue_path=queue_path, max_actions=max_actions,
                    wave_id=wave_id, budget_sec=budget_sec, session=session,
                    prefetch=prefetch, auto_inbox=auto_inbox, preflight=preflight,
                    inbox_writer=inbox_writer, log=log, now=now)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _run(data_dir, *, go: bool = False, queue_path=None, max_actions: int = 0,
         wave_id: str = "", budget_sec: int = 3000, session: str = "",
         prefetch: Optional[bool] = None, auto_inbox: Optional[bool] = None,
         preflight: Optional[bool] = None, inbox_writer=None,
         log=print, now=None) -> Dict[str, Any]:
    """执行一波：窗口 → 停手 → 计划 → 逐条 sleep 后发送 → 记波次摘要。"""
    from . import action_gateway
    run_action = action_gateway.execute
    from .browser_attach import BridgeError
    from .browser_write import ActionBlocked, StopChannel
    from . import inbox_tracking

    data_dir = Path(data_dir)
    from . import scheduled_scope
    policy = scheduled_scope.effective_policy(data_dir)
    if scheduled_scope.targets():
        policy.setdefault("supply", {})["wave_targets"] = scheduled_scope.targets()
    used_session = _use_session(session or bridge_session(policy))
    now = now or _now()
    start = night_start(now, policy)
    if not start:
        return {"ok": False, "reason": "outside_window", "now": now.isoformat(timespec="seconds")}
    night = night_key(start)
    wave = wave_id or now.strftime("%H%M")
    campaign = "night-%s-w%s" % (night, wave)
    from .run_manifest import SourceVersionChanged, assert_source_stable
    try:
        assert_source_stable(data_dir, night=night, source_dir=Path(__file__).resolve().parents[1])
    except SourceVersionChanged as exc:
        return {"ok": False, "reason": "source_version_changed",
                "expected_source_sha256": exc.expected, "actual_source_sha256": exc.actual,
                "night": night, "wave": wave, "campaign": campaign}
    from .runtime import RunContext
    context = RunContext.create(data_dir, campaign, wave, now=now,
                                metadata={"bridge_session": used_session})
    queue_file = queue_path or (data_dir / "state" / "engagement-queue.json")
    if go and scheduled_scope.requires_job(data_dir, queue_file):
        return {"ok": False, "reason": "scheduled_queue_requires_job"}
    if go and bool((policy.get("business") or {}).get("require_baseline", False)):
        from . import business_observations
        if not business_observations.has_baseline(data_dir, night):
            return {"ok": False, "reason": "business_baseline_missing",
                    "night": night, "wave": wave, "campaign": campaign,
                    "next": "先用 cycle observe --phase baseline --apply 导入带证据的基线"}
    readiness_queue = load_queue(queue_file)
    readiness_stale = stale_rows(readiness_queue, policy, now=now)
    readiness_stale_keys = {_queue_key(row) for row in readiness_stale}
    stops_config = policy.get("wave_stops") or {}
    cooldown = int(stops_config.get("cooldown_sec", STOP_COOLDOWN_SEC))
    stop_states = {name: stop_state(data_dir, night, name, now=now, cooldown=cooldown)
                   for name in (policy.get("platforms") or {})}
    if all(state == "open" for state in stop_states.values()):
        return {"ok": False, "reason": "all_channels_stopped", "night": night,
                "stop_states": stop_states}
    from . import platform_compliance
    readiness_x_attested = platform_compliance.owner_attested_active(data_dir, "x", now=now)
    readiness_eligible = [row for row in readiness_queue
                          if _queue_key(_normalize(row)) not in readiness_stale_keys
                          and str(row.get("channel") or row.get("platform") or "")
                          not in {name for name, state in stop_states.items() if state == "open"}
                          and not (str(row.get("channel") or row.get("platform") or "") == "x"
                                   and not readiness_x_attested)]
    readiness_plan = plan(
        readiness_eligible, policy, ledger=Ledger(data_dir / "ledger.jsonl"), now=now,
        budget_sec=budget_sec, max_actions=max_actions, campaign=campaign,
        throttle=Throttle(data_dir / "state"), data_dir=data_dir,
        runtime_gates=bool(scheduled_scope.targets()))
    ready_preview = bool(readiness_plan["scheduled"])
    inbox_config = policy.get("inbox") or {}
    standing_observation = auto_inbox is None and bool(inbox_config.get("auto_collect", False))
    wave_collection = auto_inbox is not None and bool(auto_inbox) and ready_preview
    inbox_enabled = go and (standing_observation or wave_collection)
    inbox_summary: Dict[str, Any] = {"auto_collect": inbox_enabled, "collected": 0,
                                     "written": 0, "pending_text": 0,
                                     "dm_count": 0, "dm_pending": [],
                                     "errors": {}, "coverage": {}}
    if inbox_enabled:
        report = collect_inbox_report(data_dir, session=used_session, log=log)
        merged = _merge_inbox_rows(queue_file, report["items"], data_dir=data_dir, now=now)
        dms = report.get("dm") or []
        pending_dms = [row for row in dms if row.get("pending_reply", True)]
        inbox_summary.update({"collected": len(report["items"]),
                              "errors": report.get("errors") or {},
                              "coverage": report.get("coverage") or {},
                              "queue_size": merged["queue_size"],
                              "dm_total": len(dms),
                              "dm_count": len(pending_dms),
                              "dm_pending": [
                                  {"author": row.get("author") or "",
                                   "thread_ref": row.get("thread_ref") or "",
                                   "their_text": str(row.get("their_text") or "")[:400],
                                   "received_at": row.get("received_at") or ""}
                                  for row in pending_dms]})
        if pending_dms:
            log("inbox/x_dm: %d 条私信待人工看，未进入自动发送队列" % len(pending_dms))
    if go and inbox_writer is not None:
        pending_inbox = [raw for raw in load_queue(queue_file)
                         if str(raw.get("source") or "") == "inbox"
                         and not str(raw.get("text") or "").strip()]
        filled = _fill_inbox_rows(queue_file, inbox_writer(pending_inbox),
                                  data_dir=data_dir, now=now)
        inbox_summary["written"] = filled["written"]
        inbox_summary["queue_size"] = filled["queue_size"]
    queue = load_queue(queue_file)
    inbox_summary["pending_text"] = sum(
        1 for raw in queue
        if str(raw.get("source") or "") == "inbox" and not str(raw.get("text") or "").strip())
    stale = stale_rows(queue, policy, now=now)
    stale_keys = {_queue_key(row) for row in stale}

    probe_actions = max(1, int(stops_config.get("probe_actions", STOP_PROBE_ACTIONS)))
    blocked = {name: state == "open" for name, state in stop_states.items()}
    if all(blocked.values()):
        return {"ok": False, "reason": "all_channels_stopped", "night": night,
                "stop_states": stop_states}
    half_open = {name for name, state in stop_states.items() if state == "half_open"}

    preflight_summary: Dict[str, Any] = {"enabled": False, "channels": {}}
    preflight_skipped: Dict[str, str] = {}
    if go and ready_preview and (bool((policy.get("wave_preflight") or {}).get("enabled", False))
               if preflight is None else bool(preflight)):
        from .wave_preflight import check as check_wave
        preflight_summary = check_wave(policy, session=used_session, log=log)
        preflight_summary["enabled"] = True
        for site, row in (preflight_summary.get("channels") or {}).items():
            if not row.get("ok"):
                preflight_skipped[site] = str(row.get("reason") or "unavailable")

    unusable = {name for name, stopped in blocked.items() if stopped}
    unusable.update(preflight_skipped)
    manifest_summary: Dict[str, Any] = {}
    if go:
        from . import run_manifest
        try:
            manifest_summary = run_manifest.write_manifest(
                data_dir, source_dir=Path(__file__).resolve().parents[1],
                queue_path=queue_file, policy=policy, night=night, wave=wave,
                campaign=campaign, session=used_session, preflight=preflight_summary, now=now,
                attempt_id=context.attempt_id)
        except (OSError, ValueError) as exc:
            return {"ok": False, "reason": "manifest_error", "error": str(exc),
                    "night": night, "wave": wave, "campaign": campaign}
    ledger = Ledger(data_dir / "ledger.jsonl")
    eligible = [row for row in queue
                if _queue_key(_normalize(row)) not in stale_keys
                and str(row.get("channel") or row.get("platform") or "") not in unusable]
    platform_skipped = []
    from . import platform_compliance
    x_attested = platform_compliance.owner_attested_active(data_dir, "x", now=now)
    for raw in list(eligible):
        row = _normalize(raw)
        if row["channel"] == "x" and not x_attested:
            eligible.remove(raw)
            platform_skipped.append({
                "channel": row["channel"], "action": row["action"],
                "target": row["target"], "reason": "account_authorization_required"})
    prepared = plan(eligible,
                    policy, ledger=ledger, now=now, budget_sec=budget_sec,
                    max_actions=max_actions, campaign=campaign,
                    throttle=Throttle(data_dir / "state"), data_dir=data_dir,
        runtime_gates=bool(scheduled_scope.targets()))
    prepared["skipped"].extend(platform_skipped)
    summary: Dict[str, Any] = {
        "ok": True, "wave": wave, "campaign": campaign, "night": night,
        "run_context": context.correlation(),
        "dry": not go, "scheduled_job_id": scheduled_scope.job_id(), "bridge_session": used_session,
        "planned": len(prepared["scheduled"]),
        "skipped": prepared["skipped"], "results": [], "stopped": {},
        "deferred": [], "deferred_channels": [],
        "pruned_stale": 0, "gone": 0,
        "stale": [{"channel": row["channel"], "action": row["action"],
                   "target": row["target"],
                   "age_hours": _age_hours(row, now=now)} for row in stale],
        "usage": prepared["usage"], "supply": prepared["supply"],
        "content": prepared["content"], "content_text": prepared["content_text"],
        "content_gate": prepared["content_gate"], "safety": prepared["safety"],
        "inbox": inbox_summary, "preflight": preflight_summary,
        "manifest": manifest_summary,
        "preflight_skipped": preflight_skipped,
        "preflight_skipped_rows": sum(
            1 for row in queue
            if str(row.get("channel") or row.get("platform") or "") in preflight_skipped),
        "stop_states": stop_states, "half_open": sorted(half_open),
        "stop_recovery": {},
        "started_at": now.isoformat(timespec="seconds"),
    }
    if go and prepared["content_gate"]["blocked"]:
        summary["ok"] = False
        summary["reason"] = "content_gate_blocked"
    if not go:
        summary["scheduled"] = [{"channel": row["channel"], "action": row["action"],
                                 "target": row["target"], "delay_sec": row["delay_sec"],
                                 "actor": row["actor"]} for row in prepared["scheduled"]]
        return summary

    dropped = set()
    deferred_channels: Dict[str, str] = {}
    deferred_rows: List[Dict[str, Any]] = []
    supply_updates: List[Tuple[str, str, str]] = []
    began = time.monotonic()
    deadline = began + max(0, int(budget_sec))
    budget_exhausted = False
    remaining = [dict(raw) for raw in queue]
    queue_dirty = False
    pruned = 0
    if stale_keys:
        kept = [raw for raw in remaining if _queue_key(_normalize(raw)) not in stale_keys]
        pruned = len(remaining) - len(kept)
        remaining = kept
        queue_dirty = True
        try:
            save_queue(queue_file, remaining)
        except OSError as exc:
            log("[queue] 丢弃超龄草稿后回写失败：%s" % exc)
    summary["pruned_stale"] = pruned

    def _consume(row: Dict[str, Any]) -> None:
        nonlocal remaining
        nonlocal queue_dirty
        queue_dirty = True
        from .queue_store import QueueStore
        try:
            remaining = QueueStore(queue_file).consume(remaining, row,
                                                       action_id=str(row.get("id") or ""))
            return
        except Exception as exc:
            log("[queue] 出队事件写入失败，尝试兼容快照回写：%s" % exc)
        key = _queue_key(_normalize(row))
        remaining = [raw for raw in remaining if _queue_key(_normalize(raw)) != key]
        try:
            save_queue(queue_file, remaining)
        except OSError as exc:
            log("[queue] 回写失败（不影响本条发送）：%s" % exc)

    def _defer(row: Dict[str, Any], reason: str) -> None:
        deferred_rows.append({"channel": row["channel"], "action": row["action"],
                              "target": row["target"], "actor": row["actor"],
                              "reason": reason})
        if str(row.get("source") or "") == "inbox":
            inbox_tracking.mark_updated(data_dir, row, "drafted", by="cycle",
                                        reason=reason)

    pending = [dict(row) for row in prepared["scheduled"]]
    probes_left = {name: probe_actions for name in half_open}
    if half_open:
        kept = []
        for item in pending:
            if item["channel"] in half_open:
                if probes_left.get(item["channel"], 0) <= 0:
                    # 冷却刚过：本波只放一条试探，其余留在队列等下一波。
                    _defer(item, "stop_probe_pending")
                    continue
                probes_left[item["channel"]] -= 1
            kept.append(item)
        pending = kept
    inbox_tracking.mark_planned(data_dir, pending, wave=wave, now=now)
    observed_durations = duration_stats(data_dir)
    previous = usage(ledger, policy, now=now)
    last_channel: Dict[str, float] = {}
    for key, when in (previous.get("last") or {}).items():
        channel = key.split("/")[0]
        last_channel[channel] = max(last_channel.get(channel, 0.0), when.timestamp())
    last_any = max(last_channel.values()) if last_channel else 0.0
    summary["scheduler"] = {"mode": "adaptive", "durations": observed_durations,
                            "decisions": []}

    worker = None
    prefetch_config = (policy.get("supply") or {}).get("prefetch") or {}
    prefetch_enabled = (bool(prefetch_config.get("enabled", False))
                        if prefetch is None else bool(prefetch))
    if prefetch_enabled:
        from .supply_prefetch import Prefetcher

        worker_policy = json.loads(json.dumps(policy))
        worker_policy.setdefault("supply", {}).setdefault(
            "prefetch", {})["enabled"] = True
        worker = Prefetcher(data_dir, worker_policy, ledger=ledger, log=log)
        summary["prefetch"] = worker.start()
    try:
        while pending:
            for item in list(pending):
                if item["channel"] in dropped:
                    pending.remove(item)
                elif item["channel"] in deferred_channels:
                    _defer(item, deferred_channels[item["channel"]])
                    pending.remove(item)
            if not pending:
                break
            if budget_exhausted or time.monotonic() >= deadline:
                budget_exhausted = True
                for item in pending:
                    _defer(item, "wave_budget")
                break
            index, ready_at, decision = choose_next(
                pending, policy, now=_now(), last_channel=last_channel,
                last_any=last_any, durations=observed_durations)
            row = pending.pop(index)
            wait = ready_at - _now().timestamp()
            if wait > 0:
                budget_left = deadline - time.monotonic()
                if wait >= budget_left:
                    if budget_left > 0:
                        time.sleep(budget_left)
                    budget_exhausted = True
                    _defer(row, "wave_budget")
                    continue
                time.sleep(wait)
            if time.monotonic() >= deadline:
                budget_exhausted = True
                _defer(row, "wave_budget")
                continue
            started = max(ready_at, _now().timestamp())
            last_channel[row["channel"]] = started
            last_any = started
            summary["scheduler"]["decisions"].append(dict(
                decision, started_at=_dt.datetime.fromtimestamp(
                    started, tz=now.tzinfo).isoformat(timespec="seconds")))
            try:
                try:
                    scheduled_scope.check_item(row)
                except scheduled_scope.ScopeError as exc:
                    summary.update(ok=False, reason=str(exc))
                    _defer(row, str(exc))
                    for outstanding in pending:
                        _defer(outstanding, str(exc))
                    break
                got = run_action(
                    channel=row["channel"], action=row["action"], data_dir=data_dir,
                    text=row["text"], target=row["target"], go=True, campaign=campaign,
                    actor=row["actor"], log=log, transport="browser-attach",
                    operator="cycle:%s" % wave, run_id=campaign,
                    attempt_id=context.attempt_id,
                    queue_item_id=str(row.get("id") or ""))
                state = got.get("result") or ("skipped" if got.get("skipped") else "unknown")
                ledger_state = got.get("ledger_result") or (
                    "committed" if state == "success" else state)
                engine = got.get("engine") or {}
                error_text = str(got.get("error") or engine.get("err") or "")[:300]
                gone = bool(got.get("gone")) or error_text == "post gone"
                candidate = str(row.get("supply_key") or "")
                if not candidate:
                    try:
                        candidate = supply_pool.candidate_key(row)
                    except Exception:  # 储备键只是追踪线索，推不出来就不追踪
                        candidate = ""
                summary["results"].append({"channel": row["channel"], "action": row["action"],
                                           "target": row["target"], "actor": row["actor"],
                                           "result": state,
                                           "ledger_result": got.get("ledger_result") or "",
                                           "reason": str(got.get("reason") or ""),
                                           "error": error_text,
                                           "gone": gone,
                                           "ok": bool(got.get("ok")),
                                           "ref": got.get("ref") or "",
                                           "reason": got.get("reason") or "",
                                           "dispatch_state": ("not_dispatched" if got.get("dispatched") is False
                                                              else ledger_state),
                                           "queue_item_id": str(row.get("id") or ""),
                                           "run_id": context.run_id, "attempt_id": context.attempt_id,
                                           "action_id": str(row.get("id") or ""),
                                           "candidate_key": candidate})
                if str(row.get("source") or "") == "inbox":
                    inbox_tracking.mark_result(
                        data_dir, row, ledger_state or state, ref=str(got.get("ref") or ""),
                        wave=wave, reason=error_text or str(got.get("reason") or ""))
                if row["channel"] in half_open:
                    if ledger_state == "committed":
                        clear_stopped(data_dir, night, row["channel"])
                        summary["stop_recovery"][row["channel"]] = "probe_ok"
                    else:
                        # 试探没发成功，但也不一定是渠道被封：保持停止文件，
                        # 下一波冷却仍过就再试一条，不改用其他渠道代发。
                        summary["stop_recovery"][row["channel"]] = (
                            "probe_%s" % (ledger_state or state or "unknown"))
                if candidate:
                    supply_updates.append((candidate, ledger_state, error_text))
                if gone:
                    # 帖子已删/不可用：再重试也不会成功，出队并单独计数。
                    summary["gone"] += 1
                    _consume(row)
                elif ledger_state in ("committed", "indeterminate", "skipped",
                                      "target_rejected", "unavailable_or_unrenderable"):
                    _consume(row)
                if ledger_state == "indeterminate":
                    ref = str(got.get("ref") or "")
                    mark_stopped(data_dir, night, row["channel"],
                                 "indeterminate", requires_human_resolution=True,
                                 unresolved_ref=ref)
                    deferred_channels[row["channel"]] = "indeterminate_pending_review"
                    summary["stopped"][row["channel"]] = "indeterminate_pending_review"
                    log("[%s] 结果未知：本渠道停批，人工裁决前不再发送" % row["channel"])
            except StopChannel as exc:
                mark_stopped(data_dir, night, row["channel"], exc.reason)
                dropped.add(row["channel"])
                summary["stopped"][row["channel"]] = exc.reason
                if row["channel"] in half_open:
                    summary["stop_recovery"][row["channel"]] = "probe_failed"
                log("[%s] 停手：%s" % (row["channel"], exc.reason))
            except ActionBlocked as exc:
                summary["results"].append({"channel": row["channel"], "action": row["action"],
                                           "target": row["target"], "actor": row.get("actor", ""),
                                           "result": "blocked", "ledger_result": "blocked",
                                           "code": exc.code, "reason": exc.code,
                                           "error": str(exc)[:300], "ok": False,
                                           "queue_item_id": str(row.get("id") or "")})
                if exc.code == "tab_hidden":
                    deferred_channels[row["channel"]] = "tab_hidden"
                    _defer(row, "tab_hidden")
                    log("[%s] 标签页不可见，本波推迟该渠道（不发送、不占额度）" % row["channel"])
            except BridgeError as exc:
                if exc.code in ("tab_not_found", "tab_open_timeout"):
                    deferred_channels[row["channel"]] = exc.code
                    _defer(row, exc.code)
                    log("[%s] 没有可用标签页（%s），本波推迟该渠道（不发送、不占额度）"
                        % (row["channel"], exc.code))
                    continue
                summary["ok"] = False
                summary["error"] = "bridge: %s" % exc
                summary["stopped"]["bridge"] = str(exc)
                break
    finally:
        if worker is not None:
            summary["prefetch"] = worker.finish()

    states = [str(r.get("ledger_result") or (
        "committed" if r.get("result") == "success" else r.get("result") or ""))
        for r in summary["results"]]
    for state in ("committed", "failed", "indeterminate", "blocked"):
        summary[state] = states.count(state)
    summary["supply_updates"] = {"used": 0, "rejected": 0, "errors": []}
    seen_pool: set = set()
    for key, ledger_state, error_text in supply_updates:
        if key in seen_pool:
            continue
        seen_pool.add(key)
        if ledger_state in ("committed", "indeterminate"):
            status, why = "used", ""
        elif ledger_state in TARGET_BLOCKED:
            status, why = "rejected", error_text or ledger_state
        else:
            continue
        try:
            supply_pool.mark(data_dir, key, status, why=why)
            summary["supply_updates"][status] += 1
        except supply_pool.PoolError:
            continue  # 手写队列行不在储备池里；只影响追踪，不影响发送结果
        except OSError as exc:
            summary["supply_updates"]["errors"].append("%s: %s" % (key, exc))
    summary["budget_exhausted"] = budget_exhausted
    summary["deferred"] = deferred_rows
    summary["deferred_channels"] = sorted(deferred_channels)
    try:
        if queue_dirty:
            save_queue(queue_file, remaining)
        summary["queue_compacted"] = queue_dirty
    except (OSError, ValueError, RuntimeError) as exc:
        summary["queue_compacted"] = False
        summary["queue_compaction_error"] = str(exc)
    summary["consumed"] = len(queue) - summary["pruned_stale"] - len(remaining)
    summary["queue_left"] = len(remaining)
    summary["elapsed_sec"] = int(time.monotonic() - began)
    out = data_dir / "state" / ("engagement-%s" % night) / ("wave-%s.json" % wave)
    _atomic_write_text(out, json.dumps(summary, ensure_ascii=False, indent=1))
    summary["summary_path"] = str(out)
    return summary


def quota_snapshot(data_dir, *, now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """只读展示：今夜与近一小时已用额度、各渠道剩余。

    quota.enforce=false 时上限与剩余一律输出 null，只留已用计数，
    避免报表继续用配额口径解释业务结果。
    """
    data_dir = Path(data_dir)
    policy = load_policy(data_dir)
    now = now or _now()
    used = usage(Ledger(data_dir / "ledger.jsonl"), policy, now=now)
    enforced = quota_enforced(policy)
    rows = {}
    throttle = Throttle(data_dir / "state")
    budget = budget_rows(policy, used=used, throttle=throttle)
    for channel, actions in (policy.get("platforms") or {}).items():
        for action, spec in (actions or {}).items():
            if not isinstance(spec, dict):
                continue
            key = "%s/%s" % (channel, action)
            night = int(spec.get("per_night") or 0)
            hour = int(spec.get("per_hour") or 0)
            b = budget.get(key) or {}
            night_used = used["night"].get(key, 0)
            day_cap = b.get("day_cap")
            day_used = b.get("day_used")
            day_left = b.get("day_left")
            rows[key] = {
                "night_used": night_used,
                "night_cap": night if enforced else None,
                "night_left": max(0, night - night_used) if enforced else None,
                "effective_night_cap": b.get("effective_per_night", night),
                "effective_night_left": (b.get("night_left",
                                               max(0, night - night_used))
                                         if enforced else None),
                "capped_by_daily_budget": bool(b.get("capped_by_daily_budget")),
                "hour_used": used["hour"].get(key, 0),
                "hour_cap": hour if enforced else None,
                "hour_left": max(0, hour - used["hour"].get(key, 0)) if enforced else None,
                "day_used": day_used,
                "day_cap": day_cap,
                "day_left": day_left,
                "min_gap_sec": int(spec.get("min_gap_sec") or 0),
            }
    return {"now": now.isoformat(timespec="seconds"),
            "in_window": night_start(now, policy) is not None,
            "hour_mode": hour_mode(policy),
            "enforced": enforced,
            "night_start": used["night_start"], "quotas": rows, "budget": budget,
            "safety": safety_rows(policy, used=used)}


def collect_inbox_report(data_dir, *, lookback_hours: Optional[int] = None,
                         channel_ids: Optional[List[str]] = None, session: str = "",
                         log=print) -> Dict[str, Any]:
    """收集并规划入站回复，同时保留每平台错误与真实能力覆盖。"""
    from . import inbox_replies

    policy = load_policy(data_dir)
    _use_session(session or bridge_session(policy))
    hours = int(lookback_hours or (policy.get("inbox") or {}).get("lookback_hours", 36))
    ids = channel_ids if channel_ids is not None else [
        str(x) for x in ((policy.get("channels") or {}).get("discord_allowed_ids") or [])]
    report = inbox_replies.collect_detailed(
        data_dir, lookback_hours=hours, channel_ids=ids, log=log)
    ledger = Ledger(Path(data_dir) / "ledger.jsonl")
    start = night_start(_now(), policy)
    report["items"] = inbox_replies.plan_replies(
        report["items"], ledger,
        per_actor_cap=int((policy.get("inbox") or {}).get("per_actor_cap", 2)),
        night_start=start)
    return report


def collect_inbox(data_dir, *, lookback_hours: Optional[int] = None,
                  channel_ids: Optional[List[str]] = None, session: str = "",
                  log=print) -> List[Dict[str, Any]]:
    """只读收集通知回复，并按每对象上限排好（写正文由调用方/Agent 完成）。"""
    return collect_inbox_report(data_dir, lookback_hours=lookback_hours,
                                channel_ids=channel_ids, session=session, log=log)["items"]


def smoke() -> Dict[str, Any]:
    """不联网自检：窗口边界、渠道间隔、空队列计划。"""
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    tz = _dt.timezone(_dt.timedelta(hours=8))
    checks = {
        "window_evening": night_start(_dt.datetime(2026, 11, 20, 22, 2, tzinfo=tz), policy) is not None,
        "window_morning": night_start(_dt.datetime(2026, 11, 21, 6, 59, tzinfo=tz), policy) is not None,
        "window_outside": night_start(_dt.datetime(2026, 11, 21, 12, 0, tzinfo=tz), policy) is None,
        "x_gap": channel_gap(policy, "x") == 90,
        "discord_gap": channel_gap(policy, "discord") == 25,
    }
    return {"ok": all(checks.values()), "checks": checks}
