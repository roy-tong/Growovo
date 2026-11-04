#!/usr/bin/env python3
"""执行桥：把「这一轮怎么打」落成内核能跑的计划与队列。

第 4 步以前是断的：策略写在一份文档，执行要人工另建 queue。本模块把两者接上——
输入策略 + 目标名单，输出 plan.json 和 queue.json，两者都必须过现有门禁
（planlint / lint_queue），过不了就不写盘。

仍然守住老规矩：这里只**生成**，不发送。发送要人审 + --go，走 action_runner。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

# 策略里的渠道 id → 内核渠道与动作
from .platforms import WRITE as _WRITE

# 有写通道的用队列执行；没有的只能出人工执行单，不假装能自动。
CHANNEL_ACTION = dict(_WRITE)


class ExecuteError(Exception):
    pass


def resolve_channel(strategy: Dict[str, Any]):
    """返回写通道；没有就返回 None——不是错误，是这类平台只能人工发。"""
    pid = (strategy.get("primary") or {}).get("id", "")
    return CHANNEL_ACTION.get(pid)


def can_auto(strategy: Dict[str, Any]) -> bool:
    return resolve_channel(strategy) is not None


def build(strategy: Dict[str, Any], targets: List[Dict[str, str]], *,
          campaign: str, goal: str = "", hypothesis: str = "",
          criteria: Optional[Dict[str, str]] = None,
          data_dir, today: Optional[str] = None,
          account: Optional[str] = None, workspace: Optional[str] = None) -> Dict[str, Any]:
    """生成 plan + queue 并落盘。targets 每项至少要有 target 与 body。"""
    pair = resolve_channel(strategy)
    if pair is None:
        return build_manual(strategy, targets, campaign=campaign, data_dir=data_dir,
                            goal=goal, criteria=criteria, today=today)
    channel, action = pair
    if not targets:
        raise ExecuteError("名单为空：没有目标就没有队列，先跑 mkt strategy cadence 那一步")

    seen: Dict[str, Dict[str, str]] = {}
    items: List[Dict[str, str]] = []
    for i, t in enumerate(targets, 1):
        tgt = (t.get("target") or "").strip()
        body = (t.get("body") or "").strip()
        if not tgt or not body:
            raise ExecuteError(f"第 {i} 个目标缺 target 或 body（不许空发）")
        if tgt in seen:
            raise ExecuteError(f"目标重复：{tgt}（同一队列不要重复触达）")
        seen[tgt] = t
        item = {"id": f"{campaign}-{i:03d}", "target": tgt, "body": body}
        if t.get("subject"):
            item["subject"] = t["subject"]
        items.append(item)

    data_dir = Path(data_dir)
    # 队列文件名必须等于 campaign：人审按 campaign 管辖，对不上会找不到计划。
    queue_path = data_dir / "queues" / f"{campaign}.json"
    plan_path = data_dir / "plans" / f"{campaign}.json"
    queue = {"campaign": campaign, "channel": channel, "action": action, "items": items}

    from core.queue import lint_queue
    errs = lint_queue(queue, path=queue_path)
    if errs:
        raise ExecuteError("队列没过门禁：" + "；".join(errs))

    criteria = criteria or {"metric": "observation",
                            "note": "只记录真实结果，不设产品生死门；可选学习实验另行登记"}
    plan = {
        "plan_id": campaign,
        "goal": goal or f"在 {strategy['primary']['name']} 验证一次真实增长动作及后续结果",
        "created": today or date.today().isoformat(),
        "hypothesis": hypothesis or (
            "与目标用户当前问题直接相关的价值资产，更可能带来有效互动和后续使用"),
        "icp": {"org": "与产品价值匹配的目标用户",
                "persona": "当前遇到产品可解决问题的人",
                "beachhead": f"{strategy['primary']['name']} 上具有明确需求信号的用户"},
        "audience": {"spec": "由产品事实与需求信号确定对象，逐条核对当前问题与价值匹配",
                     "target_count": len(items),
                     "evidence_grade": "B"},
        "waves": [{"wave": 1, "channel": channel, "action": action,
                   "size": len(items), "content_policy": "千人千面/禁模板",
                   "queue": str(queue_path)}],
        "success_criteria": criteria,
        "account": account or strategy.get("account") or "default",
        "workspace": workspace or str(Path(data_dir).resolve()),
        "review": {"human_approved": False, "approved_at": None},
    }

    from core.planlint import lint_plan
    perrs = lint_plan(plan)
    if perrs:
        raise ExecuteError("计划没过门禁：" + "；".join(perrs))

    queue_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")

    return {"plan_id": campaign, "channel": channel, "action": action,
            "items": len(items), "plan": str(plan_path), "queue": str(queue_path),
            "next": [f"mkt review {campaign}",
                     f"mkt plan approve {campaign} --by <你的名字>",
                     f"mkt queue run {queue_path}（默认 dry-run，加 --go 才发）"]}


def build_manual(strategy: Dict[str, Any], targets: List[Dict[str, str]], *,
                 campaign: str, data_dir, goal: str = "",
                 criteria: Optional[Dict[str, str]] = None,
                 today: Optional[str] = None) -> Dict[str, Any]:
    """没有写通道的平台：产出人工作业单。

    这类平台（小红书、知乎、即刻、视频号…）内核发不了，但有单子就能照做。
    单子里必须写明：在哪做、做什么、什么算做完、什么绝对不能做。
    """
    from .platforms import BY_ID
    pid = (strategy.get("primary") or {}).get("id", "")
    spec = BY_ID.get(pid, {})
    name = spec.get("name", pid)
    if not targets:
        raise ExecuteError("名单为空：没有目标就没有作业单")

    lines = [f"# 人工作业单 · {name}", "",
             f"这个渠道没有可自动调用的写接口（或以规则禁止自动化），所以内核只出单，你手工执行。",
             "", f"目标：{goal or '把这一轮做完并拿到「被问到」'}",
             f"名单：{len(targets)} 条", ""]
    lines.append("## 开跑前确认")
    for a in spec.get("account", []):
        lines.append(f"- [ ] {a}")
    lines.append(f"- [ ] 内容档位：{spec.get('tier', 'A 人写人发')}")
    if spec.get("residency"):
        lines.append(f"- [ ] 养号期 {spec['residency']} 天：期间只做贡献，不提产品")
    from . import voice
    v = voice.profile(pid)
    lines.append("")
    lines.append("## 这个渠道怎么说话")
    lines.append("")
    lines.append(f"- 语气：{v['tone']}")
    lines.append(f"- 句式：{v['sentences']}")
    lines.append(f"- 长度：{v['length']}")
    lines.append("")
    lines.append("开头可以这样写：")
    for o in v["openers"][:3]:
        lines.append(f"- {o}")
    lines.append("")
    lines.append("别这么写：" + "；".join(v["taboos"]))
    lines.append("")
    from . import experience
    checks = experience.as_prompt_lines(pid)
    if checks:
        lines.append("## 发之前先过这几条（用户经验，不是官方规则）")
        lines.append("")
        for c in checks:
            lines.append(f"- [ ] {c}")
        lines.append("")
    lines.append("## 逐条执行")
    lines.append("")
    lines.append("| # | 对象 | 发什么 | 什么算做完 |")
    lines.append("|---|---|---|---|")
    problems: List[str] = []
    for i, t in enumerate(targets, 1):
        body = (t.get("body") or "").strip().replace("|", "/")
        lines.append(f"| {i} | {t.get('target', '')} | {body[:60]} | 发出去并把链接记进台账 |")
        for p in voice.lint(pid, body):
            problems.append(f"第 {i} 条：{p}")
    lines.append("")
    if spec.get("risks"):
        lines.append("## 绝对不能做")
        for detect, signal, adjust in spec["risks"]:
            lines.append(f"- {adjust}（触发信号：{signal}）")
        lines.append("")

    if problems:
        lines.append("")
        lines.append("## 会被当成广告的地方（先改再发）")
        for p in problems[:8]:
            lines.append(f"- {p}")
        lines.append("")

    out = Path(data_dir) / "manual" / f"{campaign}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return {"manual": True, "plan_id": campaign, "channel": pid, "action": "manual",
            "items": len(targets), "plan": None, "queue": None, "sheet": str(out),
            "next": [f"按 {out.name} 逐条执行", "发完把结果记进 mkt followup"]}


def render(res: Dict[str, Any]) -> str:
    if res.get("manual"):
        return "\n".join([
            "# 人工执行单已生成", "",
            f"渠道 {res['channel']} 没有可自动调用的写接口，出了人工作业单：{res['items']} 条。",
            "", f"- 作业单：{res['sheet']}", "",
            "## 还要人做的", "",
            f"1. 打开作业单，逐条执行",
            f"2. 发完立刻 `mkt followup add ...` 记进台账，否则又会跟丢",
            "", "> 这类渠道不能自动化，强行自动化就是封号。",
        ])
    lines = ["# 执行已就绪", ""]
    lines.append(f"计划 `{res['plan_id']}`：{res['items']} 条，渠道 {res['channel']}，动作 {res['action']}。")
    lines.append("")
    lines.append(f"- 计划：{res['plan']}")
    lines.append(f"- 队列：{res['queue']}")
    lines.append("")
    lines.append("## 还要人做的三步")
    lines.append("")
    for i, n in enumerate(res["next"], 1):
        lines.append(f"{i}. `{n}`")
    lines.append("")
    lines.append("> 生成不等于发送。人审、审批、dry-run 之后才允许 --go。")
    return "\n".join(lines)
