"""Deterministic audience/content quality, experiments and outcome funnels."""
from __future__ import annotations

import copy
import re
from collections import Counter, defaultdict


SCHEMA = "growovo.execution-quality/1"
FUNNEL_SCHEMA = "growovo.outcome-funnel/1"
ALLOCATION_SCHEMA = "growovo.experiment-allocation/1"
PRODUCT_KEYWORDS = {
    "askovo": {"decision", "evidence", "review", "assumption", "research", "validation",
               "experiment", "evaluation", "compliance", "history", "source", "contradiction"},
    "growovo": {"agent", "workflow", "operations", "execution", "recovery", "automation",
                "growth", "observability", "task", "retry", "receipt", "dashboard", "deployment"},
    "verovo": {"billing", "cost", "usage", "meter", "invoice", "token", "audit", "verification",
               "conformance", "log", "charge", "counter", "spend", "reconciliation", "bill"},
}
ACTIONABLE = {"fixture", "compare", "test", "record", "measure", "reproduce", "review", "check",
              "recalculate", "inspect", "verify", "run", "replay", "evaluate", "preserve", "track"}
EVIDENCE_WORDS = {"fixture", "example", "input", "output", "accepted", "result", "evidence", "dataset",
                  "counter", "invoice", "commit", "log", "snapshot", "receipt"}
EVENT_TYPES = ("committed", "qualified_interaction", "asset_click", "first_run", "accepted_result",
               "follower", "star", "second_use")


def _words(value):
    return set(re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", str(value or "").lower()))


def communication_brief(item, document):
    product = str(item.get("product") or "")
    context = str((item.get("target") or {}).get("context") or "").lower()
    if any(word in context for word in ("billing", "invoice", "cost", "usage", "token", "charge", "spend")):
        hook = "recalculate_together"
        parts = ["对方日志或输入", "具体复现步骤", "可比较输出", "低门槛联合复算邀请"]
        cta = "joint_recalculation"
    elif any(word in context for word in ("fail", "error", "timeout", "bug", "crash", "abort")):
        hook = "reproduce_failure"
        parts = ["失败输入", "具体复现步骤", "预期与实际结果", "低门槛修复邀请"]
        cta = "review_fixture"
    elif any(word in context for word in ("decision", "launch", "approve", "roadmap", "adopt")):
        hook = "review_offer"
        parts = ["旧决定或假设", "一条新证据", "复审输出", "低门槛决定复审邀请"]
        cta = "run_decision_replay"
    elif any(word in context for word in ("benchmark", "score", "leaderboard", "evaluation", "compare")):
        hook = "compare_accepted_results"
        parts = ["同一输入", "具体复现步骤", "接受结果与成本", "低门槛复测邀请"]
        cta = "compare_results"
    else:
        hook = "task_offer"
        parts = ["对方的具体任务", "一个可执行帮助", "可观察结果", "低门槛首任务邀请"]
        cta = "run_asset"
    primary = str(((document.get("outcomes") or {}).get(product) or {}).get("primary", {}).get("id") or "")
    return {"hook_type": hook, "required_parts": parts,
            "cta": primary or cta, "causal_status": "hypothesis_not_proven"}


def evaluate_item(item, document):
    product = str(item.get("product") or "")
    target = item.get("target") or {}
    content = item.get("content") or {}
    context = str(target.get("context") or "")
    text = str(content.get("text") or "")
    qualification = target.get("qualification") or {}
    issues = []

    target_score = 0
    if qualification.get("persona") and qualification.get("source") and qualification.get("observed_at"):
        target_score += 10
    else:
        issues.append("qualification_evidence_incomplete")
    if len(context) >= 30:
        target_score += 10
    else:
        target_score += 3
        issues.append("thin_target_context")
    relevant = _words(context) & PRODUCT_KEYWORDS.get(product, set())
    if len(relevant) >= 2:
        target_score += 20
    elif relevant:
        target_score += 12
    else:
        issues.append("low_task_relevance")
    if target.get("url") and target.get("handle"):
        target_score += 5
    target_score = min(40, target_score)

    content_score = 0
    context_words = _words(context)
    text_words = _words(text)
    overlap = (context_words & text_words) - {"agent", "model", "task", "result", "would", "should"}
    if len(overlap) >= 2:
        content_score += 20
    elif len(overlap) == 1:
        content_score += 10
    else:
        issues.append("generic_content")
    if text_words & ACTIONABLE:
        content_score += 15
    else:
        issues.append("actionable_help_missing")
    if text_words & EVIDENCE_WORDS:
        content_score += 10
    if len(text) >= 120:
        content_score += 10
    elif len(text) < 60:
        issues.append("content_too_thin")
    low = text.lower()
    if "i would" in low or "robust" in low or "innovate" in low or "game-changer" in low:
        content_score -= 15
        issues.append("template_language")
    if item.get("action") == "reply" and "github.com/roy-tong" not in low:
        content_score += 5
    if item.get("action") == "publish":
        assets = (document.get("assets") or {}).get(product) or {}
        url = str((assets.get("primary") or {}).get("url") or "")
        if url and url in text:
            content_score += 10
        if len(text) <= 280:
            content_score += 5
    content_score = max(0, min(60, content_score))
    score = max(0, min(100, target_score + content_score))
    return {"schema": SCHEMA, "score": score, "target_score": target_score,
            "content_score": content_score, "tier": "A" if score >= 80 else "B" if score >= 65 else "C",
            "issues": list(dict.fromkeys(issues)),
            "communication": communication_brief(item, document),
            "evidence_status": "heuristic_not_outcome_evidence"}


def metric_rate(evidence, key):
    actions = int(evidence.get("actions") or 0)
    if actions <= 0 or key not in evidence:
        return None
    return round(float(evidence[key]) / actions, 6)


def _distribute(rows, total):
    if not rows or total <= 0:
        return {row["id"]: 0 for row in rows}
    priorities = {row["id"]: max(0.01, float(row["quality_score"])) for row in rows}
    denominator = sum(priorities.values())
    raw = {key: total * value / denominator for key, value in priorities.items()}
    counts = {key: int(value) for key, value in raw.items()}
    left = total - sum(counts.values())
    order = sorted(rows, key=lambda row: (-(raw[row["id"]] - counts[row["id"]]), row["id"]))
    for row in order[:left]:
        counts[row["id"]] += 1
    return counts


def allocate(payload):
    total = int(payload.get("total_actions") or 0)
    floor = int(payload.get("min_quality_score", 65))
    ratio = float(payload.get("explore_ratio", 0.2))
    if total <= 0:
        raise ValueError("total_actions 必须大于 0")
    if not 0 <= ratio <= 1:
        raise ValueError("explore_ratio 必须在 0 和 1 之间")
    rows = []
    for candidate in payload.get("candidates") or []:
        row = dict(candidate)
        evidence = row.get("evidence") or {}
        rates = {key: metric_rate(evidence, key)
                 for key in ("qualified_interactions", "first_runs", "accepted_results")}
        observed = [value for value in rates.values() if value is not None]
        outcome_score = sum(observed) / len(observed) if observed else 0.5
        row["outcome_rates"] = rates
        row["priority_score"] = round(float(row.get("quality_score") or 0) * (0.5 + outcome_score), 4)
        if float(row.get("quality_score") or 0) < floor:
            row.update(allocation=0, mode="excluded", reason="quality_below_floor")
        else:
            row["mode"] = "exploit" if float(evidence.get("accepted_results") or 0) > 0 else "explore"
        rows.append(row)
    eligible = [row for row in rows if row["mode"] != "excluded"]
    if not eligible:
        raise ValueError("no_quality_candidates")
    explore = [row for row in eligible if row["mode"] == "explore"]
    exploit = [row for row in eligible if row["mode"] == "exploit"]
    reserved = int(round(total * ratio)) if explore and exploit else 0
    explore_counts = _distribute(explore, reserved)
    exploit_counts = _distribute(exploit, total - reserved)
    for row in eligible:
        row["allocation"] = (explore_counts if row["mode"] == "explore" else exploit_counts)[row["id"]]
        if row["mode"] == "explore" and not exploit:
            row["allocation"] = 0
    if explore and not exploit:
        explore_counts = _distribute(explore, total)
        for row in explore:
            row["allocation"] = explore_counts[row["id"]]
        reserved = total
    return {"schema": ALLOCATION_SCHEMA, "total_actions": total,
            "explore_reserved": reserved, "candidates": rows,
            "evidence_status": "allocation_hypothesis_not_performance_prediction"}


def attach_funnel_evidence(payload, events, complete_observation=False):
    document = copy.deepcopy(payload)
    projection = project_funnel(events, complete_observation=complete_observation)
    groups = {row["key"].get("campaign_id"): row for row in projection["groups"]}
    for candidate in document.get("candidates") or []:
        group = groups.get(candidate.get("id"))
        if not group:
            continue
        counts = group["counts"]
        candidate["evidence"] = {"actions": counts.get("committed", 0)}
        for key in EVENT_TYPES[1:]:
            if complete_observation or counts.get(key, 0):
                candidate["evidence"][key + "s" if key != "second_use" else key] = counts.get(key, 0)
    return document


def project_funnel(events, complete_observation=False, group_by=("campaign_id", "product", "persona", "hook", "asset")):
    seen = set()
    grouped = defaultdict(list)
    errors = []
    duplicates = 0
    for event in events:
        event_id = str(event.get("event_id") or "")
        if event_id in seen:
            duplicates += 1
            continue
        seen.add(event_id)
        if not event_id or event.get("event_type") not in EVENT_TYPES or not event.get("subject_id"):
            errors.append({"event_id": event_id, "error": "invalid_event"})
            continue
        key = tuple(str(event.get(field) or "") for field in group_by)
        grouped[key].append(event)
    groups = []
    for key, rows in grouped.items():
        counts = Counter(row["event_type"] for row in rows)
        actions = counts["committed"]
        per_100 = {}
        for event_type in EVENT_TYPES[1:]:
            per_100[event_type + "s" if event_type != "second_use" else "second_use"] = (
                round(100 * counts[event_type] / actions, 4)
                if complete_observation and actions else None)
        groups.append({
            "key": dict(zip(group_by, key)), "counts": dict(counts),
            "unique_subjects": {event_type: len({row["subject_id"] for row in rows
                                                  if row["event_type"] == event_type})
                                for event_type in EVENT_TYPES},
            "per_100_actions": per_100,
            "stage_status": {event_type: ("observed" if counts[event_type] else
                                          "observed_zero" if complete_observation else "not_observed")
                             for event_type in EVENT_TYPES},
            "observation_complete": bool(complete_observation),
            "causality": "not_claimed",
        })
    return {"schema": FUNNEL_SCHEMA, "groups": groups, "deduplicated_events": duplicates,
            "errors": errors, "causality": "not_claimed"}
