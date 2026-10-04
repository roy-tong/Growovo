#!/usr/bin/env python3
"""CDP 会话助手 —— 自 x-ops-automation 提炼的 PROVEN 模式（架构文档 §2.2）。

约定：先跑仓库根的 launch.command 启动带调试端口的真实 Chrome；
本模块只连接，不做任何登录、不碰任何凭据。
"""
import os
import time

CDP_URL = os.environ.get("MKT_CDP_URL", "http://127.0.0.1:9222")


def connect(cdp_url=None):
    """连接调试 Chrome，返回 (browser, page)。playwright 延迟导入以便离线测试 core。"""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(cdp_url or CDP_URL)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = ctx.new_page()
    return browser, page


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
