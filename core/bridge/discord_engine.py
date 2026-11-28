#!/usr/bin/env python3
"""Post to or reply in a Discord channel through an already-logged-in tab, fast.

  python3 discord_engine.py --tab 123 --channel 1573251023500476427 --text-file draft.md [--dry]

`reply()` uses the same submit/readback path, but first opens the message's
context menu, picks the reply item, and refuses to type unless the reply bar
for that message is mounted. Every wait is a bounded poll.

Old path: full page nav + six fixed sleeps + a readback after 3s (10-15s).
This path: click the sidebar link (client-side route), wait for the target
channel's own messages to be mounted, insert text, Enter via CDP, poll for the
message. Every wait is a bounded poll.

Readiness rule that matters: pathname changes about 15ms after the sidebar
click, long before React swaps the message list and the composer. A gate that
only checks pathname + composer passes on the *previous* channel's nodes, the
text is injected into a composer that is about to be unmounted, and Enter does
nothing. So READY also requires a mounted message row whose id carries the
target channel id.
"""
import argparse
import json
import sys
import time

from . import wb  # noqa: E402

GUILD = "1573250833259171951"
COMPOSER = 'div[role="textbox"][contenteditable="true"]'

NAV = r"""(async () => {
  const p = %s, g = %s;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  if (location.pathname === p) return {ok: true, already: true};
  let a = document.querySelector('a[href="' + p + '"]');
  if (!a && g) {
    // Channel links only exist while a guild is mounted; from the DM list the
    // guild rail has buttons, so select the guild first and let it render.
    const btn = document.querySelector('[data-list-item-id="guildsnav___' + g + '"]');
    if (btn) {
      btn.click();
      for (let i = 0; i < 25 && !a; i++) {
        await sleep(150);
        a = document.querySelector('a[href="' + p + '"]');
      }
    }
  }
  if (!a) return {ok: false, err: 'channel link missing', path: location.pathname};
  a.click();
  for (let i = 0; i < 25 && location.pathname !== p; i++) await sleep(150);
  return {ok: true, clicked: true, path: location.pathname};
})()"""

READY = r"""(() => {
  const p = %s, cid = %s;
  const vis = e => { const r = e.getBoundingClientRect(); return r.height > 0; };
  const comp = Array.from(document.querySelectorAll(%s)).find(vis);
  const list = document.querySelector('[data-list-id="chat-messages"]');
  const slate = comp && comp.getAttribute('data-slate-editor');
  const rows = Array.from(document.querySelectorAll('li[id^="chat-messages-"]'));
  const mine = rows.filter(li => li.id.indexOf('chat-messages-' + cid + '-') === 0);
  const other = rows.filter(li => li.id.indexOf('chat-messages-' + cid + '-') !== 0);
  // An empty channel has no rows at all; accept only when the client shows its
  // own empty-state copy, never while rows from another channel are mounted.
  const emptyHint = /欢迎来到|这是.{0,20}的开始|还没有消息|No messages|Welcome to/.test(document.body.innerText || '');
  const ready = location.pathname === p && !!comp && !!list && slate === 'true'
    && (mine.length > 0 || (rows.length === 0 && emptyHint));
  return {path: location.pathname, ready, mine: mine.length, other: other.length,
          emptyHint, comp: !!comp, slate: slate};
})()"""

FOCUS = r"""(() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.height > 0; };
  const el = Array.from(document.querySelectorAll(%s)).find(vis);
  if (!el) return {ok: false, err: 'no composer'};
  el.focus();
  document.execCommand('selectAll');
  document.execCommand('delete');
  return {ok: true, focused: document.activeElement === el};
})()"""

LEN = r"""(() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.height > 0; };
  const el = Array.from(document.querySelectorAll(%s)).find(vis);
  return el ? {len: (el.innerText || '').length} : {len: 0, gone: true};
})()"""

BASELINE = r"""(() => {
  const cid = %s;
  const rows = Array.from(document.querySelectorAll('li[id^="chat-messages-"]'));
  const mine = rows.filter(li => li.id.indexOf('chat-messages-' + cid + '-') === 0);
  const last = mine[mine.length - 1];
  return {mine: mine.length, lastId: last ? last.id.split('-').pop() : null};
})()"""

SENT = r"""(() => {
  const cid = %s;
  const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
  const rows = Array.from(document.querySelectorAll('li[id^="chat-messages-"]'));
  const mine = rows.filter(li => li.id.indexOf('chat-messages-' + cid + '-') === 0);
  const last = mine[mine.length - 1];
  const content = last && (last.querySelector('div[id^="message-content-"]') || last);
  // Discord groups consecutive messages from the same author: continuation rows
  // do not carry a username node. Walk back to the nearest group header.
  let author = last && last.querySelector('[class*="username"], [class*="author"]');
  if (!author) {
    for (let i = mine.length - 1; i >= 0; i--) {
      const candidate = mine[i].querySelector('[class*="username"], [class*="author"]');
      if (candidate) { author = candidate; break; }
    }
  }
  const replyLink = last && last.querySelector('a[href*="/channels/"]');
  const replyTo = last && (last.getAttribute('data-reply-to') ||
    (replyLink ? String(replyLink.getAttribute('href') || '').split('/').pop() : ''));
  return {mine: mine.length, lastId: last ? last.id.split('-').pop() : null,
          lastText: clean(content ? content.textContent : ''),
          author: clean(author ? author.textContent : ''), replyTo: replyTo || null};
})()"""


TEXT_PRESENT = r"""(() => {
  const cid = %s, needle = %s;
  const clean = s => String(s || '').replace(/\s+/g, ' ').trim();
  const rows = Array.from(document.querySelectorAll('li[id^="chat-messages-"]'));
  const mine = rows.filter(li => li.id.indexOf('chat-messages-' + cid + '-') === 0);
  const last = mine[mine.length - 1];
  const body = last && (last.querySelector('div[id^="message-content-"]') || last);
  // Discord groups consecutive messages from the same author: continuation rows
  // do not carry a username node. Walk back to the nearest group header.
  let author = last && last.querySelector('[class*="username"], [class*="author"]');
  if (!author) {
    for (let i = mine.length - 1; i >= 0; i--) {
      const candidate = mine[i].querySelector('[class*="username"], [class*="author"]');
      if (candidate) { author = candidate; break; }
    }
  }
  const found = mine.some(li => clean(li.innerText).includes(needle));
  return {present: found, lastId: last ? last.id.split('-').pop() : null,
          lastText: clean(body ? body.textContent : ''),
          author: clean(author ? author.textContent : '')};
})()"""


def _norm(s):
    return " ".join((s or "").split())


def _new_row(base, got):
    """A new row, or a row whose id changed, is the send confirmation.

    Matching the body text is not: Discord renders markdown, so a list or a
    link never equals the source string. Text is recorded as evidence only.
    """
    b, g = base or {}, got or {}
    if not g.get("mine"):
        return False
    if int(g.get("mine") or 0) > int(b.get("mine") or 0):
        return True
    return bool(b.get("lastId") and g.get("lastId") and g["lastId"] != b["lastId"])
def _valid_readback(base, got, text, *, reply_to="", author=""):
    got = got or {}
    if not _new_row(base, got) or not got.get("lastId"):
        return False
    if _norm(got.get("lastText")) != _norm(text):
        return False
    if author and _norm(got.get("author")).lstrip("@").casefold() != _norm(author).lstrip("@").casefold():
        return False
    if not _norm(got.get("author")):
        return False
    if reply_to and str(got.get("replyTo") or "") != str(reply_to):
        return False
    return True


ENTER = r"""(() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.height > 0; };
  const el = Array.from(document.querySelectorAll(%s)).find(vis);
  if (!el) return {ok: false, err: 'no composer'};
  el.focus();
  const ev = new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter',
    keyCode: 13, which: 13, bubbles: true, cancelable: true});
  el.dispatchEvent(ev);
  return {ok: true, prevented: ev.defaultPrevented,
          active: document.activeElement === el};
})()"""


def _enter(tab):
    # CDP Input.dispatchKeyEvent does not reach the page through this bridge: a
    # keydown probe installed on the document saw nothing while Input.insertText
    # over the same channel worked. A keydown dispatched inside the page does
    # reach Discord's Slate composer, so that is the submit path used here.
    return wb.ev(tab, ENTER % json.dumps(COMPOSER)).get("value") or {}


def _full_nav(tab, url):
    """Whole-page navigation fallback, for when the guild is not in the sidebar.

    A tab on /channels/@me shows the DM list, so no channel link exists there.
    A raised error (beforeunload, slow route) is not fatal by itself: the READY
    poll that follows is the real gate.
    """
    try:
        wb.nav(tab, url)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001 - the READY poll is the real gate
        msg = str(e)
        if "beforeunload" in msg:
            try:
                wb.accept_dialog(tab)
            except Exception:  # noqa: BLE001
                pass
            try:
                wb.nav(tab, url)
                return {"ok": True}
            except Exception as e2:  # noqa: BLE001
                return {"ok": False, "err": str(e2)[:200]}
        return {"ok": False, "err": msg[:200]}


ROW_AT = r"""(() => {
  const cid = %s, mid = %s;
  const rows = Array.from(document.querySelectorAll('li[id^="chat-messages-"]'));
  const want = 'chat-messages-' + cid + '-' + mid;
  const row = rows.find(li => li.id === want || li.id.indexOf(want + '-') === 0);
  if (!row) return {ok: false, mounted: rows.length};
  const body = row.querySelector('div[id^="message-content-"]') || row;
  const r = body.getBoundingClientRect();
  return {ok: true, id: row.id, top: Math.round(r.top),
          height: Math.round(r.height),
          text: (row.innerText || '').replace(/\s+/g, ' ').slice(0, 120)};
})()"""

SCROLL_BOTTOM = r"""(() => {
  const list = document.querySelector('[data-list-id="chat-messages"]');
  if (!list) return {ok: false};
  list.scrollTop = list.scrollHeight;
  return {ok: true, top: list.scrollTop, height: list.scrollHeight};
})()"""

OPEN_MENU = r"""(() => {
  const cid = %s, mid = %s;
  const rows = Array.from(document.querySelectorAll('li[id^="chat-messages-"]'));
  const want = 'chat-messages-' + cid + '-' + mid;
  const row = rows.find(li => li.id === want || li.id.indexOf(want + '-') === 0);
  if (!row) return {ok: false, err: 'target_message_not_mounted'};
  const body = row.querySelector('div[id^="message-content-"]') || row;
  const r = body.getBoundingClientRect();
  // Hover buttons need a real :hover, which a synthetic event never sets, so
  // the context menu is the only reliable route for a scripted reply.
  body.dispatchEvent(new MouseEvent('contextmenu', {
    bubbles: true, cancelable: true, view: window, button: 2, buttons: 2,
    clientX: Math.round(r.left + 8), clientY: Math.round(r.top + 8)}));
  return {ok: true};
})()"""

MENU_ITEMS = r"""(() => {
  const menus = Array.from(document.querySelectorAll('[role="menu"]'))
    .filter(m => m.getBoundingClientRect().height > 0);
  const items = [];
  for (const m of menus) {
    for (const i of m.querySelectorAll('[role="menuitem"]')) {
      items.push((i.innerText || '').trim() || (i.getAttribute('aria-label') || '').trim());
    }
  }
  return {menus: menus.length, items: items.slice(0, 20)};
})()"""

PICK_REPLY = r"""(() => {
  const want = ['回复', 'Reply'];
  const menus = Array.from(document.querySelectorAll('[role="menu"]'))
    .filter(m => m.getBoundingClientRect().height > 0);
  for (const m of menus) {
    for (const i of m.querySelectorAll('[role="menuitem"]')) {
      const t = (i.innerText || '').trim();
      const a = (i.getAttribute('aria-label') || '').trim();
      if (want.indexOf(t) >= 0 || want.indexOf(a) >= 0) {
        i.click();
        return {ok: true, label: t || a};
      }
    }
  }
  return {ok: false, err: 'reply_item_missing'};
})()"""

REPLY_STATE = r"""(() => {
  const bars = Array.from(document.querySelectorAll('[class*="replyBar"]'))
    .filter(e => e.getBoundingClientRect().height > 0);
  const text = bars.map(e => (e.innerText || '').replace(/\s+/g, ' ').trim())
    .filter(Boolean).join(' | ');
  const links = bars.map(e => e.querySelector('a[href*="/channels/"]')).filter(Boolean);
  const href = links.length ? String(links[0].getAttribute('href') || '') : '';
  return {ok: bars.length > 0 && text.length > 0, bars: bars.length,
          text: text.slice(0, 160), replyTo: href ? href.split('/').pop() : null};
})()"""

ESC = r"""(() => {
  document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape',
    keyCode: 27, which: 27, bubbles: true}));
  return 1;
})()"""


def _open_channel(tab, channel):
    """侧栏点击（失败则整页导航）后，等到目标频道自己的消息行挂载。"""
    path = "/channels/%s/%s" % (GUILD, channel)
    out = {"channel": channel}
    nav = wb.ev(tab, NAV % (json.dumps(path), json.dumps(GUILD))).get("value") or {}
    out["nav"] = nav
    fell_back = False
    if not nav.get("ok"):
        # Sidebar click only works while a guild is mounted. From the DM list
        # (or any other route) fall back to a full navigation.
        out["navFallback"] = _full_nav(tab, "https://discord.com" + path)
        fell_back = True

    ready = {}
    t1 = time.time()
    ready_timeout = 20.0 if fell_back else 8.0
    # Two consecutive ready reads 150ms apart: the first can still be the tail of
    # the previous channel's render.
    streak = 0
    while time.time() - t1 < ready_timeout:
        ready = wb.ev(tab, READY % (json.dumps(path), json.dumps(channel),
                                    json.dumps(COMPOSER))).get("value") or {}
        streak = streak + 1 if ready.get("ready") else 0
        if streak >= 2:
            break
        time.sleep(0.15)
    out["readyMs"] = round((time.time() - t1) * 1000)
    out["readyProbe"] = ready
    return out, streak >= 2


def open_channel(tab, channel):
    """Public entry: select the guild/channel and wait for its own message list."""
    return _open_channel(tab, channel)


def _submit(tab, channel, text, dry, out):
    focused = wb.ev(tab, FOCUS % json.dumps(COMPOSER)).get("value") or {}
    if not focused.get("ok") or not focused.get("focused"):
        out.update(ok=False, err="no composer to focus", focus=focused)
        return out
    # Synthetic ClipboardEvent does not reach Discord's Slate editor, so the text
    # goes in through CDP Input.insertText (a trusted input event).
    pasted = {}
    for _ in range(3):
        wb.call("cdp", tabId=tab, method="Input.insertText", params={"text": text})
        pasted = wb.ev(tab, LEN % json.dumps(COMPOSER)).get("value") or {}
        if pasted.get("len", 0) >= max(1, int(len(text) * 0.85)):
            break
        wb.ev(tab, FOCUS % json.dumps(COMPOSER))
        time.sleep(0.15)
    out["paste"] = pasted
    if pasted.get("len", 0) < max(1, int(len(text) * 0.85)):
        out.update(ok=False, err="paste failed: %s" % pasted)
        return out
    if dry:
        wb.ev(tab, """(() => {const el=document.querySelector(%s); if(el){el.focus();document.execCommand('selectAll');document.execCommand('delete');} return 1;})()""" % json.dumps(COMPOSER))
        out.update(ok=True, dry=True)
        return out

    base = wb.ev(tab, BASELINE % json.dumps(channel)).get("value") or {}
    out["base"] = base
    out["enter"] = _enter(tab)

    got = {}
    t2 = time.time()
    while time.time() - t2 < 12.0:
        wb.ev(tab, SCROLL_BOTTOM)
        got = wb.ev(tab, SENT % json.dumps(channel)).get("value") or {}
        if _new_row(base, got):
            break
        time.sleep(0.15)
    out["sentMs"] = round((time.time() - t2) * 1000)
    out["readback"] = got
    out["ok"] = _new_row(base, got)
    if out["ok"]:
        # A local optimistic row appears at once and later takes its server id.
        # Re-reading after the round trip proves the message survived it.
        time.sleep(1.2)
        again = wb.ev(tab, SENT % json.dumps(channel)).get("value") or {}
        out["confirmed"] = _valid_readback(
            base, again, text, reply_to=str(out.get("replyTo") or ""))
        out["serverId"] = again.get("lastId")
        out["author"] = again.get("author")
        out["content"] = again.get("lastText")
        out["actualReplyTo"] = again.get("replyTo")
        out["readback"] = dict(got, confirmed=out["confirmed"], lastIdAfter=again.get("lastId"))
        out["ok"] = bool(out["confirmed"])
    if not out["ok"]:
        # 虚拟列表可能还没把新行挂进 DOM；滚到底部后再用正文存在性兜底。
        present = wb.ev(tab, TEXT_PRESENT % (json.dumps(channel), json.dumps(text[:80]))).get("value") or {}
        if present.get("present"):
            out["ok"] = True
            out["confirmed"] = True
            out["serverId"] = present.get("lastId")
            out["author"] = present.get("author")
            out["content"] = present.get("lastText")
            out["readback"] = dict(got, confirmed=True, text_present=True)
            return out
        out["composerAfter"] = wb.ev(tab, LEN % json.dumps(COMPOSER)).get("value") or {}
        out["err"] = "no new message row after Enter"
    return out


def post(tab, channel, text, dry=False):
    t0 = time.time()
    out, ready = _open_channel(tab, channel)
    if not ready:
        out.update(ok=False, err="channel not ready", ready=out.get("readyProbe"))
        return out
    _submit(tab, channel, text, dry, out)
    out["ms"] = round((time.time() - t0) * 1000)
    return out


def reply(tab, channel, message_id, text, dry=False):
    """在频道里回复指定消息：右键菜单 → 回复 → 校验回复条 → 发送。

    各步都 fail-closed：找不到目标行、菜单项或回复条时这条记为失败，
    宁可不发，也不把普通频道消息当成回复发出去。
    """
    t0 = time.time()
    cid, mid = str(channel), str(message_id)
    out = {"channel": cid, "message_id": mid}
    opened, ready = _open_channel(tab, cid)
    out.update(opened)
    if not ready:
        out.update(ok=False, err="channel not ready", ready=out.get("readyProbe"))
        return out

    row = {}
    t1 = time.time()
    while time.time() - t1 < 3.0:
        row = wb.ev(tab, ROW_AT % (json.dumps(cid), json.dumps(mid))).get("value") or {}
        if row.get("ok"):
            break
        # Older rows unmount above the fold; bring the newest rows into range.
        wb.ev(tab, SCROLL_BOTTOM)
        time.sleep(0.2)
    out["targetRow"] = row
    if not row.get("ok"):
        out.update(ok=False, err="target_message_not_mounted")
        return out

    out["menuOpen"] = wb.ev(tab, OPEN_MENU % (json.dumps(cid), json.dumps(mid))).get("value") or {}
    if not out["menuOpen"].get("ok"):
        out.update(ok=False, err="target_message_not_mounted")
        return out
    menu = {}
    t2 = time.time()
    while time.time() - t2 < 2.0:
        menu = wb.ev(tab, MENU_ITEMS).get("value") or {}
        if menu.get("items"):
            break
        time.sleep(0.1)
    out["menu"] = menu
    picked = wb.ev(tab, PICK_REPLY).get("value") or {}
    out["pick"] = picked
    if not picked.get("ok"):
        out.update(ok=False, err="reply_item_missing")
        return out

    state = {}
    t3 = time.time()
    while time.time() - t3 < 2.0:
        state = wb.ev(tab, REPLY_STATE).get("value") or {}
        if state.get("ok"):
            break
        time.sleep(0.1)
    out["replyState"] = state
    if not state.get("ok") or state.get("replyTo") != mid:
        # No/mismatched reply bar means the composer is not bound to this target.
        wb.ev(tab, ESC)
        out.update(ok=False, err="reply_context_mismatch" if state.get("ok") else "reply_context_missing")
        return out

    out["replyTo"] = mid
    _submit(tab, cid, text, dry, out)
    out["ms"] = round((time.time() - t0) * 1000)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tab", required=True)
    p.add_argument("--channel", required=True)
    p.add_argument("--text-file", required=True)
    p.add_argument("--dry", action="store_true")
    a = p.parse_args()
    text = open(a.text_file, encoding="utf-8").read().strip()
    out = post(int(a.tab), a.channel, text, a.dry)
    out["chars"] = len(text)
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
