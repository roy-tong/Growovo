#!/usr/bin/env python3
"""CDP 会话助手 —— 自 x-ops-automation 提炼的 PROVEN 模式（架构文档 §2.2）。

约定：先跑仓库根的 launch.command 启动带调试端口的真实 Chrome；
本模块只连接，不做任何登录、不碰任何凭据。
"""
import os
import time

CDP_URL = os.environ.get("MKT_CDP_URL", "http://127.0.0.1:9222")
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


def connect(cdp_url=None):
    """连接调试 Chrome，返回 (browser, page)。playwright 延迟导入以便离线测试 core。"""
    from channels.x.vendor.session_page import connect_browser
    pw = _playwright()
    browser = connect_browser(pw.chromium, cdp_url or CDP_URL)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = _reuse_page(ctx)
    return browser, page


def _reuse_page(ctx):
    """复用调试 Chrome 里已有的自动化标签页；没有才新建一次。

    约定：所有读操作都在同一个标签页里导航，不每次新开（用户能看到的标签
    数应保持稳定）。优先复用上次停在 x.com 的页，其次空白页。
    """
    pages = []
    try:
        pages = list(ctx.pages)
    except Exception:  # noqa: BLE001
        pages = []
    for target in pages:
        try:
            url = target.url
        except Exception:  # noqa: BLE001 页可能已关
            continue
        if url.startswith("https://x.com") or url.startswith("https://twitter.com"):
            return target
    for target in pages:
        try:
            url = target.url
        except Exception:  # noqa: BLE001
            continue
        if url in ("about:blank", "chrome://newtab/", "chrome://newtab"):
            return target
    return ctx.new_page()


def safe_goto(page, url, attempts=3, log=None):
    """goto 带重试（X 偶发 ERR_ABORTED 的通用对策；间隔 3s，不做轰炸式重试）。"""
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            return True
        except Exception as exc:
            if log:
                log(f"goto 失败({attempt}/{attempts}): {str(exc)[:120]}", "WARN")
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
