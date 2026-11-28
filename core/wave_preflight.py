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
        candidates = (attach.alive_site_tabs(site)
                      if hasattr(attach, "alive_site_tabs") else [tab])
        if not candidates:
            row.update(reason="tab_not_found", detail="没有可用标签页")
            out["channels"][site] = row
            out["ok"] = False
            log("[preflight/%s] tab_not_found" % site)
            continue
        last_error = None
        for tab in candidates:
            url = _page_url(attach, tab)
            row["tab"] = tab
            row["url"] = url[:400]
            if "/login" in url or "/signin" in url or "/session" in url:
                last_error = ("login_required", "页面停在登录页：%s" % url[:200])
                continue
            try:
                identity = attach.observe_identity(site, tab)
                row.update(ok=True, handle=identity.get("handle") or "")
                break
            except BridgeError as exc:
                last_error = ("login_required" if exc.code == "identity_unavailable"
                              else (exc.code or "identity_error"), str(exc)[:200])
            except Exception as exc:  # noqa: BLE001 - 同上
                last_error = ("identity_error", str(exc)[:200])
        else:
            reason, detail = last_error or ("login_required", "没有可读的登录账号")
            row.update(reason=reason, detail=detail)
            out["channels"][site] = row
            out["ok"] = False
            log("[preflight/%s] %s：%s" % (site, reason, row["detail"]))
            continue
        out["channels"][site] = row
    return out
