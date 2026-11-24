#!/usr/bin/env python3
"""业务执行能力矩阵。

AUTO 要求写通道已验证（PROVEN / PORTED）、内核有写原语且队列支持该动作。
UNVERIFIED 写通道进入 GATED，首跑需要技术确认；没有写原语的平台进入 MANUAL。
平台规则只作为风险提示，不把业务渠道从 AUTO 降级。
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
    autonomous = tier == AUTO and policy.get("autonomous_write") is True
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
        # policy 只承载风险说明；技术等级不被平台规则降级。
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
    """已验证且队列可执行的业务渠道，按注册表顺序返回。"""
    ordered = [ch for ch in _REGISTRY
               if is_auto(ch)]
    return ordered or sorted({str(r["channel"]) for r in matrix() if is_auto(str(r["channel"]))})


def gated_channels() -> List[str]:
    return sorted({str(r["channel"]) for r in matrix() if r["tier"] == GATED})


def is_auto(channel: str) -> bool:
    """写通道、队列和技术验证均满足即为 AUTO；平台风险另行提示。"""
    row = classify(channel)
    return row["tier"] == AUTO and bool(row["autonomous"])


def assert_auto(channels: List[str]) -> None:
    bad = [str(c) for c in channels if not is_auto(str(c))]
    if bad:
        raise PlatformError(
            f"当前可自主执行 {auto_channels()}；这些渠道缺少已验证写通道或队列支持：{bad}。"
            "先完成技术接入与首跑验证，平台风险提示不影响此判定")
