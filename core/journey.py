#!/usr/bin/env python3
"""Journey：在同一 campaign × subject × time_window 内诊断真实进展。

六阶段为 discovery → engagement → trial → activation → retention → payment。
所有阶段共用 ``filter_events``，不会拿同主体的历史 campaign 补本轮漏斗。
近似事件、缺主体事件、未知事件名和未核验价值事件都单列披露，不洗白成进展。

只读、无网络、无模型调用、无文件写入。诊断是描述性建议，不证明渠道因果。
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

STAGES = ("discovery", "engagement", "trial", "activation", "retention", "payment")
STAGE_LABELS = {
    "discovery": "发现", "engagement": "互动", "trial": "试用",
    "activation": "激活", "retention": "持续使用", "payment": "付费",
}
_STAGE_ALIASES = {
    "discovery": "discovery", "discovered": "discovery", "signal": "discovery",
    "impression": "discovery", "view": "discovery", "visit": "discovery", "发现": "discovery",
    "engagement": "engagement", "interaction": "engagement", "reply": "engagement",
    "response": "engagement", "question": "engagement", "click": "engagement", "互动": "engagement",
    "trial": "trial", "trial_started": "trial", "signup": "trial", "sign_up": "trial",
    "registration": "trial", "试用": "trial",
    "activation": "activation", "activated": "activation", "激活": "activation",
    "retention": "retention", "retained": "retention", "retained_use": "retention",
    "continued_use": "retention", "repeat_use": "retention", "ongoing_use": "retention",
    "持续使用": "retention",
    "payment": "payment", "paid": "payment", "purchase": "payment",
    "subscription_started": "payment", "付费": "payment",
}
_VALUE_STAGES = {"activation", "retention", "payment"}
_VERIFIED_VALUE_STAGES = {"activation", "payment"}


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "是")


def _false(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    return str(value or "").strip().lower() in ("0", "false", "no", "n", "否")


def _first(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _stage(event: Dict[str, Any]) -> str:
    raw = str(_first(event, "event_type", "type", "stage", "event") or "").strip().lower()
    raw = raw.replace("-", "_").replace(" ", "_")
    return _STAGE_ALIASES.get(raw, "")


def _subject(event: Dict[str, Any]) -> str:
    return str(_first(event, "subject", "subject_id", "user_id", "distinct_id") or "").strip()


def _campaign(event: Dict[str, Any]) -> str:
    return str(_first(event, "campaign", "utm_campaign") or "").strip()


def _timestamp(event: Dict[str, Any]) -> Any:
    return _first(event, "ts", "timestamp", "time", "date", "created_at", "observed_at")


def _parse_datetime(value: Any, *, end_of_date: bool = False,
                    wall_time: bool = False) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.max if end_of_date else time.min)
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if len(text) == 10:
            day = date.fromisoformat(text)
            parsed = datetime.combine(day, time.max if end_of_date else time.min)
        else:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
    else:
        raise ValueError(f"无效时间: {value!r}")
    if parsed.tzinfo is not None:
        # 无偏移窗口按记录里的本地日历时间过滤（例如 11-01 要包含 +08:00
        # 的 11-01 00:00）；窗口带偏移时则统一换算到 UTC 比较。
        parsed = (parsed.replace(tzinfo=None) if wall_time else
                  parsed.astimezone(timezone.utc).replace(tzinfo=None))
    return parsed


def _aware(value: Any) -> bool:
    if isinstance(value, datetime):
        return value.tzinfo is not None
    if isinstance(value, str) and len(value.strip()) > 10:
        text = value.strip()
        if text.endswith("Z"):
            return True
        try:
            return datetime.fromisoformat(text).tzinfo is not None
        except ValueError:
            return False
    return False


def _window(time_window: Any) -> Tuple[datetime, datetime, Dict[str, Any], bool]:
    if isinstance(time_window, dict):
        start, end = time_window.get("start"), time_window.get("end")
    elif isinstance(time_window, (list, tuple)) and len(time_window) == 2:
        start, end = time_window
    else:
        raise ValueError("time_window 必须是 {start,end} 或二元组")
    if start in (None, "") or end in (None, ""):
        raise ValueError("time_window.start/end 不能为空；本轮诊断不能用无界历史代替")
    if _aware(start) != _aware(end):
        raise ValueError("time_window.start/end 必须使用一致的时区口径")
    aware = _aware(start)
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end, end_of_date=True)
    if start_dt > end_dt:
        raise ValueError("time_window.start 不能晚于 end")
    return start_dt, end_dt, {"start": start, "end": end}, aware


def _subjects(subject: Any) -> Optional[set[str]]:
    if subject is None:
        return None
    values = [subject] if isinstance(subject, (str, int, float)) else list(subject)
    return {str(value).strip() for value in values if str(value).strip()}


def filter_events(events: Iterable[Dict[str, Any]], *, campaign: str,
                  subject: Any = None, time_window: Any) -> List[Dict[str, Any]]:
    """统一范围过滤，保留原记录及输入顺序，不做阶段或真伪升级。

    ``campaign`` 必须显式提供；``subject`` 可为单主体、主体列表或 None（本轮全体）。
    起止时间都包含在窗口内；日期形式的 end 包含当天。无偏移窗口按事件本地
    日历时间比较，带偏移窗口统一换算到 UTC。无时间或坏时间记录不进本轮。
    """
    campaign = str(campaign or "").strip()
    if not campaign:
        raise ValueError("campaign 不能为空：禁止把跨 campaign 历史混成本轮进展")
    start, end, _, aware = _window(time_window)
    subjects = _subjects(subject)
    selected: List[Dict[str, Any]] = []
    if isinstance(events, dict) and isinstance(events.get("events"), (list, tuple)):
        iterable = events["events"]
    elif isinstance(events, dict):
        iterable = [events] if any(key in events for key in ("event_type", "type", "stage", "event")) else []
    else:
        iterable = events or []
    for event in iterable:
        if not isinstance(event, dict) or event.get("kind") == "count":
            continue
        if _campaign(event) != campaign:
            continue
        event_subject = _subject(event)
        if subjects is not None and event_subject not in subjects:
            continue
        try:
            ts = _parse_datetime(_timestamp(event), wall_time=not aware)
        except (TypeError, ValueError):
            continue
        if start <= ts <= end:
            selected.append(event)
    return selected


def _event_ref(event: Dict[str, Any], index: int) -> str:
    return str(_first(event, "id", "event_id", "source_ref", "ref", "import_key")
               or f"event-{index}")


def _approximated(event: Dict[str, Any]) -> bool:
    return any(_bool(event.get(key)) for key in ("approximated", "is_approximation", "approximate"))


def _source_ref(event: Dict[str, Any]) -> str:
    return str(_first(event, "source_ref", "evidence_ref") or "").strip()


def _verified(event: Dict[str, Any]) -> bool:
    verification = event.get("verification")
    if isinstance(verification, dict):
        status = str(verification.get("status") or "verified").strip().lower()
        return (status in ("verified", "confirmed", "passed")
                and bool(str(verification.get("method") or "").strip())
                and bool(str(verification.get("ref") or "").strip()))
    return event.get("verified") is True and bool(_source_ref(event))


def _exclusion_reason(stage: str, event: Dict[str, Any]) -> str:
    if not _subject(event):
        return "缺 subject：不能计入主体进展"
    if _approximated(event):
        return "近似事件：只披露，不计入本轮真实进展"
    if stage in _VALUE_STAGES and not _source_ref(event):
        return "价值事件缺 source_ref：不计入确认进展"
    if stage in _VERIFIED_VALUE_STAGES and not _verified(event):
        return "activation/paid 缺有效 verification(method,ref)：不计入确认进展"
    return ""


def _issue_in_scope(issue: Dict[str, Any], campaign: str, subjects: Optional[set[str]],
                    start: datetime, end: datetime, aware: bool) -> bool:
    issue_campaign = _campaign(issue)
    if issue_campaign and issue_campaign != campaign:
        return False
    issue_subject = _subject(issue)
    if subjects is not None and issue_subject and issue_subject not in subjects:
        return False
    raw_ts = _timestamp(issue)
    if raw_ts not in (None, ""):
        try:
            ts = _parse_datetime(raw_ts, wall_time=not aware)
        except (TypeError, ValueError):
            return False
        if not start <= ts <= end:
            return False
    return True


def _product_issues(selected: List[Dict[str, Any]], supplied: Iterable[Dict[str, Any]],
                    campaign: str, subjects: Optional[set[str]], start: datetime,
                    end: datetime, aware: bool) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    raw_rows: List[Tuple[Dict[str, Any], str]] = []
    for index, event in enumerate(selected, 1):
        blocker = str(_first(event, "blocker_type", "issue_type") or "").strip().lower()
        if _bool(event.get("product_issue")) or blocker in ("product", "product_issue", "产品"):
            raw_rows.append((event, _event_ref(event, index)))
    issue_items = [supplied] if isinstance(supplied, dict) else (supplied or [])
    for index, issue in enumerate(issue_items, 1):
        if not isinstance(issue, dict):
            continue
        status = str(issue.get("status") or "open").strip().lower()
        if status in ("resolved", "closed", "fixed", "done") or issue.get("resolved") is True:
            continue
        if _issue_in_scope(issue, campaign, subjects, start, end, aware):
            raw_rows.append((issue, str(_first(issue, "id", "issue_id", "source_ref", "ref")
                                      or f"issue-{index}")))
    seen = set()
    for issue, fallback_ref in raw_rows:
        ref = str(_first(issue, "id", "issue_id", "source_ref", "ref") or fallback_ref)
        if ref in seen:
            continue
        seen.add(ref)
        severity = str(issue.get("severity") or "unknown").strip().lower()
        pause = (_bool(issue.get("pause_acquisition")) or severity in ("critical", "fatal")
                 or (severity in ("high", "严重") and _bool(issue.get("blocks_activation"))))
        rows.append({
            "ref": ref,
            "summary": str(_first(issue, "summary", "problem", "text", "description")
                           or "本轮存在明确产品问题"),
            "severity": severity, "source_ref": _source_ref(issue),
            "campaign": _campaign(issue) or campaign,
            "subject": _subject(issue), "pause_acquisition": pause,
            "scope_note": "未带 campaign 的显式 product_issues 按调用方提供的本轮全局问题处理",
        })
    rows.sort(key=lambda row: row["ref"])
    return rows


def _recommendation(code: str, label: str, area: str, reason: str,
                    next_step: str, refs: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"code": code, "label": label, "area": area, "reason": reason,
            "next_step": next_step, "evidence_refs": refs or [],
            "requires_human": True, "automatic_apply": False}


def _breakpoint_recommendation(breakpoint: Optional[Dict[str, Any]],
                               wrong_audience: bool, insufficient: bool) -> Dict[str, Any]:
    if insufficient or breakpoint is None:
        return _recommendation(
            "collect_evidence", "补证", "measurement",
            "本轮没有可确认的主体进展，不能据此责怪人群、内容、产品或报价",
            "先补事件主体、真实时间和来源；区分近似记录与已核验价值事件")
    current = breakpoint["from_stage"]
    refs = list(breakpoint.get("event_refs") or [])
    subjects = breakpoint["subjects"]
    if wrong_audience and current in ("discovery", "engagement"):
        return _recommendation(
            "fix_audience", "修人群", "audience",
            f"{len(subjects)} 个主体停在前段，且本轮记录明确标记人群不匹配",
            "暂停扩量，重新定义目标角色与场景，再用少量合格主体验证", refs)
    mapping = {
        "discovery": ("fix_content", "修内容", "content",
                      "有发现但没有可确认互动，先检查内容是否回答了当下任务",
                      "用同一人群测试更具体的问题、步骤和预期结果，不以曝光量验收"),
        "engagement": ("fix_handoff", "修承接", "handoff",
                       "有互动但没有可确认试用，先检查从问题到试用的承接",
                       "缩短到首个可复现任务的路径，明确试用入口、前置条件和下一步"),
        "trial": ("fix_product", "修产品", "product",
                  "有试用但没有可确认激活，先排查首次关键任务阻断",
                  "逐个复现未激活路径；区分产品、引导和人群原因后先修阻断"),
        "activation": ("fix_product", "修产品", "product",
                       "激活后没有可确认持续使用，先检查核心价值是否重复发生",
                       "观察第二次真实任务，定位价值、稳定性或使用频率问题"),
        "retention": ("fix_pricing", "修报价", "pricing",
                      "有持续使用但没有可确认付费，先验证报价与付费主体，而非继续加流量",
                      "访谈使用者和付款者，测试价格、计费单位与采购门槛，并保留拒绝证据"),
    }
    code, label, area, reason, step = mapping[current]
    return _recommendation(code, label, area, reason, step, refs)


def diagnose(events: Iterable[Dict[str, Any]], *, campaign: str, subject: Any = None,
             time_window: Any, product_issues: Iterable[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """返回本轮漏斗、逐主体轨迹、断点和有优先级的修复建议。

    断点按每个主体的最远离谱阶段计算，后续阶段已发生时不补造前序断点。
    明确产品问题始终优先于人群/内容/承接/报价建议；高危激活阻断建议暂停获客。
    """
    campaign = str(campaign or "").strip()
    start, end, window, aware = _window(time_window)
    subjects_filter = _subjects(subject)
    selected = filter_events(events, campaign=campaign, subject=subject, time_window=time_window)
    quality: Dict[str, List[Dict[str, Any]]] = {
        "approximated": [], "unverified_value_events": [],
        "missing_subjects": [], "unknown_events": [],
    }
    records: List[Dict[str, Any]] = []
    raw_stage_counts = {stage: 0 for stage in STAGES}
    observed_subjects = {stage: set() for stage in STAGES}
    confirmed_subjects = {stage: set() for stage in STAGES}
    wrong_audience = False

    for index, event in enumerate(selected, 1):
        stage = _stage(event)
        ref = _event_ref(event, index)
        if not stage:
            quality["unknown_events"].append({
                "event_ref": ref, "event_type": _first(event, "event_type", "type", "stage", "event"),
                "reason": "未知或近似名称不做语义猜测，未映射进漏斗",
            })
            continue
        raw_stage_counts[stage] += 1
        sub = _subject(event)
        if sub and not _approximated(event):
            observed_subjects[stage].add(sub)
        reason = _exclusion_reason(stage, event)
        record = {
            "event_ref": ref, "stage": stage, "subject": sub,
            "ts": _timestamp(event), "source_ref": _source_ref(event),
            "approximated": _approximated(event), "confirmed": not reason,
            "exclusion_reason": reason, "raw": event,
        }
        records.append(record)
        if not reason:
            confirmed_subjects[stage].add(sub)
        elif _approximated(event):
            quality["approximated"].append({k: record[k] for k in
                                             ("event_ref", "stage", "subject", "ts", "source_ref")} |
                                            {"reason": reason})
        elif not sub:
            quality["missing_subjects"].append({"event_ref": ref, "stage": stage, "reason": reason})
        else:
            quality["unverified_value_events"].append({k: record[k] for k in
                                                        ("event_ref", "stage", "subject", "ts", "source_ref")} |
                                                       {"reason": reason})
        if (_false(event.get("audience_match")) or _false(event.get("icp_match"))
                or str(event.get("fit") or "").strip().lower() in ("wrong_audience", "audience_mismatch")):
            wrong_audience = True

    funnel = []
    for stage in STAGES:
        confirmed_ids = sorted(confirmed_subjects[stage])
        funnel.append({
            "stage": stage, "label": STAGE_LABELS[stage],
            "events": raw_stage_counts[stage], "subjects": len(confirmed_ids),
            "subject_ids": confirmed_ids,
            "observed_subjects": len(observed_subjects[stage]),
            "excluded_events": raw_stage_counts[stage] - sum(
                1 for record in records if record["stage"] == stage and record["confirmed"]),
        })

    all_subjects = sorted({record["subject"] for record in records if record["subject"]})
    traces: List[Dict[str, Any]] = []
    breakpoints_by_stage: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for sub in all_subjects:
        own = [record for record in records if record["subject"] == sub]
        confirmed = [record for record in own if record["confirmed"]]
        reached = [stage for stage in STAGES if any(record["stage"] == stage for record in confirmed)]
        current = reached[-1] if reached else None
        next_stage = STAGES[STAGES.index(current) + 1] if current and current != STAGES[-1] else None
        current_records = [record for record in confirmed if record["stage"] == current]
        trace = {
            "subject": sub, "current_stage": current,
            "current_stage_label": STAGE_LABELS.get(current, "未确认"),
            "next_stage": next_stage,
            "reached_stages": reached,
            "missing_prior_stages": [stage for stage in STAGES[:STAGES.index(current)] if stage not in reached]
                                    if current else [],
            "progress_event_refs": [record["event_ref"] for record in current_records],
            "excluded_event_refs": [record["event_ref"] for record in own if not record["confirmed"]],
        }
        traces.append(trace)
        if current and next_stage:
            key = (current, next_stage)
            bucket = breakpoints_by_stage.setdefault(key, {
                "from_stage": current, "to_stage": next_stage,
                "from_label": STAGE_LABELS[current], "to_label": STAGE_LABELS[next_stage],
                "subjects": [], "event_refs": [],
            })
            bucket["subjects"].append(sub)
            bucket["event_refs"].extend(trace["progress_event_refs"])

    breakpoints = list(breakpoints_by_stage.values())
    for item in breakpoints:
        item["event_refs"] = list(dict.fromkeys(item["event_refs"]))
        item["subjects"].sort()
    breakpoints.sort(key=lambda item: STAGES.index(item["from_stage"]))
    primary = None
    if breakpoints:
        primary = sorted(breakpoints, key=lambda item: (-len(item["subjects"]),
                                                        STAGES.index(item["from_stage"])))[0]

    issues = _product_issues(selected, product_issues, campaign, subjects_filter, start, end, aware)
    confirmed_count = sum(1 for record in records if record["confirmed"])
    insufficient = confirmed_count == 0
    # 全部主体都到付费时 primary 为空，但这不是“无数据”。
    complete = bool(traces) and all(trace["current_stage"] == "payment" for trace in traces)
    base = _breakpoint_recommendation(primary, wrong_audience, insufficient)
    if not insufficient and primary is None and traces and all(trace["current_stage"] == "payment" for trace in traces):
        base = _recommendation(
            "no_breakpoint", "继续观察", "none",
            "本轮所有有记录主体均已出现可确认付费，没有需要硬造的断点",
            "继续观察持续使用、退款与拒绝证据；本轮描述不构成放量或因果证明")

    recommendations: List[Dict[str, Any]] = []
    if issues:
        refs = [issue["source_ref"] or issue["ref"] for issue in issues]
        pause = any(issue["pause_acquisition"] for issue in issues)
        recommendations.append(_recommendation(
            "fix_product", "修产品", "product",
            "本轮有明确产品问题，优先于修人群、内容、承接或报价" + ("；暂停新增获客" if pause else ""),
            "先复现并修复产品阻断，让原受阻对象重新完成关键任务；未通过前不要扩大流量", refs))
        if base["code"] not in ("fix_product", "collect_evidence") and not pause:
            recommendations.append(base)
        elif base["code"] == "collect_evidence":
            recommendations.append(base)
    else:
        recommendations.append(base)

    status = ("no_data" if not selected else
              "insufficient_data" if insufficient else ("complete" if complete else "active"))
    return {
        "schema_version": "1.0", "kind": "growovo.journey",
        "filters": {"campaign": campaign, "subject": subject,
                    "subjects": sorted(subjects_filter) if subjects_filter is not None else None,
                    "time_window": window},
        "scope": {"selected_events": len(selected), "known_stage_events": len(records),
                  "confirmed_events": confirmed_count},
        "funnel": funnel, "subject_journeys": traces, "breakpoints": breakpoints,
        "primary_breakpoint": primary,
        "product_issues": issues,
        "acquisition": {
            "should_pause": any(issue["pause_acquisition"] for issue in issues),
            "reason": "明确产品阻断未解除前，新增流量只会扩大失败试用" if issues
                      else "是否扩量仍需独立实验与价值事件，不由本描述性漏斗决定",
        },
        "status": status,
        "priority_recommendation": recommendations[0],
        "recommendations": recommendations, "quality": quality,
        "boundary": ("所有阶段使用同一 campaign、subject、time_window；历史 campaign 不补本轮进展。"
                     "近似与未核验价值事件只披露不确认；漏斗是描述性记账，不证明因果、购买意愿或渠道效果。"),
    }


analyze = diagnose
