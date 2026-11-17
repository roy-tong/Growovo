#!/usr/bin/env python3
"""把 koc-registry.json 导出成人看的 Markdown / CSV。

- KOC 定义：粉丝 1,000–50,000
- relevant：bio/近期内容命中三产品话题（verovo/growovo/askovo）或 agent+builder
- 其他档位（nano/micro/mid/macro）全保留，单列
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
REG = BASE / "data" / "campaigns" / "ovo-dist-20261115" / "koc-registry.json"
MD = REG.with_suffix(".md")
CSV = REG.with_suffix(".csv")


def relevant(row: dict) -> bool:
    hits = set(row.get("hits") or [])
    return bool(hits & {"verovo", "growovo", "askovo"}) or ("agent" in hits and "builder" in hits)


def tier_order(t: str) -> int:
    return {"koc": 0, "mid": 1, "micro": 2, "nano": 3, "macro": 4}.get(t, 5)


def main() -> int:
    reg = json.loads(REG.read_text(encoding="utf-8"))
    acc = list(reg.get("accounts", {}).values())
    for row in acc:
        row["relevant"] = relevant(row)
    acc.sort(key=lambda r: (0 if r["relevant"] else 1, tier_order(r.get("tier") or ""),
                            -(r.get("followers") or 0), r.get("handle", "")))
    counts = {}
    for row in acc:
        key = ("relevant " if row["relevant"] else "other ") + (row.get("tier") or "unknown")
        counts[key] = counts.get(key, 0) + 1

    lines = ["# KOC / KOL 名册（X）", "",
             f"- 总账号：{len(acc)}",
             "- KOC（1k–50k）：{}（其中相关 {}）".format(
                 sum(1 for r in acc if r.get("tier") == "koc"),
                 sum(1 for r in acc if r.get("tier") == "koc" and r["relevant"])),
             "- 分档：" + "、".join(f"{k} {v}" for k, v in sorted(counts.items())),
             "- 口径：粉丝为 2026-11-16 主页读数；relevant 只是 bio/近期内容的关键词命中，**不等于已核验**。",
             "", "## KOC（1k–50k，相关优先）", "",
             "| handle | 粉丝 | 相关 | 命中 | bio | 最近帖子 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for r in acc:
        if r.get("tier") != "koc":
            continue
        bio = (r.get("bio") or "").replace("|", "/")[:70]
        url = r.get("best_url") or ""
        link = f"[帖]({url})" if url else ""
        lines.append(f"| @{r['handle']} | {r.get('followers') or '取不到'} | "
                     f"{'是' if r['relevant'] else '否'} | {','.join(r.get('hits') or []) or '-'} | "
                     f"{bio} | {link} |")

    for tier, title in (("mid", "Mid（50k–300k）"), ("micro", "Micro（200–1k）"),
                        ("nano", "Nano（<200）"), ("macro", "Macro（>300k）"),
                        ("unknown", "粉丝取不到")):
        rows = [r for r in acc if r.get("tier") == tier]
        if not rows:
            continue
        lines += ["", f"## {title}", "", "| handle | 粉丝 | 命中 | bio |", "| --- | --- | --- | --- |"]
        for r in rows:
            bio = (r.get("bio") or "").replace("|", "/")[:70]
            lines.append(f"| @{r['handle']} | {r.get('followers') or '取不到'} | "
                         f"{','.join(r.get('hits') or []) or '-'} | {bio} |")

    MD.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cols = ["handle", "followers", "tier", "relevant", "hits", "bio", "best_url", "source"]
    with CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in acc:
            w.writerow({"handle": r["handle"], "followers": r.get("followers"),
                        "tier": r.get("tier"), "relevant": r["relevant"],
                        "hits": ",".join(r.get("hits") or []), "bio": r.get("bio"),
                        "best_url": r.get("best_url"), "source": r.get("source")})
    print(f"md={MD}\ncsv={CSV}\ncounts={counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
