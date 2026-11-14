#!/usr/bin/env python3
"""平台自动执行白名单 —— 第一期只承诺能真正发出去的渠道。

判定不看平台名气，只看三件事同时成立：
  1. 注册表里这个渠道有已验证的写通道（PROVEN / PORTED）；
  2. 内核存在可调用的写原语（core.platforms.WRITE）；
  3. 队列执行器支持该动作（core.queue_runner.QUEUE_ACTIONS）。

三条都成立才是 AUTO：可以进「全自动」承诺。缺少任何一条就只能 GATED
（人工解锁首跑）或 MANUAL（只出人工作业单，不假装能自动）。
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
    return {
        "channel": key,
        "platform": raw or key,
        "name": name,
        "status": status,
        "tier": tier,
        "write": list(pair) if pair else None,
        "actions": sorted((spec or {}).get("actions") or []),
        "reason": reason,
    }


def matrix() -> List[Dict[str, object]]:
    """覆盖平台知识库全部平台，按 auto → gated → manual 排序（同层按名称）。"""
    rows = [classify(pid) for pid in BY_ID]
    rows.sort(key=lambda r: (_TIER_ORDER.get(str(r["tier"]), 9),
                             str(r["name"]), str(r["channel"])))
    return rows


def auto_channels() -> List[str]:
    """第一期白名单，按注册表顺序返回（x 在前，email 在后）。"""
    ordered = [ch for ch in _REGISTRY
               if classify(ch)["tier"] == AUTO]
    return ordered or sorted({str(r["channel"]) for r in matrix() if r["tier"] == AUTO})


def gated_channels() -> List[str]:
    return sorted({str(r["channel"]) for r in matrix() if r["tier"] == GATED})


def is_auto(channel: str) -> bool:
    return classify(channel)["tier"] == AUTO


def assert_auto(channels: List[str]) -> None:
    bad = [str(c) for c in channels if not is_auto(str(c))]
    if bad:
        raise PlatformError(
            f"第一期只能自动执行 {auto_channels()}；这些渠道不支持：{bad}。"
            "它们可以走人工单，但不计入自动发布量")
