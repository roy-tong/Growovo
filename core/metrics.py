#!/usr/bin/env python3
"""Versioned metric dictionary and deterministic, scoped KPI measurement."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .event_store import EventStore


class MetricError(ValueError):
    pass


def _load_builtin() -> Dict[str, Any]:
    return json.loads((Path(__file__).resolve().parent / "metric_data.json").read_text(encoding="utf-8"))


def lint_metric(metric: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for key in ("metric_id", "version", "name", "layer", "subject_type", "formula", "unit", "timezone", "owner_id"):
        if not str(metric.get(key) or "").strip():
            errors.append(f"metric 缺 {key}")
    if metric.get("layer") not in ("business", "task", "process", "guardrail"):
        errors.append("metric.layer 必须是 business/task/process/guardrail")
    numerator = metric.get("numerator") or {}
    if not numerator.get("event") or not numerator.get("dedupe_key"):
        errors.append("metric.numerator 必须包含 event/dedupe_key")
    denominator = metric.get("denominator")
    if denominator is not None and (not denominator.get("event") or not denominator.get("dedupe_key")):
        errors.append("metric.denominator 必须包含 event/dedupe_key，计数指标则设为 null")
    if denominator is None and metric["formula"] != "numerator_count":
        errors.append("无分母指标的 formula 必须为 numerator_count")
    if denominator is not None and metric["formula"] != "numerator_count / denominator_count":
        errors.append("当前确定性计算器仅支持 numerator_count / denominator_count")
    if not isinstance(metric.get("source_refs"), list) or not metric.get("source_refs"):
        errors.append("metric.source_refs 不能为空")
    if not isinstance(metric.get("observation_window"), dict):
        errors.append("metric.observation_window 必填")
    return errors


class MetricRegistry:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.events = EventStore(self.data_dir / "metrics" / "versions.jsonl", "metric")

    def list(self) -> List[Dict[str, Any]]:
        rows = [copy.deepcopy(m) for m in _load_builtin()["metrics"]]
        rows.extend(copy.deepcopy(e["payload"]["metric"]) for e in self.events.all()
                    if e["event_type"] == "metric.registered")
        return rows

    def get(self, metric_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        rows = [m for m in self.list() if m["metric_id"] == metric_id]
        if version is not None:
            rows = [m for m in rows if m["version"] == version]
        if not rows:
            raise MetricError(f"找不到指标: {metric_id}@{version or 'latest'}")
        return rows[-1]

    def register(self, metric: Dict[str, Any], *, by: str) -> Dict[str, Any]:
        errors = lint_metric(metric)
        if errors:
            raise MetricError("；".join(errors))
        key = (metric["metric_id"], metric["version"])
        if any((m["metric_id"], m["version"]) == key for m in self.list()):
            raise MetricError(f"指标版本已存在且不可覆盖: {key[0]}@{key[1]}")
        self.events.append("registered", metric["metric_id"], {"metric": copy.deepcopy(metric)}, by=by)
        return copy.deepcopy(metric)


def _value(row: Dict[str, Any], path: str) -> Any:
    current: Any = row
    for part in str(path).split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _literal(raw: str) -> Any:
    value = raw.strip().strip("'\"")
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() == "null":
        return None
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def _matches_filter(row: Dict[str, Any], expression: str) -> bool:
    """Small declarative filter parser; deliberately never uses eval()."""
    if not expression:
        return True
    for clause in expression.split(" and "):
        if " != " in clause:
            path, expected = clause.split(" != ", 1)
            if _value(row, path.strip()) == _literal(expected):
                return False
        elif " == " in clause:
            path, expected = clause.split(" == ", 1)
            if _value(row, path.strip()) != _literal(expected):
                return False
        elif " = " in clause:
            path, expected = clause.split(" = ", 1)
            if _value(row, path.strip()) != _literal(expected):
                return False
        else:
            raise MetricError(f"不支持的 filter 子句: {clause}")
    return True


def _event_type(row: Dict[str, Any]) -> str:
    return str(row.get("event_type") or row.get("event") or "")


def _scoped(row: Dict[str, Any], scope: Dict[str, str]) -> bool:
    for key in ("campaign", "subject"):
        if scope.get(key) and row.get(key) != scope[key]:
            return False
    ts = row.get("ts") or row.get("timestamp")
    if (scope.get("since") or scope.get("until")) and not ts:
        return False
    since = scope.get("since", "")
    until = scope.get("until", "")
    ts_text = str(ts)
    since_cmp = ts_text[:10] if len(since) == 10 else ts_text
    until_cmp = ts_text[:10] if len(until) == 10 else ts_text
    if since and since_cmp < since:
        return False
    if until and until_cmp > until:
        return False
    return True


def _collect(events: Iterable[Dict[str, Any]], definition: Dict[str, Any],
             scope: Dict[str, str], issues: List[str]) -> int:
    key = definition["dedupe_key"]
    seen = set()
    for row in events:
        if not isinstance(row, dict) or _event_type(row) != definition["event"]:
            continue
        if not _scoped(row, scope):
            continue
        if not _matches_filter(row, definition.get("filter", "")):
            continue
        identity = _value(row, key)
        if identity in (None, ""):
            issues.append(f"{definition['event']} 缺去重键 {key}")
            continue
        seen.add(str(identity))
    return len(seen)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def measure(metric: Dict[str, Any], events: Iterable[Dict[str, Any]], *,
            scope: Optional[Dict[str, str]] = None, cohort_end: str = "",
            as_of: str = "") -> Dict[str, Any]:
    errors = lint_metric(metric)
    if errors:
        raise MetricError("；".join(errors))
    scope = dict(scope or {})
    if isinstance(events, (dict, str, bytes)):
        raise MetricError("events 必须是事件对象数组，不能传指标字典或字符串")
    rows = list(events)
    if any(not isinstance(row, dict) for row in rows):
        raise MetricError("events 中每一项都必须是事件对象")
    issues: List[str] = []
    numerator = _collect(rows, metric["numerator"], scope, issues)
    denominator_def = metric.get("denominator")
    denominator = _collect(rows, denominator_def, scope, issues) if denominator_def else None
    status = "computed"
    value: Optional[float | int]
    window = metric.get("observation_window") or {}
    if cohort_end and as_of and window.get("unit") == "day" and int(window.get("value", 0)) > 0:
        maturity = _parse_time(cohort_end) + timedelta(days=int(window["value"]))
        if _parse_time(as_of) < maturity:
            status = "pending_maturity"
    if denominator_def is not None and denominator == 0:
        value = None
        status = "insufficient_denominator"
    elif status == "pending_maturity":
        value = None
    elif denominator_def is None:
        value = numerator
    else:
        value = numerator / denominator
    return {
        "metric_id": metric["metric_id"],
        "version": metric["version"],
        "name": metric["name"],
        "layer": metric["layer"],
        "status": status,
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "unit": metric["unit"],
        "formula": metric["formula"],
        "scope": scope,
        "source_refs": copy.deepcopy(metric["source_refs"]),
        "verification_policy": metric["verification_policy"],
        "issues": issues,
        "note": "观察不等于实验增量；分母为零不显示为 0%",
    }
