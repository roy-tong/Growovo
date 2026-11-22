#!/usr/bin/env python3
"""mkt —— Growovo 执行内核统一入口（架构文档 §1）。

用法:
  mkt <channel> <action> [参数] [--go] [--campaign <id>] [--only <target>] [--adhoc]

统一语义：**写操作默认 dry-run，--go 才实跑**；实跑须绑定已人审 plan，或显式 --routine
且工作区 routine policy 覆盖该账号/渠道/动作。无 plan 管辖的临时发送仍可显式
**--adhoc** 放行（账本记 adhoc=true；MCP 不开放该通道，也不开放授权管理）。
  mkt status                账本聚合（channel/campaign/result）
  mkt check                 CDP 会话与各渠道登录态检查
  mkt demo                  零网络零账号演示（lint→人审→成功投递→三道拦截→账本/日结果卡）
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
  mkt grow events|layers|journey|qualify|goal-exit
                            三层结果（G-03）：授权产品事件导入/三层汇总/个人进度/人工改资格
                            （导入与改资格默认只预演，--apply 才落盘；其余只读）
  mkt grow goal-exit …      目标退出与个体旅程（G-NEXT-2）：契约/评估/停止记录
                            （写入默认预演，--apply 才落盘；退出不提供解除）
  mkt grow signal …         信号证据包（G-NEXT-3）：登记/撤销/视图（写入默认预演）
  mkt grow preview …        变更预演（G-NEXT-3）：快照/检查/清单（只读，不批准不执行）
  mkt grow experiment …     受控实验（G-04）：设计/批准/启动/观察/分析（写入默认 dry-run）
  mkt grow change …         策略改动（G-04）：清单/人审批准/回滚（升权改动未批准不生效）
  mkt grow start …          九步启动向导（G-05）：体检→清单→身份→目标→3 条预览→风险→
                           显式 arm→调度模式→日报；不自动 arm，写入要 --apply
  mkt grow status --explain|incident --list|resume --after-reconciliation|pause
                            统一监控与恢复（G-05）：C-01 状态解释/唯一事件 ID 告警/
                            indeterminate 裁决后恢复/一键暂停

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


def _load_facts(path, what="产品事实包"):
    """读 facts JSON；文件缺失/格式错给可行动的报错，而不是裸 traceback。"""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SystemExit(f"找不到{what}文件：{p}\n"
                         "样例：data/verovo-finops/facts.json"
                         "（product/icp/baseline/goals/value_events/constraints/owned_assets）")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(f"{what}文件不是有效 JSON：{p}（第 {e.lineno} 行: {e.msg}）")


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
        from core.session_gate import LOGGED_IN, LOGGED_OUT, check_login
        names = {"x": "X", "gmail": "Gmail", "reddit": "Reddit",
                 "linkedin": "LinkedIn", "hn": "HN", "zhihu": "知乎"}
        results = []
        for key in names:
            state = check_login(key, connect=lambda: (browser, page))
            status = state.get("status")
            label = "OK" if status == LOGGED_IN else ("未登录" if status == LOGGED_OUT else "检查失败")
            results.append((names[key], label))
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
        inline = args.on if isinstance(args.on, str) else ""
        set_pause(DATA, True, reason=args.reason or inline or "")
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
        try:
            rec = c.add(**json.loads(args.json))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            print(f"不能加入线索库：{exc}")
            if "unexpected keyword" in str(exc):
                print("platform 不是顶层字段：平台账号要放进 platforms 映射，"
                      "例如 {\"platforms\":{\"x\":\"alice\",\"email\":\"alice@example.com\"}}")
            print("字段示例：--json '{\"name\":\"Alice\",\"platforms\":{\"x\":\"alice\"}}'")
            return 2
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
        try:
            c.mark(args.contact_id, dnc=True)
        except ValueError as exc:
            print(f"不能标记 DNC：{exc}")
            return 2
        print(f"{args.contact_id} 已标记 DNC")
    elif args.action == "mark":
        try:
            updates = json.loads(args.json)
            rec = c.mark(args.contact_id, **updates)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            print(f"不能更新线索：{exc}")
            return 2
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



def cmd_tactic(args):
    from core import tactics as reg
    from core.tactic_run import TacticRun, TacticRuleError, check_candidate
    from core.gates import GateError
    from core.queue import lint_queue
    act = args.action
    path = DATA / "tactics" / f"{args.id}.json" if getattr(args, "id", "") else None

    if act == "list":
        rows = reg.suggest(channel=args.channel or "", mechanism=args.mechanism or "",
                           risk_max=args.risk_max, status=args.status or "",
                           include_stale=getattr(args, "include_stale", False))
        if not rows:
            print("没有符合条件的战术（放宽 --risk-max 或加 --include-stale 再试）")
            return 0
        for t in rows:
            mech = reg.MECHANISMS[t["mechanism"]]["name"]
            print(f"- {t['id']}：{t['name']}（{t['channel']}｜{mech}｜{t['risk']}｜{t['status']}）")
            print(f"  {t['idea']}")
        print(f"\n共 {len(rows)} 条。看参数：mkt tactic show <id>；开跑：mkt tactic start <run> --tactic <id>")
        return 0

    if act == "show":
        spec = reg.spec(args.id)
        if spec is None:
            print(f"没有这个战术：{args.id}（先 mkt tactic list）")
            return 2
        mech = reg.MECHANISMS[spec["mechanism"]]
        print(f"{spec['name']}（{spec['id']}）")
        print(f"  渠道 {spec['channel']}｜机制 {mech['name']}｜风险 {spec['risk']}｜状态 {spec['status']}")
        print(f"  做法：{spec['idea']}")
        for i, s in enumerate(spec["steps"], 1):
            print(f"    {i}. {s}")
        if spec.get("quota"):
            print(f"  配额：{json.dumps(spec['quota'], ensure_ascii=False)}")
        if spec.get("formulas"):
            print(f"  内容公式：{'、'.join(spec['formulas'])}")
        if spec.get("verdict_metrics"):
            print(f"  判定指标：{'、'.join(spec['verdict_metrics'])}")
        for e in spec["evidence"]:
            print(f"  证据：{e}")
        print(f"  注意：{spec['caution']}")
        print(f"  证据日期 {spec['evidence_date']}｜{spec['revalidate_days']} 天内须复核"
              "（机会随采用人数衰减，到期先复核再用）")
        return 0

    if act == "start":
        data = TacticRun.start(path, args.id, args.tactic, owner=args.owner or "",
                               started=args.on or None, weeks=args.weeks,
                               target_list=getattr(args, "target_list", "") or "")
        print(f"战术执行已建：{data['id']}（{data['tactic']['name']}，"
              f"{data['started']} → {data['ends']}）")
        if data.get("target_list"):
            print(f"  关联目标清单：{data['target_list']}（mkt target summary 看信任节点进度）")
        print("  第 1 周是准备周：建目标清单、过账号门槛，先记录不行动")
        return 0

    if act == "draft":
        run = TacticRun(path)
        run.add_draft(args.target, args.text, formula=args.formula or "",
                      post_age_min=args.post_age, hour=args.hour, on=args.on or None)
        data = run.load()
        pending = sum(1 for e in data.get("drafts") or []
                      for it in e.get("items") or [] if not it.get("queued"))
        print(f"候选已入（{args.id}）：待导出 {pending} 条"
              + (f"｜纪律提醒 {len(data['violations'])} 条（mkt tactic show-run 查看）"
                 if data["violations"] else ""))
        return 0

    if act == "check":
        spec = reg.spec(args.tactic)
        if spec is None:
            print(f"没有这个战术：{args.tactic}")
            return 2
        try:
            warns = check_candidate(spec, target=args.target, body=args.text,
                                    formula=args.formula or "", post_age_min=args.post_age)
        except (GateError, TacticRuleError, ValueError) as exc:
            print(f"拒绝：{exc}")
            return 2
        print("通过：可以加入候选（mkt tactic draft）")
        for w in warns:
            print(f"  提醒：{w}")
        return 0

    if act == "log":
        run = TacticRun(path)
        metrics = None
        if getattr(args, "metrics", ""):
            try:
                metrics = json.loads(args.metrics)
            except json.JSONDecodeError as exc:
                print(f"--metrics 不是合法 JSON：{exc}")
                return 2
        run.log(sent=args.sent or 0, deleted=args.deleted or 0, replied=args.replied or 0,
                moved=args.moved or 0, note=args.note or "", on=args.on or None,
                metrics=metrics)
        s = run.summary(args.on or None)
        print(f"已记录（{args.id}）：累计 {s['sent']} 条，回应率 {s['response_rate']:.0%}"
              f"｜第 {s['week']} 周·{s['phase']}")
        return 0

    if act == "show-run":
        run = TacticRun(path)
        s = run.summary(args.on or None)
        print(f"{args.id}（{s['tactic']}）｜第 {s['week']} 周·{s['phase']}：{s['todo']}")
        print(f"  触达 {s['sent']}｜被删 {s['deleted']}（{s['deleted_rate']:.0%}）"
              f"｜回应 {s['replied']}（{s['response_rate']:.0%}）｜转一对一 {s['moved']}")
        print(f"  待导出候选 {s['drafts_pending']} 条｜纪律提醒 {s['violations']} 条")
        if s.get("targets"):
            t = s["targets"]
            print(f"  目标清单 {t['list']}：KOC 甜区 {t['koc_window']}/{t['total']}"
                  f"｜近 7 天互动 {t['engaged_last_7d']}｜累计再传播 {t['amplified_total']}")
        if s.get("last_metrics"):
            m = ", ".join(f"{k}={v}" for k, v in s["last_metrics"].items() if k != "on")
            print(f"  周指标：{m}")
        if s.get("ledger_sync"):
            print(f"  账本核对：committed {s['ledger_sync']['committed_total']} 条"
                  f"（campaign {s['ledger_sync']['campaign']}）")
        if s["violations"]:
            for v in run.load()["violations"][-3:]:
                print(f"  ⚠ [{v['on']}] {v['detail'][:80]}")
        return 0

    if act == "verdict":
        v = TacticRun(path).verdict(args.on or None)
        print(f"判定：{v['verdict']}")
        print(f"  {v['reason']}")
        print(f"  数据：触达 {v['sent']}｜被删率 {v['deleted_rate']:.0%}"
              f"｜回应率 {v['response_rate']:.0%}｜转一对一 {v['moved']}｜纪律提醒 {v['violations']}")
        return 0

    if act == "export":
        run = TacticRun(path)
        try:
            queue = run.as_queue_items(args.campaign or "")
        except TacticRuleError as exc:
            print(f"不能导出：{exc}")
            return 2
        errs = lint_queue(queue)
        if errs:
            print("队列未通过编辑期检查，不落盘：")
            for e in errs:
                print(f"  - {e}")
            return 2
        out = DATA / "queues" / f"{queue['campaign']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(queue, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        print(f"队列已写：{out}（{len(queue['items'])} 条，已过编辑期检查）")
        print("  发送走既有路径：人审与 dry-run 之后再实跑（mkt queue run 先看 dry-run）")
        return 0

    if act == "sync":
        try:
            summary = TacticRun(path).sync_from_ledger(DATA)
        except TacticRuleError as exc:
            print(f"不能核对：{exc}")
            return 2
        print(f"账本核对：campaign {summary['campaign']} 共 committed {summary['committed_total']} 条")
        for d, n in sorted(summary["by_date"].items()):
            print(f"  {d}：{n}")
        return 0

    if act == "stale":
        rows = reg.stale(args.on or None)
        if not rows:
            print("没有到期战术：全部在复核期内")
            return 0
        for r in rows:
            print(f"- {r['id']}（{r['name']}）：证据 {r['evidence_date']}，"
                  f"已 {r['days_since']} 天未复核（配 {r['revalidate_days']} 天）")
        print("到期战术先复核证据再继续用：机会随采用人数衰减，老参数可能已失效")
        return 0

    if act == "forbidden":
        for f in reg.FORBIDDEN:
            print(f"- {f['name']}：{f['why']}")
            print(f"  证据：{f['evidence']}")
        return 0

    if act == "recommend":
        facts_file = Path(args.facts)
        if not facts_file.is_file():
            print(f"找不到产品事实包：{facts_file}")
            return 2
        facts = json.loads(facts_file.read_text(encoding="utf-8"))
        rec = reg.recommend(facts, args.goal or "", phase=args.phase,
                            risk_max=args.risk_max, max_picks=args.top,
                            include_stale=args.include_stale)
        print(f"战术组合（目标族 {rec['family']}｜阶段 {rec['phase']}｜风险上限 {rec['risk_max']}）：")
        for i, p in enumerate(rec["picks"], 1):
            print(f"{i}. {p['name']}（{p['id']}｜{p['mechanism_name']}｜{p['channel']}｜{p['risk']}）")
            print(f"   {p['idea']}")
            print(f"   入选理由：{'；'.join(p['why'])}")
        for n in rec["notes"]:
            print(f"  注：{n}")
        print("看全文与执行参数：mkt tactic show <id>；开跑：mkt tactic start <run> --tactic <id>")
        return 0

    if act == "lint":
        errs = reg.lint()
        if errs:
            for e in errs:
                print(e)
            return 2
        print(f"注册表自检通过：{len(reg.TACTICS)} 条战术、{len(reg.MECHANISMS)} 个机制族、"
              f"{len(reg.FORBIDDEN)} 条已证伪做法")
        return 0
    return 0




def cmd_watch(args):
    from core.watch import (KIND_TACTICS, WatchError, WatchList,
                            local_file_fetcher)
    act = args.action
    path = DATA / "watch" / f"{args.id}.json" if getattr(args, "id", "") else None

    def _local_kwargs():
        """显式本地模式：只给 file:// 用一个只读本地文件的抓取器。"""
        if not getattr(args, "allow_local_file", False):
            return {}
        return {"allow_local_file": True, "file_fetcher": local_file_fetcher}

    if act == "start":
        try:
            WatchList.start(path, args.id, owner=args.owner or "")
        except WatchError as exc:
            print(f"不能创建监控：{exc}")
            return 2
        print(f"监控清单已建：{args.id}")
        print("  加监控项：mkt watch add <id> --url <对手页面> --kind price|status|changelog"
              " --competitor <名称>（加时即抓基线快照）")
        print("  cron 定期跑：mkt watch check <id>（变化自动产出事件并联动承接战术）")
        return 0

    if act == "add":
        try:
            entry = WatchList(path).add(args.url, args.kind, args.competitor,
                                        note=args.note or "",
                                        **_local_kwargs())
        except WatchError as exc:
            print(f"不能加入：{exc}")
            return 2
        print(f"已监控 {entry['competitor']} 的 {entry['kind']} 页"
              f"（基线快照 {entry['baseline_len']} 字符）")
        fetch = entry.get("last_fetch") or {}
        if fetch.get("mode") == "pinned" and fetch.get("pinned_ip"):
            print(f"  抓取：已校验 {len(fetch.get('resolved_ips') or [])} 个地址，"
                  f"连接固定到 {fetch['pinned_ip']}")
        if entry.get("last_final_url") and entry["last_final_url"] != entry["url"]:
            print(f"  实际抓取地址（重定向后）：{entry['last_final_url']}")
        print("  变化时只记 page_content_changed；人工核实后才谈涨价/故障："
              f"{'、'.join(KIND_TACTICS[entry['kind']])}")
        return 0

    if act == "remove":
        try:
            WatchList(path).remove(args.url)
        except WatchError as exc:
            print(f"不能移除：{exc}")
            return 2
        print(f"已移除：{args.url}")
        return 0

    if act == "check":
        result = WatchList(path).check(**_local_kwargs())
        print(f"检查 {result['checked']} 项：{len(result['events'])} 个变化"
              + (f"｜抓取失败 {result['failed']} 项（不改基线）"
                 if result["failed"] else ""))
        for e in result["events"]:
            print(f"  ⚡ [{e['kind']}] {e['competitor']}（{e['on']}）："
                  f"{e['diff'] or '内容已变化'}")
            print(f"     事件：{e['event_type']}｜{e['verification']}"
                  "（人工核实前不宣称涨价/故障）")
            print(f"     候选承接：{'、'.join(e['tactics'])}（mkt tactic show 看打法）")
            print(f"     核实：mkt watch confirm {args.id} {e['id']} "
                  "--claim price_change|status_incident|rejected --by <谁> --note <依据>")
        if not result["events"]:
            print("  无实质变化；下次 cron 再查")
        return 0

    if act == "events":
        rows = WatchList(path).events(limit=args.n or 20)
        if not rows:
            print("还没有变化事件")
            return 0
        for e in rows:
            state = e.get("verification", "needs_review")
            claim = e.get("claim") or "content_changed"
            print(f"- [{e['on']}] {e['competitor']} {e['kind']}"
                  f"｜{state}/{claim}：{e['diff'] or '变化'}"
                  f" → {'、'.join(e['tactics'])}")
        return 0

    if act == "confirm":
        try:
            record = WatchList(path).confirm_event(
                args.event_id, claim=args.claim, by=args.by or "",
                note=args.note or "")
        except WatchError as exc:
            print(f"不能记录人工核实：{exc}")
            return 2
        print(f"已记录人工核实：{record['event_id']} → {record['claim']}"
              f"（by {record['by']}）；原事件与快照不改")
        return 0

    if act == "summary":
        s = WatchList(path).summary()
        print(f"监控清单 {s['list']}：{s['entries']} 项（{s['by_kind']}），"
              f"已变化 {s['changed']} 项，累计事件 {s['events_total']} 条")
        return 0
    return 0




def cmd_target(args):
    from core.targeting import TargetList, TargetingError, score_candidate
    act = args.action
    path = DATA / "targets" / f"{args.id}.json" if getattr(args, "id", "") else None

    if act == "start":
        try:
            data = TargetList.start(path, args.id, niche=args.niche or "",
                                    owner=args.owner or "")
        except TargetingError as exc:
            print(f"不能创建清单：{exc}")
            return 2
        print(f"目标清单已建：{data['id']}"
              + (f"（{data['niche']}）" if data["niche"] else ""))
        print("  加入候选：mkt target add <id> --platform x --handle <账号> --followers <数>"
              " [--engagement-rate 0.032] [--sponsored-recent 0] [--niche-match]")
        print("  先打谁由信任价格决定（KOC 甜区=1 千-5 万粉、商单少于 3、互动率高于同档基准）")
        return 0

    if act == "score":
        try:
            s = score_candidate({
                "followers": args.followers,
                "engagement_rate": float(args.engagement_rate) if args.engagement_rate else None,
                "sponsored_recent": args.sponsored_recent if args.sponsored_recent is not None else None,
                "niche_match": args.niche_match or None,   # 不传=未知（1.0），不是不匹配
            })
        except TargetingError as exc:
            print(f"不能评分：{exc}")
            return 2
        print(f"信任价格评分：{s['score']} 分（档位 {s['tier']}）")
        for r in s["reasons"]:
            print(f"  - {r}")
        return 0

    if act == "add":
        tl = TargetList(path)
        try:
            t = tl.add(args.platform, args.handle, args.followers,
                       engagement_rate=float(args.engagement_rate) if args.engagement_rate else None,
                       sponsored_recent=args.sponsored_recent if args.sponsored_recent is not None else None,
                       niche_match=args.niche_match or None,
                       note=args.note or "", on=args.on or None)
        except TargetingError as exc:
            print(f"不能加入：{exc}")
            return 2
        print(f"已入清单 {args.id}：{t['key']}（{t['score']} 分·{t['tier']}）")
        for r in t["reasons"][:2]:
            print(f"  - {r}")
        return 0

    if act == "list":
        data = TargetList(path).load()
        rows = sorted(data["targets"], key=lambda t: (-t["score"], t["key"]))
        for t in rows:
            mark = "★" if t.get("in_koc_window") else " "
            print(f"{mark} {t['score']:>3} {t['key']:<28} {t['stage']:<11} 粉丝 {t['followers']:,}")
        print(f"\n共 {len(rows)} 个（★=KOC 甜区）；下一步接触：mkt target next {args.id}")
        return 0

    if act == "next":
        rows = TargetList(path).next_batch(args.n or 5)
        if not rows:
            print("没有待接触的候选（全部已接触或清单为空）")
            return 0
        for i, t in enumerate(rows, 1):
            print(f"{i}. {t['key']}（{t['score']} 分·{t['tier']}）：{t['reasons'][0] if t['reasons'] else ''}")
        print("接触原则：先给价值（引用其观点并署名/用其内容做案例），共创而非投放")
        return 0

    if act == "advance":
        tl = TargetList(path)
        try:
            t = tl.advance(args.handle, args.stage, note=args.note or "", on=args.on or None)
        except TargetingError as exc:
            print(f"不能推进：{exc}")
            return 2
        print(f"{t['key']} → {t['stage']}（阶段只进不退；engaged/amplified 会进战术判定）")
        return 0

    if act == "summary":
        print(TargetList(path).render_summary(args.on or None))
        return 0
    return 0




def cmd_grow_reply(args):
    """回复导入、人工改判、DNC 登记与统一队列：只登记本地状态，不发送。"""
    from core import reply_case
    act = args.reply_action
    boundary = "边界：只登记本地回复与抑制，不连接邮箱、不自动回复、不自动预约。"

    if act == "import":
        try:
            result = reply_case.import_replies(
                DATA, Path(args.file).expanduser(),
                account_id=args.account,
                channel=args.channel,
                imported_by=args.by,
                authorization_ref=args.authorization,
                run_id=args.run or "",
                ignore_unmapped=bool(args.ignore_unmapped),
                auto_dnc_structured=bool(args.auto_dnc_structured),
                dry_run=bool(args.dry_run),
            )
        except reply_case.ReplyError as exc:
            print("导入失败：%s" % exc, file=sys.stderr)
            return 2
        except OSError as exc:
            print("读不了导入文件：%s" % exc, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1,
                             sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0 if not result.get("errors") else 2
        verb = "预演" if result.get("dry_run") else "导入"
        print("%s完成：批次 %s" % (verb, result.get("batch")))
        print("  新建 %s 条，重复 %s 条，冲突 %s 条"
              % (result.get("would_create") if result.get("dry_run")
                 else result.get("created"),
                 result.get("skipped_duplicate"), result.get("conflicts")))
        _relink_count = (result.get("would_relink")
                         if result.get("dry_run") else result.get("relinked"))
        if _relink_count:
            print("  补关联 %s 条（未关联 → linked）" % _relink_count)
        if result.get("auto_dnc") or result.get("auto_dnc_failed"):
            print("  结构化退订登记 DNC：成功 %s 条，失败 %s 条"
                  % (result.get("auto_dnc"), result.get("auto_dnc_failed")))
        if result.get("unknown_columns"):
            print("  ⚠ 未映射列已忽略：%s"
                  % "、".join(result.get("unknown_columns") or []))
        for warning in result.get("warnings") or []:
            print("  ⚠ %s" % warning)
        for err in result.get("errors") or []:
            print("  ✗ %s" % err, file=sys.stderr)
        print("  统一队列：%s" % result.get("queue_path"))
        print(boundary)
        return 0 if not result.get("errors") else 2

    if act == "link":
        try:
            case = reply_case.link_reply(
                DATA, args.reply_case_id, run_id=args.run,
                action_ref=args.action_ref or "", operator=args.by,
                authorization_ref=args.authorization)
        except reply_case.ReplyError as exc:
            print("补关联失败：%s" % exc, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(case, ensure_ascii=False, indent=1,
                             sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0
        if case.get("already_linked"):
            print("案例已关联 %s，无需重复补登。" % case.get("run_id"))
        else:
            print("已补关联：%s → run %s"
                  % (case.get("reply_case_id"), case.get("run_id")))
            print("  关联动作：%s" % (case.get("action_ref") or "—"))
            print("  链接状态：%s" % case.get("link_state"))
            print("  下一动作：%s（%s）"
                  % (case.get("next_required_action"),
                     case.get("next_actor")))
        print(boundary)
        return 0

    if act == "review":
        try:
            case = reply_case.review_reply(
                DATA, args.reply_case_id, reviewer=args.by,
                label=args.label, note=args.note or "")
        except reply_case.ReplyError as exc:
            print("改判失败：%s" % exc, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(case, ensure_ascii=False, indent=1,
                             sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0
        print("已记录人工改判：%s" % case.get("reply_case_id"))
        print("  候选 %s → 人判 %s（%s）"
              % (case.get("classification_candidate"),
                 case.get("human_label"), case.get("review_state")))
        print("  DNC：%s" % case.get("dnc_state"))
        print("  下一动作：%s（%s）"
              % (case.get("next_required_action"), case.get("next_actor")))
        print(boundary)
        return 0

    if act == "dnc":
        try:
            result = reply_case.confirm_dnc(
                DATA, args.reply_case_id, operator=args.by,
                reason=args.reason or "")
        except reply_case.ReplyError as exc:
            print("DNC 登记失败：%s" % exc, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1,
                             sort_keys=True))
            if not result.get("ok") and result.get("paused_path"):
                print("发送已暂停：%s" % result.get("paused_path"),
                      file=sys.stderr)
            print(boundary, file=sys.stderr)
            return 0 if result.get("ok") else 1
        if result.get("ok"):
            print("已确认 DNC：%s" % result.get("target"))
            print("  抑制引用：%s" % result.get("dnc_ref"))
            print("  后续新队列会被发送门禁抑制；本命令不提供解除")
        else:
            print("DNC 未能写入：%s" % (result.get("error") or "未知原因"),
                  file=sys.stderr)
            if result.get("paused_path"):
                print("  发送已暂停：%s" % result.get("paused_path"),
                      file=sys.stderr)
        for warning in result.get("warnings") or []:
            print("  ⚠ %s" % warning)
        print(boundary)
        return 0 if result.get("ok") else 1

    if act == "list":
        try:
            cases = reply_case.list_cases(
                DATA, run_id=args.run or "", state=args.state or "",
                limit=args.limit or 0)
        except reply_case.ReplyError as exc:
            print("读取回复队列失败：%s" % exc, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({"schema": reply_case.QUEUE_SCHEMA,
                              "count": len(cases), "cases": cases},
                             ensure_ascii=False, indent=1, sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0
        if not cases:
            print("没有符合条件的回复案例。")
            print(boundary)
            return 0
        print("%-21s %-19s %-7s %-24s %-14s %-14s %-10s %s" % (
            "ID", "收到时间", "渠道", "目标", "候选", "审核", "DNC",
            "下一动作"))
        for c in cases:
            target = c.get("target") or "—"
            if len(target) > 24:
                target = target[:23] + "…"
            print("%-21s %-19s %-7s %-24s %-14s %-14s %-10s %s" % (
                c.get("reply_case_id"), (c.get("received_at") or "")[:19],
                c.get("channel"), target,
                c.get("classification_candidate"),
                c.get("review_state"), c.get("dnc_state"),
                c.get("next_required_action") or "—"))
        print("\n共 %d 条。" % len(cases))
        print(boundary)
        return 0

    if act == "show":
        try:
            case = reply_case.get_case(DATA, args.reply_case_id)
            history = reply_case.case_history(DATA, args.reply_case_id)
        except reply_case.ReplyError as exc:
            print("读取回复案例失败：%s" % exc, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({"case": case, "history": history},
                             ensure_ascii=False, indent=1, sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0
        fields = [
            ("案例", "reply_case_id"), ("来源", "source_ref"),
            ("来源哈希", "source_hash"), ("账号", "account_id"),
            ("渠道", "channel"), ("目标", "target"),
            ("关联 run", "run_id"), ("关联动作", "action_ref"),
            ("链接状态", "link_state"), ("收到时间", "received_at"),
            ("导入时间", "imported_at"), ("候选", "classification_candidate"),
            ("置信度", "confidence"), ("审核状态", "review_state"),
            ("审核人", "reviewer"), ("人判", "human_label"),
            ("DNC", "dnc_state"), ("DNC 引用", "dnc_ref"),
            ("下一执行人", "next_actor"), ("下一动作", "next_required_action"),
        ]
        for label, key in fields:
            value = case.get(key)
            if value in (None, ""):
                value = "—"
            print("%s：%s" % (label, value))
        print("原文哈希：%s" % (case.get("raw_text_hash") or "—"))
        print("版本历史：%d 条" % len(history))
        for row in history:
            print("  v%s %s %s → %s / DNC %s"
                  % (row.get("version"), row.get("imported_at") or "",
                     row.get("review_state"), row.get("human_label") or "—",
                     row.get("dnc_state")))
        print(boundary)
        return 0

    print("未知的 reply 子命令：%s" % act, file=sys.stderr)
    return 2


def cmd_grow_goal_exit(args):
    """目标退出与个体旅程（G-NEXT-2）：契约、评估、停止记录，全部本地。"""
    from core import goal_exit
    act = args.goal_exit_action
    boundary = ("边界：只登记目标契约与本地判定；不发送、不解除 DNC、"
                "不把先后发生写成因果，不自动回写 canonical。")

    def _exit_events(raw_list):
        """--exit kind=source_ref[::subject_ref] → 退出声明；无绑定不归到个人。"""
        rows = []
        for raw in raw_list or []:
            text = str(raw or "").strip()
            kind, sep, rest = text.partition("=")
            if not sep or not kind.strip() or not rest.strip():
                raise goal_exit.GoalExitError(
                    "--exit 需要 kind=source_ref[::subject_ref]，得到 %r" % raw)
            source_ref, _, subject = rest.partition("::")
            rows.append({"kind": kind.strip(),
                         "source_ref": source_ref.strip(),
                         "subject_ref": subject.strip()})
        return rows

    if act == "contract":
        try:
            contract = goal_exit.build_contract(
                run_id=args.run,
                goal_event={"event_type": args.goal_type,
                            "target": args.target,
                            "metric": args.metric or "",
                            "label": args.label or "",
                            "qualifier": ({"verification_state":
                                           args.verify_state}
                                          if args.verify_state else {})},
                observation_window={"start": args.window_start,
                                    "end": args.window_end},
                exit_events=_exit_events(args.exit),
                attribution_window={"days": args.attr_days},
                created_by=args.by or "",
                note=args.note or "")
            written = goal_exit.write_contract(
                DATA, contract, dry_run=not args.apply)
        except goal_exit.GoalExitError as exc:
            print("目标契约未登记：%s" % exc, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({"contract": contract, "write": written},
                             ensure_ascii=False, indent=1, sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0
        print("%s完成：%s（%s）"
              % ("预演" if not args.apply else "登记",
                 contract.get("contract_id"), written.get("status")))
        goal = contract.get("goal") or {}
        window = contract.get("observation_window") or {}
        print("  目标：%s ≥ %s（%s）"
              % (goal.get("event_type"), goal.get("target"),
                 goal.get("metric")))
        print("  观察窗口：%s → %s；归因窗口 %s 天"
              % (window.get("start"), window.get("end"),
                 (contract.get("attribution_window") or {}).get("days")))
        if contract.get("exit_events"):
            print("  运行级退出声明：%s 条（未绑定对象的只作声明，不归到个人）"
                  % len(contract.get("exit_events") or []))
        if written.get("status") == "would_write":
            print("  加 --apply 才写入：%s" % written.get("path"))
        print(boundary)
        return 0

    if act == "evaluate":
        try:
            contract = goal_exit.get_contract(DATA, args.contract)
            projection = goal_exit.evaluate(
                DATA, contract, subject_ref=args.subject,
                now=args.now or None)
            written = goal_exit.write_projection(
                DATA, projection, dry_run=not args.apply)
            stopped = goal_exit.write_stop(
                DATA, projection, dry_run=not args.apply)
        except goal_exit.GoalExitError as exc:
            print("评估未执行：%s" % exc, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({"projection": projection, "write": written,
                              "stop": stopped},
                             ensure_ascii=False, indent=1, sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0
        print(goal_exit.render_text(projection))
        print("")
        print("投影：%s（%s，revision %s）"
              % (projection.get("projection_id"), written.get("status"),
                 written.get("revision")))
        if stopped.get("status") != "skipped":
            print("停止记录：%s（%s）"
                  % (stopped.get("stop_id") or "—", stopped.get("status")))
        if written.get("status") == "would_write":
            print("加 --apply 才写入：%s" % written.get("path"))
        print(boundary)
        return 0

    if act == "list":
        try:
            contracts = goal_exit.load_contracts(DATA)
        except goal_exit.GoalExitError as exc:
            print("读取目标契约失败：%s" % exc, file=sys.stderr)
            return 2
        rows = sorted(contracts.values(),
                      key=lambda c: (str(c.get("created_at") or ""),
                                     str(c.get("contract_id") or "")))
        if args.run:
            rows = [c for c in rows
                    if str(c.get("run_id") or "") == args.run]
        if args.json:
            print(json.dumps({"count": len(rows), "contracts": rows},
                             ensure_ascii=False, indent=1, sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0
        if not rows:
            print("没有已登记的目标契约。")
            print(boundary)
            return 0
        print("%-23s %-22s %-10s %-6s %-21s %-21s %s" % (
            "契约", "run", "目标", "目标数", "观察开始", "观察结束",
            "退出声明"))
        for c in rows:
            goal = c.get("goal") or {}
            window = c.get("observation_window") or {}
            print("%-23s %-22s %-10s %-6s %-21s %-21s %s" % (
                c.get("contract_id"), c.get("run_id"),
                goal.get("event_type"), goal.get("target"),
                window.get("start"), window.get("end"),
                len(c.get("exit_events") or [])))
        print("\n共 %d 个契约。" % len(rows))
        print(boundary)
        return 0

    if act == "show":
        try:
            contract = goal_exit.get_contract(DATA, args.contract)
            block = goal_exit.run_projection(
                DATA, str(contract.get("run_id") or ""))
        except goal_exit.GoalExitError as exc:
            print("读取目标契约失败：%s" % exc, file=sys.stderr)
            return 2
        rows = [p for p in block.get("projections") or []
                if p.get("contract_id") == args.contract]
        if args.json:
            print(json.dumps({"contract": contract, "projections": rows},
                             ensure_ascii=False, indent=1, sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0
        goal = contract.get("goal") or {}
        window = contract.get("observation_window") or {}
        print("契约：%s（run %s）"
              % (contract.get("contract_id"), contract.get("run_id")))
        print("  目标：%s ≥ %s（%s）"
              % (goal.get("event_type"), goal.get("target"),
                 goal.get("metric")))
        print("  观察窗口：%s → %s"
              % (window.get("start"), window.get("end")))
        print("  指纹：%s" % contract.get("fingerprint"))
        print("  登记人：%s" % (contract.get("created_by") or "—"))
        for rec in contract.get("exit_events") or []:
            print("  退出声明：%s 来源 %s 对象 %s"
                  % (rec.get("kind"), rec.get("source_ref"),
                     rec.get("subject_ref") or "—（运行级）"))
        if not rows:
            print("还没有对象投影：用 mkt grow goal-exit evaluate 评估")
        for p in rows:
            print("  %s %s：%s/%s（%s，revision %s）"
                  % (p.get("projection_id"), p.get("subject"),
                     p.get("observed"), p.get("target"), p.get("state"),
                     p.get("revision")))
        print(boundary)
        return 0

    if act == "view":
        block = goal_exit.run_projection(
            DATA, args.run, limit=args.limit or 20)
        if args.json:
            print(json.dumps(block, ensure_ascii=False, indent=1,
                             sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0 if block.get("state") != "unreadable" else 2
        if block.get("state") == "unreadable":
            print("旅程存储读不了：%s" % block.get("error"), file=sys.stderr)
            return 2
        print("旅程视图：run %s（%s）"
              % (block.get("run_id"), block.get("state")))
        print("  契约 %s 个，投影 %s 个，停止记录 %s 条，冲突 %s 条，孤立投影 %s 条"
              % (len(block.get("contracts") or []),
                 len(block.get("projections") or []),
                 block.get("stop_count"), block.get("conflict_count"),
                 block.get("orphan_projections")))
        counts = block.get("counts") or {}
        if counts:
            print("  状态计数：%s"
                  % "，".join("%s %s" % (k, counts[k]) for k in sorted(counts)))
        for p in block.get("projections") or []:
            print("  %s %s：%s/%s（%s，revision %s）"
                  % (p.get("projection_id"), p.get("subject"),
                     p.get("observed"), p.get("target"), p.get("state"),
                     p.get("revision")))
        for row in block.get("unresolved") or []:
            print("  未决：%s %s — %s"
                  % (row.get("contract_id"), row.get("subject"),
                     row.get("reason")))
        for row in block.get("stale") or []:
            print("  过期未评估：%s（窗口止于 %s）"
                  % (row.get("contract_id"), row.get("window_end")))
        if block.get("note"):
            print("  %s" % block.get("note"))
        print(boundary)
        return 0

    print("未知的 goal-exit 子命令：%s" % act, file=sys.stderr)
    return 2


def cmd_grow_signal(args):
    """信号证据包（G-NEXT-3）：登记、撤销与只读视图；写入默认预演。"""
    from core import signal_envelope
    act = args.signal_action
    boundary = ("边界：信号只证明对应来源在对应时间出现过该内容，不证明身份、"
                "意图或购买倾向；撤销不删除历史、不改账本；不发送、不自动回写 canonical。")

    def _view():
        return signal_envelope.signal_view(
            DATA, run_id=getattr(args, "run", "") or "")

    def _show_row(row, text):
        if args.json:
            print(json.dumps(row, ensure_ascii=False, indent=1, sort_keys=True))
            print(boundary, file=sys.stderr)
        else:
            print(text)
            print(boundary)
        return 0

    if act == "add":
        raw_text = args.raw_text or ""
        if args.raw_file:
            try:
                raw_text = Path(args.raw_file).read_text(encoding="utf-8")
            except OSError as exc:
                print("信号未登记：读取 --raw-file 失败：%s" % exc,
                      file=sys.stderr)
                return 2
        try:
            envelope = signal_envelope.build_signal(
                source_family=args.source_family,
                source_ref=args.source_ref,
                observed_at=args.observed_at or "",
                raw_text=raw_text,
                raw_hash=args.raw_hash or "",
                subject_ref=args.subject or "",
                identity_basis=args.identity_basis or "",
                allowed_use=args.use or None,
                consent_ref=args.consent or "",
                content_claim_refs=args.claim or [],
                max_age_days=args.max_age_days,
                run_id=args.run or "",
                note=args.note or "")
            written = signal_envelope.write_signal(
                DATA, envelope, dry_run=not args.apply)
        except signal_envelope.SignalError as exc:
            print("信号未登记：%s" % exc, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({"signal": envelope, "write": written},
                             ensure_ascii=False, indent=1, sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0
        print("%s完成：%s（%s）"
              % ("预演" if not args.apply else "登记",
                 envelope.get("signal_id"), written.get("status")))
        print(signal_envelope.render_signal_text(envelope))
        if written.get("status") == "would_write":
            print("  加 --apply 才写入：%s" % written.get("path"))
        print(boundary)
        return 0

    if act == "list":
        try:
            view = _view()
        except signal_envelope.SignalError as exc:
            print("信号读取失败：%s" % exc, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(view, ensure_ascii=False, indent=1,
                             sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0
        if not view["signals"]:
            print("还没有登记信号。")
            print(boundary)
            return 0
        counts = view["counts"]
        print("信号 %d 条（可用 %d / 过期 %d / 撤销 %d / 不完整 %d / 身份未决 %d）"
              % (counts["total"], counts["usable"], counts["stale"],
                 counts["revoked"], counts["incomplete"],
                 counts["unresolved_identity"]))
        for row in view["signals"]:
            print("  %s  %s/%s  观察 %s  %s  个性化=%s"
                  % (row.get("signal_id"), row.get("source_family"),
                     row.get("source_ref"), row.get("observed_at") or "UNKNOWN",
                     row.get("state"),
                     "允许" if (row.get("permission") or {}).get(
                         "personalization", {}).get("allowed") else "拒绝"))
        if view.get("gaps"):
            print("  证据缺口：%d 条（详见 --json）" % len(view["gaps"]))
        print(boundary)
        return 0

    if act == "show":
        try:
            view = _view()
        except signal_envelope.SignalError as exc:
            print("信号读取失败：%s" % exc, file=sys.stderr)
            return 2
        target = next((row for row in view["signals"]
                       if row.get("signal_id") == args.signal), None)
        if target is None:
            print("没有这个信号：%s" % args.signal, file=sys.stderr)
            return 2
        return _show_row(target, signal_envelope.render_signal_text(target))

    if act == "revoke":
        try:
            result = signal_envelope.revoke_source(
                DATA, source_family=args.source_family,
                source_ref=args.source_ref, by=args.by or "",
                reason=args.reason or "", dry_run=not args.apply)
        except signal_envelope.SignalError as exc:
            print("撤销未登记：%s" % exc, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1,
                             sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0
        print("%s完成：%s/%s（%s）"
              % ("预演" if not args.apply else "撤销",
                 args.source_family, args.source_ref, result.get("status")))
        if result.get("status") == "would_revoke":
            print("  加 --apply 才写入：%s" % result.get("path"))
        print(boundary)
        return 0

    print("未知的 signal 子命令：%s" % act, file=sys.stderr)
    return 2


def cmd_grow_preview(args):
    """变更预演（G-NEXT-3）：只读快照与 stale 检查；不批准、不执行。"""
    from core import signal_envelope
    act = args.preview_action
    boundary = ("边界：预演只读，不批准、不写队列、不执行、不产生外部效果；"
                "stale/过期必须重新生成；外部效果未知先走 mkt ledger resolve。")

    if act == "create":
        try:
            preview = signal_envelope.build_preview(
                DATA, plan_id=args.plan, changes=args.change or [],
                permission_required=args.permission or None,
                approval_ref=args.approval_ref or "",
                expires_at=args.expires_at or "",
                run_id=args.run or "", created_by=args.by or "",
                note=args.note or "")
            written = signal_envelope.write_preview(
                DATA, preview, dry_run=not args.apply)
        except signal_envelope.SignalError as exc:
            print("预演未登记：%s" % exc, file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({"preview": preview, "write": written},
                             ensure_ascii=False, indent=1, sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0
        print("%s完成：%s（%s）"
              % ("预演" if not args.apply else "登记",
                 preview.get("preview_id"), written.get("status")))
        print(signal_envelope.render_preview_text(preview))
        if written.get("status") == "would_write":
            print("  加 --apply 才写入：%s" % written.get("path"))
        print(boundary)
        return 0

    if act == "show":
        try:
            preview = signal_envelope.get_preview(DATA, args.preview)
        except signal_envelope.SignalError as exc:
            print("预演读取失败：%s" % exc, file=sys.stderr)
            return 2
        if preview is None:
            print("没有这个预演：%s" % args.preview, file=sys.stderr)
            return 2
        check = signal_envelope.check_preview(
            DATA, preview, account=args.account or None,
            workspace=args.workspace or None)
        if args.json:
            print(json.dumps({"preview": preview, "check": check},
                             ensure_ascii=False, indent=1, sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0
        print(signal_envelope.render_preview_text(preview, check))
        print(boundary)
        return 0

    if act == "check":
        try:
            preview = signal_envelope.get_preview(DATA, args.preview)
        except signal_envelope.SignalError as exc:
            print("预演读取失败：%s" % exc, file=sys.stderr)
            return 2
        if preview is None:
            print("没有这个预演：%s" % args.preview, file=sys.stderr)
            return 2
        check = signal_envelope.check_preview(
            DATA, preview, account=args.account or None,
            workspace=args.workspace or None)
        if args.json:
            print(json.dumps(check, ensure_ascii=False, indent=1,
                             sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0
        print("预演 %s：%s" % (preview.get("preview_id"), check.get("state")))
        for reason in check.get("reasons") or []:
            print("  - %s" % reason)
        print("  审批：%s（valid=%s）%s"
              % (check.get("approval_state"), check.get("approval_valid"),
                 check.get("approval_reason") or ""))
        for incident in check.get("incidents") or []:
            print("  事故：%s → %s"
                  % (incident.get("ref"), incident.get("next_command")))
        print(boundary)
        return 0

    if act == "list":
        try:
            view = signal_envelope.signal_view(DATA, run_id=args.run or "")
        except signal_envelope.SignalError as exc:
            print("预演读取失败：%s" % exc, file=sys.stderr)
            return 2
        block = {"schema": view["schema"], "run_id": view["run_id"],
                 "previews": view["previews"],
                 "preview_stale": view["preview_stale"],
                 "delete_items": view["delete_items"],
                 "incidents": view["incidents"],
                 "injection_note": view["injection_note"]}
        if args.json:
            print(json.dumps(block, ensure_ascii=False, indent=1,
                             sort_keys=True))
            print(boundary, file=sys.stderr)
            return 0
        if not block["previews"]:
            print("还没有变更预演。")
            print(boundary)
            return 0
        print("变更预演 %d 条（删除项 %d）"
              % (len(block["previews"]), len(block["delete_items"])))
        for row in block["previews"]:
            check = row.get("check") or {}
            print("  %s  plan=%s  %s  dry_run=%s  external_effect=%s"
                  % (row.get("preview_id"), row.get("plan_id"),
                     check.get("state"), row.get("dry_run"),
                     row.get("external_effect")))
            for item in row.get("changes") or []:
                if item.get("op") == "remove":
                    print("    删除：%s / %s  %s"
                          % (item.get("kind"), item.get("ref"),
                             item.get("summary") or ""))
        if block["preview_stale"]:
            print("  需重新生成：%s"
                  % "、".join(row.get("preview_id")
                              for row in block["preview_stale"]))
        print(boundary)
        return 0

    print("未知的 preview 子命令：%s" % act, file=sys.stderr)
    return 2


def cmd_grow_handoff(args):
    """Agent 主入口的伴随 GUI 交接：只读摘要、handoff 生命周期与回链。"""
    from core import agent_bridge
    act = args.handoff_action
    boundary = agent_bridge.BOUNDARY

    def _emit(payload):
        print(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True))
        print(boundary, file=sys.stderr)
        return 0

    try:
        if act == "locate":
            summary = agent_bridge.risk_summary(
                DATA, run_id=args.run or "", limit=args.limit,
                host=args.host or "", companion_url=args.companion_url or "")
            if args.json:
                return _emit(summary)
            print(agent_bridge.render_summary(summary))
            print(boundary)
            return 0
        if act == "create":
            handoff = agent_bridge.create_handoff(
                DATA, object_type=args.object_type, object_id=args.object_id,
                entry_route=args.entry_route or "",
                focus_ref=args.focus_ref or "",
                requested_action=args.requested_action or "",
                allowed_actions=args.allowed_action or None,
                host=args.host or "", companion_url=args.companion_url or "",
                ttl_seconds=args.ttl, by=args.by or "agent")
            if args.json:
                return _emit(handoff)
            print(agent_bridge.render_handoff(handoff))
            print(boundary)
            return 0
        if act == "show":
            handoff = agent_bridge.get_handoff(DATA, args.handoff_ref)
            if args.json:
                return _emit(handoff)
            print(agent_bridge.render_handoff(handoff))
            print(boundary)
            return 0
        if act == "list":
            rows = agent_bridge.list_handoffs(DATA)
            if args.json:
                return _emit(rows)
            if not rows:
                print("没有 handoff。")
            for row in rows:
                print(agent_bridge.render_handoff(row))
                print("")
            print(boundary)
            return 0
        if act == "revoke":
            handoff = agent_bridge.revoke_handoff(
                DATA, args.handoff_ref, by=args.by, reason=args.reason or "")
            if args.json:
                return _emit(handoff)
            print(agent_bridge.render_handoff(handoff))
            print(boundary)
            return 0
        if act == "resolve":
            resolution = agent_bridge.resolve_handoff(DATA, args.handoff_ref)
            if args.json:
                return _emit(resolution)
            print(agent_bridge.render_resolution(resolution))
            print(boundary)
            return 0
    except agent_bridge.BridgeError as exc:
        print("handoff 未完成（%s）：%s" % (exc.code, exc.message),
              file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False,
                              "error": {"code": exc.code,
                                        "message": exc.message}},
                             ensure_ascii=False))
        return 2
    print("未知的 handoff 子命令：%s" % act, file=sys.stderr)
    return 2


def cmd_grow_serve(args):
    """启动本机伴随 GUI：只读投影＋受控写，只监听 127.0.0.1。"""
    from core import gui_server
    if args.dry_run:
        if args.json:
            print(json.dumps({"schema": "growovo.gui-routes/1",
                              "routes": gui_server.routes_table(),
                              "boundary": gui_server.BOUNDARY},
                             ensure_ascii=False, indent=1, sort_keys=True))
        else:
            print(gui_server.render_routes_text())
        return 0
    if not DATA.exists():
        print("数据目录不存在：%s" % DATA, file=sys.stderr)
        return 2
    try:
        return gui_server.serve(DATA, port=args.port,
                                open_browser=not args.no_open)
    except gui_server.GuiError as exc:
        print("GUI 未启动（%s）：%s" % (exc.code, exc.message), file=sys.stderr)
        return 2
    except OSError as exc:
        print("GUI 未启动：%s" % exc, file=sys.stderr)
        return 2


def cmd_grow(args):
    from core.grow import GrowError as V1Error, GrowRun
    try:
        from core.grow_v2 import GrowError as V2Error
    except Exception:  # noqa: BLE001 兼容旧安装
        V2Error = V1Error
    GrowErrors = (V1Error, V2Error)
    act = args.action
    if act == "reply":
        return cmd_grow_reply(args)
    if act == "goal-exit":
        return cmd_grow_goal_exit(args)
    if act == "signal":
        return cmd_grow_signal(args)
    if act == "preview":
        return cmd_grow_preview(args)
    if act == "handoff":
        return cmd_grow_handoff(args)
    if act == "serve":
        return cmd_grow_serve(args)
    path = DATA / "grow" / f"{getattr(args, 'id', '')}.json" if getattr(args, "id", "") else None

    def _open():
        """按 run 文件 schema 选 v1 / v2 实现；缺文件给友好错误。"""
        if not path.exists():
            raise FileNotFoundError(f"没有这个闭环：{path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema") == "growovo.grow-run/2":
            from core.grow_v2 import GrowRunV2
            return GrowRunV2(path), raw
        return GrowRun(path), raw

    if act == "detail":
        from core import run_detail
        detail = run_detail.build_run_detail(
            DATA, args.id, on=args.on or None)
        if args.json:
            print(json.dumps(detail, ensure_ascii=False, indent=1,
                             sort_keys=True))
        if args.html:
            out = Path(args.html).expanduser()
            if out.exists():
                print(f"拒绝覆盖已有文件：{out}", file=sys.stderr)
                return 2
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(run_detail.render_html(detail), encoding="utf-8")
            print(f"已写入 {out}")
        if not args.json and not args.html:
            print(run_detail.render_summary(detail))
        if args.json:
            print("边界：本页是只读投影，不审批、不裁决、不恢复、不发送、不重试。",
                  file=sys.stderr)
        else:
            print("\n边界：本页是只读投影，不审批、不裁决、不恢复、不发送、不重试。")
        return 0

    if act == "platforms":
        from core import platform_caps
        print("第一期可自主执行的平台（技术可执行，且平台许可允许自主）：")
        for row in platform_caps.matrix():
            if row["tier"] != platform_caps.AUTO:
                continue
            write = row.get("write") or []
            if not row.get("autonomous", True):
                continue
            print(f"  ✅ {row['platform']:<12} {row['status']:<8} "
                  f"动作 {'/'.join(write[1:]) or '—'}｜{row['reason']}")
        print("\n需人工执行 / 首跑解锁（不纳入第一期自主承诺）：")
        for row in platform_caps.matrix():
            if row["tier"] != platform_caps.GATED:
                continue
            print(f"  ⚠️  {row['platform']:<12} {row['status']:<8} {row['reason']}")
        print("\n只能出人工作业单（不假装自动）：其余平台。")
        from core import platform_strategy
        rows = platform_strategy.summary_rows()
        stale = [r["platform"] for r in rows if r["stale"]]
        note = f"，{len(stale)} 个待刷新" if stale else "，全部在复核窗口内"
        print(f"\n平台策略卡：{len(rows)} 个平台（节奏/上限/门禁/证据{note}）；mkt strategy cards 查看。")
        return 0

    if act == "handoff-verovo":
        from core.verovo_handoff import HandoffError, write_handoff
        try:
            result = write_handoff(DATA, args.id, args.out)
        except (HandoffError, OSError, ValueError) as exc:
            print(f"Verovo 交接失败：{exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=1))
        print("边界：证据交接完成 ≠ 已入 Verovo canonical、已验收交付或已确认收入。")
        return 0

    if act == "experiment":
        from core import controlled_learning as cl
        sub = getattr(args, "experiment_action", "")
        if sub == "design":
            try:
                exp = cl.design_experiment(
                    DATA, exp_id=args.exp_id,
                    hypothesis=args.hypothesis, segment=args.segment,
                    mechanism=args.mechanism,
                    control_or_baseline=args.baseline,
                    primary_metric=args.metric, guardrail=args.guardrail,
                    window={"start": args.start, "end": args.end},
                    min_sample_declared=args.min_sample,
                    analysis_limitations=args.limitations,
                    stop_conditions=args.stop_conditions,
                    decision_rule=args.decision_rule, owner=args.owner,
                    randomized=args.randomized,
                    assignment_ref=args.assignment_ref,
                    dry_run=not args.apply)
            except cl.ControlledLearningError as exc:
                print(f"实验未创建：{exc}")
                return 2
            if args.json:
                print(json.dumps(exp, ensure_ascii=False, indent=1))
                return 0
            label = "已创建" if args.apply else "预演（未写入）"
            print(f"{label}：实验 {exp['exp_id']}｜预声明样本 "
                  f"{exp['min_sample_declared']}｜计划指纹 "
                  f"{str(exp['plan_sha256'])[:24]}…")
            print(f"下一步：mkt grow experiment approve {exp['exp_id']} "
                  "--by <谁> --apply（批准后才能 start）")
            if not args.apply:
                print("确认无误后加 --apply 落盘（默认只校验，不写文件）。")
            return 0
        if sub == "approve":
            try:
                record = cl.approve_experiment(
                    DATA, args.id, by=args.by, note=args.note,
                    dry_run=not args.apply)
            except cl.ControlledLearningError as exc:
                print(f"未批准：{exc}")
                return 2
            if args.json:
                print(json.dumps(record, ensure_ascii=False, indent=1))
                return 0
            approval = record.get("approval") or record
            print(("已批准" if args.apply else "预演（未写入）")
                  + f"：实验 {args.id} 由 {approval.get('by')} 批准，绑定计划指纹 "
                  f"{str(approval.get('plan_sha256'))[:24]}…")
            return 0
        if sub == "start":
            try:
                exp = cl.start_experiment(DATA, args.id, dry_run=not args.apply)
            except cl.ControlledLearningError as exc:
                print(f"未启动：{exc}")
                return 2
            if args.json:
                print(json.dumps(exp, ensure_ascii=False, indent=1))
                return 0
            print(("已启动" if args.apply else "预演（未写入）")
                  + f"：实验 {args.id} 状态 {exp.get('status')}")
            return 0
        if sub == "observe":
            try:
                result = cl.record_observation(
                    DATA, args.id, note=args.note, value=args.value,
                    evidence_ref=args.evidence_ref,
                    evidence_hash=args.evidence_hash,
                    approximated=args.approximated)
            except cl.ControlledLearningError as exc:
                print(f"未记录：{exc}")
                return 2
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=1))
                return 0
            if not result["recorded"]:
                print(f"未记录：同一份证据已记过（{result['reason']}），"
                      f"当前有效样本 {result['sample']} 条")
            else:
                print(f"已记录：有效样本 {result['sample']} 条"
                      f"｜分析口径 {result['analysis']['analysis_mode']}")
            return 0
        if sub == "analyze":
            try:
                analysis = cl.analyze_experiment(DATA, args.id)
            except cl.ControlledLearningError as exc:
                print(f"分析不了：{exc}")
                return 2
            if args.json:
                print(json.dumps(analysis, ensure_ascii=False, indent=1))
                return 0
            print(f"# 实验 {analysis['exp_id']}（{analysis['status']}）")
            print(f"- 口径：{analysis['analysis_mode']}"
                  f"｜有效样本 {analysis['sample']}/{analysis['min_sample_declared']}"
                  f"｜随机分组 {'是' if analysis['randomized'] else '否'}")
            print(f"- 结论：{analysis['wording']}")
            print(f"- 下一步：{analysis['next_action']}")
            for item in analysis.get("limitations") or []:
                print(f"- 限制：{item}")
            return 0
        if sub == "compare":
            channels = [c.strip() for c in (args.channels or "").split(",")
                        if c.strip()]
            window = ({"start": args.start, "end": args.end}
                      if (args.start or args.end) else None)
            try:
                comparison = cl.freeze_comparison(
                    DATA, args.id, product=args.product, channels=channels,
                    quota={"actions_per_week": args.quota_actions},
                    window=window, cost_basis=args.cost_basis,
                    dry_run=not args.apply)
            except cl.ControlledLearningError as exc:
                print(f"比较上下文未冻结：{exc}")
                return 2
            if args.json:
                print(json.dumps(comparison, ensure_ascii=False, indent=1))
                return 0
            print(("已冻结" if args.apply else "预演（未写入）")
                  + f"：三种替代 + 上下文指纹 "
                  f"{str(comparison['comparison_hash'])[:24]}…")
            for arm in comparison["arms"]:
                print(f"  - {arm['name']}：{arm['description']}")
            print("冻结后换时间窗/换渠道不能把结果说成有效。")
            return 0
        if sub == "metrics":
            window = ({"start": args.start, "end": args.end}
                      if (args.start or args.end) else None)
            metrics = cl.falsification_metrics(
                DATA, run_id=args.run or None, window=window)
            if args.json:
                print(json.dumps(metrics, ensure_ascii=False, indent=1))
            else:
                print(cl.render_falsification(metrics))
            return 0
        if sub == "list":
            experiments = cl.list_experiments(DATA)
            if args.json:
                print(json.dumps(experiments, ensure_ascii=False, indent=1))
                return 0
            if not experiments:
                print("还没有受控实验。先 mkt grow experiment design ...")
                return 0
            for exp in experiments:
                approval = exp.get("approval") or {}
                print(f"  {exp.get('exp_id')}｜{exp.get('status')}"
                      f"｜声明样本 {exp.get('min_sample_declared')}"
                      f"｜{'已批准 ' + str(approval.get('by')) if approval.get('by') else '未批准'}")
            return 0
        print("experiment 只支持 design/approve/start/observe/analyze/list")
        return 2

    if act == "change":
        from core import controlled_learning as cl
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            print("change 只支持 v2 闭环（先 mkt grow launch）")
            return 2
        sub = getattr(args, "change_action", "")
        if sub == "list":
            listing = run.list_changes()
            if args.json:
                print(json.dumps(listing, ensure_ascii=False, indent=1))
                return 0
            print(f"当前策略指纹：{listing['current_hash']}")
            if not listing["changes"]:
                print("还没有策略改动记录。")
                return 0
            for record in listing["changes"]:
                print(f"  {record.get('change_id')}｜{record.get('kind')}"
                      f"｜{record.get('param')}：{record.get('from')} → "
                      f"{record.get('to')}｜{record.get('on')}"
                      f"｜{str(record.get('before_hash'))[:16]}… → "
                      f"{str(record.get('after_hash'))[:16]}…")
            for record in listing["approvals"]:
                print(f"  批准 {str(record.get('change_sha256'))[:16]}…"
                      f"｜by {record.get('by')}｜{record.get('on')}")
            return 0
        if sub == "approve":
            try:
                record = run.approve_change(
                    DATA, args.change, by=args.by, on=args.on or None,
                    dry_run=not args.apply)
            except GrowErrors + (cl.ControlledLearningError,) as exc:
                print(f"未批准：{exc}")
                return 2
            if args.json:
                print(json.dumps(record, ensure_ascii=False, indent=1))
                return 0
            print(("已记录批准" if args.apply else "预演（未写入）")
                  + f"：{str(record['change_sha256'])[:16]}… 由 "
                  f"{record['by']} 批准，绑定策略指纹 "
                  f"{str(record['before_hash'])[:16]}…")
            return 0
        if sub == "rollback":
            try:
                result = run.rollback_changes(
                    DATA, by=args.by, on=args.on or None,
                    change_id=args.change or "", dry_run=not args.apply)
            except GrowErrors + (cl.ControlledLearningError,) as exc:
                print(f"未回滚：{exc}")
                return 2
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=1))
                return 0
            if not result.get("rolled_back"):
                print(f"没有回滚：{result.get('reason', '')}")
                return 0
            print(("已回滚" if args.apply else "预演（未写入）")
                  + f"：恢复策略指纹 {str(result['restored_hash'])[:16]}…"
                  "；已发出去的外部动作不回滚。")
            return 0
        print("change 只支持 list/approve/rollback")
        return 2

    if act in ("events", "layers", "journey", "qualify"):
        from core import outcome_layers

        def _window(ns):
            """--window-start/--window-end → 时间窗；都没给就是 None（不限）。"""
            start = getattr(ns, "window_start", "") or ""
            end = getattr(ns, "window_end", "") or ""
            return {"start": start, "end": end} if (start or end) else None

        if act == "events":
            sub = getattr(args, "events_action", "")
            if sub == "import":
                try:
                    result = outcome_layers.import_events(
                        DATA, args.from_file,
                        allowed_sources=args.allowed_source,
                        imported_by=args.by, window=_window(args),
                        dry_run=not args.apply,
                        trusted_verifiers=set(args.trusted_verifier or []))
                except outcome_layers.OutcomeLayerError as exc:
                    print(f"导入未执行：{exc}")
                    return 2
                if args.json:
                    print(json.dumps(result, ensure_ascii=False, indent=1))
                    return 0 if not result["errors"] else 1
                added = (result["created"] if args.apply
                         else result.get("would_create", 0))
                print(f"导入{'已写入' if args.apply else '预演（未写入）'}："
                      f"新增 {added} 条｜重复跳过 {result['skipped_duplicate']} 条"
                      f"｜错误 {len(result['errors'])} 条")
                for err in result["errors"]:
                    print(f"  第 {err['row']} 行 {err.get('event_id') or '-'}："
                          f"{err['reason']}（{err['detail']}）")
                for warning in result.get("warnings") or []:
                    print(f"  注意：{warning}")
                if result.get("partial"):
                    print("  注意：这是一次部分导入——被拒的行没有写入，其余已写入；"
                          "先修被拒行再补导。")
                if args.apply and result["created"]:
                    print("已写：outcomes/product_events.jsonl、三层凭证，"
                          "并把通过核验的业务事件投影进业务台账。")
                elif not args.apply and result["event_ids"]:
                    print("确认无误后加 --apply 落盘（默认只校验，不写任何文件）。")
                return 0 if not result["errors"] else 1
            if sub == "list":
                events = outcome_layers.read_product_events(
                    DATA, run_id=args.id or None)
                states = outcome_layers.qualification_states(DATA)
                if args.json:
                    print(json.dumps({"events": events,
                                      "qualifications": states},
                                     ensure_ascii=False, indent=1))
                    return 0
                if not events:
                    print("还没有导入过产品事件。先 `mkt grow events import "
                          "--from <file> --allowed-source <system> --by <谁>` 预演。")
                    return 0
                print(f"已导入产品事件 {len(events)} 条"
                      "（核验未通过的不计入；资格是另一条线，需人工改）：")
                for rec in events:
                    qualifies = states.get(rec.get("event_id"), True)
                    print(f"  {rec.get('event_id')}｜{rec.get('event_type')}"
                          f"｜{rec.get('subject_id')}｜{rec.get('occurred_at')}"
                          f"｜{rec.get('source_system')}"
                          f"｜核验 {rec.get('verification_state')}"
                          f"｜资格 {'计入' if qualifies else '人工判为不计入'}")
                print("资格变更留审计：mkt grow qualify <event_id> --no/--yes "
                      "--by <谁> --note <依据>")
                return 0
            if sub == "sync":
                result = outcome_layers.sync_action_receipts(
                    DATA, run_id=args.id or None, window=_window(args),
                    by=args.by or "local-ledger", dry_run=not args.apply)
                if args.json:
                    print(json.dumps(result, ensure_ascii=False, indent=1))
                    return 0
                if args.apply:
                    print(f"L1 回执已补齐 {result['written']} 张（扫描账本 "
                          f"{result['scanned']} 条）。")
                else:
                    print(f"预演：将补 {result['would_write']} 张 L1 回执"
                          f"（扫描账本 {result['scanned']} 条，未写入）。"
                          "加 --apply 落盘。")
                return 0
            print("events 只支持 import/list/sync")
            return 2

        if act == "layers":
            summary = outcome_layers.layer_summary(
                DATA, run_id=getattr(args, "id", "") or None,
                window=_window(args))
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, indent=1))
            else:
                print(outcome_layers.render_summary(summary))
            return 0

        if act == "journey":
            try:
                journey = outcome_layers.prospect_journey(
                    DATA, prospect_ref=args.ref,
                    run_id=getattr(args, "id", "") or None)
            except outcome_layers.OutcomeLayerError as exc:
                print(f"查不了：{exc}")
                return 2
            if args.json:
                print(json.dumps(journey, ensure_ascii=False, indent=1))
            else:
                print(outcome_layers.render_journey(journey))
            return 0

        if act == "qualify":
            if getattr(args, "list", False):
                wanted = str(getattr(args, "event", "") or args.event_id or "").strip()
                rows = [row for row in outcome_layers.read_qualifications(DATA)
                        if not wanted or str(row.get("event_id")) == wanted]
                latest = rows[-1] if rows else {}
                event = {}
                if wanted:
                    event = next((rec for rec in outcome_layers.read_product_events(DATA)
                                  if str(rec.get("event_id")) == wanted), {})
                payload = {"event_id": wanted or None,
                           "event": {k: event.get(k) for k in
                                     ("event_id", "event_type", "subject_id",
                                      "occurred_at", "source_system", "source_ref")
                                     if k in event} or None,
                           "qualifies": bool(latest.get("qualifies")),
                           "history": rows,
                           "count": len(rows),
                           "changes": rows,
                           "boundary": ("资格变更只追加审计，原事件不改；投影按最新资格"
                                        "过滤，台账行仍可审计")}
                if args.json:
                    print(json.dumps(payload, ensure_ascii=False, indent=1))
                else:
                    print(f"资格变更 {len(rows)} 条"
                          + (f"（事件 {wanted}）" if wanted else ""))
                    for row in rows:
                        verdict = "计入" if row.get("qualifies") else "不计入"
                        subject = row.get("subject_id") or "-"
                        kind = row.get("event_type") or "-"
                        print(f"- {row.get('ts')} {row.get('event_id')}"
                              f"（{kind}，主体 {subject}）→ {verdict}"
                              f" by {row.get('by')}：{row.get('note')}")
                return 0
            if bool(args.yes) == bool(args.no):
                print("二选一：--yes（计入）或 --no（不计入），不要两个都给或都不给")
                return 2
            try:
                record = outcome_layers.set_event_qualification(
                    DATA, args.event_id, qualifies=bool(args.yes),
                    by=args.by, note=args.note, dry_run=not args.apply)
            except outcome_layers.OutcomeLayerError as exc:
                print(f"资格未改：{exc}")
                return 2
            if args.json:
                print(json.dumps(record, ensure_ascii=False, indent=1))
                return 0
            verdict = "计入" if record["qualifies"] else "不计入"
            if args.apply:
                print(f"已记录资格变更：{record['event_id']} → {verdict}"
                      f"（原事件没改；审计 {record['ts']} by {record['by']}）。")
            else:
                print(f"预演：将把 {record['event_id']} 标为{verdict}"
                      "（原事件不改，只追加审计记录）。加 --apply 写入。")
            return 0

    if act == "start" and not getattr(args, "id", ""):
        # G-05 九步向导：不带 run id 的 mkt grow start；只建计划，不 Arming。
        from core import grow_runbook as rb
        channels = [c.strip() for c in (args.channels or "email").split(",")
                    if c.strip()]
        actions = [a.strip() for a in
                   (getattr(args, "wizard_action", "") or "message").split(",")
                   if a.strip()]
        plan = rb.build_start_plan(
            DATA, facts_path=args.facts, goal_id=args.goal,
            run_id=getattr(args, "run_id", "") or "",
            target=args.target, channels=channels, actions=actions,
            actions_per_week=int(args.actions_per_week or 7),
            account=getattr(args, "account", "") or "",
            handle=args.handle or "",
            audience=getattr(args, "audience", "") or "us-west",
            owner=args.owner or "", started=args.on or "",
            schedule=getattr(args, "schedule", "foreground") or "foreground",
            delivery=getattr(args, "delivery", "local") or "local",
            email_owner=getattr(args, "email_owner", "") or "",
            email_authorized=bool(getattr(args, "authorize_email", False)),
            source_systems=getattr(args, "source_system", []) or [])
        if getattr(args, "apply", False):
            try:
                result = rb.apply_start_plan(plan, DATA)
            except rb.RunbookError as exc:
                print(f"计划未创建（{exc.code}）：{exc}")
                return 2
            if getattr(args, "json", False):
                print(json.dumps(result, ensure_ascii=False, indent=1))
                return 0
            print(rb.render_start_plan(result))
            print(f"\n已建计划：{result['run_path']}｜status={result['status']}"
                  "｜go=False")
            print(f"下一步：mkt grow arm {result['run_id']}（必须由人显式执行）")
            return 0
        if getattr(args, "json", False):
            print(json.dumps(plan, ensure_ascii=False, indent=1))
            return 0
        print(rb.render_start_plan(plan))
        return 0

    if act == "start":
        channels = [c.strip() for c in
                    (args.channels or "x,email").split(",") if c.strip()]
        try:
            data = GrowRun.start(path, args.id, args.facts, args.goal, channels,
                                 target=args.target, window_days=args.window,
                                 started=args.on or None,
                                 actions_per_week=args.actions_per_week or 20,
                                 go=args.go, data_dir=DATA, owner=args.owner or "")
        except GrowErrors as exc:
            print(f"不能开始：{exc}")
            payload = getattr(exc, "payload", None)
            if payload:
                from core import procurement
                print(procurement.render(payload))
            return 2
        goal = data["goal"]
        print(f"闭环已建：{data['id']}（{data['started']} 起，渠道 {'、'.join(data['channels'])}）")
        print(f"  目标：{goal.get('target_text') or goal.get('metric')}"
              f"（基线 {goal.get('baseline')}）")
        print(f"  实跑开关 go={data['go']}（默认只 dry-run；确认后 mkt grow arm {data['id']}）")
        print(f"  暂存队列放 data/queues/{data['id']}-<名字>.json（campaign 用 {data['id']} 开头），"
              f"cron 每小时跑：mkt grow tick {data['id']}")
        print(f"  读数：每天一次 mkt x stats --handle <账号>（首周人工对数后加 --record）")
        return 0

    if act == "launch":
        from core.grow_v2 import GrowRunV2
        channels = [c.strip() for c in (args.channels or "x,email").split(",") if c.strip()]
        actions = [a.strip() for a in (getattr(args, "actions", "") or "").split(",") if a.strip()]
        probe = None
        if "x" in channels and not getattr(args, "skip_probe", False):
            def probe(spec):
                from core.session_gate import CDP_UNAVAILABLE, LOGGED_OUT, check_login
                login = check_login("x")
                if login.get("status") == LOGGED_OUT:
                    raise RuntimeError("X 浏览器登录态缺失：请在调试 Chrome 里登录 X 后重试（引擎只检测登录态，不管理账号）")
                if login.get("status") == CDP_UNAVAILABLE:
                    raise RuntimeError("调试 Chrome 未就绪：先运行 ./launch.command（X 读数依赖浏览器登录态）")
                from channels.x import read_stats
                readers = spec.get("measurement", {}).get("readers") or []
                handle = next((str(r.get("handle") or "") for r in readers
                               if r.get("metric") == "x_followers"), "")
                if not handle:
                    raise RuntimeError("启动预检需要 --handle <X账号>（或加 --skip-probe 只建计划）")
                stats = read_stats.read(handle, DATA / "shots" / "x-stats", log=lambda *a, **k: None)
                return {"followers": stats.get("followers"),
                        "impressions": stats.get("impressions_yesterday")}
        try:
            result = GrowRunV2.launch(
                path, run_id=args.id, facts_path=args.facts, goal_id=args.goal,
                channels=channels, target=args.target, window_days=args.window,
                actions_per_week=args.actions_per_week, started=args.on or None,
                owner=args.owner or "", account=getattr(args, "account", "") or "",
                handle=getattr(args, "handle", "") or "",
                audience=getattr(args, "audience", "us-west") or "us-west",
                actions=actions or None, data_dir=DATA,
                probe=probe, skip_probe=getattr(args, "skip_probe", False))
        except GrowErrors as exc:
            print(f"不能启动：{exc}")
            payload = getattr(exc, "payload", None)
            if payload:
                from core import procurement
                print(procurement.render(payload))
            return 2
        run = result["run"]
        strategy = run.get("strategy") or {}
        auto = strategy.get("autonomous_channels") or []
        assisted = strategy.get("assisted_channels") or []
        print(f"闭环已启动：{run['id']}（{run['started']} 起，计划渠道 "
              f"{'、'.join(run['channels'])}；自主执行 {'、'.join(auto) or '无'}；"
              f"人工辅助 {'、'.join(assisted) or '无'}）")
        for line in result["summary"]:
            print(f"  {line}")
        for warn in result.get("warnings") or []:
            print(f"  ⚠ {warn}")
        print(f"  策略指纹 {run['strategy']['fingerprint']}｜"
              f"下一步：mkt grow arm {run['id']}")
        return 0

    if act in ("agent", "produce"):
        from core.grow_agent import AgentError
        from core.grow_lock import BusyRun
        try:
            run, raw = _open()
            if raw.get("schema") != "growovo.grow-run/2":
                raise V2Error("自动供给需要 v2 闭环")
            if act == "agent":
                result = run.configure_agent(enabled=not args.disable)
            else:
                result = run.produce(DATA, on=args.on or None, max_items=args.max_items)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            # 内容供给是“产出驱动”的命令：有拒绝且一条都没补进队列时，不能
            # 用 exit 0 让 cron/脚本误以为今天已经供上货。agent 开关命令不受影响。
            if act == "produce" and not result.get("added") and result.get("rejected"):
                return 1
            return 0
        except (V2Error, AgentError, BusyRun, FileNotFoundError) as exc:
            print(f"自动供给未完成：{exc}")
            payload = getattr(exc, "payload", None)
            if payload:
                from core import procurement
                print(procurement.render(payload))
            return 2

    if act == "briefs":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            print("briefs 只支持 v2 闭环（先 mkt grow launch）")
            return 2
        briefs = run.briefs()
        if not briefs:
            print("（本闭环没有内容需求单）")
            return 0
        for brief in briefs:
            print(f"{brief['id']}（{brief['channel']}/{brief['action']}）："
                  f"每周 {brief.get('count_per_week')} 条"
                  f"｜公式 {brief.get('formula')}")
            print(f"  受众 {brief.get('audience')}｜上限 {brief.get('max_len')} 字符"
                  f"｜门 {'、'.join(brief.get('gates') or [])}")
        print(f"\n完整需求单：{DATA / 'grow' / args.id / 'briefs.md'}")
        return 0

    if act == "stage":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            print("stage 只支持 v2 闭环（先 mkt grow launch）")
            return 2
        from core.grow_v2 import GrowRunV2
        try:
            items = GrowRunV2.load_drafts(args.from_file)
            result = run.stage(DATA, items, scheduled_for=args.date or None,
                               channel=args.channel or None,
                               action=args.queue_action or None,
                               window=args.window or "",
                               by=args.by or "agent",
                               resolve_target_text=bool(args.resolve_target),
                               strict=bool(args.strict))
        except GrowErrors as exc:
            print(f"不能暂存：{exc}")
            return 2
        supply = result.get("supply") or {}
        print(f"已暂存 {result['added']} 条 → {result['path']}"
              f"（队列共 {result['total']} 条）")
        print(f"  供给：已暂存 {supply.get('staged', 0)} 条"
              + (f"｜未来 {supply.get('horizon_days')} 天缺 {supply['gap']}"
                 if supply.get("gap") else "｜未来 3 天充足"))
        quality = result.get("quality") or {}
        if quality.get("average") is not None:
            print(f"  内容结构分：均值 {quality['average']}｜低于 55 的 {quality['low']} 条")
            if quality["low"]:
                print("  低分内容照发也可能有效，但先按 mkt grow formulas 重写更稳")
        return 0

    if act == "arm":
        from core import grow_runbook as rb
        path = DATA / "grow" / f"{args.id}.json"
        if not path.is_file():
            print(f"没有这个闭环：{path}")
            return 2
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not (args.by or "").strip():
            print("AUTH_REQUIRED：arm 必须带 --by <operator> 署名；"
                  "开闸是人的决定，Agent 不能自签")
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            from core.grow import GrowRun
            try:
                GrowRun(path).arm()
            except Exception as exc:  # noqa: BLE001 旧版兼容路径
                print(f"不能开闸：{exc}")
                return 2
            print(f"{args.id}（旧版闭环）已开闸；v1 不记录署名，建议迁移到 "
                  "mkt grow launch")
            return 0
        try:
            data = rb.arm_run(DATA, path, by=args.by)
        except rb.RunbookError as exc:
            print(f"不能开闸（{exc.code}）：{exc}")
            return 2
        except GrowErrors as exc:
            print(f"不能开闸：{exc}")
            return 2
        print(f"{args.id} 实跑已开闸（{data.get('armed_by')}）。"
              "急停随时可用：mkt pause --on '原因'（暂停闸优先于一切写操作）")
        observed = data.get("observed_account") or {}
        if observed:
            if observed.get("status") == "observed":
                print(f"  当前浏览器账号：{observed.get('handle') or '(no handle)'}"
                      f"（id={observed.get('user_id')}，{observed.get('channel')}）"
                      "；每次动作会重新探测并写入账本")
            else:
                print(f"  ⚠ 当前浏览器账号未能读取（{observed.get('reason') or 'unknown'}）；"
                      "真实动作会被 CDP 身份门禁挡住")
        return 0

    if act == "pause":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        try:
            data = run.pause(reason=args.reason or "", by=args.by or "")
        except GrowErrors as exc:
            print(f"不能暂停：{exc}")
            return 2
        print(f"{args.id} 已暂停：status={data.get('status')}"
              "｜下一轮 tick 不再选中任何条目；恢复用 mkt grow resume")
        return 0

    if act == "incident":
        from core import grow_runbook as rb
        path = DATA / "grow" / f"{args.id}.json"
        if not path.exists():
            print(f"没有这个闭环：{path}")
            return 2
        probe = None
        if getattr(args, "probe_login", False):
            from core import session_gate
            probe = session_gate.check_login
        report = rb.list_incidents(DATA, path, on=args.on or None,
                                   login_probe=probe)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=1))
        else:
            print(rb.render_incidents(report))
        return 0

    if act == "resume":
        from core import grow_runbook as rb
        path = DATA / "grow" / f"{args.id}.json"
        if not path.is_file():
            print(f"没有这个闭环：{path}")
            return 2
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema") != "growovo.grow-run/2":
            try:
                from core.grow import GrowRun
                data = GrowRun(path).resume(note=args.note or "",
                                            by=args.by or "")
            except Exception as exc:  # noqa: BLE001 旧版兼容路径
                print(f"不能恢复：{exc}")
                return 2
            print(f"{args.id} 已恢复（旧版闭环）：status={data.get('status')}")
            return 0
        try:
            result = rb.resume_run(
                DATA, path, by=args.by or "", note=args.note or "",
                after_reconciliation=getattr(args, "after_reconciliation", "")
                or "")
        except rb.RunbookError as exc:
            print(f"不能恢复（{exc.code}）：{exc}")
            return 2
        except GrowErrors as exc:
            print(f"不能恢复：{exc}")
            return 2
        if not result.get("resumed"):
            print(f"{args.id} 当前不需要恢复（status={result.get('status')}）")
            return 0
        print(f"{args.id} 已恢复：status={result['status']}"
              + (f"｜事件 {args.after_reconciliation} 已核对"
                 if getattr(args, "after_reconciliation", "") else "")
              + "（暂停闸与执行门照旧生效）")
        return 0

    if act == "tick":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") == "growovo.grow-run/2":
            summary = run.tick(DATA, on=args.on or None,
                               deliver=getattr(args, "deliver", False))
            if summary.get("skipped"):
                print(f"tick（{summary.get('on') or '今天'}）：跳过（{summary['skipped']}）")
                return 0
            readings = summary.get("readings") or {}
            supply = summary.get("supply") or {}
            published = sum(int(r.get("published") or 0) for r in summary.get("ran") or [])
            print(f"tick（{summary.get('on')}）：状态 {summary.get('status')}"
                  f"｜发布 {published} 条｜读数 粉丝 "
                  f"{readings.get('followers') if readings.get('followers') is not None else '取不到'}"
                  f" / 曝光 {readings.get('impressions') if readings.get('impressions') is not None else '取不到'}"
                  f"｜供给 已暂存 {supply.get('staged', 0)} 条"
                  + (f"（缺 {supply['gap']}）" if supply.get("gap") else ""))
            for r in summary.get("ran") or []:
                line = f"  {Path(r['queue']).name}：{r.get('records', 0)} 条"
                if r.get("error"):
                    line += f"｜⚠ {r['error']}"
                elif r.get("note"):
                    line += f"｜{r['note']}"
                print(line)
            weekly = summary.get("weekly")
            if weekly:
                print(f"  周结果单：第 {weekly.get('week')} 周 "
                      f"（{weekly.get('week_start')}~{weekly.get('week_end')}）"
                      f"发布 {weekly['published']['total']}")
            if summary.get("status") == "needs_attention":
                print("  有队列需要人工处理（needs_attention 不自动重试）。"
                      f"处理完恢复：mkt grow resume {args.id} "
                      "--by \"$USER\" --note '处理了什么'")
                return 3
            return 0
        summary = run.tick(DATA, on=args.on or None)
        if summary.get("skipped"):
            print(f"tick（{summary.get('on') or '今天'}）：跳过（{summary['skipped']}）")
            return 0
        print(f"tick（{summary.get('on')}）：跑了 {len(summary.get('ran') or [])} 个队列"
              f"｜状态 {summary.get('status')}")
        for r in summary.get("ran") or []:
            line = f"  {Path(r['queue']).name}：{r['records']} 条"
            if r.get("error"):
                line += f"｜⚠ {r['error']}"
            elif r.get("note"):
                line += f"｜{r['note']}"
            print(line)
        card = summary.get("card")
        if card:
            print(f"  周结果卡：第 {card['week']} 周（{card['start']}~{card['end']}）"
                  f"发布 {card['published']}｜日均曝光 {card['avg_daily_impressions']}"
                  f"｜新增粉丝 {card['new_followers']}（取不到=该日无读数）")
        if summary.get("status") == "needs_attention":
            print("  有队列需要人工处理（needs_attention 不自动重试）。"
                  f"查完最后一轮结果后恢复：mkt grow resume {args.id} "
                  "--by \"$USER\" --note '处理了什么'")
            return 3
        return 0

    if act == "daily":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            print("daily 只支持 v2 闭环（先 mkt grow launch）")
            return 2
        card = run.daily(DATA, on=args.on or None)
        from core.grow_v2 import GrowRunV2
        print(GrowRunV2.render_daily(card))
        return 0

    if act == "decide":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            print("decide 只支持 v2 闭环（先 mkt grow launch）")
            return 2
        try:
            result = run.decide(action=args.decision or "", note=args.note or "",
                                by=args.by or "", on=args.on or None,
                                dry_run=bool(getattr(args, "dry_run", False)),
                                data_dir=DATA)
        except GrowErrors as exc:
            print(f"不能记录决定：{exc}")
            return 2
        decision = result["decision"]
        if getattr(args, "json", False):
            print(json.dumps(result, ensure_ascii=False, indent=1))
            return 0
        print(f"已记录决定：{decision['action']}（{decision['on']}）")
        if decision.get("note"):
            print(f"  说明：{decision['note']}")
        proposal = result.get("proposal") or {}
        assessment = proposal.get("assessment") or {}
        if assessment:
            print(f"  口径：{assessment.get('wording') or assessment.get('analysis_mode')}")
        for line in proposal.get("rationale") or []:
            print(f"  理由：{line}")
        for change in decision.get("changes") or []:
            print(f"  调整：{change.get('detail') or change}")
        for item in decision.get("held") or []:
            change = item.get("change") or {}
            print(f"  待人审：{change.get('param')} → {change.get('to')}"
                  f"｜{item.get('detail') or ''}")
            if item.get("change_sha256"):
                print(f"    批准：mkt grow change approve {args.id} "
                      f"--change {item['change_sha256']} --by <谁> --apply")
        if decision.get("status") == "needs_review":
            print("  当前仍按旧策略运行：未批准前不改参数，也不停跑。")
        if getattr(args, "dry_run", False):
            print("  （dry-run：只出提案，未落盘）")
        return 0

    if act == "formulas":
        from core import grow_content
        print(grow_content.render_formulas())
        return 0

    if act == "prospects":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            print("prospects 只支持 v2 闭环（先 mkt grow launch）")
            return 2
        queries = list(getattr(args, "query", None) or [])
        from core import grow_fit
        enricher = None if not getattr(args, "no_enrich", False) else (
            lambda handle: {"handle": handle})
        try:
            result = run.prospects(DATA, queries, limit=args.limit, top=args.top,
                                   max_age_hours=args.max_age_hours,
                                   min_likes=args.min_likes, pages=args.pages,
                                   on=args.on or None, enricher=enricher,
                                   koc_only=(True if args.koc_only else
                                             (False if args.all_accounts else None)),
                                   min_followers=args.min_followers or None,
                                   max_followers=args.max_followers or None,
                                   include_engaged=args.include_engaged,
                                   seen_days=args.exclude_seen_days)
        except Exception as exc:  # noqa: BLE001
            print(f"账号发现失败：{exc}")
            return 2
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1))
        else:
            print(grow_fit.render(result["prospects"], limit=args.top))
            print(f"已存：{result['path']}（候选帖 {result['targets']} 条 / "
                  f"账号 {result['count']} 个 / KOC {result['koc']} 个 / "
                  f"读主页 {result['enriched']} 个 / 排除已触达 "
                  f"{result.get('excluded_engaged', 0)} 个）")
            print(f"  搜索词：{'；'.join(result.get('queries') or [])}")
            print(f"  KOC 名单已更新：{result.get('watchlist')}")
            if result.get("koc_only"):
                print("筛选：只看 KOC（1k–50k）；要放宽加 --all-accounts")
            if result.get("near_misses"):
                print("本轮没筛到 KOC；接近的账号：")
                for row in result["near_misses"]:
                    print(f"  @{row['handle']}（{row.get('koc_tier')}｜"
                          f"{row.get('followers')} 粉丝｜{row['score']} 分｜"
                          f"{row.get('why_not', '')}）")
            print("下一步：选 A/B 级账号的最佳帖子 → 按内容公式写回复 → mkt grow stage")
        return 0

    if act == "koc":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            print("koc 只支持 v2 闭环（先 mkt grow launch）")
            return 2
        result = run.watchlist(DATA)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1))
        else:
            print(result["rendered"])
        return 0

    if act == "metrics":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            print("metrics 只支持 v2 闭环（先 mkt grow launch）")
            return 2
        result = run.metrics(DATA, days=args.days, limit=args.limit,
                             on=args.on or None, resolve=bool(args.resolve))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1))
        else:
            print(f"帖子指标：待采 {result['pending']} 条｜已写 {result['written']} 条")
            for item in result.get("items") or []:
                print(f"  {item.get('url')}：查看 {item.get('views')}｜喜欢 "
                      f"{item.get('likes')}｜回复 {item.get('replies')}"
                      + (f"｜{item['note']}" if item.get("note") else ""))
        return 0

    if act == "learn":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            print("learn 只支持 v2 闭环（先 mkt grow launch）")
            return 2
        result = run.learn(DATA, on=args.on or None)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1))
        else:
            print(result["rendered"])
            print(f"已存：{result['path']}")
            if result.get("changes"):
                print("可用：mkt grow decide %s --action optimize --dry-run 查看提案" % args.id)
        return 0

    if act == "targets":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            print("targets 只支持 v2 闭环（先 mkt grow launch）")
            return 2
        queries = list(getattr(args, "query", None) or [])
        if not queries:
            print("至少给一个 --query（可重复）")
            return 2
        from core import grow_targets
        try:
            result = run.targets(DATA, queries,
                                 limit=args.limit, max_age_hours=args.max_age_hours,
                                 min_likes=args.min_likes,
                                 min_replies=args.min_replies,
                                 pages=args.pages, on=args.on or None)
        except Exception as exc:  # noqa: BLE001 搜索失败要给人话
            print(f"目标发现失败：{exc}")
            return 2
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1))
        else:
            print(grow_targets.render(result["candidates"]))
            print(f"已存：{result['path']}（排除已发/已暂存/自己 {result['excluded_urls']} 个目标）")
            print("下一步：按 brief 写回复 → mkt grow stage 入队（不要因为搜到就直接发）")
        return 0

    if act == "review":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            print("review 只支持 v2 闭环（先 mkt grow launch）")
            return 2
        result = run.review(DATA, days=args.days, on=args.on or None)
        if args.json:
            print(json.dumps(result["summary"], ensure_ascii=False, indent=1))
        else:
            print(result["markdown"])
            if result.get("paths"):
                print(f"已存：{result['paths'].get('html') or result['paths'].get('markdown')}")
        return 0

    if act == "daemon":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            print("daemon 只支持 v2 闭环（先 mkt grow launch）")
            return 2
        try:
            run.daemon(DATA, interval_minutes=args.interval,
                       deliver=not args.no_deliver, once=args.once)
        except V1Error as exc:
            print(f"daemon 未启动：{exc}")
            return 2
        return 0

    if act == "calibrate":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            print("calibrate 只支持 v2 闭环（先 mkt grow launch）")
            return 2
        result = run.calibrate(followers=getattr(args, "followers", None),
                               match=not getattr(args, "mismatch", False),
                               note=args.note or "", by=args.by or "",
                               on=args.on or None)
        bar = "一致" if result["match"] else "不一致"
        print(f"人工对数：{result['on']} 自动 {result['auto']} / 人工 {result['human']}"
              f" → {bar}｜连续 {result['streak']}/7 个日历日｜当前级别 {result['verification']}")
        if result.get("invalid_reasons"):
            print(f"本次不计数：{'; '.join(result['invalid_reasons'])}")
        if result["promoted"]:
            print("读数已升级 PROVEN：后续结果单仍保留 approximated 快照口径")
        return 0

    if act == "status":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if getattr(args, "explain", False):
            from core import grow_runbook as rb
            if raw.get("schema") != "growovo.grow-run/2":
                print("--explain 只支持 v2 闭环（先 mkt grow launch）")
                return 2
            explain = rb.explain_status(DATA, path, on=args.on or None)
            if getattr(args, "json", False):
                print(json.dumps(explain, ensure_ascii=False, indent=1))
            else:
                print(rb.render_status(explain))
            return 0
        if raw.get("schema") == "growovo.grow-run/2":
            s = run.status(DATA, on=args.on or None)
            goal = s.get("goal") or {}
            heartbeat = s.get("heartbeat") or {}
            supply = s.get("supply") or {}
            print(f"{s['run']}｜第 {s['week']} 周｜状态 {s['status']}｜实跑 go={s['go']}")
            print(f"  目标进度：{goal.get('target_text') or goal.get('metric')}"
                  f"｜当前 {goal.get('current') if goal.get('current') is not None else '取不到'}"
                  f"｜基线 {goal.get('baseline') if goal.get('baseline') is not None else '取不到'}"
                  + (f"｜{goal['progress']}%" if goal.get("progress") is not None else ""))
            print(f"  今日配额 {s.get('today_quota')}｜已发 {s['committed_today']} 条"
                  f"｜本周计划 {s['plan_actions_per_week']} 条")
            print(f"  供给：已暂存 {supply.get('staged', 0)} 条"
                  + (f"（未来 {supply.get('horizon_days')} 天缺 {supply['gap']}）"
                     if supply.get("gap") else "（充足）"))
            today_read = s.get("readings") or {}
            has_reading = (today_read.get("followers") is not None
                           or today_read.get("impressions") is not None)
            print(f"  读数：今天 {'有' if has_reading else '无（' + str(today_read.get('error') or '未读')[:40] + '）'}"
                  f"｜心跳 {'正常' if not heartbeat.get('stale', True) else '过期/未记录'}"
                  f"｜今日决策 {'已记录' if not s['pending_decision'] else '未记录（默认继续）'}")
            print(f"  队列 {s['queues']} 个｜已出周卡 {len(s['result_cards'])} 张"
                  f"｜日卡 {s['daily_cards']} 张｜调整记录 {s['adjustments']} 条")
            return 0
        s = run.status(DATA, on=args.on or None)
        goal = s.get("goal") or {}
        print(f"{s['run']}｜第 {s['week']} 周｜状态 {s['status']}｜实跑 go={s['go']}")
        print(f"  目标进度：{goal.get('metric')} 当前 {goal.get('current')}"
              f"（基线 {goal.get('baseline')} → 目标 {goal.get('target')}）"
              + (f" {goal['progress']}%" if goal.get("progress") is not None else ""))
        if goal.get("note"):
            print(f"  {goal['note']}")
        print(f"  本周计划量 {s['plan_actions_per_week']} 条"
              f"｜今日待跑队列 {len(s['pending_queues_today'])} 个"
              f"｜已出结果卡 {len(s['result_cards'])} 张｜调整记录 {s['adjustments']} 条")
        return 0

    if act == "report":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") == "growovo.grow-run/2":
            if getattr(args, "json", False):
                data = run.load()
                cards = data.get("result_cards") or []
                if args.week is not None:
                    cards = [c for c in cards if int(c.get("week") or 0) == int(args.week)]
                if cards:
                    payload = cards[-1]
                else:
                    from datetime import date
                    on = args.on or date.today().isoformat()
                    payload = {
                        "run": data["id"],
                        "status": "not_ready",
                        "reason": ("week not completed yet" if args.week is None
                                   else f"week {args.week} has no result card yet"),
                        "started": data.get("started"),
                        "on": on,
                        "result_cards": 0,
                        "next": f"mkt grow report {data['id']}",
                    }
                print(json.dumps(payload, ensure_ascii=False, indent=1))
            else:
                print(run.report(DATA, week=getattr(args, "week", None),
                                 on=args.on or None))
        else:
            print(run.report(DATA, on=args.on or None))
        return 0

    if act == "doctor":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            print("doctor 只支持 v2 闭环（旧版请重新 mkt grow launch）")
            return 2
        result = run.doctor(DATA)
        print(f"体检：{'通过' if result.get('ok') else '有阻断项'}")
        for check in result.get("checks") or []:
            mark = "✅" if check.get("ok") else ("⛔" if check.get("level") == "blocker" else "⚠️")
            print(f"  {mark} {check.get('name')}：{check.get('detail')}")
            if not check.get("ok") and check.get("fix"):
                print(f"     修法：{check['fix']}")
        return 0 if result.get("ok") else 2

    if act == "install":
        try:
            run, raw = _open()
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        if raw.get("schema") != "growovo.grow-run/2":
            print("install 只支持 v2 闭环（旧版请重新 mkt grow launch）")
            return 2
        result = run.install(DATA, interval_minutes=int(getattr(args, "interval", 30) or 30),
                             load=bool(getattr(args, "load", False)))
        print(f"定时任务已生成：{result['path']}")
        if result.get("fallback"):
            print(f"  前台常驻脚本：{result['fallback']}（双击运行，Ctrl-C 停止）")
        print(f"  安装命令：{result.get('command')}")
        if result.get("installed"):
            print(f"  launchd：{result['installed']}")
        if result.get("verified") is False:
            print(f"  ⚠ {result.get('reason')}")
            return 2
        if result.get("error"):
            print(f"  ⚠ {result['error']}")
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
        facts = _load_facts(args.facts)
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






def _git_first_recorded(claims):
    """用 git 历史给没有日期的记录定一个可追溯的「首次记录日」。

    这不是来源发布日期，是这条经验第一次进入经验库的日期；只在采集核验里
    当 observed_at 用，并在 ingest.date_basis 里写明。
    """
    import subprocess
    target = "docs/playbooks/经验库/experience.jsonl"
    out = {}
    for claim in claims:
        if not claim:
            continue
        try:
            proc = subprocess.run(
                ["git", "log", "--format=%ad", "--date=short", f"-S{claim}", "--", target],
                cwd=BASE, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        dates = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if dates:
            out[claim] = dates[-1]
    return out


def cmd_exp(args):
    from core import experience
    if args.action == "assess":
        row = json.loads(Path(args.file).read_text(encoding="utf-8"))
        payload = experience.assess_eligibility(row, purpose=args.purpose, on=args.on or None)
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0 if payload["eligible"] else 2
    if args.action == "adopt":
        rows = experience.load()
        dates = None
        if getattr(args, "date_from_git", False):
            def _sup(row):
                try:
                    return int(row.get("support") or 0)
                except (TypeError, ValueError):
                    return 0
            pending = [r.get("claim") for r in rows
                       if (_sup(r) >= args.min_support
                           and len(r.get("sources") or []) >= args.min_support
                           and not (r.get("collected_at") or r.get("published")
                                    or r.get("observed_at")))]
            dates = _git_first_recorded(pending)
        new, report = experience.adopt_rows(rows, min_support=args.min_support,
                                            on=args.on or None, dates=dates,
                                            revisit_leads=getattr(args, "revisit_leads", False))
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=1))
        else:
            print(f"共 {report['total']} 条：已有类型 {report['already_typed']}，"
                  f"补成多源实践 {report['multi_source']}，标为单源线索 {report['lead']}")
            for pid, n in sorted(report["platforms"].items()):
                print(f"  {pid}: +{n}")
            if not args.apply:
                print("\ndry-run：没有写盘；加 --apply 写回 experience.jsonl")
        if args.apply:
            path = experience.save_rows(new)
            print(f"已写回 {path}：{report['multi_source']} 条多源实践，"
                  f"{report['lead']} 条单源线索")
        return 0
    if args.action == "usable":
        rows = experience.eligible(args.platform, purpose="craft")[:args.limit]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        elif not rows:
            print("没有通过内容工艺口径的经验；先跑 mkt exp adopt --apply")
        else:
            for r in rows:
                kind = experience.normalize_knowledge_type(r.get("knowledge_type"))
                print(f"- [{kind}] {r['claim']}")
                if r.get("action"):
                    print(f"  → {r['action']}")
        return 0
    if args.action == "show":
        print(experience.render(args.platform))
    elif args.action == "coverage":
        fresh = experience.freshness()
        for pid, n in experience.coverage().items():
            q = [x for x in experience.stale() if x["platform"] == pid]
            newest = q[0]["newest_days"] if q else None
            age = f"最新 {newest} 天前" if newest is not None else "无发布时间"
            print(f"  {pid:<18} {n:>3} 条   {age}")
        by_type = {}
        for row in experience.load():
            kind = experience.normalize_knowledge_type(row.get("knowledge_type"))
            by_type[kind or "unknown"] = by_type.get(kind or "unknown", 0) + 1
        print("\n证据轨道：" + "，".join(f"{k} {v}" for k, v in
              sorted(by_type.items(), key=lambda x: -x[1])))
        print(f"窗口 {fresh['window_days']} 天：{fresh['fresh_claims']}/{fresh['claims']} 条在窗口内"
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
    elif args.action == "plain":
        from core import expression
        text = args.text or ""
        if args.file:
            text = Path(args.file).read_text(encoding="utf-8")
        if args.fix:
            fixed, _hits = expression.fix(text)
            print(fixed)
            return 0
        res = expression.check(text)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            print(expression.render(res))
        return 0 if res["ok"] else 2
    return 0


def _content_facts(path):
    """Load product facts for content positioning; None when not supplied."""
    if not str(path or "").strip():
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("--facts 需要 JSON 对象（product/icp/value_events）")
    return data


def cmd_content(args):
    from core import content_ops
    if args.action == "rules":
        print(content_ops.render_rules(args.channel))
        return 0
    if args.action == "brief":
        b = content_ops.brief(
            args.channel, args.topic, audience=args.audience or "",
            product=args.product or "", evidence=args.evidence or "",
            cta=args.cta, title=args.title or "",
            facts=_content_facts(getattr(args, "facts", "")))
        if args.json:
            print(json.dumps(b, ensure_ascii=False, indent=1))
        else:
            print(content_ops.render_brief(b))
        return 0
    if args.action == "review":
        from core import editorial_review
        text = Path(args.file).read_text(encoding="utf-8") if args.file else (args.text or "")
        result = (editorial_review.validate(args.channel, text,
                    json.loads(Path(args.record).read_text(encoding="utf-8")))
                  if args.record else editorial_review.packet(args.channel, text, args.title))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 2
    if args.action == "audit":
        if args.file:
            text = Path(args.file).read_text(encoding="utf-8")
        else:
            text = args.text or ""
        titles = None
        if args.titles:
            titles = json.loads(Path(args.titles).read_text(encoding="utf-8"))
        if args.file and "## D2-" in text:
            res = content_ops.audit_file(args.channel, text, strict=args.strict, titles=titles)
        else:
            res = content_ops.audit_text(args.channel, text, title=args.title or "",
                                         strict=args.strict)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            print(content_ops.render_audit(res))
        return 0 if res["ok"] else 2
    if args.action == "plan":
        channels = [x.strip() for x in (args.channels or "").split(",") if x.strip()]
        raw_weekdays = (getattr(args, "weekdays", "") or "").strip()
        weekdays = None
        if raw_weekdays:
            names = [x.strip() for x in raw_weekdays.split(",") if x.strip()]
            weekdays = {c: names for c in content_ops.CHANNELS}
        p = content_ops.plan(start=args.start or None, days=args.days,
                             channels=channels,
                             cadence=getattr(args, "cadence", "evidence"),
                             facts=_content_facts(getattr(args, "facts", "")),
                             weekdays=weekdays)
        if args.json:
            text = json.dumps(p, ensure_ascii=False, indent=1)
        else:
            text = content_ops.render_plan(p)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
            print(f"已写入 {args.out}")
            if not args.json:
                return 0
        print(text)
        return 0
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
    facts = _load_facts(args.facts)
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
            from core import error_semantics
            payload = getattr(exc, "payload", None)
            if isinstance(payload, dict):
                print(json.dumps(payload, ensure_ascii=False, indent=1))
            elif os.environ.get("GROWOVO_ERROR_JSON") == "1":
                print(json.dumps(error_semantics.describe(exc), ensure_ascii=False, indent=1))
            else:
                print("业务操作被拒绝：" + "\n".join(
                    error_semantics.human_lines(error_semantics.describe(exc))), file=sys.stderr)
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
        if actions is None:
            actions = ["email:message"]
            print("提示：未指定 --actions，默认只授权 email:message；其他动作请显式列出。",
                  file=sys.stderr)
        accounts = [x.strip() for x in args.accounts.split(",") if x.strip()] or None
        days = None if args.days == 0 else args.days
        if days is None:
            print("警告：--days 0 表示常驻授权不过期；仅在你有明确理由时使用。", file=sys.stderr)
        try:
            policy = execution_policy.authorize(DATA, by=args.by, actions=actions,
                                                accounts=accounts, days=days,
                                                identities=args.identity or None,
                                                require_identity=args.require_identity,
                                                observe_identity=args.observe_account)
        except ValueError as exc:
            print(f"授权失败：{exc}", file=sys.stderr)
            return 2
        return _emit(policy)
    if args.action == "disable":
        return _emit(execution_policy.revoke(DATA, by=args.by))
    if args.action == "list-actions":
        return _emit({"routine_actions": execution_policy.list_routine_actions()})
    if args.action == "migrate-identity":
        from core import account_identity
        try:
            result = account_identity.migrate_identity(
                DATA, by=args.by, identities=args.identity or [],
                apply=bool(getattr(args, "apply", False)))
        except ValueError as exc:
            print(f"迁移失败：{exc}", file=sys.stderr)
            return 2
        return _emit(result)
    if args.action == "identity":
        from core import account_identity
        return _emit(account_identity.identity_status(DATA))
    if args.action == "check":
        allowed = execution_policy.allows(DATA, args.channel, args.action_name,
                                          account=args.account,
                                          platform_user_id=args.platform_user_id or None)
        _emit({"allowed": allowed, "channel": args.channel, "action": args.action_name,
               "account": args.account})
        return 0 if allowed else 2
    policy = execution_policy.get_policy(DATA)
    if isinstance(policy, dict):
        warnings = []
        if not policy.get("expires_at"):
            warnings.append("该常驻授权没有有效期；建议用 --days 重新启用，或显式接受长期授权")
        broad = sorted(set(policy.get("allow_actions") or []) - {"email:message"})
        if broad:
            warnings.append("该常驻授权包含非默认动作：" + "、".join(broad)
                            + "；请确认仍是当前需要的最小范围")
        if warnings:
            policy = dict(policy, warning=warnings[0], warnings=warnings)
    return _emit(policy)


@_business_command
def cmd_cycle(args):
    """夜间互动循环：通知优先 → 实时选路 → 额度/间隔 → 逐条写。

    本命令逐条经过 action_runner，并显式选择 browser-attach 传输；不走通用
    CDP 身份或 plan/routine 预检，授权由 data/policies/owner-attested-*.json
    的 owner_attested_session 独立承担。run 默认 dry-run，--go 才执行；暂停、
    DNC、页面身份、日预算、回读、账本、任务结果和统一操作日志全部保留。
    """
    import datetime as _dt
    from core import engagement_cycle as cycle

    def _when():
        text = str(getattr(args, "now", "") or "").strip()
        if not text:
            return cycle._now()
        got = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if got.tzinfo is None:
            got = got.replace(tzinfo=_dt.datetime.now().astimezone().tzinfo)
        return got

    def _queue_path():
        return Path(str(getattr(args, "queue", "") or "")
                    or (DATA / "state" / "engagement-queue.json"))

    def _log(message):
        print(message, file=sys.stderr)

    if args.action == "smoke":
        got = cycle.smoke()
        _emit(got)
        return 0 if got.get("ok") else 2

    if args.action == "quota":
        return _emit(cycle.quota_snapshot(DATA, now=_when()))

    if args.action == "pending-auth":
        from core import browser_write
        return _emit(browser_write.pending_authorization(DATA))

    if args.action == "attach":
        from core.browser_attach import BridgeError, BrowserAttach
        attach = BrowserAttach(getattr(args, "session", "") or cycle.bridge_session(
            cycle.load_policy(DATA)) or "ovo-night-run")
        out = {"ok": False, "session": attach.session, "tabs": [], "identities": {}}
        try:
            out["tabs"] = [{"tabId": t.get("tabId"), "site": t.get("site"),
                            "url": str(t.get("url") or "")[:160],
                            "title": str(t.get("title") or "")[:80]}
                           for t in attach.list_tabs()]
        except BridgeError as exc:
            out.update(error=exc.code, message=str(exc))
            _emit(out)
            return 2
        for site in ("x", "github", "discord"):
            try:
                tab = attach.attach_site(site)
                out["identities"][site] = attach.observe_identity(site, tab)
            except BridgeError as exc:
                out["identities"][site] = {"enforced": False, "error": exc.code,
                                           "message": str(exc)}
        out["ok"] = all(row.get("handle") for row in out["identities"].values())
        _emit(out)
        return 0 if out["ok"] else 2

    if args.action == "inbox":
        rows = cycle.collect_inbox(DATA, lookback_hours=getattr(args, "lookback_hours", 0) or None,
                                   session=getattr(args, "session", "") or "", log=_log)
        got = cycle.merge_inbox(DATA, rows, queue_path=_queue_path())
        _emit(got)
        return 0 if got.get("ok") else 2

    if args.action == "enqueue":
        got = cycle.enqueue(DATA, _load_structured(args.file), queue_path=_queue_path(),
                            now=_when())
        _emit(got)
        return 0 if got.get("ok") else 2

    if args.action == "plan":
        from core.ledger import Ledger
        from core.throttle import Throttle
        policy = cycle.load_policy(DATA)
        path = _queue_path()
        got = cycle.plan(cycle.load_queue(path), policy,
                         ledger=Ledger(DATA / "ledger.jsonl"), now=_when(),
                         budget_sec=int(getattr(args, "budget_sec", 3000) or 3000),
                         max_actions=int(getattr(args, "max_actions", 0) or 0),
                         campaign=str(getattr(args, "campaign", "") or ""),
                         throttle=Throttle(DATA / "state"), data_dir=DATA)
        got["queue_path"] = str(path)
        _emit(got)
        return 0

    if args.action == "run":
        got = cycle.run(DATA, go=bool(args.go), queue_path=_queue_path(),
                        max_actions=int(getattr(args, "max_actions", 0) or 0),
                        wave_id=str(getattr(args, "wave", "") or ""),
                        budget_sec=int(getattr(args, "budget_sec", 3000) or 3000),
                        session=getattr(args, "session", "") or "",
                        prefetch=getattr(args, "prefetch", None),
                        auto_inbox=getattr(args, "auto_inbox", None), log=_log)
        _emit(got)
        return 0 if got.get("ok") else 2

    raise ValueError("未知 cycle 动作：%s" % args.action)


@_business_command
def cmd_supply(args):
    """候选储备：挖掘候选目标与写作角度、看储备深度、挑候选、标状态、登记 KOC。

    只读写本地储备文件，不写队列、不发送。正文仍由每波前的准备任务写；
    储备里只放目标、证据和分数，避免 12 小时过期把成稿浪费掉。
    """
    from core import supply_mine, supply_pool
    from core.ledger import Ledger

    def _profile():
        from core import grow_fit
        path = DATA / "family-facts.json"
        if not path.is_file():
            return None
        return grow_fit.product_profile(json.loads(path.read_text(encoding="utf-8")))

    def _policy():
        path = DATA / "policies" / "engagement-cycle.json"
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def _ledger():
        path = Path(str(getattr(args, "ledger", "") or "") or (DATA / "ledger.jsonl"))
        return Ledger(path)

    def _session(policy):
        from core import engagement_cycle as cycle
        return (str(getattr(args, "session", "") or "")
                or cycle.bridge_session(policy) or "")

    def _queries(defaults):
        got = [q for q in (getattr(args, "query", None) or []) if str(q).strip()]
        return got or None

    if args.action == "stats":
        from core import engagement_cycle as cycle
        policy = _policy()
        caps = {key: row["effective_per_night"]
                for key, row in cycle.budget_rows(policy, used={"night": {}}).items()
                if row.get("effective_per_night") is not None}
        got = supply_pool.stats(DATA, policy, nights=int(args.nights or 3), per_night=caps)
        _emit(got)
        return 0

    if args.action == "pick":
        rows = supply_pool.pick(
            DATA, args.channel, args.action_name, int(args.n or 5),
            min_score=int(args.min_score or 0),
            exclude=[x for x in str(args.exclude or "").split(",") if x.strip()])
        _emit({"ok": True, "count": len(rows), "items": rows})
        return 0

    if args.action == "mark":
        _emit(supply_pool.mark(DATA, args.key, args.status, why=str(args.why or "")))
        return 0

    if args.action == "wave":
        from core.ledger import Ledger
        got = supply_pool.wave_plan(
            DATA, _policy(), wave=int(getattr(args, "wave_no", 1) or 1),
            out_path=Path(args.out) if getattr(args, "out", "") else None,
            ledger=Ledger(DATA / "ledger.jsonl"))
        _emit(got)
        return 0

    if args.action == "koc":
        registry = supply_pool.koc_load(DATA)
        accounts = registry.get("accounts") or {}
        rows = sorted(accounts.values(),
                      key=lambda row: (-int(row.get("score") or 0),
                                       -int(row.get("hits") or 0)))
        limit = int(getattr(args, "limit", 0) or 0)
        _emit({"ok": True, "accounts": len(accounts),
               "koc": supply_pool.koc_stats(DATA),
               "top": rows[:limit] if limit else rows})
        return 0

    if args.action == "import-watchlist":
        got = supply_pool.import_watchlist(DATA, args.path)
        _emit(got)
        return 0 if got.get("ok") else 2

    if args.action == "mine":
        profile, ledger = _profile(), _ledger()
        if args.channel == "content":
            got = supply_mine.mine_content(DATA, policy=_policy(),
                                           profile=profile, ledger=ledger)
        elif args.channel == "github":
            got = supply_mine.mine_github(
                DATA, queries=_queries(supply_mine.gh_queries(int(args.days or 14))),
                per_query=int(args.per_query or 20), gap=float(args.gap or 3.0),
                days=int(args.days or 14), profile=profile, ledger=ledger)
        elif args.channel == "x":
            policy = _policy()
            got = supply_mine.mine_x(
                DATA, queries=_queries(supply_mine.X_QUERIES),
                limit=int(args.limit or 12), session=_session(policy),
                gap=float(args.gap or 2.0), profile=profile, ledger=ledger)
        else:
            raise ValueError("未知挖掘渠道：%s（可选 github/x/content）" % args.channel)
        _emit(got)
        return 0 if got.get("ok") else 2

    raise ValueError("未知 supply 动作：%s" % args.action)


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
        from core import sop
        coverage = sop.SOPRegistry(DATA).coverage()
        _emit({"ok": not errors, "errors": errors, "coverage": coverage,
               "coverage_summary": _coverage_summary(coverage)})
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
    from core.sop import SOPRegistry
    store = work.WorkStore(DATA)
    if args.action == "create":
        order = store.create(_load_structured(args.file), by=args.by)
        return _emit({"work_order": order,
                      "lint_errors": work.lint_work_order(order, sop_registry=SOPRegistry(DATA)),
                      "next": f"mkt work ready --id {order['work_order_id']} --by <human>"})
    if args.action == "show":
        return _emit(store.get(args.id))
    if args.action == "lint":
        errors = (work.lint_work_order(_load_structured(args.file), sop_registry=SOPRegistry(DATA))
                  if args.file else store.lint(args.id))
        _emit({"ok": not errors, "errors": errors})
        return 0 if not errors else 2
    if args.action == "ready":
        return _emit(store.ready(args.id, by=args.by))
    if args.action == "transition":
        return _emit(store.transition(args.id, args.to, by=args.by, reason=args.reason))
    if args.action == "update":
        order = store.update(args.id, _load_structured(args.file), by=args.by)
        return _emit({"work_order": order,
                      "lint_errors": work.lint_work_order(order, sop_registry=SOPRegistry(DATA))})
    if args.action == "replace":
        order = store.replace(args.id, _load_structured(args.file), by=args.by)
        return _emit({"work_order": order,
                      "lint_errors": work.lint_work_order(order, sop_registry=SOPRegistry(DATA))})
    if args.action == "next":
        return _emit({"next": store.next_actions()})
    return _emit({"work_orders": store.list()})


def _coverage_summary(coverage):
    """SOP 覆盖率的人读一行：避免把「已运行」误读成「18 域全覆盖」。"""
    total = int(coverage.get("total_capabilities") or 0)
    covered = int(coverage.get("covered") or 0)
    uncovered = coverage.get("uncovered") or []
    line = f"SOP 覆盖：{covered}/{total} 个能力域有可执行 SOP"
    if uncovered:
        line += f"（未覆盖 {len(uncovered)} 个：{('、'.join(uncovered))}）"
    return line


@_business_command
def cmd_sop(args):
    from core import sop
    registry = sop.SOPRegistry(DATA)
    runs = sop.SOPRunStore(DATA)
    if args.action == "list":
        coverage = registry.coverage()
        return _emit({"sops": registry.list(), "coverage": coverage,
                      "coverage_summary": _coverage_summary(coverage)})
    if args.action == "coverage":
        coverage = registry.coverage()
        print(_coverage_summary(coverage))
        print("说明：SOP 覆盖只表示该能力域有可执行 Step Contract，"
              "不表示已在真实业务中运行过。")
        for row in coverage.get("capabilities") or []:
            mark = "有" if row.get("executable") else "无"
            sops = "、".join(row.get("sops") or []) or "-"
            print(f"  {row.get('capability_id')} {row.get('name')}：{mark}（{sops}）")
        return 0
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
        if not str(args.dod or "").strip():
            raise sop.SOPError("完成步骤必须提供 DoD 判定文件 --dod；"
                               "缺少时无法判定该步是否达标")
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
        metric = _load_structured(args.file)
        if getattr(args, "count", False):
            if metric.get("denominator") is not None:
                raise metrics.MetricError(
                    "计数型指标不能声明 denominator：删除分母，或去掉 --count")
            metric["formula"] = "numerator_count"
            metric.pop("denominator", None)
        elif metric.get("denominator") is None:
            raise metrics.MetricError(
                "缺少 denominator：计数型指标需要显式加 --count")
        return _emit(registry.register(metric, by=args.by))
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
        account=args.account, workspace=args.execution_workspace, by=args.by,
        allow_unbound=args.allow_unbound, binding_note=args.binding_note))


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
    try:
        strat = json.loads(Path(args.strategy_file).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"不能生成：找不到策略文件 {args.strategy_file}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"不能生成：策略文件不是有效 JSON（{args.strategy_file}，第 {e.lineno} 行: {e.msg}）。\n"
              f"生成方式：python3 mkt.py strategy build --facts <facts.json> [--selection <selection.json>] --json --out {args.strategy_file}",
              file=sys.stderr)
        return 2
    if str(args.targets).endswith(".json"):
        print(f"不能生成：--targets 是发送对象 CSV（列：target,body[,subject]），不是产品事实包 JSON：{args.targets}", file=sys.stderr)
        return 2
    try:
        rows = list(_csv.DictReader(Path(args.targets).read_text(encoding="utf-8").splitlines()))
    except FileNotFoundError:
        print(f"不能生成：找不到目标清单 {args.targets}", file=sys.stderr)
        return 2
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

    if act in ("card", "cards", "validate"):
        from core import platform_strategy
        if act == "card":
            payload = platform_strategy.card(args.platform)
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=1))
            else:
                print(platform_strategy.render_card(args.platform))
            return 0 if not payload["validator"] else 1
        if act == "cards":
            print(platform_strategy.render_summary())
            return 0
        problems = platform_strategy.validate()
        if getattr(args, "json", False):
            print(json.dumps(problems, ensure_ascii=False, indent=1))
        elif problems:
            for pid, issues in problems.items():
                print(f"{pid}：")
                for issue in issues:
                    print(f"  - {issue}")
        else:
            print(f"策略层校验通过：{len(platform_strategy.STRATEGY)} 个平台")
        return 0 if not problems else 1

    if act == "build":
        facts = _load_facts(args.facts)
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
            payload = json.dumps(s, ensure_ascii=False, indent=1)
            if args.out:
                out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(payload + "\n", encoding="utf-8")
                print(f"策略 JSON 已生成：{out}（供 mkt execute --strategy-file 用）")
            else:
                print(payload)
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
        facts = _load_facts(args.facts) if args.facts else None
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
    if act == "observe":
        from core import account_identity
        channel = str(args.channel or "x").strip().lower()
        proof = account_identity.observe(
            channel, data_dir=DATA,
            env={**os.environ, "MKT_CDP_IDENTITY": "1"})
        if proof is None:
            print(json.dumps({"status": "unavailable",
                              "channel": channel,
                              "probe": account_identity.last_probe()},
                             ensure_ascii=False, indent=1))
            return 2
        payload = {"status": "observed", "channel": proof.channel,
                   "handle": proof.handle, "user_id": proof.user_id,
                   "observed_at": proof.observed_at,
                   "proof_ref": proof.source_ref}
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0
    if act == "log":
        from core.ledger import Ledger
        from core.operations_log import OPERATIONS_LOG_NAME, OperationsLog
        # 新链路读完整操作日志；旧工作区没有 operations.jsonl 时保留账本回退。
        if (DATA / OPERATIONS_LOG_NAME).is_file():
            rows = OperationsLog(DATA).all()
            source = "operations"
        else:
            rows = Ledger(DATA / "ledger.jsonl").all()
            source = "ledger"
        rows = rows[-max(1, int(args.limit)):]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
            return 0
        if not rows:
            print("（无操作记录）")
            return 0
        for rec in rows:
            if source == "operations":
                proof = rec.get("account") or {}
                account = (proof.get("handle") or proof.get("platform_user_id")
                           or proof.get("authorized_account") or "—")
                receipt = rec.get("receipt") or {}
                receipt_ref = receipt.get("id") or receipt.get("url") or "—"
                print(f"{rec.get('finished_at') or rec.get('started_at') or ''}  "
                      f"{rec.get('channel','')}/{rec.get('action','')}  "
                      f"{rec.get('result','')}  account={account}  "
                      f"operation={rec.get('operation_id') or '—'}  "
                      f"campaign={rec.get('campaign') or '—'}  "
                      f"queue_item={rec.get('queue_item_id') or '—'}  "
                      f"receipt={receipt_ref}  target={rec.get('target') or '—'}")
                continue
            ident = rec.get("identity") or {}
            account = (ident.get("handle") or ident.get("observed_platform_identity")
                       or ident.get("authorized_account") or "—")
            print(f"{rec.get('ts','')}  {rec.get('channel','')}/{rec.get('action','')}"
                  f"  {rec.get('result','')}  account={account}"
                  f"  target={rec.get('target','')}")
        return 0
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
        if not DATA.is_dir():
            print(json.dumps({"status": "error", "reason_code": "WORKSPACE_NOT_INITIALIZED",
                              "message": f"工作台未初始化：{DATA} 不存在",
                              "hint": f"先运行 `mkt init`（即 {Path(sys.argv[0]).name} "
                                      f"--workspace {DATA} init）初始化工作台，再建计划"},
                             ensure_ascii=False, indent=1))
            return 2
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
        print("下一步：先生成队列，再审批（审批会绑定队列内容，队列不存在会拒绝）：")
        print(f"  mkt queue build --campaign {Path(queue).stem} --channel {channel} "
              f"--wave-action {wave_action} --template <模板文件> --targets <目标CSV>")
        print(f"  mkt plan approve --id {pid} --by <你的名字>")
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
            try:
                plan, path, event = approval.approve_plan(
                    DATA, args.id, by=args.by, note=args.note or "", days=args.days,
                    account=getattr(args, "account", None), workspace=getattr(args, "workspace", None))
            except approval.ApprovalError as exc:
                print(f"不能批准：{exc}")
                try:
                    pending, _ = _load_plan_arg(args)
                    wave = (pending.get("waves") or [{}])[0]
                    queue_ref = (wave.get("queue") or f"data/queues/{args.id}-w1.json")
                    print("先构建队列，再审批：")
                    print(f"  mkt queue build --campaign {Path(queue_ref).stem} "
                          f"--channel {wave.get('channel') or 'email'} "
                          f"--wave-action {wave.get('action') or 'message'} "
                          f"--template <模板文件> --targets <目标CSV>")
                except Exception:
                    print(f"先构建计划声明的队列，再：mkt plan approve --id {args.id} --by {args.by}")
                return 2
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
    from core import error_semantics, queue as queue_mod
    act = args.action
    if act == "lint":
        if not args.path:
            raise error_semantics.SemanticError(
                "NEEDS_USER_INPUT", "queue lint 需要队列文件路径。",
                missing=["队列文件路径（位置参数 path）"],
                impact="没有读取或校验任何队列，未产生外部动作。",
                next_action="传一个队列 JSON 路径后重试；路径不存在时先运行 mkt queue build。",
                example="mkt queue lint data/queues/demo-w1.json")
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
            raise error_semantics.SemanticError(
                "NEEDS_USER_INPUT", "queue run 需要队列文件路径。",
                missing=["队列文件路径（位置参数 path）"],
                impact="没有读取或执行任何队列，未发送任何消息。",
                next_action="传一个已通过 lint 的队列路径；先加 --go 才是实跑，默认 dry-run。",
                example="mkt queue run data/queues/demo-w1.json --go")
        from core.queue_runner import QueueRunError, run_queue
        try:
            results = run_queue(args.path, data_dir=DATA, go=args.go,
                                allow_unverified=args.allow_unverified,
                                only=args.only, routine=args.routine,
                                resume_batch=getattr(args, "resume_batch", False),
                                transport=getattr(args, "transport", None))
        except QueueRunError as exc:
            raise error_semantics.SemanticError(
                "SAFETY_BLOCKED", f"队列拒跑：{exc}",
                missing=[],
                impact="本批没有继续执行；已发送的记录保持不变，未执行项不会补发。",
                next_action="先按拒绝原因人工核实（未知回执/审计缺口/批次阻塞），"
                            "确认后再用 --resume-batch 重跑同一队列。",
                example=f"mkt queue run {args.path} --go --resume-batch")
        tally = {}
        for r in results:
            tally[r.get("result", "?")] = tally.get(r.get("result", "?"), 0) + 1
        audit_gaps = [r for r in results if r.get("audit_gap")]
        print(json.dumps({"queue": str(Path(args.path).resolve()), "go": args.go,
                          "results": tally,
                          "audit_gaps": len(audit_gaps)}, ensure_ascii=False, indent=1))
        if tally.get("indeterminate") or audit_gaps:
            # 未知回执/审计缺口都不是成功：给脚本一个可判别的非零状态。
            print("本批存在未知回执或审计缺口：先人工核实，再决定是否 --resume-batch。",
                  file=sys.stderr)
            return 3
        if tally.get("rejected") and not (tally.get("committed") or tally.get("dry-run")):
            # 整队都被闸门拒绝（全局暂停/抑制/配额）：没有发送任何消息，
            # 不能按"跑完了"退出 0。
            print(f"全部 {tally['rejected']} 条被闸门拒绝（全局暂停/抑制/配额），"
                  f"未发送任何消息；检查拒绝原因后再重跑。", file=sys.stderr)
            return 3
        return 0 if not tally.get("failed") else 1

    if act == "build":
        missing = [k for k in ("campaign", "template", "targets") if not getattr(args, k)]
        if missing:
            raise error_semantics.SemanticError(
                "NEEDS_USER_INPUT",
                f"queue build 需要 --campaign --template --targets（缺 {', '.join('--' + m for m in missing)}）。",
                missing=["--" + name for name in missing],
                impact="没有生成任何队列文件，也没有写入 data/queues。",
                next_action="补齐缺失参数后重试；模板与目标 CSV 都要存在。",
                example="mkt queue build --campaign demo-w1 --template templates/outreach.md "
                        "--targets targets.csv")
        out = args.out or str(DATA / "queues" / f"{args.campaign}.json")
        if args.out and not Path(args.out).is_absolute():
            # 相对 --out 按工作台根解析：review/campaign 只从 data/queues 读队列，
            # 写到 cwd 会让"队列已生成"和"review 找不到"同时成立。
            out = str(DATA / args.out)
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
        resolved = Path(out).resolve()
        print(f"已生成 {resolved}（{len(q['items'])} 条，已过门禁）")
        print(f"路径规则：--out 的相对路径按工作台 data/ 解析，绝对路径原样使用。")
        print(f"下一步：mkt queue lint {resolved}")
        return 0

    return 1


def cmd_campaign(args):
    from core import campaign as camp, error_semantics
    cid = args.campaign
    if not cid:
        raise error_semantics.SemanticError(
            "NEEDS_USER_INPUT", "campaign 需要 campaign id。",
            missing=["campaign id"],
            impact="没有读取或写入任何 campaign 数据。",
            next_action="传一个已存在的 campaign id 后重试；不确定时先运行 mkt campaigns。",
            example="mkt campaign show askovo-p0-w1")
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
        out = Path(args.out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md + "\n", encoding="utf-8")
        print(f"已写入 {out}")
        print("路径规则：campaign --out 的相对路径按当前目录解析。")
    else:
        print(md)
    return 0


def cmd_review(args):
    from core import campaign as camp, error_semantics
    if not args.campaign:
        raise error_semantics.SemanticError(
            "NEEDS_USER_INPUT", "review 需要 campaign id。",
            missing=["campaign id"],
            impact="没有生成任何审核预览，也没有写文件。",
            next_action="传一个已存在的 campaign id 后重试；先运行 mkt campaigns 查看可用 id。",
            example="mkt review askovo-p0-w1 --out reports/review.md")
    md = camp.review_markdown(DATA, _ledger(), args.campaign)
    if args.out:
        out = Path(args.out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md + "\n", encoding="utf-8")
        print(f"已写入 {out}")
        print("路径规则：review --out 的相对路径按当前目录解析。")
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
    from core import experiment_hypothesis as hyp
    from core import experiments
    if args.action in ("start", "obs", "conclude", "stop", "card-check", "card-show") \
            and not args.id:
        raise SystemExit(f"experiment {args.action} 需要 --id")
    if args.action == "card-new":
        if not args.card:
            raise SystemExit("experiment card-new 需要 --card 假设卡 JSON 文件")
        card = json.loads(Path(args.card).read_text(encoding="utf-8"))
        exp = hyp.create_from_card(DATA, card)
        print(json.dumps({"exp_id": exp["exp_id"], "status": exp["status"],
                          "hypothesis_card": exp["hypothesis_card"],
                          "hypothesis_card_sha256": exp["hypothesis_card_sha256"]},
                         ensure_ascii=False, indent=1))
        return 0
    if args.action == "card-check":
        out = hyp.check_card(DATA, args.id)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if out["ok"] else 2
    if args.action == "card-show":
        card = hyp.load_card(DATA, args.id)
        if card is None:
            raise SystemExit(f"experiment {args.id} 没有假设卡")
        doc = hyp.next_change_hint(card, args.result) if args.result else card
        print(json.dumps(doc, ensure_ascii=False, indent=1))
        return 0
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
    if args.action == "stats":
        from channels.x import read_stats
        stats = read_stats.read(args.handle or "", DATA / "shots" / "x-stats", log=print)
        print(f"读数（UNVERIFIED，首周请人工对数）：@{stats['handle']}")
        print(f"  粉丝：{stats['followers'] if stats['followers'] is not None else '取不到'}"
              f"｜昨日曝光：{stats['impressions_yesterday'] if stats['impressions_yesterday'] is not None else '取不到'}")
        print(f"  证据：{stats['saved']}"
              + (f"｜截图 {len(stats['screenshots'])} 张" if stats["screenshots"] else "（无截图）"))
        if args.record:
            written = read_stats.record(DATA, stats)
            print(f"已落账：{written or '（没有可落的数字）'}（approximated 口径，来源指向快照）")
        else:
            print("默认只读不落账；人工核对数字后加 --record 才写入 outcome 计数")
        return 0
    from channels.x.adapter import run as x_run
    identity_evidence = None
    if args.go:
        # G-01：CLI adhoc 直通路径同样过账号身份门禁——绑定身份的授权下，
        # 浏览器实际账号必须与授权一致；未绑定授权不介入（legacy 语义不变）。
        # 门禁返回的观测证据必须随回执落账，否则三条入口的回执字段不一致。
        from core.action_runner import ActionError, gate_account_identity
        try:
            identity_evidence = gate_account_identity(
                "x", DATA, getattr(args, "account", "") or "")
        except ActionError as exc:
            print(f"拒绝执行：{exc}")
            return 1
    extra = list(args.extra or [])
    if getattr(args, "file", None):
        extra += ["--file", args.file]
    if getattr(args, "thread", False):
        extra += ["--thread"]
    # reply 的 vendor 参数原先只能靠 "--" 透传，argparse 会拒绝；显式暴露两个参数。
    if getattr(args, "tweet_url", None):
        extra += ["--tweet-url", args.tweet_url]
    if getattr(args, "text", None):
        extra += ["--text", args.text]
    # CLI 直通入口也要落统一操作日志（§4.2）：捕获本次动作新增的账本行，
    # 结束后按动作现场扩写成完整记录（不依赖事后扫账本的时间启发式）。
    from core import operations_log
    recorder = operations_log.LedgerRecorder(_ledger())
    rc = x_run(args.action, extra, go=args.go, adhoc=args.adhoc,
               campaign=args.campaign, ledger=recorder, data_dir=DATA,
               identity=identity_evidence, execution_mode="user_visible_cdp")
    try:
        recorder.flush_operations(
            DATA, channel="x", action=args.action,
            target=getattr(args, "tweet_url", None) or "",
            text=getattr(args, "text", None),
            campaign=args.campaign or "", go=bool(args.go),
            operator="cli:mkt-x")
    except Exception as exc:  # noqa: BLE001 日志失败不掩盖动作结果
        print(f"[opslog] 操作日志写入失败：{exc}")
    return rc


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
    from channels.email_smtp import BatchBlocked, send_queue
    try:
        res = send_queue(args.queue, go=args.go, adhoc=args.adhoc, only=args.only,
                         campaign=args.campaign, data_dir=DATA,
                         ledger=_ledger(), throttle=_throttle(),
                         operator="mkt:email",
                         resume_batch=bool(getattr(args, "resume_batch", False)),
                         run_id=str(getattr(args, "run_id", "") or ""))
    except BatchBlocked as exc:
        print(str(exc), file=sys.stderr)
        return 3
    indeterminate = [r for r in res if r.get("result") == "indeterminate"]
    failed = [r for r in res if r.get("result") in ("failed", "rejected")]
    gaps = [r for r in res if r.get("audit_gap")]
    if indeterminate:
        print("发送结果未知（INDETERMINATE）：服务端可能已收下该邮件。本批已中止，"
              "未发送的条目没有动作；先人工核实，再 mkt ledger resolve。",
              file=sys.stderr)
        return 3
    if gaps:
        print("发送已发生但操作日志写入失败（审计缺口）：%s。"
              "不得视为完整核验通过；缺口见 %s。"
              % (gaps[0].get("audit_gap_reason", ""),
                 gaps[0].get("audit_gap", "")), file=sys.stderr)
        return 3
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
                           campaign=args.campaign, ledger=_ledger(), data_dir=DATA)
        return 0 if rec["result"] in ("committed", "dry-run") else 1
    if args.action == "publish":
        body = Path(args.body_file).read_text(encoding="utf-8").strip() \
            if args.body_file else (args.body or "")
        rec = reddit.publish(args.sub, args.title, body, go=args.go, adhoc=args.adhoc,
                             campaign=args.campaign, ledger=_ledger(), data_dir=DATA)
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
                               campaign=args.campaign, ledger=_ledger(), data_dir=DATA)
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
                           campaign=args.campaign, ledger=_ledger(), data_dir=DATA)
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
                         ledger=_ledger(), data_dir=DATA)
    else:  # reply
        rec = hn.reply(args.item, text, go=args.go, adhoc=args.adhoc,
                       campaign=args.campaign, ledger=_ledger(), data_dir=DATA)
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
        facts = _load_facts(args.facts)
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
        facts = _load_facts(args.facts)
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
            facts = _load_facts(args.facts)
            goal_plan = _goal_plan_from_selection(args.selection, facts)
        else:
            facts = _load_facts(args.facts)
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

    facts = _load_facts(args.facts)
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
        print(f"下一步：mkt queue lint {out}；mkt plan approve --id <plan_id> --by <人>；"
              f"mkt queue run {out}（先 dry-run，再 --go）")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(playbooks.render_markdown(plan))
        print(f"计划：{plan_file}")
    return 0


def cmd_action(args):
    from core.action_runner import ActionError, run_action
    from core.browser_attach import BridgeError
    from core.browser_write import ActionBlocked, StopChannel
    transport = getattr(args, "transport", "") or ""
    if transport == "browser-attach":
        from core import engagement_cycle as cycle
        cycle._use_session(getattr(args, "session", "") or cycle.bridge_session(
            cycle.load_policy(DATA)))
    text = Path(args.text_file).read_text(encoding="utf-8").strip() \
        if args.text_file else args.text
    try:
        rec = run_action(
            channel=args.channel, action=args.action, data_dir=DATA,
            campaign=args.campaign or "", go=args.go,
            allow_unverified=args.allow_unverified, target=args.target,
            text=text, title=args.title, url=args.url,
            subreddit=args.subreddit, number=args.number,
            extra=args.extra, routine=args.routine, account=args.account,
            platform_user_id=args.platform_user_id or None,
            transport=transport, actor=getattr(args, "actor", "") or "")
    except (ActionError, ActionBlocked, StopChannel, BridgeError) as exc:
        raise SystemExit(f"动作拒跑：{exc}")
    print(json.dumps(rec, ensure_ascii=False, indent=1))
    if rec.get("result") in ("committed", "dry-run", "skipped"):
        return 0
    return 3 if rec.get("result") == "indeterminate" else 1


def cmd_proof(args):
    from core import proof_export
    try:
        result = proof_export.export_proof(
            DATA, args.run_id, args.out, by=args.by or "",
            classification=args.classification or "private")
    except (proof_export.ProofExportError, proof_export.verovo_handoff.HandoffError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


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
    sp.add_argument("--on", nargs="?", const=True, default=False,
                    help="暂停；可直接跟原因 `--on '原因'`，或 `--on --reason '原因'`")
    sp.add_argument("--off", action="store_true", help="恢复")
    sp.add_argument("--reason", default="")
    sp.set_defaults(fn=cmd_pause)
    sp = sub.add_parser("report", help="一页纸运营报告（近 24h 账本/异常/暂停态）")
    sp.add_argument("--hours", type=int, default=24)
    sp.add_argument("--format", choices=["json", "md"], default="json",
                    help="md = 给人看的报告")
    sp.set_defaults(fn=cmd_report)
    sp = sub.add_parser("demo", help="零网络零账号演示：计划校验→人审闸门→成功投递→三道拦截→账本与日结果卡")
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
    sp.add_argument("--out", help="build：输出路径（相对路径按工作台 data/ 解析；"
                                  "默认 data/queues/<campaign>.json）")
    sp.add_argument("--id-prefix", dest="id_prefix")
    sp.add_argument("--max-len", dest="max_len", type=int)
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--go", action="store_true", help="run：实跑（须已人审 plan 或有效 routine policy）")
    sp.add_argument("--routine", action="store_true", help="run：使用工作区日常营销常驻授权；其他安全门不变")
    sp.add_argument("--only", help="run：只跑指定 id/target")
    sp.add_argument("--allow-unverified", dest="allow_unverified", action="store_true",
                    help="run：UNVERIFIED 渠道实跑（首跑人工在场）")
    sp.add_argument("--resume-batch", dest="resume_batch", action="store_true",
                    help="run：人工核实上一批的未知回执/审计缺口后，清除批次阻塞标记继续")
    sp.add_argument("--transport", choices=["browser-attach"],
                    help="run：显式使用当前浏览器现有标签页；授权走 owner-attested")
    sp.set_defaults(fn=cmd_queue)

    sp = sub.add_parser("action", help="单条写动作（与队列同一套门禁/审批/账本）")
    sp.add_argument("channel", choices=["x", "reddit", "linkedin", "github", "hn", "discord"])
    sp.add_argument("action", choices=["publish", "reply", "message", "engage"])
    sp.add_argument("--target", help="reply/message 的目标（URL / issue:o/r#N）")
    sp.add_argument("--text"); sp.add_argument("--text-file")
    sp.add_argument("--title"); sp.add_argument("--url")
    sp.add_argument("--subreddit"); sp.add_argument("--number", type=int)
    sp.add_argument("--campaign")
    sp.add_argument("--account", default="", help="执行账号；routine policy 按此精确匹配")
    sp.add_argument("--go", action="store_true")
    sp.add_argument("--routine", action="store_true", help="使用日常营销常驻授权；付款/合同等不适用")
    sp.add_argument("--platform-user-id", default="",
                    help="平台用户 ID；策略声明 identity 时用于核对账号身份")
    sp.add_argument("--allow-unverified", dest="allow_unverified", action="store_true")
    sp.add_argument("--extra", nargs="*", help="透传 vendor（x message/engage）")
    sp.add_argument("--transport", choices=["browser-attach"],
                    help="显式复用当前浏览器现有标签页；授权走 owner-attested")
    sp.add_argument("--session", default="", help="browser-attach：WebBridge 会话名；默认读 cycle 策略")
    sp.add_argument("--actor", default="", help="browser-attach：外部对象标识，用于每对象额度")
    sp.set_defaults(fn=cmd_action)

    sp = sub.add_parser("proof", help="Prove Anything：只读导出可复核增长证明包")
    proof_sub = sp.add_subparsers(dest="proof_action", required=True)
    a = proof_sub.add_parser("export", help="导出一个 run/campaign；不执行外部动作")
    a.add_argument("run_id")
    a.add_argument("--out", required=True, help="新目录；拒绝覆盖")
    a.add_argument("--by", required=True, help="具名证明复核人")
    a.add_argument("--classification", choices=["private", "synthetic", "public-redacted"], default="private")
    a.set_defaults(fn=cmd_proof)

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
    sp.add_argument("--out", help="report：写入文件（相对路径按当前目录解析，"
                                  "如 reports/<campaign>.md）")
    sp.add_argument("--format", choices=["json", "md"], default=None)
    sp.add_argument("--json", action="store_true", help="next：输出 JSON")
    sp.set_defaults(fn=cmd_campaign)

    sp = sub.add_parser("review", help="人审预览：队列逐条过门禁 + 已有留证")
    sp.add_argument("campaign")
    sp.add_argument("--out", help="写入文件（相对路径按当前目录解析；缺省打印到 stdout）")
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
    a = ssub.add_parser("card", help="平台策略卡：节奏、上限、门禁、变现、证据与复核状态")
    a.add_argument("platform", choices=["xiaohongshu", "wechat", "zhihu", "douyin",
                                        "x", "reddit", "hackernews", "linkedin"])
    a.add_argument("--json", action="store_true")
    a = ssub.add_parser("cards", help="平台策略层覆盖表")
    a = ssub.add_parser("validate", help="校验策略层：claim 追溯、参数自洽、检测器实现")
    a.add_argument("--json", action="store_true")
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
    a.add_argument("--purpose", choices=["strategy", "craft", "experiment", "generalization"],
                   default="strategy")
    a.add_argument("--on", default="", help="资格评估日期 YYYY-MM-DD")
    a = esub.add_parser("adopt", help="给缺 knowledge_type 的旧记录补分类：多源实践/单源线索（默认 dry-run）")
    a.add_argument("--min-support", type=int, default=2)
    a.add_argument("--on", default="", help="采集核验日期 YYYY-MM-DD；默认今天")
    a.add_argument("--apply", action="store_true", help="写回 experience.jsonl")
    a.add_argument("--revisit-leads", action="store_true",
                   help="重新评估已标为单源线索的记录（补到第二个来源后可升级）")
    a.add_argument("--date-from-git", action="store_true",
                   help="缺失日期时用 git 首次记录日补 observed_at（date_basis 会写明）")
    a.add_argument("--json", action="store_true")
    a = esub.add_parser("usable", help="按内容工艺口径列出可用经验（多源实践，附复核等级）")
    a.add_argument("--platform", required=True)
    a.add_argument("--limit", type=int, default=10)
    a.add_argument("--json", action="store_true")
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
    a = vsub.add_parser("plain", help="检查有没有生造词：普通人第一次看到能不能看懂")
    a.add_argument("--text", default=""); a.add_argument("--file", default="")
    a.add_argument("--json", action="store_true"); a.add_argument("--fix", action="store_true")
    sp.set_defaults(fn=cmd_voice)

    sp = sub.add_parser("content", help="内容运营：渠道规则、brief、审稿与多渠道路排期")
    csub = sp.add_subparsers(dest="action", required=True)
    a = csub.add_parser("rules", help="看某个渠道的内容规则")
    a.add_argument("channel", choices=["xiaohongshu", "wechat", "zhihu", "douyin", "blog"])
    a = csub.add_parser("brief", help="生成渠道原生内容 brief")
    a.add_argument("channel", choices=["xiaohongshu", "wechat", "zhihu", "douyin", "blog"])
    a.add_argument("--topic", required=True)
    a.add_argument("--audience", default="")
    a.add_argument("--product", default="")
    a.add_argument("--evidence", default="")
    a.add_argument("--cta", default="B", choices=["A", "B", "C"])
    a.add_argument("--title", default="")
    a.add_argument("--facts", default="",
                   help="产品事实 JSON（product/icp/value_events），决定读者与选题")
    a.add_argument("--json", action="store_true")
    a = csub.add_parser("review", help="整篇审稿：生成版本绑定清单，或校验填写后的记录")
    a.add_argument("channel", choices=["xiaohongshu", "wechat", "zhihu", "douyin", "blog"])
    a.add_argument("--file", default="")
    a.add_argument("--text", default="")
    a.add_argument("--title", default="")
    a.add_argument("--record", default="", help="填写后的审稿 JSON；省略时生成待填写清单")
    a = csub.add_parser("audit", help="审稿：元叙述、产品出场、标题、CTA、证据与联系方式")
    a.add_argument("channel", choices=["xiaohongshu", "wechat", "zhihu", "douyin", "blog"])
    a.add_argument("--file", default="")
    a.add_argument("--text", default="")
    a.add_argument("--title", default="")
    a.add_argument("--titles", default="", help="可选 JSON：{D2-01: 发布短标题}；用于多篇稿件标题检查")
    a.add_argument("--strict", action="store_true", help="把提醒也提升为阻断项")
    a.add_argument("--json", action="store_true")
    a = csub.add_parser("plan", help="生成多渠道路排期骨架")
    a.add_argument("--start", default="",
                   help="起始日期；省略=本地今天。指定过去日期即历史回放（会标注）")
    a.add_argument("--facts", default="",
                   help="产品事实 JSON（product/icp/value_events），决定读者与选题")
    a.add_argument("--days", type=int, default=14)
    a.add_argument("--channels", default="xiaohongshu,wechat,blog")
    a.add_argument("--cadence", default="evidence", choices=["evidence", "daily-two"],
                   help="evidence=平台策略层的证据档周节奏；daily-two=用户指定的小红书每天两篇战术")
    a.add_argument("--weekdays", default="",
                   help="按真实星期排班，如 mon,wed,sun（0=周一）；省略=按起始日第几天排")
    a.add_argument("--out", default="")
    a.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_content)

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
    a.add_argument("--actions", default="",
                   help="channel:action 逗号列表；不填只授权 email:message，其他动作必须显式列出")
    a.add_argument("--accounts", default="", help="可选：授权账号逗号列表")
    a.add_argument("--days", type=int, default=30,
                   help="授权有效天数（默认 30；0 表示不过期，需显式声明）")
    a.add_argument("--identity", action="append", default=[],
                   help="账号身份 account=platform:user_id，可重复")
    a.add_argument("--require-identity", action="store_true",
                   help="执行时必须提供匹配的平台用户 ID；未提供则拒绝")
    a.add_argument("--observe-account", action="store_true",
                   help="CDP 观测模式：不预绑定账号，执行时记录当前浏览器实际登录账号（仍需可读平台身份）")
    a = psub.add_parser("migrate-identity",
                        help="旧授权迁移：列出缺身份绑定的常驻授权，--apply 绑定后解阻真实发送")
    a.add_argument("--by", default="", help="执行迁移的人；--apply 时必填")
    a.add_argument("--identity", action="append", default=[],
                   help="account=platform:user_id，可重复；--apply 时必填")
    a.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run 只报告）")
    a = psub.add_parser("identity", help="当前身份读取状态与最近一次探测结果（只读）")
    a = psub.add_parser("disable"); a.add_argument("--by", required=True)
    a = psub.add_parser("check"); a.add_argument("--channel", required=True); a.add_argument("--action-name", required=True)
    a.add_argument("--account", default="")
    a.add_argument("--platform-user-id", default="", help="用于核对策略声明的账号身份")
    sp.set_defaults(fn=cmd_policy)

    sp = sub.add_parser("cycle", help="夜间互动循环：通知优先 → 实时选路 → 额度/间隔 → 回读记账")
    cysub = sp.add_subparsers(dest="action", required=True)
    cysub.add_parser("smoke", help="离线自检窗口、间隔与空队列计划")
    a = cysub.add_parser("quota", help="今夜与近一小时已用额度")
    a.add_argument("--now", default="", help="模拟当前时间，ISO 格式")
    cysub.add_parser("pending-auth", help="三平台 owner-attested 授权窗口（只读）")
    a = cysub.add_parser("attach", help="列出现有标签页并读三平台身份（只读，不新建标签页）")
    a.add_argument("--session", default="", help="WebBridge 会话名；默认 ovo-night-run")
    a = cysub.add_parser("inbox", help="只读收集通知回复并合并进队列（不发送）")
    a.add_argument("--lookback-hours", type=int, default=0, help="回看小时数；不填用策略值")
    a.add_argument("--queue", default="", help="队列文件；默认 data/state/engagement-queue.json")
    a.add_argument("--session", default="", help="WebBridge 会话名；不填用策略值")
    a = cysub.add_parser("enqueue", help="校验、去重并合并已准备草稿（只写本地队列）")
    a.add_argument("--file", required=True, help="JSON 数组、JSONL 或含 items 数组的 JSON")
    a.add_argument("--queue", default="")
    a.add_argument("--now", default="", help="入队时间，ISO 格式；不填用当前时间")
    a = cysub.add_parser("plan", help="把队列排成带间隔的执行计划（不发送）")
    a.add_argument("--queue", default="")
    a.add_argument("--now", default="", help="模拟当前时间，ISO 格式")
    a.add_argument("--budget-sec", type=int, default=3000, help="单波最长秒数")
    a.add_argument("--max-actions", type=int, default=0, help="本波最多动作数；0 用策略上限")
    a.add_argument("--campaign", default="")
    a = cysub.add_parser("run", help="执行一波；默认 dry-run，--go 才写")
    a.add_argument("--queue", default="")
    a.add_argument("--wave", default="", help="波次标签，默认当前 HHMM")
    a.add_argument("--budget-sec", type=int, default=3000)
    a.add_argument("--max-actions", type=int, default=0)
    a.add_argument("--go", action="store_true", help="真正执行；不加只列计划")
    a.add_argument("--session", default="", help="WebBridge 会话名；不填用策略值")
    a.add_argument("--prefetch", dest="prefetch", action="store_true", default=None,
                   help="执行期间按缺口后台预取 GitHub/内容候选")
    a.add_argument("--no-prefetch", dest="prefetch", action="store_false",
                   help="关闭本波后台预取")
    a.add_argument("--inbox", dest="auto_inbox", action="store_true", default=None,
                   help="本波排程前重新收集入站回复")
    a.add_argument("--no-inbox", dest="auto_inbox", action="store_false",
                   help="本波不重新收集；已准备的 inbox 行仍优先")
    for _sub in cysub.choices.values():
        _sub.add_argument("--json", action="store_true", help="兼容参数：输出本就是 JSON")
    sp.set_defaults(fn=cmd_cycle)

    sp = sub.add_parser("supply", help="候选储备：挖掘目标与写作角度、看深度、挑候选、登记 KOC")
    susub = sp.add_subparsers(dest="action", required=True)
    a = susub.add_parser("stats", help="按渠道看可用候选、每夜需求与缺口")
    a.add_argument("--nights", type=int, default=3, help="按几晚容量计算；默认 3")
    a = susub.add_parser("mine", help="挖掘候选并入储备（不写队列、不发送）")
    a.add_argument("--channel", required=True, choices=["github", "x", "content"])
    a.add_argument("--query", action="append", default=[], help="覆盖默认查询，可重复")
    a.add_argument("--per-query", type=int, default=20, help="GitHub 每条查询取回条数")
    a.add_argument("--limit", type=int, default=12, help="X 每条查询最多保留条数")
    a.add_argument("--days", type=int, default=14, help="GitHub 只看最近几天创建的 issue")
    a.add_argument("--gap", type=float, default=0, help="查询间隔秒数；0 用各自默认值")
    a.add_argument("--session", default="", help="WebBridge 会话名；不填用策略值")
    a.add_argument("--ledger", default="", help="账本路径；默认 data/ledger.jsonl")
    a = susub.add_parser("pick", help="按分数取 N 条未用候选（只读）")
    a.add_argument("--channel", required=True)
    a.add_argument("--action-name", required=True)
    a.add_argument("--n", type=int, default=5)
    a.add_argument("--min-score", type=int, default=0)
    a.add_argument("--exclude", default="", help="逗号分隔的 key，跳过")
    a = susub.add_parser("mark", help="标记候选状态：new/used/rejected/expired")
    a.add_argument("--key", required=True)
    a.add_argument("--status", required=True, choices=["new", "used", "rejected", "expired"])
    a.add_argument("--why", default="")
    a = susub.add_parser("koc", help="查看 KOC 账号登记")
    a.add_argument("--limit", type=int, default=0, help="只列前 N 个；0 列全部")
    a = susub.add_parser("wave", help="按每波额度生成某一波的工作清单（不写正文、不改状态）")
    a.add_argument("--wave", dest="wave_no", type=int, default=1, help="波次序号，仅作标记")
    a.add_argument("--out", default="", help="清单路径；默认 data/state/wave-supply.json")
    a = susub.add_parser("import-watchlist", help="导入旧 run 的 koc_watchlist.json")
    a.add_argument("--path", required=True)
    for _sub in susub.choices.values():
        _sub.add_argument("--json", action="store_true", help="兼容参数：输出本就是 JSON")
    sp.set_defaults(fn=cmd_supply)

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
    a = wsub.add_parser("update"); a.add_argument("--id", required=True); a.add_argument("--file", required=True); a.add_argument("--by", default="operator")
    a = wsub.add_parser("replace"); a.add_argument("--id", required=True); a.add_argument("--file", required=True); a.add_argument("--by", default="operator")
    a = wsub.add_parser("next")
    sp.set_defaults(fn=cmd_work)

    sp = sub.add_parser("sop", help="GTM OS：版本化专业 SOP 与证据门运行器")
    ssub = sp.add_subparsers(dest="action", required=True)
    a = ssub.add_parser("list")
    a = ssub.add_parser("coverage", help="人读：18 个能力域的 SOP 覆盖率与未覆盖项")
    a = ssub.add_parser("show"); a.add_argument("sop_id"); a.add_argument("--version", default="")
    a = ssub.add_parser("lint"); a.add_argument("--file", required=True)
    a = ssub.add_parser("register"); a.add_argument("--file", required=True); a.add_argument("--by", default="operator")
    a = ssub.add_parser("start"); a.add_argument("--work-order-id", required=True); a.add_argument("--by", default="operator")
    a = ssub.add_parser("run"); a.add_argument("--run-id", required=True)
    a = ssub.add_parser("complete"); a.add_argument("--run-id", required=True); a.add_argument("--step-id", required=True)
    a.add_argument("--outputs", required=True, help="步骤产物 JSON 数组文件")
    a.add_argument("--dod", default="", help="DoD 结果 JSON 数组文件（完成步骤必填）")
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
    a.add_argument("--count", action="store_true",
                   help="计数型指标：显式声明无分母，formula=numerator_count")
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
    a.add_argument("--allow-unbound", action="store_true",
                   help="允许交付物未声明 execution_payload 的旧路径；会在 provenance 中标记 unbound_explicit")
    a.add_argument("--binding-note", default="", help="解释为何允许未绑定载荷")
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

    sp = sub.add_parser("tactic", help="非常规增长战术：注册表、候选检查、执行记录与四周判定")
    tsub = sp.add_subparsers(dest="action", required=True)
    a = tsub.add_parser("list", help="列出战术（可按渠道/机制/风险筛选；证据过期默认排除）")
    a.add_argument("--channel", default=""); a.add_argument("--mechanism", default="")
    a.add_argument("--risk-max", default="gray", choices=["white", "light_gray", "gray"])
    a.add_argument("--status", default="", choices=["", "usable", "proposed"])
    a.add_argument("--include-stale", action="store_true",
                   help="包含证据过期未复核的战术（mkt tactic stale 可查）")
    a = tsub.add_parser("show", help="看一条战术的全部参数与证据")
    a.add_argument("id")
    a = tsub.add_parser("start", help="开一条战术执行记录（默认四周）")
    a.add_argument("id"); a.add_argument("--tactic", required=True)
    a.add_argument("--owner", default=""); a.add_argument("--on", default="")
    a.add_argument("--weeks", type=int, default=4)
    a.add_argument("--target-list", default="",
                   help="关联目标清单 id（koc-first 类战术：mkt target 先建）")
    a = tsub.add_parser("draft", help="加一条候选回复（发出前检查：禁链/占位符/重复/上限）")
    a.add_argument("id"); a.add_argument("--target", required=True)
    a.add_argument("--text", required=True)
    a.add_argument("--formula", default="", help="内容公式（mkt tactic show 可见）")
    a.add_argument("--post-age", type=int, default=None, help="目标帖子已发布分钟数")
    a.add_argument("--hour", type=int, default=None, help="准备回复的小时（节奏核算用）")
    a.add_argument("--on", default="")
    a = tsub.add_parser("check", help="只检查不落盘：一条候选是否可发")
    a.add_argument("--tactic", required=True); a.add_argument("--target", required=True)
    a.add_argument("--text", required=True); a.add_argument("--formula", default="")
    a.add_argument("--post-age", type=int, default=None)
    a = tsub.add_parser("log", help="记录当日实际发生数")
    a.add_argument("id")
    a.add_argument("--sent", type=int, default=0); a.add_argument("--deleted", type=int, default=0)
    a.add_argument("--replied", type=int, default=0); a.add_argument("--moved", type=int, default=0)
    a.add_argument("--metrics", default="",
                   help="周指标 JSON，如 '{\"profile_visits\": 210, \"new_followers\": 31}'")
    a.add_argument("--note", default=""); a.add_argument("--on", default="")
    a = tsub.add_parser("show-run", help="看执行进度、配额与纪律提醒")
    a.add_argument("id"); a.add_argument("--on", default="")
    a = tsub.add_parser("verdict", help="学习观察判定（人判，不自动判渠道生死）")
    a.add_argument("id"); a.add_argument("--on", default="")
    a = tsub.add_parser("export", help="把候选导出成队列文件，走既有审批发送路径")
    a.add_argument("id"); a.add_argument("--campaign", default="")
    a = tsub.add_parser("sync", help="回读账本核对实际发出数（committed 才算发出）")
    a.add_argument("id")
    a = tsub.add_parser("stale", help="证据到期未复核的战术（机会随采用人数衰减）")
    a.add_argument("--on", default="")
    a = tsub.add_parser("forbidden", help="已证伪的做法清单：证据齐全，不要碰")
    a = tsub.add_parser("recommend", help="按产品事实包+目标选战术组合（带入选理由）")
    a.add_argument("--facts", required=True, help="产品事实包 JSON 路径")
    a.add_argument("--goal", default="", help="目标 id（如 awareness.followers）")
    a.add_argument("--phase", default="cold_start", choices=["cold_start", "scale"])
    a.add_argument("--risk-max", default="white", choices=["white", "light_gray", "gray"])
    a.add_argument("--top", type=int, default=6)
    a.add_argument("--include-stale", action="store_true")
    a = tsub.add_parser("lint", help="注册表不变量自检")
    sp.set_defaults(fn=cmd_tactic)

    sp = sub.add_parser("watch", help="对手事件监控：价格/status/changelog diff → 套利事件与承接战术")
    wsub = sp.add_subparsers(dest="action", required=True)
    a = wsub.add_parser("start", help="建一个监控清单")
    a.add_argument("id"); a.add_argument("--owner", default="")
    a = wsub.add_parser("add", help="加监控项（加时即抓基线快照；死链/内网拒收）")
    a.add_argument("id"); a.add_argument("--url", required=True)
    a.add_argument("--kind", required=True, choices=["price", "status", "changelog"])
    a.add_argument("--competitor", required=True)
    a.add_argument("--note", default="")
    a.add_argument("--allow-local-file", action="store_true",
                   help="仅测试/离线演示：允许 file:// 本地页面（生产默认拒绝）")
    a = wsub.add_parser("remove", help="移除监控项")
    a.add_argument("id"); a.add_argument("--url", required=True)
    a = wsub.add_parser("check", help="抓取并 diff（cron 调用；变化只记 page_content_changed）")
    a.add_argument("id")
    a.add_argument("--allow-local-file", action="store_true",
                   help="仅测试/离线演示：允许 file:// 本地页面（生产默认拒绝）")
    a = wsub.add_parser("events", help="看变化事件（新在前；含人工核实状态）")
    a.add_argument("id"); a.add_argument("--n", type=int, default=20)
    a = wsub.add_parser("confirm", help="人工核实一条事件（追加式审计，不改原事件）")
    a.add_argument("id"); a.add_argument("event_id")
    a.add_argument("--claim", required=True,
                   choices=["price_change", "status_incident",
                            "changelog_update", "content_only", "rejected"])
    a.add_argument("--by", required=True); a.add_argument("--note", required=True)
    a = wsub.add_parser("summary", help="清单概况")
    a.add_argument("id")
    sp.set_defaults(fn=cmd_watch)

    sp = sub.add_parser("target", help="信任节点目标库：KOC 评分、先打谁、阶段推进（研究 II）")
    tgsub = sp.add_subparsers(dest="action", required=True)
    a = tgsub.add_parser("start", help="建一个目标清单（Dream-100 式）")
    a.add_argument("id"); a.add_argument("--niche", default="")
    a.add_argument("--owner", default="")
    a = tgsub.add_parser("score", help="只评分不入库：一个候选账号的信任价格")
    a.add_argument("--followers", type=int, required=True)
    a.add_argument("--engagement-rate", default="", help="如 0.032（3.2%）")
    a.add_argument("--sponsored-recent", type=int, default=None, help="近 90 天商单数")
    a.add_argument("--niche-match", action="store_true")
    a = tgsub.add_parser("add", help="加入候选（自动评分、同账号去重）")
    a.add_argument("id"); a.add_argument("--platform", required=True)
    a.add_argument("--handle", required=True)
    a.add_argument("--followers", type=int, required=True)
    a.add_argument("--engagement-rate", default="")
    a.add_argument("--sponsored-recent", type=int, default=None)
    a.add_argument("--niche-match", action="store_true")
    a.add_argument("--note", default=""); a.add_argument("--on", default="")
    a = tgsub.add_parser("list", help="按信任价格排序看全清单")
    a.add_argument("id")
    a = tgsub.add_parser("next", help="下一批该接触谁（候选里分数最高）")
    a.add_argument("id"); a.add_argument("--n", type=int, default=5)
    a = tgsub.add_parser("advance", help="阶段推进（只进不退：candidate→contacted→engaged→amplified→partner）")
    a.add_argument("id"); a.add_argument("--handle", required=True)
    a.add_argument("--stage", required=True,
                   choices=["contacted", "engaged", "amplified", "partner"])
    a.add_argument("--note", default=""); a.add_argument("--on", default="")
    a = tgsub.add_parser("summary", help="清单进度：阶段分布、近 7 天互动、累计再传播")
    a.add_argument("id"); a.add_argument("--on", default="")
    sp.set_defaults(fn=cmd_target)

    sp = sub.add_parser("grow", help="结果闭环：给事实和目标，自动执行、读数、交付日/周结果")
    gsub = sp.add_subparsers(dest="action", required=True)

    a = gsub.add_parser("detail",
                        help="单次运行详情页：只读投影 + 自包含 HTML（P0 页面）")
    a.add_argument("id", help="run_id")
    a.add_argument("--on", default="", help="按哪天判断新鲜度（默认今天）")
    a.add_argument("--html", default="", help="导出完全自包含 HTML（拒绝覆盖）")
    a.add_argument("--json", action="store_true", help="输出结构化投影 JSON")

    a = gsub.add_parser("reply",
                        help="回复统一入口：授权导入、人工改判、DNC、队列（只登记不发送）")
    rsub = a.add_subparsers(dest="reply_action", required=True)
    a = rsub.add_parser("import", help="导入授权回复文件（CSV/JSONL）；重复导入不新增")
    a.add_argument("file", help="回复文件路径（CSV/JSONL/JSON）")
    a.add_argument("--account", required=True, help="接收回复的账号 id")
    a.add_argument("--channel", default="email",
                   choices=["email", "x", "linkedin", "reddit", "github",
                            "discord", "hn"])
    a.add_argument("--by", required=True, help="导入人署名")
    a.add_argument("--authorization", required=True,
                   help="授权引用（工单/邮件/批次）")
    a.add_argument("--run", default="", help="关联 run id（关联不上进未关联队列）")
    a.add_argument("--ignore-unmapped", action="store_true",
                   help="忽略未映射列（默认拒绝，避免静默丢字段）")
    a.add_argument("--auto-dnc-structured", action="store_true",
                   help="结构化退订（如 List-Unsubscribe）直接登记 DNC")
    a.add_argument("--dry-run", action="store_true", help="只预演，不落盘")
    a.add_argument("--json", action="store_true")
    a = rsub.add_parser("link", help="给未关联回复补登 run/action_ref（追加版本，不改原文）")
    a.add_argument("reply_case_id")
    a.add_argument("--run", required=True, help="关联 run id")
    a.add_argument("--action-ref", default="", help="关联动作引用")
    a.add_argument("--by", required=True, help="补登人署名")
    a.add_argument("--authorization", required=True,
                   help="授权引用（工单/邮件/批次）")
    a.add_argument("--json", action="store_true")
    a = rsub.add_parser("review", help="人工改判分类候选（追加版本，保留原候选）")
    a.add_argument("reply_case_id")
    a.add_argument("--by", required=True)
    a.add_argument("--label", required=True,
                   choices=["interested", "not_interested", "unsubscribe",
                            "OOO", "question", "unknown"])
    a.add_argument("--note", default="")
    a.add_argument("--json", action="store_true")
    a = rsub.add_parser("dnc", help="确认退订并登记 DNC（写入抑制集；不提供解除）")
    a.add_argument("reply_case_id")
    a.add_argument("--by", required=True)
    a.add_argument("--reason", default="")
    a.add_argument("--json", action="store_true")
    a = rsub.add_parser("list", help="只读：统一回复队列")
    a.add_argument("--run", default="")
    a.add_argument("--state", default="", help="unresolved/dnc_pending 或精确状态")
    a.add_argument("--limit", type=int, default=0)
    a.add_argument("--json", action="store_true")
    a = rsub.add_parser("show", help="只读：单个回复案例与版本历史")
    a.add_argument("reply_case_id")
    a.add_argument("--json", action="store_true")

    a = gsub.add_parser("platforms", help="第一期能自动执行的平台白名单")
    a = gsub.add_parser("handoff-verovo", help="导出操作/业务事件/Askovo 上下文为 Verovo 未入账证据包")
    a.add_argument("id", help="run_id 或 campaign_id")
    a.add_argument("--out", required=True, help="新文件路径；拒绝覆盖")
    a = gsub.add_parser("launch", help="一条命令：预检→编译策略→生成执行计划与内容需求单")
    a.add_argument("id"); a.add_argument("--facts", required=True, help="产品事实包 JSON")
    a.add_argument("--goal", required=True, help="目标 id，如 awareness.followers")
    a.add_argument("--channels", default="x,email")
    a.add_argument("--target", type=float, default=None)
    a.add_argument("--window", type=int, default=None, help="目标窗口天数")
    a.add_argument("--actions-per-week", type=int, default=20)
    a.add_argument("--actions", default="", help="主渠道动作，逗号分隔：reply,publish（默认 reply）")
    a.add_argument("--account", default="", help="账号 id（账号库里的 id，用于健康闸）")
    a.add_argument("--handle", default="", help="X 账号 handle，用于每日自动读数")
    a.add_argument("--audience", default="us-west", help="受众时区目录 id")
    a.add_argument("--owner", default="", help="结果单收件邮箱")
    a.add_argument("--on", default="", help="启动日（默认今天）")
    a.add_argument("--skip-probe", action="store_true",
                   help="跳过环境探针，只建计划（首次实跑前先 doctor）")

    a = gsub.add_parser("agent", help="启用本机 Codex 自动找目标、生成、审稿和补队列")
    a.add_argument("id"); a.add_argument("--disable", action="store_true")
    a = gsub.add_parser("produce", help="立即生成并审稿补队列，不发送")
    a.add_argument("id"); a.add_argument("--on", default="")
    a.add_argument("--max-items", type=int, default=3)

    a = gsub.add_parser("start",
                        help="九步启动向导（不带 id）；带 id 时兼容旧版 v1 闭环")
    a.add_argument("id", nargs="?", default="",
                   help="旧版 v1 闭环 id（不填 = 走九步向导）")
    a.add_argument("--facts", required=True, help="产品事实包 JSON")
    a.add_argument("--goal", required=True, help="目标 id，如 awareness.followers")
    a.add_argument("--channels", default="")
    a.add_argument("--target", type=int, default=None)
    a.add_argument("--window", type=int, default=None, help="目标窗口天数")
    a.add_argument("--actions-per-week", type=int, default=None)
    a.add_argument("--owner", default=""); a.add_argument("--on", default="")
    a.add_argument("--go", action="store_true", help="建完直接开实跑（默认 dry-run）")
    a.add_argument("--run-id", default="", help="向导建的 run id（默认按产品名+日期）")
    a.add_argument("--action", dest="wizard_action", default="message",
                   help="向导的主渠道动作（默认 message）")
    a.add_argument("--account", default="", help="授权账号 id（账号健康/身份检查）")
    a.add_argument("--handle", default="", help="X handle（读数）")
    a.add_argument("--audience", default="us-west")
    a.add_argument("--schedule", choices=["foreground", "scheduled"],
                   default="foreground", help="前台常驻或生成计划任务文件")
    a.add_argument("--delivery", choices=["local", "email"], default="local",
                   help="日报订阅：默认本地文件；email 需单独授权")
    a.add_argument("--email-owner", default="")
    a.add_argument("--authorize-email", action="store_true",
                   help="单独授权邮件投递（向导本身不发送）")
    a.add_argument("--source-system", action="append", default=[],
                   help="已授权的业务事件来源（可重复）")
    a.add_argument("--dry-run", action="store_true",
                   help="只出九步计划，不写文件（默认行为，显式写更清楚）")
    a.add_argument("--apply", action="store_true", help="真的建计划（仍不 arm）")
    a.add_argument("--json", action="store_true")

    a = gsub.add_parser("tick", help="一步日循环：配额执行+自动读数+日结果+周结算（供 cron）")
    a.add_argument("id"); a.add_argument("--on", default="")
    a.add_argument("--deliver", action="store_true", help="同时投递周结果单（默认只落盘）")
    a = gsub.add_parser("arm", help="显式开闸（必须人署名；检查授权/账号健康/阻塞告警）")
    a.add_argument("id")
    a.add_argument("--by", default="", help="开闸人署名（必填；声明式身份）")
    a = gsub.add_parser("resume",
                        help="人工裁决后恢复 needs_attention/paused（必须署名；indeterminate 先裁决）")
    a.add_argument("id"); a.add_argument("--by", required=True)
    a.add_argument("--note", default="", help="处理了什么：哪条队列、结论、谁修的")
    a.add_argument("--after-reconciliation", default="",
                   help="已核对的事件 ID（mkt grow incident --list 里的 INC-…）")
    a = gsub.add_parser("incident",
                        help="运行告警：每条有唯一事件 ID 与人工恢复建议（只读）")
    a.add_argument("id"); a.add_argument("--list", action="store_true",
                                         help="列出告警（默认也是列）")
    a.add_argument("--on", default="")
    a.add_argument("--probe-login", action="store_true",
                   help="现场探测登录态（默认不碰浏览器）")
    a.add_argument("--json", action="store_true")
    a = gsub.add_parser("pause", help="一键暂停：下一轮 tick 不再选中任何条目")
    a.add_argument("id"); a.add_argument("--reason", default="")
    a.add_argument("--by", default="")
    a = gsub.add_parser("daily", help="今天的日结果（发布量/读数/供给/护栏）")
    a.add_argument("id"); a.add_argument("--on", default="")
    a = gsub.add_parser("decide", help="看完日结果后决定：继续任务 或 优化策略")
    a.add_argument("id"); a.add_argument("--action", dest="decision", required=True,
                                        choices=["continue", "optimize"])
    a.add_argument("--note", default="", help="为什么这样决定")
    a.add_argument("--by", default=""); a.add_argument("--on", default="")
    a.add_argument("--dry-run", action="store_true", help="只出提案，不落盘")
    a.add_argument("--json", action="store_true")
    a = gsub.add_parser("briefs", help="看内容需求单（agent 按这个生产）")
    a.add_argument("id")
    a = gsub.add_parser("stage", help="把内容写进按日队列（agent 供给入口，过门禁才落盘）")
    a.add_argument("id"); a.add_argument("--from", dest="from_file", required=True,
                                         help="内容文件：JSON 数组 / items / CSV(target,body)")
    a.add_argument("--date", default="", help="排期日 YYYY-MM-DD（默认今天）")
    a.add_argument("--channel", default="")
    a.add_argument("--action", dest="queue_action", default="")
    a.add_argument("--window", default="", help="如 20:00-23:00@us-west，缺省用全局窗口")
    a.add_argument("--by", default="agent")
    a.add_argument("--resolve-target", action="store_true",
                   help="target_text 缺失时打开原帖自动读取（只读）")
    a.add_argument("--strict", action="store_true",
                   help="结构分 <55 或未知公式时拒绝入队")
    a = gsub.add_parser("formulas", help="内容公式与配比（回复层/证明层）")
    a = gsub.add_parser("prospects", help="账号级候选：帖子契合度+作者主页+排序")
    a.add_argument("id")
    a.add_argument("--query", action="append", default=[],
                   help="搜索词，可重复；支持 since:/until: 操作符")
    a.add_argument("--limit", type=int, default=20)
    a.add_argument("--top", type=int, default=8)
    a.add_argument("--max-age-hours", type=float, default=48)
    a.add_argument("--min-likes", type=int, default=0)
    a.add_argument("--pages", type=int, default=1)
    a.add_argument("--no-enrich", action="store_true", help="不读作者主页（快，但账号分弱）")
    a.add_argument("--koc-only", action="store_true", help="只看 KOC（1k–50k 粉丝）")
    a.add_argument("--all-accounts", action="store_true", help="不限 KOC，按总契合度排序")
    a.add_argument("--include-engaged", action="store_true",
                   help="包含已触达账号（默认排除，避免重复打扰）")
    a.add_argument("--min-followers", type=int, default=0)
    a.add_argument("--max-followers", type=int, default=0)
    a.add_argument("--exclude-seen-days", type=int, default=0,
                   help="排除最近 N 天已进过名单的 handle（0=不排除）")
    a.add_argument("--on", default=""); a.add_argument("--json", action="store_true")
    a = gsub.add_parser("koc", help="KOC 名单：已触达/未触达、分数与量级")
    a.add_argument("id"); a.add_argument("--json", action="store_true")
    a = gsub.add_parser("metrics", help="采集已发帖子的公开指标（查看/喜欢/回复）")
    a.add_argument("id"); a.add_argument("--days", type=int, default=7)
    a.add_argument("--limit", type=int, default=20)
    a.add_argument("--resolve", action="store_true",
                   help="先回原帖解析自己的帖子 URL 再采集")
    a.add_argument("--on", default=""); a.add_argument("--json", action="store_true")
    a = gsub.add_parser("learn", help="按公式/窗口归因，给出次日调参建议")
    a.add_argument("id"); a.add_argument("--on", default="")
    a.add_argument("--json", action="store_true")
    a = gsub.add_parser("targets", help="只读发现回复目标（X 搜索），产出候选清单")
    a.add_argument("id")
    a.add_argument("--query", action="append", default=[],
                   help="搜索词，可重复；支持 since:/until: 操作符")
    a.add_argument("--limit", type=int, default=10)
    a.add_argument("--max-age-hours", type=float, default=48)
    a.add_argument("--min-likes", type=int, default=0)
    a.add_argument("--min-replies", type=int, default=0)
    a.add_argument("--pages", type=int, default=1)
    a.add_argument("--on", default=""); a.add_argument("--json", action="store_true")
    a = gsub.add_parser("review", help="最近 N 天业务复盘（发布/读数/供给/决策/建议）")
    a.add_argument("id"); a.add_argument("--days", type=int, default=7)
    a.add_argument("--on", default=""); a.add_argument("--json", action="store_true")
    a = gsub.add_parser("daemon", help="前台常驻 tick（单实例锁；Ctrl-C 停止）")
    a.add_argument("id"); a.add_argument("--interval", type=int, default=30)
    a.add_argument("--no-deliver", action="store_true")
    a.add_argument("--once", action="store_true", help="只跑一轮（供测试/手动）")
    a = gsub.add_parser("events", help="授权产品事件：导入/清单/补 L1 回执（默认只校验不落盘）")
    esub = a.add_subparsers(dest="events_action", required=True)
    e = esub.add_parser("import", help="从 JSON/JSONL/CSV 导入产品事件（默认 dry-run）")
    e.add_argument("--from", dest="from_file", required=True,
                   help="事件文件：JSON 数组 / events 数组 / JSONL / CSV")
    e.add_argument("--allowed-source", action="append", default=[],
                   help="授权来源系统（source_system），可重复，如 product_export")
    e.add_argument("--by", default="", help="导入人署名（落盘必填，用于审计）")
    e.add_argument("--window-start", default="", help="只收该日期之后发生的事件")
    e.add_argument("--window-end", default="", help="只收该日期之前发生的事件")
    e.add_argument("--apply", action="store_true", help="真写入（默认只校验）")
    e.add_argument("--trusted-verifier", action="append", default=[],
                   help="操作员额外认可的核验方 id（可重复，追加到默认白名单）")
    e.add_argument("--json", action="store_true")
    e = esub.add_parser("list", help="已导入的产品事件与资格状态（只读）")
    e.add_argument("--id", default="", help="只看某个 run")
    e.add_argument("--json", action="store_true")
    e = esub.add_parser("sync", help="把账本 committed 记录补成 L1 凭证（默认 dry-run）")
    e.add_argument("--id", default=""); e.add_argument("--by", default="local-ledger")
    e.add_argument("--window-start", default=""); e.add_argument("--window-end", default="")
    e.add_argument("--apply", action="store_true"); e.add_argument("--json", action="store_true")

    a = gsub.add_parser("layers", help="三层结果汇总：L1 执行/L2 互动/L3 业务 + 激活归因（只读）")
    a.add_argument("id", nargs="?", default="", help="run id（不填=全部）")
    a.add_argument("--window-start", default=""); a.add_argument("--window-end", default="")
    a.add_argument("--json", action="store_true")

    a = gsub.add_parser("journey", help="一个人的进度归并：触达/回应/产品事件/抑制（只读）")
    a.add_argument("ref", help="邮箱、平台 ID 或 contact id，如 x:111")
    a.add_argument("--id", default="", help="只看某个 run")
    a.add_argument("--json", action="store_true")

    a = gsub.add_parser("qualify", help="人工改一条产品事件的资格（原事件不改，留审计；默认 dry-run）")
    a.add_argument("event_id", nargs="?", default="")
    a.add_argument("--list", action="store_true",
                   help="列出资格变更审计（可配 --event 过滤），只读")
    a.add_argument("--event", default="", help="--list：只看某条事件的变更历史")
    a.add_argument("--yes", action="store_true", help="计入")
    a.add_argument("--no", action="store_true", help="不计入")
    a.add_argument("--by", default="", help="谁判的（必填，审计用）")
    a.add_argument("--note", default="", help="依据（必填，如「内部测试账号」）")
    a.add_argument("--apply", action="store_true", help="真写入（默认只预演）")
    a.add_argument("--json", action="store_true")

    a = gsub.add_parser(
        "goal-exit",
        help="目标退出与个体旅程（G-NEXT-2）：契约/评估/停止记录（写入默认预演）")
    xsub = a.add_subparsers(dest="goal_exit_action", required=True)
    x = xsub.add_parser("contract", help="登记目标契约：什么算达成、观察窗口（默认只预演）")
    x.add_argument("--run", required=True, help="run id")
    x.add_argument("--goal-type", required=True,
                   choices=["signup", "activation", "paid"])
    x.add_argument("--target", type=int, default=1, help="目标事件条数（默认 1）")
    x.add_argument("--metric", default="", help="指标名（默认同事件类型）")
    x.add_argument("--label", default="", help="人读目标名")
    x.add_argument("--verify-state", default="",
                   help="只认该核验状态的事件，如 verified")
    x.add_argument("--window-start", required=True, help="观察窗口开始日 YYYY-MM-DD")
    x.add_argument("--window-end", required=True, help="观察窗口结束日 YYYY-MM-DD")
    x.add_argument("--attr-days", type=int, default=7, help="归因窗口天数（默认 7）")
    x.add_argument("--exit", action="append", default=[],
                   help="运行级退出声明 kind=source_ref[::subject_ref]，可重复")
    x.add_argument("--by", default="", help="登记人署名")
    x.add_argument("--note", default="")
    x.add_argument("--apply", action="store_true", help="真写入（默认只预演）")
    x.add_argument("--json", action="store_true")
    x = xsub.add_parser("evaluate", help="按契约评估一个对象（默认只算不落盘）")
    x.add_argument("--contract", required=True, help="契约 id（gec-…）")
    x.add_argument("--subject", required=True, help="邮箱、平台 ID 或 contact id")
    x.add_argument("--now", default="", help="按该时间判断（测试/复算）")
    x.add_argument("--apply", action="store_true",
                   help="真写入投影与停止记录（默认只预演）")
    x.add_argument("--json", action="store_true")
    x = xsub.add_parser("list", help="只读：目标契约清单")
    x.add_argument("--run", default="")
    x.add_argument("--json", action="store_true")
    x = xsub.add_parser("show", help="只读：单个契约与最新对象投影")
    x.add_argument("--contract", required=True)
    x.add_argument("--json", action="store_true")
    x = xsub.add_parser("view", help="只读：一个 run 的旅程视图（详情页同源）")
    x.add_argument("--run", required=True)
    x.add_argument("--limit", type=int, default=20)
    x.add_argument("--json", action="store_true")

    a = gsub.add_parser(
        "signal",
        help="信号证据包（G-NEXT-3）：登记/撤销/视图；写入默认预演")
    ssub = a.add_subparsers(dest="signal_action", required=True)
    sg = ssub.add_parser(
        "add", help="登记一条信号：来源、观察时间、原始摘录（默认只预演）")
    sg.add_argument("--source-family", required=True,
                   help="来源类别，如 reply / event / form / support")
    sg.add_argument("--source-ref", required=True, help="来源唯一引用（URL 或 ID）")
    sg.add_argument("--observed-at", default="",
                   help="来源观察时间（ISO8601）；缺失记 gap，不猜")
    sg.add_argument("--subject", default="", help="信号指向的对象（邮箱/账号 ID）")
    sg.add_argument("--identity-basis", default="",
                   choices=["", "verified_email", "account_id", "explicit_map"],
                   help="身份解析依据；不按相似名称或域名合并")
    sg.add_argument("--use", action="append", default=[],
                   choices=["personalization", "segmentation", "analysis"],
                   help="允许用途，可重复；默认仅 analysis")
    sg.add_argument("--consent", default="", help="同意记录引用（可空，空则记 gap）")
    sg.add_argument("--raw-file", default="", help="原始文本文件路径")
    sg.add_argument("--raw-text", default="", help="原始文本（与 --raw-file 二选一）")
    sg.add_argument("--raw-hash", default="", help="原始内容指纹（可选，自算优先）")
    sg.add_argument("--claim", action="append", default=[],
                   help="内容主张引用，可重复")
    sg.add_argument("--max-age-days", type=int, default=30,
                   help="信号有效期天数（默认 30）")
    sg.add_argument("--run", default="", help="关联 run id（可选）")
    sg.add_argument("--by", default="", help="登记人署名")
    sg.add_argument("--note", default="")
    sg.add_argument("--apply", action="store_true", help="真写入（默认只预演）")
    sg.add_argument("--json", action="store_true")
    sg = ssub.add_parser("list", help="只读：信号与预演视图（详情页同源）")
    sg.add_argument("--run", default="")
    sg.add_argument("--json", action="store_true")
    sg = ssub.add_parser("show", help="只读：单条信号及其权限")
    sg.add_argument("--signal", required=True)
    sg.add_argument("--json", action="store_true")
    sg = ssub.add_parser("revoke", help="撤销一个来源：此后其信号不可用于个性化")
    sg.add_argument("--source-family", required=True)
    sg.add_argument("--source-ref", required=True)
    sg.add_argument("--by", required=True, help="撤销人署名（必填）")
    sg.add_argument("--reason", default="", help="撤销原因")
    sg.add_argument("--apply", action="store_true", help="真写入（默认只预演）")
    sg.add_argument("--json", action="store_true")

    a = gsub.add_parser(
        "preview",
        help="变更预演（G-NEXT-3）：只读快照/stale 检查；不批准不执行")
    psub = a.add_subparsers(dest="preview_action", required=True)
    pv = psub.add_parser(
        "create", help="为一个 plan 生成变更预演快照（默认只预演，不写文件）")
    pv.add_argument("--plan", required=True, help="plan id")
    pv.add_argument("--change", action="append", default=[],
                   help="变更条目 op:kind:ref[:summary]，可重复")
    pv.add_argument("--permission", action="append", default=[],
                   help="执行所需权限，可重复（默认 plan.approve + plan.execute）")
    pv.add_argument("--approval-ref", default="",
                   help="已存在的审批引用（预演本身不产生审批）")
    pv.add_argument("--expires-at", default="", help="预演有效期（默认 24 小时）")
    pv.add_argument("--run", default="")
    pv.add_argument("--by", default="", help="生成人署名")
    pv.add_argument("--note", default="")
    pv.add_argument("--apply", action="store_true", help="真写入（默认只预演）")
    pv.add_argument("--json", action="store_true")
    pv = psub.add_parser("show", help="只读：一个预演的内容")
    pv.add_argument("--preview", required=True)
    pv.add_argument("--account", default="", help="按该账号检查")
    pv.add_argument("--workspace", default="", help="按该工作区检查")
    pv.add_argument("--json", action="store_true")
    pv = psub.add_parser("check", help="只读：预演是否仍 current/stale/过期")
    pv.add_argument("--preview", required=True)
    pv.add_argument("--account", default="")
    pv.add_argument("--workspace", default="")
    pv.add_argument("--json", action="store_true")
    pv = psub.add_parser("list", help="只读：预演清单、删除项与异常")
    pv.add_argument("--run", default="")
    pv.add_argument("--json", action="store_true")

    a = gsub.add_parser("experiment",
                        help="受控实验（G-04）：设计/批准/启动/观察/分析/清单；写入默认 dry-run")
    esub = a.add_subparsers(dest="experiment_action", required=True)
    e = esub.add_parser("design", help="预注册一个 GrowthExperiment（默认只校验）")
    e.add_argument("--id", dest="exp_id", required=True)
    e.add_argument("--hypothesis", required=True, help="假设：改什么、为什么")
    e.add_argument("--segment", required=True, help="目标人群/对象范围")
    e.add_argument("--mechanism", required=True, help="机制，如 content.mix_weights")
    e.add_argument("--baseline", required=True, help="对照或基线是什么")
    e.add_argument("--metric", required=True, help="主要指标")
    e.add_argument("--guardrail", required=True, help="护栏：什么情况算越界")
    e.add_argument("--start", required=True); e.add_argument("--end", required=True)
    e.add_argument("--min-sample", type=int, required=True,
                   help="预声明最小样本（少于它只能算探索）")
    e.add_argument("--limitations", required=True, help="分析局限")
    e.add_argument("--stop-conditions", required=True,
                   help="停止条件，逗号分隔")
    e.add_argument("--decision-rule", default="", help="判定规则（缺省按样本+护栏生成）")
    e.add_argument("--owner", default="")
    e.add_argument("--randomized", action="store_true", help="是否随机分组")
    e.add_argument("--assignment-ref", default="", help="随机分组记录在哪")
    e.add_argument("--apply", action="store_true"); e.add_argument("--json", action="store_true")
    e = esub.add_parser("approve", help="人审批准实验计划（绑定计划指纹）")
    e.add_argument("id"); e.add_argument("--by", required=True)
    e.add_argument("--note", default="")
    e.add_argument("--apply", action="store_true"); e.add_argument("--json", action="store_true")
    e = esub.add_parser("start", help="批准且计划未变才允许开跑")
    e.add_argument("id")
    e.add_argument("--apply", action="store_true"); e.add_argument("--json", action="store_true")
    e = esub.add_parser("observe", help="记一条观察（同一 evidence 只记一次）")
    e.add_argument("id"); e.add_argument("--note", required=True)
    e.add_argument("--value", type=float, default=None)
    e.add_argument("--evidence-ref", default=""); e.add_argument("--evidence-hash", default="")
    e.add_argument("--approximated", action="store_true")
    e.add_argument("--apply", action="store_true"); e.add_argument("--json", action="store_true")
    e = esub.add_parser("analyze", help="只读分析：口径、样本门槛、措辞（不自动下结论）")
    e.add_argument("id"); e.add_argument("--json", action="store_true")
    e = esub.add_parser("compare", help="冻结三种替代的比较上下文（§12.5；默认 dry-run）")
    e.add_argument("id")
    e.add_argument("--product", required=True)
    e.add_argument("--channels", default="x", help="渠道，逗号分隔")
    e.add_argument("--quota-actions", type=int, default=7,
                   help="冻结的每周动作配额")
    e.add_argument("--start", required=True); e.add_argument("--end", required=True)
    e.add_argument("--cost-basis", required=True, help="成本归集口径")
    e.add_argument("--apply", action="store_true"); e.add_argument("--json", action="store_true")
    e = esub.add_parser("metrics", help="§12.5 七项结果指标（只读；缺项 unknown）")
    e.add_argument("--run", default=""); e.add_argument("--start", default="")
    e.add_argument("--end", default="")
    e.add_argument("--json", action="store_true")
    e = esub.add_parser("list", help="实验清单（只读）")
    e.add_argument("--json", action="store_true")

    a = gsub.add_parser("change", help="策略改动（G-04）：清单/人审批准/回滚；写入默认 dry-run")
    csub = a.add_subparsers(dest="change_action", required=True)
    c = csub.add_parser("list", help="改动、批准与已应用证据（只读）")
    c.add_argument("id"); c.add_argument("--json", action="store_true")
    c = csub.add_parser("approve", help="批准当前提案里的某一项改动（按 change sha）")
    c.add_argument("id"); c.add_argument("--change", required=True,
                                         help="改动的 change_sha256（decide 输出里有）")
    c.add_argument("--by", required=True); c.add_argument("--on", default="")
    c.add_argument("--apply", action="store_true"); c.add_argument("--json", action="store_true")
    c = csub.add_parser("rollback", help="撤销上一次策略改动（不撤销已发外部动作）")
    c.add_argument("id"); c.add_argument("--change", default="",
                                         help="指定 change_id；缺省撤销最后一次")
    c.add_argument("--by", required=True); c.add_argument("--on", default="")
    c.add_argument("--apply", action="store_true"); c.add_argument("--json", action="store_true")

    a = gsub.add_parser("calibrate", help="读数人工对数：连续 7 个日历日（每日一计、缺日重置）后升级 PROVEN")
    a.add_argument("id"); a.add_argument("--followers", type=int, default=None,
                                         help="人在 X 后台手读到的粉丝数")
    a.add_argument("--mismatch", action="store_true", help="本次不一致")
    a.add_argument("--note", default=""); a.add_argument("--by", default="")
    a.add_argument("--on", default="")
    a = gsub.add_parser("status",
                        help="看进度：目标/配额/供给/心跳；--explain 给 C-01 状态解释")
    a.add_argument("id"); a.add_argument("--on", default="")
    a.add_argument("--explain", action="store_true",
                   help="C-01 状态解释：责任人/下一动作/阻断码/未决外部动作/告警")
    a.add_argument("--json", action="store_true")
    a = gsub.add_parser("report", help="结果单（默认最新一周）")
    a.add_argument("id"); a.add_argument("--week", type=int, default=None)
    a.add_argument("--on", default=""); a.add_argument("--json", action="store_true")
    a = gsub.add_parser("doctor", help="体检：环境/授权/读数/供给/心跳/队列")
    a.add_argument("id")
    a = gsub.add_parser("install", help="生成定时任务（launchd/cron），让 tick 自动跑")
    a.add_argument("id"); a.add_argument("--interval", type=int, default=30)
    a.add_argument("--load", action="store_true", help="同时装进 launchd（默认只生成文件）")

    a = gsub.add_parser("handoff",
                        help="Agent 主入口的伴随 GUI：只读风险摘要、handoff 生命周期与回链")
    hsub = a.add_subparsers(dest="handoff_action", required=True)
    c = hsub.add_parser("locate", help="只读：风险摘要、下一动作与 GUI 深链建议")
    c.add_argument("--run", default="", help="只看一个 run")
    c.add_argument("--limit", type=int, default=10)
    c.add_argument("--host", default="agent",
                   help="请求来源（agent/codex/claude-code/claude-desktop/remote）")
    c.add_argument("--companion-url", default="",
                   help="伴随 GUI 地址（如 http://127.0.0.1:8765）")
    c.add_argument("--json", action="store_true")
    c = hsub.add_parser("create",
                        help="创建 handoff（Agent 唤起 GUI 的上下文；不授予写权限）")
    c.add_argument("--object-type", required=True,
                   choices=["run", "reply_case", "ledger_ref"])
    c.add_argument("--object-id", required=True)
    c.add_argument("--entry-route", default="", help="缺省按对象类型生成")
    c.add_argument("--focus-ref", default="")
    c.add_argument("--action", dest="requested_action", default="",
                   choices=["reply.review", "reply.dnc", "run.pause",
                            "ledger.resolve", "run.resume"])
    c.add_argument("--allowed-action", action="append", default=[],
                   help="允许 GUI 执行的动作，可重复；缺省为 requested action")
    c.add_argument("--ttl", type=int, default=900, help="有效秒数")
    c.add_argument("--host", default="agent", help="请求来源")
    c.add_argument("--companion-url", default="")
    c.add_argument("--by", default="agent")
    c.add_argument("--json", action="store_true")
    c = hsub.add_parser("show", help="只读：handoff 状态与回执")
    c.add_argument("handoff_ref"); c.add_argument("--json", action="store_true")
    c = hsub.add_parser("list", help="只读：本机 handoff 列表")
    c.add_argument("--json", action="store_true")
    c = hsub.add_parser("revoke", help="撤销 handoff（保留历史，不删回执）")
    c.add_argument("handoff_ref"); c.add_argument("--by", required=True)
    c.add_argument("--reason", default=""); c.add_argument("--json", action="store_true")
    c = hsub.add_parser("resolve", help="回链：重新读取对象最新状态与处理回执")
    c.add_argument("handoff_ref"); c.add_argument("--json", action="store_true")

    a = gsub.add_parser("serve",
                        help="启动本机伴随 GUI（只读投影＋受控写；Ctrl-C 停止）")
    a.add_argument("--port", type=int, default=8765)
    a.add_argument("--dry-run", action="store_true", help="只打印路由清单，不启动")
    a.add_argument("--json", action="store_true", help="配合 --dry-run 输出路由 JSON")
    a.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    sp.set_defaults(fn=cmd_grow)

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
    a = asub.add_parser("observe", help="只读：从 CDP 浏览器读取当前实际账号并留痕")
    a.add_argument("--channel", default="x")
    a.add_argument("--json", action="store_true")
    a = asub.add_parser("log", help="只读：统一操作日志（实际账号/回执；旧工作区回退账本）")
    a.add_argument("--limit", type=int, default=20)
    a.add_argument("--json", action="store_true")
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

    sp = sub.add_parser("x", help="X 渠道（vendor，PROVEN；stats 为 UNVERIFIED 读原语）")
    sp.add_argument("action", choices=["publish", "reply", "message", "engage", "search", "inbox", "stats"])
    sp.add_argument("extra", nargs="*", help="透传 vendor 参数（如搜索词、@handle、tweet url）")
    sp.add_argument("--file", help="publish 内容文件（默认 thread.json）")
    sp.add_argument("--thread", action="store_true")
    sp.add_argument("--handle", default="", help="stats：要读数的账号")
    sp.add_argument("--tweet-url", help="reply：目标推文 URL")
    sp.add_argument("--text", help="reply：回复正文")
    sp.add_argument("--record", action="store_true", help="stats：人工对数后落 outcome 计数")
    sp.add_argument("--go", action="store_true")
    sp.add_argument("--campaign")
    sp.add_argument("--account", default="",
                   help="G-01：绑定身份授权下实跑时声明的账号 ID（配合身份观测校验）")
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
    sp.add_argument("--resume-batch", dest="resume_batch", action="store_true",
                    help="人工核实上一批的未知回执/审计缺口后，清除批次阻塞标记继续")
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
                    help="约束 JSON：budget/region/authorized_channels/assets/data_sources/data_connected/events_verified")
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
    a.add_argument("--platforms", default="", help="逗号分隔：主渠道,集中发布渠道,辅助1,辅助2；不填用推荐组合")
    a.add_argument("--target", type=float, default=None, help="覆盖默认目标；不填用建议形状")
    a.add_argument("--window-days", type=int, default=None)
    a.add_argument("--secondary", default="", help="可选辅目标 id，例如 adoption.real_data")
    a.add_argument("--secondary-target", type=float, default=None)
    a.add_argument("--json", action="store_true")
    a.add_argument("--out", default="")
    a = gsub.add_parser("select", help="写入「目标 + 平台」选择契约，供 strategy/cadence 使用")
    a.add_argument("--facts", required=True)
    a.add_argument("--goal", required=True)
    a.add_argument("--platforms", required=True, help="逗号分隔：主渠道,集中发布渠道,辅助1,辅助2")
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
    a.add_argument("--launch-hour", default="21:00", help="集中发布时间（北京时间），默认 21:00")
    a.add_argument("--json", action="store_true")
    a.add_argument("--out", default="")
    sp.set_defaults(fn=cmd_sprint)

    from core.conversion import OFFERS as _OFFERS, STAGES as _STAGES
    sp = sub.add_parser("channel", help="渠道：具体地方、规则、经济学、实验")
    csub = sp.add_subparsers(dest="action", required=True)
    a = csub.add_parser("venues", help="列出渠道库；可按平台过滤")
    a.add_argument("--platform", default="")
    a.add_argument("--json", action="store_true")
    a = csub.add_parser("plan", help="生成渠道组合：主/集中发布/辅助/旁听 + 经济学")
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
    sp.add_argument("action",
                    choices=["new", "start", "obs", "list", "conclude", "stop",
                             "card-new", "card-check", "card-show"])
    sp.add_argument("--id", help="实验 id（小写/数字/连字符）")
    sp.add_argument("--card", help="card-new：假设卡 JSON 文件（支持/证伪指标、替代解释、下一轮改哪层）")
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
    try:
        return args.fn(args) or 0
    except ValueError as exc:
        # 业务输入错误给结构化输出，不让用户面对 Python traceback。
        # 已带语义载荷的异常保留自己的 reason_code，不被通用兜底覆盖。
        payload = getattr(exc, "payload", None)
        if not isinstance(payload, dict):
            payload = {"status": "error", "reason_code": "INPUT_INVALID",
                       "message": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 2


if __name__ == "__main__":
    sys.exit(main())
