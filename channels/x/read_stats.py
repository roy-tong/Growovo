#!/usr/bin/env python3
"""X 读数原语（UNVERIFIED）：通过 WebMonitor 受管页读取粉丝数与昨日曝光。

解析函数与浏览器操作分离，首周仍需人工对数。只读，不发送、不点赞、不关注。
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from core.browser_attach import BrowserAttach
from core.bridge import wb

FOLLOWERS_RES = [
    re.compile(r"([\d][\d,]*(?:\.\d+)?)\s*(K|M)?\s*Followers\b", re.I),
    re.compile(r"([\d][\d,]*(?:\.\d+)?)\s*(万|K|M)?\s*(?:位)?关注者", re.I),
    re.compile(r"([\d][\d,]*(?:\.\d+)?)\s*(万|K|M)?\s*(?:位)?粉丝", re.I),
]
IMPRESSIONS_RES = [
    re.compile(r"Impressions[^\d]{0,60}([\d][\d,]*(?:\.\d+)?)\s*(K|M)?", re.I | re.S),
    re.compile(r"曝光量[^\d]{0,60}([\d][\d,]*(?:\.\d+)?)\s*(万|K|M)?", re.I),
]
MULTIPLIERS = {"K": 1_000, "M": 1_000_000, "万": 10_000}


def _to_int(num: str, suffix: Optional[str]) -> Optional[int]:
    try:
        value = float(num.replace(",", ""))
    except ValueError:
        return None
    if suffix:
        value = value * MULTIPLIERS.get(suffix.upper() if suffix.isascii() else suffix, 1)
    return int(round(value))


def parse_followers(text: str) -> Optional[int]:
    for pattern in FOLLOWERS_RES:
        match = pattern.search(text or "")
        if match:
            return _to_int(match.group(1), match.group(2))
    return None


def parse_impressions(text: str) -> Optional[int]:
    for pattern in IMPRESSIONS_RES:
        match = pattern.search(text or "")
        if match:
            return _to_int(match.group(1), match.group(2))
    return None


def _navigate(tab, url):
    try:
        return wb.nav(tab, url)
    except Exception as exc:
        if "beforeunload" not in str(exc).lower():
            raise
        wb.accept_dialog(tab)
        wb.time.sleep(0.5)
        return wb.nav(tab, url)


def _read_until(attach, tab, markers, timeout_sec=20):
    deadline = time.monotonic() + timeout_sec
    text = ""
    while True:
        text = attach.read_page(tab, max_chars=12000)
        lowered = text.lower()
        if any(word in lowered for word in ("captcha", "verify your identity", "unusual activity")):
            raise RuntimeError("platform_challenge")
        if any(str(marker).lower() in lowered for marker in markers):
            return text
        if time.monotonic() >= deadline:
            return text
        wb.time.sleep(0.5)


def read(handle: str, out_dir, *, cdp_url: Optional[str] = None,
         log=print, data_dir=None) -> Dict[str, Any]:
    handle = handle.lstrip("@").strip()
    if not handle:
        raise ValueError("需要提供 handle（如 --handle verovo）")
    from core.protected_content import is_target_suppressed
    if is_target_suppressed(data_dir or out_dir, handle):
        result = {
            "schema": "growovo.x-stats/1", "handle": handle,
            "read_at": datetime.now().isoformat(timespec="seconds"),
            "followers": None, "impressions_yesterday": None,
            "impressions_note": "protected_account：已永久跳过，不再访问",
            "impressions_window": (date.today() - timedelta(days=1)).isoformat(),
            "screenshots": [], "verification": "UNVERIFIED：受保护账号不读取",
            "login_status": "protected_account",
        }
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        path = Path(out_dir) / ("stats-protected-%s.json" % handle)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        result["saved"] = str(path)
        return result
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    profile = f"https://x.com/{handle}"
    followers = None
    impressions = None
    analytics_note = ""
    shots = []
    login_status = "webmonitor_observed"
    try:
        attach = BrowserAttach()
        tab = attach.ensure_managed_tab(
            data_dir or out, "x", profile,
            group_title="OVO engagement cycle", prefer_visible=True)
        attach.observe_identity("x", tab)
        _navigate(tab, profile)
        body = _read_until(attach, tab, ("关注者", "Followers", "粉丝"))
        followers = parse_followers(body)
        shot = out / f"stats-{stamp}-profile.png"
        try:
            attach.screenshot(tab, str(shot))
            shots.append(str(shot))
        except Exception as exc:
            log(f"截图失败：{exc}", "WARN")
        _navigate(tab, "https://x.com/i/account_analytics")
        body = _read_until(
            attach, tab,
            ("Impressions", "曝光量", "50 followers", "50 位粉丝", "50 个关注者"))
        impressions = parse_impressions(body)
        if impressions is None and ("50 followers" in body or "50 位粉丝" in body
                                    or "50 个关注者" in body):
            analytics_note = "账号未达 50 关注者，平台不提供曝光明细"
        shot = out / f"stats-{stamp}-analytics.png"
        try:
            attach.screenshot(tab, str(shot))
            shots.append(str(shot))
        except Exception as exc:
            log(f"截图失败：{exc}", "WARN")
    except Exception as exc:
        code = str(getattr(exc, "code", "") or "read_error")
        login_status = code
        analytics_note = f"WebMonitor 受管页不可用（{code}）：{str(exc)[:180]}"
    result = {
        "schema": "growovo.x-stats/1",
        "handle": handle,
        "read_at": datetime.now().isoformat(timespec="seconds"),
        "followers": followers,
        "impressions_yesterday": impressions,
        "impressions_note": analytics_note,
        "impressions_window": (date.today() - timedelta(days=1)).isoformat(),
        "screenshots": shots,
        "verification": "UNVERIFIED：选择器按公开 UI 编写，首周请人工对数",
        "login_status": login_status,
    }
    path = out / f"stats-{stamp}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    result["saved"] = str(path)
    return result


def record(data_dir, stats: Dict[str, Any], *, campaign: str = "") -> Dict[str, int]:
    from core.attribution import OutcomeLedger
    ol = OutcomeLedger(Path(data_dir))
    written: Dict[str, int] = {}
    source = str(stats.get("saved") or "x-read-stats")
    if isinstance(stats.get("followers"), int):
        ol.add_count(metric="x_followers", value=stats["followers"],
                     window=str(stats.get("read_at") or "")[:10],
                     source_ref=source, approximated=True, campaign=campaign)
        written["x_followers"] = stats["followers"]
    if isinstance(stats.get("impressions_yesterday"), int):
        ol.add_count(metric="x_impressions", value=stats["impressions_yesterday"],
                     window=str(stats.get("impressions_window") or "")[:10],
                     source_ref=source, approximated=True, campaign=campaign)
        written["x_impressions"] = stats["impressions_yesterday"]
    return written
