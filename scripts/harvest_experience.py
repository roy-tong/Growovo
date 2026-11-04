#!/usr/bin/env python3
"""经验库采集器：从公众号实操文里抓「别人真跑过」的做法。

为什么要有这个脚本：用户经验是主观的、会过时的，**必须靠样本量和新鲜度**才有说服力。
一次性手抓 20 条会在三个月后失效，所以要能定期重跑。

用法：
    python3 scripts/harvest_experience.py --platform xiaohongshu --pages 40
    python3 scripts/harvest_experience.py --all            # 全部平台

产出：
    docs/playbooks/经验库/corpus/<platform>/<hash>.txt   原文（含标题、URL、发布时间）
    docs/playbooks/经验库/corpus/<platform>/index.jsonl  每篇的元数据

纪律：
    - 只采公开文章，不做登录、不绕验证码；
    - 每篇保留原始 URL 与发布时间，便于复核与过期判断；
    - 抓不到就少抓，不编。
"""
from __future__ import annotations

import argparse
import pathlib
import hashlib
import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
import http.cookiejar
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "docs" / "playbooks" / "经验库" / "corpus"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 每个平台一组查询词：越具体越容易命中实操文
QUERIES = {
    "xiaohongshu": ["小红书运营", "小红书涨粉", "小红书限流", "小红书笔记", "小红书起号",
                    "小红书素人", "小红书爆款", "小红书选题"],
    "zhihu": ["知乎运营", "知乎涨粉", "知乎回答", "知乎引流", "知乎算法", "知乎机构号"],
    "wechat": ["公众号运营", "公众号涨粉", "公众号搜一搜", "公众号冷启动", "公众号排版"],
    "douyin": ["抖音运营", "抖音起号", "抖音完播率", "抖音选题", "抖音账号"],
    "bilibili": ["B站运营", "B站UP主", "B站涨粉", "B站封面", "B站推荐机制"],
    "zhishixingqiu": ["知识星球运营", "知识星球变现", "知识星球社群"],
    "weibo": ["微博运营", "微博涨粉", "微博话题"],
    "juejin": ["掘金社区运营", "掘金发文", "掘金技术博客"],
    "jike": ["即刻运营", "即刻社区"],
    "v2ex": ["V2EX 社区", "V2EX 发帖"],
}


def opener():
    # Public harvesting must retain certificate and hostname verification. A
    # corporate CA can be configured through the normal SSL_CERT_FILE setting.
    ctx = ssl.create_default_context()
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj),
                                     urllib.request.HTTPSHandler(context=ctx))
    op.addheaders = [("User-Agent", UA), ("Accept-Language", "zh-CN,zh;q=0.9")]
    return op


def strip_html(h: str) -> str:
    h = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"),
                 ("&lt;", "<"), ("&gt;", ">"), ("&ldquo;", "“"), ("&rdquo;", "”")):
        h = h.replace(a, b)
    return re.sub(r"\s+", " ", h).strip()


def publish_ts(html: str):
    for pat in (r"var\s+ct\s*=\s*\"(\d{9,11})\"", r"create_time\s*=\s*'?\"?(\d{9,11})"):
        m = re.search(pat, html)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    return None


def search(op, query, cap=6):
    url = "https://weixin.sogou.com/weixin?type=2&query=" + urllib.parse.quote(query)
    try:
        h = op.open(url, timeout=18).read().decode("utf-8", "ignore")
    except Exception:
        return []
    out = []
    for href, title in re.findall(r"<h3>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", h, re.S):
        link = "https://weixin.sogou.com" + re.sub(r"[\r\n\t ]+", "", href)
        import html as H
        t = re.sub(r"<[^>]+>", "", H.unescape(title)).strip()
        out.append((t, link))
        if len(out) >= cap:
            break
    return out


def resolve_and_fetch(op, link):
    try:
        body = op.open(link, timeout=18).read().decode("utf-8", "ignore")
    except Exception:
        return None
    parts = re.findall(r"url \+= '([^']*)'", body)
    real = "".join(parts).replace("@", "")
    if not real.startswith("http"):
        return None
    try:
        html = op.open(real, timeout=25).read().decode("utf-8", "ignore")
    except Exception:
        return None
    text = strip_html(html)
    if len(text) < 1200:
        return None
    return {"url": real, "text": text, "published": publish_ts(html)}


def harvest_platform(pid: str, pages: int, workers: int = 1, days: int = 0) -> dict:
    """单线程复用同一个 opener：搜狗的重定向握手依赖搜索页种下的 cookie，
    换 opener 就拿不到真实地址。"""
    op = opener()
    seen, items = set(), []
    for q in QUERIES.get(pid, []):
        for title, link in search(op, q):
            time.sleep(0.8)
            if len(items) >= pages:
                break
            time.sleep(1.2)   # 搜狗对连续请求有节流，慢一点反而抓得到
            got = resolve_and_fetch(op, link)
            if not got:
                continue
            if days and got.get("published"):
                age = (time.time() - got["published"]) / 86400
                if age > days:
                    continue
            key = hashlib.md5(got["url"].encode()).hexdigest()[:10]
            if key in seen:
                continue
            seen.add(key)
            items.append({"platform": pid, "query": q, "title": title, "key": key, **got})

    d = OUT / pid
    d.mkdir(parents=True, exist_ok=True)
    index = []
    for r in items:
        p = d / f"{r['key']}.txt"
        pub = r.get("published")
        pub_iso = ("None" if not pub else
                   __import__("datetime").datetime.fromtimestamp(pub).date().isoformat())
        p.write_text(f"TITLE: {r['title']}\nURL: {r['url']}\nPUBLISHED: {pub_iso}\n"
                     f"ASR: false\nASR_ENGINE: none\nHUMAN_REVIEWED: false\n\n{r['text']}",
                     encoding="utf-8")
        index.append({"title": r["title"], "url": r["url"], "canonical_url": r["url"],
                      "published": r.get("published"), "rights": "unknown",
                      "relevance": "unreviewed", "human_reviewed": False, "asr": False,
                      "file": f"{r['key']}.txt", "query": r["query"]})
    (d / "index.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in index) + "\n",
                                   encoding="utf-8")
    ages = []
    now = time.time()
    for x in index:
        if x.get("published"):
            ages.append((now - x["published"]) / 86400)
    ages.sort()
    return {"platform": pid, "articles": len(index),
            "with_date": len(ages),
            "median_age_days": round(ages[len(ages) // 2], 1) if ages else None,
            "newest_age_days": round(ages[0], 1) if ages else None}


def harvest_urls(pid: str, path: str, days: int = 0) -> dict:
    """直接喂链接：你在微信里搜到/收藏的文章，把链接贴进一个 txt，我来抓。

    为什么要有这条路：搜狗只给历史文章（实测最新也是几年前），
    而 mp.weixin.qq.com 的正文本身是能抓的——缺的只是「近期文章的链接」。
    """
    op = opener()
    urls = [u.strip() for u in pathlib.Path(path).read_text(encoding="utf-8").splitlines()
            if u.strip().startswith("http")]
    d = OUT / pid
    d.mkdir(parents=True, exist_ok=True)
    saved, skipped = 0, 0
    import time as _t
    from datetime import datetime
    for u in urls:
        _t.sleep(1.5)
        got = resolve_and_fetch(op, u)
        if not got:
            skipped += 1
            continue
        if days and got.get("published"):
            if (_t.time() - got["published"]) / 86400 > days:
                skipped += 1
                continue
        pub_iso = ("None" if not got.get("published") else
                   datetime.fromtimestamp(got["published"]).date().isoformat())
        key = hashlib.md5(got["url"].encode()).hexdigest()[:10]
        (d / f"{key}.txt").write_text(
            f"TITLE: 未命名（纯链接导入）\nURL: {got['url']}\nPUBLISHED: {pub_iso}\n"
            f"ASR: false\nASR_ENGINE: none\nHUMAN_REVIEWED: false\n\n{got['text']}",
            encoding="utf-8")
        saved += 1
        print(f"  ✓ {got['url'][:50]} | {len(got['text'])} 字 | {pub_iso}")
    print(f"导入 {saved} 篇，跳过 {skipped} 篇 → {d}")
    return {"platform": pid, "articles": saved}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--pages", type=int, default=40)
    ap.add_argument("--days", type=int, default=0, help="只要最近 N 天（0=不限）")
    ap.add_argument("--urls", default="", help="从文件读链接直接抓（公众号最实用的入口）")
    args = ap.parse_args()
    if args.urls:
        harvest_urls(args.platform or "wechat", args.urls, args.days)
        return
    targets = [args.platform] if args.platform else (list(QUERIES) if args.all else [])
    if not targets:
        ap.error("需要 --platform 或 --all")
    for pid in targets:
        res = harvest_platform(pid, args.pages, days=args.days)
        print(f"[{pid}] 抓到 {res['articles']} 篇，"
              f"有日期 {res['with_date']} 篇，"
              f"最新 {res['newest_age_days']} 天前，中位 {res['median_age_days']} 天前")


if __name__ == "__main__":
    main()
