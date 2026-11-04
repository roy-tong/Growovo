#!/usr/bin/env python3
"""CDP 采集：用你已经登录的浏览器，人工速度地读公开内容。

边界（写死在代码里，不靠自觉）：
    - 只连**已存在的**浏览器，不新开无痕、不换账号
    - 单标签复用，每步随机 3–8 秒，不并发
    - 单次上限默认 10 条，跑完就停
    - 一旦出现验证码/滑块/安全验证：**立即整体停止**并记录，不重试、不绕过
    - 只读公开笔记/视频的标题与正文，不碰私信、不碰他人主页

用法：
    python3 scripts/harvest_cdp.py attach
    python3 scripts/harvest_cdp.py search --platform xhs --q 小红书运营 --limit 10
    python3 scripts/harvest_cdp.py search --platform douyin --q 抖音运营 --limit 10
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import time
import urllib.parse
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "docs" / "playbooks" / "经验库" / "corpus"
CDP = "http://127.0.0.1:9222"

CHALLENGE = re.compile(r"验证码|安全验证|滑块|人机验证|请完成验证|captcha", re.I)

PLATFORMS = {
    "xhs": {
        "name": "小红书",
        "search_url": "https://www.xiaohongshu.com/search_result?keyword={q}&source=web_search_result_notes",
        "card_sel": 'a[href*="/explore/"]',
        "wait": 5,
        "detail_wait": 4,
        "text_js": """() => {
            const pick = document.querySelector('#detail-desc, .note-content, .desc');
            const title = document.querySelector('.note-content .title, #detail-title');
            return {title: (title && title.innerText) || document.title,
                    body: (pick && pick.innerText) || document.body.innerText.slice(0, 3000)};
        }""",
    },
    "douyin": {
        "name": "抖音",
        "search_url": "https://www.douyin.com/search/{q}?type=video",
        "card_sel": ".search-result-card",
        "wait": 8,
        "detail_wait": 5,
        "text_js": """() => {
            const pick = document.querySelector('[data-e2e="video-desc"], .video-info-detail, .desc');
            return {title: document.title,
                    body: (pick && pick.innerText) || document.body.innerText.slice(0, 3000)};
        }""",
    },
}


def parse_card_date(text: str):
    """卡片上的时间只有「MM-DD」或「N天前」，按当前日期补全年份。"""
    from datetime import date, timedelta
    t = (text or "").strip()
    today = date.today()
    m = re.search(r"(?<!\d)(\d{1,2})-(\d{1,2})(?!\d)", t)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        y = today.year if mo <= today.month else today.year - 1
        try:
            got = date(y, mo, d)
        except ValueError:
            return None
        if got > today:                 # 卡片只有月日，跨年时会算成未来
            got = got.replace(year=got.year - 1)
        return got
    m = re.search(r"(\d+)\s*天前", t)
    if m:
        return today - timedelta(days=int(m.group(1)))
    if "昨天" in t:
        return today - timedelta(days=1)
    if "今天" in t or "小时前" in t or "分钟前" in t:
        return today
    return None


class ChallengeHit(Exception):
    """遇到验证就抛，整个任务停。"""


def human_pause(lo=3.0, hi=8.0):
    time.sleep(random.uniform(lo, hi))


def connect():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(CDP)
    ctx = browser.contexts[0]
    page = ctx.new_page()          # 独占标签页，避免和其他采集器抢同一个
    return pw, browser, ctx, page


def check_challenge(page, where=""):
    try:
        txt = page.evaluate("() => document.body.innerText.slice(0, 4000)")
    except Exception:
        return
    if CHALLENGE.search(txt or ""):
        raise ChallengeHit(f"{where} 出现安全验证：{CHALLENGE.search(txt).group(0)}")


def collect_tokens(page, limit):
    """小红书笔记必须带 xsec_token 才能打开；从页面状态里把 id→token 取出来。"""
    return page.evaluate("""(limit) => {
        const st = window.__INITIAL_STATE__ || {};
        let raw = JSON.stringify(st);
        const out=[];
        const re=/"noteId":"([0-9a-f]{24})"[\s\S]{0,400}?"xsecToken":"([^"]+)"/g;
        let m; const seen=new Set();
        while((m=re.exec(raw))!==null && out.length<limit){
            if(seen.has(m[1])) continue; seen.add(m[1]);
            out.push({id:m[1], token:m[2]});
        }
        return out;
    }""", limit)


def collect_cards(page, sel, limit):
    """卡片上的时间藏在整块 innerText 里（标题 | 作者 | MM-DD | 点赞），整体取回来再解析。"""
    page.mouse.wheel(0, 900)
    human_pause(1.5, 3.0)
    page.mouse.wheel(0, 900)
    human_pause(1.5, 3.0)
    return page.evaluate("""(limit) => {
        const out=[];
        for (const b of document.querySelectorAll('section.note-item')) {
            const a=b.querySelector('a[href*="/explore/"]');
            const href=a? a.getAttribute('href'):'';
            if(!href) continue;
            const ti=b.querySelector('.title, [class*="title"]');
            out.push({href,
                      stamp: (b.innerText||'').split(String.fromCharCode(10)).join(' | ').trim(),
                      title: ti? ti.innerText.trim():''});
            if(out.length>=limit) break;
        }
        return out;
    }""", limit)


def run_search(platform: str, query: str, limit: int, dry: bool, days: int = 60) -> int:
    cfg = PLATFORMS[platform]
    pw, browser, ctx, page = connect()
    saved = 0
    try:
        search = cfg["search_url"].format(q=urllib.parse.quote(query))
        page.goto(search, wait_until="domcontentloaded", timeout=45000)
        human_pause(cfg["wait"], cfg["wait"] + 3)
        check_challenge(page, "搜索页")
        cards = collect_cards(page, cfg["card_sel"], max(limit * 4, 20))

        from datetime import date, timedelta
        cutoff = date.today() - timedelta(days=days)
        picked = []
        for c in cards:
            d = parse_card_date(c.get("stamp", ""))
            if d is None or d < cutoff:
                continue
            picked.append({**c, "date": d})
        print(f"[{cfg['name']}] 卡片 {len(cards)} 张，{days} 天内 {len(picked)} 张，取 {min(limit,len(picked))} 张")
        if not picked:
            print("  没有近期内容——换关键词或放宽 --days")
            return 0

        for c in picked[:limit]:
            href = c["href"]
            human_pause()
            try:
                card = page.locator(f'section.note-item:has(a[href="{href}"])').first
                for _ in range(6):
                    if card.count() > 0:
                        break
                    page.mouse.wheel(0, 700)
                    human_pause(1.2, 2.2)
                if card.count() == 0:
                    print(f"  跳过（找不到卡片）：{c.get('title','')[:30]}")
                    continue
                card.click(timeout=20000)
                human_pause(cfg["detail_wait"], cfg["detail_wait"] + 2)
                check_challenge(page, "详情页")
                got = page.evaluate(cfg["text_js"])
                url = page.url
                page.keyboard.press("Escape")     # 关掉浮层，留在搜索结果页
                human_pause(1.5, 2.5)
            except ChallengeHit:
                raise
            except Exception as e:
                print(f"  跳过 {href[:24]}：{type(e).__name__}")
                continue
            body = (got.get("body") or "").strip()
            title = (got.get("title") or c.get("title") or "").strip()
            if len(body) < 200:
                print(f"  跳过（正文 {len(body)} 字）：{title[:30]}")
                continue
            if dry:
                print(f"  [试跑] {c['date']} {title[:34]} | {len(body)} 字")
                saved += 1
                continue
            d = OUT / {"xhs": "xiaohongshu"}.get(platform, platform)
            d.mkdir(parents=True, exist_ok=True)
            key = hashlib.md5(url.encode()).hexdigest()[:10]
            (d / f"{key}.txt").write_text(
                f"TITLE: {title}\nURL: {url}\nPUBLISHED: {c['date'].isoformat()}\n"
                f"QUERY: {query}\n\n{body}", encoding="utf-8")
            with (d / "index.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"title": title, "url": url, "query": query,
                                     "platform": platform, "published": c["date"].isoformat(),
                                     "chars": len(body)}, ensure_ascii=False) + "\n")
            saved += 1
            print(f"  ✓ {c['date']} {title[:34]} | {len(body)} 字")
    except ChallengeHit as e:
        print(f"\n⛔ {e}\n已立即停止。请人工完成验证后再决定是否重跑——不要反复触发。",
              file=sys.stderr)
        return -1
    finally:
        pw.stop()
    print(f"完成 {saved} 条")
    return saved


def harvest_douyin(query: str, limit: int, days: int = 60) -> int:
    """抖音视频页是 blob + MSE，拿不到可下载地址；但搜索页本身有卡片描述和 AI 汇总。

    所以这里只采「文字部分」：每张卡片的标题/时长/播放/话题标签，外加搜索页顶部的
    AI 汇总结论。这是诚实的能力边界——不做做不到的事。
    """
    pw, browser, ctx, page = connect()
    saved = 0
    try:
        page.goto(PLATFORMS["douyin"]["search_url"].format(q=urllib.parse.quote(query)),
                  wait_until="domcontentloaded", timeout=45000)
        human_pause(9, 12)
        check_challenge(page, "搜索页")
        page.mouse.wheel(0, 600)
        human_pause(2, 3)
        data = page.evaluate("""(limit) => {
            const cards=[...document.querySelectorAll('.search-result-card')];
            const out=[];
            for (const c of cards) {
                const t=c.innerText.split(String.fromCharCode(10)).join(' | ').trim();
                if (t.length < 12) continue;
                const a=c.querySelector('a[href*="/video/"]');
                out.push({text:t, href:a? a.getAttribute('href'):''});
                if(out.length>=limit) break;
            }
            const ai=document.body.innerText.indexOf('为你生成回答')>=0 ?
                     document.body.innerText.slice(0, 1200) : '';
            return {cards: out, ai};
        }""", limit)
        cards = data["cards"] if isinstance(data, dict) else []
        ai = (data.get("ai") or "") if isinstance(data, dict) else ""
        print(f"[抖音] 卡片 {len(cards)} 张")
        d = OUT / "douyin"
        d.mkdir(parents=True, exist_ok=True)
        if ai:
            key = hashlib.md5((query + "ai").encode()).hexdigest()[:10]
            (d / f"ai-{key}.txt").write_text(
                f"TITLE: 抖音搜索 AI 汇总 · {query}\nURL: https://www.douyin.com/search/{urllib.parse.quote(query)}\n"
                f"PUBLISHED: None\nKIND: ai_summary\n\n{ai}", encoding="utf-8")
            saved += 1
            print(f"  ✓ AI 汇总 {len(ai)} 字")
        for c in cards:
            body = c["text"]
            if len(body) < 20:
                continue
            key = hashlib.md5(body.encode()).hexdigest()[:10]
            url = ("https:" + c["href"]) if c["href"].startswith("//") else c["href"]
            (d / f"{key}.txt").write_text(
                f"TITLE: {body[:40]}\nURL: {url}\nPUBLISHED: None\nQUERY: {query}\n\n{body}",
                encoding="utf-8")
            saved += 1
            print(f"  ✓ {body[:44]}")
    except ChallengeHit as e:
        print(f"\n⛔ {e}\n已立即停止。", file=sys.stderr)
        return -1
    finally:
        pw.stop()
    print(f"完成 {saved} 条")
    return saved


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("attach")
    s = sub.add_parser("search")
    s.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    s.add_argument("--q", required=True)
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--days", type=int, default=60, help="只要最近 N 天的内容")
    s.add_argument("--cards-only", action="store_true", help="抖音：只采搜索页文字")
    args = ap.parse_args()

    if args.cmd == "attach":
        pw, browser, ctx, page = connect()
        try:
            print("标题:", page.title(), "\nURL:", page.url)
            print("cookie 域:", sorted({c["domain"] for c in ctx.cookies()})[:6])
        finally:
            pw.stop()
        return 0
    if args.limit > 20:
        print("单次最多 20 条（保护账号），大额度请用 scripts/harvest_plan.py 分批跑",
              file=sys.stderr)
        return 2
    if args.platform == "douyin":
        got = harvest_douyin(args.q, args.limit, args.days)
    else:
        got = run_search(args.platform, args.q, args.limit, args.dry_run, args.days)
    # 退出码只表达成败：0=跑完了，2=触发验证（熔断信号）。抓到的条数不算退出码。
    return 0 if got >= 0 else 2


if __name__ == "__main__":
    sys.exit(main())
