"""browser-attach 传输适配器；由 core.action_runner 统一调用。

每次写都按同一顺序过门：暂停总闸 → DNC → owner-attested 授权 → 页面内身份观测
→ 频道日预算 → 执行 → 回读命中 → 账本。回读不命中记 indeterminate，绝不记成功。
遇到验证码/限流/风控关键词立刻抛 StopChannel，由上层停该渠道。这里只写一次账本；
action_runner 负责规范回执、任务结果与统一操作日志，不重复记账或预留额度。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from .browser_attach import BridgeError, BrowserAttach
from .bridge import discord_engine, gh_post, x_engine
from .gates import load_suppressions
from .ledger import Ledger
from .platform_compliance import (
    ComplianceError, load_owner_attested, require_owner_attested_browser_write,
)
from .safety import is_paused

STOP_WORDS = {
    "x": ("captcha", "verification", "automation", "rate limit", "429", "suspended",
          "verify your identity", "unusual activity"),
    "github": ("403", "429", "abuse", "rate limit", "422"),
    "discord": ("captcha", "rate limit", "429", "verification", "slowmode"),
}


class ActionBlocked(RuntimeError):
    """门禁拒绝：不是技术故障，重试也不会变。"""

    def __init__(self, code: str, message: str):
        super().__init__("%s: %s" % (code, message))
        self.code = code


class StopChannel(RuntimeError):
    """平台风控信号：停该渠道，不重试、不绕行。"""

    def __init__(self, channel: str, reason: str):
        super().__init__("%s 停手：%s" % (channel, reason))
        self.channel = channel
        self.reason = reason


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _hit_stop_word(channel: str, text: str) -> str:
    low = str(text or "").lower()
    for word in STOP_WORDS.get(channel, ()):
        if word in low:
            return word
    return ""


def _suppressed(data_dir, target: str) -> bool:
    suppressed = load_suppressions(data_dir)
    value = str(target or "").strip()
    return any(v in suppressed for v in {value, value.lstrip("@"), "@" + value.lstrip("@")})


def _ledger(data_dir) -> Ledger:
    return Ledger(Path(data_dir) / "ledger.jsonl")


def _parse_gh_target(target: str):
    """接受 'owner/repo#123' 或 issue/PR 的网页 URL。"""
    text = str(target or "").strip()
    m = re.match(r"^([\w.-]+/[\w.-]+)#(\d+)$", text)
    if m:
        return m.group(1), int(m.group(2))
    m = re.search(r"github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull)/(\d+)", text)
    if m:
        return m.group(1), int(m.group(2))
    raise ActionBlocked("target_invalid", "GitHub 目标需为 owner/repo#123 或 issue/PR 链接：%r" % text)


def _write_body(data_dir, channel: str, text: str) -> Path:
    outbox = Path(data_dir) / "state" / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / ("%s-%s.md" % (channel, uuid.uuid4().hex[:10]))
    path.write_text(text, encoding="utf-8")
    return path


def _fail_state(data_dir, channel: str) -> Path:
    return Path(data_dir) / "state" / ("fail-%s.json" % channel)


def _bump_fail(data_dir, channel: str) -> int:
    path = _fail_state(data_dir, channel)
    count = 0
    try:
        count = int(json.loads(path.read_text(encoding="utf-8")).get("count") or 0)
    except Exception:
        count = 0
    count += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"count": count, "at": _now()}, ensure_ascii=False), encoding="utf-8")
    return count


def _clear_fail(data_dir, channel: str) -> None:
    path = _fail_state(data_dir, channel)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def _send_attempted(channel: str, engine: Dict[str, Any]) -> bool:
    """发送控件已被触发，但后续客户端确认或回读可能失败。"""
    if not isinstance(engine, dict):
        return False
    if channel == "discord":
        return bool((engine.get("enter") or {}).get("ok"))
    if channel == "x":
        attempts = [engine, engine.get("send"), engine.get("sent")]
        return any(isinstance(item, dict) and (
            bool(item.get("ok")) or bool(item.get("clickedAt"))
            or item.get("clickMs") is not None) for item in attempts[1:])
    return False


def execute(channel: str, action: str, data_dir, *, text: str, target: Optional[str] = None,
            go: bool = False, campaign: str = "", actor: str = "",
            log=print) -> Dict[str, Any]:
    """跑一条 browser-attach 写；返回含 ok / result / evidence 的结果字典。

    actor 是这条动作针对的外部对象（GitHub 作者、X handle、Discord 用户名），
    用于每夜每对象动作上限；target 是投递位置（issue、推文 URL、频道 ID）。
    """
    channel = str(channel).lower()
    action = str(action).lower()
    text = str(text or "").strip()
    if (channel, action) not in {
            ("x", "reply"), ("x", "publish"), ("github", "reply"), ("discord", "publish")}:
        raise ActionBlocked("unsupported", "browser-attach 不支持 %s/%s" % (channel, action))
    if not text:
        raise ActionBlocked("text_empty", "正文为空，拒绝发送")
    if action == "reply" and not target:
        raise ActionBlocked("target_required", "回复必须给出目标")
    if is_paused(data_dir):
        raise ActionBlocked("paused", "全局暂停中（mkt pause --off 恢复）——写操作拒跑")
    if target and _suppressed(data_dir, target):
        raise ActionBlocked("suppressed", "目标在抑制名单（DNC/bounced/suppressed）: %s" % target)

    ledger = _ledger(data_dir)
    ref = "ba:%s:%s:%s" % (channel, action, uuid.uuid4().hex[:10])
    done_states = ("committed", "indeterminate")
    content_key = hashlib.sha1(text[:200].encode("utf-8")).hexdigest()[:12] \
        if action == "publish" else ""
    if action == "reply" and target and ledger.already(channel=channel, target=str(target),
                                                       ok=done_states):
        return {"ok": True, "skipped": True, "reason": "already_done", "target": target, "ref": ref}
    if action == "publish":
        for row in ledger.results(channel=channel, action=action):
            if row.get("result") not in done_states:
                continue
            if row.get("content_key") and row.get("content_key") == content_key:
                return {"ok": True, "skipped": True, "reason": "already_done_content",
                        "target": target, "content_key": content_key, "ref": ref}
        if channel == "discord" and campaign and target:
            if ledger.already(channel=channel, target=str(target), campaign=campaign,
                              ok=done_states):
                return {"ok": True, "skipped": True, "reason": "already_done",
                        "target": target, "ref": ref}
    if not go:
        return {"ok": True, "dry": True, "channel": channel, "action": action,
                "target": target, "text_len": len(text), "ref": ref}

    attach = BrowserAttach()
    tab = attach.ensure_tab(channel if channel != "github" else "github",
                           {"x": "https://x.com/home", "github": "https://github.com/notifications",
                            "discord": "https://discord.com/channels/@me"}[channel],
                           group_title="OVO engagement cycle",
                           prefer_visible=(channel == "x"))
    focus = {"used": False, "before": "", "after": ""}
    if channel == "x":
        visibility = attach.probe_visibility(tab)
        focus["before"] = visibility
        if visibility != "visible":
            # 后台标签页不渲染时间线；先用 CDP 焦点模拟恢复，不激活标签页。
            attempt = attach.focus_emulation(tab, True)
            recovered = attach.probe_visibility(tab) if attempt.get("ok") else ""
            focus["after"] = recovered
            if not (attempt.get("ok") and recovered == "visible"):
                if attempt.get("ok"):
                    attach.focus_emulation(tab, False)
                raise ActionBlocked(
                    "tab_hidden",
                    "X 标签页 visibilityState=%r（后台或屏幕锁定），焦点模拟不可用："
                    "本条不发送、不占额度" % visibility)
            focus["used"] = True
    identity = attach.observe_identity(channel, tab)
    try:
        permit = require_owner_attested_browser_write(
            channel, action, data_dir, go=True,
            actual_transport="browser-attach", identity=identity)
    except ComplianceError as exc:
        raise ActionBlocked(exc.code, str(exc)) from exc

    from .throttle import Throttle, ThrottleError
    try:
        Throttle(Path(data_dir) / "state").reserve(channel, action, 1)
    except ThrottleError as exc:
        raise ActionBlocked("quota_exhausted", str(exc)) from exc

    result: Dict[str, Any] = {"ok": False, "channel": channel, "action": action,
                              "target": target, "ref": ref, "identity": identity,
                              "identity_handle": identity["handle"],
                              "authorization": permit, "focus_emulation": focus}
    written = ""
    try:
        if channel == "x" and action == "reply":
            out = x_engine.reply_native(tab, str(target), text)
            result["engine"] = out
            if out.get("ok"):
                status = out.get("status") or {}
                sid = str(status.get("id") or "")
                if sid.isdigit():
                    written = sid
                    result["status_binding"] = str(status.get("source") or "")
                    result["readback_ref"] = sid
                    result["ok"] = True
                else:
                    key = "k%d" % (abs(hash(target)) % 10 ** 8)
                    got = x_engine.readback(tab, identity["handle"],
                                            [{"key": key, "prefix": text[:40],
                                              "url": str(target)}]) or {}
                    written = str(got.get(key) or "")
                    result["status_binding"] = "readback" if written else ""
                    result["readback_ref"] = written
                    result["ok"] = bool(written)
        elif channel == "x" and action == "publish":
            out = x_engine.original(tab, text)
            result["engine"] = out
            if out.get("ok"):
                status = out.get("status") or {}
                sid = str(status.get("id") or "")
                if sid.isdigit():
                    written = sid
                    result["status_binding"] = str(status.get("source") or "")
                    result["readback_ref"] = sid
                    result["ok"] = True
                else:
                    key = "k%d" % (abs(hash(text[:40])) % 10 ** 8)
                    got = x_engine.readback(tab, identity["handle"],
                                            [{"key": key, "prefix": text[:40]}]) or {}
                    written = str(got.get(key) or "")
                    result["status_binding"] = "readback" if written else ""
                    result["readback_ref"] = written
                    result["ok"] = bool(written)
        elif channel == "github":
            repo, num = _parse_gh_target(str(target))
            body = _write_body(data_dir, "github", text)
            out = gh_post.post(repo, num, str(body))
            result["engine"] = out
            if out.get("ok"):
                check = gh_post.verify(repo, num, text[:60])
                result["readback_ref"] = check.get("url") or ""
                result["ok"] = bool(check.get("ok"))
                if result["ok"] and check.get("url"):
                    result["receipt"] = {"url": str(check["url"])}
        else:
            channel_id = str(target or "")
            if not channel_id.isdigit():
                raise ActionBlocked("target_invalid", "Discord 目标必须是频道 ID：%r" % channel_id)
            out = discord_engine.post(tab, channel_id, text)
            result["engine"] = out
            result["readback_ref"] = str(out.get("readback") or "")[:200]
            result["ok"] = bool(out.get("ok"))
            server_id = str(out.get("serverId") or "")
            if result["ok"] and server_id.isdigit():
                # serverId 是回读到的消息 snowflake；没有完整证据时不编造 permalink。
                result["receipt"] = {"id": server_id}
        if channel == "x" and result["ok"] and written.isdigit():
            # readback 目前只证明前缀命中；ID/permalink 可落证，但不伪称全文哈希一致。
            result["receipt"] = {
                "id": written,
                "url": "https://x.com/%s/status/%s" % (identity["handle"], written),
            }
    except x_engine.Stop as exc:
        ledger.append(channel=channel, transport="browser-attach", action=action,
                      campaign=campaign, target=str(target or "-"), result="rejected",
                      ref=ref, detail="平台风控信号：%s" % exc,
                      identity_handle=identity["handle"], actor=actor)
        raise StopChannel(channel, str(exc)) from exc
    except BridgeError as exc:
        result["error"] = str(exc)
        if exc.code in ("bridge_unavailable", "tab_not_found", "tab_open_timeout"):
            ledger.append(channel=channel, transport="browser-attach", action=action,
                          campaign=campaign, target=str(target or "-"), result="rejected",
                          ref=ref, detail=str(exc), actor=actor)
        raise
    except ActionBlocked:
        raise
    except Exception as exc:  # 引擎内部异常：结果未知，记 indeterminate
        result["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
    finally:
        if focus["used"]:
            attach.focus_emulation(tab, False)

    engine_text = json.dumps(result.get("engine") or {}, ensure_ascii=False)
    stop_word = _hit_stop_word(channel, engine_text if not result["ok"] else "")
    if stop_word and not result["ok"]:
        detail = "页面出现风控关键词 %r" % stop_word
        if channel == "github" and stop_word == "422":
            # 422 多为正文被拒（重复内容/触发过滤），连续两次才判定为平台风控
            if _bump_fail(data_dir, channel) < 2:
                result["error"] = result.get("error") or "GitHub 422 正文被拒（第 1 次，不停止渠道）"
            else:
                ledger.append(channel=channel, transport="browser-attach", action=action,
                              campaign=campaign, target=str(target or "-"), result="rejected",
                              ref=ref, detail=detail,
                              identity_handle=identity["handle"], actor=actor)
                raise StopChannel(channel, "连续两次 422，判定为平台拒绝")
        else:
            ledger.append(channel=channel, transport="browser-attach", action=action,
                          campaign=campaign, target=str(target or "-"), result="rejected",
                          ref=ref, detail=detail,
                          identity_handle=identity["handle"], actor=actor)
            raise StopChannel(channel, "页面出现 %r" % stop_word)

    engine = result.get("engine") or {}
    if result["ok"]:
        state = "committed"
        _clear_fail(data_dir, channel)
    elif written or engine.get("ok") or _send_attempted(channel, engine):
        # 点击发送后回读失败不能证明未发送；消费队列并等待人工核查，绝不自动重试。
        state = "indeterminate"
    else:
        state = "failed"
    detail = (result.get("error") or engine.get("err") or "") if not result["ok"] else ""
    if detail and engine:
        detail = "%s | %s" % (detail, json.dumps(engine, ensure_ascii=False)[:1200])
    ledger.append(channel=channel, transport="browser-attach", action=action,
                  campaign=campaign, target=str(target or "-"), result=state, ref=ref,
                  evidence=result.get("readback_ref") or "",
                  identity_handle=identity["handle"], actor=actor,
                  detail=str(detail)[:1200], content_key=content_key,
                  mode="owner_attested_session")
    result["ledger_result"] = state
    result["result"] = "success" if state == "committed" else state
    log("[%s/%s] %s%s" % (channel, action, state,
                          "" if result["ok"] else " (%s)" % (detail or "回读未命中")))
    return result


def pending_authorization(data_dir) -> Dict[str, Any]:
    """给 CLI 展示用：三个平台当前授权窗口与停止条件（不触发任何动作）。"""
    out = {}
    for platform in ("x", "github", "discord"):
        auth = load_owner_attested(data_dir, platform)
        out[platform] = {
            "present": bool(auth),
            "route": auth.get("route"),
            "approved_by": auth.get("approved_by"),
            "window": [auth.get("window_start"), auth.get("window_end")],
            "scopes": auth.get("scopes") or [],
            "revoked": bool(auth.get("revoked")),
        }
    return out
