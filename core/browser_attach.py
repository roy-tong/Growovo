"""browser-attach 传输：接管操作者已登录浏览器里的现有标签页。

与 cdp 传输的区别：cdp 自己拉起调试端口、自己开新标签页；browser-attach
通过 WebBridge 扩展操作操作者正在使用的窗口，默认只复用现有标签页，找不到
即拒绝；只有底层调用方显式 allow_new=True 才允许新开。不读取、不导出、不保存
cookies 或令牌；页面内的身份探测只读页面上可见的账号标识（X 账号菜单、
Discord 用户面板、GitHub user-login 标记）。
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

from .bridge import wb

SITES = {
    "x": ("https://x.com/", "https://twitter.com/"),
    "github": ("https://github.com/",),
    "discord": ("https://discord.com/",),
}

_JS_X_IDENTITY = r"""
(() => {
  const b = document.querySelector('[data-testid="SideNav_AccountSwitcher_Button"]');
  const text = b ? (b.innerText || '') : '';
  const m = text.match(/@([A-Za-z0-9_]{1,15})/);
  return {ok: !!m, screen_name: m ? m[1] : ''};
})()
"""

_JS_GH_IDENTITY = r"""
(() => {
  const m = document.querySelector('meta[name="user-login"]');
  return {ok: !!m, login: m ? m.getAttribute('content') : ''};
})()
"""

_JS_DC_IDENTITY = r"""
(() => {
  const el = document.querySelector('[class*="panels"] [class*="nameTag"]')
          || document.querySelector('[class*="nameTag"]');
  const lines = el ? (el.innerText || '').split('\n').map(s => s.trim()).filter(Boolean) : [];
  const handle = lines.find(s => /^[a-z0-9._]{2,32}$/.test(s)) || '';
  return {ok: !!handle, username: handle, text: lines.join('|').slice(0, 80)};
})()
"""

_JS_ROUTE_SPA = r"""
(() => {
  const u = new URL(__URL__);
  if (location.origin !== u.origin) { location.href = u.href; return 'full'; }
  history.pushState({}, '', u.pathname + u.search);
  window.dispatchEvent(new PopStateEvent('popstate'));
  return 'spa';
})()
"""

IDENTITY_JS = {"x": _JS_X_IDENTITY, "github": _JS_GH_IDENTITY, "discord": _JS_DC_IDENTITY}


class BridgeError(RuntimeError):
    """结构化桥接失败；code 供上层决定停渠道还是重试。"""

    def __init__(self, code: str, message: str):
        super().__init__("%s: %s" % (code, message))
        self.code = code
        self.message = message


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


class BrowserAttach:
    """操作者浏览器的只读观察 + 接管入口。"""

    def __init__(self, session: Optional[str] = None):
        if session:
            wb.set_session(session)
        self.session = wb.SESSION

    # ---- 连接与标签页 -------------------------------------------------
    def require_daemon(self) -> Dict[str, Any]:
        try:
            payload = wb.call("list_tabs", _timeout=8)
        except Exception as exc:  # 连接层异常统一成结构化错误
            raise BridgeError("bridge_unavailable",
                              "WebBridge 扩展未连接（%s）：请在操作者浏览器打开扩展后重试" % exc)
        if not payload.get("ok"):
            raise BridgeError("bridge_unavailable",
                              "WebBridge 扩展返回错误：%s" % (payload.get("error"),))
        return payload.get("data") or {}

    def list_tabs(self) -> List[Dict[str, Any]]:
        payload = self.require_daemon()
        tabs = payload.get("tabs") or []
        out = []
        for tab in tabs:
            url = str(tab.get("url") or "")
            site = next((name for name, prefixes in SITES.items()
                         if url.startswith(prefixes)), "")
            row = dict(tab)
            row["site"] = site
            out.append(row)
        return out

    def find_tab(self, prefix: str) -> Optional[Dict[str, Any]]:
        for tab in self.list_tabs():
            if str(tab.get("url") or "").startswith(prefix):
                return tab
        return None

    def attach(self, prefix: str) -> int:
        """复用现有标签页；找不到就报错，绝不悄悄新建。"""
        tab = self.find_tab(prefix)
        if not tab:
            raise BridgeError("tab_not_found",
                              "浏览器里没有已打开的 %s 标签页；请先在操作者浏览器打开该站点" % prefix)
        return int(tab["tabId"])

    def _site_tabs(self, site: str) -> List[Dict[str, Any]]:
        """该站点的所有标签页，按站点前缀优先级排序（x.com 先于 twitter.com）。"""
        prefixes = SITES.get(site, ())

        def rank(row):
            url = str(row.get("url") or "")
            for idx, prefix in enumerate(prefixes):
                if url.startswith(prefix):
                    return idx
            return len(prefixes)

        rows = [row for row in self.list_tabs() if rank(row) < len(prefixes)]
        return sorted(rows, key=rank)

    def probe_visibility(self, tab: int) -> str:
        """读标签页 document.visibilityState；读不到返回空串（调用方按不可见处理）。"""
        try:
            return str(wb.js(tab, "document.visibilityState") or "")
        except Exception:
            return ""

    def focus_emulation(self, tab: int, enabled: bool) -> Dict[str, Any]:
        """CDP 焦点模拟：让后台标签页报告 visible，不激活标签页、不抢焦点。

        Emulation.setFocusEmulationEnabled 只改页面看到的状态，不移动窗口或
        标签页；调用方用完必须关闭，避免把标签页留在模拟态。
        """
        try:
            env = wb.call("cdp", tabId=tab,
                          method="Emulation.setFocusEmulationEnabled",
                          params={"enabled": bool(enabled)})
        except Exception as exc:  # noqa: BLE001 - 调用方按模拟不可用处理
            return {"ok": False, "error": str(exc)}
        if env.get("ok"):
            return {"ok": True}
        return {"ok": False, "error": str(env.get("error") or "cdp refused")}

    def _borrow_site_tab(self, site: str) -> Optional[int]:
        """会话里没有该站点时，借用用户已打开的同站标签页。

        浏览器重启或标签页被关掉后，会话的标签页列表会空，但用户浏览器里
        可能仍开着同站页面。attach_tab 只是把已有页借进会话，不新建、不切前台。
        """
        prefixes = SITES.get(site, ())
        try:
            payload = wb.call("list_browser_tabs", _timeout=8)
        except Exception:  # noqa: BLE001 - 旧版扩展没有该工具时按借不到处理
            return None
        if not payload.get("ok"):
            return None
        rows = [row for row in ((payload.get("data") or {}).get("tabs") or [])
                if any(str(row.get("url") or "").startswith(p) for p in prefixes)]
        rows.sort(key=lambda row: bool(row.get("active")))
        for row in rows:
            tab_id = row.get("tabId")
            if not tab_id:
                continue
            try:
                env = wb.call("attach_tab", tabId=tab_id, _timeout=8)
            except Exception:  # noqa: BLE001 - 借不到就试下一个
                continue
            if env.get("ok"):
                return int(tab_id)
        return None

    def _tab_alive(self, tab: int, site: str) -> bool:
        """探活：页面真实地址仍属于该站点才算可用。

        扩展的标签页列表可能保留已崩溃/已关闭标签的旧 URL（例如标题仍是
        github.com、实际却是 chrome-error://）。只按列表 URL 排序会把这种
        死页当成交付目标，后续身份观测必然失败，所以复用前先读真实地址。
        """
        try:
            env = wb.call("evaluate", tabId=int(tab), code="location.href", _timeout=6)
        except Exception:  # noqa: BLE001 - 探活失败按不可用处理
            return False
        if not env.get("ok"):
            return False
        href = str(((env.get("data") or {}).get("value")) or "")
        return any(href.startswith(prefix) for prefix in SITES.get(site, ()))

    def attach_site(self, site: str, prefer_visible: bool = False) -> int:
        """接管该站点已有标签页；prefer_visible 时优先选前台可见标签页。

        X 时间线和发帖框只在可见标签页渲染；后台或锁屏时 visibilityState 为
        hidden，优先选可见页能把“有标签页但页面空”的情况区分开。
        只复用探活通过的标签；会话里没有存活页时先尝试借用用户已打开的同站
        页，仍没有才报 tab_not_found。
        """
        alive: List[int] = []
        for row in self._site_tabs(site):
            tab = int(row["tabId"])
            if self._tab_alive(tab, site):
                alive.append(tab)
                if not prefer_visible:
                    return tab
        if alive:
            for tab in alive:
                if self.probe_visibility(tab) == "visible":
                    return tab
            return alive[0]
        borrowed = self._borrow_site_tab(site)
        if borrowed is not None:
            return borrowed
        raise BridgeError("tab_not_found", "浏览器里没有可用的 %s 标签页" % site)

    def ensure_tab(self, site: str, url: str, group_title: Optional[str] = None,
                   prefer_visible: bool = False, allow_new: bool = False) -> int:
        """默认只复用同站标签页；找不到即拒绝，新开页必须由调用方显式允许。"""
        try:
            return self.attach_site(site, prefer_visible=prefer_visible)
        except BridgeError as exc:
            if exc.code != "tab_not_found":
                raise
        if not allow_new:
            raise BridgeError(
                "tab_not_found",
                "浏览器里没有已打开的 %s 标签页；请先在当前浏览器打开该站点（默认不新开标签页）" % site)
        wb.nav(None, url, new_tab=True, group_title=group_title)
        deadline = 8
        for _ in range(deadline * 4):
            tab = self.find_tab(SITES[site][0])
            if tab:
                return int(tab["tabId"])
            wb.time.sleep(0.25)
        raise BridgeError("tab_open_timeout", "新建 %s 标签页后 8 秒内未出现在标签列表" % site)

    # ---- 页面操作 -----------------------------------------------------
    def eval(self, tab: int, code: str) -> Any:
        return wb.js(tab, code)

    def route_spa(self, tab: int, url: str, route_js: str = "") -> str:
        code = route_js or _JS_ROUTE_SPA
        code = code.replace("__URL__", "new URL(%r)" % url).replace("__URLSTR__", repr(url))
        return wb.js(tab, code)

    def wait(self, tab: int, timeout: int = 20000, **conds) -> Dict[str, Any]:
        return wb.wait(tab, timeout=timeout, **conds)

    def fill_native(self, tab: int, selector: str, text: str) -> Dict[str, Any]:
        return wb.fill(tab, selector, text)

    def press_keys(self, tab: int, keys: str, focus_emulation: bool = False) -> Dict[str, Any]:
        return wb.keys(tab, keys, focus_emulation=focus_emulation)

    def read_page(self, tab: int, max_chars: int = 4000) -> str:
        payload = wb.read(tab, max_chars=max_chars)
        return str(payload.get("text") or payload.get("content") or "")

    def screenshot(self, tab: int, path: str) -> Dict[str, Any]:
        return wb.shot(tab, path)

    # ---- 身份观测 -----------------------------------------------------
    def observe_identity(self, site: str, tab: int) -> Dict[str, Any]:
        """在页面内读当前登录账号；返回固定五字段 + enforced/mode。

        找不到可读身份就抛 identity_unavailable，让调用方 fail closed。
        """
        code = IDENTITY_JS.get(site)
        if not code:
            raise BridgeError("identity_unsupported", "站点 %r 没有身份探测脚本" % site)
        got = wb.js(tab, code) or {}
        if site == "x":
            if not (got.get("ok") and got.get("screen_name")):
                raise BridgeError("identity_unavailable",
                                  "X 页面未登录或账号菜单不可读：%s" % (got,))
            pid, handle = "x:%s" % got["screen_name"], str(got["screen_name"])
        elif site == "github":
            if not got.get("ok") or not got.get("login"):
                raise BridgeError("identity_unavailable", "GitHub 页面未登录（无 user-login 标记）")
            pid, handle = "github:%s" % got["login"], str(got["login"])
        else:
            if not (got.get("ok") and got.get("username")):
                raise BridgeError("identity_unavailable", "Discord 页面未登录或用户面板不可读")
            pid, handle = "discord:%s" % got["username"], str(got["username"])
        if not handle:
            raise BridgeError("identity_unavailable", "身份观测缺少 handle；拒绝在账号不明时执行")
        return {
            "enforced": True,
            "mode": "cdp_observed",
            "observed_platform_identity": pid,
            "handle": handle,
            "identity_proof_ref": "browser-attach:%s@%s" % (tab, _now()),
            "browser_context": "webbridge-session:%s" % self.session,
            "observed_at": _now(),
        }
