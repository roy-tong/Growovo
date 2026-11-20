#!/usr/bin/env python3
"""平台自动执行白名单 —— 第一期只承诺能真正发出去的渠道。

判定不看平台名气，只看四件事同时成立：
  1. 注册表里这个渠道有已验证的写通道（PROVEN / PORTED）；
  2. 内核存在可调用的写原语（core.platforms.WRITE）；
  3. 队列执行器支持该动作（core.queue_runner.QUEUE_ACTIONS）；
  4. 平台许可允许自主写入（core.platform_compliance.policy_row）。

四条都成立才是 AUTO：可以进「全自动」承诺。技术可执行但平台许可禁止
自主写入的渠道（如 X 的非 API 自动化）降为 GATED：只出人工辅助任务，
不得在编译阶段被当成可自动执行。缺少技术条件的同样只能 GATED 或
MANUAL（只出人工作业单，不假装能自动）。
"""
from __future__ import annotations

from typing import Dict, List

from channels import REGISTRY as _REGISTRY
from .platforms import BY_ID, WRITE
from .queue_runner import QUEUE_ACTIONS

AUTO = "auto"
GATED = "gated"
MANUAL = "manual"

_TIER_ORDER = {AUTO: 0, GATED: 1, MANUAL: 2}


class PlatformError(RuntimeError):
    pass


_WRITE_CHANNEL_TO_PLATFORM = {pair[0]: pid for pid, pair in WRITE.items()}


def _normalize(channel: str) -> str:
    """把平台 id 与内核渠道名归一：hackernews ↔ hn、email ↔ email。"""
    key = str(channel or "").strip().lower()
    if key in _REGISTRY:
        return key
    pair = WRITE.get(key)
    if pair:
        return pair[0]
    if key in _WRITE_CHANNEL_TO_PLATFORM:
        return key
    return key


def classify(channel: str) -> Dict[str, object]:
    """返回一个平台/渠道的自动执行等级与依据。"""
    raw = str(channel or "").strip().lower()
    key = _normalize(raw)
    name = (BY_ID.get(raw) or BY_ID.get(key) or {}).get("name") or raw or key
    spec = _REGISTRY.get(key)
    platform_key = _WRITE_CHANNEL_TO_PLATFORM.get(key, key)
    pair = WRITE.get(raw) or WRITE.get(key) or WRITE.get(platform_key)
    status = (spec or {}).get("status", "")
    if spec and pair and pair[1] in QUEUE_ACTIONS:
        if status in ("PROVEN", "PORTED"):
            tier = AUTO
            reason = (f"{spec.get('status')} 写通道已验证，队列可直接执行 "
                      f"{pair[0]}/{pair[1]}")
        else:
            tier = GATED
            reason = ("有写通道但选择器/流程未实测（UNVERIFIED），"
                      "首跑需人工盯并显式 allow_unverified")
    elif spec and pair:
        tier = GATED
        reason = f"写动作为 {pair[1]}，队列执行器不支持该动作，暂不能全自动"
    elif spec:
        tier = MANUAL
        reason = "注册表有渠道但没有自动写原语，只能出人工作业单"
    else:
        tier = MANUAL
        reason = "内核没有这个平台，按人工处理，不假装支持"
    technical_tier = tier
    policy = _policy(key)
    autonomous = bool(policy.get("autonomous_write", True))
    if tier == AUTO and not autonomous:
        # The technical path works, but the platform forbids autonomous use;
        # keeping tier=AUTO would promise automation the runtime must refuse.
        tier = GATED
        reason = (f"{status} 写通道技术可用，但平台许可禁止自主执行"
                  f"（{policy.get('code')}）：只出人工辅助任务，或接官方 API 并授权")
    return {
        "channel": key,
        "platform": raw or key,
        "name": name,
        "status": status,
        "tier": tier,
        "technical_tier": technical_tier,
        "autonomous": autonomous,
        "write": list(pair) if pair else None,
        "actions": sorted((spec or {}).get("actions") or []),
        # 技术可执行 ≠ 平台允许自主执行：X 的写通道仍可用于人工/只读，
        # 但自主写默认被平台规则拒绝（core/platform_compliance.py）。
        "policy": policy,
        "reason": reason,
    }


def _policy(channel: str) -> Dict[str, object]:
    from .platform_compliance import policy_row
    return policy_row(channel)


def matrix() -> List[Dict[str, object]]:
    """覆盖平台知识库全部平台，按 auto → gated → manual 排序（同层按名称）。"""
    rows = [classify(pid) for pid in BY_ID]
    rows.sort(key=lambda r: (_TIER_ORDER.get(str(r["tier"]), 9),
                             str(r["name"]), str(r["channel"])))
    return rows


def auto_channels() -> List[str]:
    """第一期自主执行白名单，按注册表顺序返回（平台许可禁止自主的渠道不在内）。"""
    ordered = [ch for ch in _REGISTRY
               if is_auto(ch)]
    return ordered or sorted({str(r["channel"]) for r in matrix() if is_auto(str(r["channel"]))})


def gated_channels() -> List[str]:
    return sorted({str(r["channel"]) for r in matrix() if r["tier"] == GATED})


def is_auto(channel: str) -> bool:
    """只有「技术上可执行」且「平台许可允许自主写」同时成立才算 AUTO。

    只看 tier 会把 X 这类技术可用但平台禁止自主写的渠道误判为可自动执行；
    编译器和调度器都必须走这个函数，而不是直接读 tier。
    """
    row = classify(channel)
    return row["tier"] == AUTO and bool(row["autonomous"])


def assert_auto(channels: List[str]) -> None:
    bad = [str(c) for c in channels if not is_auto(str(c))]
    if bad:
        raise PlatformError(
            f"第一期只能自主执行 {auto_channels()}；这些渠道不能自主执行：{bad}。"
            "它们可以走人工辅助任务，但不计入自动发布量")
