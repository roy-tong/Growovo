#!/usr/bin/env python3
"""时区窗口策略：什么时候、对哪个地区、用什么语言说话。

依据 docs/timezone-windows.md（含 2026-11-05/08 两次 campaign 的实测对照）。
cadence.py「集中发布」动作中的「定好发布窗口（当地时间 + 北京时间）」由本模块落实。

三条硬规则：
  1. 只在受众本地黄金窗发送；窗口外宁可排队等待。
  2. 受众本地 00:00–06:00 为死区（autopsy 实测：对睡着的观众发帖 = 零互动），
     即使该时刻其他受众在黄金窗内，也不向死区受众投放其定向内容。
  3. 语言与话题跟随受众（见 AUDIENCES），不翻译轰炸。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

# 受众目录：tz 为 UTC 偏移（小时，冬令时基准；夏令时由调用方传入修正量）。
AUDIENCES: Dict[str, Dict] = {
    "jp":      {"tz": 9,  "lang": "ja", "topics": ["质量纪律", "测试文化", "细节工程"]},
    "cn":      {"tz": 8,  "lang": "zh", "topics": ["效率工具", "出海", "独立开发"]},
    "sg":      {"tz": 8,  "lang": "en", "topics": ["SEA startup", "SaaS"]},
    "au-east": {"tz": 11, "lang": "en", "topics": ["solo founder", "indie hacker", "SaaS"]},
    "eu-west": {"tz": 1,  "lang": "en", "topics": ["B2B", "procurement", "open source"]},
    "us-east": {"tz": -5, "lang": "en", "topics": ["scale", "fundraising", "devtools"]},
    "us-west": {"tz": -8, "lang": "en", "topics": ["AI tooling", "open source", "devtools"]},
}

# 受众本地时间的黄金窗（起点, 终点），小时浮点。
GOLDEN_WINDOWS: List[Tuple[float, float]] = [
    (8.0, 10.0),    # 工作日启动段（通勤后第一轮刷推）
    (12.0, 13.5),   # 午休段
    (20.0, 23.0),   # 晚间放松段（实测最强互动窗）
]

# 死区：受众本地 00:00–06:00。autopsy 实测 121 条凌晨帖零互动。
DEAD_ZONE: Tuple[float, float] = (0.0, 6.0)

# 同一受众两次触达的最小本地时间间隔（小时）。
MIN_RETOUCH_HOURS = 6


def local_hour(utc_hour: float, tz: float) -> float:
    """UTC 小时 → 受众本地小时（[0,24) 浮点）。"""
    return (utc_hour + tz) % 24


def in_dead_zone(local: float) -> bool:
    return DEAD_ZONE[0] <= local < DEAD_ZONE[1]


def in_golden(local: float) -> bool:
    return any(s <= local < e for s, e in GOLDEN_WINDOWS)


def audience_states(utc_hour: float, keys: Optional[List[str]] = None) -> Dict[str, Dict]:
    """每个受众在给定 UTC 时刻的状态：本地小时、是否黄金窗、是否死区。"""
    out = {}
    for k in (keys or AUDIENCES):
        local = local_hour(utc_hour, AUDIENCES[k]["tz"])
        out[k] = {
            "local_hour": round(local, 2),
            "golden": in_golden(local) and not in_dead_zone(local),
            "dead": in_dead_zone(local),
        }
    return out


def window_score(utc_hour: float, keys: Optional[List[str]] = None) -> Dict:
    """一个发送时刻的价值：黄金窗受众数（得分）+ 死区受众数（定向禁投名单）。"""
    states = audience_states(utc_hour, keys)
    golden = [k for k, s in states.items() if s["golden"]]
    dead = [k for k, s in states.items() if s["dead"]]
    return {
        "utc_hour": utc_hour,
        "score": len(golden),
        "golden_audiences": golden,
        "dead_audiences": dead,       # 这些受众的定向内容此刻禁投
        "usable_audiences": golden,   # 通用内容可触达的受众
    }


def best_hours(audiences: Optional[List[str]] = None, top: int = 3) -> List[Dict]:
    """未来 24 小时（整点）里得分最高的发送时刻，降序。"""
    now = datetime.now(timezone.utc).hour
    ranked = [window_score(h % 24, audiences) for h in range(now, now + 24)]
    ranked.sort(key=lambda r: (-r["score"], r["utc_hour"]))
    return ranked[:top]


def next_window(audiences: Optional[List[str]] = None) -> Dict:
    """最近的一个得分 ≥1 的整点时刻；含推荐投放对象。"""
    now = datetime.now(timezone.utc)
    for i in range(1, 25):
        at = now + timedelta(hours=i)
        at = at.replace(minute=0, second=0, microsecond=0)
        w = window_score(at.hour, audiences)
        if w["score"] >= 1:
            return {"at_utc": at.isoformat(), **w}
    return {"at_utc": None, "score": 0, "golden_audiences": [], "dead_audiences": []}


def content_hint(audience_key: str) -> Dict:
    """某受众的定向内容提示：语言 + 推荐话题。"""
    a = AUDIENCES.get(audience_key)
    if not a:
        raise KeyError(f"unknown audience: {audience_key}")
    return {"lang": a["lang"], "topics": a["topics"], "tz": a["tz"]}


def plan_language_mix(keys: Optional[List[str]] = None) -> List[str]:
    """多语言排期规则：非英语受众各留 1/3 容量，英语为通用兜底（实测日语帖零稀释）。"""
    langs = [AUDIENCES[k]["lang"] for k in (keys or AUDIENCES)]
    non_en = [l for l in dict.fromkeys(langs) if l != "en"]
    return non_en + ["en"] * max(1, len(non_en) * 2)
