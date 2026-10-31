#!/usr/bin/env python3
"""Email CDP 读通道（inbox）—— 验证等级：PROVEN（模式即 x-ops scan_inbox.py，
已在真实会话使用；本文件为其参数化移植）。

只读：扫 Gmail 线程概要 / 退信搜索，落 JSON 供线索状态更新。
与写通道（SMTP）分离——邮件渠道"SMTP 写 + CDP 读"双通道结构的读侧。
"""
import json
import time
from pathlib import Path

from core import cdp
from core.evidence import log

DATA = Path(__file__).resolve().parents[1] / "data"  # mkt-automation/data（原 parents[2] 误指仓库外层）

DEFAULT_QUERY = "to:me newer_than:7d -from:me"
BOUNCE_QUERY = "from:mailer-daemon OR from:postmaster newer_than:7d"


def scan(query=DEFAULT_QUERY, *, out=None, log=log):
    out = Path(out or (DATA / "inbox-scan.json"))
    browser, page = cdp.connect()
    try:
        page.goto("https://mail.google.com/mail/u/0/#search/" + query.replace(" ", "+"),
                  timeout=45000)
        time.sleep(4)
        if "accounts.google.com" in page.url:
            log("Gmail 未登录（先在 CDP 会话里登录）", "ERROR")
            return None
        rows, stagnant, last = [], 0, 0
        for _ in range(40):
            found = page.evaluate(
                '() => [...document.querySelectorAll(\'div[role="main"] tr.zA\')].map(r => r.innerText)')
            stagnant = stagnant + 1 if len(found) == last else 0
            last, rows = len(found), found
            if stagnant >= 2:
                break
            page.mouse.wheel(0, 4000)
            time.sleep(1.2)
        result = {"query": query, "count": len(rows), "rows": rows}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"扫描完成：{len(rows)} 条线程 → {out}")
        return result
    finally:
        page.close()


def bounces(*, out=None):
    """退信扫描：命中地址交由调用方/上层更新线索 state.bounced 并进抑制名单。"""
    return scan(BOUNCE_QUERY, out=out)


if __name__ == "__main__":
    import sys
    scan(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY)
