#!/usr/bin/env python3
"""Reddit 渠道 —— CDP 通道，验证等级：UNVERIFIED（03 渠道文档 §2）。

选择器按 old.reddit.com 公开页面结构编写，未经实测。首跑门槛：
search 只读 → dry-run 评论 → 实评 1 条（无链接）→ 无痕窗口确认公开可见。

风控红线（内置，不是建议）：
  - engage（upvote）默认禁用：vote manipulation 判定风险
  - publish 日 ≤3（throttle 表）
  - 新账号 30 天内不发外链（人工纪律，代码不代管）
  - 一切写操作默认 dry-run，--go 才执行（mkt CLI 层强制）
"""
import time
from pathlib import Path

from core import cdp
from core.evidence import Evidence, log
from core.ledger import Ledger
from core.pipeline import standard_pipeline

DATA = Path(__file__).resolve().parents[1] / "data"  # 仓库根/data（架构 §6；原 parents[2] 误指仓库外层）

OLD = "https://old.reddit.com"


def search(query, *, limit=10, out=None, log=log):
    """只读：全站/子版块搜索，抓标题+链接+子版块（线索发现）。"""
    browser, page = cdp.connect()
    try:
        from urllib.parse import quote
        cdp.safe_goto(page, f"{OLD}/search?q={quote(query)}&sort=new", log=log)
        time.sleep(3)
        if "login" in page.url or page.locator(".search-result").count() == 0:
            log("未登录或无结果（Reddit 结构可能变化，本模块 UNVERIFIED）", "WARN")
        rows = page.evaluate("""() => [...document.querySelectorAll('.search-result')].map(r => ({
            title: r.querySelector('.search-title')?.innerText || '',
            link: r.querySelector('.search-title')?.href || '',
            sub: r.querySelector('.search-subreddit-link')?.innerText || '',
            meta: r.querySelector('.search-result-meta')?.innerText || ''
          }))""")
        rows = rows[:limit]
        out = Path(out or (DATA / "reddit-search.json"))
        out.parent.mkdir(parents=True, exist_ok=True)
        import json
        out.write_text(json.dumps({"query": query, "results": rows},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"{len(rows)} 条候选 → {out}")
        return rows
    finally:
        page.close()


def _comment_box(page):
    """old.reddit 评论区首个可展开的评论文本框。"""
    page.locator("a.bylink").first.wait_for(state="visible", timeout=10000)
    page.locator(".commentarea textarea#form-content, .usertext-edit textarea").first \
        .wait_for(state="visible", timeout=10000)
    return page.locator(".commentarea textarea#form-content, .usertext-edit textarea").first


def reply(thread_url, text, *, go=False, adhoc=False, campaign=None, ledger=None, log=log,
          data_dir=None):
    """在指定帖子下评论（走统一写路径管道：回读不一致绝不发送）。"""
    data = Path(data_dir or DATA)
    ledger = ledger or Ledger(data / "ledger.jsonl")
    pipe = standard_pipeline(data, ledger, adhoc=adhoc)
    ev = Evidence(data / "shots" / (campaign or "adhoc"))
    browser, page = cdp.connect()
    try:
        def _compose(item):
            cdp.safe_goto(page, thread_url, log=log)
            time.sleep(3)
            if "reddit.com/login" in page.url:
                raise RuntimeError("Reddit 未登录")

        def _fill(item):
            cdp.type_slow(_comment_box(page), text)

        def _readback(item):
            return cdp.read_back(_comment_box(page))

        def _send(item):
            page.locator(".commentarea button[type=submit]").first.click()

        def _confirm(item, timeout=20):
            time.sleep(4)
            return page.locator(".usertext-edit").count() == 0 or \
                page.locator(f"text={text[:40]}").count() > 0

        def _shot(item):
            p = ev.shot_path(f"reddit-reply-{int(time.time())}.png")
            page.screenshot(path=str(p))
            return p

        return pipe.run_one(
            {"id": thread_url}, channel="reddit", action="reply",
            campaign=campaign or "", transport="cdp", expect=text,
            target=thread_url, compose=_compose, fill=_fill, readback=_readback,
            send=_send, confirm=_confirm, shot=_shot, dry_run=not go)
    finally:
        page.close()


def publish(subreddit, title, body, *, go=False, adhoc=False, campaign=None, ledger=None, log=log,
            data_dir=None):
    """子版块发 text post（old.reddit submit 表单）。日 ≤3 由 throttle 硬约束。"""
    data = Path(data_dir or DATA)
    ledger = ledger or Ledger(data / "ledger.jsonl")
    pipe = standard_pipeline(data, ledger, adhoc=adhoc)
    ev = Evidence(data / "shots" / (campaign or "adhoc"))
    browser, page = cdp.connect()
    try:
        def _compose(item):
            cdp.safe_goto(page, f"{OLD}/r/{subreddit}/submit?selftext=yes", log=log)
            time.sleep(3)
            if "reddit.com/login" in page.url:
                raise RuntimeError("Reddit 未登录")
            page.locator("#title, input[name=title]").first.wait_for(
                state="visible", timeout=10000)

        def _fill(item):
            cdp.type_slow(page.locator("#title, input[name=title]").first, title)
            cdp.type_slow(page.locator(".usertext-edit textarea, textarea[name=text]").first, body)

        def _readback(item):
            ta = page.locator(".usertext-edit textarea, textarea[name=text]").first
            return cdp.read_back(ta)  # 标题 input 的回读在 fill 内隐式成功（type_slow 不静默）

        def _send(item):
            page.locator("button[type=submit], .submit button").first.click()

        def _confirm(item, timeout=25):
            time.sleep(5)
            return "comments" in page.url  # 提交成功跳转帖子页

        def _shot(item):
            p = ev.shot_path(f"reddit-submit-{int(time.time())}.png")
            page.screenshot(path=str(p))
            return p

        return pipe.run_one(
            {"id": f"r/{subreddit}"}, channel="reddit", action="publish",
            campaign=campaign or "", transport="cdp", expect=body,
            target=f"https://old.reddit.com/r/{subreddit}",
            compose=_compose, fill=_fill, readback=_readback,
            send=_send, confirm=_confirm, shot=_shot, dry_run=not go)
    finally:
        page.close()
