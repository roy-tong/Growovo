#!/usr/bin/env python3
"""策略引擎：从「这个产品 + 这些数据」推出「这一轮只打哪一条、怎么起量、什么时候放大」。

和 gtm.py 的分工：gtm 回答「平台怎么排位」（静态匹配），本模块回答
「这一轮具体怎么打」（动态：结合复盘结果、账号现状、人工预算）。

输出四块，每一块都必须是能照着做的：
  主渠道（1）+ 发射点（1）+ 不做（≥3，带理由）
  冷启动五阶段：每阶段目标、判据、预计时长
  放大条件：什么信号出现才加量、加多少、往哪个方向加
  本周三件事：第一周具体做什么

平台知识来自 docs/playbooks 手册库，这里只保留可执行的参数（账号门槛、阶段判据、
放大条件、风控映射、内容档位），不再重复写打法。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import gtm

# 每个平台的运营参数。字段含义：
#   account     开跑前必须有的账号条件（没有就开不了）
#   residency   养号期（天），期间只做贡献不做推广
#   stages      冷启动五阶段的判据（阶段名, 判据, 参考天数）
#   scale       放大的触发信号与方式
#   risk        （检测手段, 我们能看到的信号, 具体改什么）
#   tier        内容档位：A 人写 / B 人改+披露 / C AI 生成+门审
from .platforms import BY_ID as _BY_ID, PLATFORMS as _PLATFORMS

# 运营参数统一来自 core/platforms.py，本模块只负责「怎么用」。
PLAYBOOK: Dict[str, Dict[str, Any]] = {
    p["id"]: {"account": p["account"], "residency": p["residency"],
              "stages": p["stages"], "scale": p["scale"],
              "risk": p["risks"], "tier": p["tier"], "kind": p["kind"]}
    for p in _PLATFORMS
}

DEFAULT_PLAYBOOK = {
    "account": ["一个真实可登录的账号", "先读平台规则"],
    "residency": 14,
    "stages": [("有资格", "内容不被平台拦", 0), ("被看见", "有人点进来", 14),
               ("被问到", "≥3 人主动提问", 28), ("被用上", "有人真的用", 45),
               ("被付费", "≥1 笔", 90)],
    "scale": {"signal": "连续 2 周有「被问到」", "how": "加人，不加频率"},
    "risk": [("平台判定", "内容被删/限流", "降一档内容，降一半频率")],
    "tier": "B 人改写 + 披露",
}


def _pb(platform_id: str) -> Dict[str, Any]:
    return PLAYBOOK.get(platform_id, DEFAULT_PLAYBOOK)


def _weakest_first(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """复盘里表现最差的渠道，用来生成「不做」清单。"""
    scored = [r for r in rows if (r.get("actions") or 0) >= 20]
    scored.sort(key=lambda r: (r.get("response_per_100") or 0))
    return scored


def build(product: Dict[str, Any], focus: Optional[Dict[str, Any]] = None,
          analysis: Optional[Dict[str, Any]] = None,
          constraints: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """生成这一轮的策略。focus 来自 gtm.diagnose；analysis 来自 retro。"""
    constraints = constraints or {}
    focus = focus or {}
    primary = focus.get("primary") or "github"
    launch = focus.get("launch")
    deferred = focus.get("deferred") or []

    pb = _pb(primary)
    rows = (analysis or {}).get("channels") or []

    # 不做清单：先放复盘里最差的渠道，再补上 gtm 判为不优先的
    not_doing: List[Dict[str, str]] = []
    for r in _weakest_first(rows)[:2]:
        if r["channel"] == primary:
            continue
        rate = r.get("response_per_100")
        not_doing.append({
            "name": r["name"],
            "why": (f"复盘里 {int(r.get('actions') or 0)} 次动作只换回 "
                    f"{r.get('responses') or 0} 次回应"
                    + (f"（每百次 {rate}）" if rate is not None else "")
                    + "，这一轮不再投入"),
        })
    for name in deferred:
        if len(not_doing) >= 3:
            break
        if any(x["name"] == name for x in not_doing):
            continue
        not_doing.append({"name": name,
                          "why": "匹配度够，但人手只够一条渠道——排到下一轮"})

    # 复盘里有实测时，用它校正静态匹配：先看结果产出，再看回应。
    measured = [r for r in rows if (r.get("actions") or 0) >= 30
                and r.get("outcome_per_100") is not None]
    if measured:
        best = sorted(measured, key=lambda r: (-(r["outcome_per_100"] or 0),
                                               -(r["response_per_100"] or 0)))[0]
        if best["channel"] in PLAYBOOK:
            primary, pb = best["channel"], _pb(best["channel"])
        else:
            primary = best["channel"] if best["channel"] in PLAYBOOK else primary

    # 保留清单：不是主力，但实测有效率，不能一起砍掉。
    keep_small: List[Dict[str, str]] = []
    for r in rows:
        if r["channel"] == primary:
            continue
        a = r.get("actions") or 0
        resp = r.get("responses") or 0
        rate = r.get("response_per_100")
        if a >= 30 and (rate or 0) >= 3:
            keep_small.append({"name": r["name"],
                               "why": (f"每百次动作能换回 {rate} 次回应，是有效的；"
                                       "但这一轮产能只够一条渠道，先保留不扩")})
        elif 0 < a < 30 and resp:
            keep_small.append({"name": r["name"],
                               "why": (f"只跑了 {int(a)} 次，{int(resp)} 次有回应——"
                                       "样本太小不能当主力，但不要停")})

    stages = [{"stage": s, "criterion": c, "days": d, "done": False}
              for s, c, d in pb["stages"]]

    first_week = _first_week(primary, pb, constraints)

    return {
        "product": product.get("name", ""),
        "primary": {"id": primary, "name": gtm_name(primary),
                    "why": _primary_reason(primary, analysis),
                    "account": pb["account"], "residency": pb["residency"],
                    "tier": pb["tier"], "kind": pb.get("kind", "社区")},
        "launch": ({"id": launch, "name": gtm_name(launch),
                    "why": "只用来换曝光和真实反馈，不背获客指标"} if launch else None),
        "not_doing": not_doing[:3],
        "keep_small": keep_small[:3],
        "cold_start": stages,
        "scale": pb["scale"],
        "risk": [{"detect": d, "signal": s, "adjust": a} for d, s, a in pb["risk"]],
        "first_week": first_week,
        "budget": {"human_minutes_per_week": constraints.get("human_minutes_per_week", 120),
                   "actions_per_week": constraints.get("actions_per_week", 20)},
    }


def gtm_name(pid: str) -> str:
    p = _BY_ID.get(pid)
    return p["name"] if p else pid


def _primary_reason(pid: str, analysis: Optional[Dict[str, Any]]) -> str:
    rows = (analysis or {}).get("channels") or []
    for r in rows:
        if r["channel"] == pid and r.get("response_per_100") is not None:
            return (f"复盘里它是效率最高的一条：每 100 次动作换回 "
                    f"{r['response_per_100']} 次回应，是其他渠道的数倍")
    return "按人群和产品类型匹配选出来的，**还没实测过**——这一轮就是它的第一次验证"


def _first_week(primary: str, pb: Dict[str, Any], constraints: Dict[str, Any]) -> List[str]:
    """第一周只做三件事：补齐资格、把名单定下来、写第一条。"""
    tasks = [f"补齐开跑资格：{pb['account'][0]}"]
    if pb["residency"] > 0:
        tasks.append(f"进入 {pb['residency']} 天养号期：只答问题，一条产品都不提")
    else:
        tasks.append("整理 20 个目标对象，每人写一句「他最近在做什么」")
    tasks.append("写完第一条消息（给东西 → 再说自己 → 结尾一个真问题），先给自己看一遍")
    return tasks


def render(strategy: Dict[str, Any]) -> str:
    """业务语言渲染。不出现内部编码。"""
    from .narrative import table
    lines: List[str] = []
    p = strategy["primary"]
    lines.append(f"# 这一轮怎么打 · {strategy['product']}")
    lines.append("")
    lines.append(f"**主渠道只有一个：{p['name']}。** {p['why']}。")
    lines.append("")
    if strategy.get("launch"):
        lines.append(f"发射点：{strategy['launch']['name']}——{strategy['launch']['why']}。")
        lines.append("")
    if strategy.get("keep_small"):
        lines.append("## 保留但不主力")
        lines.append("")
        lines.append(table(["打法", "为什么保留"],
                           [[x["name"], x["why"]] for x in strategy["keep_small"]]))
        lines.append("")
    if strategy["not_doing"]:
        lines.append("## 这一轮不做")
        lines.append("")
        lines.append(table(["渠道", "为什么不做"],
                           [[x["name"], x["why"]] for x in strategy["not_doing"]]))
        lines.append("")
    lines.append("## 开跑前提")
    lines.append("")
    for a in p["account"]:
        lines.append(f"- {a}")
    lines.append(f"- 内容档位：{p['tier']}")
    if p["residency"]:
        lines.append(f"- 养号期：{p['residency']} 天，期间只做贡献")
    lines.append("")
    from . import voice
    v = voice.profile(strategy["primary"]["id"])
    lines.append("## 这个渠道怎么说话")
    lines.append("")
    lines.append(f"- 语气：{v['tone']}")
    lines.append(f"- 句式：{v['sentences']}")
    lines.append(f"- 长度：{v['length']}")
    lines.append(f"- 黑话：{v['memes']}")
    lines.append("")
    lines.append("开头可以这样写：")
    for o in v["openers"][:3]:
        lines.append(f"- {o}")
    lines.append("")
    lines.append("别这么写：" + "；".join(v["taboos"]))
    lines.append("")
    from . import experience
    if hasattr(experience, "eligible"):
        exp_rules = experience.eligible(strategy["primary"]["id"])
    else:
        exp_rules = experience.graded(strategy["primary"]["id"])["rule"]
    if exp_rules:
        lines.append("## 用户怎么说（不是官方，是别人真跑过的）")
        lines.append("")
        for r in exp_rules[:4]:
            lines.append(f"- **{r['claim']}**（{r['support']} 篇提到）→ {r['action']}")
        lines.append("")
    lines.append("## 冷启动五个阶段")
    lines.append("")
    lines.append(table(["阶段", "通过判据", "参考天数"],
                       [[s["stage"], s["criterion"], s["days"]]
                        for s in strategy["cold_start"]]))
    lines.append("")
    lines.append("> 「冷启动完成」意味着走到**被问到**这一档，不是「发出去了」。")
    lines.append("")
    lines.append("## 什么时候可以放大")
    lines.append("")
    lines.append(f"信号：{strategy['scale']['signal']}")
    lines.append("")
    lines.append(f"怎么做：{strategy['scale']['how']}")
    lines.append("")
    lines.append("## 平台会怎么查我们")
    lines.append("")
    lines.append(table(["平台检测", "我们能看到", "具体改什么"],
                       [[r["detect"], r["signal"], r["adjust"]] for r in strategy["risk"]]))
    lines.append("")
    lines.append("## 第一周三件事")
    lines.append("")
    for i, t in enumerate(strategy["first_week"], 1):
        lines.append(f"{i}. {t}")
    lines.append("")
    return "\n".join(lines)
