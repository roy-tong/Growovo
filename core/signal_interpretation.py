#!/usr/bin/env python3
"""Signal Interpretation：校验宿主 Agent 提供的结构化语义候选。

原始帖子、工单和关键词不是需求。本模块不调用模型、不扫描文本来猜情绪或购买
意图，只校验宿主显式提供的语义、证据和接触资格。输出仍是「待验证需求候选」，
不是市场需求、收入预测或触达许可。
"""
from __future__ import annotations

import copy
import hashlib
import re
from typing import Any, Dict, Iterable, List, Tuple


COMMERCIAL_KINDS = {
    "willingness_to_pay", "price_anchor", "paid", "payment",
    "budget_confirmed", "procurement_started",
}


def _list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _first(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "是")


def _refs(value: Any) -> List[str]:
    refs: List[str] = []
    if isinstance(value, dict):
        for key in ("source_ref", "evidence_ref", "ref", "url"):
            item = value.get(key)
            if isinstance(item, (str, int, float)) and str(item).strip():
                refs.append(str(item).strip())
        for key in ("source_refs", "evidence_refs"):
            for item in _list(value.get(key)):
                if isinstance(item, (str, int, float)) and str(item).strip():
                    refs.append(str(item).strip())
    elif isinstance(value, (str, int, float)) and str(value).strip():
        refs.append(str(value).strip())
    return list(dict.fromkeys(refs))


def _source_ref(candidate: Dict[str, Any]) -> str:
    direct = _first(candidate, "source_ref", "evidence_ref")
    if direct:
        return _text(direct)
    return (_refs(candidate.get("source")) or [""])[0]


def _semantic(candidate: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("semantics", "interpretation", "semantic_candidate"):
        value = candidate.get(key)
        if isinstance(value, dict):
            return value
    return candidate


def _commercial(candidate: Dict[str, Any], semantic: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """工具寻求与购买意图分轨；快捷/自报 purchase_intent 永不升级。"""
    risks: List[str] = []
    declared = _first(semantic, "purchase_intent", "self_declared_purchase_intent")
    if declared is None:
        declared = candidate.get("purchase_intent")
    self_declared = bool(declared) and declared is not False
    evidence = _first(semantic, "commercial_evidence", "purchase_evidence")
    if evidence is None:
        evidence = candidate.get("commercial_evidence")
    if isinstance(evidence, dict) and _bool(evidence.get("self_declared")):
        self_declared = True
    if self_declared:
        risks.append("self-declared purchase_intent 只是陈述，不能升级为已观察商业意图")

    result = {
        "status": "self_declared" if self_declared else "not_observed",
        "evidence_refs": [],
        "kind": "",
        "note": "工具寻求、问题严重程度和购买意向文本都不是支付证据；商业意图单列且需要可回溯观察。",
    }
    if not isinstance(evidence, dict):
        return result, risks
    kind = _text(_first(evidence, "kind", "type")).lower()
    refs = _refs(evidence)
    origin = _text(_first(evidence, "evidence_origin", "observation_type")).lower()
    observed = evidence.get("observed") is True
    if kind == "purchase_intent" or origin in ("self_report", "self_declared", "declaration"):
        self_declared = True
        result["status"] = "self_declared"
        result["kind"] = kind
        risks.append("commercial_evidence 仍是自报购买意图，未作为商业升级依据")
        return result, list(dict.fromkeys(risks))
    if observed and kind in COMMERCIAL_KINDS and refs:
        result.update({"status": "observed", "kind": kind, "evidence_refs": refs})
    else:
        risks.append("商业证据缺少受支持类型、observed=true 或可回溯来源，保持 not_observed")
    return result, list(dict.fromkeys(risks))


def _alternative(candidate: Dict[str, Any], semantic: Dict[str, Any],
                 source_ref: str) -> Tuple[Dict[str, Any], List[str], bool]:
    raw = _first(candidate, "alternative", "current_alternative", "substitute")
    if raw is None:
        raw = _first(semantic, "alternative", "current_alternative", "substitute")
    issues: List[str] = []
    if isinstance(raw, str):
        raw = {"name": raw}
    if not isinstance(raw, dict):
        return {"status": "unknown", "name": "", "unsatisfied_reasons": []}, [
            "缺少当前替代方案；不能默认用户没有可用替代"
        ], False
    name = _text(_first(raw, "name", "tool", "method", "description"))
    status = _text(raw.get("status")).lower()
    if not status:
        if raw.get("unsatisfied") is True:
            status = "unsatisfied"
        elif raw.get("satisfied") is True:
            status = "satisfied"
        else:
            status = "unknown"
    reasons: List[Dict[str, str]] = []
    for item in _list(_first(raw, "unsatisfied_reasons", "reasons", "limitations")):
        if isinstance(item, dict):
            reason = _text(_first(item, "reason", "text", "summary"))
            ref = _text(_first(item, "evidence_ref", "source_ref"))
        else:
            reason, ref = _text(item), ""
        ref = ref or _text(_first(raw, "evidence_ref", "source_ref")) or source_ref
        if reason:
            reasons.append({"reason": reason, "evidence_ref": ref})
    if not name:
        issues.append("替代方案缺少 name/description")
    if status not in ("unsatisfied", "satisfied", "unknown"):
        issues.append("替代方案 status 必须是 unsatisfied / satisfied / unknown")
        status = "unknown"
    if status == "unsatisfied" and not reasons:
        issues.append("声称替代方案不满足时，必须给出具体原因")
    if any(not row["evidence_ref"] for row in reasons):
        issues.append("替代方案不满足原因必须可回溯到来源")
    if status == "unknown":
        issues.append("替代方案是否满足仍为 unknown")
    return {
        "name": name, "status": status, "unsatisfied_reasons": reasons,
        "evidence_refs": list(dict.fromkeys(row["evidence_ref"] for row in reasons if row["evidence_ref"])),
    }, issues, status == "satisfied"


def _contact(candidate: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str], bool]:
    raw = _first(candidate, "contact_eligibility", "contactability", "contact")
    if isinstance(raw, bool):
        raw = {"status": "eligible" if raw else "ineligible"}
    if not isinstance(raw, dict):
        return {"status": "unknown", "basis": "", "evidence_ref": ""}, [
            "缺少接触资格；需求候选不自动获得触达资格"
        ], False
    status = _text(_first(raw, "status", "eligibility")).lower()
    if not status and "eligible" in raw:
        status = "eligible" if raw.get("eligible") is True else "ineligible"
    if status in ("not_contactable", "blocked", "dnc", "unauthorized"):
        status = "ineligible"
    if status not in ("eligible", "ineligible", "unknown"):
        status = "unknown"
    basis = _text(_first(raw, "basis", "reason", "permission_basis"))
    ref = _text(_first(raw, "evidence_ref", "source_ref", "ref"))
    issues: List[str] = []
    if status == "eligible" and (not basis or not ref):
        issues.append("可接触资格必须有 basis 与独立 evidence_ref/source_ref")
    if status == "unknown":
        issues.append("接触资格未知，不得进入可执行机会")
    return {"status": status, "basis": basis, "evidence_ref": ref}, issues, status == "ineligible"


def interpret(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """校验一条宿主语义候选；任何缺失都保留为 review/rejected，不猜补。"""
    if not isinstance(candidate, dict):
        raise ValueError("语义候选必须是 dict")
    candidate_id = _text(_first(candidate, "id", "candidate_id", "signal_id")) or "candidate-unknown"
    semantic = _semantic(candidate)
    source_ref = _source_ref(candidate)
    subject_raw = candidate.get("subject", semantic.get("subject"))
    if isinstance(subject_raw, dict):
        subject = {
            "id": _text(_first(subject_raw, "id", "subject_id", "user_id")),
            "role": _text(_first(subject_raw, "role", "audience", "segment")),
        }
    else:
        subject = {"id": _text(subject_raw), "role": _text(semantic.get("audience"))}
    discussion = _text(_first(semantic, "discussion_type", "conversation_type", "classification")).lower()
    discussion = {
        "problem_statement": "problem", "need": "problem", "问题": "problem",
        "technical": "technical_discussion", "tech_discussion": "technical_discussion",
        "技术讨论": "technical_discussion",
    }.get(discussion, discussion)
    problem = _text(_first(semantic, "problem", "problem_statement", "need"))
    problem_ref = _text(_first(semantic, "problem_evidence_ref", "problem_source_ref")) or source_ref
    tool_raw = _first(semantic, "tool_seeking", "tool_seek")
    if isinstance(tool_raw, dict):
        tool_status = _text(_first(tool_raw, "status", "state")).lower()
        tool_ref = _text(_first(tool_raw, "evidence_ref", "source_ref")) or source_ref
    else:
        tool_status, tool_ref = "unknown", ""
    tool_status = {"seeking": "seeking", "tool_seeking": "seeking", "yes": "seeking",
                   "not_seeking": "not_seeking", "no": "not_seeking"}.get(tool_status, tool_status or "unknown")
    alternative, alternative_issues, alternative_satisfied = _alternative(candidate, semantic, source_ref)
    contact, contact_issues, contact_ineligible = _contact(candidate)
    commercial, commercial_risks = _commercial(candidate, semantic)

    issues: List[str] = []
    if not source_ref:
        issues.append("缺少原始帖子/工单的 source_ref")
    if not subject["id"]:
        issues.append("缺少主体 subject.id")
    if not subject["role"]:
        issues.append("缺少主体角色 subject.role")
    if discussion == "technical_discussion":
        issues.append("纯技术讨论不是需求候选，即使出现工具名或购买字样")
    elif discussion != "problem":
        issues.append("discussion_type 必须明确区分 problem 与 technical_discussion")
    if discussion == "problem" and not problem:
        issues.append("problem 候选缺少结构化 problem/problem_statement")
    if problem and not problem_ref:
        issues.append("问题陈述缺少 problem_evidence_ref")
    if tool_status not in ("seeking", "not_seeking"):
        issues.append("tool_seeking 必须显式为 seeking/not_seeking，unknown 不得猜测")
    elif not tool_ref:
        issues.append("工具寻求判断缺少 evidence_ref")
    issues.extend(alternative_issues)
    issues.extend(contact_issues)

    rejected = discussion == "technical_discussion" or contact_ineligible or alternative_satisfied
    if contact_ineligible:
        issues.append("对象不可接触或没有授权，不能进入可执行机会")
    if alternative_satisfied:
        issues.append("现有替代方案已满足，不能编造未满足需求")
    status = "rejected" if rejected else ("needs_review" if issues else "qualified")
    risks = list(dict.fromkeys(commercial_risks + [
        "语义候选由宿主 Agent 提供，确定性校验不等于语义真实性或市场需求证明",
        "可接触资格只表示指定 basis 下可接触，不授权群发或绕过平台限制",
    ]))
    key_text = _text(candidate.get("dedup_key")) or "|".join(
        (source_ref, subject["id"], problem or candidate_id)).lower()
    digest = hashlib.sha256(key_text.encode("utf-8")).hexdigest()[:16]
    signal_type = _text(_first(candidate, "signal_type", "type")) or "help_request"
    opportunity_signal = {
        "id": candidate_id,
        "signal_type": signal_type,
        "need": problem,
        "scenario": _first(semantic, "scenario", "scene") or candidate.get("scenario", ""),
        "audience": subject["role"],
        "source_ref": source_ref,
        "occurrences": candidate.get("occurrences", 1),
        "tags": copy.deepcopy(candidate.get("tags", [])),
        "product_fit": copy.deepcopy(candidate.get("product_fit", {})),
        "interpreted_commercial": copy.deepcopy(commercial),
        "interpretation_id": candidate_id,
    }
    for key in ("value_ids", "audience_ids", "strength_ids", "limitation_ids",
                "product_issue", "severity", "blocks_activation", "blocker_type"):
        if key in candidate:
            opportunity_signal[key] = copy.deepcopy(candidate[key])
    return {
        "id": candidate_id, "dedup_key": digest, "status": status,
        "need_status": "unmet_need_candidate" if status == "qualified" else "not_qualified",
        "source": {"source_ref": source_ref, "status": "traceable" if source_ref else "missing"},
        "subject": subject,
        "semantics": {
            "discussion_type": discussion or "unknown", "problem": problem,
            "problem_evidence_ref": problem_ref,
            "tool_seeking": {"status": tool_status, "evidence_ref": tool_ref},
        },
        "alternative": alternative, "contact_eligibility": contact,
        "commercial_intent": commercial,
        "issues": list(dict.fromkeys(issues)), "risks": risks,
        "duplicate_ids": [], "opportunity_signal": opportunity_signal if status == "qualified" else None,
    }


def _items(candidates: Any) -> List[Dict[str, Any]]:
    if isinstance(candidates, dict):
        raw = _first(candidates, "semantic_candidates", "candidates", "items")
        if raw is None and any(k in candidates for k in ("semantics", "interpretation", "source_ref")):
            raw = [candidates]
    else:
        raw = candidates
    return [row for row in _list(raw) if isinstance(row, dict)]


def interpret_many(candidates: Any) -> Dict[str, Any]:
    """批量校验并按「来源×主体×问题」确定性去重，不合并跨来源独立证据。"""
    rows = [interpret(row) for row in _items(candidates)]
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["dedup_key"], []).append(row)
    output: List[Dict[str, Any]] = []
    rank = {"qualified": 0, "needs_review": 1, "rejected": 2}
    for key in sorted(grouped):
        group = sorted(grouped[key], key=lambda row: (rank[row["status"]], row["id"]))
        row = copy.deepcopy(group[0])
        duplicates = [item["id"] for item in group[1:]]
        row["duplicate_ids"] = sorted(set(duplicates))
        row["duplicate_count"] = len(duplicates)
        if len({item["status"] for item in group}) > 1:
            row["status"] = "needs_review"
            row["need_status"] = "not_qualified"
            row["opportunity_signal"] = None
            row["issues"] = list(dict.fromkeys(row["issues"] + ["重复候选的语义结论冲突，需人工复核"]))
        output.append(row)
    output.sort(key=lambda row: (rank[row["status"]], row["id"]))
    counts = {status: sum(row["status"] == status for row in output)
              for status in ("qualified", "needs_review", "rejected")}
    return {
        "schema_version": "1.0", "kind": "growovo.signal_interpretation",
        "candidates": output,
        "summary": {"input": len(rows), "unique": len(output),
                    "duplicates": len(rows) - len(output), **counts},
        "boundary": ("只校验宿主提供的结构化语义与证据，不从关键词推断需求。"
                     "技术讨论不是需求；工具寻求与自报购买意图都不是已观察商业意图；"
                     "不可接触对象不进入可执行机会。"),
    }


analyze = interpret_many
