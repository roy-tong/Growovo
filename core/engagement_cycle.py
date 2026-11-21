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
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .ledger import Ledger

DEFAULT_POLICY = {
    "window": {"start_hour": 22, "end_hour": 7, "waves_per_night": 9},
    "platforms": {
        "x": {
            "publish": {"min_gap_sec": 90, "per_hour": 10, "per_night": 90},
            "reply": {"min_gap_sec": 90, "per_hour": 20, "per_night": 180},
        },
        "github": {"reply": {"min_gap_sec": 20, "per_hour": 15, "per_night": 135}},
        "discord": {"publish": {"min_gap_sec": 25, "per_hour": 6, "per_night": 54}},
    },
    "interleave": {"priority": ["x", "github", "discord"], "min_gap_sec": 15,
                   "max_actions_per_wave": 51},
    "inbox": {"per_actor_cap": 2, "lookback_hours": 36, "order": ["x", "github", "discord"]},
    "queue": {"max_age_hours": 12},
    "quota": {"hour_mode": "rolling"},
    "supply": {
        "wave_targets": {"x/publish": 10, "x/reply": 20,
                         "github/reply": 15, "discord/publish": 6},
        "prefetch": {"enabled": False, "lookahead_waves": 2,
                     "sources": ["github", "content"], "max_queries": 3,
                     "per_query": 10, "gap_sec": 15, "days": 14},
    },
    "channels": {"discord_allowed_ids": []},
    "bridge": {"session": ""},
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


def plan(queue: List[Dict[str, Any]], policy: Dict[str, Any], *, ledger: Ledger,
         now: _dt.datetime, budget_sec: int = 3000, max_actions: int = 0,
         campaign: str = "") -> Dict[str, Any]:
    """把候选队列排成带 scheduled_at 的执行计划；纯计算，无副作用。"""
    start = night_start(now, policy)
    used = usage(ledger, policy, now=now)
    cap = int((policy.get("inbox") or {}).get("per_actor_cap", 2))
    allowed_dc = {str(x) for x in ((policy.get("channels") or {}).get("discord_allowed_ids") or [])}
    priority = [str(x) for x in ((policy.get("interleave") or {}).get("priority")
                                 or ["x", "github", "discord"])]
    limit = int(max_actions or (policy.get("interleave") or {}).get("max_actions_per_wave") or 0)

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
        chans = [c for c in order if c in by_channel] \
            + [c for c in by_channel if c not in order]
        mixed: List[Dict[str, Any]] = []
        while any(by_channel[c] for c in chans):
            for name in chans:
                if by_channel[name]:
                    mixed.append(by_channel[name].pop(0))
        while any(rest[name] for name in priority):
            for name in priority:
                if rest.get(name):
                    mixed.append(rest[name].pop(0))
        return mixed

    for row in _candidate_rows():
        if _stale(row, tz=now.tzinfo, now=now, limit_sec=_stale_limit(policy)):
            _reject(row, "stale_row")
            continue
        spec = action_spec(policy, row["channel"], row["action"])
        if not spec:
            _reject(row, "unsupported")
            continue
        if not row["text"]:
            _reject(row, "text_empty")
            continue
        if row["action"] == "reply" and not row["target"]:
            _reject(row, "target_required")
            continue
        if row["channel"] == "discord":
            if allowed_dc and row["target"] not in allowed_dc:
                _reject(row, "channel_not_allowed")
                continue
        key = _candidate_key(row)
        if key in seen:
            _reject(row, "duplicate_in_queue")
            continue
        seen.add(key)

        lookup = "%s/%s" % (row["channel"], row["action"])
        if row["action"] == "reply" and ledger.already(channel=row["channel"], target=row["target"]):
            _reject(row, "already_done")
            continue
        if row["channel"] == "discord" and campaign and ledger.already(
                channel="discord", target=row["target"], campaign=campaign):
            _reject(row, "already_done_in_wave")
            continue
        night_used = used["night"].get(lookup, 0) + planned.get(lookup, 0)
        if night_used >= int(spec.get("per_night") or 0):
            _reject(row, "night_quota_exhausted")
            continue
        hour_used = used["hour"].get(lookup, 0) + planned.get(lookup, 0)
        if hour_used >= int(spec.get("per_hour") or 0):
            _reject(row, "hour_quota_exhausted")
            continue
        actor_key = row["actor"].lower() if row["action"] == "reply" else ""
        if actor_key and actors.get(actor_key, 0) >= cap:
            _reject(row, "actor_cap")
            continue
        if limit and len(scheduled) >= limit:
            _reject(row, "wave_cap")
            continue

        gap = channel_gap(policy, row["channel"])
        earliest = max(now.timestamp(), last_at.get(row["channel"], 0.0) + gap)
        if global_gap:
            earliest = max(earliest, last_any + global_gap)
        if earliest - now.timestamp() > budget_sec:
            _reject(row, "wave_budget")
            continue
        at = _dt.datetime.fromtimestamp(earliest, tz=now.tzinfo)
        scheduled.append(dict(row, scheduled_at=at.isoformat(timespec="seconds"),
                              delay_sec=int(earliest - now.timestamp()),
                              quota_key=lookup))
        last_at[row["channel"]] = earliest
        last_any = earliest
        planned[lookup] = planned.get(lookup, 0) + 1
        if actor_key:
            actors[actor_key] = actors.get(actor_key, 0) + 1

    targets = (policy.get("supply") or {}).get("wave_targets") or {}
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
    supply = {}
    for key in sorted(set(action_keys) | set(targets)):
        target = int(targets.get(key) or 0)
        queued = queued_by_key.get(key, 0)
        scheduled_count = planned.get(key, 0)
        supply[key] = {
            "target": target, "queued": queued, "scheduled": scheduled_count,
            "rejected": rejected_by_key.get(key, 0),
            "gap": max(0, target - scheduled_count),
            "unscheduled": max(0, queued - scheduled_count),
            "reasons": sorted({row["reason"] for row in skipped
                               if "%s/%s" % (row["channel"], row["action"]) == key}),
        }

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
    }


def load_queue(path) -> List[Dict[str, Any]]:
    """读队列文件：接受 JSON 数组或 {"items": [...]}。"""
    file = Path(path)
    if not file.is_file():
        return []
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        payload = payload.get("items") or []
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def _atomic_write_text(path, text: str) -> Path:
    """同目录临时文件 + replace，避免进程中断留下半截 JSON。"""
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=file.name + ".", suffix=".tmp", dir=str(file.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(file))
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return file


def save_queue(path, items: List[Dict[str, Any]]) -> Path:
    return _atomic_write_text(path, json.dumps(items, ensure_ascii=False, indent=1))


def _lock_path(data_dir) -> Path:
    return Path(data_dir) / "state" / "engagement-cycle.lock"


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
        existing = load_queue(path)
        merged = [dict(row) for row in existing]
        known = {_candidate_key(row) for row in merged}
        for raw in fresh:
            row = _normalize(raw)
            key = _candidate_key(row)
            if key in known:
                for old in merged:
                    if _candidate_key(old) == key and not old.get("text"):
                        old.setdefault("their_text", row.get("their_text") or "")
                        old.setdefault("actor", row.get("actor") or "")
                continue
            known.add(key)
            merged.append(row)
        save_queue(path, merged)
        return {"ok": True, "count": len(fresh), "items": fresh,
                "queue_path": str(path), "queue_size": len(merged),
                "next": "为每条补 text（没有真实依据就留空丢弃），再跑 cycle plan"}
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
            elif row["channel"] == "discord" and not row["target"].isdigit():
                reason = "target_invalid"
            elif row["channel"] == "discord" and allowed_dc and row["target"] not in allowed_dc:
                reason = "channel_not_allowed"
            if reason:
                rejected.append({"index": idx, "channel": row["channel"],
                                 "action": row["action"], "reason": reason})
                continue
            row["queued_at"] = now.isoformat(timespec="seconds")
            key = _candidate_key(row)
            old_idx = next((i for i, old in enumerate(existing)
                            if _candidate_key(old) == key), None)
            if old_idx is not None and not str(existing[old_idx].get("text") or "").strip():
                merged = dict(existing[old_idx])
                merged.update(row)
                merged["source"] = str(existing[old_idx].get("source") or row["source"])
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
        return {"ok": True, "added": len(added), "filled": len(filled),
                "duplicates": duplicates, "rejected": rejected,
                "queue_path": str(path), "queue_size": len(existing),
                "items": added + filled}
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def stop_path(data_dir, night: str, channel: str) -> Path:
    return Path(data_dir) / "state" / "stops" / ("%s-%s.json" % (night, channel))


def is_stopped(data_dir, night: str, channel: str) -> bool:
    return stop_path(data_dir, night, channel).is_file()


def mark_stopped(data_dir, night: str, channel: str, reason: str) -> Path:
    path = stop_path(data_dir, night, channel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"channel": channel, "night": night, "reason": reason,
                                "at": _now().isoformat(timespec="seconds")},
                               ensure_ascii=False), encoding="utf-8")
    return path


def run(data_dir, *, go: bool = False, queue_path=None, max_actions: int = 0,
        wave_id: str = "", budget_sec: int = 3000, session: str = "",
        prefetch: Optional[bool] = None, log=print) -> Dict[str, Any]:
    """串行执行真实波次；dry-run 保持只读且不占用执行锁。"""
    if not go:
        return _run(data_dir, go=False, queue_path=queue_path, max_actions=max_actions,
                    wave_id=wave_id, budget_sec=budget_sec, session=session,
                    prefetch=prefetch, log=log)
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
                    prefetch=prefetch, log=log)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _run(data_dir, *, go: bool = False, queue_path=None, max_actions: int = 0,
         wave_id: str = "", budget_sec: int = 3000, session: str = "",
         prefetch: Optional[bool] = None, log=print) -> Dict[str, Any]:
    """执行一波：窗口 → 停手 → 计划 → 逐条 sleep 后发送 → 记波次摘要。"""
    from .action_runner import run_action
    from .browser_attach import BridgeError
    from .browser_write import ActionBlocked, StopChannel

    data_dir = Path(data_dir)
    policy = load_policy(data_dir)
    used_session = _use_session(session or bridge_session(policy))
    now = _now()
    start = night_start(now, policy)
    if not start:
        return {"ok": False, "reason": "outside_window", "now": now.isoformat(timespec="seconds")}
    night = night_key(start)
    wave = wave_id or now.strftime("%H%M")
    campaign = "night-%s-w%s" % (night, wave)
    queue_file = queue_path or (data_dir / "state" / "engagement-queue.json")
    queue = load_queue(queue_file)
    stale = stale_rows(queue, policy, now=now)
    stale_keys = {_queue_key(row) for row in stale}

    blocked = {name: stop_path(data_dir, night, name).exists()
               for name in (policy.get("platforms") or {})}
    if all(blocked.values()):
        return {"ok": False, "reason": "all_channels_stopped", "night": night}

    ledger = Ledger(data_dir / "ledger.jsonl")
    prepared = plan([row for row in queue
                     if _queue_key(_normalize(row)) not in stale_keys
                     and not blocked.get(str(row.get("channel") or row.get("platform") or ""))],
                    policy, ledger=ledger, now=now, budget_sec=budget_sec,
                    max_actions=max_actions, campaign=campaign)
    summary: Dict[str, Any] = {
        "ok": True, "wave": wave, "campaign": campaign, "night": night,
        "dry": not go, "bridge_session": used_session,
        "planned": len(prepared["scheduled"]),
        "skipped": prepared["skipped"], "results": [], "stopped": {},
        "deferred": [], "deferred_channels": [],
        "pruned_stale": 0,
        "stale": [{"channel": row["channel"], "action": row["action"],
                   "target": row["target"],
                   "age_hours": _age_hours(row, now=now)} for row in stale],
        "usage": prepared["usage"], "supply": prepared["supply"],
        "started_at": now.isoformat(timespec="seconds"),
    }
    if not go:
        summary["scheduled"] = [{"channel": row["channel"], "action": row["action"],
                                 "target": row["target"], "delay_sec": row["delay_sec"],
                                 "actor": row["actor"]} for row in prepared["scheduled"]]
        return summary

    dropped = set()
    deferred_channels: Dict[str, str] = {}
    deferred_rows: List[Dict[str, Any]] = []
    began = time.monotonic()
    deadline = began + max(0, int(budget_sec))
    budget_exhausted = False
    remaining = [dict(raw) for raw in queue]
    pruned = 0
    if stale_keys:
        kept = [raw for raw in remaining if _queue_key(_normalize(raw)) not in stale_keys]
        pruned = len(remaining) - len(kept)
        remaining = kept
        try:
            save_queue(queue_file, remaining)
        except OSError as exc:
            log("[queue] 丢弃超龄草稿后回写失败：%s" % exc)
    summary["pruned_stale"] = pruned

    def _consume(row: Dict[str, Any]) -> None:
        key = _queue_key(_normalize(row))
        for idx, raw in enumerate(remaining):
            if _queue_key(_normalize(raw)) == key:
                remaining.pop(idx)
                break
        try:
            save_queue(queue_file, remaining)
        except OSError as exc:
            log("[queue] 回写失败（不影响本条发送）：%s" % exc)

    def _defer(row: Dict[str, Any], reason: str) -> None:
        deferred_rows.append({"channel": row["channel"], "action": row["action"],
                              "target": row["target"], "actor": row["actor"],
                              "reason": reason})

    pending = [dict(row) for row in prepared["scheduled"]]
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
                got = run_action(
                    channel=row["channel"], action=row["action"], data_dir=data_dir,
                    text=row["text"], target=row["target"], go=True, campaign=campaign,
                    actor=row["actor"], log=log, transport="browser-attach",
                    operator="cycle:%s" % wave, run_id=campaign,
                    queue_item_id=str(row.get("id") or ""))
                state = got.get("result") or ("skipped" if got.get("skipped") else "unknown")
                ledger_state = got.get("ledger_result") or (
                    "committed" if state == "success" else state)
                engine = got.get("engine") or {}
                summary["results"].append({"channel": row["channel"], "action": row["action"],
                                           "target": row["target"], "actor": row["actor"],
                                           "result": state,
                                           "ledger_result": got.get("ledger_result") or "",
                                           "error": str(got.get("error") or engine.get("err") or "")[:300],
                                           "ok": bool(got.get("ok")),
                                           "ref": got.get("ref") or ""})
                if ledger_state in ("committed", "indeterminate", "skipped"):
                    _consume(row)
            except StopChannel as exc:
                mark_stopped(data_dir, night, row["channel"], exc.reason)
                dropped.add(row["channel"])
                summary["stopped"][row["channel"]] = exc.reason
                log("[%s] 停手：%s" % (row["channel"], exc.reason))
            except ActionBlocked as exc:
                summary["results"].append({"channel": row["channel"], "action": row["action"],
                                           "target": row["target"], "result": "blocked",
                                           "code": exc.code})
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
    summary["budget_exhausted"] = budget_exhausted
    summary["deferred"] = deferred_rows
    summary["deferred_channels"] = sorted(deferred_channels)
    summary["consumed"] = len(queue) - summary["pruned_stale"] - len(remaining)
    summary["queue_left"] = len(remaining)
    summary["elapsed_sec"] = int(time.monotonic() - began)
    out = data_dir / "state" / ("engagement-%s" % night) / ("wave-%s.json" % wave)
    _atomic_write_text(out, json.dumps(summary, ensure_ascii=False, indent=1))
    summary["summary_path"] = str(out)
    return summary


def quota_snapshot(data_dir, *, now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """只读展示：今夜与近一小时已用额度、各渠道剩余。"""
    data_dir = Path(data_dir)
    policy = load_policy(data_dir)
    now = now or _now()
    used = usage(Ledger(data_dir / "ledger.jsonl"), policy, now=now)
    rows = {}
    for channel, actions in (policy.get("platforms") or {}).items():
        for action, spec in (actions or {}).items():
            if not isinstance(spec, dict):
                continue
            key = "%s/%s" % (channel, action)
            night = int(spec.get("per_night") or 0)
            hour = int(spec.get("per_hour") or 0)
            rows[key] = {
                "night_used": used["night"].get(key, 0),
                "night_cap": night,
                "night_left": max(0, night - used["night"].get(key, 0)),
                "hour_used": used["hour"].get(key, 0),
                "hour_cap": hour,
                "hour_left": max(0, hour - used["hour"].get(key, 0)),
                "min_gap_sec": int(spec.get("min_gap_sec") or 0),
            }
    return {"now": now.isoformat(timespec="seconds"),
            "in_window": night_start(now, policy) is not None,
            "hour_mode": hour_mode(policy),
            "night_start": used["night_start"], "quotas": rows}


def collect_inbox(data_dir, *, lookback_hours: Optional[int] = None,
                  channel_ids: Optional[List[str]] = None, session: str = "",
                  log=print) -> List[Dict[str, Any]]:
    """只读收集通知回复，并按每对象上限排好（写正文由调用方/Agent 完成）。"""
    from . import inbox_replies

    policy = load_policy(data_dir)
    _use_session(session or bridge_session(policy))
    hours = int(lookback_hours or (policy.get("inbox") or {}).get("lookback_hours", 36))
    ids = channel_ids if channel_ids is not None else [
        str(x) for x in ((policy.get("channels") or {}).get("discord_allowed_ids") or [])]
    raw = inbox_replies.collect(data_dir, lookback_hours=hours, channel_ids=ids, log=log)
    ledger = Ledger(Path(data_dir) / "ledger.jsonl")
    start = night_start(_now(), policy)
    return inbox_replies.plan_replies(
        raw, ledger,
        per_actor_cap=int((policy.get("inbox") or {}).get("per_actor_cap", 2)),
        night_start=start)


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
