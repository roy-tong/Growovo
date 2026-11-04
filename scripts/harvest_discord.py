#!/usr/bin/env python3
"""Discord 采集：用 bot token 读你所在服务器的频道消息。

为什么必须用 bot：Discord 的网页端把存储和事件都锁死了（localStorage 不可用、
合成点击不生效），DOM 自动化拿不到东西。bot token 是官方通道，合规且稳定。

边界：
    - 只读，不发消息、不改设置
    - 只读 bot 被邀请进的服务器
    - 带关键词过滤，只留运营/自动化/风控相关的消息
    - 每个频道最多 100 条，请求间隔 1.2 秒（Discord 限速 5 次/2 秒）

用法：
    export DISCORD_BOT_TOKEN=xxx
    python3 scripts/harvest_discord.py --list
    python3 scripts/harvest_discord.py --all --limit 200
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://discord.com/api/v9"
OUT = Path(__file__).resolve().parents[1] / "docs" / "playbooks" / "经验库" / "corpus" / "discord"

# 只留跟「运营/增长/自动化/风控」有关的消息
RELEVANT = ("运营", "增长", "获客", "转化", "留存", "投放", "私域", "内容", "选题", "涨粉",
            "风控", "限流", "封号", "违规", "矩阵", "自动化", "脚本", "爬", "抓取", "API",
            "growth", "launch", "marketing", "funnel", "churn", "retention", "pricing",
            "automation", "automate", "workflow", "rate limit", "banned", "shadowban",
            "scraping", "crawler", "proxy", "account", "conversion", "acquisition")


def headers() -> dict:
    """优先 user token（能列服务器），其次 bot token。"""
    user = os.environ.get("DISCORD_USER_TOKEN", "").strip()
    if user:
        return {"Authorization": user, "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
    tok = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not tok:
        raise SystemExit("缺 DISCORD_USER_TOKEN 或 DISCORD_BOT_TOKEN")
    return {"Authorization": f"Bot {tok}",
            "User-Agent": "DiscordBot (https://github.com/local/research, 1.0)"}


def api(path: str, retries: int = 3):
    for i in range(retries):
        try:
            req = urllib.request.Request(API + path, headers=headers())
            return json.loads(urllib.request.urlopen(req, timeout=25).read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 * (i + 1))
                continue
            return {"error": f"HTTP {e.code}"}
        except Exception as e:
            return {"error": str(e)[:80]}
    return {"error": "retries exhausted"}


def relevant(text: str) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in RELEVANT)


def run(limit: int, only: str = ""):
    me = api("/users/@me")
    if me.get("error") or not me.get("id"):
        print("token 无效：", me)
        return 0
    print(f"bot: {me.get('username')}#{me.get('discriminator','')}")
    guilds = api("/users/@me/guilds")
    if isinstance(guilds, dict):
        print("读取服务器失败：", guilds)
        return 0
    print(f"服务器 {len(guilds)} 个")
    if not guilds:
        print("bot 还没被邀请进任何服务器——先用邀请链接拉它进去")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    saved = 0
    for g in guilds:
        if only and only.lower() not in (g.get("name") or "").lower():
            continue
        chans = api(f"/guilds/{g['id']}/channels")
        if isinstance(chans, dict):
            continue
        texts = [c for c in chans if c.get("type") == 0]
        # 频道太多（100+），按名字相关度排序，只取前 25 个
        def score(c):
            n = (c.get("name") or "").lower()
            good = ("general", "ai", "agent", "mcp", "growth", "market", "launch",
                    "tool", "dev", "build", "startup", "product", "support", "intro")
            return sum(1 for k in good if k in n)
        texts.sort(key=score, reverse=True)
        texts = texts[:25]
        print(f"[{g['name']}] 文字频道取 {len(texts)} 个")
        for c in texts:
            time.sleep(1.2)
            msgs = api(f"/channels/{c['id']}/messages?limit=100")
            if isinstance(msgs, dict):
                continue
            picked = [m for m in msgs if m.get("content") and len(m["content"]) > 80
                      and relevant(m["content"])]
            if not picked:
                continue
            body = "\n\n".join(f"- {m['content'].strip()[:1200]}" for m in picked)
            key = hashlib.md5(f"{g['id']}:{c['id']}".encode()).hexdigest()[:10]
            (OUT / f"{key}.txt").write_text(
                f"TITLE: {g['name']} / #{c['name']}\nURL: https://discord.com/channels/{g['id']}/{c['id']}\n"
                f"PUBLISHED: None\nGUILD: {g['name']}\nCHANNEL: {c['name']}\n\n{body}",
                encoding="utf-8")
            saved += 1
            print(f"  ✓ #{c['name']} {len(picked)} 条相关消息")
            if saved * 100 >= limit:
                break
    print(f"共 {saved} 个频道 → {OUT}")
    return saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--guild", default="")
    ap.add_argument("--limit", type=int, default=300)
    a = ap.parse_args()
    if a.list:
        me = api("/users/@me")
        print("me:", me.get("username") or me)
        guilds = api("/users/@me/guilds")
        if isinstance(guilds, list):
            for g in guilds:
                print(" -", g.get("name"), g.get("id"))
        return
    run(a.limit, a.guild)


if __name__ == "__main__":
    main()
