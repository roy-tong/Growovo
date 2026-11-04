#!/usr/bin/env python3
"""Opportunity Discovery：把产品事实与已收集信号整理成可验证候选。

纯函数、无网络、无模型调用、无文件写入。匹配只使用输入里的显式标签、ID
和确定性的文字包含关系，不把关键词相似度包装成需求或购买意愿。

产品事实支持 ``value/values``、``target_audience/audiences``、
``strengths/advantages``、``limitations/limits``；每项可以是字符串或带
``id/text/tags`` 的 dict。信号按五类传入：``help_requests``、
``competitor_limits``、``new_workflows``、``repeated_questions``、
``inactive_trials``，也可用 ``{"signals": [{"type": ...}]}``。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Sequence, Tuple

SIGNAL_TYPES = (
    "help_request", "competitor_limit", "new_workflow",
    "repeated_question", "inactive_trial",
)

_SIGNAL_ALIASES = {
    "help_requests": "help_request", "help_request": "help_request",
    "help": "help_request", "求助": "help_request",
    "competitor_limits": "competitor_limit", "competitor_limit": "competitor_limit",
    "competitor_limitations": "competitor_limit", "竞品限制": "competitor_limit",
    "new_workflows": "new_workflow", "new_workflow": "new_workflow",
    "workflows": "new_workflow", "新工作流": "new_workflow",
    "repeated_questions": "repeated_question", "repeated_question": "repeated_question",
    "user_questions": "repeated_question", "重复用户问题": "repeated_question",
    "inactive_trials": "inactive_trial", "inactive_trial": "inactive_trial",
    "trial_not_activated": "inactive_trial", "trial_inactive": "inactive_trial",
    "试用未激活": "inactive_trial",
}

_SIGNAL_LABELS = {
    "help_request": "用户求助",
    "competitor_limit": "竞品限制",
    "new_workflow": "新工作流",
    "repeated_question": "重复用户问题",
    "inactive_trial": "试用未激活",
}

_ACTION_LABELS = {
    "give_solution": "给方案",
    "generate_asset": "生成资产",
    "fix_product": "修产品",
    "pause_acquisition": "暂停获客",
    "collect_evidence": "补证",
}

_VALUE_KEYS = ("value", "values", "value_propositions", "value_proposition", "价值")
_AUDIENCE_KEYS = ("target_audience", "audiences", "audience", "target_users", "icp", "目标人群")
_STRENGTH_KEYS = ("strengths", "advantages", "strength", "优势")
_LIMIT_KEYS = ("limitations", "limits", "constraints", "限制")
_TEXT_KEYS = ("text", "need", "outcome", "name", "title", "summary", "description",
              "segment", "role", "hypothesis", "label", "value")
_STOPWORDS = {
    "a", "an", "and", "for", "the", "to", "of", "with", "user", "users",
    "product", "workflow", "help", "用户", "产品", "帮助", "工作流", "需求",
}


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _first(mapping: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "是")


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _tokens(value: Any) -> set[str]:
    text = _norm(value)
    words = set(re.findall(r"[a-z0-9][a-z0-9_+#.-]{1,}", text))
    # 中文不做模型分词；完整短语仅在显式标签或包含关系中使用。
    phrases = set(re.findall(r"[\u4e00-\u9fff]{2,}", text))
    return {x for x in words | phrases if x not in _STOPWORDS}


def _entries(raw: Any, prefix: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for index, item in enumerate(_as_list(raw), 1):
        if isinstance(item, dict):
            data = dict(item)
            text = _norm(_first(data, _TEXT_KEYS))
            identifier = str(data.get("id") or data.get("key") or f"{prefix}-{index}")
            raw_tags = _as_list(data.get("tags")) + _as_list(data.get("keywords"))
        else:
            data = {"text": str(item)}
            text = _norm(item)
            identifier = f"{prefix}-{index}"
            raw_tags = []
        tags = {_norm(x) for x in raw_tags if _norm(x)}
        terms = set(tags) | _tokens(text) | _tokens(identifier)
        out.append({"id": identifier, "text": text, "tags": tags,
                    "terms": {x for x in terms if x}, "raw": data})
    return out


def _product_entries(facts: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    nested = facts.get("product") if isinstance(facts.get("product"), dict) else {}

    def pick(keys: Sequence[str]) -> Any:
        value = _first(facts, keys)
        return value if value is not None else _first(nested, keys)

    return {
        "value": _entries(pick(_VALUE_KEYS), "value"),
        "audience": _entries(pick(_AUDIENCE_KEYS), "audience"),
        "strength": _entries(pick(_STRENGTH_KEYS), "strength"),
        "limitation": _entries(pick(_LIMIT_KEYS), "limitation"),
    }


def _signal_items(signals: Any) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[str]]:
    items: List[Tuple[str, Dict[str, Any]]] = []
    warnings: List[str] = []
    if signals is None:
        return items, warnings
    if isinstance(signals, dict) and any(k in signals for k in _SIGNAL_ALIASES):
        groups: Iterable[Any] = [(key, value) for key, value in signals.items()
                                 if key in _SIGNAL_ALIASES]
        for key, value in groups:
            kind = _SIGNAL_ALIASES[key]
            for signal in _as_list(value):
                if isinstance(signal, dict):
                    items.append((kind, dict(signal)))
                elif isinstance(signal, str) and signal.strip():
                    items.append((kind, {"text": signal}))
                else:
                    warnings.append(f"忽略非结构化信号: {key}")
        return items, warnings
    if isinstance(signals, dict):
        raw_items = signals.get("signals", signals.get("items", []))
    else:
        raw_items = signals
    for signal in _as_list(raw_items):
        if not isinstance(signal, dict):
            warnings.append("忽略非结构化信号")
            continue
        raw_kind = str(_first(signal, ("signal_type", "type", "kind", "category")) or "")
        kind = _SIGNAL_ALIASES.get(raw_kind, _SIGNAL_ALIASES.get(raw_kind + "s", ""))
        if not kind:
            warnings.append(f"忽略未知信号类型: {raw_kind or '（空）'}")
            continue
        items.append((kind, dict(signal)))
    return items, warnings


def _signal_text(signal: Dict[str, Any]) -> str:
    keys = ("need", "problem", "scenario", "scene", "question", "workflow", "text",
            "summary", "description", "audience", "segment", "role", "limitation")
    parts = [signal.get(key) for key in keys]
    parts.extend(_as_list(signal.get("tags")))
    return _norm(" ".join(str(x) for x in parts if isinstance(x, (str, int, float)) and str(x).strip()))


def _explicit_ids(signal: Dict[str, Any], kind: str) -> set[str]:
    keys = {
        "value": ("value_ids", "matched_value_ids", "product_values"),
        "audience": ("audience_ids", "matched_audience_ids", "segments"),
        "strength": ("strength_ids", "matched_strength_ids"),
        "limitation": ("limitation_ids", "matched_limitation_ids"),
    }[kind]
    result: set[str] = set()
    for key in keys:
        result.update(str(x) for x in _as_list(signal.get(key)) if str(x).strip())
    fit = signal.get("product_fit")
    if isinstance(fit, dict):
        for key in keys:
            result.update(str(x) for x in _as_list(fit.get(key)) if str(x).strip())
    return result


def _matches(entries: List[Dict[str, Any]], signal: Dict[str, Any], kind: str,
             signal_text: str, signal_terms: set[str]) -> List[Dict[str, Any]]:
    explicit = _explicit_ids(signal, kind)
    matched = []
    for entry in entries:
        id_match = entry["id"] in explicit
        tag_match = bool(entry["tags"] & signal_terms)
        term_match = bool(entry["terms"] & signal_terms)
        text = entry["text"]
        contain_match = bool(text and len(text) >= 2 and
                             (text in signal_text or (len(signal_text) >= 2 and signal_text in text)))
        if id_match or tag_match or term_match or contain_match:
            matched.append(entry)
    return matched


def _refs(signal: Dict[str, Any]) -> List[str]:
    refs: List[str] = []
    for key in ("source_ref", "source", "evidence_ref"):
        value = signal.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            refs.append(str(value).strip())
    for key in ("source_refs", "evidence_refs", "sources"):
        for value in _as_list(signal.get(key)):
            if isinstance(value, dict):
                value = value.get("source_ref") or value.get("ref") or value.get("url")
            if isinstance(value, (str, int, float)) and str(value).strip():
                refs.append(str(value).strip())
    return list(dict.fromkeys(refs))


def _commercial(signal: Dict[str, Any]) -> Dict[str, Any]:
    evidence = signal.get("commercial_evidence")
    observed = False
    refs: List[str] = []
    if isinstance(evidence, dict):
        kind = _norm(evidence.get("kind") or evidence.get("type"))
        eligible = kind in ("purchase_intent", "willingness_to_pay", "price_anchor", "paid", "payment")
        refs = _refs(evidence)
        observed = bool(eligible and _bool(evidence.get("observed")) and refs)
    # 快捷字段也必须有自己的来源，抱怨文本里的“想买”不会被扫描升级。
    if not observed and _bool(signal.get("purchase_intent")) and _refs(signal):
        observed = True
        refs = _refs(signal)
    return {
        "status": "observed" if observed else "not_observed",
        "evidence_refs": refs,
        "note": "求助、抱怨、竞品不满和重复提问都不是购买意愿；只有带来源的明确商业证据才单列标记。",
    }


def _product_issue(kind: str, signal: Dict[str, Any],
                   limitations: List[Dict[str, Any]]) -> bool:
    blocker = _norm(signal.get("blocker_type") or signal.get("issue_type"))
    explicit = _bool(signal.get("product_issue")) or blocker in ("product", "product_issue", "产品")
    blocking_limit = any(_bool(item["raw"].get("blocking")) for item in limitations)
    return bool(explicit or (kind == "inactive_trial" and blocking_limit))


def _action(kind: str, signal: Dict[str, Any], match: Dict[str, Any],
            product_issue: bool, refs: List[str]) -> Dict[str, Any]:
    score = match["score"]
    if product_issue:
        severity = _norm(signal.get("severity"))
        pause = (_bool(signal.get("pause_acquisition")) or severity in ("critical", "fatal")
                 or (severity in ("high", "严重") and _bool(signal.get("blocks_activation"))))
        code = "pause_acquisition" if pause else "fix_product"
        reason = ("本轮存在明确的产品阻断；先停止扩大流量，修复并验证激活路径"
                  if pause else "本轮存在明确的产品问题；先定位并修复，不能靠增加内容或获客掩盖")
        step = ("保留现有服务对象，暂停新增获客；用原失败对象复测关键任务"
                if pause else "复现阻断，修复后让原试用对象重新完成关键任务")
    elif not refs or match["status"] == "unmatched" or not _signal_text(signal):
        code = "collect_evidence"
        reason = "来源、需求描述或产品匹配仍不完整；先补证，不把弱信号当增长机会"
        step = "补同一角色、场景、任务与现有替代方案的原始记录，并核对产品限制"
    elif kind == "inactive_trial":
        blocker = _norm(signal.get("blocker_type"))
        if blocker in ("onboarding", "handoff", "content", "承接", "引导") and score >= 50:
            code = "generate_asset"
            reason = "未激活信号明确指向引导或承接，先做一个可复现的激活资产"
            step = "生成最短激活教程，观察试用者是否完成首个关键任务"
        else:
            code = "collect_evidence"
            reason = "试用未激活本身不能判定是产品、报价、人群还是承接问题"
            step = "回看激活路径并访谈未激活者，先确认阻断类型"
    elif kind == "repeated_question" and score >= 50:
        occurrences = signal.get("occurrences", signal.get("count", 1))
        try:
            repeated = int(occurrences) >= 2
        except (TypeError, ValueError):
            repeated = True
        code = "generate_asset" if repeated else "give_solution"
        reason = ("同一问题重复出现且与产品价值匹配，可沉淀成资产；重复次数仍不代表付费"
                  if repeated else "问题与产品价值匹配，先直接回答并观察任务完成情况")
        step = "制作带来源、复现步骤和限制的内容资产，以任务完成而非阅读量验收"
    elif kind == "new_workflow" and score >= 50:
        code = "generate_asset" if _bool(signal.get("reusable")) else "give_solution"
        reason = "观察到的工作流与产品价值匹配，先验证能否帮助用户完成同一任务"
        step = "复现新工作流并记录前置条件；可复用后再沉淀模板或教程"
    elif score >= 50:
        code = "give_solution"
        reason = "问题与产品价值匹配，先给具体方案并验证结果，不直接推断购买"
        step = "针对原场景给出可执行方案，记录用户是否完成任务及仍存在的阻断"
    else:
        code = "collect_evidence"
        reason = "只有部分人群或优势匹配，尚不足以证明这是产品价值机会"
        step = "补充需求与核心价值之间的直接证据，再决定是否投入资产"
    return {"code": code, "label": _ACTION_LABELS[code], "reason": reason,
            "next_step": step, "automatic_apply": False}


def _cost_risk_validation(action: str, signal: Dict[str, Any],
                          limitations: List[Dict[str, Any]], refs: List[str]) -> Tuple[Dict[str, Any], List[str], Dict[str, Any]]:
    level = {"generate_asset": "medium", "fix_product": "high",
             "pause_acquisition": "high", "give_solution": "low",
             "collect_evidence": "low"}[action]
    drivers = {
        "generate_asset": ["需要复现、人审与真实任务验证"],
        "fix_product": ["需要工程定位、修复和回归验证"],
        "pause_acquisition": ["承担修复成本与暂停新增流量的机会成本"],
        "give_solution": ["需要人工核对方案与场景"],
        "collect_evidence": ["需要补访谈、原始记录或行为证据"],
    }[action]
    risks = ["问题信号不等于购买意愿，不能据此预测收入或放量"]
    if not refs:
        risks.append("缺少可回溯来源，当前只能作为待核实线索")
    for item in limitations:
        risks.append(f"产品限制：{item['text'] or item['id']}")
    for risk in _as_list(signal.get("risks")):
        if str(risk).strip():
            risks.append(str(risk))
    methods = {
        "generate_asset": ("让目标用户仅按资产完成复现；观察 task_completed/activation，"
                           "成功以真实任务事件为准，失败则记录阻断，不用曝光验收"),
        "give_solution": ("在原场景交付方案并观察用户任务是否完成；未完成任务即保留反证，"
                          "另设访谈确认是否存在付费依据"),
        "fix_product": ("用修复前失败的同一关键任务做回归；只有真实 activation 事件恢复才算通过"),
        "pause_acquisition": ("暂停新增流量，待原阻断对象能完成关键任务并有真实 activation 记录后再评审恢复"),
        "collect_evidence": ("补齐角色×场景×任务×替代方案×后果；寻找满意替代、无问题和明确拒绝等反证"),
    }
    return ({"level": level, "drivers": drivers},
            list(dict.fromkeys(risks)),
            {"method": methods[action], "evidence_refs": refs,
             "value_event": "activation" if action in ("fix_product", "pause_acquisition") else "task_completed",
             "not_a_success_metric": ["impression", "view", "reach", "sent"]})


def discover(product_facts: Dict[str, Any], signals: Any) -> Dict[str, Any]:
    """返回稳定排序的机会候选；空输入返回空候选而非编造机会。"""
    facts = product_facts if isinstance(product_facts, dict) else {}
    product = _product_entries(facts)
    signal_items, warnings = _signal_items(signals)
    candidates: List[Dict[str, Any]] = []
    for index, (kind, signal) in enumerate(signal_items, 1):
        text = _signal_text(signal)
        terms = _tokens(text) | {_norm(x) for x in _as_list(signal.get("tags")) if _norm(x)}
        matched = {name: _matches(items, signal, name, text, terms)
                   for name, items in product.items()}
        score = min(100, (50 if matched["value"] else 0)
                    + (25 if matched["audience"] else 0)
                    + (15 if matched["strength"] else 0)
                    + (10 if matched["value"] and matched["audience"] and matched["strength"] else 0))
        if matched["value"] and score >= 50:
            status = "matched"
        elif score:
            status = "partial"
        else:
            status = "unmatched"
        match = {
            "status": status, "score": score,
            "value_ids": [x["id"] for x in matched["value"]],
            "audience_ids": [x["id"] for x in matched["audience"]],
            "strength_ids": [x["id"] for x in matched["strength"]],
            "limitation_ids": [x["id"] for x in matched["limitation"]],
            "basis": ("显式 ID、标签或确定性的文字包含匹配；分数只表示产品事实匹配，不表示需求强度或收入概率"),
        }
        refs = _refs(signal)
        issue = _product_issue(kind, signal, matched["limitation"])
        action = _action(kind, signal, match, issue, refs)
        cost, risks, validation = _cost_risk_validation(action["code"], signal,
                                                        matched["limitation"], refs)
        need = str(_first(signal, ("need", "problem", "question", "text", "summary")) or "").strip()
        signal_id = str(signal.get("id") or signal.get("signal_id") or f"signal-{index}")
        digest = hashlib.sha1(f"{kind}|{signal_id}|{need}|{index}".encode("utf-8")).hexdigest()[:12]
        explanation = [f"信号类型：{_SIGNAL_LABELS[kind]}；来源：{len(refs)} 条可回溯引用"]
        if match["value_ids"]:
            explanation.append("匹配产品价值：" + "、".join(match["value_ids"]))
        if match["audience_ids"]:
            explanation.append("匹配目标人群：" + "、".join(match["audience_ids"]))
        if not match["value_ids"]:
            explanation.append("尚未匹配核心价值，不能把问题热度当成产品机会")
        if issue:
            explanation.append("存在本轮产品问题，产品修复优先于生成资产或扩大获客")
        candidates.append({
            "id": f"opp-{digest}", "signal_id": signal_id, "signal_type": kind,
            "need": need, "scenario": signal.get("scenario") or signal.get("scene") or "",
            "audience": signal.get("audience") or signal.get("segment") or "",
            "demand_match": match, "explanation": explanation,
            "evidence": {"refs": refs, "source_status": "traceable" if refs else "missing",
                         "occurrences": signal.get("occurrences", signal.get("count", 1)),
                         "note": "出现次数只描述样本内频次，不证明市场普遍性或商业需求"},
            "commercial_intent": _commercial(signal),
            "product_issue": issue, "next_action": action,
            "cost": cost, "risks": risks, "validation": validation,
        })
    action_rank = {"pause_acquisition": 0, "fix_product": 1, "collect_evidence": 2,
                   "give_solution": 3, "generate_asset": 4}
    candidates.sort(key=lambda c: (0 if c["product_issue"] else 1,
                                   action_rank[c["next_action"]["code"]],
                                   -c["demand_match"]["score"], c["id"]))
    return {
        "schema_version": "1.0", "kind": "growovo.opportunity_discovery",
        "candidates": candidates,
        "summary": {
            "signals": len(signal_items), "candidates": len(candidates),
            "product_issues": sum(1 for c in candidates if c["product_issue"]),
            "priority_action": candidates[0]["next_action"] if candidates else None,
        },
        "warnings": warnings,
        "boundary": ("候选是待验证判断，不是购买、市场规模或因果结论；匹配分不代表需求强度。"
                     "产品阻断优先于获客；曝光和发送不能作为价值验证。"),
    }


# 主线程可按动作语义选用 discover 或 analyze，行为完全一致。
analyze = discover
