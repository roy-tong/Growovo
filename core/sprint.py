#!/usr/bin/env python3
"""冲刺引擎：把一个目标方案压缩到 N 天，排成并行工作流和小时级动作。

六周计划回答「怎么稳稳地做」；冲刺回答「如果只有 3 天，先做什么、同时做什么、
什么必须砍掉」。本模块只做排期和约束检查，不发送、不写平台、不替人判断：

- 一个资产，多个表面：同一份技术拆解拆成 HN / Reddit / X / LinkedIn / 社区；
- 借场优先：先去人群已经在的地方，不指望自己的粉丝；
- 并行直连：把 15 条私信压到 3 天，而不是 6 周；
- 时限稀缺：只给前几个愿意给数据的人做免费样本，不做无限免费；
- 容量校验：平台日限、人工小时、依赖关系，超了就标出来；
- 止损预置：2 小时、24 小时、48 小时三个检查点，到点就换，不硬撑。

冲刺是信号测试，不是市场验证。目标达成只说明这 3 天的动作有效，
不证明需求、付费或可规模化。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional

MIN_DAYS, MAX_DAYS = 1, 14
DEFAULT_LAUNCH_HOUR = "21:00"  # 北京时间；美东 08:00（冬令时）≈ 北京 21:00

# 保守的平台日限：不是平台上限，是「不把账号打坏」的上限。
LIMITS: Dict[str, Dict[str, int]] = {
    "hackernews": {"post": 1, "comment": 10},
    "reddit": {"post": 2, "comment": 10},
    "x": {"post": 3, "reply": 20},
    "linkedin": {"post": 1, "invite": 15, "message": 20},
    "github": {"issue": 3, "comment": 10},
    "email": {"message": 5},
}

RED_LINES = [
    "不买星、不互投、不进投票群；发现一次就停这一轮",
    "不群发、不批量私信、不用自动化工具代发",
    "不在禁止推广的社区发产品帖；先读版规，再决定发不发",
    "不伪造数据、不编客户名、不把公开样例说成真实账期",
    "不承诺追回、节省比例或 ROI；样本只给差异，不给结论",
    "客户数据本地处理、只读、脱敏、交付后删除；不进公开语料",
]

LEVERS = [
    {"name": "借场", "what": "去人群已经在的地方发，不指望自己的粉丝",
     "why": "3 天养不出受众，只能借现成的", "risk": "版规/社区反感；先读规则、先答问题"},
    {"name": "一鱼多吃", "what": "一份技术拆解拆成 HN/Reddit/X/LinkedIn/清单",
     "why": "内容生产是瓶颈，复用是唯一解", "risk": "重复感；每个表面换开头和例子"},
    {"name": "并行直连", "what": "30 条具名私信压到 3 天，不排队",
     "why": "回复率不会因为等而变高", "risk": "像群发；每条必须引用对方原话"},
    {"name": "时限稀缺", "what": "前 5 个给数据的人做免费 24h 样本",
     "why": "给一个现在做的理由", "risk": "白嫖；先给范围，再要数据"},
    {"name": "存量关系", "what": "9 个已说过话的人 + 2 个老客户，先请他们看/转",
     "why": "冷启动最短的路是已经认识你的人", "risk": "消耗关系；只请一次，不催"},
    {"name": "公开构建", "what": "每天发一次真实数字（star/数据/回话）",
     "why": "第二天和第三天还能再触达一次", "risk": "自嗨；只发数字和学到的东西"},
]

CONTINGENCIES = [
    {"trigger": "发射后 2 小时，HN < 20 分或 0 评论",
     "action": "把同一篇内容换开头发 Reddit/X，不重复发 HN；HN 只回评论，不重发"},
    {"trigger": "第 2 天 12:00，真实数据仍为 0",
     "action": "把「给我数据」改成「我帮你跑，你只给脱敏导出」；再加 10 条私信"},
    {"trigger": "第 2 天结束，star 已 +30",
     "action": "停止开新表面，把第 3 天全部压到有效表面；不庆祝，先复现一次"},
    {"trigger": "任何平台删帖、限流或警告",
     "action": "停该平台 24 小时，只做评论/回答；不换号、不申诉轰炸"},
]


class SprintError(ValueError):
    """冲刺排期不成立。"""


def _parse_hour(value: str) -> time:
    try:
        hh, mm = str(value).split(":")[:2]
        return time(int(hh), int(mm))
    except (TypeError, ValueError):
        raise SprintError(f"时间格式应为 HH:MM：{value}")


def _front_weights(days: int, lag: int = 0) -> List[float]:
    if days == 1:
        return [1.0]
    if days == 2:
        weights = [0.6, 0.4]
    else:
        tail = 0.2 / (days - 2)
        weights = [0.45, 0.35] + [tail] * (days - 2)
    if lag > 0:
        shifted = [0.0] * days
        for i, w in enumerate(weights):
            j = min(i + lag, days - 1)
            shifted[j] += w
        total = sum(shifted) or 1.0
        weights = [w / total for w in shifted]
    return weights


def _split_target(value: float, days: int, lag: int = 0) -> List[int]:
    weights = _front_weights(days, lag)
    raw = [value * w for w in weights]
    out = [int(round(x)) for x in raw]
    # 保证总和不少于目标：把差额补到最后一个非零日
    diff = int(round(value)) - sum(out)
    if diff:
        idx = max((i for i, x in enumerate(out) if x > 0), default=days - 1)
        out[idx] += diff
        if out[idx] < 0:
            out[idx] = 0
    return out


def _daily_targets(goal: Dict[str, Any], secondary: Optional[Dict[str, Any]],
                   days: int) -> List[Dict[str, Any]]:
    primary = _split_target(float(goal.get("target") or 0), days, lag=0)
    secondary_counts = ([0] * days if not secondary
                        else _split_target(float(secondary.get("target") or 0),
                                           days, lag=1))
    rows = []
    for i in range(days):
        rows.append({
            "day": i + 1,
            "primary": primary[i],
            "secondary": secondary_counts[i],
        })
    return rows


def _blocks(days: int, start: date, launch_hour: str) -> List[Dict[str, Any]]:
    """小时级模板；第 4 天以后重复第 2 天的节奏。"""
    launch = _parse_hour(launch_hour)
    tpl: List[Dict[str, Any]] = [
        # day, start, end, workstream, task, owner, accept
        (1, "08:00", "10:00", "资产", "定稿核心资产：一篇技术拆解 + 一个可复现样例 + 一页清单", "human", "资产链接 + 一条可复现命令"),
        (1, "10:00", "11:00", "资产", "发布到仓库/官网，自己从头跑一遍", "human", "发布记录 + 运行截图"),
        (1, "14:00", "15:00", "借场", "确认 3 个社区和 2 个群组的发帖规则与窗口", "human", "规则清单：能不能发、发在哪、什么时候"),
        (1, "20:30", "21:00", "发射", "预热检查：标题、首评、链接、UTM、错别字", "human", "检查表全打勾"),
        (1, launch.strftime("%H:%M"), (datetime.combine(start, launch) + timedelta(minutes=30)).strftime("%H:%M"),
         "发射", "在 Hacker News 发一次（Show HN），自己写首条作者评论", "human", "发布链接 + 首评"),
        (1, "21:30", "22:00", "放大", "X 线程 + LinkedIn 帖，同一份资产换开头", "human", "2 条发布记录"),
        (1, "22:00", "22:30", "放大", "Reddit 发帖或答 3 个相关帖（按版规，先答后提）", "human", "1 帖或 3 条回答"),
        (1, "22:30", "23:30", "直连", "10 条具名私信：引用原话 + 一个真问题 + 免费样本", "human", "10 条发出记录"),
        (1, "23:30", "00:30", "承接", "回完 HN/Reddit 评论，记下高频问题", "human", "评论清零 + 问题清单"),
        (2, "00:30", "01:30", "承接", "第二轮评论；把新问题补进 FAQ", "human", "FAQ diff"),
        (2, "09:00", "10:00", "直连", "10 条私信 + 昨日未回跟进", "human", "发出/跟进记录"),
        (2, "14:00", "15:00", "借场", "2 个社区/群组发价值帖：只给方法，不带产品链接", "human", "2 条记录"),
        (2, "20:00", "21:00", "放大", "X 第二条：一个具体差异/案例；LinkedIn 回评论", "human", "1 条发布 + 评论记录"),
        (2, "21:00", "22:00", "承接", "回评论 + 5 条私信", "human", "记录"),
        (2, "22:00", "23:30", "交付", "收到数据的样本：24 小时内出 3 页差异报告", "human", "样本包 + 读回"),
        (3, "09:00", "10:00", "直连", "10 条私信 + 全部跟进；只问一个真问题", "human", "记录"),
        (3, "14:00", "15:00", "放大", "发「48 小时学到什么」：只发数字和教训，不吹", "human", "1 条发布"),
        (3, "20:00", "21:00", "收口", "最后一轮评论/私信；把愿意给数据的人拉进 24h 交付", "human", "名单 + 时间承诺"),
        (3, "21:00", "23:00", "判定", "判定：记四个数 star、真实数据、回话、样本；按判据三选一", "machine", "一页判定 + 下一轮动作"),
    ]
    out: List[Dict[str, Any]] = []
    for day, start_s, end_s, ws, task, owner, accept in tpl:
        if day > days and day > 3:
            continue
        if day > days:
            continue
        d = start + timedelta(days=day - 1)
        out.append({
            "day": day,
            "date": d.isoformat(),
            "start": start_s,
            "end": end_s,
            "workstream": ws,
            "task": task,
            "owner": owner,
            "accept": accept,
        })
    # 超过 3 天：把第 2 天的节奏复制到第 4..N 天，改日期和文案
    if days > 3:
        base = [b for b in out if b["day"] == 2]
        for day in range(4, days + 1):
            d = start + timedelta(days=day - 1)
            for b in base:
                item = dict(b)
                item["day"] = day
                item["date"] = d.isoformat()
                item["task"] = b["task"] + "（第 %d 天：只做有效表面，不开新的）" % day
                out.append(item)
    out.sort(key=lambda b: (b["day"], b["start"]))
    return out


def _minutes_between(start_s: str, end_s: str) -> int:
    def mins(value: str) -> int:
        hh, mm = value.split(":")[:2]
        return int(hh) * 60 + int(mm)
    delta = mins(end_s) - mins(start_s)
    return delta if delta > 0 else delta + 24 * 60


def _capacity(goal_plan: Dict[str, Any], days: int) -> Dict[str, Any]:
    planned = {"hackernews": {"post": 1, "comment": 5},
               "reddit": {"post": 1, "comment": 5},
               "x": {"post": 2, "reply": 5},
               "linkedin": {"post": 1, "message": 10},
               "github": {"issue": 1, "comment": 3}}
    per_day = {k: {a: max(1, round(v / days)) if v else 0 for a, v in d.items()}
               for k, d in planned.items()}
    rows = []
    over = []
    for platform, actions in planned.items():
        limits = LIMITS.get(platform, {})
        for action, total in actions.items():
            limit = limits.get(action)
            if limit is None:
                continue
            used = per_day[platform][action]
            ok = used <= limit
            rows.append({"platform": platform, "action": action,
                         "per_day": used, "limit": limit, "ok": ok})
            if not ok:
                over.append(f"{platform}/{action}: {used}/天 > 上限 {limit}")
    return {"rows": rows, "over_limit": over}


def build(goal_plan: Dict[str, Any], days: int = 3,
          start: Optional[str] = None, hours_per_day: float = 8.0,
          launch_hour: str = DEFAULT_LAUNCH_HOUR) -> Dict[str, Any]:
    """把一个目标方案压成 N 天冲刺；只读，不写文件、不发消息。"""
    days = int(days)
    if days < MIN_DAYS or days > MAX_DAYS:
        raise SprintError(f"冲刺天数必须在 {MIN_DAYS}–{MAX_DAYS} 天之间")
    goal = goal_plan.get("goal") or {}
    secondary = goal_plan.get("secondary")
    if not goal:
        raise SprintError("缺少目标：先跑 mkt goal plan")
    try:
        start_d = date.fromisoformat(start) if start else date.today()
    except ValueError:
        raise SprintError(f"开始日期应为 YYYY-MM-DD：{start}")
    _parse_hour(launch_hour)

    daily = _daily_targets(goal, secondary, days)
    blocks = _blocks(days, start_d, launch_hour)
    human_minutes = sum(_minutes_between(b["start"], b["end"]) for b in blocks
                        if b["owner"] == "human")
    capacity = _capacity(goal_plan, days)
    budget = hours_per_day * 60 * days
    over_capacity = human_minutes > budget
    platforms = goal_plan.get("platforms") or {}
    platform_names = []
    if platforms.get("primary"):
        platform_names.append(platforms["primary"]["name"])
    if platforms.get("launch"):
        platform_names.append(platforms["launch"]["name"])
    platform_names.extend(r["name"] for r in platforms.get("support") or [])

    return {
        "schema": "growovo.sprint-plan/1",
        "product": (goal_plan.get("product") or {}).get("name", ""),
        "goal": goal,
        "secondary": secondary,
        "days": days,
        "starts": start_d.isoformat(),
        "ends": (start_d + timedelta(days=days - 1)).isoformat(),
        "launch_hour": launch_hour,
        "timezone_note": "时间为北京时间；美东冬令时 08:00 ≈ 北京 21:00",
        "platforms": platform_names,
        "daily_targets": daily,
        "workstreams": [
            {"name": "资产", "goal": "一份可复用的核心资产", "why": "没有资产，后面所有动作都是空转"},
            {"name": "发射", "goal": "一次集中曝光", "why": "3 天只有一次发射窗口"},
            {"name": "放大", "goal": "同一资产的第二、第三个表面", "why": "单平台不够，换表面不换内容"},
            {"name": "直连", "goal": "30 条具名私信", "why": "回复率不会因为等而变高"},
            {"name": "承接", "goal": "评论、私信、样本 24h 内回", "why": "错过 24h，热度就凉了"},
            {"name": "判定", "goal": "第 72 小时三选一", "why": "不留「再看看」"},
        ],
        "blocks": blocks,
        "levers": LEVERS,
        "contingencies": CONTINGENCIES,
        "capacity": capacity,
        "red_lines": RED_LINES,
        "cost": {"human_minutes": human_minutes,
                 "budget_minutes": int(budget),
                 "over_capacity": over_capacity,
                 "cash": 0},
        "boundary": ("3 天冲刺是信号测试，不是市场验证。目标达成只说明这 3 天的"
                     "资产、表面和话术有效；不证明需求、付费或可规模化。"),
    }


def render(plan: Dict[str, Any]) -> str:
    from .narrative import table
    g = plan["goal"]
    sec = plan.get("secondary")
    lines = [f"# {plan['days']} 天冲刺 · {g['name']}"
             + (f" + {sec['name']}" if sec else ""), ""]
    lines.append(f"**{plan['starts']} → {plan['ends']}。** 这一轮不是把六周计划压扁，"
                 f"是换一套打法：一个资产、多个表面、并行直连、到点判定。")
    lines.append("")
    lines.append(f"> {plan['timezone_note']}")
    lines.append("")
    lines.append("## 每天追什么")
    lines.append("")
    rows = []
    for d in plan["daily_targets"]:
        rows.append([f"第 {d['day']} 天",
                     g["metric_label"], f"+{d['primary']}",
                     (sec["metric_label"] if sec else "—"),
                     (f"+{d['secondary']}" if sec else "—")])
    lines.append(table(["天", "主指标", "当天", "辅指标", "当天"], rows))
    lines.append("")
    lines.append("## 并行工作流")
    lines.append("")
    lines.append(table(["工作流", "目标", "为什么"],
                       [[w["name"], w["goal"], w["why"]] for w in plan["workstreams"]]))
    lines.append("")
    lines.append("## 小时级排期")
    lines.append("")
    lines.append(table(["天", "时间", "工作流", "做什么", "谁", "验收物"],
                       [[f"D{b['day']}", f"{b['start']}–{b['end']}", b["workstream"],
                         b["task"], "人" if b["owner"] == "human" else "机器",
                         b["accept"]] for b in plan["blocks"]]))
    lines.append("")
    lines.append("## 加速杠杆")
    lines.append("")
    lines.append(table(["杠杆", "怎么做", "为什么", "风险"],
                       [[x["name"], x["what"], x["why"], x["risk"]]
                        for x in plan["levers"]]))
    lines.append("")
    lines.append("## 到点就换")
    lines.append("")
    lines.append(table(["触发", "动作"],
                       [[x["trigger"], x["action"]] for x in plan["contingencies"]]))
    lines.append("")
    lines.append("## 容量校验")
    lines.append("")
    lines.append(table(["平台", "动作", "每天", "上限", "可以吗"],
                       [[r["platform"], r["action"], r["per_day"], r["limit"],
                         "可以" if r["ok"] else "**超限**"]
                        for r in plan["capacity"]["rows"]]))
    lines.append("")
    if plan["capacity"]["over_limit"]:
        lines.append("**超限项**：" + "；".join(plan["capacity"]["over_limit"]))
        lines.append("")
    cost = plan["cost"]
    lines.append(f"人工：约 {cost['human_minutes']} 分钟；预算 "
                 f"{cost['budget_minutes']} 分钟"
                 + ("，**超预算**" if cost["over_capacity"] else "，在预算内") + "。")
    lines.append("")
    lines.append("## 红线")
    lines.append("")
    for rule in plan["red_lines"]:
        lines.append(f"- {rule}")
    lines.append("")
    lines.append("## 72 小时后怎么判")
    lines.append("")
    lines.append(table(["结果", "判定", "下一步"],
                       [["主目标和辅目标都达成", "这条资产/表面/话术成立", "原样复现一次，再谈加量"],
                        ["只达成一个", "不算成功；看是资产问题还是表面问题", "换表面或换话术，不加量"],
                        ["两个都没动", "信号不成立", "停，回到目标选择，不硬撑"]]))
    lines.append("")
    lines.append(f"> {plan['boundary']}")
    lines.append("")
    return "\n".join(lines)
