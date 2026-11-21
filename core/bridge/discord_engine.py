#!/usr/bin/env python3
"""Post to a Discord channel through an already-logged-in tab, fast.

  python3 discord_engine.py --tab 123 --channel 1573251023500476427 --text-file draft.md [--dry]

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

NAV = r"""(() => {
  const p = %s;
  if (location.pathname === p) return {ok: true, already: true};
  const a = document.querySelector('a[href="' + p + '"]');
  if (!a) return {ok: false, err: 'sidebar link missing'};
  a.click();
  return {ok: true, clicked: true};
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
  const rows = Array.from(document.querySelectorAll('li[id^="chat-messages-"]'));
  const mine = rows.filter(li => li.id.indexOf('chat-messages-' + cid + '-') === 0);
  const last = mine[mine.length - 1];
  const txt = last ? (last.innerText || '').replace(/\s+/g, ' ') : '';
  return {mine: mine.length, lastId: last ? last.id.split('-').pop() : null,
          lastHead: txt.slice(0, 70)};
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


def post(tab, channel, text, dry=False):
    t0 = time.time()
    path = "/channels/%s/%s" % (GUILD, channel)
    out = {"channel": channel}
    nav = wb.ev(tab, NAV % json.dumps(path)).get("value") or {}
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
        ready = wb.ev(tab, READY % (json.dumps(path), json.dumps(channel), json.dumps(COMPOSER))).get("value") or {}
        streak = streak + 1 if ready.get("ready") else 0
        if streak >= 2:
            break
        time.sleep(0.15)
    out["readyMs"] = round((time.time() - t1) * 1000)
    out["readyProbe"] = ready
    if streak < 2:
        out.update(ok=False, err="channel not ready", ready=ready)
        return out

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
        out.update(ok=True, dry=True, ms=round((time.time() - t0) * 1000))
        return out

    base = wb.ev(tab, BASELINE % json.dumps(channel)).get("value") or {}
    out["base"] = base
    out["enter"] = _enter(tab)

    got = {}
    t2 = time.time()
    while time.time() - t2 < 8.0:
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
        out["confirmed"] = _new_row(base, again)
        out["serverId"] = again.get("lastId")
        out["readback"] = dict(got, confirmed=out["confirmed"], lastIdAfter=again.get("lastId"))
        out["ok"] = bool(out["confirmed"])
    if not out["ok"]:
        out["composerAfter"] = wb.ev(tab, LEN % json.dumps(COMPOSER)).get("value") or {}
        out["err"] = "no new message row after Enter"
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
