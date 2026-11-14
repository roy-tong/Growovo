#!/usr/bin/env python3
"""内容策略：把「怎么写出有人愿意回、愿意点主页、愿意关注」拆成可检查的结构。

三个层次（完整设计见 docs/grow-运营策略-v1.md）：
  1. 回复层（发现）：在别人帖下先同意具体观点，再补一个可验证的事实/经验，
     最后用一个真问题把对话接下去；不放链接、不提产品、不空泛夸。
  2. 证明层（主页）：原创帖子展示「持续解决同一类问题」的能力，让点进主页的人
     相信值得关注；CTA 只出现在自己的帖子里，不出现在回复里。
  3. 主页层（承接）：bio/置顶/近期内容一致，决定「看完帖子点进来的人会不会关注」。

本模块只做两件事：给 brief 配公式与配比；给写好的内容打分并列出问题。
它不生成文案，也不保证平台给流量——结构对了仍然可能没人看。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from .grow_fit import INTENT_CUES, PROMO_MARKERS, terms

# 每层每天/每周做多少：先做对话，再做证明；粉丝少的时候回复占比更高。
MIX_BY_FAMILY: Dict[str, Dict[str, float]] = {
    "awareness": {"reply": 0.8, "post": 0.2},
    "adoption": {"reply": 0.6, "post": 0.4},
    "revenue": {"reply": 0.5, "post": 0.5},
}
DEFAULT_MIX = {"reply": 0.8, "post": 0.2}

REPLY_FORMULAS: Dict[str, Dict[str, Any]] = {
    "reply.agree_extend_ask": {
        "name": "同意 + 补充 + 追问",
        "beats": ["acknowledge", "contribution", "invite"],
        "best_for": "对方在描述一个真实问题，你有同类经验",
        "rule": "先复述对方的具体点，再补一个他没说到的细节，最后问一个他愿意回答的问题",
    },
    "reply.contrast_boundary": {
        "name": "同意 + 反向边界 + 追问",
        "beats": ["acknowledge", "contrast", "boundary", "invite"],
        "best_for": "对方结论大体对，但漏了条件或代价",
        "rule": "先承认对的部分，再指出一个边界，不否定整个人",
    },
    "reply.koc_peer": {
        "name": "对同行 KOC：补充 + 追问",
        "beats": ["acknowledge", "contribution", "invite"],
        "best_for": "垂直领域 KOC 的帖子：把对方当同行，不恭维、不推销",
        "rule": "补一个对方没用过的细节或数字，再问一个他愿意回答的问题；不吹捧、不提产品",
    },
    "reply.experience_number": {
        "name": "经验 + 具体数字 + 追问",
        "beats": ["acknowledge", "contribution", "invite"],
        "best_for": "你有真实数字、流程或失败教训可分享",
        "rule": "数字必须是真的；没有数字就换同意+补充式",
    },
}

POST_FORMULAS: Dict[str, Dict[str, Any]] = {
    "post.teardown": {
        "name": "拆解一个具体问题",
        "beats": ["hook", "context", "method", "evidence", "boundary", "invite"],
        "best_for": "展示专业深度，让主页访客相信你懂行",
        "rule": "一个帖子只拆一个点；给步骤、给真实数字、给出边界",
    },
    "post.checklist": {
        "name": "可执行清单",
        "beats": ["hook", "checklist", "evidence", "invite"],
        "best_for": "被收藏、被转发，适合做主页门面",
        "rule": "每条都能今天就做；不写正确的废话",
    },
    "post.case": {
        "name": "真实案例复盘",
        "beats": ["hook", "situation", "action", "result", "lesson"],
        "best_for": "建立信任，承接回复层带来的主页访问",
        "rule": "结果要可验证；失败案例比成功案例更可信",
    },
}

ALL_FORMULAS = {**REPLY_FORMULAS, **POST_FORMULAS}
KIND_FORMULAS = {"reply": REPLY_FORMULAS, "post": POST_FORMULAS}

GENERIC_PRAISE = ("great post", "thanks for sharing", "nice one", "well said",
                  "完全同意", "说得好", "学习了", "有道理", "挺好的")
CTA_MARKERS = ("关注我", "follow me", "点主页", "我的主页", "in my bio",
               "link in bio", "私信我", "dm me")


def formulas_for(kind: str = "") -> List[Dict[str, Any]]:
    pool = KIND_FORMULAS.get(kind) if kind else ALL_FORMULAS
    return [dict(spec, id=key) for key, spec in (pool or {}).items()]


def mix_for(goal_family: str = "") -> Dict[str, float]:
    return dict(MIX_BY_FAMILY.get(str(goal_family or ""), DEFAULT_MIX))


def default_weights(kind: str) -> Dict[str, float]:
    pool = KIND_FORMULAS.get(kind) or {}
    if not pool:
        return {}
    share = 1.0 / len(pool)
    return {name: round(share, 4) for name in pool}


def apply_weights(weights: Optional[Dict[str, float]], *,
                  boost: str, reduce: str, step: float = 0.1,
                  floor: float = 0.05, ceiling: float = 0.6) -> Dict[str, float]:
    """在同类公式内做 +step / -step，裁剪到 [floor, ceiling] 后归一。纯函数。"""
    kind = "reply" if str(boost).startswith("reply.") else "post"
    pool = default_weights(kind)
    current = dict(pool)
    for key, value in (weights or {}).items():
        if key in current:
            try:
                current[key] = float(value)
            except (TypeError, ValueError):
                continue
    if boost in current:
        current[boost] = min(ceiling, current[boost] + step)
    if reduce in current:
        current[reduce] = max(floor, current[reduce] - step)
    total = sum(current.values()) or 1.0
    return {key: round(value / total, 4) for key, value in current.items()}


def allocate(count: int, names: Sequence[str],
             weights: Optional[Dict[str, float]] = None) -> Dict[str, int]:
    """把条数按权重分配到公式，总和不变（余数给权重最大者）。"""
    total = max(0, int(count))
    if not names or total == 0:
        return {}
    if weights:
        raw = {name: total * float(weights.get(name, 0.0)) for name in names}
    else:
        raw = {name: total / len(names) for name in names}
    out = {name: int(value) for name, value in raw.items()}
    remainder = total - sum(out.values())
    order = sorted(names, key=lambda n: (-(raw[n] - out[n]), n))
    for i in range(remainder):
        out[order[i % len(order)]] += 1
    return {name: value for name, value in out.items() if value}


def enrich_brief(brief: Dict[str, Any], *, goal_family: str = "",
                 formula_weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """给需求单补公式与配比：agent 按公式写，不按灵感写。"""
    kind = "reply" if brief.get("action") == "reply" else "post"
    pool = KIND_FORMULAS.get(kind) or {}
    if not pool:
        return dict(brief)
    names = list(pool)
    total = int(brief.get("count_per_week") or 0)
    allocation = allocate(total, names, formula_weights)
    plan = [{"formula": name, "name": pool[name]["name"], "count": count}
            for name, count in allocation.items()]
    enriched = dict(brief)
    enriched["formulas"] = plan
    enriched["content_layer"] = kind
    enriched["mix"] = mix_for(goal_family)
    enriched["rules"] = [
        "回复：先回应对方的具体观点，不放链接、不提产品、不空泛夸",
        "原创：一个帖子只讲一个点，给步骤/数字/边界，CTA 只放在自己的帖子里",
        "同一句话不重复用；不同帖子用不同公式",
    ]
    return enriched


def suggest_formula(target_text: str = "", *, kind: str = "reply",
                    koc: bool = False) -> str:
    """按原帖特征给一个默认公式；agent 仍可显式覆盖。"""
    if kind != "reply":
        return "post.teardown"
    low = str(target_text or "").lower()
    if koc:
        return "reply.koc_peer"
    if "?" in low or "？" in low or any(word in low for word in
                                       ("how do", "anyone", "recommend", "怎么", "如何")):
        return "reply.agree_extend_ask"
    if any(word in low for word in ("but", "however", "不过", "但")):
        return "reply.contrast_boundary"
    return "reply.agree_extend_ask"


def _has_link(text: str) -> bool:
    return bool(re.search(r"https?://|www\.", str(text or ""), re.I))


def _ends_with_question(text: str) -> bool:
    return str(text or "").strip().endswith(("?", "？"))


def _score_beats(text: str, target_text: str, beats: Sequence[str],
                 *, product_name: str = "") -> Dict[str, bool]:
    low = str(text or "").lower()
    target_terms = terms(target_text)
    text_terms = terms(text)
    overlap = target_terms & text_terms
    acknowledge = bool(overlap) and any(
        word in low for word in list(overlap)[:20]) if target_text else False
    contribution = bool(re.search(
        r"\d|%|\bwe\b|\bour\b|\bi\b|我们|我用|in practice|first-hand|"
        r"the fix|the trap|the hard part|具体|例如|"
        r"\b(filter|compare|match|check|reconcile|separate|align|map|start with)\b|核对|对齐|拆分|比较", low))
    contrast = any(word in low for word in ("but", "however", "不过", "但", "相反"))
    boundary = any(word in low for word in (
        "unless", "except", "only if", "fails when", "breaks when",
        "doesn't work when", "caveat", "the limit", "除非", "前提", "代价",
        "边界", "不一定"))
    invite = _ends_with_question(text)
    return {"acknowledge": acknowledge, "contribution": contribution,
            "contrast": contrast, "boundary": boundary, "invite": invite,
            "overlap_terms": sorted(overlap)[:6]}


def score_content(text: str, *, kind: str = "reply", target_text: str = "",
                  formula: str = "", product_name: str = "") -> Dict[str, Any]:
    """0–100 的内容结构分 + 问题清单；启发式，不假装能预测流量。"""
    text = str(text or "").strip()
    low = text.lower()
    formula = formula or ("reply.agree_extend_ask" if kind == "reply" else "post.teardown")
    unknown_formula = formula not in ALL_FORMULAS
    spec = ALL_FORMULAS.get(formula) or ALL_FORMULAS[
        "reply.agree_extend_ask" if kind == "reply" else "post.teardown"]
    beats = _score_beats(text, target_text, spec["beats"], product_name=product_name)
    score = 0
    issues: List[str] = []
    strengths: List[str] = []

    if kind == "reply":
        if beats["acknowledge"]:
            score += 25
            strengths.append("回应的正是对方帖里的具体内容")
        elif target_text:
            issues.append("没有接上对方的具体观点（先复述或引用一个具体点）")
        if beats["contribution"]:
            score += 20
            strengths.append("补了自己的事实/数字/经验")
        else:
            issues.append("只表态没增量：补一个具体事实、数字或做法")
        if beats["boundary"]:
            score += 8
            strengths.append("给了边界，不像无脑附和")
        if beats["invite"]:
            score += 15
            strengths.append("用问题把对话接下去")
        else:
            issues.append("结尾没有真问题，对话容易断")
        if _has_link(text):
            score -= 20
            issues.append("回复里带链接：平台和人都容易当广告")
        if product_name and product_name.lower() in low:
            score -= 10
            issues.append("回复里提了产品名：前 20 次互动先不谈产品")
        if any(marker in low for marker in GENERIC_PRAISE):
            score -= 12
            issues.append("空泛夸（great post/说得好）：删掉，换成具体内容")
    else:
        if len(text) >= 120:
            score += 15
        if re.search(r"\d|%", text):
            score += 15
            strengths.append("有数字/证据")
        if any(word in low for word in ("first", "then", "step", "第一", "然后", "步骤")):
            score += 10
            strengths.append("有可执行步骤")
        if beats["boundary"]:
            score += 10
            strengths.append("交代了边界/失败情况")
        if beats["invite"]:
            score += 5
        if _has_link(text):
            score += 0  # 自己的帖子可带链接，但别指望它带来互动

    length = len(text)
    if 60 <= length <= 280:
        score += 10
    elif length < 40:
        score -= 15
        issues.append("太短，没有信息增量")
    elif length > 280:
        score -= 10
        issues.append("超出发帖长度上限（X 280）")

    if any(marker in low for marker in CTA_MARKERS) and kind == "reply":
        score -= 10
        issues.append("回复里出现关注/私信 CTA：先提供价值，CTAs 留给自己的帖子")
    if any(marker in low for marker in PROMO_MARKERS):
        score -= 15
        issues.append("有营销/引流词，容易被当广告")

    if unknown_formula:
        issues.append(f"未知公式 {formula}，请用 mkt grow formulas 查看可选公式")
    score = max(0, min(100, score))
    return {"score": score, "formula": formula, "kind": kind,
            "unknown_formula": unknown_formula,
            "beats": beats, "issues": issues, "strengths": strengths,
            "ok": score >= 55 and not any(i.startswith("回复里带链接") for i in issues)}


def lint_stage_item(item: Dict[str, Any], *, product_name: str = "") -> Dict[str, Any]:
    """stage 时对单条内容跑结构检查；返回可并入队列项的检查结果。"""
    kind = "reply" if item.get("kind") == "reply" else item.get("kind") or "post"
    result = score_content(str(item.get("body") or ""), kind=kind,
                           target_text=str(item.get("target_text") or ""),
                           formula=str(item.get("formula") or ""),
                           product_name=product_name)
    return result


def render_formulas() -> str:
    lines = ["# 内容公式（按层）", ""]
    for kind, pool in KIND_FORMULAS.items():
        lines.append(f"## {'回复层' if kind == 'reply' else '证明层'}")
        lines.append("")
        for key, spec in pool.items():
            lines.append(f"- `{key}`：{spec['name']}")
            lines.append(f"  节奏：{' → '.join(spec['beats'])}")
            lines.append(f"  适合：{spec['best_for']}")
            lines.append(f"  纪律：{spec['rule']}")
        lines.append("")
    lines += ["配比（按目标）：", ""]
    for family, mix in MIX_BY_FAMILY.items():
        lines.append(f"- {family}：回复 {int(mix['reply'] * 100)}% / 原创 {int(mix['post'] * 100)}%")
    return "\n".join(lines)
