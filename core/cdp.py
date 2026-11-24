#!/usr/bin/env python3
"""CDP 会话助手 —— 自 x-ops-automation 提炼的 PROVEN 模式（架构文档 §2.2）。

约定：先跑仓库根的 launch.command 启动带调试端口的真实 Chrome；
本模块只连接调试端口，不做任何登录、不碰任何凭据。它**不会**创建
WebBridge 受管标签页：调试浏览器和操作者日常浏览器是两个不同的进程，
在一个里面建标签再连另一个执行，会让身份探测与执行落在不同浏览器。
需要操作者已登录浏览器时走 `browser-attach` 传输。
"""
import os
import time

CDP_URL = os.environ.get("MKT_CDP_URL", "http://127.0.0.1:9222")
SITE_PREFIXES = {
    "x": ("https://x.com/", "https://twitter.com/"),
    "gmail": ("https://mail.google.com/",),
    "reddit": ("https://old.reddit.com/", "https://www.reddit.com/"),
    "hn": ("https://news.ycombinator.com/",),
    "linkedin": ("https://www.linkedin.com/",),
}
GOTO_TIMEOUT_MS = int(os.environ.get("MKT_GOTO_TIMEOUT_MS", "20000"))
# 这些是导航被中断（上一次跳转未完成、连接抖动），重试有意义；
# 导航超时（Playwright 自身或 net::ERR_TIMED_OUT）说明页面/线路卡住了，
# 重试只会再赔一遍同样的等待，直接返回 False 交给调用方决定。
_RETRYABLE_NAV = ("ERR_ABORTED", "ERR_CONNECTION", "ERR_NETWORK_CHANGED")
_PLAYWRIGHT = None


def _playwright():
    """进程内复用同一个 Playwright 实例。

    每次 connect 都 sync_playwright().start() 会在同进程第二次调用时报
    「Sync API inside the asyncio loop」；prospects 需要连续读多个主页。
    """
    global _PLAYWRIGHT
    if _PLAYWRIGHT is None:
        from playwright.sync_api import sync_playwright
        _PLAYWRIGHT = sync_playwright().start()
    return _PLAYWRIGHT


def connect(cdp_url=None, site="x", data_dir=None, managed_url=None):
    """连接调试 Chrome，返回 (browser, page)。playwright 延迟导入以便离线测试 core。

    data_dir/managed_url 仅为旧调用签名保留；CDP 路径不会创建 WebBridge 标签页。
    身份探测与执行必须使用同一个 CDP 端点。X 的 CDP 只在显式
    `MKT_X_LEGACY_CDP=1` 时允许，默认 X 任务走 browser-attach。
    """
    if site == "x" and os.environ.get("MKT_X_LEGACY_CDP") != "1":
        raise RuntimeError(
            "x_legacy_cdp_required: 默认 X 任务不走调试浏览器；"
            "请用 browser-attach，或显式 MKT_X_LEGACY_CDP=1 / --legacy-cdp")
    from channels.x.vendor.session_page import connect_browser
    pw = _playwright()
    browser = connect_browser(pw.chromium, cdp_url or CDP_URL)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = _reuse_page(ctx, SITE_PREFIXES.get(site, SITE_PREFIXES["x"]))
    return browser, page


def _reuse_page(ctx, url_prefixes=None):
    from channels.x.vendor.session_page import automation_page

    return automation_page(ctx, url_prefixes)


def release_page(page):
    return None


def assert_x_ready(page):
    """调试浏览器若停在 X 登录页，立即给出可行动错误，不把空结果当“没有数据”。"""
    url = str(getattr(page, "url", "") or "").strip()
    low = url.lower()
    if "/login" in low or "i/flow/login" in low:
        raise RuntimeError(
            "x_login_required: 调试浏览器停在 X 登录页；请登录该浏览器，"
            "或改用 browser-attach（如 mkt cycle attach / mkt x stats）")


def safe_goto(page, url, attempts=3, log=None, timeout_ms=None):
    """goto 带重试：只重试可恢复的中断；导航超时直接返回 False，不再重试。"""
    budget = GOTO_TIMEOUT_MS if timeout_ms is None else int(timeout_ms)
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=budget)
            return True
        except Exception as exc:
            message = str(exc)
            retryable = any(marker in message for marker in _RETRYABLE_NAV)
            if log:
                label = "，不重试" if not retryable else ""
                log(f"goto 失败({attempt}/{attempts}{label}): {message[:120]}", "WARN")
            if not retryable or attempt >= attempts:
                return False
            time.sleep(3)
    return False


def type_slow(locator, text, delay=0.012, post_wait=0.8):
    """逐字符输入（事件完整、人类节奏）——所有 CDP 写路径统一用这个。"""
    locator.click()
    time.sleep(0.3)
    locator.press_sequentially(text, delay=int(delay * 1000), timeout=60000)
    time.sleep(post_wait)


def read_back(locator):
    """回读编辑器文本（contenteditable 用 inner_text，textarea 用 input_value）。"""
    try:
        tag = locator.evaluate("el => el.tagName.toLowerCase()")
    except Exception:
        tag = ""
    if tag == "textarea" or tag == "input":
        return locator.input_value()
    return locator.inner_text()
