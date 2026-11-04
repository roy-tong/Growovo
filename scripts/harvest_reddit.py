#!/usr/bin/env python3
"""Reddit 采集：走 redlib 镜像 + 已登录浏览器，抓帖子和高赞评论。

为什么绕镜像：reddit.com 直连 403（本机 IP 被拦），old.reddit 同样 403，
redlib 实例要过 JS 挑战——只有真浏览器能过，所以复用 CDP。

抓什么：帖子正文 + 前若干条评论。评论往往比正文有用——
「我试过 X，结果 Y」这种句子只在评论区出现。

用法：
    python3 scripts/harvest_reddit.py --subs SaaS,Entrepreneur --limit 8
    python3 scripts/harvest_reddit.py --all --limit 15
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import time
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "playbooks" / "经验库" / "corpus" / "reddit"
MIRROR = "https://safereddit.com"

# 信源是 Reddit，要采的是「运营/增长/自动化/风控」的经验帖
SUBS = ["SaaS", "Entrepreneur", "startups", "marketing", "growthhacking",
        "indiehackers", "juststart", "smallbusiness", "digital_marketing",
        "SEO", "socialmedia", "automation", "devops"]


def human_pause(lo=2.0, hi=5.0):
    time.sleep(random.uniform(lo, hi))


def clean(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def run(subs, limit, sort="top", window="month"):
    from playwright.sync_api import sync_playwright
    OUT.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx = b.contexts[0]
    page = ctx.new_page()
    total = 0
    for sub in subs:
        url = f"{MIRROR}/r/{sub}/{sort}?t={window}"
        try:
            page.goto(url, wait_until="load", timeout=60000)
        except Exception as e:
            print(f"[{sub}] 打开失败 {type(e).__name__}")
            continue
        time.sleep(10)   # 等 JS 挑战过去
        posts = page.evaluate("""() => {
            const out=[];
            for (const a of document.querySelectorAll('a[href*="/comments/"]')) {
                const t=(a.innerText||'').trim();
                if (t.length < 15) continue;
                out.push({title: t, href: a.getAttribute('href')});
                if (out.length >= 40) break;
            }
            return out;
        }""")
        seen, picked = set(), []
        for p in posts:
            key = p["href"].split("/")[4] if len(p["href"].split("/")) > 4 else p["href"]
            if key in seen:
                continue
            seen.add(key)
            picked.append(p)
        print(f"[{sub}] 帖子 {len(picked)}，取 {min(limit, len(picked))}")
        for p in picked[:limit]:
            full = MIRROR + p["href"] if p["href"].startswith("/") else p["href"]
            human_pause()
            try:
                page.goto(full, wait_until="load", timeout=60000)
                time.sleep(6)
                got = page.evaluate("""() => {
                    const body = document.querySelector('.post_body, .post .md, .post') ;
                    const comments = [...document.querySelectorAll('.comment .comment_body, .comment_body')]
                        .map(e => (e.innerText||'').trim()).filter(t => t.length > 40).slice(0, 12);
                    return {title: document.title.replace(' - redlib','').slice(0,120),
                            body: body ? body.innerText.trim() : '',
                            comments};
                }""")
            except Exception as e:
                print(f"  跳过 {p['title'][:30]}：{type(e).__name__}")
                continue
            body = clean(got.get("body", ""))
            comments = [clean(c) for c in (got.get("comments") or [])]
            text = body + "\n\n" + "\n\n".join(f"- {c}" for c in comments)
            if len(text) < 250:
                print(f"  跳过（内容太短 {len(text)}）：{p['title'][:30]}")
                continue
            key = hashlib.md5(full.encode()).hexdigest()[:10]
            (OUT / f"{key}.txt").write_text(
                f"TITLE: {clean(got.get('title') or p['title'])}\nURL: {full}\n"
                f"PUBLISHED: None\nSUBREDDIT: r/{sub}\n\n{text}", encoding="utf-8")
            total += 1
            print(f"  ✓ r/{sub} {p['title'][:40]} | {len(text)} 字")
    pw.stop()
    print(f"共 {total} 帖 → {OUT}")
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subs", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--sort", default="top")
    ap.add_argument("--window", default="month")
    a = ap.parse_args()
    subs = [s.strip() for s in a.subs.split(",") if s.strip()] if a.subs else (SUBS if a.all else SUBS[:3])
    run(subs, a.limit, a.sort, a.window)


if __name__ == "__main__":
    main()
