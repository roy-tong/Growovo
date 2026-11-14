#!/usr/bin/env python3
"""策略编译器 —— 把「产品事实 + 增长目标」编译成可执行的 RunSpec。

定位：策略的终点不是一份文档，而是一组可执行参数。本模块只做编译（纯函数、
不写盘、不发送）：调 goals 生成目标契约，调 strategy 选主渠道与理由，再折成
配额、时间窗、供给需求单、读数计划与护栏。真正落盘由 grow_v2.launch 负责。

第一期只允许自动执行渠道（x / email）；非自动渠道在这里直接拒绝，避免出现
「编译出来却发不出去」的假计划。
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from . import goals, grow_content, grow_fit, platform_caps, strategy, timezone_windows as tw
from .grow_schedule import DEFAULT_AUDIENCE, split_week
from .platforms import WRITE as PLATFORM_WRITE
from .queue import MAX_LEN

SCHEMA = "growovo.grow-run/2"
MAX_ACTIONS_PER_RUN = 10
DEFAULT_INTERVAL = [60, 120]


class CompileError(RuntimeError):
    pass


def _platform_channel(platform_id: str) -> Optional[str]:
    pair = PLATFORM_WRITE.get(platform_id)
    return pair[0] if pair else None


def _handle_from_account(data_dir, account_id: str) -> str:
    if not account_id:
        return ""
    try:
        from .accounts import AccountRegistry
        acc = AccountRegistry()
        return str(acc.get(account_id).get("handle") or "").lstrip("@")
    except Exception:  # noqa: BLE001 账号库缺失不影响编译，读数计划稍后由 doctor 提醒
        return ""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(spec_or_parts: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(spec_or_parts).encode("utf-8")).hexdigest()[:16]


def _supply_brief(channel: str, action: str, count: int, audience: str,
                  tactic: str = "") -> Dict[str, Any]:
    formulas = {
        "reply": ["具体场景 + 具体做法 + 一句可验证的结果",
                  "先回答对方问题，再补一个他没问到的细节"],
        "publish": ["一个可复现的做法 + 一个真实数字", "反常识结论 + 证据 + 边界"],
        "message": ["对方最近做的一件事 + 一个具体帮助 + 一个低门槛问题"],
    }
    return {
        "id": f"b-{channel}-{action}",
        "channel": channel,
        "action": action,
        "count_per_week": int(count),
        "formula": formulas.get(action, formulas["reply"])[0],
        "formulas": formulas.get(action, []),
        "audience": audience,
        "max_len": MAX_LEN.get((channel, action)),
        "tactic": tactic,
        "gates": ["voice", "tactic", "no-link" if action == "reply" else "content",
                  "dedupe", "no-placeholder"],
        "rule": "一条内容只服务一个具体的人/问题；不群发、不复用同一句话",
    }


def compile_run(*, run_id: str, facts: Dict[str, Any], goal_id: str,
                channels: Optional[List[str]] = None,
                target: Optional[float] = None,
                window_days: Optional[int] = None,
                actions_per_week: int = 20,
                started: Optional[str] = None,
                owner: str = "", account: str = "", handle: str = "",
                audience: str = DEFAULT_AUDIENCE,
                actions: Optional[List[str]] = None,
                data_dir=None) -> Dict[str, Any]:
    """编译 RunSpec v2。失败抛 CompileError；成功返回纯 dict（未落盘）。"""
    run_id = str(run_id or "").strip()
    if not run_id:
        raise CompileError("闭环 id 不能为空")
    started = str(started or date.today().isoformat())
    requested = [c.strip() for c in (channels or ["x", "email"]) if str(c).strip()]
    if not requested:
        raise CompileError("至少要指定一个渠道（第一期自动执行：x、email）")
    try:
        platform_caps.assert_auto(requested)
    except platform_caps.PlatformError as exc:
        raise CompileError(str(exc)) from exc
    if audience not in tw.AUDIENCES:
        raise CompileError(f"未知受众 {audience}；可选：{sorted(tw.AUDIENCES)}")

    spec = goals.get(goal_id)  # 未知目标在这里抛 GoalError
    affinity = set((spec.get("affinity") or {}).keys())
    goal_channels = [c for c in requested if c in affinity]
    if not goal_channels:
        raise CompileError(
            f"目标 {goal_id} 与渠道 {requested} 对不上；可选目标："
            f"{sorted(g for g in goals.BY_ID if set((goals.get(g).get('affinity') or {})) & set(requested))}")

    try:
        plan = goals.build_plan(facts, goal_id, platforms=goal_channels,
                                target=target, window_days=window_days)
    except Exception as exc:  # GoalError 原样带上编译上下文
        raise CompileError(f"目标契约生成失败：{exc}") from exc

    goal = dict(plan["goal"])
    # 用启动日锚定窗口，保证离线补跑/测试可复现（build_plan 用今天）
    start_date = date.fromisoformat(started)
    window = int(goal.get("window_days") or 28)
    goal["starts"] = started
    goal["ends"] = (start_date + timedelta(days=window - 1)).isoformat()
    goal["baseline"] = plan["goal"].get("baseline")

    roles = plan.get("platforms") or {}
    primary_row = roles.get("primary") or {}
    primary_platform = str(primary_row.get("id") or "")
    primary_channel = _platform_channel(primary_platform)
    if not primary_channel:
        raise CompileError(
            f"主渠道 {primary_platform or '未知'} 没有自动写通道；第一期只支持 "
            f"{platform_caps.auto_channels()}。换个目标或显式指定 --channels x")
    if not platform_caps.is_auto(primary_channel):
        raise CompileError(
            f"主渠道 {primary_channel} 不在第一期自动执行白名单"
            f"（{platform_caps.auto_channels()}）；该渠道只能出人工单")

    # 主渠道 + 用户点名的承接渠道（email 常作为承接层，不参与目标口径）
    channel_list = [primary_channel] + [c for c in requested if c != primary_channel]
    actions_map: Dict[str, List[str]] = {}
    for ch in channel_list:
        if ch == "email":
            actions_map[ch] = ["message"]
        elif ch == "x":
            chosen = [a for a in (actions or ["reply"]) if a in ("reply", "publish")]
            actions_map[ch] = chosen or ["reply"]
        else:
            raise CompileError(f"渠道 {ch} 暂无自动动作定义")

    policy_actions = sorted({f"{ch}:{a}" for ch, acts in actions_map.items() for a in acts})

    primary_actions = actions_map[primary_channel]
    secondary_channels = [c for c in channel_list if c != primary_channel]
    if secondary_channels:
        # 主渠道拿 85%，承接渠道（email）拿 15%，避免超额发送
        weights: Dict[str, float] = {}
        primary_share = 0.85
        for i, act in enumerate(primary_actions):
            weights[act] = weights.get(act, 0.0) + primary_share / len(primary_actions)
        secondary_share = 1.0 - primary_share
        secondary_actions = [a for c in secondary_channels for a in actions_map[c]]
        for act in secondary_actions:
            weights[act] = weights.get(act, 0.0) + secondary_share / len(secondary_actions)
    else:
        weights = {a: (0.8 if i == 0 else 0.2 / max(1, len(primary_actions) - 1))
                   for i, a in enumerate(primary_actions)}
        if len(primary_actions) == 1:
            weights = {primary_actions[0]: 1.0}
    daily = split_week(actions_per_week, started, weights)
    total_supply = int(actions_per_week)
    brief_counts: Dict[str, int] = {}
    for day in daily.values():
        for act, n in day.items():
            brief_counts[act] = brief_counts.get(act, 0) + int(n)

    tactic = "x-reply-guy" if primary_channel == "x" and "reply" in primary_actions else ""
    briefs = [_supply_brief(primary_channel, act, brief_counts.get(act, 0), audience, tactic)
              for act in primary_actions]
    for ch in channel_list:
        if ch == primary_channel:
            continue
        for act in actions_map[ch]:
            briefs.append(_supply_brief(ch, act, max(1, total_supply // 7), audience))

    resolved_handle = (handle or _handle_from_account(data_dir, account)).lstrip("@")
    goal_family = str(goal.get("family") or "")
    marketed = int(goal.get("baseline") or 0)
    early_phase = marketed < 5000
    mix = grow_content.mix_for(goal_family)
    if early_phase:
        mix = {"reply": 0.85, "post": 0.15}
    koc_band = [1000, 50000]
    briefs = [grow_content.enrich_brief(brief, goal_family=goal_family)
              for brief in briefs]
    for brief in briefs:
        brief["target_policy"] = "koc_first" if early_phase else "balanced"
        brief["koc_band"] = koc_band
    fit_profile = grow_fit.product_profile(facts)
    fit_profile = {"name": fit_profile.get("name"), "url": fit_profile.get("url"),
                   "what": fit_profile.get("what"),
                   "keywords": sorted(fit_profile.get("keywords") or []),
                   "audience": sorted(fit_profile.get("audience") or []),
                   "value_events": fit_profile.get("value_events") or []}

    readers = []
    if "x" in channel_list:
        readers = [{"metric": "x_followers", "handle": resolved_handle, "daily": True},
                   {"metric": "x_impressions", "handle": resolved_handle, "daily": True}]

    windows = {"audience": audience,
               "timezone_offset": float(tw.AUDIENCES[audience]["tz"]),
               "local": [list(w) for w in tw.GOLDEN_WINDOWS],
               "dead_zone": [0.0, 6.0]}

    strategy_block = {
        "primary": {"channel": primary_channel, "platform": primary_platform,
                    "actions": actions_map[primary_channel],
                    "why": primary_row.get("why", ""),
                    "tier": primary_row.get("tier", ""),
                    "residency_days": int(primary_row.get("residency") or 0)},
        "support": [{"channel": ch, "actions": actions_map[ch],
                     "why": "承接主渠道来的人，不背目标指标"}
                    for ch in channel_list if ch != primary_channel],
        "not_doing": [{"platform": r.get("id"), "name": r.get("name"),
                       "why": r.get("why", "")}
                      for r in (roles.get("not_now") or [])],
        "tactics": [tactic] if tactic else [],
        "compiled_at": date.today().isoformat(),
    }

    quota_block = {
        "initial_actions_per_week": int(actions_per_week),
        "actions_per_week": int(actions_per_week),
        "action_weights": weights,
        "daily_preview": daily,
        "max_per_run": MAX_ACTIONS_PER_RUN,
        "interval_seconds": list(DEFAULT_INTERVAL),
    }

    from .tactics import recommend
    tactic_portfolio = recommend(facts, goal_id, channels=channel_list,
        phase="cold_start" if early_phase else "scale", risk_max="white", today=started)
    # Advisory portfolio cannot expand executor channels, actions or quotas.
    strategy_block["tactic_portfolio"] = tactic_portfolio
    spec_payload = {
        "schema": SCHEMA,
        "id": run_id,
        "owner": owner,
        "started": started,
        "status": "active",
        "go": False,
        "facts_ref": "",
        "facts_sha256": hashlib.sha256(_canonical(facts).encode("utf-8")).hexdigest()[:16],
        "goal": {k: goal.get(k) for k in
                 ("id", "family", "name", "metric", "metric_label", "target",
                  "target_kind", "target_value", "target_text", "window_days",
                  "starts", "ends", "baseline", "proves", "does_not_prove",
                  "leading", "counter")},
        "strategy": strategy_block,
        "channels": channel_list,
        "actions": policy_actions,
        "quota": quota_block,
        "windows": windows,
        "supply": {"briefs": briefs, "min_cover_days": 3,
                   "staged": 0, "coverage_days": 0, "gap": int(actions_per_week)},
        "product": {"name": (facts.get("product") or {}).get("name", ""),
                     "what": (facts.get("product") or {}).get("what", "")},
        "fit_profile": fit_profile,
        "content": {"mix": mix, "formula_weights": {},
                    "layers": {"reply": "发现层", "post": "证明层"}},
        "growth_strategy": {
            "phase": "early" if early_phase else "growth",
            "focus": "koc",
            "koc_band": koc_band,
            "koc_share_target": 0.6 if early_phase else 0.4,
            "why": "早期优先垂直 KOC（1k–50k）：声量够、更像真人、愿意信任和转述；"
                   "不追头部大号，也不靠大量普通用户充数",
            "weekly_targets": {
                "koc_replies": max(1, round(actions_per_week * 0.6)),
                "peer_posts": max(1, round(actions_per_week * 0.15)),
            },
            "rules": [
                "每个 KOC 先认真回一次，不连续刷",
                "不买粉、不互关、不参加互赞群",
                "回复只提供价值，不放链接、不提产品",
                "头部大号只读不追，把产能留给 KOC",
            ],
        },
        "automation": {"enabled": True, "provider": "codex", "daily_budget": 12},
        "content_index": {},
        "post_urls": {},
        "measurement": {"readers": readers,
                        "business_campaign": run_id,
                        "verification": "UNVERIFIED",
                        "calibration": {"due": "", "checks": []}},
        "guards": {"account_id": account, "min_health": 60,
                   "max_delete_rate": 0.1,
                   "pause_on": ["takeover", "limited"],
                   "residency_days": int(primary_row.get("residency") or 0)},
        "heartbeat": {"interval_minutes": 30, "last_tick": None, "ok": None},
        # ---- v1 兼容字段 ----
        "plan": {"actions_per_week": int(actions_per_week),
                 "initial_actions_per_week": int(actions_per_week)},
        "last_queue_run": {},
        "result_cards": [],
        "adjustments": [],
        "history": [],
        # ---- v2 新增 ----
        "daily_cards": [],
        "decisions": [],
        "telemetry": {"weeks": {}, "days": {}},
        "supply_state": {},
    }
    strategy_block["fingerprint"] = fingerprint({
        "goal": {k: spec_payload["goal"].get(k) for k in ("id", "target", "window_days")},
        "strategy": {"primary": strategy_block["primary"],
                     "support": strategy_block["support"],
                     "tactics": strategy_block["tactics"]},
        "quota": {"initial_actions_per_week": actions_per_week,
                  "action_weights": weights},
        "windows": windows,
        "briefs": briefs,
    })
    return spec_payload
