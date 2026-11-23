"""Append-only business observations with explicit evidence grades and pair checks."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from .ledger import _append_line, _read_jsonl_fail_loud


GRADES = {"owner_declared", "observed", "independently_verified"}


def path(data_dir) -> Path:
    return Path(data_dir) / "state" / "business-observations.jsonl"


def _metric(value: Any, label: str):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("指标 %s 必须是非负整数或 null" % label)
    if isinstance(value, int):
        if value < 0:
            raise ValueError("指标 %s 不能为负数" % label)
        return value
    if isinstance(value, dict):
        return {str(key): _metric(item, "%s.%s" % (label, key)) for key, item in value.items()}
    raise ValueError("指标 %s 必须是非负整数、null 或指标对象" % label)


def _instant(value: Any, label: str) -> dt.datetime:
    text = str(value or "").strip()
    try:
        stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("%s 必须是 ISO 8601 时间" % label)
    if stamp.tzinfo is None:
        raise ValueError("%s 必须包含时区" % label)
    return stamp


def _evidence(data_dir: Path, entries: Any, prefix: str = "evidence"):
    canonical = []
    issues = []
    if not isinstance(entries, list) or not entries:
        return canonical, [prefix + "_missing"]
    for index, item in enumerate(entries):
        label = "%s[%d]" % (prefix, index)
        if not isinstance(item, dict):
            issues.append(label + "_requires_path_and_sha256")
            continue
        path_text = str(item.get("path") or "").strip()
        declared_hash = str(item.get("sha256") or "").strip().lower()
        if not path_text or not declared_hash:
            issues.append(label + "_requires_path_and_sha256")
            continue
        canonical.append({"path": path_text, "sha256": declared_hash})
        candidate = Path(path_text).expanduser()
        if not candidate.is_absolute():
            candidate = data_dir / candidate
        if not candidate.is_file():
            issues.append(label + "_missing")
            continue
        actual_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual_hash != declared_hash:
            issues.append(label + "_sha256_mismatch")
    return canonical, issues


def _source(data_dir: Path, source: Dict[str, Any], metrics: Dict[str, Any]):
    evidence, issues = _evidence(data_dir, source.get("evidence"))
    raw_subject = source.get("subject") if isinstance(source.get("subject"), dict) else {}
    subject = {key: str(raw_subject.get(key) or "").strip() for key in ("account", "product")}
    for key, value in subject.items():
        if not value:
            issues.append("subject_%s_missing" % key)
    raw_definitions = source.get("metric_definitions")
    raw_definitions = raw_definitions if isinstance(raw_definitions, dict) else {}
    definitions = {key: str(raw_definitions.get(key) or "").strip() for key in metrics}
    for key, value in definitions.items():
        if not value:
            issues.append("metric_definition_%s_missing" % key)

    canonical = {"kind": str(source.get("kind") or "import"), "evidence": evidence,
                 "subject": subject, "metric_definitions": definitions}
    verification = source.get("independent_verification")
    if verification is not None:
        if not isinstance(verification, dict):
            issues.append("independent_verification_invalid")
        else:
            reviewer = str(verification.get("reviewer") or "").strip()
            control_domain = str(verification.get("control_domain") or "").strip()
            authority = str(verification.get("authority") or "").strip()
            verification_result = str(verification.get("verification_result") or "").strip()
            verified_at = str(verification.get("verified_at") or "").strip()
            if not reviewer:
                issues.append("independent_reviewer_missing")
            if control_domain not in {"buyer_controlled", "third_party"}:
                issues.append("independent_control_domain_invalid")
            if not authority:
                issues.append("independent_authority_missing")
            if verification_result != "verified":
                issues.append("independent_result_not_verified")
            try:
                _instant(verified_at, "independent_verification.verified_at")
            except ValueError:
                issues.append("independent_verified_at_invalid")
            review_evidence, review_issues = _evidence(
                data_dir, verification.get("evidence"), prefix="independent_evidence")
            issues.extend(review_issues)
            canonical["independent_verification"] = {
                "reviewer": reviewer, "control_domain": control_domain,
                "authority": authority, "verification_result": verification_result,
                "verified_at": verified_at, "evidence": review_evidence,
            }
    grade = "owner_declared" if issues else (
        "independently_verified" if verification is not None else "observed")
    return canonical, grade, sorted(set(issues))


def import_observation(data_dir, payload: Dict[str, Any], *, night: str, phase: str,
                       by: str, apply: bool = False) -> Dict[str, Any]:
    data_dir = Path(data_dir)
    if not isinstance(payload, dict):
        raise ValueError("业务观察必须是 JSON 对象")
    if phase not in {"baseline", "final"}:
        raise ValueError("业务观察 phase 必须是 baseline/final")
    if not str(night or "").strip() or not str(by or "").strip():
        raise ValueError("业务观察必须有 night 和署名 by")
    observed_at = str(payload.get("observed_at") or "").strip()
    _instant(observed_at, "observed_at")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError("metrics 至少包含一个指标；未知用 null，不要填 0")
    metrics = {str(key): _metric(value, str(key)) for key, value in metrics.items()}
    source = payload.get("source") or {}
    if not isinstance(source, dict):
        raise ValueError("source 必须是对象")
    raw_evidence = source.get("evidence")
    if not isinstance(raw_evidence, list) or not any(str(item).strip() for item in raw_evidence):
        raise ValueError("业务观察必须提供 source.evidence")
    canonical_source, grade, source_issues = _source(data_dir, source, metrics)
    canonical = {"night": night, "phase": phase, "observed_at": observed_at,
                 "observation_pair_id": str(payload.get("observation_pair_id") or "").strip(),
                 "metrics": metrics, "source": canonical_source}
    observation_id = "obs:" + hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    record = dict(canonical, observation_id=observation_id, by=by,
                  observation_grade=grade, source_issues=source_issues,
                  imported_at=dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    existing = [row for row in _read_jsonl_fail_loud(path(data_dir), kind="业务观察")
                if row.get("observation_id") == observation_id]
    duplicate = bool(existing)
    if apply and not duplicate:
        _append_line(path(data_dir), json.dumps(record, ensure_ascii=False) + "\n")
    return {"ok": True, "apply": bool(apply), "duplicate": duplicate,
            "observation_id": observation_id, "path": str(path(data_dir)),
            "observation": record, "causal_credit": "not_claimed"}


def _rows(data_dir, night: str):
    return [row for row in _read_jsonl_fail_loud(path(data_dir), kind="业务观察")
            if row.get("night") == night]


def has_baseline(data_dir, night: str) -> bool:
    return any(row.get("phase") == "baseline" for row in _rows(data_dir, night))


def _value(baseline, final, grade: str):
    if baseline is None or final is None:
        return {"status": "unknown", "baseline": baseline, "final": final, "delta": None}
    return {"status": grade, "baseline": baseline, "final": final, "delta": final - baseline}


def _comparison_id(night, pair_id, baseline, final):
    if not baseline or not final:
        return None
    raw = json.dumps({"night": night, "observation_pair_id": pair_id,
                      "baseline_observation_id": baseline.get("observation_id"),
                      "final_observation_id": final.get("observation_id")},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "cmp:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _superseded(rows, final_index, pair_id, night, baseline, include_current):
    history = []
    for index, row in enumerate(rows[:final_index]):
        if row.get("phase") != "final" or row.get("observation_pair_id") != pair_id:
            continue
        previous = next((item for item in reversed(rows[:index])
                         if item.get("phase") == "baseline"
                         and item.get("observation_pair_id") == pair_id), None)
        if previous:
            history.append({
                "comparison_id": _comparison_id(night, pair_id, previous, row),
                "observation_pair_id": pair_id,
                "baseline_observation_id": previous.get("observation_id"),
                "final_observation_id": row.get("observation_id"),
                "revision_state": "superseded",
            })
    if include_current and baseline:
        history.append({
            "comparison_id": _comparison_id(night, pair_id, baseline, rows[final_index]),
            "observation_pair_id": pair_id,
            "baseline_observation_id": baseline.get("observation_id"),
            "final_observation_id": rows[final_index].get("observation_id"),
            "revision_state": "superseded", "reason": "pair_revision_required",
        })
    unique = {}
    for item in history:
        unique[item["comparison_id"]] = item
    return list(unique.values())


def _invalid(final, baseline, errors, *, night="", history=None):
    pair_id = (final or baseline or {}).get("observation_pair_id")
    return {"status": "invalid", "metrics": {}, "errors": sorted(set(errors)),
            "attribution": "not_claimed", "revision_state": "current",
            "comparison_id": _comparison_id(night, pair_id, baseline, final),
            "superseded_comparisons": history or [],
            "observation_pair_id": pair_id,
            "observation_grade": (final or baseline or {}).get("observation_grade"),
            "baseline_observation_id": (baseline or {}).get("observation_id"),
            "final_observation_id": (final or {}).get("observation_id"),
            "note": "配对不合法，未计算 delta；请用新的 observation_pair_id 导入修订。"}


def compare(data_dir, night: str, pair_id: str = "") -> Dict[str, Any]:
    rows = [row for row in _rows(data_dir, night)
            if not pair_id or row.get("observation_pair_id") == pair_id]
    if not rows:
        return {"status": "not_included", "metrics": {}, "attribution": "not_claimed",
                "note": "没有独立业务观察；committed 不能替代粉丝、star、注册或激活读数。"}
    final_indexes = [index for index, row in enumerate(rows) if row.get("phase") == "final"]
    if not final_indexes:
        baseline = next((row for row in reversed(rows) if row.get("phase") == "baseline"), None)
        return {"status": "unknown", "metrics": {}, "attribution": "not_claimed",
                "observation_grade": (baseline or {}).get("observation_grade"),
                "baseline_observation_id": (baseline or {}).get("observation_id"),
                "final_observation_id": None,
                "note": "缺少 final 观察，未计算 delta。"}

    final_index = final_indexes[-1]
    final = rows[final_index]
    pair_id = str(final.get("observation_pair_id") or "").strip()
    errors = []
    if not pair_id:
        errors.append("observation_pair_id_missing")
    late_baselines = [row for row in rows[final_index + 1:]
                      if row.get("phase") == "baseline"
                      and row.get("observation_pair_id") == pair_id]
    if late_baselines:
        errors.append("pair_revision_required")
    baseline_candidates = [row for row in rows[:final_index]
                           if row.get("phase") == "baseline"
                           and row.get("observation_pair_id") == pair_id]
    if not baseline_candidates:
        errors.append("matching_baseline_missing")
    baseline = baseline_candidates[-1] if baseline_candidates else None

    final_source = final.get("source") if isinstance(final.get("source"), dict) else {}
    baseline_source = (baseline or {}).get("source") if isinstance((baseline or {}).get("source"), dict) else {}
    final_subject = final_source.get("subject") if isinstance(final_source.get("subject"), dict) else {}
    baseline_subject = baseline_source.get("subject") if isinstance(baseline_source.get("subject"), dict) else {}
    for key in ("account", "product"):
        if not final_subject.get(key) or final_subject.get(key) != baseline_subject.get(key):
            errors.append("subject_%s_mismatch" % key)
    base_metrics = (baseline or {}).get("metrics") or {}
    final_metrics = final.get("metrics") or {}
    final_definitions = final_source.get("metric_definitions") or {}
    baseline_definitions = baseline_source.get("metric_definitions") or {}
    for key in sorted(set(base_metrics) | set(final_metrics)):
        if not final_definitions.get(key) or final_definitions.get(key) != baseline_definitions.get(key):
            errors.append("metric_definition_%s_mismatch" % key)
    try:
        baseline_at = _instant((baseline or {}).get("observed_at"), "baseline.observed_at")
        final_at = _instant(final.get("observed_at"), "final.observed_at")
        if baseline_at >= final_at:
            errors.append("observed_at_order_invalid")
    except ValueError:
        errors.append("observed_at_invalid")
    grade = final.get("observation_grade")
    if grade not in GRADES or grade != (baseline or {}).get("observation_grade"):
        errors.append("observation_grade_mismatch")
    history = _superseded(rows, final_index, pair_id, night, baseline,
                          include_current=bool(late_baselines))
    if errors:
        return _invalid(final, baseline, errors, night=night, history=history)

    metrics = {}
    for key in sorted(set(base_metrics) | set(final_metrics)):
        before = base_metrics.get(key)
        after = final_metrics.get(key)
        if isinstance(before, dict) or isinstance(after, dict):
            before = before if isinstance(before, dict) else {}
            after = after if isinstance(after, dict) else {}
            metrics[key] = {name: _value(before.get(name), after.get(name), grade)
                            for name in sorted(set(before) | set(after))}
        else:
            metrics[key] = _value(before, after, grade)

    def comparable(value):
        if isinstance(value, dict) and "status" in value:
            return value["status"] == grade
        if isinstance(value, dict):
            return any(comparable(item) for item in value.values())
        return False

    return {"status": grade if any(comparable(value) for value in metrics.values()) else "unknown",
            "metrics": metrics, "errors": [], "attribution": "not_claimed",
            "revision_state": "current",
            "comparison_id": _comparison_id(night, pair_id, baseline, final),
            "supersedes": [item["comparison_id"] for item in history],
            "observation_pair_id": pair_id, "observation_grade": grade,
            "baseline_observation_id": baseline.get("observation_id"),
            "final_observation_id": final.get("observation_id"),
            "observation_path": str(path(data_dir)),
            "note": "delta 只是同 pair、同对象、同口径的前后变化，不证明由 Growovo 动作造成；缺失指标保持 unknown。"}
