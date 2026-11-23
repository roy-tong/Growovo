#!/usr/bin/env python3
"""KOC/KOL 名册采集（WebBridge 版）——搜 X → 收 handle → 读主页补粉丝/bio。

与 koc_harvest.py 的区别：不连 playwright CDP（9222），走 Kimi WebBridge
（core/bridge/wb.py + BrowserAttach），借用用户已登录的 X 标签页。只读。

用法:
  python3 scripts/koc_harvest_wb.py harvest --queries-file /tmp/koc-queries.txt
  python3 scripts/koc_harvest_wb.py enrich --top 300
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from core import supply_pool  # noqa: E402
from core.browser_attach import BridgeError, BrowserAttach  # noqa: E402
from core.supply_mine import X_EXTRACT  # noqa: E402

DATA = BASE / "data"
RAW = DATA / "state" / "koc-harvest-raw.json"

TOPICS = {
    "verovo": ("invoice", "billing", "finops", "reconcil", "accounting", "token",
               "usage", "spend", "cost", "bill", "扣费", "账单", "对账", "成本"),
    "growovo": ("gtm", "growth", "outbound", "cold email", "cold outreach", "lead",
                "marketing", "sdr", "demand", "funnel", "增长", "获客"),
    "askovo": ("research", "discovery", "evidence", "insight", "user research",
               "jtbd", "validation", "调研", "研究", "需求"),
    "agent": ("agent", "llm", "mcp", "context", "observability", "eval", "rag",
              "inference", "prompt"),
    "builder": ("indie", "build in public", "solo", "founder", "devtool", "open source",
                "独立开发", "出海"),
}

USER_EXTRACT = r"""
(() => {
  const out = [];
  const seen = new Set();
  for (const cell of document.querySelectorAll('[data-testid="UserCell"]')) {
    const link = cell.querySelector('a[href^="/"]');
    if (!link) continue;
    const handle = String(link.getAttribute('href') || '').replace(/^\//, '').split(/[/?]/)[0];
    if (!handle || seen.has(handle)) continue;
    seen.add(handle);
    const lines = String(cell.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
    const bio = lines.filter(l => !l.startsWith('@') && l !== '关注' && l !== 'Following'
      && l !== 'Follow' && l !== '正在关注').slice(1).join(' ').slice(0, 240);
    out.push({handle: handle, bio: bio, url: 'https://x.com/' + handle});
  }
  return out;
})()
"""

PROFILE_JS = r"""
(() => {
  const txt = (el) => el ? String(el.innerText || '').trim() : '';
  const followers = (() => {
    const a = document.querySelector('a[href$="/verified_followers"]')
      || document.querySelector('a[href$="/followers"]');
    if (!a) return null;
    const label = String(a.getAttribute('aria-label') || a.innerText || '');
    const m = label.replace(/,/g, '').match(/([\d.]+)\s*([KMB万千]?)/i);
    if (!m) return null;
    const n = parseFloat(m[1]);
    const u = (m[2] || '').toUpperCase();
    const scale = u === 'K' || u === '千' ? 1e3 : u === 'M' || u === '百' ? 1e6
      : u === 'B' ? 1e9 : u === '万' ? 1e4 : 1;
    return Math.round(n * scale);
  })();
  const bio = txt(document.querySelector('[data-testid="UserDescription"]'));
  const posts = Array.from(document.querySelectorAll('article[data-testid="tweet"] [data-testid="tweetText"]'))
    .slice(0, 5).map(txt);
  return {url: location.href, followers, bio, recent: posts};
})()
"""


def topics_for(text: str) -> list[str]:
    low = str(text or "").lower()
    return [g for g, words in TOPICS.items() if any(w in low for w in words)]


def load_raw() -> dict:
    if RAW.is_file():
        try:
            got = json.loads(RAW.read_text(encoding="utf-8"))
            if isinstance(got.get("accounts"), dict):
                return got
        except json.JSONDecodeError:
            pass
    return {"updated_at": None, "queries": [], "accounts": {}}


def save_raw(raw: dict) -> None:
    raw["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    RAW.parent.mkdir(parents=True, exist_ok=True)
    tmp = RAW.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(RAW)


def harvest(args) -> int:
    queries = list(args.query or [])
    if args.queries_file:
        queries += [line.strip() for line in Path(args.queries_file).read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")]
    if not queries:
        print("没有查询词", file=sys.stderr)
        return 2
    bridge = BrowserAttach(args.session or None)
    try:
        tab = bridge.attach_site("x", prefer_visible=True)
    except BridgeError as exc:
        print(f"attach failed: {exc.code} {exc.message}", file=sys.stderr)
        return 3
    raw = load_raw()
    fresh = 0
    for index, query in enumerate(queries):
        if index and args.gap:
            time.sleep(args.gap)
        url = "https://x.com/search?q=%s&f=live" % __import__("urllib.parse", fromlist=["quote"]).quote(query)
        try:
            bridge.route_spa(tab, url)
        except Exception as exc:  # noqa: BLE001
            print(f"[{index+1}/{len(queries)}] nav failed: {query[:50]} :: {str(exc)[:120]}", flush=True)
            continue
        posts: dict[str, dict] = {}
        for attempt in range(args.scrolls + 1):
            time.sleep(1.2 if attempt == 0 else 0.9)
            try:
                got = bridge.eval(tab, X_EXTRACT)
            except Exception:  # noqa: BLE001
                got = None
            if isinstance(got, list):
                for post in got:
                    if post.get("id") and post["id"] not in posts:
                        posts[post["id"]] = post
            if len(posts) >= args.limit:
                break
            try:
                bridge.eval(tab, "window.scrollBy(0, 1800); true")
            except Exception:  # noqa: BLE001
                pass
        new = 0
        for post in list(posts.values())[: args.limit]:
            handle = str(post.get("handle") or "").strip().lstrip("@")
            if not handle:
                continue
            entry = raw["accounts"].get(handle) or {"handle": handle, "hits": 0}
            entry["hits"] = int(entry.get("hits") or 0) + 1
            entry["last_query"] = query[:80]
            entry["topics"] = sorted({str(t) for t in entry.get("topics") or []}
                                     | set(topics_for(post.get("text"))))
            best = post.get("url") or ""
            old = entry.get("best_url") or ""
            score = int(post.get("likes") or 0) + 2 * int(post.get("replies") or 0)
            if best and (not old or score >= int(entry.get("best_score") or 0)):
                entry["best_url"] = best
                entry["best_score"] = score
            if not entry.get("sample"):
                entry["sample"] = str(post.get("text") or "")[:200]
            if handle not in raw["accounts"]:
                new += 1
            raw["accounts"][handle] = entry
        raw["queries"].append({"q": query, "posts": len(posts), "new_handles": new})
        save_raw(raw)
        print(f"[{index+1}/{len(queries)}] {query[:48]!r} posts={len(posts)} new={new} total={len(raw['accounts'])}",
              flush=True)
    rows = [{"handle": a["handle"], "best_url": a.get("best_url") or "",
             "topics": a.get("topics") or []} for a in raw["accounts"].values()]
    got = supply_pool.koc_merge(DATA, rows, source="x-search-wb")
    print(json.dumps({"raw_accounts": len(rows), "merge": got}, ensure_ascii=False))
    return 0


def users(args) -> int:
    """用户搜索页：每个查询直接拿 20+ 账号（bio/主页链接）。"""
    queries = list(args.query or [])
    if args.queries_file:
        queries += [line.strip() for line in Path(args.queries_file).read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")]
    if not queries:
        print("没有查询词", file=sys.stderr)
        return 2
    bridge = BrowserAttach(args.session or None)
    try:
        tab = bridge.attach_site("x", prefer_visible=True)
    except BridgeError as exc:
        print(f"attach failed: {exc.code} {exc.message}", file=sys.stderr)
        return 3
    raw = load_raw()
    for index, query in enumerate(queries):
        if index and args.gap:
            time.sleep(args.gap)
        url = "https://x.com/search?q=%s&f=user" % __import__("urllib.parse", fromlist=["quote"]).quote(query)
        try:
            bridge.eval(tab, "location.assign(%s); true" % json.dumps(url))
        except Exception as exc:  # noqa: BLE001
            print(f"[{index+1}/{len(queries)}] nav failed: {query[:50]} :: {str(exc)[:120]}", flush=True)
            continue
        time.sleep(args.wait)
        found: dict[str, dict] = {}
        for attempt in range(args.scrolls + 1):
            try:
                got = bridge.eval(tab, USER_EXTRACT)
            except Exception:  # noqa: BLE001
                got = None
            if isinstance(got, list):
                for row in got:
                    if row.get("handle") and row["handle"] not in found:
                        found[row["handle"]] = row
            if len(found) >= args.limit:
                break
            try:
                bridge.eval(tab, "window.scrollTo(0, document.documentElement.scrollHeight); true")
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1.6)
        new = 0
        for handle, row in list(found.items())[: args.limit]:
            entry = raw["accounts"].get(handle) or {"handle": handle, "hits": 0}
            entry["hits"] = int(entry.get("hits") or 0) + 1
            entry["last_query"] = query[:80]
            entry["topics"] = sorted({str(t) for t in entry.get("topics") or []}
                                     | set(topics_for(row.get("bio"))))
            entry["best_url"] = entry.get("best_url") or row.get("url") or ""
            if row.get("bio") and not entry.get("bio"):
                entry["bio"] = row["bio"]
            if handle not in raw["accounts"]:
                new += 1
            raw["accounts"][handle] = entry
        raw["queries"].append({"q": query, "mode": "user", "posts": len(found), "new_handles": new})
        save_raw(raw)
        print(f"[{index+1}/{len(queries)}] {query[:44]!r} users={len(found)} new={new} total={len(raw['accounts'])}",
              flush=True)
    rows = [{"handle": a["handle"], "bio": a.get("bio") or "", "best_url": a.get("best_url") or "",
             "topics": a.get("topics") or []} for a in raw["accounts"].values()]
    got = supply_pool.koc_merge(DATA, rows, source="x-user-search-wb")
    print(json.dumps({"raw_accounts": len(rows), "merge": got}, ensure_ascii=False))
    return 0


def enrich(args) -> int:
    payload = supply_pool.koc_load(DATA)
    known = payload["accounts"]
    todo = [row for row in known.values() if row.get("followers") is None]
    todo.sort(key=lambda r: (-int(r.get("hits") or 0), str(r.get("handle"))))
    if args.handles_file:
        wanted = [line.strip().lstrip("@") for line in
                  Path(args.handles_file).read_text(encoding="utf-8").splitlines() if line.strip()]
        todo = [known[h] for h in wanted if h in known]
    todo = todo[: args.top]
    if not todo:
        print("没有待补账号")
        return 0
    bridge = BrowserAttach(args.session or None)
    try:
        tab = bridge.attach_site("x", prefer_visible=True)
    except BridgeError as exc:
        print(f"attach failed: {exc.code} {exc.message}", file=sys.stderr)
        return 3
    rows = []
    for index, row in enumerate(todo):
        handle = row["handle"]
        try:
            bridge.route_spa(tab, f"https://x.com/{handle}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{index+1}/{len(todo)}] @{handle} nav failed: {str(exc)[:80]}", flush=True)
            continue
        time.sleep(args.wait)
        got = {}
        for _ in range(2):
            try:
                got = bridge.eval(tab, PROFILE_JS) or {}
            except Exception:  # noqa: BLE001
                got = {}
            if got.get("followers") is not None:
                break
            time.sleep(1.5)
        rows.append({"handle": handle, "followers": got.get("followers"),
                     "bio": got.get("bio") or "",
                     "recent_posts": got.get("recent") or []})
        if (index + 1) % 10 == 0:
            supply_pool.koc_merge(DATA, rows[-10:], source="x-profile-wb")
            print(f"[{index+1}/{len(todo)}] @{handle} followers={got.get('followers')}", flush=True)
        if args.gap:
            time.sleep(args.gap)
    done = len(rows) - (len(rows) % 10)
    if rows[done:]:
        supply_pool.koc_merge(DATA, rows[done:], source="x-profile-wb")
    print(json.dumps({"enriched": len(rows), "with_followers": sum(1 for r in rows if r["followers"])}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("harvest")
    h.add_argument("--query", action="append", default=[])
    h.add_argument("--queries-file")
    h.add_argument("--limit", type=int, default=40)
    h.add_argument("--scrolls", type=int, default=5)
    h.add_argument("--gap", type=float, default=2.5)
    h.add_argument("--session", default="")
    u = sub.add_parser("users")
    u.add_argument("--query", action="append", default=[])
    u.add_argument("--queries-file")
    u.add_argument("--limit", type=int, default=60)
    u.add_argument("--scrolls", type=int, default=2)
    u.add_argument("--wait", type=float, default=5.0)
    u.add_argument("--gap", type=float, default=2.0)
    u.add_argument("--session", default="")
    e = sub.add_parser("enrich")
    e.add_argument("--top", type=int, default=200)
    e.add_argument("--handles-file")
    e.add_argument("--wait", type=float, default=2.2)
    e.add_argument("--gap", type=float, default=1.5)
    e.add_argument("--session", default="")
    args = ap.parse_args()
    if args.cmd == "harvest":
        return harvest(args)
    if args.cmd == "users":
        return users(args)
    return enrich(args)


if __name__ == "__main__":
    raise SystemExit(main())
