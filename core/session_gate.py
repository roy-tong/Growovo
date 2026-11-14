#!/usr/bin/env python3
"""Session gate —— 账号=浏览器登录态（Growovo 账号策略的底层能力）。

策略：引擎不管理账号身份、不存任何凭据；用户保证调试 Chrome 里目标站点
已登录，动作执行前由本模块检测登录态——有登录态直接放行，缺失明确报缺。
本模块只检测，不登录、不碰 Cookie/token（零凭据纪律与 core/accounts.py、
core/cdp.py 一致）。
"""
import time
from urllib.parse import urlsplit
from typing import Any, Callable, Dict, Optional, Tuple

# 站点探测表：渠道 → 落点 URL + 未登录标记（URL 重定向子串）。
# 渠道键与 channels.REGISTRY 渠道名对齐；gmail 供 email 的 CDP 通道复用。
# 新渠道（如 zhihu）接入执行通道时在此声明即可被 precheck/doctor 覆盖。
PROBES: Dict[str, Dict[str, Any]] = {
    "x": {"url": "https://x.com/home", "bad_markers": ["/login", "/i/flow/login"], "ready": '[data-testid="SideNav_AccountSwitcher_Button"]'},
    "gmail": {"url": "https://mail.google.com", "bad_markers": ["accounts.google.com"], "ready": '[role="navigation"]'},
    "reddit": {"url": "https://old.reddit.com", "bad_markers": ["login"], "ready": 'form.logout a'},
    "linkedin": {"url": "https://www.linkedin.com/feed/", "bad_markers": ["login", "authwall"], "ready": 'button.global-nav__primary-link-me-menu-trigger'},
    "hn": {"url": "https://news.ycombinator.com/submit", "bad_markers": ["login"], "ready": 'a[href^="logout"]'},
    # zhihu 目前是人工档渠道（出作业单）；登录态可见性先行——settings 页未登录会跳 signin
    "zhihu": {"url": "https://www.zhihu.com/settings/profile", "bad_markers": ["signin", "login"], "ready": 'button.AppHeader-profileEntry'},
}

LOGGED_IN = "logged_in"
LOGGED_OUT = "logged_out"
CDP_UNAVAILABLE = "cdp_unavailable"  # 浏览器通道不在：与「未登录」是两种缺失，不互相冒充

_TTL_SECONDS = 300.0
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def clear_cache() -> None:
    """清空探测缓存（测试与强制刷新用）。"""
    _CACHE.clear()


def _state(channel: str, status: str, *, url: str = "", marker: str = "",
           error: str = "") -> Dict[str, Any]:
    return {"channel": channel, "status": status, "url": url or None,
            "marker": marker or None, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "error": error or None}


def check_login(channel: str, *, connect: Optional[Callable[..., Any]] = None,
                now: Optional[Callable[[], float]] = None) -> Dict[str, Any]:
    """检测 channel 在调试浏览器中的登录态，返回 state dict。

    - 结果带 TTL 缓存：队列连续执行不会每条都开页探测。
    - connect 注入点供离线测试（默认 core.cdp.connect）。
    - 未声明探测表的渠道返回 status="unknown"，由调用方决定语义。
    """
    key = str(channel or "").strip().lower()
    if key not in PROBES:
        return _state(key, "unknown")
    clock = now or time.monotonic
    cached = _CACHE.get(key)
    if cached is not None and (clock() - cached[0]) < _TTL_SECONDS:
        return dict(cached[1])
    state = _probe(key, PROBES[key], connect=connect)
    _CACHE[key] = (clock(), dict(state))
    return state


def _probe(key: str, probe: Dict[str, Any], *,
           connect: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    from . import cdp
    do_connect = connect or cdp.connect
    try:
        _browser, page = do_connect()
    except Exception as exc:  # noqa: BLE001 连接层任何失败都归为通道不可用
        return _state(key, CDP_UNAVAILABLE, error=str(exc)[:160])
    try:
        if not cdp.safe_goto(page, probe["url"], attempts=2):
            return _state(key, CDP_UNAVAILABLE, error="goto 失败")
        landed = str(getattr(page, "url", "") or "")
        for marker in probe["bad_markers"]:
            if marker and marker in landed:
                return _state(key, LOGGED_OUT, url=landed, marker=marker)
        expected_host = urlsplit(probe["url"]).hostname
        if urlsplit(landed).hostname != expected_host:
            return _state(key, "unknown", url=landed, error="未到达目标站点，不能确认登录")
        try:
            page.locator(probe["ready"]).first.wait_for(state="visible", timeout=5000)
        except Exception:
            return _state(key, "unknown", url=landed, error="未检测到已登录界面标记，不能确认登录")
        return _state(key, LOGGED_IN, url=landed)
    except Exception as exc:  # noqa: BLE001
        return _state(key, CDP_UNAVAILABLE, error=str(exc)[:160])
    # 不 close 复用页：cdp._reuse_page 的约定是自动化标签页保持稳定。
