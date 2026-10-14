#!/usr/bin/env python3
"""HN 渠道 —— CDP 写（submit/评论）+ 公共 API 读（Algolia 搜索），验证等级：UNVERIFIED。

来源：AgentMeasure 发布自动化（PUBLISH_AGENT.md 评估可行 + showhn-pack SOP）。
选择器按 news.ycombinator.com 公开页面结构编写，未经实测。首跑门槛：
search 只读 → publish dry-run → 实发 1 帖（Show HN，自有项目）→ 提交后 1 小时
内发首条作者评论 → 无痕窗口确认公开可见。

风控红线（内置，不是建议）：
  - publish 日 ≤1（throttle 表）：HN 文化=低频发射，多投即自毁（社区对营销
    账号的处置是 shadow 处理，不会通知你）
  - Show HN 只对自己拥有/深度参与的项目（人工纪律，代码不代管）
  - 标题 ≤80 字符、无 clickbait 词（Gate max_len 硬拦截）
  - engage/message 不实现：无 DM；投票操纵判定风险同 Reddit
  - 一切写操作默认 dry-run，--go 才执行（mkt CLI 层强制）
"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from core import cdp
from core.evidence import Evidence, log
from core.gates import Gate
from core.ledger import Ledger
from core.pipeline import WritePipeline
from core.throttle import Throttle

DATA = Path(__file__).resolve().parents[1] / "data"
HN = "https://news.ycombinator.com"
ALGOLIA = "https://hn.algolia.com/api/v1"
UA = {"User-Agent": "mkt-automation-hn"}

TITLE_MAX = 80  # HN 官方指引：标题不超过 80 字符（showhn-pack 提交检查同款）


def _validate_submission(title, url=None, text=None):
    """提交物校验（纯函数，离线可测）。url 与 text 二选一，标题必填且 ≤80。"""
    if not (title or "").strip():
        raise ValueError("标题为空")
    if len(title) > TITLE_MAX:
        raise ValueError(f"标题 {len(title)} 字符超上限 {TITLE_MAX}（HN 指引）")
    if bool(url) == bool(text):
        raise ValueError("url 与 text 必须二选一（HN submit 表单约束）")
    if url and not str(url).startswith(("http://", "https://")):
        raise ValueError(f"url 不合法: {url}")
    return True


# ---------------------------------------------------------------- 读原语

def search(query, *, limit=10, out=None, log=log):
    """只读：Algolia HN Search 公共 API（匿名、零凭据），抓标题+链接+分值。

    与 github 读原语同款定位：客群/讨论发现（如 "agent usage" 相关线程），
    不依赖登录态，风控中性。
    """
    req = urllib.request.Request(
        f"{ALGOLIA}/search?query={urllib.parse.quote(query)}&tags=story&hitsPerPage={limit}",
        headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    rows = [{"title": h.get("title") or "", "url": h.get("url") or f"{HN}/item?id={h.get('objectID')}",
             "points": h.get("points"), "comments": h.get("num_comments"),
             "created": (h.get("created_at") or "")[:10],
             "item": f"{HN}/item?id={h.get('objectID')}"}
            for h in data.get("hits", [])[:limit]]
    out = Path(out or (DATA / "hn-search.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"query": query, "results": rows},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    for r_ in rows:
        log(f"{r_['points'] or 0:>4}分 {r_['comments'] or 0:>4}评  {r_['title'][:70]}")
    log(f"{len(rows)} 条候选 → {out}")
    return rows


def inbox(*, log=log):
    """读收件侧：CDP 打开 /replies（登录态下=对我评论的回复）。

    HN 无通知 API；提交后的守帖动作（首小时回评论，Show HN 文化要求）从这里巡检。
    """
    browser, page = cdp.connect()
    try:
        cdp.safe_goto(page, f"{HN}/replies", log=log)
        time.sleep(2)
        if "login" in page.url:
            raise RuntimeError("HN 未登录（先在浏览器手动登录一次，agent 无法代登录）")
        rows = page.evaluate("""() => [...document.querySelectorAll('.athing')].slice(0, 30).map(r => ({
            text: r.querySelector('.commtext')?.innerText.slice(0, 200) || '',
            link: r.querySelector('a[href*="item"]')?.href || '',
            meta: r.querySelector('.onstory')?.innerText || ''
          }))""")
        for r_ in rows:
            log(f"[{r_.get('meta', '')}] {r_.get('text', '')[:80]}…")
        log(f"{len(rows)} 条新回复（守帖用：逐条实质回应，不灌 thanks）")
        return rows
    finally:
        page.close()


# ---------------------------------------------------------------- 写原语（统一写路径）

def publish(title, url=None, text=None, *, go=False, campaign=None,
            ledger=None, log=log):
    """提交（Show HN / 链接帖 / 文本帖）—— news.ycombinator.com/submit 表单。

    走统一管道：Gate（标题 ≤80/占位符/禁字符）→ 节流 → 回读比对 → 截图 →
    dry-run 停点 → 提交 → 确认跳转 item 页。日 ≤1 由 throttle 硬约束。
    """
    _validate_submission(title, url, text)
    ledger = ledger or Ledger(DATA / "ledger.jsonl")
    pipe = WritePipeline(gate=Gate(ledger), ledger=ledger,
                         throttle=Throttle(DATA / "state"))
    ev = Evidence(DATA / "shots" / (campaign or "adhoc"))
    target = f"hn:submit:{(url or 'text')[:60]}"
    browser, page = cdp.connect()
    try:
        def _compose(item):
            cdp.safe_goto(page, f"{HN}/submit", log=log)
            time.sleep(2)
            if "login" in page.url:
                raise RuntimeError("HN 未登录（先在浏览器手动登录一次）")
            page.locator("input[name=title]").first.wait_for(
                state="visible", timeout=10000)

        def _fill(item):
            cdp.type_slow(page.locator("input[name=title]").first, title)
            if url:
                cdp.type_slow(page.locator("input[name=url]").first, url)
            if text:
                cdp.type_slow(page.locator("textarea[name=text]").first, text)

        def _readback(item):
            got = page.locator("input[name=title]").first.input_value()
            if text:
                got += "\n" + page.locator("textarea[name=text]").first.input_value()
            return got  # url 为 input[type=url]，填错会被 HN 表单自身拒绝，不重复回读

        def _send(item):
            page.locator("form[action='submit'] input[type=submit], "
                         "form[action='/submit'] input[type=submit]").first.click()

        def _confirm(item, timeout=25):
            time.sleep(5)
            return "item?id=" in page.url  # 提交成功跳转新条目页

        def _shot(item):
            p = ev.shot_path(f"hn-submit-{int(time.time())}.png")
            page.screenshot(path=str(p))
            return p

        return pipe.run_one(
            {"id": target}, channel="hn", action="publish",
            campaign=campaign or "", transport="cdp",
            expect=(title + "\n" + text) if text else title,
            target=target, compose=_compose, fill=_fill, readback=_readback,
            send=_send, confirm=_confirm, shot=_shot, dry_run=not go)
    finally:
        page.close()


def reply(item, text, *, go=False, campaign=None, ledger=None, log=log):
    """在条目下评论 —— item 为 news.ycombinator.com/item?id=N URL 或 item:N。

    首要用途：Show HN 提交后的首条作者评论（HN 文化：作者须说明背景+欢迎批评，
    提交后 1 小时内）；其次是守帖期对实质质疑的逐条回应。
    """
    item = str(item)
    if item.startswith("item:"):
        item = f"{HN}/item?id={item[5:]}"
    if "news.ycombinator.com" not in item:
        raise ValueError(f"item 应为 news.ycombinator.com URL 或 item:N: {item}")
    ledger = ledger or Ledger(DATA / "ledger.jsonl")
    pipe = WritePipeline(gate=Gate(ledger), ledger=ledger,
                         throttle=Throttle(DATA / "state"))
    ev = Evidence(DATA / "shots" / (campaign or "adhoc"))
    browser, page = cdp.connect()
    try:
        def _compose(i):
            cdp.safe_goto(page, item, log=log)
            time.sleep(2)
            if "login" in page.url:
                raise RuntimeError("HN 未登录（先在浏览器手动登录一次）")
            ta = page.locator("form[action='/comment'] textarea, "
                              "textarea[name=text]").first
            ta.wait_for(state="visible", timeout=10000)

        def _fill(i):
            cdp.type_slow(page.locator("form[action='/comment'] textarea, "
                                       "textarea[name=text]").first, text)

        def _readback(i):
            return cdp.read_back(page.locator("form[action='/comment'] textarea, "
                                              "textarea[name=text]").first)

        def _send(i):
            page.locator("form[action='/comment'] input[type=submit], "
                         "form[action='/comment'] button").first.click()

        def _confirm(i, timeout=20):
            time.sleep(4)
            return page.locator(f"text={text[:40]}").count() > 0  # 评论区出现本条

        def _shot(i):
            p = ev.shot_path(f"hn-reply-{int(time.time())}.png")
            page.screenshot(path=str(p))
            return p

        return pipe.run_one(
            {"id": item}, channel="hn", action="reply",
            campaign=campaign or "", transport="cdp", expect=text,
            target=item, compose=_compose, fill=_fill, readback=_readback,
            send=_send, confirm=_confirm, shot=_shot, dry_run=not go)
    finally:
        page.close()
