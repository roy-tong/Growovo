#!/usr/bin/env python3
"""mkt —— Growovo 执行内核统一入口（架构文档 §1）。

用法:
  mkt <channel> <action> [参数] [--go] [--campaign <id>] [--only <target>] [--adhoc]

统一语义：**写操作默认 dry-run，--go 才实跑**；实跑必须绑定已人审 plan
（data/plans/*.json 的 review.human_approved=true），无 plan 管辖的临时发送需
显式 **--adhoc** 放行（账本记 adhoc=true；MCP 不开放该通道）。
  mkt status                账本聚合（channel/campaign/result）
  mkt check                 CDP 会话与各渠道登录态检查
  mkt demo                  零网络零账号演示（lint→人审→写路径 dry-run→账本）
  mkt doctor [--selftest]   环境体检（--selftest 连跑全部离线单测）
  mkt experiment …          实验引擎：预注册→观察→人判（机器不判因果）
  mkt outcome …             效果归因：事件/计数台账 + 漏斗（口径非因果）

示例:
  python3 mkt.py email message --queue data/queues/er-wave7.json        # dry-run
  python3 mkt.py email message --queue data/queues/er-wave7.json --go   # 实跑
  python3 mkt.py x publish --campaign day3 -- --dry-run 传给 vendor（默认即 dry-run）
  python3 mkt.py reddit search --query "agent observability"
  python3 mkt.py linkedin message --url <profile> --note-file note.txt
"""
import argparse
import json
import os
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


def _report_md(d):
    lines = [f"# 运营报告（近 {d.get('window_hours')}h）", "",
             f"- 全局暂停：{'ON' if d.get('paused') else 'OFF'}",
             f"- 账本记录：{d.get('total_records', 0)}",
             f"- 抑制名单：{d.get('suppress_list_size', 0)} 条", ""]
    rows = d.get("by_channel_result") or []
    if rows:
        lines += ["| 渠道 | 结果 | 条数 |", "|---|---|---|"]
        lines += [f"| {r['channel']} | {r['result']} | {r['count']} |" for r in rows]
        lines.append("")
    attention = d.get("needs_attention") or []
    if attention:
        lines += ["## 需要关注", ""]
        lines += [f"- `{a['ts']}` {a['channel']} → {a['target']}：{a['result']}（{a['detail']}）"
                  for a in attention]
        lines.append("")
    lines.append(f"> {d.get('note', '')}")
    return "\n".join(lines)


def _status_md(d):
    rows = d.get("breakdown") or []
    lines = [f"账本合计 {d.get('total', 0)} 条", ""]
    if rows:
        lines += ["| 渠道 | campaign | 结果 | 条数 |", "|---|---|---|---|"]
        lines += [f"| {r['channel']} | {r['campaign']} | {r['result']} | {r['count']} |"
                  for r in rows]
    else:
        lines.append("（还没有账本记录——先 `mkt demo` 或跑一次队列）")
    return "\n".join(lines)


def cmd_status(args):
    d = _ledger().summary()
    if getattr(args, "format", "json") == "md":
        print(_status_md(d))
    else:
        print(json.dumps(d, ensure_ascii=False, indent=1))


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
        checks = [
            ("X", "https://x.com/home", "/login"),
            ("Gmail", "https://mail.google.com", "accounts.google.com"),
            ("Reddit", "https://old.reddit.com", "login"),
            ("LinkedIn", "https://www.linkedin.com/feed/", "login"),
            ("HN", "https://news.ycombinator.com/submitted", "login"),
        ]
        results = []
        for name, url, bad in checks:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                results.append((name, "OK" if bad not in page.url else "未登录"))
            except Exception:
                results.append((name, "检查失败"))
        print("CDP 会话正常。登录态: " + "；".join(f"{n}={s}" for n, s in results))
        return 0
    finally:
        page.close()


def cmd_pause(args):
    from core.safety import is_paused, pause_file, set_pause
    if args.off:
        set_pause(DATA, False)
        print("已恢复（全局暂停解除）。")
    elif args.on:
        set_pause(DATA, True, reason=args.reason or "")
        print(f"已暂停一切写操作（{pause_file(DATA)}）。恢复: mkt pause --off")
    else:
        p = is_paused(DATA)
        print(f"全局暂停: {'ON — ' + pause_file(DATA).read_text(encoding='utf-8').strip() if p else 'OFF'}")


def cmd_report(args):
    from core.safety import report
    d = report(DATA, _ledger(), hours=args.hours)
    if getattr(args, "format", "json") == "md":
        print(_report_md(d))
    else:
        print(json.dumps(d, ensure_ascii=False, indent=1))


def cmd_contacts(args):
    from core.contacts import Contacts
    c = Contacts(DATA / "contacts.jsonl")
    if args.action == "add":
        rec = c.add(**json.loads(args.json))
        print(json.dumps(rec, ensure_ascii=False, indent=1))
    elif args.action == "find":
        rows = c.find(platform=args.platform, value=args.value, org=args.org,
                      text=args.text, include_dnc=args.all)
        print(f"{len(rows)} 条")
        for r in rows[:50]:
            st = r.get("state", {})
            print(f"  {r['id']}  {r.get('name', '')}  {r.get('org', '')}  "
                  f"tier{r.get('tier', '?')}  dnc={st.get('dnc', False)}  "
                  f"{json.dumps(r.get('platforms', {}), ensure_ascii=False)}")
    elif args.action == "dnc":
        c.mark(args.contact_id, dnc=True)
        print(f"{args.contact_id} 已标记 DNC")
    elif args.action == "mark":
        updates = json.loads(args.json)
        rec = c.mark(args.contact_id, **updates)
        print(json.dumps(rec, ensure_ascii=False, indent=1))
    elif args.action == "import":
        print(json.dumps(c.import_legacy(args.file), ensure_ascii=False))
    return 0


def cmd_demo(args):
    from core.demo import run_demo
    return run_demo(BASE)


def cmd_doctor(args):
    from core.doctor import run_doctor
    return run_doctor(BASE, DATA, selftest=args.selftest)


def _load_plan_arg(args):
    from core.approval import find_plan
    if getattr(args, "file", None):
        path = Path(args.file)
        return json.loads(path.read_text(encoding="utf-8")), path
    pid = getattr(args, "id", None)
    if not pid:
        raise SystemExit("需要 --id <plan_id> 或 --file <plan.json>")
    plan, path = find_plan(DATA, pid)
    if plan is None:
        raise SystemExit(f"找不到计划 {pid!r}（data/plans/）")
    return plan, path


def cmd_plan(args):
    from core import approval
    from core.planlint import lint_plan
    act = args.action

    if act == "new":
        from datetime import date
        if not (args.id and args.goal and args.hypothesis and args.beachhead):
            raise SystemExit("plan new 需要 --id --goal --hypothesis --beachhead")
        pid = args.id
        size = args.size or 10
        channel = args.channel or "email"
        wave_action = args.wave_action or "message"
        queue = args.queue or f"data/queues/{pid}-w1.json"
        plan = {
            "plan_id": pid,
            "goal": args.goal,
            "created": date.today().isoformat(),
            "hypothesis": args.hypothesis,
            "icp": {"org": args.org or "待补", "persona": args.persona or "待补",
                    "beachhead": args.beachhead},
            "audience": {"spec": args.audience or f"待补：{args.beachhead} 的聚集地",
                         "target_count": size, "evidence_grade": args.evidence_grade or "C"},
            "waves": [{"wave": 1, "channel": channel, "action": wave_action, "size": size,
                       "content_policy": args.content_policy or "千人千面/禁模板",
                       "queue": queue}],
            "success_criteria": {"metric": args.metric or "reply_rate",
                                 "target": args.target or "≥5%",
                                 "stop": args.stop or "退信率>5% 即停"},
            "review": {"human_approved": False, "approved_at": None},
        }
        errs = lint_plan(plan)
        if errs:
            print("计划未通过校验：")
            for e in errs:
                print(f"  [ERR] {e}")
            return 1
        path = DATA / "plans" / f"{pid}.json"
        if path.exists() and not args.force:
            raise SystemExit(f"{path} 已存在（加 --force 覆盖）")
        approval.save_plan(plan, path)
        print(f"已写入 {path}")
        print(f"下一步：mkt plan approve {pid} --by <你的名字>")
        return 0

    if act == "lint":
        plan, path = _load_plan_arg(args)
        errs = lint_plan(plan)
        if errs:
            print(f"{path}: {len(errs)} 处错误")
            for e in errs:
                print(f"  [ERR] {e}")
            return 1
        print(f"{path}: PASS（可审批）")
        return 0

    if act == "show":
        plan, path = _load_plan_arg(args)
        print(json.dumps({"path": str(path), "plan": plan,
                          "approval": approval.status(plan)},
                         ensure_ascii=False, indent=1))
        return 0

    if act == "list":
        rows = approval.list_plans(DATA)
        if not rows:
            print("（无计划）")
            return 0
        for plan, path in rows:
            st = approval.status(plan)
            state = "已批准" if st["valid"] else ("未批准" if not st["human_approved"]
                                                 else f"失败:{st['reason'][:30]}")
            print(f"  {plan.get('plan_id'):24} {state:28} {plan.get('goal', '')[:36]}  {path.name}")
        return 0

    if act in ("approve", "revoke"):
        if not args.id:
            raise SystemExit(f"plan {act} 需要 --id")
        if not args.by:
            raise SystemExit(f"plan {act} 需要 --by <你的名字>（无署名的审批不是审批）")
        if act == "approve":
            plan, path, event = approval.approve_plan(
                DATA, args.id, by=args.by, note=args.note or "", days=args.days)
            print(f"已批准 {args.id}（{path}）")
            print(json.dumps(event, ensure_ascii=False, indent=1))
            queue = ((plan.get("waves") or [{}])[0].get("queue")
                     or f"data/queues/{args.id}-w1.json")
            print(f"下一步：mkt review {Path(queue).stem}  或  mkt queue lint {queue}")
            return 0
        plan, event = approval.revoke_plan(DATA, args.id, by=args.by, note=args.note or "")
        print(json.dumps(event, ensure_ascii=False, indent=1))
        return 0

    return 1


def cmd_queue(args):
    from core import queue as queue_mod
    act = args.action
    if act == "lint":
        if not args.path:
            raise SystemExit("queue lint 需要队列文件路径")
        q = queue_mod.load_queue(args.path)
        errs = queue_mod.lint_queue(q, path=args.path, max_len=args.max_len)
        if errs:
            print(f"{args.path}: {len(errs)} 处错误")
            for e in errs:
                print(f"  [ERR] {e}")
            return 1
        print(f"{args.path}: PASS（{len(q.get('items') or [])} 条可发）")
        return 0

    if act == "run":
        if not args.path:
            raise SystemExit("queue run 需要队列文件路径")
        from core.queue_runner import QueueRunError, run_queue
        try:
            results = run_queue(args.path, data_dir=DATA, go=args.go,
                                allow_unverified=args.allow_unverified,
                                only=args.only)
        except QueueRunError as exc:
            raise SystemExit(f"队列拒跑：{exc}")
        tally = {}
        for r in results:
            tally[r.get("result", "?")] = tally.get(r.get("result", "?"), 0) + 1
        print(json.dumps({"queue": args.path, "go": args.go,
                          "results": tally}, ensure_ascii=False, indent=1))
        return 0 if not tally.get("failed") else 1

    if act == "build":
        missing = [k for k in ("campaign", "template", "targets") if not getattr(args, k)]
        if missing:
            raise SystemExit(f"queue build 需要 --campaign --template --targets（缺 {', '.join(missing)}）")
        out = args.out or str(DATA / "queues" / f"{args.campaign}.json")
        q, errs = queue_mod.build_queue(
            template_path=args.template, targets_csv=args.targets,
            campaign=args.campaign, channel=args.channel or "email",
            action=args.wave_action or "message", out_path=out,
            id_prefix=args.id_prefix or "", force=args.force, max_len=args.max_len,
            subject_template=args.subject_template)
        if errs:
            print(f"未生成（{len(errs)} 处需修）：")
            for e in errs:
                print(f"  [ERR] {e}")
            return 1
        print(f"已生成 {out}（{len(q['items'])} 条，已过门禁）")
        print(f"下一步：mkt queue lint {out}")
        return 0

    return 1


def cmd_campaign(args):
    from core import campaign as camp
    cid = args.campaign
    if not cid:
        raise SystemExit("campaign 需要 campaign id（如 askovo-p0-w1）")
    if args.action == "next":
        n = camp.next_step(DATA, _ledger(), cid)
        if args.json:
            print(json.dumps(n, ensure_ascii=False, indent=1))
        else:
            print(f"{n['title']}\n  {n['command']}\n  理由：{n['why']}")
        return 0
    report = camp.collect(DATA, _ledger(), cid)
    report["next"] = camp.next_step(DATA, _ledger(), cid)
    if args.action == "show":
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    fmt = args.format or "md"
    if fmt == "json":
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    md = camp.render_markdown(report)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md + "\n", encoding="utf-8")
        print(f"已写入 {out}")
    else:
        print(md)
    return 0


def cmd_review(args):
    from core import campaign as camp
    if not args.campaign:
        raise SystemExit("review 需要 campaign id")
    md = camp.review_markdown(DATA, _ledger(), args.campaign)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md + "\n", encoding="utf-8")
        print(f"已写入 {out}")
    else:
        print(md)
    return 0


def cmd_ledger(args):
    led = _ledger()
    act = args.action
    if act == "resolve":
        if not args.ref:
            raise SystemExit("ledger resolve 需要 <ref>（账本里的 ref 字段）")
        rec = led.resolve(args.ref, result=args.result, by=args.by, note=args.note)
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        return 0
    rows = led.all() if args.all else led.indeterminate()
    print(f"{len(rows)} 条")
    for r in rows:
        print(f"  {r.get('ts')}  {r.get('ref')}  {r.get('channel')}  {r.get('target')}  "
              f"{r.get('result')}  {r.get('detail', '')[:60]}")
    return 0


def cmd_link(args):
    from core import links as links_mod
    if args.action == "create":
        if not (args.campaign and args.channel and args.url):
            raise SystemExit("link create 需要 --campaign --channel --url")
        rec = links_mod.make_link(DATA, url=args.url, campaign=args.campaign,
                                  channel=args.channel, content=args.content or "",
                                  medium=args.medium or "", term=args.term or "")
        print(rec["tagged_url"])
        if args.json:
            print(json.dumps(rec, ensure_ascii=False, indent=1))
        return 0
    rows = links_mod.list_links(DATA, args.campaign or None)
    print(f"{len(rows)} 条")
    for r in rows:
        print(f"  {r.get('campaign')}  {r.get('channel')}  {r.get('tagged_url')}")
    return 0


def cmd_init(args):
    from core import onboarding
    info = onboarding.init(DATA)
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=1))
    else:
        print(onboarding.render_init(info))
    return 0


def cmd_next(args):
    from core.campaign import next_step
    n = next_step(DATA, _ledger(), args.campaign or None)
    if args.json:
        print(json.dumps(n, ensure_ascii=False, indent=1))
    else:
        print(f"{n['title']}\n  {n['command']}\n  理由：{n['why']}")
    return 0


def cmd_experiment(args):
    from core import experiments
    if args.action in ("start", "obs", "conclude", "stop") and not args.id:
        raise SystemExit(f"experiment {args.action} 需要 --id")
    if args.action == "new":
        if not (args.id and args.hypothesis and args.metric and args.rule
                and args.start and args.end):
            raise SystemExit("experiment new 需要 --id --hypothesis --metric --rule --start --end")
        field = args.field or args.metric
        agg = args.agg
        if args.metric_source == "outcomes" and field.endswith("_rate"):
            agg = "latest"  # 比率指标按最后一次观察聚合，count 只数观察条数
        exp = experiments.new_experiment(
            DATA, exp_id=args.id, hypothesis=args.hypothesis, metric=args.metric,
            decision_rule=args.rule, start=args.start, end=args.end,
            variant=args.variant or "", owner=args.owner or "",
            field=field, agg=agg,
            campaign=args.campaign or "", source=args.metric_source or "")
        print(json.dumps(exp, ensure_ascii=False, indent=1))
        return 0
    if args.action == "start":
        print(json.dumps(experiments.start_experiment(DATA, args.id), ensure_ascii=False, indent=1))
        return 0
    if args.action == "obs":
        if args.from_outcomes:
            exp = experiments.observe_from_outcomes(DATA, args.id)
            print(json.dumps({"exp_id": exp["exp_id"], "observations": len(exp["observations"]),
                              "aggregate": experiments.aggregate(exp),
                              "last": exp["observations"][-1]}, ensure_ascii=False, indent=1))
            return 0
        if not args.note:
            raise SystemExit("experiment obs 需要 --note '观察说明'（或用 --from-outcomes 自动聚合）")
        data = json.loads(args.data) if args.data else {}
        exp = experiments.record_observation(
            DATA, args.id, note=args.note, data=data,
            evidence_ref=args.evidence_ref or "", approximated=args.approximated)
        print(json.dumps({"exp_id": exp["exp_id"], "observations": len(exp["observations"]),
                          "aggregate": experiments.aggregate(exp)}, ensure_ascii=False))
        return 0
    if args.action == "list":
        exp_dir = DATA / "experiments"
        rows = sorted(exp_dir.glob("*.json")) if exp_dir.is_dir() else []
        if not rows:
            print("（无实验）")
        for path in rows:
            exp = json.loads(path.read_text(encoding="utf-8"))
            print(f"  {exp['exp_id']}  {exp['status']:8} 观察x{len(exp['observations'])}  "
                  f"{(exp.get('conclusion') or {}).get('result', '—'):12} {exp['hypothesis'][:40]}")
        return 0
    if args.action == "conclude":
        if not (args.result and args.by):
            raise SystemExit("experiment conclude 需要 --result met|not_met|inconclusive --by '判定人'")
        exp = experiments.conclude(DATA, args.id, result=args.result, decided_by=args.by)
        print(json.dumps(exp["conclusion"], ensure_ascii=False, indent=1))
        return 0
    if args.action == "stop":
        print(json.dumps(experiments.stop_experiment(DATA, args.id, reason=args.reason or ""),
                         ensure_ascii=False, indent=1))
        return 0
    return 1


def cmd_outcome(args):
    from core.attribution import OutcomeLedger
    ledger = OutcomeLedger(DATA)
    act = args.action
    if act == "event":
        if not (args.subject and args.type):
            raise SystemExit("outcome event 需要 --subject <主体> --type signal|signup|activation|paid")
        if args.type in ("activation", "paid") and not (args.verify_method and args.verify_ref):
            raise SystemExit(f"outcome event {args.type} 需要 --verify-method 与 --verify-ref"
                             "（价值事件必须带核验状态）")
        verification = None
        if args.verify_method and args.verify_ref:
            verification = {"method": args.verify_method, "ref": args.verify_ref}
        rec = ledger.add_event(subject=args.subject, event_type=args.type,
                               channel=args.channel or "", touch=args.touch or "",
                               source_ref=args.source_ref or "", approximated=args.approximated,
                               verification=verification, campaign=args.campaign or "")
        print(json.dumps(rec, ensure_ascii=False))
        return 0
    if act == "count":
        if not (args.metric and args.value is not None and args.window and args.source_ref):
            raise SystemExit("outcome count 需要 --metric --value N --window --source-ref")
        rec = ledger.add_count(metric=args.metric, value=args.value, window=args.window,
                               source_ref=args.source_ref, approximated=True,
                               campaign=args.campaign or "")
        print(json.dumps(rec, ensure_ascii=False))
        return 0
    if act == "import":
        from core.outcome_io import import_csv
        path = args.csv or args.file
        if not path:
            raise SystemExit("outcome import 需要 --csv <文件.csv>")
        mapping = {}
        for pair in (args.event_map or "").split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                mapping[k.strip()] = v.strip()
        res = import_csv(DATA, path, campaign=args.campaign or "",
                         dry_run=args.dry_run, event_map=mapping)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 1 if res["errors"] else 0
    if act == "sync":
        if not args.campaign:
            raise SystemExit("outcome sync 需要 --campaign（把账本聚合成 count 快照）")
        from core.outcome_io import sync_ledger
        res = sync_ledger(DATA, _ledger(), args.campaign, dry_run=args.dry_run)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    if act == "funnel":
        print(json.dumps(ledger.funnel(args.campaign or None), ensure_ascii=False, indent=1))
        return 0
    if act == "attribution":
        print(json.dumps(ledger.attribution(args.campaign or None), ensure_ascii=False, indent=1))
        return 0
    return 1


def cmd_x(args):
    from channels.x.adapter import run as x_run
    extra = list(args.extra or [])
    if getattr(args, "file", None):
        extra += ["--file", args.file]
    if getattr(args, "thread", False):
        extra += ["--thread"]
    return x_run(args.action, extra, go=args.go, adhoc=args.adhoc,
                campaign=args.campaign, ledger=_ledger())


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
    res = send_queue(args.queue, go=args.go, adhoc=args.adhoc, only=args.only,
                     campaign=args.campaign, data_dir=DATA,
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
        rec = reddit.reply(args.url, text, go=args.go, adhoc=args.adhoc,
                           campaign=args.campaign, ledger=_ledger())
        return 0 if rec["result"] in ("committed", "dry-run") else 1
    if args.action == "publish":
        body = Path(args.body_file).read_text(encoding="utf-8").strip() \
            if args.body_file else (args.body or "")
        rec = reddit.publish(args.sub, args.title, body, go=args.go, adhoc=args.adhoc,
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
        rec = linkedin.message(args.url, note, go=args.go, adhoc=args.adhoc,
                               campaign=args.campaign, ledger=_ledger())
        return 0 if rec["result"] in ("committed", "dry-run") else 1
    return 2


def cmd_github(args):
    from channels import github
    if args.action == "audit":
        github.audit(args.repo)
        return 0
    if args.action == "search":
        github.search(args.query, limit=args.limit)
        return 0
    if args.action == "inbox":
        github.inbox(args.repo)
        return 0
    if args.action == "reply":
        text = Path(args.text_file).read_text(encoding="utf-8").strip() \
            if args.text_file else args.text
        rec = github.reply(args.repo, args.number, text, go=args.go, adhoc=args.adhoc,
                           campaign=args.campaign, ledger=_ledger())
        detail = rec.get("detail", "")
        print(f"github reply -> {rec['result']}"
              + (f"（{detail}）" if detail else "")
              + (f"  evidence: {rec.get('evidence')}" if rec.get("evidence") else ""))
        return 0 if rec["result"] in ("committed", "dry-run") else 1
    return 2


def cmd_hn(args):
    from channels import hn
    if args.action == "search":
        hn.search(args.query, limit=args.limit)
        return 0
    if args.action == "inbox":
        hn.inbox()
        return 0
    text = Path(args.text_file).read_text(encoding="utf-8").strip() \
        if args.text_file else (args.text or "")
    if args.action == "publish":
        rec = hn.publish(args.title, url=args.url, text=text or None,
                         go=args.go, adhoc=args.adhoc, campaign=args.campaign,
                         ledger=_ledger())
    else:  # reply
        rec = hn.reply(args.item, text, go=args.go, adhoc=args.adhoc,
                       campaign=args.campaign, ledger=_ledger())
    detail = rec.get("detail", "")
    print(f"hn {args.action} -> {rec['result']}"
          + (f"（{detail}）" if detail else "")
          + (f"  evidence: {rec.get('evidence')}" if rec.get("evidence") else ""))
    return 0 if rec["result"] in ("committed", "dry-run") else 1


def cmd_memory(args):
    """Read-only, privacy-safe patterns from execution receipts (never auto-replay)."""
    from core.operations_memory import summarize
    result = summarize(_ledger().all(), campaign=args.campaign,
                       channel=args.channel, minimum_confirmed=args.minimum_confirmed)
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() and not args.force:
            print(f"输出已存在: {output}（加 --force 才覆盖）")
            return 1
        output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


def cmd_playbook(args):
    from core import playbooks
    if args.name != "github-inbox":
        raise SystemExit(f"未知 playbook: {args.name}")
    if args.input:
        rows = json.loads(Path(args.input).read_text(encoding="utf-8"))
    else:
        from channels import github
        rows = github.inbox(args.repo)
    campaign = args.campaign or ("github-inbox-" + args.repo.replace("/", "-"))
    plan = playbooks.triage_github_inbox(rows, args.repo)
    plan, plan_file = playbooks.save_plan(DATA, plan, campaign=campaign)
    result = {"plan": str(plan_file), "counts": plan.get("counts", {})}
    if args.drafts:
        drafts = playbooks.load_drafts(args.drafts)
        out = Path(args.out or (DATA / "queues" / f"{campaign}.json"))
        queue, errs, meta = playbooks.build_reply_queue(
            plan, drafts, out, force=args.force)
        result.update({"queue": str(out), "queue_errors": errs,
                       **meta})
        if errs:
            print(json.dumps(result, ensure_ascii=False, indent=1))
            return 1
        print(f"已生成 {out}（{meta.get('items', 0)} 条候选回复）")
        print(f"下一步：mkt queue lint {out}；mkt plan approve <plan_id> --by <人>；"
              f"mkt queue run {out}（先 dry-run，再 --go）")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(playbooks.render_markdown(plan))
        print(f"计划：{plan_file}")
    return 0


def cmd_action(args):
    from core.action_runner import ActionError, run_action
    text = Path(args.text_file).read_text(encoding="utf-8").strip() \
        if args.text_file else args.text
    try:
        rec = run_action(
            channel=args.channel, action=args.action, data_dir=DATA,
            campaign=args.campaign or "", go=args.go,
            allow_unverified=args.allow_unverified, target=args.target,
            text=text, title=args.title, url=args.url,
            subreddit=args.subreddit, number=args.number,
            extra=args.extra)
    except ActionError as exc:
        raise SystemExit(f"动作拒跑：{exc}")
    print(json.dumps(rec, ensure_ascii=False, indent=1))
    return 0 if rec.get("result") in ("committed", "dry-run") else 1


def cmd_collect(args):
    from core import collector
    act = args.action
    if act == "serve":
        token = args.token or os.environ.get("MKT_COLLECT_TOKEN", "")
        return collector.serve(DATA, host=args.host, port=args.port, token=token)
    if act == "event":
        if not args.json:
            raise SystemExit("collect event 需要 --json '{\"subject\":…}'")
        try:
            rec = collector.ingest(DATA, json.loads(args.json),
                                   token=args.token,
                                   expected_token=os.environ.get("MKT_COLLECT_TOKEN", ""))
        except collector.CollectorError as exc:
            raise SystemExit(f"collect 拒收：{exc}")
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        return 0
    if act == "import":
        if not (args.source and args.csv):
            raise SystemExit("collect import 需要 --source plausible|posthog --csv <文件>")
        if args.source == "plausible":
            res = collector.import_plausible(DATA, args.csv, campaign=args.campaign or "",
                                             dry_run=args.dry_run)
        else:
            from core.outcome_io import import_csv
            mapping = {}
            for pair in (args.event_map or "").split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    mapping[k.strip()] = v.strip()
            res = import_csv(DATA, args.csv, campaign=args.campaign or "",
                             dry_run=args.dry_run, event_map=mapping)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 1 if res.get("errors") else 0
    return 1


def build_parser():
    p = argparse.ArgumentParser(prog="mkt", description="Growovo 执行内核统一入口")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("status", help="账本聚合")
    sp.add_argument("--format", choices=["json", "md"], default="json",
                    help="md = 给人看的表格")
    sp.set_defaults(fn=cmd_status)
    sp = sub.add_parser("check", help="CDP 会话检查（X/Gmail/Reddit/LinkedIn/HN 登录态）")
    sp.set_defaults(fn=cmd_check)
    sp = sub.add_parser("pause", help="全局暂停总闸（一切写操作拒跑）")
    sp.add_argument("--on", action="store_true", help="暂停（可 --reason '原因'）")
    sp.add_argument("--off", action="store_true", help="恢复")
    sp.add_argument("--reason", default="")
    sp.set_defaults(fn=cmd_pause)
    sp = sub.add_parser("report", help="一页纸运营报告（近 24h 账本/异常/暂停态）")
    sp.add_argument("--hours", type=int, default=24)
    sp.add_argument("--format", choices=["json", "md"], default="json",
                    help="md = 给人看的报告")
    sp.set_defaults(fn=cmd_report)
    sp = sub.add_parser("demo", help="零网络零账号演示：计划校验→人审闸门→写路径 dry-run→账本")
    sp.set_defaults(fn=cmd_demo)
    sp = sub.add_parser("doctor", help="环境体检：内核/数据/凭据/CDP/暂停态（--selftest 连跑单测）")
    sp.add_argument("--selftest", action="store_true", help="附带跑全部离线单测")
    sp.set_defaults(fn=cmd_doctor)

    sp = sub.add_parser("plan", help="计划：新建/校验/查看/列表/审批/撤销")
    sp.add_argument("action", choices=["new", "lint", "show", "list", "approve", "revoke"])
    sp.add_argument("--id", help="plan_id")
    sp.add_argument("--file", help="lint/show 直接指定计划文件")
    sp.add_argument("--goal", help="new：一句话目标（如「拿到 20 个激活」）")
    sp.add_argument("--hypothesis", help="new：可证伪假设")
    sp.add_argument("--beachhead", help="new：单一 beachhead（不铺多线）")
    sp.add_argument("--org"); sp.add_argument("--persona"); sp.add_argument("--audience")
    sp.add_argument("--channel", default="email")
    sp.add_argument("--wave-action", dest="wave_action", default="message")
    sp.add_argument("--size", type=int, default=10)
    sp.add_argument("--queue", help="new：队列路径（默认 data/queues/<id>-w1.json）")
    sp.add_argument("--metric"); sp.add_argument("--target"); sp.add_argument("--stop")
    sp.add_argument("--content-policy", dest="content_policy")
    sp.add_argument("--evidence-grade", dest="evidence_grade")
    sp.add_argument("--by", help="approve/revoke：审批人署名")
    sp.add_argument("--note"); sp.add_argument("--days", type=int, help="批准有效期（天）")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_plan)

    sp = sub.add_parser("queue", help="队列：编辑期门禁（lint）与模板生成（build）")
    sp.add_argument("action", choices=["lint", "build", "run"])
    sp.add_argument("path", nargs="?", help="lint/run：队列文件路径")
    sp.add_argument("--campaign"); sp.add_argument("--channel", default="email")
    sp.add_argument("--wave-action", dest="wave_action", default="message")
    sp.add_argument("--template", help="build：正文模板（{列名} 变量）")
    sp.add_argument("--subject-template", dest="subject_template",
                    help="build：邮件主题模板（可选，同样支持 {列名}）")
    sp.add_argument("--targets", help="build：目标 CSV（target/email + 变量列）")
    sp.add_argument("--out", help="build：输出路径（默认 data/queues/<campaign>.json）")
    sp.add_argument("--id-prefix", dest="id_prefix")
    sp.add_argument("--max-len", dest="max_len", type=int)
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--go", action="store_true", help="run：实跑（须已人审 plan）")
    sp.add_argument("--only", help="run：只跑指定 id/target")
    sp.add_argument("--allow-unverified", dest="allow_unverified", action="store_true",
                    help="run：UNVERIFIED 渠道实跑（首跑人工在场）")
    sp.set_defaults(fn=cmd_queue)

    sp = sub.add_parser("action", help="单条写动作（与队列同一套门禁/审批/账本）")
    sp.add_argument("channel", choices=["x", "reddit", "linkedin", "github", "hn"])
    sp.add_argument("action", choices=["publish", "reply", "message", "engage"])
    sp.add_argument("--target", help="reply/message 的目标（URL / issue:o/r#N）")
    sp.add_argument("--text"); sp.add_argument("--text-file")
    sp.add_argument("--title"); sp.add_argument("--url")
    sp.add_argument("--subreddit"); sp.add_argument("--number", type=int)
    sp.add_argument("--campaign")
    sp.add_argument("--go", action="store_true")
    sp.add_argument("--allow-unverified", dest="allow_unverified", action="store_true")
    sp.add_argument("--extra", nargs="*", help="透传 vendor（x message/engage）")
    sp.set_defaults(fn=cmd_action)

    sp = sub.add_parser("collect", help="本地 collector：链接跳转/事件 webhook/CSV 导入")
    sp.add_argument("action", choices=["serve", "event", "import"])
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8787)
    sp.add_argument("--token", help="serve：Bearer token（或 MKT_COLLECT_TOKEN）")
    sp.add_argument("--json", help="event：事件 JSON")
    sp.add_argument("--source", choices=["plausible", "posthog"], help="import：数据源")
    sp.add_argument("--csv", help="import：CSV 文件")
    sp.add_argument("--campaign")
    sp.add_argument("--event-map", dest="event_map",
                    help="posthog：事件名映射，如 user_signed_up=signup,activated=activation")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true")
    sp.set_defaults(fn=cmd_collect)

    sp = sub.add_parser("campaign", help="Campaign 视图：计划/队列/账本/结果合一")
    sp.add_argument("action", choices=["show", "report", "next"])
    sp.add_argument("campaign")
    sp.add_argument("--out", help="report：写入文件（如 reports/<campaign>.md）")
    sp.add_argument("--format", choices=["json", "md"], default=None)
    sp.add_argument("--json", action="store_true", help="next：输出 JSON")
    sp.set_defaults(fn=cmd_campaign)

    sp = sub.add_parser("review", help="人审预览：队列逐条过门禁 + 已有留证")
    sp.add_argument("campaign")
    sp.add_argument("--out")
    sp.set_defaults(fn=cmd_review)

    sp = sub.add_parser("ledger", help="账本：indeterminate 人工裁决")
    sp.add_argument("action", choices=["resolve", "list"])
    sp.add_argument("ref", nargs="?", help="resolve：账本 ref")
    sp.add_argument("--result", choices=["committed", "failed", "suppressed", "bounced", "no-dm"])
    sp.add_argument("--by"); sp.add_argument("--note")
    sp.add_argument("--all", action="store_true", help="list：全部记录（默认只看 indeterminate）")
    sp.set_defaults(fn=cmd_ledger)

    sp = sub.add_parser("link", help="追踪链接：生成带 utm 的链接并落盘")
    sp.add_argument("action", choices=["create", "list"])
    sp.add_argument("--campaign"); sp.add_argument("--channel"); sp.add_argument("--url")
    sp.add_argument("--content"); sp.add_argument("--medium"); sp.add_argument("--term")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_link)

    sp = sub.add_parser("init", help="初始化工作区：建目录/报凭据缺口/给下一步")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_init)

    sp = sub.add_parser("next", help="现在该做什么（状态机）")
    sp.add_argument("--campaign"); sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_next)
    sp = sub.add_parser("contacts", help="线索库：add/find/dnc/mark/import")
    sp.add_argument("action", choices=["add", "find", "dnc", "mark", "import"])
    sp.add_argument("--json", help="add/mark 的 JSON 字段")
    sp.add_argument("--platform"); sp.add_argument("--value")
    sp.add_argument("--org"); sp.add_argument("--text")
    sp.add_argument("--all", action="store_true", help="find 含 DNC")
    sp.add_argument("--contact-id")
    sp.add_argument("--file", help="import 旧格式文件（er-batch）")
    sp.set_defaults(fn=cmd_contacts)

    sp = sub.add_parser("x", help="X 渠道（vendor，PROVEN）")
    sp.add_argument("action", choices=["publish", "reply", "message", "engage", "search", "inbox"])
    sp.add_argument("extra", nargs="*", help="透传 vendor 参数（如搜索词、@handle、tweet url）")
    sp.add_argument("--file", help="publish 内容文件（默认 thread.json）")
    sp.add_argument("--thread", action="store_true")
    sp.add_argument("--go", action="store_true")
    sp.add_argument("--campaign")
    sp.add_argument("--adhoc", action="store_true",
                       help="实跑无 plan 管辖时的显式放行（账本记 adhoc=true）")
    sp.set_defaults(fn=cmd_x)

    sp = sub.add_parser("email", help="Email 渠道（SMTP 写 + CDP 读，PORTED）")
    sp.add_argument("action", choices=["send", "publish", "reply", "message", "inbox", "bounces"])
    sp.add_argument("--queue", help="队列文件（data/queues/<campaign>.json）")
    sp.add_argument("--only", help="只处理指定 id/target")
    sp.add_argument("--go", action="store_true")
    sp.add_argument("--campaign")
    sp.add_argument("--adhoc", action="store_true",
                       help="实跑无 plan 管辖时的显式放行（账本记 adhoc=true）")
    sp.set_defaults(fn=cmd_email)

    sp = sub.add_parser("reddit", help="Reddit 渠道（UNVERIFIED）")
    sp.add_argument("action", choices=["search", "reply", "publish"])
    sp.add_argument("--query"); sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--url", help="reply 目标帖 URL")
    sp.add_argument("--text"); sp.add_argument("--text-file")
    sp.add_argument("--sub"); sp.add_argument("--title")
    sp.add_argument("--body"); sp.add_argument("--body-file")
    sp.add_argument("--go", action="store_true"); sp.add_argument("--campaign")
    sp.add_argument("--adhoc", action="store_true",
                       help="实跑无 plan 管辖时的显式放行（账本记 adhoc=true）")
    sp.set_defaults(fn=cmd_reddit)

    sp = sub.add_parser("linkedin", help="LinkedIn 渠道（UNVERIFIED，半自动）")
    sp.add_argument("action", choices=["search", "message"])
    sp.add_argument("--keywords")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--url", help="目标 profile URL")
    sp.add_argument("--note"); sp.add_argument("--note-file")
    sp.add_argument("--go", action="store_true"); sp.add_argument("--campaign")
    sp.add_argument("--adhoc", action="store_true",
                       help="实跑无 plan 管辖时的显式放行（账本记 adhoc=true）")
    sp.set_defaults(fn=cmd_linkedin)

    sp = sub.add_parser("github", help="GitHub 渠道（api 通道；读=PROVEN，写=UNVERIFIED）")
    sp.add_argument("action", choices=["audit", "search", "inbox", "reply"])
    sp.add_argument("--repo", help="owner/repo（audit/inbox/reply 目标，reply 配 --number）")
    sp.add_argument("--query"); sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--number", type=int, help="reply 目标 issue/PR 编号")
    sp.add_argument("--text"); sp.add_argument("--text-file")
    sp.add_argument("--go", action="store_true"); sp.add_argument("--campaign")
    sp.add_argument("--adhoc", action="store_true",
                       help="实跑无 plan 管辖时的显式放行（账本记 adhoc=true）")
    sp.set_defaults(fn=cmd_github)

    sp = sub.add_parser("memory", help="从真实操作回执沉淀匿名化运营经验（只读，不自动重放）")
    sp.add_argument("--campaign", help="只分析指定 campaign")
    sp.add_argument("--channel", help="只分析指定平台")
    sp.add_argument("--minimum-confirmed", type=int, default=3,
                    help="推荐人工复盘的最低已确认操作数量")
    sp.add_argument("--output", help="可选：导出不含个人目标信息的 JSON")
    sp.add_argument("--force", action="store_true", help="允许覆盖已有导出")
    sp.set_defaults(fn=cmd_memory)

    sp = sub.add_parser("playbook", help="Playbook：把日常运营拆成分流→草稿→队列→审批执行")
    sp.add_argument("name", choices=["github-inbox"])
    sp.add_argument("--repo", required=True, help="owner/repo（必须有读权限）")
    sp.add_argument("--campaign", help="计划/队列归属（默认 github-inbox-<owner>-<repo>）")
    sp.add_argument("--drafts", help="候选回复 JSON：{id|number: body} 或 {drafts:[...]}")
    sp.add_argument("--input", help="离线 JSON 行（测试/导出后复盘；不联网）")
    sp.add_argument("--out", help="队列输出路径（默认 data/queues/<campaign>.json）")
    sp.add_argument("--force", action="store_true", help="覆盖已有队列")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_playbook)

    sp = sub.add_parser("hn", help="HN 渠道（CDP 写 + Algolia 读，UNVERIFIED）")
    sp.add_argument("action", choices=["search", "publish", "reply", "inbox"])
    sp.add_argument("--query"); sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--title", help="publish 标题（≤80 字符；Show HN 前缀自有项目专用）")
    sp.add_argument("--url", help="publish 链接帖 URL（与 --text 二选一）")
    sp.add_argument("--item", help="reply 目标：item:N 或 news.ycombinator.com/item?id=N")
    sp.add_argument("--text"); sp.add_argument("--text-file")
    sp.add_argument("--go", action="store_true"); sp.add_argument("--campaign")
    sp.add_argument("--adhoc", action="store_true",
                       help="实跑无 plan 管辖时的显式放行（账本记 adhoc=true）")
    sp.set_defaults(fn=cmd_hn)

    sp = sub.add_parser("experiment", help="实验引擎：预注册→观察→人判（机器不判因果）")
    sp.add_argument("action", choices=["new", "start", "obs", "list", "conclude", "stop"])
    sp.add_argument("--id", help="实验 id（小写/数字/连字符）")
    sp.add_argument("--hypothesis"); sp.add_argument("--metric")
    sp.add_argument("--rule", help="预注册判定规则（启动前必填）")
    sp.add_argument("--start"); sp.add_argument("--end")
    sp.add_argument("--variant"); sp.add_argument("--owner")
    sp.add_argument("--field", default="", help="默认取 metric 名")
    sp.add_argument("--agg", default="count", choices=["count", "sum", "latest"])
    sp.add_argument("--note", help="obs：观察说明（必填）")
    sp.add_argument("--data", help="obs：JSON 数据，如 '{\"value\": 3}'")
    sp.add_argument("--evidence-ref", default="")
    sp.add_argument("--approximated", action="store_true", help="obs：无出处数据显式标近似")
    sp.add_argument("--campaign", help="实验归属的 campaign（供 campaign 视图关联）")
    sp.add_argument("--metric-source", dest="metric_source", choices=["", "outcomes"],
                    default="", help="new：指标来源；outcomes = 从事件台账自动聚合")
    sp.add_argument("--from-outcomes", dest="from_outcomes", action="store_true",
                    help="obs：从 outcome 事件台账自动生成观察值")
    sp.add_argument("--result", choices=["met", "not_met", "inconclusive"])
    sp.add_argument("--by", help="conclude：判定人署名（必填）")
    sp.add_argument("--reason", help="stop：原因")
    sp.set_defaults(fn=cmd_experiment)

    sp = sub.add_parser("outcome", help="效果归因：事件/计数台账 + 漏斗 + 记账口径（非因果）")
    sp.add_argument("action", choices=["event", "count", "import", "sync", "funnel", "attribution"])
    sp.add_argument("--subject", help="event：主体标识（如访客 id / cohort 名）")
    sp.add_argument("--type", choices=["signal", "signup", "activation", "paid"])
    sp.add_argument("--channel"); sp.add_argument("--touch")
    sp.add_argument("--source-ref", default="")
    sp.add_argument("--approximated", action="store_true",
                   help="event：signup 近似口径（activation/paid 禁止近似）")
    sp.add_argument("--verify-method", default="",
                   help="event：核验方式（activation/paid 必填，如 payment-processor/产品后台）")
    sp.add_argument("--verify-ref", default="",
                   help="event：核验依据（activation/paid 必填，如订单号/后台记录 id）")
    sp.add_argument("--metric", help="count：指标名")
    sp.add_argument("--value", type=int, help="count：非负整数")
    sp.add_argument("--window", help="count：统计窗口")
    sp.add_argument("--csv", help="import：事件 CSV（subject,event_type + 可选列）")
    sp.add_argument("--event-map", dest="event_map",
                    help="import：原始事件名 → 漏斗阶段，如 user_signed_up=signup")
    sp.add_argument("--campaign", help="事件/计数/导入/漏斗的 campaign 归属")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="import/sync：只报告不写盘")
    sp.set_defaults(fn=cmd_outcome)
    return p


def _extract_workspace(argv):
    """全局 --workspace（可放在任意位置，argparse 子命令里不重复声明）。"""
    out, ws = [], None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--workspace":
            if i + 1 >= len(argv):
                raise SystemExit("--workspace 需要目录路径")
            ws = argv[i + 1]
            i += 2
            continue
        if a.startswith("--workspace="):
            ws = a.split("=", 1)[1]
            i += 1
            continue
        out.append(a)
        i += 1
    return out, ws


def main(argv=None):
    global DATA
    from core import workspace
    argv = list(sys.argv[1:] if argv is None else argv)
    argv, ws = _extract_workspace(argv)
    DATA = workspace.resolve(ws, base=BASE)
    args = build_parser().parse_args(argv)
    if getattr(args, "action", None) in ("send", "publish", "reply", "message") \
            and args.cmd == "email" and not args.queue:
        raise SystemExit("email 写动作需要 --queue data/queues/<campaign>.json")
    if getattr(args, "action", None) == "add" and args.cmd == "contacts" and not args.json:
        raise SystemExit("contacts add 需要 --json '{\"name\":…, \"platforms\":{…}}'")
    if getattr(args, "action", None) in ("dnc", "mark") and args.cmd == "contacts" \
            and not args.contact_id:
        raise SystemExit(f"contacts {args.action} 需要 --contact-id")
    if getattr(args, "action", None) in ("audit", "inbox") and args.cmd == "github" \
            and not args.repo:
        raise SystemExit(f"github {args.action} 需要 --repo owner/repo")
    if getattr(args, "action", None) == "search" and args.cmd == "github" and not args.query:
        raise SystemExit("github search 需要 --query '关键词'")
    if getattr(args, "action", None) == "reply" and args.cmd == "github" \
            and not (args.repo and args.number and (args.text or args.text_file)):
        raise SystemExit("github reply 需要 --repo owner/repo --number N --text '…'（或 --text-file）")
    if getattr(args, "action", None) == "search" and args.cmd == "hn" and not args.query:
        raise SystemExit("hn search 需要 --query '关键词'")
    if getattr(args, "action", None) == "publish" and args.cmd == "hn" \
            and not (args.title and (args.url or args.text or args.text_file)):
        raise SystemExit("hn publish 需要 --title '…' 且 --url 与 --text/--text-file 二选一")
    if getattr(args, "action", None) == "reply" and args.cmd == "hn" \
            and not (args.item and (args.text or args.text_file)):
        raise SystemExit("hn reply 需要 --item <url|item:N> --text '…'（或 --text-file）")
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
