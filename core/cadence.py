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


def build(strategy: Dict[str, Any], start: str, actions_per_week: int = 20) -> Dict[str, Any]:
    """按四周切：资格周 / 触达周 / 跟进周 / 判定周。"""
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


def render(plan: Dict[str, Any]) -> str:
    from .narrative import table
    lines = [f"# 四周动作表 · {plan['primary']}",
             "",
             f"{plan['start']} → {plan['end']}，人工合计约 {plan['total_human_minutes']} 分钟，"
             f"计划触达 {plan['actions_planned']} 次。"]
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
