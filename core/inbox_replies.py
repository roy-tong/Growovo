"""通知回读：只读收集「别人回复我」的条目，供每波第一段优先回复。

流程：collect（只读）→ plan_replies（排序、去重、每对象上限）→ 由调用方写正文
→ action_runner(transport="browser-attach") 发送。本模块不发送、不点开新标签页。
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any, Dict, List, Optional

from .browser_attach import BrowserAttach
from .bridge import discord_engine, gh_post, wb

PLATFORM_ORDER = ("x", "github", "discord")


def capabilities() -> Dict[str, Dict[str, Any]]:
    """实际入站覆盖；不支持的类型必须显式出现，不能用空数组冒充没有消息。"""
    return {
        "x": {"native_reply": True, "direct_message": False,
              "source": "notifications/mentions"},
        "github": {"issue_comment": True, "direct_message": "not_applicable",
                   "source": "unread notifications + latest comment"},
        "discord": {"channel_post_mention": True, "native_reply": False,
                    "direct_message": False, "source": "allowed channels"},
    }

# X 通知页：一条 article 就是一个提及/回复。只取正文、作者、时间和永久链接。
_JS_X_MENTIONS = r"""
(() => {
  const out = [];
  const arts = Array.from(document.querySelectorAll('article[data-testid="tweet"]'));
  for (const a of arts) {
    const textEl = a.querySelector('[data-testid="tweetText"]');
    const text = textEl ? (textEl.innerText || '').trim() : '';
    if (!text) continue;
    const timeEl = a.querySelector('time');
    const when = timeEl ? (timeEl.getAttribute('datetime') || '') : '';
    let url = '';
    for (const l of a.querySelectorAll('a[href*="/status/"]')) {
      const h = l.getAttribute('href') || '';
      if (/^\/[A-Za-z0-9_]+\/status\/\d+$/.test(h)) { url = 'https://x.com' + h; break; }
    }
    const nameEl = a.querySelector('[data-testid="User-Name"] a[href^="/"]');
    const handle = nameEl ? (nameEl.getAttribute('href') || '').replace(/^\//, '').split('/')[0] : '';
    if (!url || !handle) continue;
    out.push({url: url, handle: handle, text: text.slice(0, 900), received_at: when});
  }
  return {items: out, path: location.pathname};
})()
"""

_JS_DC_REPLIES = r"""
(() => {
  const me = %s, cid = %s;
  const low = me.toLowerCase();
  const out = [];
  const rows = Array.from(document.querySelectorAll('li[id^="chat-messages-"]'));
  for (const li of rows) {
    if (li.id.indexOf('chat-messages-' + cid + '-') !== 0) continue;
    const mid = li.id.split('-').pop();
    const ctxEl = li.querySelector('div[id^="message-reply-context"]');
    const ctx = ctxEl ? (ctxEl.innerText || '').replace(/\s+/g, ' ').trim() : '';
    const bodyEl = li.querySelector('div[id^="message-content-"]');
    const text = bodyEl ? (bodyEl.innerText || '').trim() : '';
    if (!text) continue;
    const h3 = li.querySelector('h3');
    let author = '';
    if (h3) {
      const parts = Array.from(h3.querySelectorAll('span'))
        .map(x => (x.innerText || '').trim()).filter(Boolean);
      author = parts.length ? parts[0] : (h3.innerText || '').trim().split('\n')[0];
    }
    const repliedToMe = (ctx && ctx.toLowerCase().indexOf(low) !== -1)
      || (text.toLowerCase().indexOf('@' + low) !== -1);
    if (!repliedToMe) continue;
    if (author && author.toLowerCase() === low) continue;
    const timeEl = li.querySelector('time');
    out.push({message_id: mid, author: author, text: text.slice(0, 900),
              reply_context: ctx.slice(0, 200),
              received_at: timeEl ? (timeEl.getAttribute('datetime') || '') : ''});
  }
  return {items: out, cid: cid};
})()
"""


def _now() -> _dt.datetime:
    return _dt.datetime.now().astimezone()


def _parse_time(value: Any) -> Optional[_dt.datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        got = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if got.tzinfo is None:
        got = got.astimezone()
    return got


def _poll_value(attach: BrowserAttach, tab: int, js: str, *, timeout: float = 12.0,
                interval: float = 0.4) -> Dict[str, Any]:
    """轮询到 JS 返回非空 items，或超时返回最后一次结果。"""
    deadline = _now().timestamp() + timeout
    last: Dict[str, Any] = {}
    while _now().timestamp() < deadline:
        last = attach.eval(tab, js) or {}
        if last.get("items"):
            return last
        wb.time.sleep(interval)
    return last


_JS_X_SCROLL = "(() => { window.scrollBy(0, 1200); return {y: window.scrollY}; })()"


def _collect_mentions_scrolled(attach: BrowserAttach, tab: int, *, rounds: int = 8,
                               pause: float = 0.9) -> List[Dict[str, Any]]:
    """边读边滚，按 status 链接去重合并。

    X 的通知列表是虚拟列表：滚动后顶部的 article 会卸载，所以每轮先把当前屏
    并进 seen 再往下滚。连续 3 轮没有新条目就停，避免滚到很早的历史通知。
    """
    seen: Dict[str, Dict[str, Any]] = {}
    stalled = 0
    for _ in range(max(1, rounds)):
        got = attach.eval(tab, _JS_X_MENTIONS) or {}
        added = 0
        for item in got.get("items") or []:
            url = str((item or {}).get("url") or "")
            if not url:
                continue
            if url not in seen:
                added += 1
            seen[url] = item
        stalled = 0 if added else stalled + 1
        if stalled >= 3:
            break
        try:
            attach.eval(tab, _JS_X_SCROLL)
        except Exception:  # noqa: BLE001 - 滚不动就用手上已有的
            break
        wb.time.sleep(pause)
    return list(seen.values())


def collect_x(attach: BrowserAttach, handle: str, *, lookback_hours: int = 36) -> List[Dict[str, Any]]:
    tab = attach.ensure_tab("x", "https://x.com/notifications/mentions",
                            group_title="OVO engagement cycle")
    wb.nav(tab, "https://x.com/notifications/mentions")
    wb.time.sleep(1.2)
    _poll_value(attach, tab, _JS_X_MENTIONS)
    cutoff = _now() - _dt.timedelta(hours=lookback_hours)
    out = []
    for item in _collect_mentions_scrolled(attach, tab):
        if str(item.get("handle") or "").lower() == handle.lower():
            continue
        when = _parse_time(item.get("received_at"))
        if when and when < cutoff:
            continue
        out.append({
            "platform": "x",
            "action": "reply",
            "thread_ref": item["url"],
            "target": item["url"],
            "author": item.get("handle") or "",
            "handle": item.get("handle") or "",
            "their_text": item.get("text") or "",
            "received_at": item.get("received_at") or "",
            "capability": "native_reply",
        })
    return out


def _github_latest_comment(subject: Dict[str, Any]) -> Dict[str, Any]:
    url = str(subject.get("latest_comment_url") or "").strip()
    if not url:
        return {}
    path = url.replace("https://api.github.com/", "", 1)
    res = gh_post.run(["api", "--method", "GET", path])
    if not res.get("ok"):
        return {}
    try:
        got = json.loads(res.get("out") or "{}")
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def collect_github(handle: str, *, lookback_hours: int = 36) -> List[Dict[str, Any]]:
    res = gh_post.run(["api", "--method", "GET", "notifications?all=false&per_page=50"])
    if not res.get("ok"):
        return []
    try:
        rows = json.loads(res.get("out") or "[]")
    except ValueError:
        return []
    cutoff = _now() - _dt.timedelta(hours=lookback_hours)
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason") or "")
        if reason not in ("mention", "comment", "team_mention"):
            continue
        subject = row.get("subject") or {}
        if not isinstance(subject, dict):
            continue
        url = str(subject.get("url") or "")
        m = re.search(r"/repos/([^/]+/[^/]+)/(?:issues|pulls)/(\d+)$", url)
        if not m:
            continue
        when = _parse_time(row.get("updated_at"))
        if when and when < cutoff:
            continue
        repo, num = m.group(1), m.group(2)
        target = "%s#%s" % (repo, num)
        comment = _github_latest_comment(subject)
        author = str((comment.get("user") or {}).get("login") or "")
        if not author:
            owner = (row.get("repository") or {}).get("owner") or {}
            author = str(owner.get("login") or "") if isinstance(owner, dict) else ""
        context = str(comment.get("body") or "").strip()
        if not context:
            context = "%s（%s；评论正文未取到）" % (subject.get("title") or "", reason)
        out.append({
            "platform": "github",
            "action": "reply",
            "thread_ref": str(comment.get("html_url") or target),
            "target": target,
            "author": author,
            "handle": author,
            "their_text": context,
            "received_at": str(comment.get("updated_at") or row.get("updated_at") or ""),
            "capability": "issue_comment",
            "context_incomplete": not bool(comment.get("body")),
            "unread": bool(row.get("unread")),
        })
    return out


def collect_discord(attach: BrowserAttach, username: str, channel_ids: List[str]) -> List[Dict[str, Any]]:
    if not channel_ids:
        return []
    tab = attach.ensure_tab("discord", "https://discord.com/channels/@me",
                            group_title="OVO engagement cycle")
    out = []
    for cid in channel_ids:
        path = "/channels/%s/%s" % (discord_engine.GUILD, cid)
        try:
            wb.ev(tab, discord_engine.NAV % json.dumps(path))
        except Exception:  # noqa: BLE001 - 单个频道失败不拖垮整轮回读
            continue
        deadline = _now().timestamp() + 8
        ready = {}
        while _now().timestamp() < deadline:
            ready = wb.ev(tab, discord_engine.READY % (
                json.dumps(path), json.dumps(cid), json.dumps(discord_engine.COMPOSER))).get("value") or {}
            if ready.get("ready"):
                break
            wb.time.sleep(0.2)
        if not ready.get("ready"):
            continue
        got = attach.eval(tab, _JS_DC_REPLIES % (json.dumps(username), json.dumps(cid))) or {}
        for item in got.get("items") or []:
            out.append({
                "platform": "discord",
                "action": "publish",
                "thread_ref": "%s:%s" % (cid, item.get("message_id") or ""),
                "target": cid,
                "channel_id": cid,
                "author": item.get("author") or "",
                "handle": item.get("author") or "",
                "their_text": item.get("text") or "",
                "received_at": item.get("received_at") or "",
                "capability": "channel_post_mention",
            })
    return out


def collect_detailed(data_dir, *, attach: Optional[BrowserAttach] = None,
                     lookback_hours: int = 36, channel_ids: Optional[List[str]] = None,
                     log=print) -> Dict[str, Any]:
    """只读收集三平台回复；空结果与读取失败分开报告。"""
    attach = attach or BrowserAttach()
    out: Dict[str, List[Dict[str, Any]]] = {"x": [], "github": [], "discord": []}
    errors: Dict[str, str] = {}
    handles: Dict[str, str] = {}
    try:
        handle = attach.observe_identity("x", attach.ensure_tab(
            "x", "https://x.com/home", group_title="OVO engagement cycle"))["handle"]
        handles["x"] = handle
        out["x"] = collect_x(attach, handle, lookback_hours=lookback_hours)
    except Exception as exc:  # noqa: BLE001 - 回读失败不阻断其他平台
        errors["x"] = str(exc)[:300]
        log("inbox/x 失败：%s" % exc)
    try:
        out["github"] = collect_github(handles.get("github", ""), lookback_hours=lookback_hours)
    except Exception as exc:  # noqa: BLE001
        errors["github"] = str(exc)[:300]
        log("inbox/github 失败：%s" % exc)
    try:
        dc_handle = attach.observe_identity("discord", attach.ensure_tab(
            "discord", "https://discord.com/channels/@me",
            group_title="OVO engagement cycle"))["handle"]
        handles["discord"] = dc_handle
        out["discord"] = collect_discord(attach, dc_handle, channel_ids or [])
    except Exception as exc:  # noqa: BLE001
        errors["discord"] = str(exc)[:300]
        log("inbox/discord 失败：%s" % exc)
    for platform in PLATFORM_ORDER:
        log("inbox/%s: %d 条待看" % (platform, len(out[platform])))
    return {"items": out, "errors": errors, "coverage": capabilities()}


def collect(data_dir, *, attach: Optional[BrowserAttach] = None, lookback_hours: int = 36,
            channel_ids: Optional[List[str]] = None, log=print) -> Dict[str, List[Dict[str, Any]]]:
    return collect_detailed(data_dir, attach=attach, lookback_hours=lookback_hours,
                            channel_ids=channel_ids, log=log)["items"]


def plan_replies(items: Dict[str, List[Dict[str, Any]]], ledger, *,
                 per_actor_cap: int = 2, night_start: Optional[_dt.datetime] = None) -> List[Dict[str, Any]]:
    """按 X → GitHub → Discord 排序，去掉已回复线程与超限对象。"""
    counts: Dict[str, int] = {}
    start = night_start or (_now() - _dt.timedelta(hours=12))
    for row in ledger.all():
        actor = str(row.get("actor") or "").lower()
        if actor:
            counts[actor] = counts.get(actor, 0) + 1

    plan: List[Dict[str, Any]] = []
    seen: set = set()
    for platform in PLATFORM_ORDER:
        rows = sorted(items.get(platform) or [],
                      key=lambda r: _parse_time(r.get("received_at")) or _now(), reverse=True)
        for row in rows:
            key = (row.get("platform"), row.get("thread_ref"))
            if key in seen:
                continue
            seen.add(key)
            if row.get("action") == "reply" and ledger.already(
                    channel=row["platform"], target=str(row.get("target") or "")):
                continue
            actor = str(row.get("handle") or row.get("author") or "").lower()
            if actor and counts.get(actor, 0) >= per_actor_cap:
                continue
            if actor:
                counts[actor] = counts.get(actor, 0) + 1
            plan.append(dict(row, source="inbox"))
    return plan
