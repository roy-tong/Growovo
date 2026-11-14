#!/usr/bin/env python3
"""X 读数原语（UNVERIFIED）：读粉丝数与昨日曝光，数字与截图一起落盘。

三件套口径里的「日均曝光」「新增粉丝」来自这里。诚实分级：按公开 UI 编写、
未经人工对数，所以标 UNVERIFIED——首周每天人工对一次数（截图 vs 手读后台），
对上之后才把 read_stats 的 PROVEN 事实写进 channels/__init__.py 的 note。

设计：解析函数（parse_*）与浏览器操作（read）分离——解析可离线测试，
浏览器部分复用 core/cdp 的 PROVEN 连接模式（launch.command 起的调试 Chrome）。
只读，不发送、不点赞、不关注。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from core import cdp, session_gate

FOLLOWERS_RES = [
    # 英文 UI：profile 页 "1,234 Followers"（含万位缩写 1.2K / 1.5M）
    re.compile(r"([\d][\d,]*(?:\.\d+)?)\s*(K|M)?\s*Followers\b", re.I),
    # 中文 UI：新版把 Followers 译作「关注者」；「正在关注」是 Following，不能误读
    re.compile(r"([\d][\d,]*(?:\.\d+)?)\s*(万|K|M)?\s*(?:位)?关注者", re.I),
    re.compile(r"([\d][\d,]*(?:\.\d+)?)\s*(万|K|M)?\s*(?:位)?粉丝", re.I),
]
IMPRESSIONS_RES = [
    # analytics 页 "Impressions 12,345"（数字可能跨标签，允许中间夹空白）
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
    """从 profile 页文本里取粉丝数；取不到返回 None（不猜）。"""
    for pattern in FOLLOWERS_RES:
        m = pattern.search(text or "")
        if m:
            value = _to_int(m.group(1), m.group(2))
            if value is not None:
                return value
    return None


def parse_impressions(text: str) -> Optional[int]:
    """从 analytics 页文本里取曝光数；取不到返回 None（不猜）。"""
    for pattern in IMPRESSIONS_RES:
        m = pattern.search(text or "")
        if m:
            value = _to_int(m.group(1), m.group(2))
            if value is not None:
                return value
    return None


def read(handle: str, out_dir, *, cdp_url: Optional[str] = None,
         log=print) -> Dict[str, Any]:
    """读一次数：profile 粉丝数 + analytics 昨日曝光，截图与 JSON 同落 out_dir。"""
    handle = handle.lstrip("@").strip()
    if not handle:
        raise ValueError("需要提供 handle（如 --handle verovo）")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # 账号=浏览器登录态：先探登录态再开页——未登录时如实写明原因，
    # 不让「登录态缺失」冒充解析不出来的「取不到」（两者是不同的故障）。
    login = session_gate.check_login(
        "x", connect=(lambda: cdp.connect(cdp_url)) if cdp_url else None)
    login_status = login.get("status")
    if login_status in ("logged_out", "cdp_unavailable"):
        note = ("X 浏览器登录态缺失：请在调试 Chrome 里登录 X（引擎只检测登录态，不管理账号）"
                if login_status == "logged_out"
                else "调试 Chrome 未连接：先运行 ./launch.command")
        result = {
            "schema": "growovo.x-stats/1",
            "handle": handle,
            "read_at": datetime.now().isoformat(timespec="seconds"),
            "followers": None,
            "impressions_yesterday": None,
            "impressions_note": note,
            "impressions_window": (date.today() - timedelta(days=1)).isoformat(),
            "screenshots": [],
            "verification": "UNVERIFIED：登录态缺失，未执行浏览器读数",
            "login_status": login_status,
        }
        path = out / f"stats-{stamp}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
        result["saved"] = str(path)
        return result
    browser, page = cdp.connect(cdp_url)
    try:
        followers = None
        imp = None
        analytics_note = ""
        shots: list = []
        if cdp.safe_goto(page, f"https://x.com/{handle}", log=log):
            page.wait_for_timeout(3000)
            try:
                body = page.inner_text("body")
            except Exception as exc:  # noqa: BLE001 读页面失败按缺数处理
                log(f"读 profile 文本失败：{exc}", "WARN")
                body = ""
            followers = parse_followers(body)
            shot = out / f"stats-{stamp}-profile.png"
            try:
                page.screenshot(path=str(shot))
                shots.append(str(shot))
            except Exception as exc:  # noqa: BLE001
                log(f"截图失败：{exc}", "WARN")
        if cdp.safe_goto(page, "https://x.com/i/account_analytics", log=log):
            page.wait_for_timeout(5000)
            try:
                body = page.inner_text("body")
            except Exception as exc:  # noqa: BLE001
                log(f"读 analytics 文本失败：{exc}", "WARN")
                body = ""
            imp = parse_impressions(body)
            analytics_note = ""
            if imp is None and ("50 followers" in body or "50 位粉丝" in body
                                or "50 个关注者" in body):
                analytics_note = "账号未达 50 关注者，平台不提供曝光明细"
            shot = out / f"stats-{stamp}-analytics.png"
            try:
                page.screenshot(path=str(shot))
                shots.append(str(shot))
            except Exception as exc:  # noqa: BLE001
                log(f"截图失败：{exc}", "WARN")
    finally:
        try:
            browser.close()
        except Exception:  # noqa: BLE001 关不掉不阻塞读数落盘
            pass
    window = (date.today() - timedelta(days=1)).isoformat()
    result = {
        "schema": "growovo.x-stats/1",
        "handle": handle,
        "read_at": datetime.now().isoformat(timespec="seconds"),
        "followers": followers,
        "impressions_yesterday": imp,
        "impressions_note": analytics_note,
        "impressions_window": window,
        "screenshots": shots,
        "verification": "UNVERIFIED：选择器按公开 UI 编写，首周请人工对数",
        "login_status": login_status,
    }
    path = out / f"stats-{stamp}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    result["saved"] = str(path)
    return result


def record(data_dir, stats: Dict[str, Any], *, campaign: str = "") -> Dict[str, int]:
    """把读数落成 outcome 计数（approximated 口径，source_ref 指向快照）。"""
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
