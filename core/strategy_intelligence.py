"""Evidence-bound strategy cards, contextual selection and outcome adjustment."""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from . import experience, tactics


SCHEMA = "growovo.strategy-decision/1"
CENSUS_SCHEMA = "growovo.strategy-corpus-census/1"
ATLAS_SCHEMA = "growovo.strategy-atlas/1"

MECHANISM_META = {
    "borrowed_audience": {"intents": {"scale", "awareness", "adoption"}, "stages": {"early", "growth"}, "channels": {"x", "github", "reddit", "linkedin"}, "query": ("回复", "评论", "首评")},
    "proven_demand": {"intents": {"scale", "awareness"}, "stages": {"early", "growth"}, "channels": {"x", "xiaohongshu", "wechat", "bilibili"}, "query": ("爆款", "验证", "选题")},
    "search_seat": {"intents": {"scale", "adoption", "efficiency"}, "stages": {"early", "growth", "retention"}, "channels": {"x", "github", "xiaohongshu", "wechat", "zhihu"}, "query": ("搜索", "长尾", "关键词")},
    "official_tap": {"intents": {"scale", "awareness"}, "stages": {"early", "growth"}, "channels": {"x", "github", "xiaohongshu"}, "query": ("官方", "入口", "目录")},
    "volume_probability": {"intents": {"scale", "awareness"}, "stages": {"growth"}, "channels": set(), "min_quality": 70, "query": ("数量", "频率", "发布")},
    "rule_window": {"intents": {"scale", "awareness", "efficiency"}, "stages": {"early", "growth"}, "channels": {"x", "hackernews", "zhihu", "product-hunt"}, "query": ("窗口", "首小时", "时间")},
    "story_pull": {"intents": {"scale", "awareness"}, "stages": {"early", "growth"}, "channels": {"x", "wechat", "xiaohongshu"}, "query": ("故事", "失败", "情绪", "复盘")},
    "trust_nodes": {"intents": {"scale", "adoption", "efficiency"}, "stages": {"early"}, "channels": set(), "query": ("KOC", "信任", "粉丝")},
    "sequence_window": {"intents": {"retention", "adoption", "efficiency"}, "stages": {"early", "retention"}, "channels": set(), "query": ("细分", "次序", "衰减", "密度")},
    "compound_loop": {"intents": {"scale", "adoption", "retention"}, "stages": {"growth", "retention"}, "channels": set(), "min_accepted_results": 1, "query": ("复利", "循环", "回流", "推荐")},
    "product_embedded": {"intents": {"scale", "adoption"}, "stages": {"growth", "retention"}, "channels": set(), "required_assets": {"shareable"}, "query": ("分享", "署名", "导出", "邀请")},
    "value_exchange": {"intents": {"adoption", "commercial", "scale"}, "stages": {"growth", "retention"}, "channels": set(), "required_assets": {"private_data"}, "query": ("私有数据", "基准", "资产", "报告")},
    "paid_amplify": {"intents": {"scale", "efficiency"}, "stages": {"growth"}, "channels": set(), "paid_allowed": True, "min_accepted_results": 1, "query": ("付费", "放大", "赢家", "暖")},
    "competitor_events": {"intents": {"scale", "adoption", "commercial"}, "stages": {"early", "growth"}, "channels": set(), "requires_event": True, "query": ("竞品", "涨价", "迁移", "故障")},
    "own_the_venue": {"intents": {"scale", "awareness"}, "stages": {"growth", "retention"}, "channels": {"web", "github", "email"}, "query": ("目录", "榜单", "认证", "周报")},
    "alliance_bundle": {"intents": {"adoption", "commercial", "scale"}, "stages": {"growth", "retention"}, "channels": set(), "required_assets": {"partner"}, "query": ("联盟", "捆绑", "集成", "伙伴")},
    "event_waves": {"intents": {"scale", "awareness", "adoption"}, "stages": {"early", "growth"}, "channels": set(), "requires_event": True, "query": ("浪潮", "生效", "关停", "预算", "监管")},
    "radical_transparency": {"intents": {"awareness", "adoption", "scale"}, "stages": {"early", "growth"}, "channels": {"x", "wechat", "github"}, "required_assets": {"real_data"}, "query": ("公开", "透明", "收入", "更新日志", "复盘")},
    "agent_surfaces": {"intents": {"scale", "adoption"}, "stages": {"early", "growth", "retention"}, "channels": {"agent", "web", "github"}, "required_assets": {"agent_ready"}, "query": ("Agent", "MCP", "引用", "API", "试用")},
    "diagnostic_probe": {"intents": {"efficiency", "adoption"}, "stages": {"early", "growth", "retention"}, "channels": set(), "query": ("探针", "驳回", "审核", "限流", "接口")},
}


def census(rows=None, assessor=None):
    rows = list(experience.load() if rows is None else rows)
    assessor = assessor or (lambda row: experience.assess_eligibility(row, purpose="strategy"))
    assessed = [assessor(row) for row in rows]
    reasons = Counter(reason for row in assessed for reason in row.get("reasons") or [])
    return {
        "schema": CENSUS_SCHEMA, "records": len(rows),
        "by_knowledge": dict(Counter(str(row.get("knowledge_type") or "unknown") for row in rows)),
        "by_platform": dict(Counter(str(row.get("platform") or "unknown") for row in rows)),
        "eligibility": {"eligible": sum(bool(row.get("eligible")) for row in assessed),
                        "not_eligible": sum(not row.get("eligible") for row in assessed)},
        "review_grades": dict(Counter(str(row.get("review_grade") or "unknown") for row in assessed)),
        "top_reasons": reasons.most_common(12),
        "boundary": "raw_corpus_is_not_strategy_proof",
    }


def _examples(card, rows):
    query = card["query"]
    matches = [row for row in rows if any(str(term).lower() in str(row.get("claim") or "").lower()
                                         for term in query)]
    examples = []
    for row in matches:
        craft = experience.assess_eligibility(row, purpose="craft")
        strategy = experience.assess_eligibility(row, purpose="strategy")
        examples.append({
            "claim": row.get("claim"), "platform": row.get("platform"),
            "knowledge_type": row.get("knowledge_type"), "support": row.get("support"),
            "canonical_url": row.get("canonical_url"),
            "craft_eligible": craft["eligible"], "strategy_eligible": strategy["eligible"],
            "review_grade": strategy["review_grade"],
        })
    examples.sort(key=lambda row: (not row["craft_eligible"], -(row["support"] or 0)))
    return examples[:5]


def strategy_cards(rows=None):
    rows = list(experience.load() if rows is None else rows)
    cards = []
    for mechanism, principle in tactics.MECHANISMS.items():
        meta = MECHANISM_META[mechanism]
        card = {
            "id": mechanism, "name": principle["name"], "principle": principle["principle"],
            "ask": principle["ask"], "intents": sorted(meta["intents"]),
            "stages": sorted(meta["stages"]), "channels": sorted(meta["channels"]),
            "min_quality": meta.get("min_quality", 0),
            "min_accepted_results": meta.get("min_accepted_results", 0),
            "paid_allowed_required": bool(meta.get("paid_allowed")),
            "requires_event": bool(meta.get("requires_event")),
            "required_assets": sorted(meta.get("required_assets") or set()),
            "query": list(meta["query"]),
            "evidence_refs": ["core/tactics.py#" + mechanism,
                              "docs/playbooks/战术/非常规增长战术总表.md#" + mechanism,
                              "docs/playbooks/经验库/experience.jsonl"],
        }
        card["corpus_examples"] = _examples(card, rows)
        strategy_eligible = sum(row["strategy_eligible"] for row in card["corpus_examples"])
        card["usage_boundary"] = "conditional_strategy" if strategy_eligible else "experiment_only"
        cards.append(card)
    return cards


def _score(card, context):
    intent = str(context.get("intent") or "awareness")
    stage = str(context.get("stage") or "early")
    channels = set(context.get("channels") or [])
    quality = float(context.get("quality_score") or 0)
    accepted = int(context.get("accepted_results") or 0)
    score = (40 if intent in card["intents"] else 0) + (10 if stage in card["stages"] else 0)
    if intent == "scale" and card["id"] == "volume_probability":
        score += 15
    if accepted and card["id"] in {"compound_loop", "product_embedded"}:
        score += 15
    score += 15 if not card["channels"] or channels & set(card["channels"]) else 0
    score += min(15, quality * 0.15)
    score += 10 if accepted else 5
    reasons = []
    if intent in card["intents"]:
        reasons.append("命中当前意图：%s" % intent)
    if stage in card["stages"]:
        reasons.append("匹配业务阶段：%s" % stage)
    if not card["channels"] or channels & set(card["channels"]):
        reasons.append("现有渠道可执行")
    if accepted:
        reasons.append("已有接受结果可作为调整证据")
    else:
        reasons.append("尚无接受结果，保留探索而非判定无效")
    return round(score, 2), reasons


def _missing(card, context):
    missing = []
    quality = float(context.get("quality_score") or 0)
    if quality < card["min_quality"]:
        missing.append("quality_below_floor")
    if int(context.get("accepted_results") or 0) < card["min_accepted_results"]:
        missing.append("accepted_results_required")
    if card["paid_allowed_required"] and not context.get("paid_allowed"):
        missing.append("paid_not_allowed")
    if card["requires_event"] and not context.get("event"):
        missing.append("event_trigger_required")
    assets = set(context.get("product_assets") or [])
    if card["required_assets"] and not assets & set(card["required_assets"]):
        missing.append("required_asset:" + ",".join(card["required_assets"]))
    channels = set(context.get("channels") or [])
    if card["channels"] and not channels & set(card["channels"]):
        missing.append("channel_access_required")
    return missing


def select_strategy(context):
    candidates = []
    for card in strategy_cards():
        score, reasons = _score(card, context)
        missing = _missing(card, context)
        candidates.append(dict(card, eligible=not missing, missing_inputs=missing,
                               selection_score=score, selection_reason=reasons))
    candidates.sort(key=lambda row: (-row["selection_score"], row["id"]))
    selected = [row for row in candidates if row["eligible"]][:3]
    digest = hashlib.sha256(json.dumps({"context": context, "selected": [row["id"] for row in selected]},
                                       ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
    return {"schema": SCHEMA, "decision_id": "strategy-" + digest,
            "context": context, "selected": selected, "candidates": candidates,
            "evidence_status": "contextual_hypothesis_not_performance_prediction"}


def adjust_strategy(context, card, outcomes):
    actions = int(outcomes.get("actions") or 0)
    interactions = int(outcomes.get("qualified_interactions") or 0)
    first_runs = int(outcomes.get("first_runs") or 0)
    accepted = int(outcomes.get("accepted_results") or 0)
    second = int(outcomes.get("second_use") or 0)
    quality = float(outcomes.get("quality_score", context.get("quality_score") or 0))
    mechanism = str(card.get("id") or "")
    if outcomes.get("channel_stop"):
        action, next_mechanism, multiplier = "recover_channel", "diagnostic_probe", 0
        reason = "渠道存在明确停止状态，先恢复与分类，不扩大写入"
    elif quality < 70:
        action, next_mechanism, multiplier = "revise_quality", mechanism, 0.5
        reason = "素材低于放量质量门，先修对象与内容"
    elif actions == 0:
        action, next_mechanism, multiplier = "explore", mechanism, 1.0
        reason = "尚无结果数据，保留高质量探索"
    elif accepted and not second:
        action, next_mechanism, multiplier = "build_retention_loop", "compound_loop", 1.0
        reason = "已有接受结果但没有第二任务，先把输出变成复用输入"
    elif accepted and second:
        action, next_mechanism, multiplier = "scale_up", mechanism, 1.5
        reason = "接受结果与第二任务同时出现，支持放大当前机制"
    elif first_runs and not accepted:
        action, next_mechanism, multiplier = "improve_first_task", "diagnostic_probe", 0.75
        reason = "有首用但没有接受结果，先修首任务和输出"
    elif interactions and not first_runs:
        action, next_mechanism, multiplier = "improve_asset", "product_embedded", 0.75
        reason = "有互动但没有首用，先修资产和承接"
    elif actions >= 30 and not interactions:
        action, next_mechanism, multiplier = "switch_audience_or_hook", "borrowed_audience", 0.5
        reason = "已有足够动作但没有合格互动，换受众或钩子，不盲目加量"
    else:
        action, next_mechanism, multiplier = "explore", mechanism, 1.0
        reason = "样本仍不足，继续探索并保留下一轮判断"
    return {"action": action, "next_mechanism": next_mechanism,
            "allocation_multiplier": multiplier, "reason": reason,
            "evidence": {"actions": actions, "qualified_interactions": interactions,
                         "first_runs": first_runs, "accepted_results": accepted, "second_use": second},
            "evidence_status": "observed_context_not_causal_proof"}


def recommend(context, outcomes):
    selection = select_strategy(context)
    card = selection["selected"][0] if selection["selected"] else selection["candidates"][0]
    return dict(selection, adjustment=adjust_strategy(context, card, outcomes))
