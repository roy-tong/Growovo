#!/usr/bin/env python3
"""mkt —— Growovo 执行内核统一入口（架构文档 §1）。

用法:
  mkt <channel> <action> [参数] [--go] [--campaign <id>] [--only <target>] [--adhoc]

统一语义：**写操作默认 dry-run，--go 才实跑**；实跑须绑定已人审 plan，或显式 --routine
且工作区 routine policy 覆盖该账号/渠道/动作。无 plan 管辖的临时发送仍可显式
**--adhoc** 放行（账本记 adhoc=true；MCP 不开放该通道，也不开放授权管理）。
  mkt status                账本聚合（channel/campaign/result）
  mkt check                 CDP 会话与各渠道登录态检查
  mkt demo                  零网络零账号演示（lint→人审→写路径 dry-run→账本）
  mkt work-demo             GTM Work OS 全闭环演示（不执行队列）
  mkt orchestrate …         按产品/目标/任务选最小岗位与表达方式
  mkt policy …              日常营销常驻授权（不开放付款等高风险动作）
  mkt capability …          18 能力域 / 40 岗位族注册表
  mkt template …            32 个交付物模板与实例校验
  mkt context/objective/work …  不可变上下文、目标与 WorkOrder
  mkt sop/deliverable/handoff …  SOP、交付验收与跨岗位交接
  mkt metric …              版本化 KPI 字典与确定性测量
  mkt work-action …         accepted 交付物→既有 plan/queue（不发送）
  mkt doctor [--selftest]   环境体检（--selftest 连跑全部离线单测）
  mkt experiment …          实验引擎：预注册→观察→人判（机器不判因果）
  mkt workflow …            可复用工作流：learn/approve/replay/runs/reconcile（版本化 + 指纹 + 运行日志）
  mkt scorecard             运营记分卡：回执与业务结果的描述性口径
  mkt gtm diagnose --facts … GTM 诊断：产品事实包→平台优先级+冷启动+放量判据（证据标签强制）
  mkt gtm design "…"      GTM 设计：对象×目标×机制×渠道 + 结果契约（只设计，不执行）
  mkt gtm handoff <json>  设计 → 候选岗位编排 + 目标契约映射（可选 --product）
  mkt optimize              优化建议（需人工决定，不自动改预算或重放）
  mkt schedule …            持久化排期：add/list/tick/status/resume
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
    from core.followups import Followups
    d = report(DATA, _ledger(), hours=args.hours)
    fu = Followups(DATA / "followups.jsonl")
    d["followups"] = fu.stats()
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



def cmd_followup(args):
    from core.followups import Followups, describe
    fu = Followups(DATA / "followups.jsonl")
    act = args.action
    if act == "add":
        rec = fu.add(args.platform, args.target, source=args.source,
                     note=args.note, on=args.on or None, channel=args.channel,
                     campaign=getattr(args, "campaign", ""))
        print(describe(rec))
        print(f"  下次跟进：{rec['next_due']}")
    elif act == "due":
        rows = fu.due(args.on or None)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
            return 0
        if not rows:
            print("今天没有该跟的人。")
            return 0
        print(f"今天该跟 {len(rows)} 个：")
        for r in rows:
            print("  " + describe(r))
    elif act == "list":
        rows = fu.all()
        if args.stage:
            rows = [r for r in rows if r["stage"] == args.stage]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
            return 0
        print(f"{len(rows)} 个对象")
        for r in rows:
            print("  " + describe(r))
    elif act == "touch":
        rec = fu.touch(args.id, args.result, note=args.note,
                       channel=args.channel, on=args.on or None,
                       campaign=getattr(args, "campaign", ""))
        print(describe(rec))
        if rec.get("next_due"):
            print(f"  下次跟进：{rec['next_due']}")
        else:
            print(f"  已关闭：{rec.get('closed_reason', '')}")
    elif act == "stats":
        st = fu.stats(args.on or None)
        if args.json:
            print(json.dumps(st, ensure_ascii=False, indent=1))
            return 0
        print(f"对象 {st['total']}｜进行中 {st['open']}｜今天到期 {st['due']}｜逾期 {st['overdue']}")
        print(f"有实质回应 {st['substantive']}｜45 天后仍在对话 {st['conversations_after_45d']}")
        for row in fu.platform_breakdown():
            print(f"  {row['platform']:<10} 共 {row['total']:>2}｜进行中 {row['open']:>2}｜实质 {row['substantive']}")
    elif act == "close":
        rec = fu.close(args.id, args.reason)
        print(f"{rec['id']} 已关闭：{rec['closed_reason']}")
    return 0


def cmd_intent(args):
    from core.intent import IntentCampaign
    act = args.action
    path = DATA / "intent" / f"{args.id}.json"
    if act == "start":
        subs = [x.strip() for x in (args.subs or "").split(",") if x.strip()]
        data = IntentCampaign.start(path, args.id, platform=args.platform or "reddit",
                                    subs=subs, started=args.on or None, owner=args.owner or "",
                                    weeks=getattr(args, "weeks", 4),
                                    weekly_target=getattr(args, "weekly_target", None))
        print(f"战术已建：{data['id']}（{data['started']} → {data['ends']}）")
        print(f"  {data['platform']}｜目标 sub：{', '.join(data['subs']) or '（未填）'}")
        return 0
    c = IntentCampaign(path)
    if act == "log":
        d = c.log(sent=args.sent or 0, deleted=args.deleted or 0,
                  replied=args.replied or 0, moved=args.moved or 0,
                  note=args.note or "", on=args.on or None)
        s = c.summary(args.on or None)
        print(f"已记录（{args.id}）：累计 {s['sent']} 条触达，回应率 {s['response_rate']:.0%}")
        return 0
    if act == "show":
        s = c.summary(args.on or None)
        print(f"{args.id}｜第 {s['week']} 周·{s['phase']}：{s['todo']}")
        print(f"  触达 {s['sent']}｜被删 {s['deleted']}（{s['deleted_rate']:.0%}）"
              f"｜回应 {s['replied']}（{s['response_rate']:.0%}）｜转私信 {s['moved']}")
        if s["weeks_behind_quota"]:
            print(f"  ⚠ 第 {'、'.join(map(str, s['weeks_behind_quota']))} 周未达每周 20 条配额")
        return 0
    if act == "verdict":
        v = c.verdict(args.on or None)
        print(f"判定：{v['verdict']}")
        print(f"  {v['reason']}")
        print(f"  数据：触达 {v['sent']}｜被删率 {v['deleted_rate']:.0%}"
              f"｜回应率 {v['response_rate']:.0%}｜转私信 {v['moved']}")
        return 0
    return 0



def cmd_retro(args):
    from core import logscan, retro, narrative
    act = args.action

    if act == "scan":
        root = Path(args.path).expanduser()
        if not root.exists():
            print(f"路径不存在：{root}", file=sys.stderr)
            return 2
        out = Path(args.out) if args.out else (DATA / "retro" / "scan.json")
        res = logscan.scan(root, out, max_files=args.max_files)
        inv = res["inventory"]
        print(f"扫描 {inv['files']} 个文件（{inv['bytes'] / 1024:.0f} KB），"
              f"{res['files_with_claims']} 个有可用事实，共提取 {res['claims']} 条")
        print(f"  已保存：{res['saved']}")
        return 0

    src = Path(args.scan) if getattr(args, "scan", "") else (DATA / "retro" / "scan.json")

    if act == "analyze":
        if not src.exists():
            print("还没有扫描结果，先跑 mkt retro scan --path <目录>", file=sys.stderr)
            return 2
        data = logscan.load(src)
        pool = retro.evidence_pool(data["claims"])
        print("日志里在说的那些话（同一句重复出现越多次，越可能是关键事实）\n")
        for row in pool[:8]:
            print(f"【{row['name']}】")
            for it in row["items"]:
                name = retro.METRIC_NAMES.get(it["metric"], it["metric"])
                mark = f" ×{it['support']}" if it["support"] > 1 else ""
                print(f"  ({name}){mark} {it['sentence'][:110]}")
                print(f"         └ {it['file']}")
            print()
        return 0

    if act == "report":
        data = None
        if getattr(args, "path", ""):
            root = Path(args.path).expanduser()
            if root.exists():
                tmp = DATA / "retro" / "scan.json"
                logscan.scan(root, tmp, max_files=args.max_files)
                data = logscan.load(tmp)
        if data is None:
            if not src.exists():
                print("需要 --path <目录> 或先跑 mkt retro scan", file=sys.stderr)
                return 2
            data = logscan.load(src)

        if not getattr(args, "facts", ""):
            print("报告需要一个钉好口径的事实文件（--facts）。\n"
                  "机器只负责把证据摊开；动作数/回应数用哪个口径，必须人来钉，"
                  "否则算出来的比率是假的。", file=sys.stderr)
            return 2
        facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
        rows = retro.rates_from_facts(facts)
        for r in rows:
            r["sources"] = [f"{args.facts}#{r['channel']}"]
        analysis = {"channels": rows, "waste": retro.waste(rows),
                    "conflicts": retro.conflicts(retro.consolidate(data["claims"]),
                                                 only_channels=list(facts.keys())),
                    "timeline": retro.timeline(data["claims"]),
                    "evidence_index": {}}
        claims = narrative.build_claims(analysis)
        problems = narrative.audit_claims(claims)
        if problems:
            print("结论不合格，拒绝出报告：", file=sys.stderr)
            for p in problems:
                print("  - " + p, file=sys.stderr)
            return 3
        md = _retro_markdown(analysis, claims, data, Path(args.path) if args.path else None)
        out = Path(args.out) if args.out else (BASE / "reports" / "retro.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"已生成：{out}")
        print(f"  证据 {len(data['claims'])} 条 · 渠道 {len(rows)} 个 · "
              f"结论 {len(claims)} 条（全部通过四件套自检）")
        return 0
    return 0


def _retro_markdown(a, claims, data, root):
    from core import narrative as na
    from core import retro as rt
    inv = data["inventory"]
    t = a["timeline"]
    parts = ["# 复盘报告", ""]
    parts.append(na.lede(
        f"扫了 {inv['files']} 个文件，提取 {len(data['claims'])} 条带出处的事实，"
        f"覆盖 {t['start']} → {t['end']}（{t['days']} 天，{t['active_days']} 个有记录的日子）。"))
    parts.append("")
    parts.append(na.section("渠道账",
        "动作数与回应数采用人工钉定的口径；机器只负责把日志里的证据摊开。",
        na.table(["渠道", "动作", "回应", "结果", "每百次动作带来回应", "每百次动作带来结果"],
                 [[r["name"], r["actions"], r["responses"], r["outcomes"],
                   r["response_per_100"], r["outcome_per_100"]] for r in a["channels"]])))
    parts.append("")
    w = rt.wins(a)
    if w:
        parts.append("")
        parts.append(na.section("做对了什么",
            "复盘不能只列错误。这些动作有效率和量，下一轮直接复用。",
            na.table(["打法", "类型", "为什么算做对了"],
                     [[x["name"], x["kind"], x["why"]] for x in w])))
    parts.append("")
    if claims:
        parts.append(na.section("结论", "每条都按「结论—依据—机制—所以」写，缺一项就不允许出现。",
                                na.render_claims(claims)))
    else:
        thin = [r for r in a["channels"] if (r.get("actions") or 0) < 30]
        names = "、".join(r["name"] for r in thin) or "全部渠道"
        parts.append(na.section("结论：样本不足，不下判断",
            "复盘不允许在数据不够的时候给结论。这不是留白，是明确说「现在还看不出来」。",
            "\n".join([
                f"- 所有渠道的单轮动作都不到 30 次（{names}），任何比率都没有统计意义",
                "- 现在的阶段目标是**把一轮动作跑完**，不是判断渠道好坏",
                "- 下一步：按策略跑满 4 周、每周 ≥20 次触达，再回来复盘",
            ])))
    pool = rt.evidence_pool(data["claims"])
    if pool:
        lines = []
        for row in pool[:5]:
            lines.append(f"**{row['name']}**")
            for it in row["items"][:4]:
                name = rt.METRIC_NAMES.get(it["metric"], it["metric"])
                mark = f"（重复 {it['support']} 次）" if it["support"] > 1 else ""
                lines.append(f"- ({name}) {it['sentence'][:140]}{mark}")
            lines.append("")
        parts.append("")
        parts.append(na.section("证据池",
            "日志里被反复提到的数字，按出现次数排序。不作为结论，只作为核对与反驳的材料。",
            "\n".join(lines)))
    if a["conflicts"]:
        parts.append("")
        parts.append(na.section("数字对不上的地方",
            "跨渠道比较之前必须先统一口径，否则比的是记录方式，不是效果。",
            na.table(["渠道", "指标", "日志里出现的取值"],
                     [[c["name"], c["metric"], "、".join(map(str, c["values"]))] for c in a["conflicts"]])))
    rules = rt.derive_rules(a)
    if rules:
        parts.append("")
        parts.append(na.section("下次的规则", "每条都挂在数字上，不是口号。",
                                na.table(["规则", "依据"], [[r["rule"], r["basis"]] for r in rules])))
    parts.append("")
    parts.append(na.section("数据说明",
        "这一节决定了上面的结论能被信到什么程度。",
        "\n".join([
            f"- 扫描范围：{root if root else inv['root']}",
            f"- 文件 {inv['files']} 个，其中 {len(data.get('files_with_claims', []))} 个含可用事实",
            "- 跳过 json/jsonl/csv 原始语料：那里面的数字是数据本身，不是运营动作",
            "- 单文件最多读 256KB；超过一万的数值、8 位日期、PR 号一律丢弃",
            "- 同一个指标有多个合理取值时全部保留，不自动裁决——口径由人钉",
        ])))
    return "\n".join(parts)






def cmd_exp(args):
    from core import experience
    if args.action == "assess":
        row = json.loads(Path(args.file).read_text(encoding="utf-8"))
        payload = experience.assess_eligibility(row, purpose=args.purpose, on=args.on or None)
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0 if payload["eligible"] else 2
    if args.action == "show":
        print(experience.render(args.platform))
    elif args.action == "coverage":
        fresh = experience.freshness()
        for pid, n in experience.coverage().items():
            q = [x for x in experience.stale() if x["platform"] == pid]
            newest = q[0]["newest_days"] if q else None
            age = f"最新 {newest} 天前" if newest is not None else "无发布时间"
            print(f"  {pid:<18} {n:>3} 条   {age}")
        print(f"\n窗口 {fresh['window_days']} 天：{fresh['fresh_claims']}/{fresh['claims']} 条在窗口内"
              f"，{'达标' if fresh['meets_window'] else '不达标（需要重采）'}")
    elif args.action == "stale":
        plan = experience.maintenance_plan()
        print(f"维护窗口 {plan['window_days']} 天，每平台目标 {plan['target_per_platform']} 条，"
              f"建议每 {plan['refresh_cadence_days']} 天重采一次")
        print(f"命令：{plan['command']}\n")
        for x in plan["platforms_needing_refresh"]:
            print(f"  待刷新 {x['name']:<10} {x['claims']:>3} 条  {x['why']}")
        if not plan["platforms_needing_refresh"]:
            print("  全部在窗口内。")
    return 0


def cmd_voice(args):
    from core import voice
    if args.action == "show":
        print(voice.render(args.platform))
    elif args.action == "draft":
        d = voice.draft(args.platform, hook=args.hook or "", what=args.what or "",
                        ask=args.ask or "")
        print(f"# {args.platform} 的开头 · {d['tone']}")
        print()
        for i, x in enumerate(d["drafts"], 1):
            print(f"{i}. {x['opener']}")
        print()
        print(f"长度：{d['length']}｜别这么写：" + "；".join(d["taboos"]))
    elif args.action == "lint":
        text = args.text or ""
        if args.file:
            text = Path(args.file).read_text(encoding="utf-8")
        problems = voice.lint(args.platform, text)
        if not problems:
            print("这条可以用。")
            return 0
        print(f"{len(problems)} 个问题：")
        for p in problems:
            print(f"  - {p}")
        return 2
    return 0


def cmd_result(args):
    from core import result
    from core.followups import Followups
    from core.intent import IntentCampaign
    from core.scope import scope_from_args, filter_rows, ScopedFollowups
    scope = scope_from_args(args)
    entries = filter_rows(_ledger().all() if hasattr(_ledger(), "all") else [], scope)
    fu = ScopedFollowups(Followups(DATA / "followups.jsonl"), scope)
    ic = None
    if getattr(args, "intent", ""):
        pth = DATA / "intent" / f"{args.intent}.json"
        if pth.exists():
            ic = IntentCampaign(pth)
    isum = ic.summary() if ic else None
    events = []
    ev = DATA / "outcomes" / "events.jsonl"
    if ev.exists():
        events = [json.loads(x) for x in ev.read_text(encoding="utf-8").splitlines() if x.strip()]
    events = filter_rows(events, scope)
    crit = json.loads(args.criteria) if getattr(args, "criteria", "") else []
    value_events = [x.strip() for x in str(getattr(args, "value_events", "") or "").split(",") if x.strip()]
    from core.task_outcome import read_records
    task_records = result.scoped_task_outcomes(read_records(DATA), scope)
    r = result.collect(ledger_entries=entries, followups=fu, intent_summary=isum,
                       outcome_events=events, criteria=crit, scope=scope,
                       value_events=value_events or None,
                       task_outcome_records=task_records)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print(result.render(r))
    return 0


def _journey_summary(payload):
    from core.narrative import table
    lines = ["# 用户旅程诊断", ""]
    if payload.get("status") in ("no_data", "insufficient_data"):
        lines.append("当前范围没有足够的已确认事件，不把历史对象当成本轮进展。")
    rows = [[x["label"], x["subjects"], x["events"]] for x in payload.get("funnel", [])]
    lines.append(table(["阶段", "确认人数", "记录数"], rows))
    rec = payload.get("priority_recommendation") or {}
    if rec:
        lines.extend(["", f"下一步：{rec.get('label', '')}——{rec.get('reason', '')}"])
    if payload.get("acquisition", {}).get("should_pause"):
        lines.append("存在未解除的产品阻断，先修产品并暂停新增获客。")
    return "\n".join(lines)


def cmd_opportunity(args):
    from core import opportunity
    facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
    signals = json.loads(Path(args.signals).read_text(encoding="utf-8"))
    payload = opportunity.discover(facts, signals)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0
    from core.narrative import table
    lines = ["# 需求机会候选", ""]
    rows = []
    for c in payload.get("candidates", []):
        action = c.get("next_action") or {}
        rows.append([c.get("need", "")[:36], c.get("demand_match", {}).get("status", ""),
                     action.get("label") or action.get("code", ""),
                     (c.get("explanation") or [""])[0]])
    if not rows:
        lines.append("没有形成候选；继续采集或补产品事实，不编造机会。")
    else:
        lines.append(table(["需求", "匹配", "下一动作", "判断依据"], rows))
        lines.append("")
        lines.append("> 匹配分只表示产品事实匹配，不证明需求强度、购买意愿或收入。")
    print("\n".join(lines))
    return 0


def cmd_signal_interpretation(args):
    from core import signal_interpretation
    candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    payload = signal_interpretation.interpret_many(candidates)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        summary = payload["summary"]
        print("Signal Interpretation（只读）："
              f"qualified={summary['qualified']} needs_review={summary['needs_review']} "
              f"rejected={summary['rejected']} duplicates={summary['duplicates']}")
        for row in payload["candidates"]:
            print(f"- {row['id']}: {row['status']}；commercial={row['commercial_intent']['status']}；"
                  + ("；".join(row["issues"]) if row["issues"] else "结构与证据字段齐全，仍需现实验证"))
    return 0


def cmd_value_chain(args):
    from core import value_delivery
    spec = json.loads(Path(args.file).read_text(encoding="utf-8"))
    payload = value_delivery.run_chain(spec)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(f"价值交付链：state={payload['state']} complete={payload['complete']}")
        reproduction = payload.get("reproduction") or {}
        if reproduction:
            print(f"复现：verified={reproduction['verified']} exit_code={reproduction['exit_code']} "
                  f"source_ref={reproduction['source_ref']}")
        for error in payload.get("errors") or []:
            print(f"- {error}")
    return 0 if payload["complete"] else 2


def cmd_artifact(args):
    from core import value_artifact
    artifact = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if args.action == "lint":
        errors = value_artifact.lint(artifact)
        if errors:
            print("\n".join(errors))
            return 2
        print("价值资产校验通过。")
        return 0
    print(value_artifact.render(artifact))
    return 0


def _business_command(fn):
    """Consistent CLI failure semantics for the business work OS."""
    def wrapped(args):
        try:
            return fn(args) or 0
        except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
            print(f"业务操作被拒绝：{exc}", file=sys.stderr)
            return 2
    return wrapped


def _load_structured(path):
    text = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def _emit(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


@_business_command
def cmd_policy(args):
    from core import execution_policy
    if args.action == "enable":
        actions = [x.strip() for x in args.actions.split(",") if x.strip()] or None
        accounts = [x.strip() for x in args.accounts.split(",") if x.strip()] or None
        days = None if args.days == 0 else args.days
        if days is None:
            print("警告：--days 0 表示常驻授权不过期；仅在你有明确理由时使用。", file=sys.stderr)
        try:
            policy = execution_policy.authorize(DATA, by=args.by, actions=actions,
                                                accounts=accounts, days=days)
        except ValueError as exc:
            print(f"授权失败：{exc}", file=sys.stderr)
            return 2
        return _emit(policy)
    if args.action == "disable":
        return _emit(execution_policy.revoke(DATA, by=args.by))
    if args.action == "list-actions":
        return _emit({"routine_actions": execution_policy.list_routine_actions()})
    if args.action == "check":
        allowed = execution_policy.allows(DATA, args.channel, args.action_name, account=args.account)
        _emit({"allowed": allowed, "channel": args.channel, "action": args.action_name,
               "account": args.account})
        return 0 if allowed else 2
    policy = execution_policy.get_policy(DATA)
    if isinstance(policy, dict) and not policy.get("expires_at"):
        policy = dict(policy, warning="该常驻授权没有有效期；建议用 --days 重新启用，或显式接受长期授权")
    return _emit(policy)


@_business_command
def cmd_orchestrate(args):
    from core import orchestration
    product = _load_structured(args.product)
    if isinstance(product, dict) and isinstance(product.get("product"), dict):
        product = product["product"]
    plan = orchestration.orchestrate(product, args.goal, args.task,
                                     audience=args.audience, channel=args.channel,
                                     motion=args.motion)
    errors = orchestration.lint_orchestration(plan)
    plan["validation"] = {"ok": not errors, "errors": errors}
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        plan["saved_to"] = str(out)
    if args.format == "md":
        print(orchestration.render_markdown(plan))
    else:
        _emit(plan)
    return 0 if not errors else 2


@_business_command
def cmd_capability(args):
    from core import capabilities
    if args.action == "lint":
        errors = capabilities.lint_registry()
        _emit({"ok": not errors, "errors": errors})
        return 0 if not errors else 2
    if args.action == "show":
        if bool(args.role_family) == bool(args.capability):
            raise ValueError("show 需要且只能指定 --role-family 或 --capability")
        payload = capabilities.get_role_family(args.role_family) if args.role_family else capabilities.get_capability(args.capability)
    else:
        roles = capabilities.list_role_families()
        if args.capability:
            roles = [r for r in roles if r["capability_id"] == args.capability]
        payload = {"capabilities": capabilities.list_capabilities(), "role_families": roles,
                   "counts": {"capabilities": len(capabilities.list_capabilities()),
                              "role_families": len(roles)}}
    return _emit(payload)


@_business_command
def cmd_template(args):
    from core import templates
    if args.action == "list":
        return _emit({"templates": templates.list_templates()})
    if args.action == "show":
        return _emit(templates.get_template(args.template_id))
    if args.file:
        spec = _load_structured(args.file)
        errors = templates.lint_instance(spec["template_id"], spec.get("version") or spec.get("template_version"), spec.get("fields") or {})
    else:
        errors = templates.lint_registry()
    _emit({"ok": not errors, "errors": errors})
    return 0 if not errors else 2


@_business_command
def cmd_context(args):
    from core.work import ContextStore
    store = ContextStore(DATA)
    if args.action == "import":
        return _emit(store.import_snapshot(_load_structured(args.file), by=args.by))
    if args.action == "show":
        return _emit(store.get(args.context_id, args.version or None))
    return _emit({"contexts": store.list()})


@_business_command
def cmd_objective(args):
    from core.work import ObjectiveStore
    store = ObjectiveStore(DATA)
    if args.action == "create":
        return _emit(store.create(_load_structured(args.file), by=args.by))
    if args.action == "show":
        return _emit(store.get(args.objective_id, args.version or None))
    return _emit({"objectives": store.list()})


@_business_command
def cmd_work(args):
    from core import work
    store = work.WorkStore(DATA)
    if args.action == "create":
        order = store.create(_load_structured(args.file), by=args.by)
        return _emit({"work_order": order, "lint_errors": work.lint_work_order(order),
                      "next": f"mkt work ready --id {order['work_order_id']} --by <human>"})
    if args.action == "show":
        return _emit(store.get(args.id))
    if args.action == "lint":
        errors = work.lint_work_order(_load_structured(args.file)) if args.file else store.lint(args.id)
        _emit({"ok": not errors, "errors": errors})
        return 0 if not errors else 2
    if args.action == "ready":
        return _emit(store.ready(args.id, by=args.by))
    if args.action == "transition":
        return _emit(store.transition(args.id, args.to, by=args.by, reason=args.reason))
    if args.action == "next":
        return _emit({"next": store.next_actions()})
    return _emit({"work_orders": store.list()})


@_business_command
def cmd_sop(args):
    from core import sop
    registry = sop.SOPRegistry(DATA)
    runs = sop.SOPRunStore(DATA)
    if args.action == "list":
        return _emit({"sops": registry.list()})
    if args.action == "show":
        return _emit(registry.get(args.sop_id, args.version or None))
    if args.action == "lint":
        errors = sop.lint_sop(_load_structured(args.file))
        _emit({"ok": not errors, "errors": errors})
        return 0 if not errors else 2
    if args.action == "register":
        return _emit(registry.register(_load_structured(args.file), by=args.by))
    if args.action == "start":
        return _emit(runs.start(args.work_order_id, by=args.by))
    if args.action == "run":
        return _emit(runs.get(args.run_id))
    if args.action == "complete":
        approval = {"by": args.approval_by, "scope": args.approval_scope} if args.approval_by else None
        return _emit(runs.complete_step(args.run_id, args.step_id,
                                        outputs=_load_structured(args.outputs),
                                        dod_results=_load_structured(args.dod), by=args.by,
                                        approval=approval))
    if args.action == "block":
        return _emit(runs.block_step(args.run_id, args.step_id, reason=args.reason, by=args.by))
    if args.action == "reopen":
        return _emit(runs.reopen_step(args.run_id, args.step_id, reason=args.reason, by=args.by))
    raise ValueError(f"未知 SOP action: {args.action}")


@_business_command
def cmd_deliverable(args):
    from core.deliverables import DeliverableStore
    store = DeliverableStore(DATA)
    if args.action == "create":
        return _emit(store.create(_load_structured(args.file), by=args.by))
    if args.action == "show":
        return _emit(store.get(args.id, args.version or None))
    if args.action == "list":
        return _emit({"deliverables": store.list()})
    if args.action == "submit":
        return _emit(store.submit(args.id, version=args.version or None, by=args.by))
    if args.action == "review":
        return _emit(store.review(args.id, version=args.version or None,
                                  results=_load_structured(args.results), by=args.by))
    if args.action == "revise":
        return _emit(store.revise(args.id, _load_structured(args.fields), by=args.by))
    raise ValueError(f"未知 Deliverable action: {args.action}")


@_business_command
def cmd_handoff(args):
    from core.handoffs import HandoffStore
    store = HandoffStore(DATA)
    if args.action == "create":
        return _emit(store.create(_load_structured(args.file), by=args.by))
    if args.action == "accept":
        return _emit(store.accept(args.id, by=args.by))
    if args.action == "reject":
        return _emit(store.reject(args.id, by=args.by, reason=args.reason,
                                  return_step_id=args.return_step))
    if args.action == "pending":
        return _emit(store.pending(now=args.now or None))
    if args.action == "show":
        return _emit(store.get(args.id))
    return _emit({"handoffs": store.list()})


@_business_command
def cmd_metric(args):
    from core import metrics
    registry = metrics.MetricRegistry(DATA)
    if args.action == "list":
        return _emit({"metrics": registry.list()})
    if args.action == "show":
        return _emit(registry.get(args.metric_id, args.version or None))
    if args.action == "register":
        return _emit(registry.register(_load_structured(args.file), by=args.by))
    metric = registry.get(args.metric_id, args.version or None)
    events = _load_structured(args.events)
    scope = {k: v for k, v in {"campaign": args.campaign, "subject": args.subject,
                               "since": args.since, "until": args.until}.items() if v}
    return _emit(metrics.measure(metric, events, scope=scope,
                                 cohort_end=args.cohort_end, as_of=args.as_of))


@_business_command
def cmd_work_action(args):
    import csv
    from core import work_actions
    strategy = _load_structured(args.strategy_file)
    targets = [{"target": row.get("target", ""), "body": row.get("body", ""),
                "subject": row.get("subject", "")}
               for row in csv.DictReader(Path(args.targets).read_text(encoding="utf-8").splitlines())]
    return _emit(work_actions.materialize(
        DATA, work_order_id=args.work_order_id, step_id=args.step_id,
        deliverable_id=args.deliverable_id, deliverable_version=args.deliverable_version or None,
        strategy=strategy, targets=targets, campaign=args.campaign,
        account=args.account, workspace=args.execution_workspace, by=args.by))


def cmd_playbook_model(args):
    from core import playbook_model
    if args.action == "list":
        print(json.dumps({"motions": playbook_model.MOTIONS,
                          "verticals": playbook_model.VERTICALS}, ensure_ascii=False, indent=1))
        return 0
    playbook = playbook_model.compose(args.platform, args.motion, args.vertical)
    if args.json:
        print(json.dumps(playbook, ensure_ascii=False, indent=1))
    else:
        print(playbook_model.render(playbook))
    return 0


def cmd_evaluate(args):
    from core import evaluation
    if args.action == "compare":
        ours = json.loads(Path(args.ours).read_text(encoding="utf-8"))
        alternative = json.loads(Path(args.alternative).read_text(encoding="utf-8"))
        payload = evaluation.compare_runs(ours, alternative)
    elif args.action == "evolution":
        previous = json.loads(Path(args.previous).read_text(encoding="utf-8"))
        current = json.loads(Path(args.current).read_text(encoding="utf-8"))
        payload = evaluation.assess_evolution(previous, current)
    else:
        case = json.loads(Path(args.file).read_text(encoding="utf-8"))
        problems = evaluation.validate_external_case(case)
        payload = {"ok": not problems, "problems": problems,
                   "note": "外部任务契约通过不代表业务效果已验证"}
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0 if payload.get("ok", True) else 2


def cmd_journey(args):
    from core import journey
    path = Path(args.events)
    text = path.read_text(encoding="utf-8")
    try:
        events = json.loads(text)
    except json.JSONDecodeError:
        events = [json.loads(x) for x in text.splitlines() if x.strip()]
    if not args.since or not args.until:
        print("journey 需要 --since 与 --until，禁止用无界历史代替本轮进展", file=sys.stderr)
        return 2
    identity_map = json.loads(Path(args.identity_map).read_text(encoding="utf-8")) if args.identity_map else None
    payload = journey.diagnose(events, campaign=args.campaign,
                               subject=args.subject or None,
                               time_window={"start": args.since, "end": args.until},
                               identity_map=identity_map)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(_journey_summary(payload))
    return 0


def cmd_revise(args):
    from core import result, revise
    from core.followups import Followups
    from core.intent import IntentCampaign
    from core.scope import scope_from_args, filter_rows, ScopedFollowups
    scope = scope_from_args(args)
    entries = filter_rows(_ledger().all() if hasattr(_ledger(), "all") else [], scope)
    fu = ScopedFollowups(Followups(DATA / "followups.jsonl"), scope)
    ic = None
    if getattr(args, "intent", ""):
        pth = DATA / "intent" / f"{args.intent}.json"
        if pth.exists():
            ic = IntentCampaign(pth)
    if getattr(args, "result", ""):
        r = json.loads(Path(args.result).read_text(encoding="utf-8"))
    else:
        events = []
        ev = DATA / "outcomes" / "events.jsonl"
        if ev.exists():
            events = [json.loads(x) for x in ev.read_text(encoding="utf-8").splitlines() if x.strip()]
        from core.task_outcome import read_records
        r = result.collect(ledger_entries=entries, followups=fu,
                           intent_summary=ic.summary() if ic else None,
                           outcome_events=filter_rows(events, scope), scope=scope,
                           task_outcome_records=result.scoped_task_outcomes(read_records(DATA), scope))
    params = json.loads(args.params) if getattr(args, "params", "") else {}
    diff = revise.propose(r, params, intent=ic.summary() if ic else None)
    print(revise.render(diff))
    return 0


def cmd_execute(args):
    import csv as _csv
    from core import execute
    strat = json.loads(Path(args.strategy_file).read_text(encoding="utf-8"))
    rows = list(_csv.DictReader(Path(args.targets).read_text(encoding="utf-8").splitlines()))
    targets = [{"target": r.get("target", ""), "body": r.get("body", ""),
                "subject": r.get("subject", "")} for r in rows]
    try:
        res = execute.build(strat, targets, campaign=args.campaign, data_dir=DATA,
                            criteria=json.loads(args.criteria) if args.criteria else None,
                            account=getattr(args, "account", None),
                            workspace=getattr(args, "workspace", None))
    except execute.ExecuteError as e:
        print(f"不能生成：{e}", file=sys.stderr)
        return 2
    print(execute.render(res))
    return 0


def _goal_plan_from_selection(path, facts):
    from core import goals
    sel = json.loads(Path(path).read_text(encoding="utf-8"))
    roles = sel.get("platforms") or {}
    ids = [roles.get("primary")]
    if roles.get("launch"):
        ids.append(roles["launch"])
    ids.extend(roles.get("support") or [])
    ids = [i for i in ids if i]
    secondary = next((m for m in (sel.get("metrics") or [])
                      if m.get("role") == "secondary"), None)
    return goals.build_plan(facts, sel["goal_id"], platforms=ids,
                            target=sel.get("target"),
                            window_days=sel.get("window_days"),
                            secondary_goal_id=(secondary or {}).get("id"),
                            secondary_target=(secondary or {}).get("target"))


def cmd_strategy(args):
    from core import gtm, strategy, cadence, retro, logscan
    act = args.action

    if act == "build":
        facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
        goal_plan = None
        if getattr(args, "selection", ""):
            goal_plan = _goal_plan_from_selection(args.selection, facts)
        payload = gtm.diagnose(facts)
        analysis = None
        if getattr(args, "retro_facts", ""):
            rfacts = json.loads(Path(args.retro_facts).read_text(encoding="utf-8"))
            rows = retro.rates_from_facts(rfacts)
            for r in rows:
                r["sources"] = [f"{args.retro_facts}#{r['channel']}"]
            analysis = {"channels": rows, "waste": retro.waste(rows)}
        s = strategy.build(facts["product"], focus=payload["focus"],
                           analysis=analysis,
                           constraints={"human_minutes_per_week": args.human_minutes,
                                        "actions_per_week": args.actions_per_week},
                           goal_plan=goal_plan)
        if getattr(args, "json", False):
            print(json.dumps(s, ensure_ascii=False, indent=1))
            return 0
        md = strategy.render(s)
        if args.out:
            out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8")
            print(f"策略已生成：{out}")
        else:
            print(md)
        return 0

    if act == "cadence":
        facts = json.loads(Path(args.facts).read_text(encoding="utf-8")) if args.facts else None
        goal_plan = None
        if getattr(args, "selection", ""):
            if not facts:
                print("用 --selection 时必须同时给 --facts", file=sys.stderr)
                return 2
            goal_plan = _goal_plan_from_selection(args.selection, facts)
            s = strategy.build(facts["product"], focus={}, analysis=None,
                               goal_plan=goal_plan)
        elif facts:
            payload = gtm.diagnose(facts)
            s = strategy.build(facts["product"], focus=payload["focus"], analysis=None)
        else:
            s = {"primary": {"name": args.primary or "GitHub", "residency": 0}}
            s.update({"cold_start": []})
        plan = cadence.build(s, start=args.start,
                             actions_per_week=args.actions_per_week,
                             goal_plan=goal_plan)
        md = cadence.render(plan)
        if args.out:
            out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8")
            print(f"动作表已生成：{out}")
        else:
            print(md)
        return 0
    return 0


def cmd_progress(args):
    from core import progress
    from core.followups import Followups
    from core.intent import IntentCampaign
    fu = Followups(DATA / "followups.jsonl")
    ic = None
    if getattr(args, "intent", ""):
        p = DATA / "intent" / f"{args.intent}.json"
        if p.exists():
            ic = IntentCampaign(p)
    on = args.on or None
    if args.action == "daily":
        print(progress.render_daily(progress.daily(fu, ic, on)))
    else:
        print(progress.render_weekly(progress.weekly(fu, ic, on)))
    return 0


def cmd_billing(args):
    from core import billing
    pricing = json.loads(args.pricing) if args.pricing else None
    try:
        path = DATA / "pricing-experiments.jsonl"
        if args.summary:
            print(json.dumps(billing.acceptance_summary(path), ensure_ascii=False, indent=1))
            return 0
        if args.experiment_id:
            log = billing.PricingExperimentLog(path)
            if args.record_acceptance:
                rec = log.record(args.experiment_id, args.record_acceptance,
                                 reason=args.reason, actor=args.by)
                print(json.dumps(rec, ensure_ascii=False, indent=1))
            else:
                rec = log.offer(args.experiment_id, actions=args.actions,
                                outcomes=args.outcomes, pricing=pricing,
                                prospect=args.prospect)
                print(json.dumps(rec, ensure_ascii=False, indent=1))
            return 0
        return _billing_run(args, pricing)
    except ValueError as e:
        print(f"报价被拒：{e}", file=sys.stderr)
        return 2


def _billing_run(args, pricing):
    from core import billing
    if args.from_files:
        q = billing.quote_from_files(DATA / "ledger.jsonl", DATA / "outcomes" / "events.jsonl",
                                     pricing)
    else:
        q = billing.quote(args.actions, args.outcomes, pricing)
    if args.json:
        print(json.dumps(q, ensure_ascii=False, indent=1))
    else:
        print(billing.describe(q))
    return 0


def cmd_account(args):
    from core.accounts import AccountRegistry
    registry = AccountRegistry(DATA / "accounts.json")
    act = args.action
    if act == "add":
        rec = registry.register(
            args.platform, args.handle, args.identity_type, account_id=args.account_id or None,
            owner=args.owner, ownership_evidence=args.ownership_evidence,
            authorization_evidence=args.authorization_evidence,
            multi_account_allowed=args.multi_account_allowed,
            multi_account_evidence=args.multi_account_evidence,
            disclosure=args.disclosure)
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        return 0
    if act == "list":
        print(json.dumps(registry.all(platform=args.platform, status=args.status),
                         ensure_ascii=False, indent=1))
        return 0
    if act == "check":
        print(json.dumps(registry.operational(args.account_id), ensure_ascii=False, indent=1))
        return 0
    if act == "warm":
        rec = registry.transition(args.account_id, "warming", reason=args.reason or "进入养号阶段")
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        return 0
    if act == "eligible":
        rec = registry.transition(args.account_id, "eligible", reason=args.reason or "养号复核通过",
                                  evidence=args.evidence, reviewer=args.by)
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        return 0
    if act == "pause":
        rec = registry.transition(args.account_id, "paused", reason=args.reason)
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        return 0
    if act == "takeover":
        rec = registry.request_human_takeover(args.account_id, args.reason)
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        return 0
    if act == "resolve":
        rec = registry.resolve_human_takeover(args.account_id, reviewer=args.by, note=args.note)
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        return 0
    if act == "audit":
        print(json.dumps(registry.audit(args.account_id or None), ensure_ascii=False, indent=1))
        return 0
    return 2


def cmd_network(args):
    from core.network import ProxyRegistry
    from core.accounts import AccountRegistry
    registry = ProxyRegistry(DATA / "network.json")
    accounts = AccountRegistry(DATA / "accounts.json")
    act = args.action
    if act == "bind":
        rec = registry.bind(args.account_id, args.env_var, platform=args.platform,
                            purpose=args.purpose, account_registry=accounts)
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        return 0
    if act == "list":
        print(json.dumps(registry.all(), ensure_ascii=False, indent=1))
        return 0
    if act == "unbind":
        rec = registry.unbind(args.account_id, reason=args.reason)
        print(json.dumps(rec, ensure_ascii=False, indent=1))
        return 0
    if act == "audit":
        print(json.dumps(registry.audit(args.account_id or None), ensure_ascii=False, indent=1))
        return 0
    if act == "export":
        print(json.dumps(registry.export(), ensure_ascii=False, indent=1))
        return 0
    return 2


def cmd_demo(args):
    from core.demo import run_demo
    return run_demo(BASE)


def cmd_work_demo(args):
    from core.work_demo import run_work_demo
    return run_work_demo(BASE)


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
                DATA, args.id, by=args.by, note=args.note or "", days=args.days,
                account=getattr(args, "account", None), workspace=getattr(args, "workspace", None))
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
                                only=args.only, routine=args.routine)
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
    from core.followups import Followups
    n = next_step(DATA, _ledger(), args.campaign or None,
                  followups=Followups(DATA / "followups.jsonl"))
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


def cmd_scorecard(args):
    from core import scorecard
    payload = scorecard.build(Path(DATA), campaign=args.campaign, since=args.since)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        rendered = scorecard.markdown(payload)
        print(rendered)
        if args.output:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(rendered, encoding="utf-8")
    return 0


def cmd_optimize(args):
    from core import optimization, scorecard
    from core.operations_memory import summarize
    card = scorecard.build(Path(DATA), campaign=args.campaign, since=args.since)
    memory = summarize(_ledger().all(), campaign=args.campaign,
                       channel=args.channel, minimum_confirmed=args.minimum_confirmed)
    payload = optimization.propose(card, memory=memory)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        rendered = optimization.markdown(payload)
        print(rendered)
        if args.output:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(rendered, encoding="utf-8")
    return 0


def cmd_gtm(args):
    cmd = getattr(args, "gtm_cmd", None)
    if cmd == "diagnose":
        from core import gtm
        facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
        payload = gtm.diagnose(facts)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            rendered = gtm.markdown(payload)
            print(rendered)
            if args.output:
                out = Path(args.output)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(rendered, encoding="utf-8")
        return 0
    from core import gtm_design
    if cmd == "taxonomy":
        payload = {
            "schema": gtm_design.SCHEMA,
            "objects": [{"id": key, "label": value["label"],
                         "motions": value["motions"]} for key, value in gtm_design.OBJECTS.items()],
            "objectives": [{"id": key, "label": value["label"],
                            "not_success": value["not_success"]}
                           for key, value in gtm_design.OBJECTIVES.items()],
            "motions": [{"id": key, "label": value["label"],
                         "objects": value["objects"], "objectives": value["objectives"],
                         "requires": value["requires"]}
                        for key, value in gtm_design.MOTIONS.items()],
            "requirements": gtm_design.REQUIREMENTS,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        else:
            print("对象：" + "、".join(f"{row['label']}({row['id']})" for row in payload["objects"]))
            print("目标：" + "、".join(f"{row['label']}({row['id']})" for row in payload["objectives"]))
            print("机制：" + "、".join(f"{row['label']}({row['id']})" for row in payload["motions"]))
        return 0
    if cmd == "lint":
        payload = gtm_design.load_json_arg(args.file)
        problems = gtm_design.lint(payload)
        if args.json:
            print(json.dumps({"ok": not problems, "problems": problems},
                             ensure_ascii=False, indent=1))
        else:
            print("设计自洽。" if not problems else "发现问题：\n" + "\n".join(f"- {p}" for p in problems))
        return 0 if not problems else 1
    if cmd == "design":
        try:
            constraints = gtm_design.load_json_arg(args.constraints) if args.constraints else {}
            proposal = gtm_design.load_json_arg(args.proposal) if args.proposal else {}
            payload = gtm_design.design(
                args.task,
                object_id=args.object or None,
                objective_id=args.objective or None,
                constraints=constraints,
                proposal=proposal or None,
            )
        except (OSError, ValueError) as exc:
            print(f"设计失败：{exc}", file=sys.stderr)
            return 2
        if args.save:
            saved = Path(args.save)
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        else:
            rendered = gtm_design.render(payload)
            print(rendered)
            if args.out:
                out = Path(args.out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(rendered, encoding="utf-8")
        return 0
    if cmd == "handoff":
        try:
            design = gtm_design.load_json_arg(args.design)
            product = gtm_design.load_json_arg(args.product) if args.product else None
            payload = gtm_design.handoff(design, product=product)
        except (OSError, ValueError) as exc:
            print(f"交接失败：{exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        else:
            rendered = gtm_design.render_handoff(payload)
            print(rendered)
            if args.out:
                out = Path(args.out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(rendered, encoding="utf-8")
        return 0
    print("用法: mkt gtm diagnose --facts <facts.json> | "
          "mkt gtm design \"<任务>\" [--object …] [--objective …] | mkt gtm lint <file> | "
          "mkt gtm taxonomy", file=sys.stderr)
    return 2


def cmd_goal(args):
    from core import goals
    try:
        facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"事实包读不了：{e}", file=sys.stderr)
        return 2
    act = args.action
    try:
        if act == "list":
            payload = goals.list_goals(facts)
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=1))
                return 0
            rendered = goals.render_list(payload)
        elif act == "plan":
            platforms = _split_platforms(getattr(args, "platforms", ""))
            plan = goals.build_plan(facts, args.goal, platforms=platforms,
                                    target=args.target, window_days=args.window_days,
                                    secondary_goal_id=args.secondary or None,
                                    secondary_target=args.secondary_target)
            if args.json:
                print(json.dumps(plan, ensure_ascii=False, indent=1))
                return 0
            rendered = goals.render_plan(plan)
        elif act == "select":
            platforms = _split_platforms(args.platforms)
            selection = goals.select(facts, args.goal, platforms,
                                     target=args.target, window_days=args.window_days,
                                     note=args.note,
                                     secondary_goal_id=args.secondary or None,
                                     secondary_target=args.secondary_target)
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(selection, ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")
            if args.json:
                print(json.dumps(selection, ensure_ascii=False, indent=1))
            else:
                print(f"已写入选择：{out}")
                goal_label = selection["goal_id"]
                if selection.get("secondary_goal_id"):
                    goal_label += f" + {selection['secondary_goal_id']}"
                print(f"目标：{goal_label}；平台：{'、'.join(selection['selected'])}")
                print(f"下一步：python3 mkt.py strategy build --facts {args.facts} "
                      f"--selection {out}")
            return 0
        else:
            print("用法: mkt goal list|plan|select", file=sys.stderr)
            return 2
    except goals.GoalError as e:
        print(f"不能生成：{e}", file=sys.stderr)
        return 2
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
        print(f"已生成：{out}")
    else:
        print(rendered)
    return 0


def cmd_sprint(args):
    from core import goals, sprint
    try:
        if getattr(args, "selection", ""):
            facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
            goal_plan = _goal_plan_from_selection(args.selection, facts)
        else:
            facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
            platforms = _split_platforms(getattr(args, "platforms", ""))
            goal_plan = goals.build_plan(
                facts, args.goal, platforms=platforms,
                target=args.target, window_days=None,
                secondary_goal_id=args.secondary or None,
                secondary_target=args.secondary_target)
        plan = sprint.build(goal_plan, days=args.days, start=args.start,
                            hours_per_day=args.hours_per_day,
                            launch_hour=args.launch_hour)
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=1))
            return 0
        rendered = sprint.render(plan)
    except (goals.GoalError, sprint.SprintError) as e:
        print(f"不能生成：{e}", file=sys.stderr)
        return 2
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
        print(f"已生成：{out}")
    else:
        print(rendered)
    return 0


def _goal_plan_for(args, facts):
    from core import goals
    if getattr(args, "selection", ""):
        return _goal_plan_from_selection(args.selection, facts)
    platforms = _split_platforms(getattr(args, "platforms", ""))
    return goals.build_plan(
        facts, args.goal, platforms=platforms,
        secondary_goal_id=args.secondary or None)


def cmd_channel(args):
    from core import channels, goals
    act = args.action
    if act == "venues":
        rows = channels.VENUES
        if args.platform:
            rows = [r for r in rows if r["platform"] == args.platform]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
            return 0
        from core.narrative import table
        print(table(["id", "平台", "地方", "人群", "能不能发", "怎么进", "信号"],
                    [[r["id"], r["platform"], r["name"], "、".join(r["audience"]),
                      r["promo"], r["access"], f"{r['signal_density']}/3"]
                     for r in rows]))
        return 0

    facts = json.loads(Path(args.facts).read_text(encoding="utf-8"))
    try:
        goal_plan = (_goal_plan_for(args, facts)
                     if getattr(args, "goal", "") or getattr(args, "selection", "")
                     else None)
        if act == "score":
            row = channels.venue(args.venue)
            scored = channels.score_venue(row, facts, goal_plan)
            if args.json:
                print(json.dumps(scored, ensure_ascii=False, indent=1))
            else:
                print(f"{scored['name']}：{scored['score']} 分；"
                      f"受众命中 {'、'.join(scored['audience_hits']) or '无'}；"
                      f"promo={scored['promo']}，access={scored['access']}")
            return 0
        plan = channels.plan(facts, goal_plan)
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=1))
            return 0
        rendered = channels.render(plan)
    except (goals.GoalError, ValueError) as e:
        print(f"不能生成：{e}", file=sys.stderr)
        return 2
    if getattr(args, "out", ""):
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
        print(f"已生成：{out}")
    else:
        print(rendered)
    return 0


def cmd_deal(args):
    from core import conversion
    deals = conversion.Deals(DATA / "deals.jsonl")
    act = args.action
    try:
        if act == "add":
            rec = deals.add(args.contact, name=args.name, campaign=args.campaign,
                            owner=args.owner, note=args.note)
            print(json.dumps(rec, ensure_ascii=False, indent=1) if args.json
                  else f"已建 {rec['id']}：{rec['name']}（{rec['stage']}）")
            return 0
        if act == "advance":
            rec = deals.advance(args.id, args.stage, args.by,
                                evidence=args.evidence, note=args.note,
                                verification_method=getattr(args, "verify_method", ""),
                                verification_ref=getattr(args, "verify_ref", ""))
            print(json.dumps(rec, ensure_ascii=False, indent=1) if args.json
                  else f"{rec['id']} → {rec['stage']}")
            return 0
        if act == "qualify":
            criteria = {"trigger": args.trigger, "contactable": args.contactable,
                        "data": args.data, "cost": args.cost, "price": args.price}
            out = deals.qualify(args.id, criteria)
            print(json.dumps(out, ensure_ascii=False, indent=1) if args.json
                  else f"{args.id}：{out['result']['score']}/5，"
                       f"{'合格' if out['result']['qualified'] else '未合格'}")
            return 0
        if act == "list":
            rows = [r for r in deals.all() if not args.stage or r["stage"] == args.stage]
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=1))
                return 0
            from core.narrative import table
            print(table(["id", "对象", "阶段", "报价", "更新"],
                        [[r["id"], r.get("name", ""), r["stage"],
                          r.get("value", 0), r["updated"]] for r in rows]))
            return 0
        if act == "funnel":
            data = deals.funnel(args.on or None)
            print(json.dumps(data, ensure_ascii=False, indent=1) if args.json
                  else conversion.render_status(data))
            return 0
        if act == "next":
            on = args.on or conversion._today()
            rows = []
            for row in deals.all():
                if row["stage"] in ("lost", "repeat"):
                    continue
                days = (conversion.date.fromisoformat(on)
                        - conversion.date.fromisoformat(row["updated"])).days
                nxt = conversion.next_action(row["stage"], days)
                if nxt["action"] == "等":
                    continue
                item = dict(row)
                item["next"] = nxt
                rows.append(item)
            rows.sort(key=lambda r: (r["next"]["due_in_days"], r["id"]))
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=1))
            else:
                print(conversion.render_next(rows))
            return 0
        if act == "quote":
            deal = deals.get(args.id)
            out = conversion.quote(deal, args.offer)
            rendered = conversion.render_quote(out)
            if args.json:
                print(json.dumps(out, ensure_ascii=False, indent=1))
                return 0
            if args.out:
                path = Path(args.out)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(rendered, encoding="utf-8")
                print(f"已生成：{path}")
            else:
                print(rendered)
            return 0
        if act == "objections":
            if args.json:
                print(json.dumps(conversion.OBJECTIONS, ensure_ascii=False, indent=1))
                return 0
            from core.narrative import table
            print(table(["异议", "信号", "怎么回", "什么时候走开"],
                        [[o["label"], o["signal"], o["response"], o["walk_away"]]
                         for o in conversion.OBJECTIONS]))
            return 0
    except conversion.ConversionError as e:
        print(f"不能执行：{e}", file=sys.stderr)
        return 2
    print("用法: mkt deal add|advance|qualify|list|next|funnel|quote|objections",
          file=sys.stderr)
    return 2


def _split_platforms(raw):
    if not raw:
        return None
    return [x.strip() for x in str(raw).replace("，", ",").split(",") if x.strip()]


def cmd_schedule(args):
    from core import scheduler
    data = Path(DATA)
    if args.action == "add":
        schedule = scheduler.add(
            data, schedule_id=args.id, queues=args.queue or [],
            interval_minutes=args.interval, go=args.go, max_waves=args.max_waves)
        print(json.dumps(schedule, ensure_ascii=False, indent=2))
        return 0
    if args.action == "list":
        print(json.dumps(scheduler.list_schedules(data), ensure_ascii=False, indent=2))
        return 0
    if args.action == "tick":
        result = scheduler.tick(data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not any(item.get("outcome") == "needs_review" for item in result["ran"]) else 3
    if args.action == "status":
        print(json.dumps(scheduler.list_schedules(data), ensure_ascii=False, indent=2))
        return 0
    if args.action == "resume":
        if not (args.id and args.note):
            raise SystemExit("schedule resume 需要 --id 与 --note")
        schedule = scheduler.resume(data, args.id, note=args.note, by=args.by)
        print(json.dumps(schedule, ensure_ascii=False, indent=2))
        return 0
    raise SystemExit(f"未知 schedule 动作: {args.action}")


def cmd_workflow(args):
    """Versioned, approved, replayable operation recipes (no implicit sends)."""
    from core import workflows
    from core.action_runner import run_action
    data = Path(DATA)
    action = args.action
    if action == "list":
        rows = workflows.list_workflows(data)
        print(json.dumps([
            {"id": row["workflow"]["id"], "version": row["workflow"].get("version"),
             "channel": row["workflow"].get("channel"), "action": row["workflow"].get("action"),
             "approval": row["approval"]}
            for row in rows
        ], ensure_ascii=False, indent=2))
        return 0
    if action == "show":
        workflow = workflows.find_workflow(data, args.workflow_id)
        print(json.dumps({"workflow": workflow, "approval": workflows.approval(data, workflow),
                          "errors": workflows.lint(workflow)}, ensure_ascii=False, indent=2))
        return 0
    if action == "lint":
        workflow = workflows.find_workflow(data, args.workflow_id)
        errors = workflows.lint(workflow)
        print(json.dumps({"workflow_id": workflow["id"], "errors": errors,
                          "status": "pass" if not errors else "fail"}, ensure_ascii=False, indent=2))
        return 0 if not errors else 1
    if action == "learn":
        if not args.trace:
            raise SystemExit("workflow learn 需要 --trace trace.json")
        trace = json.loads(Path(args.trace).read_text(encoding="utf-8"))
        workflow = workflows.learn_from_trace(trace, workflow_id=args.workflow_id, name=args.name)
        path = workflows.save_workflow(data, workflow)
        print(json.dumps({"status": "learned", "path": str(path), "workflow_id": workflow["id"],
                          "steps": len(workflow["steps"]),
                          "approval": workflows.approval(data, workflow)}, ensure_ascii=False, indent=2))
        return 0
    if action == "approve":
        workflow = workflows.find_workflow(data, args.workflow_id)
        result = workflows.approve(data, workflow, by=args.by, days=args.days, note=args.note or "")
        print(json.dumps({"status": "approved", "workflow_id": workflow["id"],
                          "path": result["path"], "approval": result["approval"]},
                         ensure_ascii=False, indent=2))
        return 0
    if action == "replay":
        workflow = workflows.find_workflow(data, args.workflow_id)
        values = {}
        for pair in args.var or []:
            key, sep, value = str(pair).partition("=")
            if not sep or not key:
                raise SystemExit(f"--var 需要 key=value，收到 {pair!r}")
            values[key] = value
        needs_page = any(str(step.get("kind")) in ("open", "fill", "click", "press", "read")
                         for step in workflow.get("steps") or [])
        page = browser = None
        if needs_page:
            if not args.cdp_url:
                raise SystemExit("该 workflow 含 UI 步骤，需要 --cdp-url http://127.0.0.1:9222")
            from core import cdp
            browser, page = cdp.connect(args.cdp_url)
        def _executor(**kwargs):
            return run_action(data_dir=str(data), log=print, **kwargs)
        try:
            result = workflows.replay(data, workflow, page=page, executor=_executor,
                                      go=args.go, campaign=args.campaign or "",
                                      values=values, log=print)
        finally:
            if page is not None:
                try:
                    page.close()
                finally:
                    if browser is not None:
                        browser.close()
        print(json.dumps({key: value for key, value in result.items() if key != "run"},
                         ensure_ascii=False, indent=2))
        return 0 if result["status"] in ("completed", "dry-run") else 3
    if action == "runs":
        rows = workflows.list_runs(data, args.workflow_id)
        print(json.dumps([
            {"run_id": row.get("run_id"), "workflow_id": row.get("workflow_id"),
             "status": row.get("status"), "stop_reason": row.get("stop_reason"),
             "steps": len(row.get("steps") or []), "started_at": row.get("started_at")}
            for row in rows
        ], ensure_ascii=False, indent=2))
        return 0
    if action == "reconcile":
        if not (args.run and args.outcome and args.note):
            raise SystemExit("workflow reconcile 需要 --run <id> --outcome sent|not_sent --note '…'")
        run = workflows.reconcile(data, args.run, outcome=args.outcome, note=args.note, by=args.by)
        print(json.dumps({"run_id": run["run_id"], "status": run["status"],
                          "reconciliations": run.get("reconciliations")}, ensure_ascii=False, indent=2))
        return 0
    raise SystemExit(f"未知 workflow 动作: {action}")


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
            extra=args.extra, routine=args.routine, account=args.account)
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
    sp = sub.add_parser("work-demo", help="零网络 GTM Work OS：上下文→工作单→SOP→交付→交接→KPI→plan/queue")
    sp.set_defaults(fn=cmd_work_demo)
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
    sp.add_argument("--account", help="approve：绑定的实际执行账号")
    sp.add_argument("--workspace", help="approve：绑定的执行工作区")
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
    sp.add_argument("--go", action="store_true", help="run：实跑（须已人审 plan 或有效 routine policy）")
    sp.add_argument("--routine", action="store_true", help="run：使用工作区日常营销常驻授权；其他安全门不变")
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
    sp.add_argument("--account", default="", help="执行账号；routine policy 按此精确匹配")
    sp.add_argument("--go", action="store_true")
    sp.add_argument("--routine", action="store_true", help="使用日常营销常驻授权；付款/合同等不适用")
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




    sp = sub.add_parser("strategy", help="策略：主渠道/冷启动/放大/风控/第一周")
    ssub = sp.add_subparsers(dest="action", required=True)
    a = ssub.add_parser("build", help="从产品事实包 + 复盘算出这一轮怎么打")
    a.add_argument("--facts", required=True, help="产品事实包 JSON（同 mkt gtm diagnose）")
    a.add_argument("--retro-facts", default="", help="人工钉定的口径 JSON，用于「不做」清单")
    a.add_argument("--human-minutes", type=int, default=120)
    a.add_argument("--actions-per-week", type=int, default=20)
    a.add_argument("--out", default="")
    a.add_argument("--json", action="store_true", help="输出策略 JSON（供 mkt execute 用）")
    a.add_argument("--selection", default="", help="mkt goal select 写出的选择契约 JSON")
    a = ssub.add_parser("cadence", help="四周动作表")
    a.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    a.add_argument("--facts", default="")
    a.add_argument("--primary", default="")
    a.add_argument("--selection", default="", help="mkt goal select 写出的选择契约 JSON")
    a.add_argument("--actions-per-week", type=int, default=20)
    a.add_argument("--out", default="")
    sp.set_defaults(fn=cmd_strategy)




    sp = sub.add_parser("exp", help="用户经验：别人真跑过的做法，带出处与样本量")
    esub = sp.add_subparsers(dest="action", required=True)
    a = esub.add_parser("show", help="看某个平台的经验"); a.add_argument("platform")
    a = esub.add_parser("coverage", help="哪些平台已有经验")
    a = esub.add_parser("stale", help="哪些平台的经验该重采了")
    a = esub.add_parser("assess", help="只读资格复核：权利依据/确认人/时间/复核记录/来源域")
    a.add_argument("--file", required=True, help="单条经验 JSON")
    a.add_argument("--purpose", choices=["strategy", "experiment", "generalization"], default="strategy")
    a.add_argument("--on", default="", help="资格评估日期 YYYY-MM-DD")
    sp.set_defaults(fn=cmd_exp)

    sp = sub.add_parser("voice", help="平台语气：怎么说话、开头怎么写、会不会像广告")
    vsub = sp.add_subparsers(dest="action", required=True)
    a = vsub.add_parser("show", help="看这个平台的语气画像")
    a.add_argument("platform")
    a = vsub.add_parser("draft", help="给三个开头骨架")
    a.add_argument("platform")
    a.add_argument("--hook", default=""); a.add_argument("--what", default=""); a.add_argument("--ask", default="")
    a = vsub.add_parser("lint", help="检查一段文字会不会被当成广告")
    a.add_argument("platform"); a.add_argument("--text", default=""); a.add_argument("--file", default="")
    sp.set_defaults(fn=cmd_voice)

    sp = sub.add_parser("result", help="执行结果：平台动作/任务结果/业务结果三层")
    sp.add_argument("--campaign", default=""); sp.add_argument("--intent", default="")
    sp.add_argument("--subject", default=""); sp.add_argument("--since", default=""); sp.add_argument("--until", default="")
    sp.add_argument("--value-events", default="", help="逗号分隔的价值事件类型；默认统计所有已核验事件")
    sp.add_argument("--criteria", default="", help="可选学习指标 JSON 数组，不是产品生死门")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_result)

    sp = sub.add_parser("signal-interpret", help="只读语义候选校验：来源/主体/问题/替代/接触资格/商业分轨/去重")
    sp.add_argument("--candidates", required=True, help="宿主 Agent 结构化语义候选 JSON")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_signal_interpretation)

    sp = sub.add_parser("opportunity", help="需求发现：语义候选经校验后×产品事实；旧信号只作未验证匹配")
    sp.add_argument("--facts", required=True, help="产品事实 JSON")
    sp.add_argument("--signals", required=True, help="semantic_candidates 或旧五类信号 JSON")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_opportunity)

    sp = sub.add_parser("value-chain", help="本地价值交付链：研究→方案→授权复现→资产→授权交付→用户读回")
    sp.add_argument("action", choices=["run"], help="只运行绑定命令指纹的本地复现；不发送平台消息")
    sp.add_argument("--file", required=True, help="完整链 JSON（含命令级与交付级授权）")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_value_chain)

    sp = sub.add_parser("artifact", help="价值资产：校验或渲染教程/样例/测试/模板/对比/案例")
    sp.add_argument("action", choices=["lint", "render"])
    sp.add_argument("--file", required=True, help="价值资产 JSON")
    sp.set_defaults(fn=cmd_artifact)

    sp = sub.add_parser("policy", help="日常营销常驻授权：仅限授权账号/渠道/动作；付款等仍需人工")
    psub = sp.add_subparsers(dest="action", required=True)
    a = psub.add_parser("show")
    a = psub.add_parser("list-actions")
    a = psub.add_parser("enable"); a.add_argument("--by", required=True)
    a.add_argument("--actions", default="", help="可选：channel:action 逗号列表；默认内置日常动作")
    a.add_argument("--accounts", default="", help="可选：授权账号逗号列表")
    a.add_argument("--days", type=int, default=30,
                   help="授权有效天数（默认 30；0 表示不过期，需显式声明）")
    a = psub.add_parser("disable"); a.add_argument("--by", required=True)
    a = psub.add_parser("check"); a.add_argument("--channel", required=True); a.add_argument("--action-name", required=True)
    a.add_argument("--account", default="")
    sp.set_defaults(fn=cmd_policy)

    sp = sub.add_parser("orchestrate", help="按产品/目标/任务选择最小岗位组合、交付物与表达方式")
    sp.add_argument("--product", required=True, help="产品事实 JSON；也接受含 product 的事实包")
    sp.add_argument("--goal", required=True, help="本轮业务目标")
    sp.add_argument("--task", required=True, help="具体任务、受众、渠道和范围描述")
    sp.add_argument("--audience", default="", choices=["", "executive", "operator", "developer", "sales", "customer", "partner", "高管", "运营", "开发者", "销售", "客户", "伙伴"])
    sp.add_argument("--channel", default="", help="email/github/x/linkedin/xiaohongshu/wechat/landing/presentation 或中文别名")
    sp.add_argument("--motion", default="", help="可选：显式覆盖任务类型推断")
    sp.add_argument("--format", choices=["json", "md"], default="json", help="输出结构化编排或可阅读方案 brief")
    sp.add_argument("--out", default="", help="可选：保存 Workstream 候选 JSON")
    sp.set_defaults(fn=cmd_orchestrate)

    sp = sub.add_parser("capability", help="GTM OS：18 能力域与 40 岗位族注册表")
    csub = sp.add_subparsers(dest="action", required=True)
    a = csub.add_parser("list", help="列出能力域与岗位族"); a.add_argument("--capability", default="")
    a = csub.add_parser("show", help="查看能力域或岗位完整契约")
    a.add_argument("--capability", default=""); a.add_argument("--role-family", default="")
    a = csub.add_parser("lint", help="校验 18/40 覆盖与引用完整性")
    sp.set_defaults(fn=cmd_capability)

    sp = sub.add_parser("template", help="GTM OS：32 个交付物模板与实例校验")
    tsub = sp.add_subparsers(dest="action", required=True)
    a = tsub.add_parser("list")
    a = tsub.add_parser("show"); a.add_argument("template_id")
    a = tsub.add_parser("lint"); a.add_argument("--file", default="", help="可选：{template_id,version,fields} 实例")
    sp.set_defaults(fn=cmd_template)

    sp = sub.add_parser("context", help="GTM OS：导入不可变 Askovo/用户业务上下文快照")
    csub = sp.add_subparsers(dest="action", required=True)
    a = csub.add_parser("import"); a.add_argument("--file", required=True); a.add_argument("--by", default="operator")
    a = csub.add_parser("list")
    a = csub.add_parser("show"); a.add_argument("context_id"); a.add_argument("--version", default="")
    sp.set_defaults(fn=cmd_context)

    sp = sub.add_parser("objective", help="GTM OS：运营目标与主结果指标契约")
    osub = sp.add_subparsers(dest="action", required=True)
    a = osub.add_parser("create"); a.add_argument("--file", required=True); a.add_argument("--by", default="operator")
    a = osub.add_parser("list")
    a = osub.add_parser("show"); a.add_argument("objective_id"); a.add_argument("--version", default="")
    sp.set_defaults(fn=cmd_objective)

    sp = sub.add_parser("work", help="GTM OS：WorkOrder 创建、校验、ready、状态与下一步")
    wsub = sp.add_subparsers(dest="action", required=True)
    a = wsub.add_parser("create"); a.add_argument("--file", required=True); a.add_argument("--by", default="operator")
    a = wsub.add_parser("list")
    a = wsub.add_parser("show"); a.add_argument("--id", required=True)
    a = wsub.add_parser("lint"); a.add_argument("--id", default=""); a.add_argument("--file", default="")
    a = wsub.add_parser("ready"); a.add_argument("--id", required=True); a.add_argument("--by", default="operator")
    a = wsub.add_parser("transition"); a.add_argument("--id", required=True); a.add_argument("--to", required=True)
    a.add_argument("--by", default="operator"); a.add_argument("--reason", default="")
    a = wsub.add_parser("next")
    sp.set_defaults(fn=cmd_work)

    sp = sub.add_parser("sop", help="GTM OS：版本化专业 SOP 与证据门运行器")
    ssub = sp.add_subparsers(dest="action", required=True)
    a = ssub.add_parser("list")
    a = ssub.add_parser("show"); a.add_argument("sop_id"); a.add_argument("--version", default="")
    a = ssub.add_parser("lint"); a.add_argument("--file", required=True)
    a = ssub.add_parser("register"); a.add_argument("--file", required=True); a.add_argument("--by", default="operator")
    a = ssub.add_parser("start"); a.add_argument("--work-order-id", required=True); a.add_argument("--by", default="operator")
    a = ssub.add_parser("run"); a.add_argument("--run-id", required=True)
    a = ssub.add_parser("complete"); a.add_argument("--run-id", required=True); a.add_argument("--step-id", required=True)
    a.add_argument("--outputs", required=True, help="步骤产物 JSON 数组文件")
    a.add_argument("--dod", required=True, help="DoD 结果 JSON 数组文件")
    a.add_argument("--by", default="operator"); a.add_argument("--approval-by", default=""); a.add_argument("--approval-scope", default="")
    a = ssub.add_parser("block"); a.add_argument("--run-id", required=True); a.add_argument("--step-id", required=True)
    a.add_argument("--reason", required=True); a.add_argument("--by", default="operator")
    a = ssub.add_parser("reopen"); a.add_argument("--run-id", required=True); a.add_argument("--step-id", required=True)
    a.add_argument("--reason", required=True); a.add_argument("--by", default="operator")
    sp.set_defaults(fn=cmd_sop)

    sp = sub.add_parser("deliverable", help="GTM OS：模板交付物、提交、独立验收与修订")
    dsub = sp.add_subparsers(dest="action", required=True)
    a = dsub.add_parser("create"); a.add_argument("--file", required=True); a.add_argument("--by", default="operator")
    a = dsub.add_parser("list")
    a = dsub.add_parser("show"); a.add_argument("--id", required=True); a.add_argument("--version", default="")
    a = dsub.add_parser("submit"); a.add_argument("--id", required=True); a.add_argument("--version", default=""); a.add_argument("--by", default="operator")
    a = dsub.add_parser("review"); a.add_argument("--id", required=True); a.add_argument("--version", default="")
    a.add_argument("--results", required=True, help="逐项 rubric 审核结果 JSON 数组"); a.add_argument("--by", required=True)
    a = dsub.add_parser("revise"); a.add_argument("--id", required=True); a.add_argument("--fields", required=True); a.add_argument("--by", default="operator")
    sp.set_defaults(fn=cmd_deliverable)

    sp = sub.add_parser("handoff", help="GTM OS：跨岗位交接、接收、拒收与 SLA")
    hsub = sp.add_subparsers(dest="action", required=True)
    a = hsub.add_parser("create"); a.add_argument("--file", required=True); a.add_argument("--by", default="operator")
    a = hsub.add_parser("list")
    a = hsub.add_parser("show"); a.add_argument("--id", required=True)
    a = hsub.add_parser("pending"); a.add_argument("--now", default="")
    a = hsub.add_parser("accept"); a.add_argument("--id", required=True); a.add_argument("--by", required=True)
    a = hsub.add_parser("reject"); a.add_argument("--id", required=True); a.add_argument("--by", required=True)
    a.add_argument("--reason", required=True); a.add_argument("--return-step", required=True)
    sp.set_defaults(fn=cmd_handoff)

    sp = sub.add_parser("metric", help="GTM OS：版本化指标字典与确定性测量")
    msub = sp.add_subparsers(dest="action", required=True)
    a = msub.add_parser("list")
    a = msub.add_parser("show"); a.add_argument("metric_id"); a.add_argument("--version", default="")
    a = msub.add_parser("register"); a.add_argument("--file", required=True); a.add_argument("--by", default="operator")
    a = msub.add_parser("measure"); a.add_argument("--metric-id", required=True); a.add_argument("--version", default="")
    a.add_argument("--events", required=True, help="事件 JSON 数组或 JSONL")
    a.add_argument("--campaign", default=""); a.add_argument("--subject", default="")
    a.add_argument("--since", default=""); a.add_argument("--until", default="")
    a.add_argument("--cohort-end", default=""); a.add_argument("--as-of", default="")
    sp.set_defaults(fn=cmd_metric)

    sp = sub.add_parser("work-action", help="GTM OS：accepted 交付物→既有 plan/queue；不发送")
    asub = sp.add_subparsers(dest="action", required=True)
    a = asub.add_parser("materialize")
    a.add_argument("--work-order-id", required=True); a.add_argument("--step-id", required=True)
    a.add_argument("--deliverable-id", required=True); a.add_argument("--deliverable-version", default="")
    a.add_argument("--strategy-file", required=True); a.add_argument("--targets", required=True)
    a.add_argument("--campaign", required=True); a.add_argument("--account", default="")
    a.add_argument("--execution-workspace", default=""); a.add_argument("--by", default="operator")
    sp.set_defaults(fn=cmd_work_action)

    sp = sub.add_parser("playbook-model", help="Playbook 组合：Platform × Motion × Vertical")
    sp.add_argument("action", choices=["list", "compose"])
    sp.add_argument("--platform", default="")
    sp.add_argument("--motion", default="")
    sp.add_argument("--vertical", default="")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_playbook_model)

    sp = sub.add_parser("evaluate", help="能力/竞争/进化评估：同条件对照与外部未见任务")
    sp.add_argument("action", choices=["compare", "evolution", "external"])
    sp.add_argument("--ours", default=""); sp.add_argument("--alternative", default="")
    sp.add_argument("--previous", default=""); sp.add_argument("--current", default="")
    sp.add_argument("--file", default="")
    sp.set_defaults(fn=cmd_evaluate)

    sp = sub.add_parser("journey", help="用户旅程：同 campaign/subject/time_window 的断点诊断")
    sp.add_argument("--events", required=True, help="事件 JSON 或 JSONL")
    sp.add_argument("--campaign", required=True)
    sp.add_argument("--subject", default="")
    sp.add_argument("--identity-map", default="", help="可选身份别名→规范主体 JSON；不猜测合并身份")
    sp.add_argument("--since", required=True); sp.add_argument("--until", required=True)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_journey)

    sp = sub.add_parser("revise", help="优化回流：改哪个参数、从多少到多少")
    sp.add_argument("--result", default="", help="人工钉定的结果 JSON（复盘上一轮时用）")
    sp.add_argument("--params", default="", help="当前参数 JSON")
    sp.add_argument("--campaign", default=""); sp.add_argument("--subject", default="")
    sp.add_argument("--since", default=""); sp.add_argument("--until", default="")
    sp.add_argument("--intent", default="")
    sp.set_defaults(fn=cmd_revise)

    sp = sub.add_parser("execute", help="执行桥：策略+名单→计划与队列（人审后才发）")
    sp.add_argument("--strategy-file", required=True, help="strategy build --json 的输出")
    sp.add_argument("--targets", required=True, help="CSV，列：target,body[,subject]")
    sp.add_argument("--campaign", required=True)
    sp.add_argument("--account", default="", help="执行账号；默认 default")
    sp.add_argument("--workspace", default="", help="执行工作区；默认当前 MKT_HOME")
    sp.add_argument("--criteria", default="")
    sp.set_defaults(fn=cmd_execute)

    sp = sub.add_parser("progress", help="进度：日报/周报")
    psub = sp.add_subparsers(dest="action", required=True)
    a = psub.add_parser("daily", help="今天该关心什么")
    a.add_argument("--on", default=""); a.add_argument("--intent", default="")
    a = psub.add_parser("weekly", help="这一周比上一周好还是差")
    a.add_argument("--on", default=""); a.add_argument("--intent", default="")
    sp.set_defaults(fn=cmd_progress)

    sp = sub.add_parser("retro", help="复盘：扫描本地日志→事实→渠道账→业务结论")
    rsub = sp.add_subparsers(dest="action", required=True)
    a = rsub.add_parser("scan", help="扫描目录，提取带出处的事实")
    a.add_argument("--path", required=True, help="要扫描的目录")
    a.add_argument("--out", default="")
    a.add_argument("--max-files", type=int, default=20000)
    a = rsub.add_parser("analyze", help="看渠道账")
    a.add_argument("--scan", default="")
    a = rsub.add_parser("report", help="生成业务复盘报告（需要人工钉定的口径文件）")
    a.add_argument("--facts", required=True, help="口径文件 JSON：{渠道: {动作,回应,结果}}")
    a.add_argument("--path", default="")
    a.add_argument("--scan", default="")
    a.add_argument("--out", default="")
    a.add_argument("--max-files", type=int, default=20000)
    sp.set_defaults(fn=cmd_retro)

    sp = sub.add_parser("followup", help="跟进：说过话的人、到期提醒、三次原则")
    fsub = sp.add_subparsers(dest="action", required=True)
    a = fsub.add_parser("add", help="登记一个刚接触过的对象")
    a.add_argument("--platform", required=True)
    a.add_argument("--target", required=True, help="账号/邮箱/仓库")
    a.add_argument("--source", default="", help="从哪条讨论来的")
    a.add_argument("--note", default="")
    a.add_argument("--channel", default="")
    a.add_argument("--campaign", default="", help="可选活动归属；结果投影按此隔离")
    a.add_argument("--on", default="", help="日期 YYYY-MM-DD，默认今天")
    a = fsub.add_parser("due", help="今天该跟谁")
    a.add_argument("--on", default=""); a.add_argument("--json", action="store_true")
    a = fsub.add_parser("list", help="全部对象")
    a.add_argument("--stage", default=""); a.add_argument("--json", action="store_true")
    a = fsub.add_parser("touch", help="记一次跟进结果")
    a.add_argument("id")
    a.add_argument("--result", required=True,
                   choices=["no_reply", "replied", "asked", "used", "declined"])
    a.add_argument("--note", default=""); a.add_argument("--channel", default="")
    a.add_argument("--campaign", default="", help="可选活动归属；默认沿用对象所属活动")
    a.add_argument("--on", default="")
    a = fsub.add_parser("stats", help="跟进健康度")
    a.add_argument("--on", default=""); a.add_argument("--json", action="store_true")
    a = fsub.add_parser("close", help="人工关闭")
    a.add_argument("id"); a.add_argument("--reason", default="")
    sp.set_defaults(fn=cmd_followup)

    sp = sub.add_parser("intent", help="意图信号学习实验：可配置周期与观察指标，不自动判死渠道")
    isub = sp.add_subparsers(dest="action", required=True)
    a = isub.add_parser("start", help="开一个可配置学习实验")
    a.add_argument("id"); a.add_argument("--platform", default="reddit")
    a.add_argument("--subs", default="", help="逗号分隔的目标 sub")
    a.add_argument("--owner", default=""); a.add_argument("--on", default="")
    a.add_argument("--weeks", type=int, default=4, help="学习周期，默认 4 周")
    a.add_argument("--weekly-target", type=int, default=None, help="可选每周观察目标，不设为产品生死门")
    a = isub.add_parser("log", help="记一周的触达数")
    a.add_argument("id"); a.add_argument("--sent", type=int, default=0)
    a.add_argument("--deleted", type=int, default=0); a.add_argument("--replied", type=int, default=0)
    a.add_argument("--moved", type=int, default=0); a.add_argument("--note", default="")
    a.add_argument("--on", default="")
    a = isub.add_parser("show", help="看进度与配额"); a.add_argument("id"); a.add_argument("--on", default="")
    a = isub.add_parser("verdict", help="输出学习观察与安全异常，不给渠道生死判决")
    a.add_argument("id"); a.add_argument("--on", default="")
    sp.set_defaults(fn=cmd_intent)

    sp = sub.add_parser("account", help="授权账号资产：登记/养号/健康度/人工接管/审计")
    asub = sp.add_subparsers(dest="action", required=True)
    for name in ("add", "list", "check", "warm", "eligible", "pause", "takeover", "resolve", "audit"):
        a = asub.add_parser(name)
        a.add_argument("--account-id", default="")
        a.add_argument("--platform", default="")
        a.add_argument("--handle", default="")
        a.add_argument("--identity-type", default="real_authorized")
        a.add_argument("--owner", default="")
        a.add_argument("--ownership-evidence", default="")
        a.add_argument("--authorization-evidence", default="")
        a.add_argument("--multi-account-allowed", action="store_true")
        a.add_argument("--multi-account-evidence", default="")
        a.add_argument("--disclosure", default="")
        a.add_argument("--status", default="")
        a.add_argument("--reason", default="")
        a.add_argument("--evidence", default="")
        a.add_argument("--by", default="")
        a.add_argument("--note", default="")
    sp.set_defaults(fn=cmd_account)

    sp = sub.add_parser("network", help="代理环境引用绑定：不读凭据、不提高配额、不绕封禁")
    nsub = sp.add_subparsers(dest="action", required=True)
    for name in ("bind", "list", "unbind", "audit", "export"):
        a = nsub.add_parser(name)
        a.add_argument("--account-id", required=(name == "bind"))
        a.add_argument("--env-var", default="")
        a.add_argument("--platform", default="")
        a.add_argument("--purpose", default="authorized_access")
        a.add_argument("--reason", default="")
    sp.set_defaults(fn=cmd_network)

    sp = sub.add_parser("billing", help="报价实验：动作/结果/订阅/混合")
    sp.add_argument("--experiment-id", default="")
    sp.add_argument("--record-acceptance", choices=["accepted", "rejected", "pending", "unknown"], default="")
    sp.add_argument("--prospect", default="")
    sp.add_argument("--reason", default="")
    sp.add_argument("--by", default="")
    sp.add_argument("--summary", action="store_true")
    sp.add_argument("--actions", type=int, default=0, help="动作单元数")
    sp.add_argument("--outcomes", type=int, default=0, help="客户系统可验证结果数")
    sp.add_argument("--pricing", default="", help="定价 JSON，覆盖默认值")
    sp.add_argument("--from-files", action="store_true", help="从账本与结果事件统计")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_billing)

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

    sp = sub.add_parser("scorecard", help="运营记分卡：执行回执与业务结果的描述性口径")
    sp.add_argument("--campaign"); sp.add_argument("--since", help="起始时间 ISO 前缀")
    sp.add_argument("--output", help="可选：导出 Markdown")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_scorecard)

    sp = sub.add_parser("gtm", help="GTM 策略：diagnose 事实包→平台优先级；design 对象×目标×机制×渠道")
    gsub = sp.add_subparsers(dest="gtm_cmd")
    dg = gsub.add_parser("diagnose", help="GTM 诊断：事实包→平台优先级+冷启动+放量判据")
    dg.add_argument("--facts", required=True, help="产品事实包 JSON（schema 见 core/gtm.py 模块文档）")
    dg.add_argument("--output", help="可选：导出 Markdown")
    dg.add_argument("--json", action="store_true")
    dg = gsub.add_parser("design", help="GTM 设计：对象/目标/机制/渠道 + 结果契约（不执行）")
    dg.add_argument("task", help="增长任务原话，例如「增长一个小红书摄影账号」")
    dg.add_argument("--object", default="", help="显式对象：software_saas/content_account/hardware/oss_project/service/b2b")
    dg.add_argument("--objective", default="",
                    help="显式目标：awareness/acquisition/activation/revenue/retention/contribution")
    dg.add_argument("--constraints", default="",
                    help="约束 JSON 文件或内联 JSON：budget/region/authorized_channels/assets/data_sources")
    dg.add_argument("--proposal", default="",
                    help="Agent 候选 JSON：{objective, motions:[{id,why}], channels:[…]}")
    dg.add_argument("--json", action="store_true")
    dg.add_argument("--out", default="", help="可选：保存 Markdown")
    dg.add_argument("--save", default="", help="可选：保存设计 JSON，供 handoff 使用")
    dg = gsub.add_parser("lint", help="校验一份 GTM 设计 JSON 是否自洽")
    dg.add_argument("file")
    dg.add_argument("--json", action="store_true")
    dg = gsub.add_parser("handoff", help="GTM 交接：设计 → 候选岗位编排 + 目标契约映射")
    dg.add_argument("design", help="gtm design --save 生成的 JSON")
    dg.add_argument("--product", default="", help="产品事实包 JSON；不提供则只返回契约与命令")
    dg.add_argument("--json", action="store_true")
    dg.add_argument("--out", default="", help="可选：保存 Markdown")
    dg = gsub.add_parser("taxonomy", help="列出对象/目标/机制词表")
    dg.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_gtm)

    sp = sub.add_parser("goal", help="目标：先定这一轮要什么（声量/使用/商业），再选平台")
    gsub = sp.add_subparsers(dest="action", required=True)
    a = gsub.add_parser("list", help="列出这个产品可选的目标与推荐平台")
    a.add_argument("--facts", required=True, help="产品事实包 JSON（同 mkt gtm diagnose）")
    a.add_argument("--json", action="store_true")
    a.add_argument("--out", default="")
    a = gsub.add_parser("plan", help="生成一个目标（+平台）的完整方案")
    a.add_argument("--facts", required=True)
    a.add_argument("--goal", required=True, help="目标 id，例如 commercial.first_paid")
    a.add_argument("--platforms", default="", help="逗号分隔：主渠道,发射点,辅助1,辅助2；不填用推荐组合")
    a.add_argument("--target", type=float, default=None, help="覆盖默认目标；不填用建议形状")
    a.add_argument("--window-days", type=int, default=None)
    a.add_argument("--secondary", default="", help="可选辅目标 id，例如 adoption.real_data")
    a.add_argument("--secondary-target", type=float, default=None)
    a.add_argument("--json", action="store_true")
    a.add_argument("--out", default="")
    a = gsub.add_parser("select", help="写入「目标 + 平台」选择契约，供 strategy/cadence 使用")
    a.add_argument("--facts", required=True)
    a.add_argument("--goal", required=True)
    a.add_argument("--platforms", required=True, help="逗号分隔：主渠道,发射点,辅助1,辅助2")
    a.add_argument("--target", type=float, default=None)
    a.add_argument("--window-days", type=int, default=None)
    a.add_argument("--secondary", default="", help="可选辅目标 id")
    a.add_argument("--secondary-target", type=float, default=None)
    a.add_argument("--note", default="")
    a.add_argument("--out", required=True)
    a.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_goal)

    sp = sub.add_parser("sprint", help="冲刺：把目标方案压成 N 天并行工作流与小时级排期")
    ssub = sp.add_subparsers(dest="action", required=True)
    a = ssub.add_parser("plan", help="生成 3 天/7 天冲刺方案（只读）")
    a.add_argument("--facts", required=True, help="产品事实包 JSON")
    a.add_argument("--goal", default="", help="目标 id；用 --selection 时可省")
    a.add_argument("--secondary", default="")
    a.add_argument("--secondary-target", type=float, default=None)
    a.add_argument("--platforms", default="")
    a.add_argument("--target", type=float, default=None)
    a.add_argument("--selection", default="", help="mkt goal select 写出的选择契约")
    a.add_argument("--days", type=int, default=3, help="冲刺天数，默认 3")
    a.add_argument("--start", default="", help="开始日期 YYYY-MM-DD，默认今天")
    a.add_argument("--hours-per-day", type=float, default=8.0)
    a.add_argument("--launch-hour", default="21:00", help="发射时间（北京时间），默认 21:00")
    a.add_argument("--json", action="store_true")
    a.add_argument("--out", default="")
    sp.set_defaults(fn=cmd_sprint)

    from core.conversion import OFFERS as _OFFERS, STAGES as _STAGES
    sp = sub.add_parser("channel", help="渠道：具体地方、规则、经济学、实验")
    csub = sp.add_subparsers(dest="action", required=True)
    a = csub.add_parser("venues", help="列出渠道库；可按平台过滤")
    a.add_argument("--platform", default="")
    a.add_argument("--json", action="store_true")
    a = csub.add_parser("plan", help="生成渠道组合：主/发射/辅助/旁听 + 经济学")
    a.add_argument("--facts", required=True)
    a.add_argument("--selection", default="")
    a.add_argument("--goal", default="")
    a.add_argument("--secondary", default="")
    a.add_argument("--platforms", default="")
    a.add_argument("--json", action="store_true")
    a.add_argument("--out", default="")
    a = csub.add_parser("score", help="给一个渠道打分")
    a.add_argument("--venue", required=True)
    a.add_argument("--facts", required=True)
    a.add_argument("--selection", default="")
    a.add_argument("--goal", default="")
    a.add_argument("--secondary", default="")
    a.add_argument("--platforms", default="")
    a.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_channel)

    sp = sub.add_parser("deal", help="转化：阶段、下一个动作、资格、报价、漏斗")
    dsub = sp.add_subparsers(dest="action", required=True)
    a = dsub.add_parser("add", help="建一个转化对象")
    a.add_argument("--contact", required=True)
    a.add_argument("--name", default="")
    a.add_argument("--campaign", default="")
    a.add_argument("--owner", default="")
    a.add_argument("--note", default="")
    a.add_argument("--json", action="store_true")
    a = dsub.add_parser("advance", help="推进阶段；所有阶段必须有 evidence；qualified 先过资格，paid 可附独立核验")
    a.add_argument("--id", required=True)
    a.add_argument("--stage", required=True, choices=_STAGES)
    a.add_argument("--by", default="roy")
    a.add_argument("--evidence", default="")
    a.add_argument("--verify-method", default="", help="paid 可选：独立核验方式；与 --verify-ref 成对")
    a.add_argument("--verify-ref", default="", help="paid 可选：独立核验记录；与 --verify-method 成对")
    a.add_argument("--note", default="")
    a.add_argument("--json", action="store_true")
    a = dsub.add_parser("qualify", help="五项资格打分")
    a.add_argument("--id", required=True)
    a.add_argument("--trigger", action="store_true")
    a.add_argument("--contactable", action="store_true")
    a.add_argument("--data", action="store_true")
    a.add_argument("--cost", action="store_true")
    a.add_argument("--price", action="store_true")
    a.add_argument("--json", action="store_true")
    a = dsub.add_parser("list", help="列出对象")
    a.add_argument("--stage", choices=_STAGES, default="")
    a.add_argument("--json", action="store_true")
    a = dsub.add_parser("funnel", help="漏斗与卡住的对象")
    a.add_argument("--on", default="")
    a.add_argument("--json", action="store_true")
    a = dsub.add_parser("next", help="今天该跟谁")
    a.add_argument("--on", default="")
    a.add_argument("--json", action="store_true")
    a = dsub.add_parser("quote", help="生成一页 SOW")
    a.add_argument("--id", required=True)
    a.add_argument("--offer", choices=sorted(_OFFERS), default="diagnostic")
    a.add_argument("--out", default="")
    a.add_argument("--json", action="store_true")
    a = dsub.add_parser("objections", help="异议库")
    a.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_deal)

    sp = sub.add_parser("optimize", help="按记分卡与运营记忆给出人工决策建议（不自动执行）")
    sp.add_argument("--campaign"); sp.add_argument("--channel")
    sp.add_argument("--since"); sp.add_argument("--minimum-confirmed", type=int, default=3)
    sp.add_argument("--output", help="可选：导出 Markdown")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_optimize)

    sp = sub.add_parser("schedule", help="持久化队列排期：到点跑一波，暂停/崩溃即停")
    sp.add_argument("action", choices=["add", "list", "tick", "status", "resume"])
    sp.add_argument("--id"); sp.add_argument("--queue", action="append")
    sp.add_argument("--interval", type=int, default=1440, help="分钟，默认 1440")
    sp.add_argument("--max-waves", type=int)
    sp.add_argument("--go", action="store_true", help="排期波次实跑（默认 dry-run）")
    sp.add_argument("--note"); sp.add_argument("--by", default="operator")
    sp.set_defaults(fn=cmd_schedule)

    sp = sub.add_parser("workflow", help="可复用工作流：学习/审批/重放（版本化 + 指纹 + 运行日志）")
    sp.add_argument("action", choices=["list", "show", "lint", "learn", "approve", "replay", "runs", "reconcile"])
    sp.add_argument("workflow_id", nargs="?")
    sp.add_argument("--trace", help="observe 轨迹 JSON（learn 用）")
    sp.add_argument("--name", help="workflow 名称（learn 用）")
    sp.add_argument("--by", default="operator", help="审批/裁决人")
    sp.add_argument("--days", type=int, default=30, help="审批有效期（默认 30 天）")
    sp.add_argument("--note", help="审批/裁决说明")
    sp.add_argument("--cdp-url", help="UI 步骤使用的调试浏览器地址")
    sp.add_argument("--campaign", help="重放归属 campaign")
    sp.add_argument("--go", action="store_true", help="实跑（默认 dry-run，只解析定位器）")
    sp.add_argument("--var", action="append", help="模板变量 key=value，可重复")
    sp.add_argument("--run", help="运行 id（reconcile 用）")
    sp.add_argument("--outcome", choices=["sent", "not_sent"], help="裁决结果")
    sp.set_defaults(fn=cmd_workflow)

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
