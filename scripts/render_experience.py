#!/usr/bin/env python3
"""把 experience.jsonl 渲染成每个平台一份可读文档（带新鲜度抬头）。

维护方式：采集 → 抽取 → 渲染，三步都是脚本，可定期重跑。
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
LIB = BASE / "docs" / "playbooks" / "经验库"
DATA = LIB / "experience.jsonl"
FRESH_DAYS = 60

NAME = {"xiaohongshu": "小红书", "zhihu": "知乎", "wechat": "微信公众号", "douyin": "抖音",
        "bilibili": "B站", "zhishixingqiu": "知识星球", "weibo": "微博", "jike": "即刻",
        "juejin": "掘金", "v2ex": "V2EX", "sspai": "少数派", "wechat-official": "微信公众号"}


def main() -> None:
    rows = [json.loads(l) for l in DATA.read_text(encoding="utf-8").splitlines() if l.strip()]
    by = defaultdict(list)
    for r in rows:
        by[r["platform"]].append(r)
    for pid, items in by.items():
        items.sort(key=lambda r: (-int(r.get("support") or 0), r.get("claim", "")))
        dated = [r["published"] for r in items if r.get("published")]
        newest = max(dated) if dated else None
        rules = [r for r in items if r.get("kind") == "rule"]
        verb = [r for r in items if r.get("kind") == "verbatim"]
        topics = [r for r in items if r.get("kind") == "topic"]
        md = [f"# {NAME.get(pid, pid)} · 用户经验（{len(items)} 条）", "",
              "> 这些不是我们想出来的，是**别人真跑过之后写下来的**。",
              f"> 采样：人工提炼 {len(rules)} 条 · 多篇复现 {len(verb)} 条 · 话题观察 {len(topics)} 条"
              f"（话题观察只说明这个变量重要，不说明某种做法有效）。",
              f"> 最新来源：{newest or '无发布时间'}；窗口要求：{FRESH_DAYS} 天。"
              + ("" if newest else " **本平台缺发布时间，全部标为待刷新。**"),
              ""]
        if rules:
            md += ["## 可以当规律的", ""]
            for i, r in enumerate(rules, 1):
                md += [f"{i}. **{r['claim']}**（{r['support']} 篇提到）",
                       f"   - 所以：{r['action']}",
                       f"   - 出处：{'；'.join(r.get('sources') or [])}", ""]
        if verb:
            md += ["## 多篇文章里复现的做法（可以当规律）", ""]
            for i, r in enumerate(verb, 1):
                md += [f"{i}. **{r['claim']}**（{r['support']} 篇提到）",
                       f"   - 所以：{r['action']}",
                       f"   - 出处：{'；'.join(r.get('sources') or [])}", ""]
        if topics:
            md += ["## 话题观察（很多篇都在谈，说明是关键变量）", "",
                   "| 主题 | 谈到它的文章数 | 代表说法 |", "|---|---:|---|"]
            for r in topics:
                md.append(f"| {r['claim'].split('：')[0]} | {r.get('topic_articles')} | {r['action'][:60]} |")
            md.append("")
        (LIB / f"{pid}.md").write_text("\n".join(md), encoding="utf-8")
    print(f"已渲染 {len(by)} 个平台的经验页，共 {len(rows)} 条")


if __name__ == "__main__":
    main()
