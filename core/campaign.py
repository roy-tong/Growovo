#!/usr/bin/env python3
"""Campaign 视图 —— 把 plan / queue / ledger / outcome 四份数据缝成一条。

在此之前四份数据只靠命名约定关联：复盘要人肉对账，"这轮到底做了啥、值不值"
没有单一入口。本模块提供：

  campaign show   结构化视图（JSON，给 agent / 脚本）
  campaign report Markdown 一页纸（给人；也可当交付物）
  review          人审预览：队列逐条过门禁 + 已有留证 + 审批状态
  next            状态机：下一步该做什么、为什么

口径纪律：账本里的 committed 是"发出去了"，不是"转化了"；outcome 是独立台账；
报告把两者分栏展示，绝不合成一个"转化率"。
"""
import json
from datetime import datetime
from pathlib import Path

from .approval import status as approval_status
from .gates import Gate, GateError, load_suppressions
from .planlint import governing_plan
from .queue import default_queue_path, item_text, lint_queue, load_queue, max_len_for


def _resolve_queue_path(data_dir, plan, campaign):
    """按计划波次 → 约定路径找队列；找不到返回默认路径（不存在也返回）。"""
    if plan:
        for w in plan.get("waves", []):
            raw = w.get("queue") or ""
            if not raw:
                continue
            stem = Path(raw).stem
            if stem == campaign or campaign == f"{plan.get('plan_id')}-w{w.get('wave')}":
                for cand in (Path(raw), Path(data_dir) / "queues" / f"{stem}.json"):
                    if cand.is_file():
                        return cand
                return Path(data_dir) / "queues" / f"{stem}.json"
    return default_queue_path(data_dir, campaign)


def load_campaign_queue(data_dir, plan, campaign):
    path = _resolve_queue_path(data_dir, plan, campaign)
    if not path.is_file():
        return None, path
    try:
        return load_queue(path), path
    except json.JSONDecodeError:
        return None, path


def preflight(queue, data_dir):
    """逐条过门禁（不发送、不写账本）。返回 [{id,target,ok,reason}]。"""
    if not queue:
        return []
    channel, action = queue.get("channel"), queue.get("action")
    gate = Gate(ledger=None, suppress=load_suppressions(data_dir))
    cap = max_len_for(channel, action)
    rows = []
    for item in queue.get("items") or []:
        iid, target = item.get("id", ""), item.get("target", "")
        try:
            gate.check(channel=channel, action=action, target=target,
                       campaign=queue.get("campaign"), content=item_text(item),
                       max_len=cap, dedupe=False)
            rows.append({"id": iid, "target": target, "ok": True, "reason": ""})
        except GateError as exc:
            rows.append({"id": iid, "target": target, "ok": False, "reason": str(exc)})
    return rows


def collect(data_dir, ledger, campaign):
    from .attribution import OutcomeLedger
    from . import links as links_mod

    plan, plan_path = governing_plan(data_dir, campaign)
    queue, queue_path = load_campaign_queue(data_dir, plan, campaign)
    records = ledger.results(campaign=campaign)

    by_result, by_channel = {}, {}
    for r in records:
        by_result[r.get("result", "?")] = by_result.get(r.get("result", "?"), 0) + 1
        ch = r.get("channel", "?")
        by_channel.setdefault(ch, {}).setdefault(r.get("result", "?"), 0)
        by_channel[ch][r.get("result", "?")] += 1

    ol = OutcomeLedger(data_dir)
    events = [r for r in ol.records(campaign) if r.get("kind") == "event"]
    counts = [r for r in ol.records(campaign) if r.get("kind") == "count"]
    by_type = {}
    for e in events:
        by_type[e.get("event_type", "?")] = by_type.get(e.get("event_type", "?"), 0) + 1
    unattributed = [r for r in ol.records() if not r.get("campaign")]
    exp = None
    for f in sorted((Path(data_dir) / "experiments").glob("*.json")):
        try:
            e = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if e.get("campaign") == campaign or e.get("exp_id") == campaign:
            exp = e
            break

    return {
        "campaign": campaign,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "plan": (dict(plan, _path=str(plan_path)) if plan else None),
        "plan_approval": approval_status(plan) if plan else None,
        "queue": {
            "path": str(queue_path),
            "exists": queue is not None,
            "items": len((queue or {}).get("items") or []),
            "channel": (queue or {}).get("channel"),
            "action": (queue or {}).get("action"),
            "lint_errors": lint_queue(queue, path=queue_path) if queue else [],
            "preflight": preflight(queue, data_dir),
        } if queue is not None else {
            "path": str(queue_path), "exists": False, "items": 0,
            "lint_errors": [], "preflight": [],
        },
        "ledger": {
            "records": len(records),
            "by_result": by_result,
            "by_channel": by_channel,
            "committed": by_result.get("committed", 0),
            "dry_run": by_result.get("dry-run", 0),
        },
        "outcomes": {
            "events": len(events),
            "by_type": by_type,
            "counts": len(counts),
            "unattributed_global": len(unattributed),
        },
        "links": links_mod.list_links(data_dir, campaign),
        "experiment": exp,
    }


def _experiment_line(exp):
    if not exp:
        return "—（还没有为这轮 campaign 建实验）"
    return (f"{exp.get('exp_id')} · {exp.get('status')} · 指标 {exp.get('metric')} · "
            f"规则 {exp.get('decision_rule')}")


def _plan_line(report):
    plan = report.get("plan") or {}
    if not plan:
        return "—（没有计划管辖这个 campaign）"
    ap = report.get("plan_approval") or {}
    state = ("已批准" if ap.get("valid") else ("未批准" if not ap.get("human_approved")
                                              else f"批准失效：{ap.get('reason')}"))
    if ap.get("legacy"):
        state += "（legacy：无指纹）"
    by = ap.get("approved_by") or "—"
    return f"{plan.get('plan_id')} · {state} · 审批人 {by} · 目标：{plan.get('goal', '')}"


def render_markdown(report):
    c = report["campaign"]
    plan = report.get("plan") or {}
    queue = report["queue"]
    led = report["ledger"]
    out = report["outcomes"]
    ap = report.get("plan_approval") or {}

    lines = [f"# Campaign 报告 · {c}", ""]
    status_bits = []
    if led["committed"]:
        status_bits.append(f"已执行 {led['committed']} 条")
    if led["dry_run"]:
        status_bits.append(f"干跑 {led['dry_run']} 条")
    if out["events"]:
        status_bits.append(f"已记事件 {out['events']} 条")
    lines.append("**状态**：" + ("；".join(status_bits) if status_bits else "尚未执行")
                 + f"  \n生成于 {report['generated_at']}")
    lines += ["", "## 1. 定（计划与人审）", "", f"- {_plan_line(report)}"]
    if plan:
        icp = plan.get("icp") or {}
        lines.append(f"- ICP：{icp.get('org', '')} / {icp.get('persona', '')}；"
                     f"beachhead：{icp.get('beachhead', '')}")
        sc = plan.get("success_criteria") or {}
        lines.append(f"- 成功判据：{sc.get('metric')} {sc.get('target')}；停止：{sc.get('stop')}")
    if plan and ap.get("plan_sha256"):
        lines.append(f"- 计划指纹：`{ap['plan_sha256'][:16]}…`"
                     + (f"（有效期至 {ap['expires_at']}）" if ap.get("expires_at") else ""))

    lines += ["", "## 2. 跑（队列与账本）", ""]
    lines.append(f"- 队列：`{queue['path']}` · {queue['items']} 条"
                 if queue["exists"] else f"- 队列：`{queue['path']}`（尚未生成）")
    if queue["exists"]:
        bad = [r for r in queue["preflight"] if not r["ok"]]
        lines.append(f"- 人审预检：{queue['items'] - len(bad)}/{queue['items']} 条可发"
                     + (f"；{len(bad)} 条需修" if bad else ""))
        for r in bad[:10]:
            lines.append(f"  - `{r['id']}` {r['target']}：{r['reason']}")
    lines.append(f"- 账本：{led['records']} 条（committed {led['committed']} / "
                 f"dry-run {led['dry_run']} / 其他 {led['records'] - led['committed'] - led['dry_run']}）")

    lines += ["", "## 3. 量（结果台账）", ""]
    lines.append(f"- 事件：{out['events']} 条" + (f" · {out['by_type']}" if out["by_type"] else ""))
    lines.append(f"- 计数快照：{out['counts']} 条")
    if out["unattributed_global"]:
        lines.append(f"- 注意：全局还有 {out['unattributed_global']} 条事件没有 campaign 归属"
                     "（导入时加 --campaign 或补 campaign 列）")
    led_links = report.get("links") or []
    lines.append(f"- 追踪链接：{len(led_links)} 条"
                 + ("" if led_links else "（还没有；`mkt link` 生成带 utm 的链接）"))
    for l in led_links[:5]:
        lines.append(f"  - {l.get('channel')} · {l.get('tagged_url')}")

    lines += ["", "## 4. 实验与判断", ""]
    lines.append(f"- 实验：{_experiment_line(report.get('experiment'))}")
    nxt = report.get("next") or {}
    lines += ["", "## 5. 下一步", "", f"**{nxt.get('title', '')}**", ""]
    if nxt.get("why"):
        lines.append(f"理由：{nxt['why']}")
    if nxt.get("command"):
        lines += ["", "```bash", nxt["command"], "```"]
    lines += ["", "---", "",
              "口径说明：committed 是「发出去了」不是「转化了」；事件与触达分栏，"
              "不合成转化率。证据出处：`data/ledger.jsonl`、`data/outcomes/events.jsonl`、"
              "`data/approvals.jsonl`。"]
    return "\n".join(lines)


def review_markdown(data_dir, ledger, campaign):
    report = collect(data_dir, ledger, campaign)
    queue = report["queue"]
    lines = [f"# 人审预览 · {campaign}", "", f"- {_plan_line(report)}",
             f"- 队列：`{queue['path']}`", ""]
    if not queue["exists"]:
        lines.append("队列还没生成。先 `mkt queue build` 或写入 data/queues/<campaign>.json。")
        return "\n".join(lines)
    lines += ["| # | id | 目标 | 预检 | 说明 |", "|---|---|---|---|---|"]
    for i, r in enumerate(queue["preflight"], 1):
        lines.append(f"| {i} | `{r['id']}` | {r['target']} | "
                     f"{'PASS' if r['ok'] else 'REJECT'} | {r['reason']} |")
    dry = [r for r in ledger.results(campaign=campaign) if r.get("result") == "dry-run"]
    if dry:
        lines += ["", "## 已有干跑留证", ""]
        for r in dry[:20]:
            lines.append(f"- `{r.get('ref')}` {r.get('target')} — {r.get('evidence') or '无截图'}")
    ch = queue.get("channel") or "email"
    act = queue.get("action") or "message"
    lines += ["", "## 批准与执行", "",
              f"1. 批准：`mkt plan approve <plan_id> --by <你的名字>`（需先过 lint）",
              f"2. 干跑：`mkt {ch} {act} --queue {queue['path']}`",
              f"3. 实跑：同上加 `--go`（必须已有有效批准）"]
    return "\n".join(lines)


def next_step(data_dir, ledger, campaign=None):
    """状态机：返回 {code,title,command,why}。无 campaign 时取最近修改的计划。"""
    from .approval import list_plans
    from .planlint import governing_plan

    plan, plan_path = (None, None)
    if campaign:
        plan, plan_path = governing_plan(data_dir, campaign)
    if plan is None:
        plans = list_plans(data_dir)
        if plans:
            plans.sort(key=lambda x: x[1].stat().st_mtime, reverse=True)
            plan, plan_path = plans[0]
            wave_queue = ((plan.get("waves") or [{}])[0].get("queue") or "")
            campaign = campaign or (Path(wave_queue).stem if wave_queue
                                    else plan.get("plan_id", ""))
        else:
            return {"code": "plan-new", "title": "先写一份计划",
                    "command": "mkt plan new --id <plan_id> --goal '…' --beachhead '…' "
                               "--channel email --wave-action message --size 10",
                    "why": "没有计划就没有人审对象；内核只执行受计划管辖的动作。"}

    st = approval_status(plan)
    pid = plan.get("plan_id", "")
    if not st["valid"]:
        return {"code": "approve", "title": "计划待审批（或批准已失效）",
                "command": f"mkt plan approve {pid} --by <你的名字>",
                "why": st.get("reason") or "人审是执行前置条件。"}

    queue, qpath = load_campaign_queue(data_dir, plan, campaign)
    if queue is None:
        w = (plan.get("waves") or [{}])[0]
        return {"code": "queue-missing", "title": "计划已批，队列还没生成",
                "command": f"mkt queue build --campaign {campaign or pid} "
                           f"--channel {w.get('channel', 'email')} "
                           f"--wave-action {w.get('action', 'message')} "
                           f"--template <模板.md> --targets <目标.csv>",
                "why": "队列是内核唯一认识的执行单位；先有队列才能干跑。"}
    errs = lint_queue(queue, path=qpath)
    if errs:
        return {"code": "queue-lint", "title": f"队列有 {len(errs)} 处需修",
                "command": f"mkt queue lint {qpath}",
                "why": errs[0]}

    records = ledger.results(campaign=campaign)
    results = [r.get("result") for r in records]
    ch = queue.get("channel") or "email"
    act = queue.get("action") or "message"
    if not records:
        return {"code": "dry-run", "title": "先干跑一遍，看截图再决定",
                "command": f"mkt {ch} {act} --queue {qpath}",
                "why": "写路径默认 dry-run：门禁、回读、留证都走一遍但不发送。"}
    if results.count("dry-run") and "committed" not in results:
        return {"code": "go", "title": "干跑已留证，可以人审后实跑",
                "command": f"mkt review {campaign}", "why": "先看人审预览，再在同一队列加 --go。"}
    if "committed" in results:
        from .attribution import OutcomeLedger
        ol = OutcomeLedger(data_dir)
        events = [r for r in ol.records(campaign) if r.get("kind") == "event"]
        if not events:
            return {"code": "capture", "title": "已发出，下一步把结果记回来",
                    "command": f"mkt link --campaign {campaign} --channel <渠道> --url <落地页> "
                               f"&& mkt outcome import <导出.csv> --campaign {campaign}",
                    "why": "没有结果事件，这轮 campaign 无法判断有效与否。"}
        exp = None
        for f in sorted((Path(data_dir) / "experiments").glob("*.json")):
            try:
                e = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if e.get("campaign") == campaign:
                exp = e
                break
        if exp is None:
            return {"code": "experiment-new", "title": "建一个预注册实验再继续投放",
                    "command": f"mkt experiment new --id {campaign} --hypothesis '…' "
                               f"--metric signup --rule '≥3 个注册为 met' "
                               f"--start {datetime.now().date()} --end <日期>",
                    "why": "没有预注册判据，后面的数字只能当故事，不能当结论。"}
        if exp.get("status") == "running":
            return {"code": "observe", "title": "实验运行中，补观察值",
                    "command": f"mkt experiment obs --id {exp['exp_id']} --from-outcomes",
                    "why": "观察只在 running 期接受；自动聚合避免手抄。"}
    return {"code": "report", "title": "可以出这一轮的报告了",
            "command": f"mkt campaign report {campaign}",
            "why": "把计划、执行、结果、实验合成一页，作为继续/停止/加码的依据。"}
