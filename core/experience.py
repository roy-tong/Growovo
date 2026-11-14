#!/usr/bin/env python3
"""用户经验库：别人真跑过之后写下来的东西，带出处与样本量。

为什么单独一层：平台官方规则只告诉你「不能做什么」，用户经验才告诉你
「怎么做才有用」——限流看哪个指标、标题粘到搜索框验证、删笔记会掉权重……
这些在官方文档里永远找不到。

使用纪律：
  support 只数独立来源，不等于资格，也不等于独立复现。
  support =1 只能生成实验；官方规则与自有实测也不能被改写为普遍规律。
  旧 kind 仅用于展示，进入策略必须按 knowledge_type 通过 eligible()。
"""
from __future__ import annotations

import json
import sysconfig
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DATA = Path(__file__).resolve().parents[1] / "docs" / "playbooks" / "经验库" / "experience.jsonl"
if not DATA.is_file():
    DATA = Path(sysconfig.get_path("data")) / "share" / "growovo" / "experience.jsonl"
CORPUS = DATA.parent / "corpus"

# 用户经验会过时：平台规则和玩法两三个月就变一轮。
# 所以每条经验必须带发布时间，超过窗口的自动标记为「待刷新」。
FRESH_DAYS = 60
MIN_SUPPORT = 2     # 少于两篇独立提到的，只能算线索
_CACHE: Optional[List[Dict[str, Any]]] = None


# ---------------------------------------------------------------- 经验资格
#
# ``kind`` 是旧库的人工提炼/逐字复现/话题展示分类，不能代表证据资格。
# 新写入策略的记录必须显式带 knowledge_type，并通过下面的逐轨校验。
OFFICIAL_RULE = "official_rule"
REPRODUCIBLE_METHOD = "reproducible_method"
SINGLE_EXPERT = "single_expert_experience"
SHORT_TERM_TACTIC = "short_term_tactic"
FIRST_PARTY_TEST = "own_test"
OWN_TEST = FIRST_PARTY_TEST
KNOWLEDGE_TYPES = (
    OFFICIAL_RULE, REPRODUCIBLE_METHOD, SINGLE_EXPERT,
    SHORT_TERM_TACTIC, FIRST_PARTY_TEST,
)
KNOWLEDGE_ALIASES = {
    "official": OFFICIAL_RULE,
    "official-rule": OFFICIAL_RULE,
    "官方规则": OFFICIAL_RULE,
    "reproducible": REPRODUCIBLE_METHOD,
    "reproducible-method": REPRODUCIBLE_METHOD,
    "可复现方法": REPRODUCIBLE_METHOD,
    "single_expert": SINGLE_EXPERT,
    "expert_experience": SINGLE_EXPERT,
    "单篇资深经验": SINGLE_EXPERT,
    "short_term": SHORT_TERM_TACTIC,
    "tactic": SHORT_TERM_TACTIC,
    "短期技巧": SHORT_TERM_TACTIC,
    "first_party_test": FIRST_PARTY_TEST,
    "first-party": FIRST_PARTY_TEST,
    "自有实测": FIRST_PARTY_TEST,
}
# 不同知识不能使用同一个 60 天窗口。尤其短期技巧必须更快衰减。
TRACK_FRESH_DAYS = {
    OFFICIAL_RULE: 365,
    REPRODUCIBLE_METHOD: 180,
    SINGLE_EXPERT: 180,
    SHORT_TERM_TACTIC: 14,
    FIRST_PARTY_TEST: 90,
}
RIGHTS_CLEARED = {
    "owned", "licensed", "permission_granted", "permitted", "cleared",
    "public_domain", "fair_use", "official", "official_terms",
    "cc0", "cc_by", "cc_by_sa",
}
CURRENT_STATES = {"current", "effective", "active", "in_effect", "valid"}
INACTIVE_STATES = {"expired", "stale", "retracted", "superseded", "invalid", "archived"}
_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"}


def _first(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def normalize_knowledge_type(value: Any) -> str:
    """Return the canonical track name, or an empty string for unknown types."""
    key = str(value or "").strip().lower()
    return KNOWLEDGE_ALIASES.get(key, key if key in KNOWLEDGE_TYPES else "")


def canonicalize_url(value: Any) -> str:
    """Validate an HTTP(S) canonical URL without following it or reading credentials."""
    raw = str(value or "").strip()
    if not raw or any(ch.isspace() for ch in raw):
        return ""
    try:
        u = urlsplit(raw)
        if u.scheme.lower() not in ("http", "https") or not u.hostname:
            return ""
        if u.username is not None or u.password is not None:
            return ""
        host = (u.hostname or "").lower()
        if u.port:
            host = f"{host}:{u.port}"
        query = urlencode([(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
                           if k.lower() not in _TRACKING_PARAMS])
        path = u.path or "/"
        return urlunsplit((u.scheme.lower(), host, path, query, ""))
    except (TypeError, ValueError):
        return ""


def _rights_review(row: Dict[str, Any], today: date) -> Dict[str, Any]:
    """权利布尔值只是旧自报；正式资格需要依据、确认人、时间和审查记录。"""
    value = row.get("rights", row.get("rights_status"))
    detail = value if isinstance(value, dict) else {}
    status = (_first(detail, "status", "license") if detail else value)
    if not status:
        status = row.get("rights_status")
    status = str(status or "").strip().lower().replace("-", "_")
    basis = str(_first(detail, "basis", "rights_basis") or row.get("rights_basis") or status).strip()
    confirmed_by = str(_first(detail, "confirmed_by", "reviewer") or row.get("rights_confirmed_by") or "").strip()
    confirmed_at = _first(detail, "confirmed_at", "reviewed_at") or row.get("rights_confirmed_at")
    evidence_ref = str(_first(detail, "evidence_ref", "record_ref", "review_ref")
                       or row.get("rights_evidence_ref") or "").strip()
    issues: List[str] = []
    if status not in RIGHTS_CLEARED or not basis:
        issues.append("没有明确可用的权利依据")
    if not confirmed_by:
        issues.append("权利依据缺少确认人 rights_confirmed_by")
    confirmed_date = _as_date(confirmed_at)
    if not confirmed_at or confirmed_date is None:
        issues.append("权利依据缺少有效确认时间 rights_confirmed_at")
    elif confirmed_date > today:
        issues.append("权利确认时间在未来")
    if not evidence_ref:
        issues.append("权利依据缺少可追溯记录 rights_evidence_ref")
    grade = "traceable_review" if not issues else ("internal_self_report" if status in RIGHTS_CLEARED else "unreviewed")
    return {
        "ok": not issues, "grade": grade, "status": status or "unknown", "basis": basis,
        "confirmed_by": confirmed_by, "confirmed_at": confirmed_at,
        "evidence_ref": evidence_ref, "issues": issues,
        "legacy_flag_ignored": row.get("rights_cleared") is True,
    }


def _rights_ok(row: Dict[str, Any]) -> bool:
    # 兼容旧内部调用；没有确认时间上下文时按今天审查，布尔值仍不放行。
    return _rights_review(row, date.today())["ok"]


def _relevance_score(row: Dict[str, Any]) -> float:
    value = row.get("relevance", row.get("relevant"))
    if isinstance(value, dict):
        value = value.get("score", value.get("status"))
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value) if 0 <= float(value) <= 1 else 0.0
    labels = {
        "high": 1.0, "relevant": 1.0, "related": 1.0, "platform_match": 1.0,
        "medium": 0.5, "partial": 0.5, "low": 0.25,
        "irrelevant": 0.0, "unrelated": 0.0, "unreviewed": 0.0,
    }
    return labels.get(str(value or "").strip().lower(), 0.0)


def _domain(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        return str(urlsplit(raw).hostname or "").rstrip(".")
    return raw.split("/", 1)[0].split(":", 1)[0].rstrip(".")


def _evidence_domains(row: Dict[str, Any], canonical: str) -> List[str]:
    values = [canonical] + _independent_sources(row, canonical)
    return sorted({domain for domain in (_domain(value) for value in values) if domain})


def _human_review(row: Dict[str, Any], today: date, canonical: str) -> Dict[str, Any]:
    raw_records: List[Dict[str, Any]] = []
    for value in (row.get("human_review"), row.get("review_record")):
        if isinstance(value, dict):
            raw_records.append(value)
    records = row.get("review_records")
    if isinstance(records, (list, tuple)):
        raw_records.extend(value for value in records if isinstance(value, dict))
    expected_domains = _evidence_domains(row, canonical)
    assessments: List[Dict[str, Any]] = []
    for index, review in enumerate(raw_records, 1):
        reviewer = str(review.get("reviewer") or review.get("confirmed_by") or "").strip()
        reviewed_at = review.get("reviewed_at") or review.get("confirmed_at")
        record_ref = str(review.get("record_ref") or review.get("review_ref")
                         or review.get("evidence_ref") or "").strip()
        review_type = str(review.get("review_type") or review.get("kind") or "traceable").strip().lower()
        domains = sorted({domain for domain in (_domain(value) for value in
                         (review.get("source_domains") or row.get("source_domains") or [])) if domain})
        issues: List[str] = []
        if str(review.get("status") or "").strip().lower() not in ("approved", "reviewed", "passed"):
            issues.append("复核记录 status 不是 approved/reviewed/passed")
        if not reviewer:
            issues.append("复核记录缺少 reviewer")
        reviewed_date = _as_date(reviewed_at)
        if not reviewed_at or reviewed_date is None:
            issues.append("复核记录缺少有效 reviewed_at")
        elif reviewed_date > today:
            issues.append("复核时间在未来")
        if not record_ref:
            issues.append("复核记录缺少 record_ref")
        if not domains:
            issues.append("复核记录缺少 source_domains")
        missing_domains = [domain for domain in expected_domains if domain not in domains]
        if missing_domains:
            issues.append("复核记录未覆盖来源域: " + ", ".join(missing_domains))
        if review_type in ("self_report", "internal_self_report", "internal"):
            issues.append("内部自报不是可追溯审查")
        assessments.append({
            "id": str(review.get("id") or f"review-{index}"), "reviewer": reviewer,
            "reviewed_at": reviewed_at, "record_ref": record_ref,
            "source_domains": domains, "issues": issues, "traceable": not issues,
        })
    if row.get("human_reviewed") is False:
        for assessment in assessments:
            assessment["traceable"] = False
            assessment["issues"] = list(dict.fromkeys(assessment["issues"] + ["human_reviewed=false 与复核记录冲突"]))
    traceable = [item for item in assessments if item["traceable"]]
    internal = [item for item in assessments
                if str(item.get("reviewer") or "").strip() and item.get("reviewed_at")]
    if traceable:
        grade = "traceable_review"
    elif internal:
        grade = "internal_self_report"
    else:
        grade = "unreviewed"
    issues: List[str] = []
    if not assessments:
        issues.append("只有 human_reviewed 布尔值或没有复核记录；需要署名、时间、record_ref 和来源域")
    elif not traceable:
        # 选信息最完整的一条解释缺口；其余记录仍全部披露。
        best = sorted(assessments, key=lambda item: len(item["issues"]))[0]
        issues.extend(best["issues"] or ["复核记录不可追溯"])
    return {"grade": grade, "records": assessments, "issues": issues,
            "source_domains": expected_domains,
            "legacy_flag_ignored": row.get("human_reviewed") is True}


def _human_reviewed(row: Dict[str, Any]) -> bool:
    # 旧 helper 保持存在，但也必须满足正式可追溯审查。
    canonical = canonicalize_url(row.get("canonical_url"))
    return _human_review(row, date.today(), canonical)["grade"] == "traceable_review"


def _asr_used(row: Dict[str, Any]) -> bool:
    value = row.get("asr", row.get("is_asr", False))
    if isinstance(value, dict):
        value = value.get("used", value.get("enabled", False))
    return value is True or str(value).strip().lower() in ("1", "true", "yes")


def _as_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        if re.fullmatch(r"\d{10}(?:\.\d+)?", text):
            return datetime.fromtimestamp(float(text), tz=timezone.utc).date()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).date() if "T" in text or " " in text else date.fromisoformat(text[:10])
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _source_key(value: Any) -> str:
    if isinstance(value, dict):
        value = (value.get("canonical_url") or value.get("url") or
                 value.get("source_id") or value.get("id") or "")
    return canonicalize_url(value) or str(value or "").strip()


def _independent_sources(row: Dict[str, Any], canonical: str) -> List[str]:
    raw = row.get("independent_sources")
    # Official rules and a deliberately single-source expert note can name their
    # canonical item directly. Other tracks must enumerate independent sources.
    kind = normalize_knowledge_type(row.get("knowledge_type"))
    if raw is None and kind in (OFFICIAL_RULE, SINGLE_EXPERT):
        raw = [canonical]
    if not isinstance(raw, (list, tuple, set)):
        return []
    return sorted({key for key in (_source_key(x) for x in raw) if key})


def _support(row: Dict[str, Any]) -> int:
    try:
        value = int(row.get("support", 1))
        return value if value >= 0 else 0
    except (TypeError, ValueError):
        return 0


def _official(row: Dict[str, Any]) -> bool:
    if row.get("official") is True or row.get("official_authority"):
        return True
    if str(row.get("source_type", "")).lower() in ("official", "platform_official"):
        return True
    authority = row.get("authority")
    return isinstance(authority, dict) and authority.get("official") is True and bool(authority.get("name"))


def _verified_author_and_scenario(row: Dict[str, Any]) -> bool:
    author, scenario = row.get("author"), row.get("scenario")
    author_ok = bool(str(author or "").strip()) and row.get("author_verified") is True
    scenario_ok = bool(str(scenario or "").strip()) and row.get("scenario_verified") is True
    if isinstance(author, dict):
        author_ok = (author.get("verified") is True and
                     bool(author.get("name") or author.get("profile_url") or author.get("id")))
    if isinstance(scenario, dict):
        scenario_ok = (scenario.get("verified") is True and
                       bool(scenario.get("description") or scenario.get("context")))
    return author_ok and scenario_ok


def _independently_reproduced(row: Dict[str, Any]) -> bool:
    value = row.get("independent_reproductions", row.get("reproductions"))
    if value is None:
        value = row.get("independent_reproduced")
    if value is True:
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return value >= 1
    if isinstance(value, (list, tuple, set)):
        return len({_source_key(x) for x in value if _source_key(x)}) >= 1
    sources = row.get("independent_sources") or []
    return (isinstance(sources, (list, tuple)) and
            sum(isinstance(x, dict) and x.get("reproduced") is True for x in sources) >= 2)


def _sample_size(row: Dict[str, Any]) -> int:
    value = row.get("sample", row.get("sample_size"))
    if isinstance(value, dict):
        value = value.get("count", value.get("n"))
    try:
        return int(value) if not isinstance(value, bool) and int(value) > 0 else 0
    except (TypeError, ValueError):
        return 0


def _cost_present(row: Dict[str, Any]) -> bool:
    value = row.get("cost")
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return value >= 0
    if isinstance(value, dict):
        amount = value.get("amount", value.get("value"))
        try:
            return amount is not None and float(amount) >= 0
        except (TypeError, ValueError):
            return False
    return False


def assess_eligibility(row: Dict[str, Any], *, purpose: str = "strategy",
                       on: Optional[Union[str, date]] = None) -> Dict[str, Any]:
    """Explain whether one record may be used for strategy or only an experiment.

    This function is deliberately conservative: missing provenance never inherits
    eligibility from ``kind='rule'`` or from a large ``support`` number.
    """
    purpose = str(purpose or "strategy").lower()
    if purpose in ("content", "playbook"):
        purpose = "strategy"
    if purpose not in ("strategy", "experiment", "generalization"):
        raise ValueError("purpose 必须是 strategy / experiment / generalization")
    today = _as_date(on) or date.today()
    kind = normalize_knowledge_type(row.get("knowledge_type"))
    reasons: List[str] = []
    if not kind:
        reasons.append("缺少或无法识别 knowledge_type")
    canonical = canonicalize_url(row.get("canonical_url"))
    if not canonical:
        reasons.append("缺少有效 canonical_url")
    rights_review = _rights_review(row, today)
    reasons.extend(rights_review["issues"])
    if _relevance_score(row) < 0.5:
        reasons.append("相关性不足或尚未复核")
    human_review = _human_review(row, today, canonical)
    if purpose in ("strategy", "generalization"):
        if human_review["grade"] != "traceable_review":
            reasons.extend(human_review["issues"])
            if human_review["grade"] == "internal_self_report":
                reasons.append("内部自报只能生成限定实验，正式策略需要可追溯审查")
    elif human_review["grade"] not in ("traceable_review", "internal_self_report"):
        reasons.extend(human_review["issues"])
    # Transcript-like content cannot silently masquerade as human-written text.
    origin = str(row.get("origin") or row.get("source_type") or "").lower()
    if ("asr" in origin or "transcript" in origin) and "asr" not in row and "is_asr" not in row:
        reasons.append("转写内容缺少 ASR 标记")

    status = str(row.get("status") or "").strip().lower()
    if row.get("expired") is True or status in INACTIVE_STATES:
        reasons.append("记录已过期、撤回或被替代")
    valid_from = _as_date(row.get("valid_from"))
    if row.get("valid_from") and valid_from is None:
        reasons.append("valid_from 日期无效")
    elif valid_from and valid_from > today:
        reasons.append("记录尚未生效")
    valid_until = _as_date(row.get("valid_until") or row.get("expires_at"))
    if (row.get("valid_until") or row.get("expires_at")) and valid_until is None:
        reasons.append("有效期日期无效")
    elif valid_until and valid_until < today:
        reasons.append("记录已超过有效期")

    if kind:
        date_fields = {
            OFFICIAL_RULE: ("current_verified_at", "verified_at", "reviewed_at", "published"),
            FIRST_PARTY_TEST: ("tested_at", "observed_at", "published"),
        }.get(kind, ("published", "observed_at", "tested_at"))
        observed = next((_as_date(row.get(f)) for f in date_fields if _as_date(row.get(f))), None)
        if observed is None:
            reasons.append("缺少可判断新鲜度的有效日期")
        elif observed > today:
            reasons.append("来源日期在未来")
        else:
            try:
                window = int(row.get("freshness_days") or TRACK_FRESH_DAYS[kind])
                if window <= 0 or (kind == SHORT_TERM_TACTIC and window > 30):
                    raise ValueError
                if (today - observed).days > window:
                    reasons.append(f"超过 {kind} 的 {window} 天新鲜度窗口")
            except (TypeError, ValueError):
                reasons.append("freshness_days 无效")

    sources = _independent_sources(row, canonical)
    support = _support(row)
    generalizable = False
    if kind == OFFICIAL_RULE:
        current = row.get("currently_effective", row.get("current", row.get("effective")))
        if current is False or (current is not True and status not in CURRENT_STATES):
            reasons.append("官方规则没有当前有效标记")
        if not _official(row):
            reasons.append("没有官方权威身份证据")
        if len(sources) != 1 or support != 1:
            reasons.append("官方规则必须只对应一个官方权威来源")
        generalizable = True
    elif kind == REPRODUCIBLE_METHOD:
        if not str(row.get("mechanism") or "").strip():
            reasons.append("可复现方法缺少机制解释")
        if support < MIN_SUPPORT or len(sources) < MIN_SUPPORT:
            reasons.append("可复现方法至少需要两个独立来源")
        if not _independently_reproduced(row):
            reasons.append("缺少独立复现证据")
        generalizable = support >= MIN_SUPPORT and len(sources) >= MIN_SUPPORT
    elif kind == SINGLE_EXPERT:
        if support != 1 or len(sources) != 1:
            reasons.append("单篇资深经验必须保持单篇来源边界")
        if not _verified_author_and_scenario(row):
            reasons.append("作者或场景真实性尚未核验")
        if purpose == "strategy":
            reasons.append("单篇资深经验只能生成实验，不能直接进入普遍策略")
    elif kind == SHORT_TERM_TACTIC:
        generalizable = support >= MIN_SUPPORT and len(sources) >= MIN_SUPPORT
        if purpose == "strategy" and not generalizable:
            reasons.append("support=1 的短期技巧只能生成实验，不能当普遍规律")
    elif kind == FIRST_PARTY_TEST:
        if not str(row.get("action") or "").strip():
            reasons.append("自有实测缺少动作")
        if "result" not in row or row.get("result") in (None, ""):
            reasons.append("自有实测缺少结果")
        if _sample_size(row) < 1:
            reasons.append("自有实测缺少有效样本量")
        if not _cost_present(row):
            reasons.append("自有实测缺少成本")
        # First-party evidence is useful in its own context, not a universal law.
        generalizable = False

    if purpose == "generalization" and not generalizable:
        reasons.append("该记录只能限定场景使用，不能普遍化")
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "knowledge_type": kind,
        "purpose": purpose,
        "canonical_url": canonical,
        "support": support,
        "independent_sources": sources,
        "asr": _asr_used(row),
        "generalizable": generalizable,
        "rights_review": rights_review,
        "review_grade": human_review["grade"],
        "review_records": human_review["records"],
        "source_domains": human_review["source_domains"],
        "experiment_only": (kind == SINGLE_EXPERT or (kind == SHORT_TERM_TACTIC and support < MIN_SUPPORT)
                            or human_review["grade"] == "internal_self_report"),
    }


def is_eligible(row: Dict[str, Any], *, purpose: str = "strategy",
                on: Optional[Union[str, date]] = None) -> bool:
    return assess_eligibility(row, purpose=purpose, on=on)["eligible"]


def eligible(subject: Any = None, *, rows: Optional[Iterable[Dict[str, Any]]] = None,
             pid: Optional[str] = None, platform: Optional[str] = None,
             purpose: str = "strategy", knowledge_type: Optional[str] = None,
             on: Optional[Union[str, date]] = None) -> Any:
    """Query eligible records, or return a boolean when passed one record.

    ``eligible('reddit')`` reads the bundled library; ``eligible(rows=...)``
    filters an imported collection; ``eligible(record)`` is a convenience gate.
    """
    if isinstance(subject, dict):
        return is_eligible(subject, purpose=purpose, on=on)
    if isinstance(subject, str):
        pid = subject
    elif subject is not None:
        if rows is not None:
            raise ValueError("rows 不能重复提供")
        rows = subject
    pid = pid or platform
    if pid:
        from .platforms import BY_ID
        if pid not in BY_ID:
            for key, value in BY_ID.items():
                if pid == value.get("name") or pid in value.get("aliases", ()):
                    pid = key
                    break
    wanted = normalize_knowledge_type(knowledge_type) if knowledge_type else ""
    if knowledge_type and not wanted:
        return []
    out = []
    for row in (load() if rows is None else rows):
        if pid and row.get("platform") != pid:
            continue
        if wanted and normalize_knowledge_type(row.get("knowledge_type")) != wanted:
            continue
        if is_eligible(row, purpose=purpose, on=on):
            out.append(row)
    return out


def load(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    global _CACHE
    if path is None and _CACHE is not None:
        return _CACHE
    p = Path(path) if path else DATA
    rows: List[Dict[str, Any]] = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if path is None:
        _CACHE = rows
    return rows


def for_platform(pid: str) -> List[Dict[str, Any]]:
    """这个平台的用户经验，按样本量排序。"""
    from .platforms import BY_ID
    if pid not in BY_ID:
        for k, v in BY_ID.items():
            if pid == v.get("name") or pid in v.get("aliases", ()):
                pid = k
                break
    rows = [r for r in load() if r["platform"] == pid]
    rows.sort(key=lambda r: -int(r.get("support") or 0))
    return rows


def graded(pid: str) -> Dict[str, List[Dict[str, Any]]]:
    """按证据强度分三档，方案里不能混用：

    rule      人工提炼、有出处、可当依据
    verbatim  多篇文章里逐字复现，等于用户自己反复确认
    topic     很多篇都在谈这个主题——只说明它是关键变量，不说明某种做法有效
    """
    rows = for_platform(pid)
    out = {"rule": [], "verbatim": [], "topic": []}
    for r in rows:
        kind = r.get("kind") or ("verbatim" if int(r.get("support") or 0) >= 2 else "topic")
        out.setdefault(kind, []).append(r)
    return out


def coverage() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in load():
        out[r["platform"]] = out.get(r["platform"], 0) + 1
    return dict(sorted(out.items(), key=lambda x: -x[1]))


def render(pid: str) -> str:
    from .platforms import BY_ID
    name = BY_ID.get(pid, {}).get("name", pid)
    g = graded(pid)
    if not any(g.values()):
        return f"# {name} · 用户经验\n\n还没有收集到，别编。"
    lines = [f"# {name} · 用户经验", "",
             "> 这是旧语料的展示分级，不是策略资格。只有 canonical URL、权利、相关性、"
             "独立来源、人工复核和新鲜度均通过 eligible() 的记录才能进入策略；"
             "单篇资深经验只能生成实验。", ""]
    if g["rule"]:
        lines.append("## 人工提炼（历史展示，资格另审）")
        lines.append("")
        for i, r in enumerate(g["rule"], 1):
            lines.append(f"{i}. **{r['claim']}**（{r['support']} 篇提到）")
            lines.append(f"   - 所以：{r['action']}")
            lines.append(f"   - 出处：{'；'.join(r.get('sources') or [])}")
            lines.append("")
    if g.get("verbatim"):
        lines.append("## 多篇文章里复现的做法")
        lines.append("")
        for r in g["verbatim"][:8]:
            lines.append(f"- **{r['claim']}**（{r['support']} 篇）→ {r['action']}")
        lines.append("")
    if g.get("topic"):
        lines.append("## 话题观察（说明这是关键变量，不代表做法有效）")
        lines.append("")
        for r in g["topic"][:8]:
            n = r.get("topic_articles") or "?"
            lines.append(f"- {r['claim']}（{n} 篇谈到）")
        lines.append("")
    return "\n".join(lines)


def as_prompt_lines(pid: str, limit: int = 6) -> List[str]:
    """给策略/内容生产用的短句；旧 kind 不再是资格通行证。"""
    return [r["action"] for r in eligible(pid)[:limit]
            if str(r.get("action") or "").strip()]


# ---------------------------------------------------------------- 新鲜度与维护

def _age_days(published: Optional[str]) -> Optional[int]:
    if not published:
        return None
    from datetime import date
    try:
        d = date.fromisoformat(str(published)[:10])
    except ValueError:
        return None
    return (date.today() - d).days


def freshness(pid: Optional[str] = None) -> Dict[str, Any]:
    """这批经验的时效：最新一条多久之前、多少条还在窗口内、中位年龄。"""
    rows = load()
    if pid:
        rows = [r for r in rows if r["platform"] == pid]
    ages = sorted(a for a in (_age_days(r.get("published")) for r in rows) if a is not None)
    in_window = [a for a in ages if a <= FRESH_DAYS]
    return {
        "claims": len(rows),
        "dated": len(ages),
        "undated": len(rows) - len(ages),
        "fresh_claims": len(in_window),
        "newest_days": ages[0] if ages else None,
        "median_days": ages[len(ages) // 2] if ages else None,
        "oldest_days": ages[-1] if ages else None,
        "window_days": FRESH_DAYS,
        "meets_window": bool(ages) and len(in_window) >= max(1, len(rows) // 2),
    }


def stale(window: int = FRESH_DAYS) -> List[Dict[str, Any]]:
    """哪些平台该重新采了。没有日期的也算待刷新——无法证明它新。"""
    from .platforms import BY_ID
    out = []
    for pid in sorted({r["platform"] for r in load()}):
        rows = [r for r in load() if r["platform"] == pid]
        ages = sorted(a for a in (_age_days(r.get("published")) for r in rows) if a is not None)
        need = (not ages) or (ages[0] > window)
        out.append({
            "platform": pid,
            "name": BY_ID.get(pid, {}).get("name", pid),
            "claims": len(rows),
            "newest_days": ages[0] if ages else None,
            "needs_refresh": need,
            "why": ("没有发布时间，无法证明是新的" if not ages
                    else f"最新一条已是 {ages[0]} 天前，超过 {window} 天窗口"),
        })
    return out


def maintenance_plan(window: int = FRESH_DAYS) -> Dict[str, Any]:
    """维护计划：哪些平台要重采、采多少、多久一次。"""
    s = stale(window)
    todo = [x for x in s if x["needs_refresh"]]
    return {
        "window_days": window,
        "min_support": MIN_SUPPORT,
        "target_per_platform": 50,
        "refresh_cadence_days": window // 2,
        "platforms_needing_refresh": todo,
        "command": "python3 scripts/harvest_experience.py --platform <id> --pages 50",
        "note": ("经验是主观的，样本不足就不该当结论：单平台至少 50 条、每条至少 2 篇独立来源、"
                 "且发布时间在窗口内，三个条件同时满足才算可信"),
    }
