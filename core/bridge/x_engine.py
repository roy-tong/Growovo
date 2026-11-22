#!/usr/bin/env python3
"""X engine: WebBridge transport + client-side routing + in-page polling.

Measured on the user's own Chrome (2026-11-20):
  WebBridge round trip   3-10ms
  full page navigation   ~6.0s
  client-side route nav  ~1.0s   (history.pushState + popstate)
  reply send + confirm   ~0.9s

The old post_x.py paid 25-40s per action: every step was a separate round trip
followed by a blind sleep, on top of a 6s full page load. A reply here is
1 round trip to open the composer, 1 to send, 1 banner probe: ~2.5-3s total.

  python3 x_engine.py reply    --tab 123 --url ... --text-file f.md
  python3 x_engine.py original --tab 123 --text-file f.md
  python3 x_engine.py run      --plan plan.json --tabs 123,456 --gap 6
  python3 x_engine.py readback --tab 123 --handle elliwoodtong --manifest m.json
"""
import argparse
import json
import sys
import threading
import time
import urllib.parse

from . import wb  # noqa: E402

WARN_WORDS = [
    "出错", "失败", "限制", "验证", "something went wrong", "try again",
    "rate limit", "not allowed", "suspended", "unable to", "couldn't send",
]

CHECK_BANNER = """(() => {
  const nodes = Array.from(document.querySelectorAll(
    '[role="alert"], [data-testid="toast"], [data-testid="errorBanner"], [data-testid="emptyState"]'
  ));
  let text = nodes.map(n => n.innerText || '').join('\\n');
  if (!document.querySelector('[data-testid="primaryColumn"], [data-testid="SideNav_NewTweet_Button"]')) {
    const shell = document.querySelector('[data-testid="error-detail"], main, body');
    if (shell) text += '\\n' + (shell.innerText || '').slice(0, 600);
  }
  const low = text.toLowerCase();
  return {hits: %s.filter(w => low.includes(w)), len: text.length};
})()""" % json.dumps(WARN_WORDS)


def _path_of(url):
    u = urllib.parse.urlparse(url)
    return u.path or "/"


def _sid_of(url):
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    return parts[-1] if parts and parts[-2:-1] == ["status"] else ""


OPEN_REPLY = r"""(async () => {
  const PATH = %s;
  const SID = %s;
  const t0 = Date.now();
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const vis = e => e && e.getBoundingClientRect().height > 0;
  if (location.pathname !== PATH) {
    history.pushState({}, '', PATH);
    window.dispatchEvent(new PopStateEvent('popstate', {state: {}}));
  }
  let art = null;
  while (Date.now() - t0 < 8000) {
    art = Array.from(document.querySelectorAll('article')).find(a => (a.innerHTML || '').includes(SID));
    if (art) break;
    await sleep(50);
  }
  if (!art) return {ok: false, err: 'article not rendered', path: location.pathname};
  const artMs = Date.now() - t0;
  let clicks = 0, err = null;
  while (Date.now() - t0 < 15000) {
    const rb = Array.from(art.querySelectorAll('[data-testid="reply"]')).find(vis);
    if (!rb) { await sleep(60); continue; }
    rb.scrollIntoView({block: 'center'});
    await sleep(40);
    rb.click();
    clicks++;
    const tC = Date.now();
    while (Date.now() - tC < 3000) {
      if (Array.from(document.querySelectorAll('[data-testid="tweetTextarea_0"]')).find(vis)) {
        return {ok: true, artMs: artMs, totalMs: Date.now() - t0, clicks: clicks};
      }
      await sleep(40);
    }
    err = 'composer not open after click';
  }
  return {ok: false, err: err || 'no reply button in article', artMs: artMs, clicks: clicks};
})()"""


def _send_script(text, dupe_prefix=None):
    return r"""(async () => {
  const TEXT = %s;
  const DUPE = %s;
  const t0 = Date.now();
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const vis = e => e && e.getBoundingClientRect().height > 0;
  const flat = s => (s || '').replace(/\s+/g, ' ').trim();
  const tas = Array.from(document.querySelectorAll('[data-testid="tweetTextarea_0"]')).filter(vis);
  if (!tas.length) return {ok: false, err: 'no composer'};
  const el = tas[tas.length - 1];
  if (DUPE) {
    const key = flat(DUPE).slice(0, 60);
    for (const a of Array.from(document.querySelectorAll('article'))) {
      if (flat(a.innerText).includes(key)) return {ok: false, err: 'dupe_in_thread', dupe: key};
    }
  }
  el.focus();
  const dt = new DataTransfer();
  dt.setData('text/plain', TEXT);
  el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
  let got = 0;
  for (let i = 0; i < 40; i++) {
    got = (el.innerText || '').length;
    if (got >= Math.min(TEXT.length, TEXT.length * 0.85)) break;
    await sleep(50);
  }
  if (!got) return {ok: false, err: 'paste did not land'};
  let btn = null;
  let n = el;
  for (let d = 0; d < 30 && n; d++) {
    const b = n.querySelector('[data-testid="tweetButtonInline"], [data-testid="tweetButton"]');
    if (b) { btn = b; break; }
    n = n.parentElement;
  }
  if (!btn) return {ok: false, err: 'no send button', len: got};
  if (btn.getAttribute('aria-disabled') === 'true' || btn.disabled === true) {
    return {ok: false, err: 'send button disabled', len: got};
  }
  btn.click();
  let cleared = false;
  for (let i = 0; i < 100; i++) {
    const alive = document.body.contains(el);
    if (!alive || !(el.innerText || '').length) { cleared = true; break; }
    await sleep(50);
  }
  return {ok: cleared, cleared: cleared, len: got, clickMs: Date.now() - t0};
})()""" % (json.dumps(text), json.dumps(dupe_prefix or ""))


OPEN_COMPOSER = r"""(async () => {
  const t0 = Date.now();
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const vis = e => e && e.getBoundingClientRect().height > 0;
  const composer = () => Array.from(document.querySelectorAll('[data-testid="tweetTextarea_0"]')).find(vis);
  if (composer()) return {ok: true, already: true};
  for (let attempt = 0; attempt < 3; attempt++) {
    if (location.pathname !== '/home') {
      history.pushState({}, '', '/home');
      window.dispatchEvent(new PopStateEvent('popstate', {state: {}}));
    }
    const tA = Date.now();
    while (Date.now() - tA < 4000) {
      const b = document.querySelector('[data-testid="SideNav_NewTweet_Button"]');
      if (b) {
        b.click();
        const tC = Date.now();
        while (Date.now() - tC < 2500) {
          if (composer()) return {ok: true, openMs: Date.now() - t0, attempt: attempt + 1};
          await sleep(40);
        }
        break;
      }
      await sleep(50);
    }
    await sleep(500);
  }
  return {ok: false, err: 'compose modal did not open', ms: Date.now() - t0};
})()"""

READBACK = r"""(async () => {
  const PREFIXES = %s;
  const flat = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = {};
  for (let round = 0; round < 4; round++) {
    for (const a of Array.from(document.querySelectorAll('article'))) {
      const link = a.querySelector('a[href*="/status/"]');
      if (!link) continue;
      const m = link.getAttribute('href').match(/\/status\/(\d+)/);
      if (!m) continue;
      const txt = flat(a.innerText);
      for (const p of PREFIXES) {
        if (!out[p.key] && txt.includes(flat(p.prefix))) out[p.key] = m[1];
      }
    }
    if (Object.keys(out).length >= PREFIXES.length) break;
    window.scrollBy(0, 1400);
    await new Promise(r => setTimeout(r, 400));
  }
  return {found: out, scanned: document.querySelectorAll('article').length};
})()"""

ROUTE = r"""(() => {
  const p = %s;
  if (location.pathname !== p) {
    history.pushState({}, '', p);
    window.dispatchEvent(new PopStateEvent('popstate', {state: {}}));
  }
  return location.pathname;
})()"""


STALE_MODAL = r"""(() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.height > 0 && r.width > 0; };
  const comp = () => Array.from(document.querySelectorAll('[data-testid="tweetTextarea_0"]')).filter(vis).length;
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"]')).filter(vis);
  const st = {dialogs: dialogs.length, path: location.pathname, composer: comp()};
  if (st.dialogs && !st.composer) {
    const b = Array.from(document.querySelectorAll('[data-testid="app-bar-close"], [aria-label="Close"], [aria-label="关闭"]')).filter(vis);
    if (b.length) { b[0].click(); st.closed = true; }
  }
  if (location.pathname === '/compose/post' && !comp()) {
    history.pushState({}, '', '/home');
    window.dispatchEvent(new PopStateEvent('popstate', {state: {}}));
    st.routed = true;
  }
  return st;
})()"""


class Stop(Exception):
    """Raised when the platform signals a hard stop."""


def _prep(tab):
    """Clear a stale compose shell left behind by an earlier run."""
    st = {}
    for _ in range(4):
        try:
            st = wb.ev(tab, STALE_MODAL).get("value") or {}
        except Exception:  # noqa: BLE001 - probe only
            st = {}
        if not st.get("dialogs") and not (st.get("path") == "/compose/post" and not st.get("composer")):
            return st
        time.sleep(0.25)
    return st


def _banner(tab):
    try:
        return wb.ev(tab, CHECK_BANNER).get("value") or {}
    except Exception:  # noqa: BLE001 - a banner probe must never mask a send
        return {}


def _full_nav(tab, url):
    try:
        wb.nav(tab, url)
        return True
    except Exception as e:  # noqa: BLE001 - beforeunload / heavy route
        msg = str(e)
        if "beforeunload" in msg:
            try:
                wb.accept_dialog(tab)
            except Exception:  # noqa: BLE001
                pass
            try:
                wb.nav(tab, url)
                return True
            except Exception as e2:  # noqa: BLE001
                return "page load timeout" in str(e2) or "timeout" in str(e2)
        return "page load timeout" in msg or "timeout" in msg


def _reload(tab, settle: float = 3.0):
    """强制整页重载一次。

    整页 navigate 到同一路径时，X 的客户端路由可能接管而不重新取数据，
    帖子就一直是「未渲染」。reload 一定会重新加载文档。
    """
    try:
        wb.ev(tab, "(() => { location.reload(); return {ok: true}; })()")
    except Exception:  # noqa: BLE001 - reload 会打断本次 evaluate，属预期
        pass
    time.sleep(settle)
    return True


def reply(tab, url, text, dupe_prefix=None, dry=False):
    t0 = time.time()
    prep = _prep(tab)
    if prep.get("dialogs") and not prep.get("composer"):
        return {"kind": "reply", "target": url, "ok": False,
                "err": "stale modal", "prep": prep, "ms": round((time.time() - t0) * 1000)}
    sid = _sid_of(url)
    opened = wb.ev(tab, OPEN_REPLY % (json.dumps(_path_of(url)), json.dumps(sid))).get("value") or {}
    if not opened.get("ok"):
        if not _full_nav(tab, url):
            return {"kind": "reply", "target": url, "ok": False, "err": "nav failed", "open": opened}
        opened = wb.ev(tab, OPEN_REPLY % (json.dumps(_path_of(url)), json.dumps(sid))).get("value") or {}
        opened["fallback_full_nav"] = True
        if not opened.get("ok"):
            return {"kind": "reply", "target": url, "ok": False,
                    "err": "prep: " + str(opened.get("err")), "open": opened,
                    "ms": round((time.time() - t0) * 1000)}
    if dry:
        wb.ev(tab, """(() => {
          const tas = Array.from(document.querySelectorAll('[data-testid="tweetTextarea_0"]'))
            .filter(t => (t.innerText||'').trim().length > 0);
          for (const e of tas) { e.focus(); document.execCommand('selectAll'); document.execCommand('delete'); e.innerHTML=''; }
          return tas.length;
        })()""")
        return {"kind": "reply", "target": url, "ok": True, "dry": True, "open": opened,
                "ms": round((time.time() - t0) * 1000)}
    sent = wb.ev(tab, _send_script(text, dupe_prefix)).get("value") or {}
    if sent.get("err") == "dupe_in_thread":
        return {"kind": "reply", "target": url, "ok": False, "dupe": True,
                "err": "dupe_in_thread", "open": opened, "ms": round((time.time() - t0) * 1000)}
    if not sent.get("ok"):
        return {"kind": "reply", "target": url, "ok": False, "err": sent.get("err"),
                "sent": sent, "open": opened, "ms": round((time.time() - t0) * 1000)}
    banner = _banner(tab)
    if banner.get("hits"):
        raise Stop("banner:%s url=%s" % (",".join(banner["hits"]), url))
    return {"kind": "reply", "target": url, "ok": True, "open": opened, "sent": sent,
            "ms": round((time.time() - t0) * 1000)}


def original(tab, text, dry=False):
    t0 = time.time()
    opened = wb.ev(tab, OPEN_COMPOSER).get("value") or {}
    if not opened.get("ok"):
        _full_nav(tab, "https://x.com/home")
        opened = wb.ev(tab, OPEN_COMPOSER).get("value") or {}
        opened["fallback_full_nav"] = True
    if not opened.get("ok"):
        return {"kind": "original", "ok": False, "err": "prep: " + str(opened.get("err")), "open": opened}
    if dry:
        wb.ev(tab, """(() => {
          const tas = Array.from(document.querySelectorAll('[data-testid="tweetTextarea_0"]'))
            .filter(t => (t.innerText||'').trim().length > 0);
          for (const e of tas) { e.focus(); document.execCommand('selectAll'); document.execCommand('delete'); e.innerHTML=''; }
          return tas.length;
        })()""")
        return {"kind": "original", "ok": True, "dry": True, "open": opened}
    exclude = _status_ids(tab)
    sent = wb.ev(tab, _send_script(text)).get("value") or {}
    if not sent.get("ok"):
        return {"kind": "original", "ok": False, "sent": sent,
                "err": sent.get("err") or "composer did not clear after send"}
    status = _status_after(tab, exclude, text)
    banner = _banner(tab)
    if banner.get("hits"):
        raise Stop("banner:%s original" % ",".join(banner["hits"]))
    return {"kind": "original", "ok": True, "open": opened, "sent": sent,
            "status": status, "ms": round((time.time() - t0) * 1000)}


READBACK_ONE = r"""(() => {
  const PREFIXES = %s;
  const flat = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = {};
  for (const a of Array.from(document.querySelectorAll('article'))) {
    const link = a.querySelector('a[href*="/status/"]');
    if (!link) continue;
    const m = link.getAttribute('href').match(/\/status\/(\d+)/);
    if (!m) continue;
    const txt = flat(a.innerText);
    for (const p of PREFIXES) if (!out[p.key] && txt.includes(flat(p.prefix))) out[p.key] = m[1];
  }
  return {found: out, scanned: document.querySelectorAll('article').length};
})()"""


def _scan_at(tab, url, manifest, timeout):
    """Scan one page for the manifest prefixes; returns {key: status_id}."""
    absolute = url if url.startswith("http") else "https://x.com" + url
    route = url if url.startswith("/") else _path_of(url)
    got = {}
    try:
        wb.ev(tab, ROUTE % json.dumps(route))
    except Exception:  # noqa: BLE001 - a failed route is not fatal, the scan decides
        pass
    t0 = time.time()
    navved = False
    while time.time() - t0 < timeout:
        got = wb.ev(tab, READBACK_ONE % json.dumps(manifest, ensure_ascii=False)).get("value") or {}
        found = got.get("found") or {}
        if len(found) >= len(manifest):
            return found
        if not navved and time.time() - t0 > 3.0:
            # A hidden tab can leave the column unmounted; a full navigation is
            # the only thing that reliably re-renders it.
            _full_nav(tab, absolute)
            navved = True
        time.sleep(0.8)
    return got.get("found") or {}


def readback(tab, handle, manifest, timeout=30.0):
    """manifest: [{"key": "...", "prefix": "...", "url": "..."}] -> {key: status_id}

    Each entry is looked up on its own thread page (the reply is in the thread it
    answered), falling back to the profile timeline when no url is given.
    /with_replies is avoided as the primary entry: it leaves its column unmounted
    in a background tab, which made earlier readbacks report a sent reply as
    missing.
    """
    deadline = time.time() + float(timeout)
    out = {}
    groups = {}
    for item in manifest:
        url = str(item.get("url") or "/%s" % handle)
        groups.setdefault(url, []).append(item)
    for url, items in groups.items():
        left = deadline - time.time()
        if left <= 0:
            break
        found = _scan_at(tab, url, items, max(2.0, left))
        out.update(found)
    return out


def run_plan(plan, tabs, gap=6.0, dry=False, log=print):
    """Batch: one worker per tab, sends spaced by `gap` seconds."""
    results = []
    lock = threading.Lock()
    queue = list(plan)
    next_slot = [0.0]

    def worker(tab):
        while True:
            with lock:
                if not queue:
                    return
                item = queue.pop(0)
                now = time.time()
                wait = max(0.0, next_slot[0] - now)
                next_slot[0] = max(now, next_slot[0]) + gap
            if wait:
                time.sleep(wait)
            try:
                if item["kind"] == "reply":
                    r = reply(tab, item["url"], item["text"], item.get("dupe"), dry=dry)
                else:
                    r = original(tab, item["text"], dry=dry)
            except Stop as e:
                with lock:
                    queue.clear()
                r = {"kind": item["kind"], "target": item.get("url"), "ok": False,
                     "stop": True, "err": str(e)}
            except Exception as e:  # noqa: BLE001 - one bad item must not kill the wave
                r = {"kind": item["kind"], "target": item.get("url"), "ok": False,
                     "err": "%s: %s" % (type(e).__name__, str(e)[:300])}
            r["id"] = item.get("id")
            with lock:
                results.append(r)
                log(json.dumps(r, ensure_ascii=False))

    threads = [threading.Thread(target=worker, args=(t,), daemon=True) for t in tabs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("reply")
    r.add_argument("--tab", required=True)
    r.add_argument("--url", required=True)
    r.add_argument("--text-file", required=True)
    r.add_argument("--dry", action="store_true")

    o = sub.add_parser("original")
    o.add_argument("--tab", required=True)
    o.add_argument("--text-file", required=True)
    o.add_argument("--dry", action="store_true")

    b = sub.add_parser("run")
    b.add_argument("--plan", required=True)
    b.add_argument("--tabs", required=True)
    b.add_argument("--gap", type=float, default=6.0)
    b.add_argument("--dry", action="store_true")

    k = sub.add_parser("readback")
    k.add_argument("--tab", required=True)
    k.add_argument("--handle", required=True)
    k.add_argument("--manifest", required=True)

    a = p.parse_args()
    if a.cmd == "reply":
        out = reply(a.tab, a.url, open(a.text_file, encoding="utf-8").read().strip(), dry=a.dry)
    elif a.cmd == "original":
        out = original(a.tab, open(a.text_file, encoding="utf-8").read().strip(), dry=a.dry)
    elif a.cmd == "run":
        plan = json.load(open(a.plan, encoding="utf-8"))
        items = plan["items"] if isinstance(plan, dict) else plan
        tabs = [int(t) for t in a.tabs.split(",")]
        t0 = time.time()
        res = run_plan(items, tabs, gap=a.gap, dry=a.dry)
        out = {"count": len(res), "ok": sum(1 for x in res if x.get("ok")),
               "wall_s": round(time.time() - t0, 1), "results": res}
    else:
        manifest = json.load(open(a.manifest, encoding="utf-8"))
        out = readback(a.tab, a.handle, manifest)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ---------------------------------------------------------------------------
# reply_fast: open -> paste -> send in ONE in-page script.
# The old two-call flow (OPEN_REPLY then _send_script) lost the composer in the
# gap: X collapses a reply box that is not focused, and a stray empty modal can
# make OPEN_REPLY report ok on the wrong textarea.
# ---------------------------------------------------------------------------
REPLY_FAST = r"""(async () => {
  const PATH = %s, SID = %s, TEXT = %s, DUPE = %s, DRY = %s;
  const t0 = Date.now();
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const vis = e => { const r = e && e.getBoundingClientRect(); return !!r && r.height > 0; };
  const flat = s => (s || '').replace(/\\s+/g, ' ').trim();
  const boxes = () => Array.from(document.querySelectorAll('[data-testid="tweetTextarea_0"]')).filter(vis);
  const log = {steps: []};

  // 1. dismiss a stray compose shell left by an earlier run
  const dlg = Array.from(document.querySelectorAll('[role="dialog"]')).filter(vis);
  if (dlg.length) {
    const filled = boxes().some(b => (b.innerText || '').trim().length > 0);
    if (!filled) {
      const x = Array.from(document.querySelectorAll('[data-testid="app-bar-close"], [aria-label="Close"], [aria-label="\\u5173\\u95ed"]')).filter(vis)[0];
      if (x) { x.click(); log.steps.push('closed-stray'); await sleep(350); }
    }
  }

  // 2. route to the target (client-side; full nav is the caller's fallback)
  if (location.pathname !== PATH) {
    history.pushState({}, '', PATH);
    window.dispatchEvent(new PopStateEvent('popstate', {state: {}}));
  }
  let art = null;
  while (Date.now() - t0 < 8000) {
    art = Array.from(document.querySelectorAll('article')).find(a => (a.innerHTML || '').includes(SID));
    if (art) break;
    await sleep(50);
  }
  if (!art) return {ok: false, err: 'article not rendered', path: location.pathname};
  log.artMs = Date.now() - t0;

  // 3. dupe guard inside the thread
  if (DUPE) {
    const key = flat(DUPE).slice(0, 60);
    for (const a of Array.from(document.querySelectorAll('article'))) {
      if (flat(a.innerText).includes(key)) return {ok: false, err: 'dupe_in_thread', dupe: key};
    }
  }

  // 4. open, fill, send -- retry as a unit so a collapsed box is reopened
  let lastErr = null;
  for (let attempt = 1; attempt <= 3; attempt++) {
    let ta = boxes().slice(-1)[0];
    if (!ta) {
      const rb = Array.from(art.querySelectorAll('[data-testid="reply"]')).filter(vis)[0];
      if (!rb) { await sleep(200); lastErr = 'no reply button'; continue; }
      rb.scrollIntoView({block: 'center'});
      rb.click();
      log.steps.push('click' + attempt);
      const tC = Date.now();
      while (Date.now() - tC < 1500) {
        ta = boxes().slice(-1)[0];
        if (ta) break;
        await sleep(40);
      }
    }
    if (!ta) { lastErr = 'no composer after click'; continue; }

    ta.focus();
    const sel = window.getSelection();
    const rg = document.createRange();
    rg.selectNodeContents(ta);
    sel.removeAllRanges(); sel.addRange(rg);
    document.execCommand('delete');
    const dt = new DataTransfer();
    dt.setData('text/plain', TEXT);
    ta.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
    let got = 0;
    const tP = Date.now();
    while (Date.now() - tP < 2500) {
      got = (ta.innerText || '').length;
      if (got >= Math.min(TEXT.length, TEXT.length * 0.85)) break;
      await sleep(50);
    }
    if (!got) { lastErr = 'paste did not land'; await sleep(200); continue; }

    let btn = null, n = ta;
    for (let d = 0; d < 30 && n; d++) {
      const b = n.querySelector('[data-testid="tweetButtonInline"], [data-testid="tweetButton"]');
      if (b) { btn = b; break; }
      n = n.parentElement;
    }
    if (!btn) { lastErr = 'no send button'; await sleep(200); continue; }
    if (btn.getAttribute('aria-disabled') === 'true' || btn.disabled === true) {
      await sleep(250);
      if (btn.getAttribute('aria-disabled') === 'true' || btn.disabled === true) {
        lastErr = 'send button disabled'; continue;
      }
    }
    if (DRY) { ta.innerHTML = ''; return {ok: true, dry: true, len: got, attempt: attempt, log: log}; }
    btn.click();
    log.clickMs = Date.now() - t0;
    let cleared = false;
    const tS = Date.now();
    while (Date.now() - tS < 6000) {
      if (!document.body.contains(ta) || !(ta.innerText || '').length) { cleared = true; break; }
      await sleep(50);
    }
    return {ok: cleared, cleared: cleared, len: got, attempt: attempt, log: log};
  }
  return {ok: false, err: lastErr, log: log};
})()"""


def reply_fast(tab, url, text, dupe_prefix=None, dry=False):
    """One round trip: route, click reply, paste, send. Falls back to full nav."""
    t0 = time.time()
    script = REPLY_FAST % (json.dumps(_path_of(url)), json.dumps(_sid_of(url)),
                           json.dumps(text), json.dumps(dupe_prefix or ""),
                           "true" if dry else "false")
    out = wb.ev(tab, script).get("value") or {}
    if not out.get("ok") and str(out.get("err", "")).startswith("article not rendered"):
        if _full_nav(tab, url):
            out = wb.ev(tab, script).get("value") or {}
            out["fallback_full_nav"] = True
    if out.get("ok") and not dry:
        banner = _banner(tab)
        if banner.get("hits"):
            raise Stop("banner:%s url=%s" % (",".join(banner["hits"]), url))
    out.update({"kind": "reply", "target": url, "ms": round((time.time() - t0) * 1000)})
    return out


# --- native-input reply path (2026-11-20) -------------------------------------
# Draft.js ignores synthetic ClipboardEvent('paste') entirely, and its
# innerText stays empty when nothing was actually inserted. The browser's own
# input pipeline (WebBridge "fill") does land, so the reply flow is:
#   1 JS call: route to the status, click Reply, wait for the composer
#   1 native fill: write the text through the real input pipeline
#   1 JS call: click send and return at once
# Send confirmation comes from a search readback, not from the composer going
# blank: X keeps the reply compose page open after a successful send.
OPEN_REPLY_NATIVE = r"""(async () => {
  const PATH = %s, SID = %s, DUPE = %s;
  const t0 = Date.now();
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const vis = e => { const r = e && e.getBoundingClientRect(); return !!r && r.height > 0; };
  const flat = s => (s || '').replace(/\s+/g, ' ').trim();
  const boxes = () => Array.from(document.querySelectorAll('[data-testid="tweetTextarea_0"]')).filter(vis);
  const log = {};

  const dlg = Array.from(document.querySelectorAll('[role="dialog"]')).filter(vis);
  if (dlg.length) {
    const x = Array.from(document.querySelectorAll('[data-testid="app-bar-close"],[aria-label="Close"],[aria-label="\u5173\u95ed"]')).filter(vis)[0];
    if (x) { x.click(); log.stray = 1; await sleep(300); }
  }

  const here = SID && (document.body.innerText || '').includes(SID);
  if (location.pathname !== PATH || !here) {
    history.pushState({}, '', PATH);
    window.dispatchEvent(new PopStateEvent('popstate', {state: {}}));
  }
  const gone = () => {
    const col = document.querySelector('[data-testid="primaryColumn"]');
    const t = ((col || document.body).innerText || '').slice(0, 1200);
    return /This post is unavailable|This page doesn.t exist|Hmm\\.\\.\\.this page|该帖子不可用|此帖不可用|Post unavailable/i.test(t);
  };
  let art = null;
  while (Date.now() - t0 < 12000) {
    art = Array.from(document.querySelectorAll('article')).find(a => (a.innerHTML || '').includes(SID));
    if (art) break;
    if (Date.now() - t0 > 1500 && gone()) {
      return {ok: false, err: 'post gone', path: location.pathname, log};
    }
    await sleep(50);
  }
  if (!art) return {ok: false, err: 'article not rendered', path: location.pathname, log};
  log.artMs = Date.now() - t0;

  if (DUPE) {
    const key = flat(DUPE).slice(0, 60);
    for (const a of Array.from(document.querySelectorAll('article'))) {
      if (flat(a.innerText).includes(key)) return {ok: false, err: 'dupe_in_thread', dupe: key};
    }
  }

  // A hidden tab throttles setTimeout hard, so no in-page wait for the box:
  // click Reply and hand the wait to the Python caller, which polls BOX.
  const box = boxes().slice(-1)[0];
  if (!box) {
    const rb = Array.from(art.querySelectorAll('[data-testid="reply"]')).filter(vis)[0];
    if (!rb) return {ok: false, err: 'no reply button', log};
    rb.scrollIntoView({block: 'center'});
    rb.click();
    log.clicked = 1;
  }
  log.boxMs = Date.now() - t0;
  log.replyTo = (document.querySelector('[data-testid="primaryColumn"]') || document.body).innerText.slice(0, 120);
  return {ok: true, log: log, pending: !box, len: box ? (box.innerText || '').length : 0};
})()"""

BOX = r"""(() => {
  const vis = e => { const r = e && e.getBoundingClientRect(); return !!r && r.height > 0; };
  const b = Array.from(document.querySelectorAll('[data-testid="tweetTextarea_0"]')).filter(vis).slice(-1)[0];
  return b ? {found: true, len: (b.innerText || '').length} : {found: false};
})()"""

AFTER_SEND = r"""(() => {
  const vis = e => { const r = e && e.getBoundingClientRect(); return !!r && r.height > 0; };
  const flat = s => (s || '').replace(/\s+/g, ' ').trim();
  const b = Array.from(document.querySelectorAll('[data-testid="tweetTextarea_0"]')).filter(vis).slice(-1)[0];
  const arts = Array.from(document.querySelectorAll('article'));
  const head = flat(%s);
  const hit = arts.filter(a => flat(a.innerText).includes(head)).length;
  return {box: !!b, len: b ? (b.innerText || '').length : 0,
          articles: arts.length, hit: hit,
          toast: ((document.querySelector('[data-testid="toast"]') || {}).innerText || '').slice(0, 80)};
})()"""

CLICK_SEND = r"""(() => {
  const vis = e => { const r = e && e.getBoundingClientRect(); return !!r && r.height > 0; };
  const btn = Array.from(document.querySelectorAll('[data-testid="tweetButton"],[data-testid="tweetButtonInline"]')).filter(vis).slice(-1)[0];
  if (!btn) return {ok: false, err: 'no send button'};
  if (btn.getAttribute('aria-disabled') === 'true' || btn.disabled === true) return {ok: false, err: 'send disabled'};
  const box = document.querySelector('[data-testid="tweetTextarea_0"]');
  const len = box ? (box.innerText || '').length : 0;
  btn.click();
  return {ok: true, len: len, clickedAt: Date.now()};
})()"""

# 发送前页面上已存在的 status id；发送后新出现的才是本次动作的候选。
STATUS_IDS = r"""(() => Array.from(document.querySelectorAll('article a[href*="/status/"]'))
  .map(a => { const m = (a.getAttribute('href') || '').match(/\/status\/(\d+)/); return m ? m[1] : ''; })
  .filter(Boolean).slice(0, 40))()"""

# 发送后的回执绑定：优先地址栏（单帖页），其次 toast 里的状态链接，
# 最后是正文命中前缀的 article 自链接。只在页面自己给出 id 时返回。
STATUS_AFTER_SEND = r"""(() => {
  const EXCLUDE = %s;
  const PREFIX = %s;
  const flat = s => (s || '').replace(/\s+/g, ' ').trim();
  const sid = href => {
    const m = String(href || '').match(/\/status\/(\d+)/);
    return m ? m[1] : '';
  };
  const fresh = id => !!id && EXCLUDE.indexOf(id) < 0;
  const here = sid(location.pathname);
  if (fresh(here)) return {id: here, url: location.origin + location.pathname, source: 'location'};
  const toast = document.querySelector('[data-testid="toast"]');
  if (toast) {
    const link = toast.querySelector('a[href*="/status/"]');
    const id = sid(link ? link.getAttribute('href') : '');
    if (fresh(id)) return {id: id, url: link.href, source: 'toast'};
  }
  const head = flat(PREFIX).slice(0, 60);
  if (head) {
    for (const a of Array.from(document.querySelectorAll('article'))) {
      if (!flat(a.innerText).includes(head)) continue;
      const link = a.querySelector('a[href*="/status/"]');
      const id = sid(link ? link.getAttribute('href') : '');
      if (fresh(id)) return {id: id, url: link.href, source: 'article'};
    }
  }
  return {id: '', url: '', source: ''};
})()"""


CLEAR_BOX = r"""(() => {
  const b = document.querySelector('[data-testid="tweetTextarea_0"]');
  if (!b) return {ok: false, err: 'no box'};
  b.focus();
  document.execCommand('selectAll');
  document.execCommand('delete');
  return {ok: true, len: (b.innerText || '').length};
})()"""


def _wait_box(tab, timeout=8.0):
    """Poll for a visible reply composer; a hidden tab cannot wait in-page."""
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout:
        last = wb.ev(tab, BOX).get("value") or {}
        if last.get("found"):
            return last
        time.sleep(0.2)
    return last


def _write_text(tab, text):
    """Clear, fill, verify one copy. A retry clears again, so text never doubles."""
    out = {"attempts": []}
    for attempt in range(2):
        wb.ev(tab, CLEAR_BOX)
        t1 = time.time()
        try:
            f = wb.fill(tab, '[data-testid="tweetTextarea_0"]', text)
        except wb.WBError as e:
            return dict(out, ok=False, err="fill:%s" % e)
        got = wb.ev(tab, BOX).get("value") or {}
        out["attempts"].append({"ms": round((time.time() - t1) * 1000),
                                "fill": bool(f.get("success")), "len": got.get("len")})
        if f.get("success") and abs(int(got.get("len") or 0) - len(text)) <= 2:
            return dict(out, ok=True, len=got.get("len"))
        if got.get("len") and int(got["len"]) > len(text) * 1.5:
            continue  # doubled text: clear again and rewrite
    return dict(out, ok=False, err="fill did not land", len=(out["attempts"] or [{}])[-1].get("len"))


def _status_ids(tab):
    """发送前页面已有的 status id；发送后新出现的才是本次动作的候选。"""
    try:
        got = wb.ev(tab, STATUS_IDS).get("value")
    except Exception:  # noqa: BLE001 - a probe must never block a send
        return []
    if not isinstance(got, list):
        return []
    return [str(item) for item in got if str(item).isdigit()]


def _status_after(tab, exclude, prefix, timeout=6.0):
    """Poll for the status id the page itself reports after a send.

    Best effort only: an empty result means the caller falls back to the profile
    readback, never that the send failed.
    """
    script = STATUS_AFTER_SEND % (json.dumps([str(x) for x in (exclude or [])],
                                             ensure_ascii=False),
                                  json.dumps(prefix or "", ensure_ascii=False))
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            got = wb.ev(tab, script).get("value") or {}
        except Exception:  # noqa: BLE001 - binding is best-effort
            got = {}
        if isinstance(got, dict) and str(got.get("id") or "").isdigit():
            return got
        time.sleep(0.35)
    return {"id": "", "url": "", "source": ""}


def reply_native(tab, url, text, dupe_prefix=None, dry=False):
    """Reply through the real input pipeline. Returns per-step timing."""
    t0 = time.time()
    out = {"kind": "reply", "target": url, "dry": bool(dry), "steps": {}}
    script = OPEN_REPLY_NATIVE % (json.dumps(_path_of(url)), json.dumps(_sid_of(url)),
                                  json.dumps(dupe_prefix or ""))
    o = wb.ev(tab, script).get("value") or {}
    out["steps"]["open"] = round((time.time() - t0) * 1000)
    out["open"] = o.get("log", {})
    if not o.get("ok") and str(o.get("err", "")) != "post gone":
        if str(o.get("err", "")).startswith("article not rendered") and _full_nav(tab, url):
            t1 = time.time()
            o = wb.ev(tab, script).get("value") or {}
            out["steps"]["open_fallback"] = round((time.time() - t1) * 1000)
            out["out"] = o.get("log", {})
        if str(o.get("err", "")).startswith("article not rendered"):
            # 第二轮仍没渲染：强制重载一次。缓存陈旧时整页导航可能被
            # 客户端路由接管，reload 才会重新取数据。
            t1 = time.time()
            try:
                if _reload(tab):
                    o = wb.ev(tab, script).get("value") or {}
                    out["steps"]["open_reload"] = round((time.time() - t1) * 1000)
                    out["reload_out"] = o.get("log", {})
            except Exception:  # noqa: BLE001 - 重载失败保留第一次的错误
                pass
    if not o.get("ok"):
        out.update({"ok": False, "err": o.get("err")})
        out["ms"] = round((time.time() - t0) * 1000)
        return out

    if o.get("pending"):
        t1 = time.time()
        box = _wait_box(tab)
        out["steps"]["box"] = round((time.time() - t1) * 1000)
        if not box.get("found"):
            # 还没输入任何文字，重载后再点一次 Reply 不会重复发送；时间线
            # 陈旧是回复框不出现的常见原因，值得一次干净的重试。
            t2 = time.time()
            o2 = {}
            if _full_nav(tab, url):
                o2 = wb.ev(tab, script).get("value") or {}
                out["reopen"] = o2.get("log", {})
                if o2.get("ok"):
                    box = _wait_box(tab) if o2.get("pending") else {"found": True}
            else:
                out["reopen"] = {"nav": False}
            out["steps"]["reopen"] = round((time.time() - t2) * 1000)
            if not box.get("found"):
                out.update({"ok": False, "err": "no composer after reopen",
                            "open": out.get("open"), "reopen_err": o2.get("err")})
                out["ms"] = round((time.time() - t0) * 1000)
                return out

    w = _write_text(tab, text)
    out["steps"]["fill"] = w["attempts"]
    out["len"] = w.get("len", 0)
    if not w.get("ok"):
        out.update({"ok": False, "err": w.get("err"), "got": w})
        out["ms"] = round((time.time() - t0) * 1000)
        return out

    if dry:
        out.update({"ok": True, "dry": True})
        out["ms"] = round((time.time() - t0) * 1000)
        return out

    t2 = time.time()
    exclude = _status_ids(tab)
    target_sid = _sid_of(url)
    if target_sid and target_sid not in exclude:
        exclude.append(target_sid)
    s = wb.ev(tab, CLICK_SEND).get("value") or {}
    out["steps"]["send"] = round((time.time() - t2) * 1000)
    out["send"] = s
    if not s.get("ok"):
        out.update({"ok": False, "err": s.get("err")})
        out["ms"] = round((time.time() - t0) * 1000)
        return out

    # The composer emptying is the fastest honest signal that the client took
    # the click; the posted status id comes from the profile readback.
    head = " ".join(text.split())[:40]
    t3 = time.time()
    after = {}
    while time.time() - t3 < 10.0:
        after = wb.ev(tab, AFTER_SEND % json.dumps(head, ensure_ascii=False)).get("value") or {}
        if (after.get("box") and not after.get("len")) or not after.get("box") or after.get("hit"):
            break
        if after.get("toast"):
            break
        time.sleep(0.4)
    out["steps"]["cleared"] = round((time.time() - t3) * 1000)
    out["after"] = after
    out["ok"] = bool((after.get("box") and not after.get("len")) or after.get("hit") or after.get("toast"))
    if not out["ok"]:
        out["err"] = "composer still holds text"

    t4 = time.time()
    banner = _banner(tab)
    out["steps"]["banner"] = round((time.time() - t4) * 1000)
    if banner.get("hits"):
        out["banner"] = banner
        raise Stop("banner:%s url=%s" % (",".join(banner["hits"]), url))
    out["status"] = (_status_after(tab, exclude, text) if out["ok"]
                     else {"id": "", "url": "", "source": ""})
    out["ms"] = round((time.time() - t0) * 1000)
    return out
