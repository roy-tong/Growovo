#!/usr/bin/env python3
"""KOC / KOL 名册采集器 —— 搜 X → 去重 → 读主页 → 分档 → 落盘。

- 只读：搜索与主页读取，不发任何内容
- 名册是一个 JSON：已见过的人不重复读主页（--refresh 可强制）
- 所有粉丝档位都保留：nano / micro / koc / mid / macro
- 每处理完一个 query 落盘一次，中断可续

用法:
  python3 scripts/koc_harvest.py --seed-watchlist data/grow/ovo-koc/koc_watchlist.json
  python3 scripts/koc_harvest.py --query "AI invoice" --query "FinOps AI" --limit 15
  python3 scripts/koc_harvest.py --queries-file /tmp/queries.txt --target-koc 100
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from core import grow_fit  # noqa: E402

DEFAULT_REGISTRY = BASE / "data" / "campaigns" / "ovo-dist-20261115" / "koc-registry.json"
SEARCH = BASE / "channels" / "x" / "vendor" / "search_x.py"
HEADER_RE = re.compile(r"@([A-Za-z0-9_]+)\s+·")
URL_RE = re.compile(r"https://x\.com/([A-Za-z0-9_]+)/status/(\d+)")

GROUPS = {
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


def hits_for(text: str) -> list[str]:
    low = str(text or "").lower()
    out = []
    for group, words in GROUPS.items():
        if any(w.lower() in low for w in words):
            out.append(group)
    return out


def load_registry(path: Path) -> dict:
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("accounts"), dict):
                return data
        except json.JSONDecodeError:
            pass
    return {"updated_at": None, "accounts": {}}


def save_registry(path: Path, reg: dict) -> None:
    reg["updated_at"] = date.today().isoformat()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def seed_watchlist(reg: dict, path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        src = json.loads(path.read_text(encoding="utf-8")).get("accounts") or {}
    except (json.JSONDecodeError, OSError):
        return 0
    added = 0
    for handle, row in src.items():
        handle = str(handle).lstrip("@")
        if not handle or handle in reg["accounts"]:
            continue
        followers = row.get("followers")
        bio = str(row.get("bio") or "")
        reg["accounts"][handle] = {
            "handle": handle,
            "followers": followers if isinstance(followers, int) else None,
            "tier": grow_fit.account_tier(followers),
            "bio": bio,
            "hits": hits_for(bio),
            "best_url": row.get("best_url") or "",
            "source": "watchlist-seed",
            "first_seen": row.get("first_seen") or date.today().isoformat(),
            "status": "seen",
        }
        added += 1
    return added


def search(query: str, limit: int) -> list[dict]:
    try:
        proc = subprocess.run([sys.executable, str(SEARCH), query, "--limit", str(limit)],
                              cwd=str(SEARCH.parent), capture_output=True, text=True,
                              timeout=120)
    except subprocess.TimeoutExpired:
        print(f"[search] timeout: {query}", flush=True)
        return []
    rows, current = [], None
    for line in (proc.stdout or "").splitlines():
        m = HEADER_RE.search(line)
        if m:
            current = {"handle": m.group(1), "best_url": ""}
            rows.append(current)
            continue
        u = URL_RE.search(line)
        if u and current is not None and not current["best_url"]:
            current["handle"] = u.group(1)
            current["best_url"] = u.group(0)
    return rows


def harvest_query(reg: dict, query: str, limit: int, *, refresh: bool,
                  sleep_s: float) -> tuple[int, int]:
    rows = search(query, limit)
    if not rows:
        # X 搜索偶发空结果（限流/页面未就绪）：等 40 秒重试一次再判空。
        print(f"[search] empty for {query!r}, retry in 40s", flush=True)
        time.sleep(40)
        rows = search(query, limit)
    new = 0
    for row in rows:
        handle = str(row.get("handle") or "").lstrip("@")
        if not handle:
            continue
        existing = reg["accounts"].get(handle)
        if existing and not refresh and existing.get("followers") is not None:
            if row.get("best_url") and not existing.get("best_url"):
                existing["best_url"] = row["best_url"]
            continue
        try:
            account = grow_fit.enrich_account(handle, log=lambda *a, **k: None)
        except Exception as exc:  # noqa: BLE001
            account = {"handle": handle, "note": str(exc)[:120]}
        followers = account.get("followers")
        bio = str(account.get("bio") or "")
        recent = " ".join(account.get("recent_posts") or [])
        reg["accounts"][handle] = {
            "handle": handle,
            "followers": followers if isinstance(followers, int) else None,
            "tier": grow_fit.account_tier(followers),
            "bio": bio,
            "hits": sorted(set(hits_for(bio) + hits_for(recent))),
            "best_url": row.get("best_url") or "",
            "source": f"search:{query}",
            "first_seen": date.today().isoformat(),
            "status": "new",
            "note": account.get("note") or "",
        }
        new += 1
        time.sleep(max(0.0, float(sleep_s)))
    koc = sum(1 for a in reg["accounts"].values() if a.get("tier") == "koc")
    print(f"[query] {query!r}: +{new} new | total {len(reg['accounts'])} | KOC {koc}", flush=True)
    return new, koc


def export_csv(reg: dict, path: Path) -> None:
    cols = ["handle", "followers", "tier", "hits", "bio", "best_url", "source", "status"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for row in sorted(reg["accounts"].values(),
                          key=lambda r: (-(r.get("followers") or 0), r["handle"])):
            w.writerow({**{c: row.get(c) for c in cols if c != "hits"},
                        "hits": ",".join(row.get("hits") or [])})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--query", action="append", default=[])
    ap.add_argument("--queries-file")
    ap.add_argument("--handles-file", help="每行一个 @handle：直接补读这些账号，不搜索")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--seed-watchlist")
    ap.add_argument("--target-koc", type=int, default=100)
    ap.add_argument("--max-queries", type=int, default=0)
    ap.add_argument("--refresh-empty", action="store_true",
                    help="先把 KOC 档里 bio 为空的账号补读一遍（用于算相关度）")
    args = ap.parse_args()

    reg_path = Path(args.registry)
    reg = load_registry(reg_path)
    if args.seed_watchlist:
        n = seed_watchlist(reg, Path(args.seed_watchlist))
        print(f"[seed] +{n} from watchlist", flush=True)

    if args.refresh_empty:
        targets = [a for a in reg["accounts"].values()
                   if not a.get("bio") and a.get("tier") == "koc"]
        print(f"[refresh] {len(targets)} KOC with empty bio", flush=True)
        for i, row in enumerate(targets, 1):
            handle = row["handle"]
            try:
                account = grow_fit.enrich_account(handle, log=lambda *a, **k: None)
                followers = account.get("followers")
                bio = str(account.get("bio") or "")
                recent = " ".join(account.get("recent_posts") or [])
                row.update({
                    "followers": followers if isinstance(followers, int) else row.get("followers"),
                    "tier": grow_fit.account_tier(followers if isinstance(followers, int) else row.get("followers")),
                    "bio": bio,
                    "hits": sorted(set(hits_for(bio) + hits_for(recent))),
                    "note": account.get("note") or "",
                })
            except Exception as exc:  # noqa: BLE001
                row["note"] = str(exc)[:120]
            if i % 10 == 0:
                save_registry(reg_path, reg)
                print(f"[refresh] {i}/{len(targets)}", flush=True)
            time.sleep(max(0.0, float(args.sleep)))
        save_registry(reg_path, reg)

    queries = list(args.query)
    if args.queries_file:
        queries += [ln.strip() for ln in Path(args.queries_file).read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.startswith("#")]
    if args.max_queries:
        queries = queries[:args.max_queries]

    handles = []
    if args.handles_file:
        handles = [ln.strip().lstrip("@") for ln in Path(args.handles_file).read_text(encoding="utf-8").splitlines()
                   if ln.strip() and not ln.startswith("#")]
    for handle in handles:
        if handle in reg["accounts"] and reg["accounts"][handle].get("followers") is not None and not args.refresh:
            continue
        try:
            account = grow_fit.enrich_account(handle, log=lambda *a, **k: None)
        except Exception as exc:  # noqa: BLE001
            account = {"handle": handle, "note": str(exc)[:120]}
        followers = account.get("followers")
        bio = str(account.get("bio") or "")
        recent = " ".join(account.get("recent_posts") or [])
        reg["accounts"][handle] = {
            "handle": handle,
            "followers": followers if isinstance(followers, int) else None,
            "tier": grow_fit.account_tier(followers),
            "bio": bio,
            "hits": sorted(set(hits_for(bio) + hits_for(recent))),
            "best_url": "",
            "source": "github-contributor",
            "first_seen": date.today().isoformat(),
            "status": "new",
            "note": account.get("note") or "",
        }
        print(f"[handle] @{handle}: {followers} {grow_fit.account_tier(followers)}", flush=True)
        time.sleep(max(0.0, float(args.sleep)))
    if handles:
        save_registry(reg_path, reg)

    for q in queries:
        harvest_query(reg, q, args.limit, refresh=args.refresh, sleep_s=args.sleep)
        save_registry(reg_path, reg)
        koc = sum(1 for a in reg["accounts"].values() if a.get("tier") == "koc")
        if koc >= args.target_koc:
            print(f"[stop] KOC target reached: {koc}", flush=True)
            break

    save_registry(reg_path, reg)
    csv_path = reg_path.with_suffix(".csv")
    export_csv(reg, csv_path)
    tiers = {}
    for a in reg["accounts"].values():
        tiers[a.get("tier") or "unknown"] = tiers.get(a.get("tier") or "unknown", 0) + 1
    print(f"[done] registry={reg_path} csv={csv_path}", flush=True)
    print(f"[done] tiers={tiers}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
