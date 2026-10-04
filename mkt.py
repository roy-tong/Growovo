#!/usr/bin/env python3
"""mkt —— 自动化营销组件统一入口（架构文档 §1）。

用法:
  mkt <channel> <action> [参数] [--go] [--campaign <id>] [--only <target>]

统一语义：**写操作默认 dry-run，--go 才实跑**（比 x-ops 更强的安全缺省）。
  mkt status                账本聚合（channel/campaign/result）
  mkt check                 CDP 会话与各渠道登录态检查

示例:
  python3 mkt.py email message --queue data/queues/er-wave7.json        # dry-run
  python3 mkt.py email message --queue data/queues/er-wave7.json --go   # 实跑
  python3 mkt.py x publish --campaign day3 -- --dry-run 传给 vendor（默认即 dry-run）
  python3 mkt.py reddit search --query "agent observability"
  python3 mkt.py linkedin message --url <profile> --note-file note.txt
"""
import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DATA = BASE / "data"


def _ledger():
    from core.ledger import Ledger
    return Ledger(DATA / "ledger.jsonl")


def _throttle():
    from core.throttle import Throttle
    return Throttle(DATA / "state")


def cmd_status(args):
    print(json.dumps(_ledger().summary(), ensure_ascii=False, indent=1))


def cmd_check(args):
    """CDP 会话检查（不碰任何写操作）。"""
    from core import cdp
    try:
        browser, page = cdp.connect()
    except Exception as exc:
        print(f"[ERROR] 无法连接 {cdp.CDP_URL}: {exc}")
        print("先跑 ./launch.command 启动带调试端口的 Chrome。")
        return 2
    try:
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30000)
        x_ok = "/login" not in page.url
        page.goto("https://mail.google.com", wait_until="domcontentloaded", timeout=30000)
        g_ok = "accounts.google.com" not in page.url
        print(f"CDP 会话正常。X 登录态: {'OK' if x_ok else '未登录'}；Gmail 登录态: {'OK' if g_ok else '未登录'}")
        return 0
    finally:
        page.close()


def cmd_x(args):
    from channels.x.adapter import run as x_run
    extra = list(args.extra or [])
    if getattr(args, "file", None):
        extra += ["--file", args.file]
    if getattr(args, "thread", False):
        extra += ["--thread"]
    return x_run(args.action, extra, go=args.go, campaign=args.campaign,
                ledger=_ledger())


def cmd_email(args):
    if args.action == "inbox":
        from channels import email_cdp
        email_cdp.scan()
        return 0
    if args.action == "bounces":
        from channels import email_cdp
        email_cdp.bounces()
        return 0
    # 写动作统一走 SMTP 队列（publish/reply/message 同一管道，campaign 区分）
    from channels.email_smtp import send_queue
    res = send_queue(args.queue, go=args.go, only=args.only, campaign=args.campaign,
                     ledger=_ledger(), throttle=_throttle())
    failed = sum(1 for r in res if r["result"] in ("failed", "rejected"))
    return 1 if failed else 0


def cmd_reddit(args):
    from channels import reddit
    if args.action == "search":
        reddit.search(args.query, limit=args.limit)
        return 0
    if args.action == "reply":
        text = Path(args.text_file).read_text(encoding="utf-8").strip() \
            if args.text_file else args.text
        rec = reddit.reply(args.url, text, go=args.go, campaign=args.campaign,
                           ledger=_ledger())
        return 0 if rec["result"] in ("committed", "dry-run") else 1
    if args.action == "publish":
        body = Path(args.body_file).read_text(encoding="utf-8").strip() \
            if args.body_file else (args.body or "")
        rec = reddit.publish(args.sub, args.title, body, go=args.go,
                             campaign=args.campaign, ledger=_ledger())
        return 0 if rec["result"] in ("committed", "dry-run") else 1
    return 2


def cmd_linkedin(args):
    from channels import linkedin
    if args.action == "search":
        linkedin.search(args.keywords, limit=args.limit)
        return 0
    if args.action == "message":
        note = Path(args.note_file).read_text(encoding="utf-8").strip() \
            if args.note_file else args.note
        rec = linkedin.message(args.url, note, go=args.go, campaign=args.campaign,
                               ledger=_ledger())
        return 0 if rec["result"] in ("committed", "dry-run") else 1
    return 2


def build_parser():
    p = argparse.ArgumentParser(prog="mkt", description="自动化营销组件统一入口")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("status", help="账本聚合")
    sp.set_defaults(fn=cmd_status)
    sp = sub.add_parser("check", help="CDP 会话检查")
    sp.set_defaults(fn=cmd_check)

    sp = sub.add_parser("x", help="X 渠道（vendor，PROVEN）")
    sp.add_argument("action", choices=["publish", "reply", "message", "engage", "search", "inbox"])
    sp.add_argument("extra", nargs="*", help="透传 vendor 参数（如搜索词、@handle、tweet url）")
    sp.add_argument("--file", help="publish 内容文件（默认 thread.json）")
    sp.add_argument("--thread", action="store_true")
    sp.add_argument("--go", action="store_true")
    sp.add_argument("--campaign")
    sp.set_defaults(fn=cmd_x)

    sp = sub.add_parser("email", help="Email 渠道（SMTP 写 + CDP 读，PORTED）")
    sp.add_argument("action", choices=["send", "publish", "reply", "message", "inbox", "bounces"])
    sp.add_argument("--queue", help="队列文件（data/queues/<campaign>.json）")
    sp.add_argument("--only", help="只处理指定 id/target")
    sp.add_argument("--go", action="store_true")
    sp.add_argument("--campaign")
    sp.set_defaults(fn=cmd_email)

    sp = sub.add_parser("reddit", help="Reddit 渠道（UNVERIFIED）")
    sp.add_argument("action", choices=["search", "reply", "publish"])
    sp.add_argument("--query"); sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--url", help="reply 目标帖 URL")
    sp.add_argument("--text"); sp.add_argument("--text-file")
    sp.add_argument("--sub"); sp.add_argument("--title")
    sp.add_argument("--body"); sp.add_argument("--body-file")
    sp.add_argument("--go", action="store_true"); sp.add_argument("--campaign")
    sp.set_defaults(fn=cmd_reddit)

    sp = sub.add_parser("linkedin", help="LinkedIn 渠道（UNVERIFIED，半自动）")
    sp.add_argument("action", choices=["search", "message"])
    sp.add_argument("--keywords")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--url", help="目标 profile URL")
    sp.add_argument("--note"); sp.add_argument("--note-file")
    sp.add_argument("--go", action="store_true"); sp.add_argument("--campaign")
    sp.set_defaults(fn=cmd_linkedin)
    return p


def main():
    args = build_parser().parse_args()
    if getattr(args, "action", None) in ("send", "publish", "reply", "message") \
            and args.cmd == "email" and not args.queue:
        raise SystemExit("email 写动作需要 --queue data/queues/<campaign>.json")
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
