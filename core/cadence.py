#!/usr/bin/env python3
"""落地设计：把「这一轮打哪条渠道」变成「这四周每天做什么、谁做、什么算做完」。

策略回答方向，本模块回答动作。生成的是**可执行的任务表**，每条带：
  day       第几天
  what      做什么（动词开头）
  who       machine / human
  minutes   预计人工分钟
  accept    验收物（没有验收物就不算做完）
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional


# 第一周按渠道类型给不同动作：内容型要先有内容，搜索型要先有页面。
FIRST_WEEK_BY_KIND = {
    "内容": ["先发 3 篇纯分享（不提产品），把账号养起来",
             "翻同题目前 20 篇的评论区，记下高频问题当选题"],
    "视频": ["定 3 个选题，先做完 1 条 5 分钟以内的教程",
             "把教程切成 3 条 30 秒短片，留作后续分发"],
    "搜索": ["梳理 5 个用户会搜的问题，每个写一页能直接抄走的答案",
             "把现有文档里最常被问的三处补成独立页"],
    "目录": ["收录产品页并认领，补齐截图与定价",
             "找 3 个真实客户，请他们各写一条评价（不设奖励）"],
    "社媒": ["先评论三周，再发第一条原创",
             "把主页做成能承接的：一句话定位 + 一个入口"],
    "社区": ["读目标社区的规则与置顶，记下三条会踩雷的",
             "先答 10 个真问题，一条产品都不提"],
    "发射": ["把物料备齐：一句话定位、演示、三张真实截图",
             "定好发布窗口（当地时间 + 北京时间）"],
    "直连": ["把名单按「他最近在做什么」筛一遍，剔除对不上的",
             "写三版开头，删掉所有客套"],
}


def _d(start: str, offset: int) -> str:
    return (date.fromisoformat(start) + timedelta(days=offset)).isoformat()


def build(strategy: Dict[str, Any], start: str, actions_per_week: int = 20,
          goal_plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """按四周切：资格周 / 触达周 / 跟进周 / 判定周。

    给了 goal_plan 时，前两周直接用目标方案里的动作，不再套「每周 20 条」的
    通用模板；后两周按目标族补跟进/样本/报价/复盘。
    """
    if goal_plan and goal_plan.get("actions"):
        return _build_from_goal(strategy, start, goal_plan)
    primary = strategy["primary"]
    res = int(primary.get("residency") or 0)
    half = max(actions_per_week // 2, 5)

    weeks: List[Dict[str, Any]] = []

    # 第 1 周：资格 + 名单
    kind = (strategy.get("primary") or {}).get("kind", "社区")
    w1 = [
        {"day": _d(start, 0), "what": "补齐账号条件（逐条对照开跑前提）",
         "who": "human", "minutes": 20, "accept": "开跑前提清单全部打勾"},
        {"day": _d(start, 1), "what": f"整理名单：{actions_per_week} 个目标对象，每人写一句他最近在做什么",
         "who": "machine", "minutes": 30, "accept": "名单表有 20 行，每行有人名、来源、一句话"},
        {"day": _d(start, 3), "what": "写第一条消息的三个版本，自己读一遍删掉所有客套",
         "who": "human", "minutes": 40, "accept": "三条草稿，每条都有一个真问题结尾"},
    ]
    for i, extra in enumerate(FIRST_WEEK_BY_KIND.get(kind, [])[:2]):
        w1.append({"day": _d(start, 2 + i), "what": extra, "who": "human",
                   "minutes": 40, "accept": "做完并留下可回看的记录"})
    if res > 0:
        w1.append({"day": _d(start, 4), "what": f"进入养号期（{res} 天）：只答问题，不提产品",
                   "who": "human", "minutes": 25, "accept": "每天 2 条有效回答，连续 3 天"})
    weeks.append({"week": 1, "focus": "资格与名单", "tasks": w1})

    # 第 2–3 周：触达
    for wk in (2, 3):
        tasks = []
        for i in range(2):
            off = (wk - 1) * 7 + i * 3
            tasks.append({"day": _d(start, off),
                          "what": f"发 {half} 条针对性消息（给东西 → 再说自己 → 一个真问题）",
                          "who": "human", "minutes": 40,
                          "accept": f"{half} 条发出记录，含平台、时间、对象"})
        tasks.append({"day": _d(start, (wk - 1) * 7 + 6),
                      "what": "回完本周所有回应，把要跟进的人记进台账",
                      "who": "human", "minutes": 30,
                      "accept": "台账新增记录数 = 本周回话人数"})
        weeks.append({"week": wk, "focus": "触达", "tasks": tasks})

    # 第 4 周：跟进 + 复盘与下一轮（不设固定产品判决日）
    w4 = [
        {"day": _d(start, 21), "what": "把第 3 天到期的跟进全部做完（不新增触达）",
         "who": "human", "minutes": 30, "accept": "到期台账清零"},
        {"day": _d(start, 24), "what": "统计四个数：发出、被删、回话、转一对一",
         "who": "machine", "minutes": 5, "accept": "四条数字与台账一致"},
        {"day": _d(start, 27), "what": "复盘本轮结果，确定下一轮改哪个参数或补哪条证据",
         "who": "human", "minutes": 20, "accept": "参数 diff 或补证任务写进复盘，附触发数据"},
    ]
    weeks.append({"week": 4, "focus": "跟进与下一轮", "tasks": w4})

    for w in weeks:
        w["tasks"].sort(key=lambda t: t["day"])
    total = sum(t["minutes"] for w in weeks for t in w["tasks"])
    return {
        "primary": primary["name"],
        "start": start,
        "end": _d(start, 27),
        "weeks": weeks,
        "total_human_minutes": total,
        "actions_planned": actions_per_week * 3,
    }


def _build_from_goal(strategy: Dict[str, Any], start: str,
                     goal_plan: Dict[str, Any]) -> Dict[str, Any]:
    start_d = date.fromisoformat(start)
    actions = goal_plan.get("actions") or []
    base = date.fromisoformat(actions[0]["day"]) if actions else start_d
    weeks: Dict[int, List[Dict[str, Any]]] = {1: [], 2: [], 3: [], 4: []}
    for a in actions:
        off = max((date.fromisoformat(a["day"]) - base).days, 0)
        wk = 1 if off < 7 else 2
        weeks[wk].append({
            "day": (start_d + timedelta(days=off)).isoformat(),
            "what": a["what"], "who": a["who"],
            "minutes": a["minutes"], "accept": a["accept"],
        })

    family = ((goal_plan.get("goal") or {}).get("family") or "")
    if family == "commercial":
        w3 = [
            ("跟进第 1 个样本的读回；前两周没做成，就补 1 个（≤3 小时），最多 2 个",
             "human", 120, "读回记录；或 1 份新样本范围 + 数据清单"),
            ("对已报价的人做一次跟进；还没有报价，就开口一次（只一次）",
             "human", 45, "跟进或报价记录"),
            ("更新报价台账：谁报了、谁拒了、为什么", "machine", 10, "报价台账，每条带原因"),
        ]
    elif family == "adoption":
        w3 = [
            ("跟进 3 个用户的卡点，只修最高频的那一个", "human", 90, "修复记录 + 频次"),
            ("把可复现样例放进 README 首屏，删掉解释性长文", "human", 45, "README diff"),
            ("记本周四个数：clone/fork/issue/真实数据", "machine", 10, "四个数入账"),
        ]
    elif family == "awareness":
        w3 = [
            ("把首篇拆成 3 条短内容，分发到辅助平台，不改主线", "human", 90, "3 条发布记录"),
            ("回完本周所有评论，把高频问题记成下一篇选题", "human", 45, "评论清空 + 选题清单"),
            ("记本周四个数：访问/clone/star/外部 issue", "machine", 10, "四个数入账"),
        ]
    else:
        w3 = [
            ("给 1 个上期客户提下期范围与价格", "human", 45, "报价记录"),
            ("问一次转介绍：还有谁在为同一件事头疼", "human", 30, "1 条转介绍或明确没有"),
            ("记续约/复购信号：谁在问、问什么", "machine", 10, "台账"),
        ]
    support = strategy.get("support") or []
    for i, (what, who, minutes, accept) in enumerate(w3):
        task = {"day": (start_d + timedelta(days=14 + i * 2)).isoformat(),
                "what": what, "who": who, "minutes": minutes, "accept": accept}
        weeks[3].append(task)
        if support and i == 0:
            names = "、".join(s["name"] for s in support)
            weeks[3].append({"day": (start_d + timedelta(days=15)).isoformat(),
                             "what": f"在 {names} 各做 1 次轻动作（回答/转发/更新），不单独开 campaign",
                             "who": "human", "minutes": 20,
                             "accept": f"{len(support)} 条记录，带链接"})

    weeks[4] = [
        {"day": (start_d + timedelta(days=21)).isoformat(),
         "what": "把到期跟进全部做完（不新增触达）", "who": "human",
         "minutes": 30, "accept": "到期台账清零"},
        {"day": (start_d + timedelta(days=24)).isoformat(),
         "what": "统计四个数：发出、被删、回话、样本/报价", "who": "machine",
         "minutes": 5, "accept": "四条数字与台账一致"},
        {"day": (start_d + timedelta(days=27)).isoformat(),
         "what": "按目标判据复盘：继续 / 缩范围 / 换目标，三选一",
         "who": "human", "minutes": 20, "accept": "一页判定，附触发数据"},
    ]

    for w in weeks.values():
        w.sort(key=lambda t: t["day"])
    total = sum(t["minutes"] for w in weeks.values() for t in w)
    goal_name = (goal_plan.get("goal") or {}).get("name")
    if goal_name and goal_plan.get("secondary"):
        goal_name += " + " + goal_plan["secondary"]["name"]
    if goal_plan.get("secondary"):
        labels = ("资格与内容", "发布与试用", "分发与修复", "跟进与下一轮")
    else:
        labels = {
            "awareness": ("资格与内容", "发布与回应", "分发与复盘", "跟进与下一轮"),
            "adoption": ("资格与样例", "试用与反馈", "修复与分发", "跟进与下一轮"),
            "commercial": ("资格与名单", "触达与样本", "交付与报价", "跟进与下一轮"),
            "repeat": ("客户与结果", "回访与报价", "续约与转介绍", "复盘与下一轮"),
        }.get(family, ("资格与名单", "触达与样本", "交付与报价", "跟进与下一轮"))
    return {
        "primary": strategy["primary"]["name"],
        "goal": goal_name,
        "start": start,
        "end": (start_d + timedelta(days=27)).isoformat(),
        "weeks": [{"week": k, "focus": labels[k - 1], "tasks": weeks[k]}
                  for k in (1, 2, 3, 4)],
        "total_human_minutes": total,
        "actions_planned": len(actions),
    }


def render(plan: Dict[str, Any]) -> str:
    from .narrative import table
    title = f"# 四周动作表 · {plan['primary']}"
    if plan.get("goal"):
        title += f" · {plan['goal']}"
    lines = [title, ""]
    if plan.get("goal"):
        lines.append(f"{plan['start']} → {plan['end']}，人工合计约 {plan['total_human_minutes']} 分钟，"
                     f"计划动作 {plan['actions_planned']} 项（按目标方案，不套通用模板）。")
    else:
        lines.append(f"{plan['start']} → {plan['end']}，人工合计约 {plan['total_human_minutes']} 分钟，"
                     f"计划触达 {plan['actions_planned']} 次。")
    for w in plan["weeks"]:
        lines.append("")
        lines.append(f"## 第 {w['week']} 周 · {w['focus']}")
        lines.append("")
        lines.append(table(["日期", "做什么", "谁", "分钟", "验收物"],
                           [[t["day"], t["what"], "人" if t["who"] == "human" else "机器",
                             t["minutes"], t["accept"]] for t in w["tasks"]]))
    lines.append("")
    lines.append("> 没有验收物的任务不算做完。")
    return "\n".join(lines)
