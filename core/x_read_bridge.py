"""X 只读的 WebBridge 统一入口：搜索、正文、指标、回执定位与账号资料。

Playwright CDP 只保留给显式 `MKT_X_LEGACY_CDP=1` 的兼容路径。默认任务
不再连接 9222：身份观测、页面导航和回执都复用同一个 WebMonitor 受管标签。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from .browser_attach import BrowserAttach
from .bridge import wb

LEGACY_ENV = "MKT_X_LEGACY_CDP"


def legacy_enabled() -> bool:
    return os.environ.get(LEGACY_ENV) == "1"


def open_x(data_dir, url: str, *, group_title: str = "OVO X read"):
    attach = BrowserAttach()
    tab = attach.ensure_managed_tab(
        data_dir, "x", url, group_title=group_title, prefer_visible=True)
    return attach, tab


def navigate(tab, url: str) -> Any:
    try:
        return wb.nav(tab, url)
    except Exception as exc:  # noqa: BLE001
        if "beforeunload" not in str(exc).lower():
            raise
        wb.accept_dialog(tab)
        wb.time.sleep(0.5)
        return wb.nav(tab, url)


def read_page_text(attach, tab, *, max_chars: int = 12000) -> str:
    return attach.read_page(tab, max_chars=max_chars)


def assert_ready(attach, tab) -> None:
    text = read_page_text(attach, tab, max_chars=3000).casefold()
    if "log in" in text or "sign in" in text or "登录" in text:
        raise RuntimeError(
            "x_login_required: WebBridge 页面停在登录页；请先登录操作者浏览器")


def read_articles(attach, tab) -> List[Dict[str, Any]]:
    script = r"""
    (() => {
      const clean = s => String(s || "").replace(/\s+/g, " ").trim();
      return Array.from(document.querySelectorAll('article[data-testid="tweet"]')).map(article => {
        const link = article.querySelector('a[href*="/status/"]');
        const time = article.querySelector('time');
        const author = article.querySelector('[data-testid="User-Name"]');
        const labels = Array.from(article.querySelectorAll('button[aria-label], [role="button"][aria-label]'))
          .map(b => b.getAttribute('aria-label') || b.innerText || '');
        return {
          url: link ? new URL(link.getAttribute("href"), location.href).href : "",
          text: clean(article.innerText),
          author: clean(author ? author.innerText : ""),
          created_at: time ? (time.getAttribute("datetime") || clean(time.textContent)) : "",
          labels: labels,
        };
      });
    })()
    """
    rows = attach.eval(tab, script)
    return rows if isinstance(rows, list) else []
