#!/usr/bin/env python3
"""波次前只读预检：标签页、登录状态、被重定向到登录页。

预检不新建标签页、不发送、不导航。失败只说明这个渠道本波不可用，调用方
应跳过该渠道并记录原因，而不是把动作记成发送失败或触发渠道熔断。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .browser_attach import BridgeError


def _page_url(attach, tab: int) -> str:
    try:
        return str(attach.eval(tab, "location.href") or "")
    except Exception:  # noqa: BLE001 - 读不到 URL 不改变结论，交给身份检查
        return ""


def check(policy: Dict[str, Any], *, attach=None, session: Optional[str] = None,
          log=print) -> Dict[str, Any]:
    """逐平台检查可用性；返回 {ok, channels: {site: {...}}}。

    reason 取值：
      tab_not_found    浏览器里没有该站点的标签页
      login_required   页面在登录页，或读不到登录账号
      identity_error   身份探测的其他失败
    """
    if attach is None:
        from .browser_attach import BrowserAttach
        attach = BrowserAttach(session)
    out: Dict[str, Any] = {"ok": True, "channels": {}}
    for site in ("x", "github", "discord"):
        if site not in (policy.get("platforms") or {}):
            continue
        row: Dict[str, Any] = {"site": site, "ok": False, "reason": ""}
        try:
            tab = attach.attach_site(site)
        except BridgeError as exc:
            row.update(reason=exc.code or "tab_not_found", detail=str(exc)[:200])
            out["channels"][site] = row
            out["ok"] = False
            log("[preflight/%s] %s：%s" % (site, row["reason"], row["detail"]))
            continue
        except Exception as exc:  # noqa: BLE001 - 预检本身失败不能拖垮整波
            row.update(reason="preflight_error", detail=str(exc)[:200])
            out["channels"][site] = row
            out["ok"] = False
            log("[preflight/%s] preflight_error：%s" % (site, row["detail"]))
            continue
        row["tab"] = tab
        url = _page_url(attach, tab)
        row["url"] = url[:400]
        if "/login" in url or "/signin" in url or "/session" in url:
            row.update(reason="login_required", detail="页面停在登录页：%s" % url[:200])
            out["channels"][site] = row
            out["ok"] = False
            log("[preflight/%s] login_required：%s" % (site, row["detail"]))
            continue
        try:
            identity = attach.observe_identity(site, tab)
            row.update(ok=True, handle=identity.get("handle") or "")
        except BridgeError as exc:
            reason = "login_required" if exc.code == "identity_unavailable" else (
                exc.code or "identity_error")
            row.update(reason=reason, detail=str(exc)[:200])
            out["channels"][site] = row
            out["ok"] = False
            log("[preflight/%s] %s：%s" % (site, reason, row["detail"]))
            continue
        except Exception as exc:  # noqa: BLE001 - 同上
            row.update(reason="identity_error", detail=str(exc)[:200])
            out["channels"][site] = row
            out["ok"] = False
            log("[preflight/%s] identity_error：%s" % (site, row["detail"]))
            continue
        out["channels"][site] = row
    return out
