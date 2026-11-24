#!/usr/bin/env python3
"""LinkedIn 渠道 —— CDP 通道，验证等级：UNVERIFIED（03 渠道文档 §3）。

业务执行不再被平台合规口径或终端确认阻断；平台规则只作为风险提示。
选择器按 LinkedIn 公开页面结构编写，UI 变化频繁，全部 UNVERIFIED，
首跑仍需显式技术确认，审批、配额、身份和回读保持不变。
"""
import time
from pathlib import Path

from core import cdp
from core.evidence import Evidence, log
from core.ledger import Ledger
from core.pipeline import standard_pipeline
from core.throttle import Throttle

DATA = Path(__file__).resolve().parents[1] / "data"  # 仓库根/data（原 parents[2] 误指仓库外层）


def search(keywords, *, limit=10, out=None, log=log):
    """Tier A 只读：搜索人，抓名字/头衔/链接进线索库（b2b-account-mapping 的上游）。"""
    from urllib.parse import quote
    url = f"https://www.linkedin.com/search/results/people/?keywords={quote(keywords)}"
    browser, page = cdp.connect(site="linkedin", data_dir=DATA, managed_url=url)
    try:
        cdp.safe_goto(page, url, log=log)
        time.sleep(4)
        if "login" in page.url:
            log("LinkedIn 未登录", "ERROR")
            return []
        rows = page.evaluate("""() => [...document.querySelectorAll('.reusable-search__result-container')].map(r => ({
            name: r.querySelector('.entity-result__title-text')?.innerText?.trim() || '',
            title: r.querySelector('.entity-result__primary-subtitle')?.innerText?.trim() || '',
            link: r.querySelector('a.app-aware-link')?.href || ''
          }))""")
        rows = [r for r in rows if r["name"]][:limit]
        out = Path(out or (DATA / "linkedin-search.json"))
        out.parent.mkdir(parents=True, exist_ok=True)
        import json
        out.write_text(json.dumps({"keywords": keywords, "results": rows},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"{len(rows)} 人 → {out}（公司账号须按 b2b-account-mapping 富化到员工后才可入队）")
        return rows
    finally:
        cdp.release_page(page)


def message(profile_url, note, *, go=False, adhoc=False, campaign=None, ledger=None, log=log,
            data_dir=None):
    """连接请求：填好 note → 截图 → 业务授权与执行安全检查 → 发送。

    go=False 为显式 dry-run；go=True 不再要求环境解锁或终端确认。
    """
    data = Path(data_dir or DATA)
    ledger = ledger or Ledger(data / "ledger.jsonl")
    throttle = Throttle(data / "state")
    if go and not throttle.daily_allows("linkedin", "message"):
        raise SystemExit(
            "LinkedIn 今日连接额度已用完（≤10 条/日），本条未发送；"
            "明天预算日重置后再跑，或改用 email 联系同一对象。"
            "当天补量连发会提高平台风控风险，保持每天 10 条以内的节奏更稳。")
    pipe = standard_pipeline(data, ledger, adhoc=adhoc)
    ev = Evidence(data / "shots" / (campaign or "adhoc"))
    browser, page = cdp.connect(site="linkedin", data_dir=data, managed_url=profile_url)
    try:
        def _compose(item):
            cdp.safe_goto(page, profile_url, log=log)
            time.sleep(4)
            if "login" in page.url:
                raise RuntimeError("LinkedIn 未登录")
            btn = page.locator("button:has-text('Connect'), button[aria-label*='Connect']").first
            btn.wait_for(state="visible", timeout=10000)
            btn.click()
            time.sleep(1.5)
            add = page.locator("button:has-text('Add a note'), button:has-text('添加备注')").first
            try:
                if add.is_visible(timeout=2000):
                    add.click()
                    time.sleep(1)
            except Exception:
                pass  # 有的入口直接进带 note 的表单
            page.locator("textarea[name='message'], #custom-message").first \
                .wait_for(state="visible", timeout=8000)

        def _fill(item):
            cdp.type_slow(
                page.locator("textarea[name='message'], #custom-message").first, note)

        def _readback(item):
            return cdp.read_back(page.locator("textarea[name='message'], #custom-message").first)

        def _send(item):
            page.locator("button[type=submit]:has-text('Send'), button:has-text('发送'), button[aria-label='Send']").first.click()

        def _confirm(item, timeout=15):
            time.sleep(4)
            return page.locator("button:has-text('Connect')").count() == 0 or \
                page.locator(".artdeco-modal").count() == 0

        def _shot(item):
            p = ev.shot_path(f"li-connect-{int(time.time())}.png")
            page.screenshot(path=str(p))
            return p

        mode = "业务执行（平台风险仅提示）" if go else "dry-run"
        log(f"[linkedin] {profile_url} — {mode}，日预算 {throttle.daily_used('linkedin', 'message')}/10")
        return pipe.run_one(
            {"id": profile_url}, channel="linkedin", action="message",
            campaign=campaign or "", transport="cdp", expect=note,
            target=profile_url, compose=_compose, fill=_fill, readback=_readback,
            send=_send, confirm=_confirm, shot=_shot, dry_run=not go)
    finally:
        cdp.release_page(page)
