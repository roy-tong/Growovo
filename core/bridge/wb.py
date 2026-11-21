#!/usr/bin/env python3
"""Kimi WebBridge client for the OVO night run.

Usage:
  python3 wb.py tabs
  python3 wb.py nav --tab 123 --url https://x.com/home
  python3 wb.py ev --tab 123 --code-file /tmp/code.js
  python3 wb.py fill --tab 123 --selector 'div[role="textbox"]' --text-file /tmp/t.txt
  python3 wb.py click --tab 123 --selector '[data-testid="reply"]'
  python3 wb.py keys --tab 123 --keys Enter --focus
  python3 wb.py wait --tab 123 --selector '[data-testid="tweetTextarea_0"]' --timeout 20000
  python3 wb.py read --tab 123 --max 4000
  python3 wb.py shot --tab 123 --path /tmp/shot.png
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DAEMON = os.environ.get("MKT_BRIDGE_DAEMON", "http://127.0.0.1:10086/command")
SESSION = os.environ.get("MKT_BRIDGE_SESSION", "growovo")


class WBError(RuntimeError):
    def __init__(self, err):
        super().__init__(json.dumps(err, ensure_ascii=False))
        self.err = err


RETRY_TRANSPORT = 3


def set_session(name):
    """切换 WebBridge 会话（决定可见的标签页组）。"""
    global SESSION
    SESSION = name or "growovo"


def _normalize(args):
    tid = args.get("tabId")
    if isinstance(tid, str) and tid.isdigit():
        args["tabId"] = int(tid)
    return args


def call(_tool, _timeout=180, **args):
    _normalize(args)
    body = json.dumps({"action": _tool, "args": args, "session": SESSION}).encode("utf-8")
    last = None
    for attempt in range(RETRY_TRANSPORT):
        req = urllib.request.Request(DAEMON, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=_timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "ignore")
            try:
                return json.loads(raw)
            except ValueError:
                pass
            last = "HTTP %s: %s" % (e.code, raw[:200])
            if e.code < 500:
                break
        except Exception as e:  # noqa: BLE001 - transport failures are reported as JSON
            last = e
        time.sleep(1.5 * (attempt + 1))
    return {"ok": False, "error": {"code": "transport", "message": str(last)}}


def data(_tool, **args):
    env = call(_tool, **args)
    if not env.get("ok"):
        raise WBError(env.get("error", {"code": "unknown"}))
    return env.get("data", {})


def tabs():
    return data("list_tabs").get("tabs", [])


def nav(tab, url, new_tab=False, group_title=None):
    args = {"url": url}
    if tab:
        args["tabId"] = tab
        args["newTab"] = new_tab
    else:
        args["newTab"] = True
    if group_title:
        args["group_title"] = group_title
    return data("navigate", **args)


def ev(tab, code):
    return data("evaluate", tabId=tab, code=code)


def js(tab, code):
    """Evaluate and return the JS value; raises on page error."""
    d = ev(tab, code)
    return d.get("value")


def fill(tab, selector, text):
    return data("fill", tabId=tab, selector=selector, value=text)


def click(tab, selector, focus_emulation=False):
    args = {"selector": selector}
    if focus_emulation:
        args["focus_emulation"] = True
    return data("click", tabId=tab, **args)


def mouse_click(tab, selector, focus_emulation=True):
    args = {"selector": selector}
    if focus_emulation:
        args["focus_emulation"] = True
    return data("mouse_click", tabId=tab, **args)


def keys(tab, keys_str, focus_emulation=False):
    if isinstance(keys_str, (list, tuple)):
        keys_str = " ".join(keys_str)
    args = {"keys": keys_str}
    if focus_emulation:
        args["focus_emulation"] = True
    return data("send_keys", tabId=tab, **args)


def wait(tab, timeout=20000, **conds):
    return data("wait", tabId=tab, timeout_ms=timeout, **conds)


def read(tab, max_chars=4000):
    return data("read_page", tabId=tab, max_chars=max_chars)


def shot(tab, path):
    return data("screenshot", tabId=tab, path=path)


def paste_script(text):
    """JS that injects text into the focused composer via a paste event."""
    lit = json.dumps(text)
    return """(() => {
  const text = %s;
  const el = document.activeElement && document.activeElement.matches && document.activeElement.matches('[contenteditable="true"], textarea')
    ? document.activeElement
    : document.querySelector('[data-testid="tweetTextarea_0"], div[role="textbox"][contenteditable="true"], textarea');
  if (!el) return {ok: false, err: 'no composer'};
  el.focus();
  const dt = new DataTransfer();
  dt.setData('text/plain', text);
  el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
  return {ok: true, tag: el.tagName, testid: el.getAttribute('data-testid') || ''};
})()""" % lit


def composer_len_script(selector):
    lit = json.dumps(selector)
    return """(() => {
  const el = document.querySelector(%s);
  if (!el) return {found: false};
  const txt = el.value !== undefined && el.tagName === 'TEXTAREA' ? el.value : (el.innerText || '');
  return {found: true, len: txt.length, head: txt.slice(0, 80)};
})()""" % lit


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("tabs")

    n = sub.add_parser("nav")
    n.add_argument("--tab")
    n.add_argument("--url", required=True)
    n.add_argument("--new-tab", action="store_true")
    n.add_argument("--group")

    e = sub.add_parser("ev")
    e.add_argument("--tab", required=True)
    e.add_argument("--code")
    e.add_argument("--code-file")

    f = sub.add_parser("fill")
    f.add_argument("--tab", required=True)
    f.add_argument("--selector", required=True)
    f.add_argument("--text")
    f.add_argument("--text-file")

    c = sub.add_parser("click")
    c.add_argument("--tab", required=True)
    c.add_argument("--selector", required=True)
    c.add_argument("--focus", action="store_true")

    m = sub.add_parser("mclick")
    m.add_argument("--tab", required=True)
    m.add_argument("--selector", required=True)

    k = sub.add_parser("keys")
    k.add_argument("--tab", required=True)
    k.add_argument("--keys", required=True)
    k.add_argument("--focus", action="store_true")

    w = sub.add_parser("wait")
    w.add_argument("--tab", required=True)
    w.add_argument("--selector")
    w.add_argument("--text")
    w.add_argument("--text-gone")
    w.add_argument("--timeout", type=int, default=20000)

    r = sub.add_parser("read")
    r.add_argument("--tab", required=True)
    r.add_argument("--max", type=int, default=4000)

    s = sub.add_parser("shot")
    s.add_argument("--tab", required=True)
    s.add_argument("--path", required=True)

    a = p.parse_args(argv)
    try:
        if a.cmd == "tabs":
            out = tabs()
        elif a.cmd == "nav":
            out = nav(a.tab, a.url, a.new_tab, a.group)
        elif a.cmd == "ev":
            code = a.code if a.code else open(a.code_file, encoding="utf-8").read()
            out = ev(a.tab, code)
        elif a.cmd == "fill":
            text = a.text if a.text is not None else open(a.text_file, encoding="utf-8").read()
            out = fill(a.tab, a.selector, text)
        elif a.cmd == "click":
            out = click(a.tab, a.selector, a.focus)
        elif a.cmd == "mclick":
            out = mouse_click(a.tab, a.selector)
        elif a.cmd == "keys":
            out = keys(a.tab, a.keys, a.focus)
        elif a.cmd == "wait":
            conds = {}
            if a.selector:
                conds["selector"] = a.selector
            if a.text:
                conds["text"] = a.text
            if a.text_gone:
                conds["text_gone"] = a.text_gone
            out = wait(a.tab, a.timeout, **conds)
        elif a.cmd == "read":
            out = read(a.tab, a.max)
        elif a.cmd == "shot":
            out = shot(a.tab, a.path)
        else:  # pragma: no cover
            p.error("unknown command")
            return 2
    except WBError as exc:
        print(json.dumps({"ok": False, "error": exc.err}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "data": out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())


def dialog(tab, op="status"):
    return call("dialog", tabId=tab, action=op)


def accept_dialog(tab):
    return call("dialog", tabId=tab, action="accept")
