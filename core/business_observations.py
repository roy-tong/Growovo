"""Append-only authorized business observations; deltas never imply attribution."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from .ledger import _append_line, _read_jsonl_fail_loud


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


def import_observation(data_dir, payload: Dict[str, Any], *, night: str, phase: str,
                       by: str, apply: bool = False) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("业务观察必须是 JSON 对象")
    if phase not in {"baseline", "final"}:
        raise ValueError("业务观察 phase 必须是 baseline/final")
    if not str(night or "").strip() or not str(by or "").strip():
        raise ValueError("业务观察必须有 night 和署名 by")
    observed_at = str(payload.get("observed_at") or "").strip()
    try:
        stamp = dt.datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("observed_at 必须是 ISO 8601 时间")
    if stamp.tzinfo is None:
        raise ValueError("observed_at 必须包含时区")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError("metrics 至少包含一个指标；未知用 null，不要填 0")
    metrics = {str(key): _metric(value, str(key)) for key, value in metrics.items()}
    source = payload.get("source") or {}
    evidence = [str(item).strip() for item in (source.get("evidence") or []) if str(item).strip()]
    if not evidence:
        raise ValueError("业务观察必须提供 source.evidence")
    canonical = {"night": night, "phase": phase, "observed_at": observed_at,
                 "metrics": metrics, "source": {"kind": str(source.get("kind") or "import"),
                                                "evidence": evidence}}
    observation_id = "obs:" + hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    record = dict(canonical, observation_id=observation_id, by=by,
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


def _value(baseline, final):
    if baseline is None or final is None:
        return {"status": "unknown", "baseline": baseline, "final": final, "delta": None}
    return {"status": "measured", "baseline": baseline, "final": final, "delta": final - baseline}


def compare(data_dir, night: str) -> Dict[str, Any]:
    rows = _rows(data_dir, night)
    if not rows:
        return {"status": "not_included", "metrics": {}, "attribution": "not_claimed",
                "note": "没有独立业务观察；committed 不能替代粉丝、star、注册或激活读数。"}
    baseline = next((row for row in reversed(rows) if row.get("phase") == "baseline"), None)
    final = next((row for row in reversed(rows) if row.get("phase") == "final"), None)
    base_metrics = (baseline or {}).get("metrics") or {}
    final_metrics = (final or {}).get("metrics") or {}
    metrics = {}
    for key in sorted(set(base_metrics) | set(final_metrics)):
        before = base_metrics.get(key)
        after = final_metrics.get(key)
        if isinstance(before, dict) or isinstance(after, dict):
            before = before if isinstance(before, dict) else {}
            after = after if isinstance(after, dict) else {}
            metrics[key] = {name: _value(before.get(name), after.get(name))
                            for name in sorted(set(before) | set(after))}
        else:
            metrics[key] = _value(before, after)

    def measured(value):
        if isinstance(value, dict) and "status" in value:
            return value["status"] == "measured"
        if isinstance(value, dict):
            return any(measured(item) for item in value.values())
        return False
    return {"status": "measured" if any(measured(value) for value in metrics.values()) else "unknown",
            "metrics": metrics, "attribution": "not_claimed",
            "baseline_observation_id": (baseline or {}).get("observation_id"),
            "final_observation_id": (final or {}).get("observation_id"),
            "observation_path": str(path(data_dir)),
            "note": "delta 只是同口径前后变化，不证明由 Growovo 动作造成；缺失指标保持 unknown。"}
