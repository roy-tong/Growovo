"""Read-only deterministic reports for persisted engagement-cycle waves."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Tuple


CONTENT_GATES = (
    "reply_artifact_floor_met",
    "focus_floor_met",
    "x_publish_artifact_floor_met",
)


def _integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _state(row: Dict[str, Any]) -> str:
    state = str(row.get("ledger_result") or "").strip()
    if state:
        return state
    state = str(row.get("result") or "unknown").strip()
    return "committed" if state == "success" else state


def _key(row: Dict[str, Any]) -> str:
    return "%s/%s" % (row.get("channel") or "unknown", row.get("action") or "unknown")


def _path(data_dir: Path, path: Path) -> str:
    try:
        return str(path.relative_to(data_dir))
    except ValueError:
        return str(path)


def _source_paths(data_dir: Path, night: str) -> Tuple[str, List[Path]]:
    state = data_dir / "state"
    if night:
        directory = state / ("engagement-%s" % night)
        paths = sorted(directory.glob("wave-*.json"))
        if not paths:
            raise ValueError("没有可汇总的波次：%s" % directory)
        return night, paths
    candidates = []
    for directory in state.glob("engagement-*"):
        paths = sorted(directory.glob("wave-*.json"))
        if paths:
            candidates.append((directory.name, paths))
    if not candidates:
        raise ValueError("没有可汇总的波次：%s" % state)
    _, paths = max(candidates, key=lambda item: item[0])
    return paths[0].parent.name.replace("engagement-", "", 1), paths


def _key_row(rows: Dict[str, Dict[str, Any]], key: str) -> Dict[str, Any]:
    return rows.setdefault(key, {
        "target": 0, "planned": 0, "attempted": 0,
        "committed": 0, "deferred": 0, "unattempted": 0,
        "results": {},
    })


def _project_task_outcome(report: Dict[str, Any]) -> Dict[str, Any]:
    """One canonical run outcome for every consumer.

    ``report["status"]`` (complete/attention) and each ``wave["status"]``
    (complete/partial/failed) remain for compatibility, but they answer
    different questions. This projection answers "did the run resolve the
    task?" and is the field a CLI/MCP/night report should read.
    """
    totals = report["totals"]
    target = _integer(totals.get("target"))
    planned = _integer(totals.get("planned"))
    attempted = _integer(totals.get("attempted"))
    committed = _integer(totals.get("committed"))
    basis = target if target else planned
    wave_statuses = [str(row.get("status") or "") for row in report.get("waves", [])]
    reasons = []
    if report.get("errors"):
        reasons.append("engine_error")
    if "failed" in wave_statuses:
        reasons.append("wave_failed")
    if "partial" in wave_statuses:
        reasons.append("wave_partial")
    if _integer(totals.get("deferred")):
        reasons.append("deferred")
    if _integer(totals.get("unattempted")):
        reasons.append("unattempted")

    if not basis:
        outcome = "unknown"
    elif committed >= basis:
        outcome = "resolved"
    elif committed > 0:
        outcome = "partial"
    elif attempted > 0:
        outcome = "unresolved"
    else:
        outcome = "unknown"

    if "wave_failed" in reasons:
        outcome = "unresolved" if committed == 0 else "partial"
    elif "wave_partial" in reasons and outcome == "resolved":
        outcome = "partial"

    return {
        "outcome": outcome,
        "basis": "target" if target else ("planned" if planned else "none"),
        "target": target,
        "planned": planned,
        "attempted": attempted,
        "committed": committed,
        "reasons": sorted(set(reasons)),
        "note": ("canonical run outcome; legacy report.status and wave.status "
                 "remain for compatibility"),
    }


def _scheduled_wave(wave_id: str) -> bool:
    match = re.fullmatch(r"(?:\d{2}-(\d{4})|(\d{4})(?:d\d|gh)?)", wave_id)
    if not match:
        return False
    stamp = match.group(1) or match.group(2)
    hour, minute = int(stamp[:2]), int(stamp[2:])
    return minute < 60 and (hour >= 19 or hour < 7)


def build_report(data_dir, *, night: str = "", scheduled_only: bool = False) -> Dict[str, Any]:
    """Aggregate real wave receipts without network, queue or ledger writes."""
    data_dir = Path(data_dir)
    night, paths = _source_paths(data_dir, str(night or "").strip())
    report: Dict[str, Any] = {
        "schema_version": "growovo.cycle-report/1",
        "night": night,
        "status": "complete",
        "wave_count": 0,
        "dry_run_wave_count": 0,
        "auxiliary_wave_count": 0,
        "auxiliary_waves": [],
        "totals": {},
        "by_key": {},
        "waves": [],
        "exceptions": [],
        "inbox": {},
        "content": {},
        "business_outcomes": {
            "status": "not_included",
            "note": "committed 只代表平台动作回执；粉丝、star、注册和激活需要独立读数。",
        },
        "source_files": [],
        "errors": [],
    }
    total_results = Counter()
    key_rows: Dict[str, Dict[str, Any]] = {}
    inbox_errors = Counter()
    content_misses = Counter()
    collection_observations = 0
    latest_pending = 0

    for path in paths:
        raw = path.read_bytes()
        relative = _path(data_dir, path)
        report["source_files"].append({
            "path": relative,
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
        try:
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("wave JSON 必须是对象")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            report["errors"].append({"path": relative, "error": str(exc)})
            continue
        wave_id = str(payload.get("wave") or path.stem.replace("wave-", "", 1))
        if scheduled_only and not _scheduled_wave(wave_id):
            report["auxiliary_wave_count"] += 1
            report["auxiliary_waves"].append(wave_id)
            continue
        if payload.get("dry"):
            report["dry_run_wave_count"] += 1
            continue

        report["wave_count"] += 1
        supply = payload.get("supply") if isinstance(payload.get("supply"), dict) else {}
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        deferred = payload.get("deferred") if isinstance(payload.get("deferred"), list) else []
        wave_results = defaultdict(Counter)
        wave_supply = {}
        wave_deferred = Counter()

        for key, item in supply.items():
            if not isinstance(item, dict) or "/" not in key:
                continue
            target = _integer(item.get("effective", item.get("configured")))
            scheduled = _integer(item.get("scheduled"))
            wave_supply[key] = {"target": target, "scheduled": scheduled}
            row = _key_row(key_rows, key)
            row["target"] += target
            row["planned"] += scheduled

        states = Counter()
        for item in results:
            if not isinstance(item, dict):
                continue
            key = _key(item)
            state = _state(item)
            states[state] += 1
            total_results[state] += 1
            wave_results[key][state] += 1
            row = _key_row(key_rows, key)
            row["attempted"] += 1
            row["committed"] += int(state == "committed")
            merged = Counter(row["results"])
            merged[state] += 1
            row["results"] = dict(merged)
            if state != "committed":
                report["exceptions"].append({
                    "wave": wave_id,
                    "channel": str(item.get("channel") or "unknown"),
                    "action": str(item.get("action") or "unknown"),
                    "target": str(item.get("target") or ""),
                    "result": state,
                    "reason": str(item.get("reason") or ""),
                    "error": str(item.get("error") or "")[:300],
                })

        for item in deferred:
            if isinstance(item, dict):
                key = _key(item)
                wave_deferred[key] += 1
                _key_row(key_rows, key)["deferred"] += 1

        wave_unattempted = 0
        for key in set(wave_supply) | set(wave_results) | set(wave_deferred):
            scheduled = wave_supply.get(key, {}).get("scheduled", 0)
            missing = max(0, scheduled - sum(wave_results[key].values()) - wave_deferred[key])
            wave_unattempted += missing
            _key_row(key_rows, key)["unattempted"] += missing

        inbox = payload.get("inbox") if isinstance(payload.get("inbox"), dict) else {}
        collection_observations += _integer(inbox.get("collected"))
        latest_pending = _integer(inbox.get("pending_text"))
        current_inbox_errors = inbox.get("errors") if isinstance(inbox.get("errors"), dict) else {}
        for channel in current_inbox_errors:
            inbox_errors[channel] += 1
        content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
        for gate in CONTENT_GATES:
            if gate in content and not content.get(gate):
                content_misses[gate] += 1

        wave_target = sum(row["target"] for row in wave_supply.values())
        wave_planned = sum(row["scheduled"] for row in wave_supply.values())
        planned = _integer(payload.get("planned")) if "planned" in payload else wave_planned
        attempted = sum(sum(counts.values()) for counts in wave_results.values())
        committed = sum(counts.get("committed", 0) for counts in wave_results.values())
        held = sum(wave_deferred.values())
        wave_unattempted = max(0, planned - attempted - held)
        reasons = []
        if not payload.get("ok", False):
            reasons.append("engine_error")
        if wave_target > wave_planned:
            reasons.append("target_shortfall")
        if any(state != "committed" for state in states):
            reasons.append("non_committed_results")
        if held:
            reasons.append("deferred")
        if wave_unattempted:
            reasons.append("unattempted")
        if payload.get("stopped"):
            reasons.append("stopped")
        warnings = []
        if latest_pending:
            warnings.append("inbox_pending_text")
        if current_inbox_errors:
            warnings.append("inbox_errors")
        if payload.get("preflight_skipped"):
            warnings.append("preflight_skipped")
        warnings.extend("%s_missed" % gate for gate in CONTENT_GATES
                        if gate in content and not content.get(gate))
        expected = wave_target if wave_target else planned
        status = "complete" if not reasons and expected == committed else (
            "failed" if not payload.get("ok", False) else "partial")
        report["waves"].append({
            "wave": wave_id,
            "campaign": str(payload.get("campaign") or ""),
            "ok": bool(payload.get("ok", False)),
            "status": status,
            "target": wave_target,
            "planned": planned,
            "attempted": attempted,
            "committed": committed,
            "deferred": held,
            "unattempted": wave_unattempted,
            "results": dict(sorted(states.items())),
            "reasons": reasons,
            "warnings": warnings,
            "stopped": payload.get("stopped") or {},
            "elapsed_sec": _integer(payload.get("elapsed_sec")),
        })

    for row in key_rows.values():
        basis = row["target"] if row["target"] else row["planned"]
        row["preplanning_shortfall"] = max(0, row["target"] - row["planned"])
        row["not_committed"] = max(0, basis - row["committed"])
        row["results"] = dict(sorted(row["results"].items()))
    report["by_key"] = {key: key_rows[key] for key in sorted(key_rows)}
    target = sum(row["target"] for row in key_rows.values())
    planned = sum(row["planned"] for row in report["waves"])
    attempted = sum(row["attempted"] for row in report["waves"])
    committed = sum(row["committed"] for row in report["waves"])
    held = sum(row["deferred"] for row in report["waves"])
    unattempted = sum(row["unattempted"] for row in report["waves"])
    shortfall_basis = target if target else planned
    report["totals"] = {
        "target": target,
        "target_status": "recorded" if target else "unavailable",
        "shortfall_basis": "target" if target else "planned_target_unavailable",
        "planned": planned,
        "attempted": attempted,
        "committed": committed,
        "deferred": held,
        "unattempted": unattempted,
        "not_committed": max(0, shortfall_basis - committed),
        "preplanning_shortfall": max(0, target - planned) if target else 0,
        "completion_rate": round(committed / target, 4) if target else None,
        "results": dict(sorted(total_results.items())),
    }
    report["task_outcome"] = _project_task_outcome(report)
    report["inbox"] = {
        "collection_observations": collection_observations,
        "latest_pending_text": latest_pending,
        "errors": dict(sorted(inbox_errors.items())),
        "note": "collection_observations 是收件扫描观察数，不是去重后的消息数。",
    }
    report["content"] = {"planned_gate_misses": dict(sorted(content_misses.items()))}
    if report["errors"] or any(row["status"] != "complete" for row in report["waves"]):
        report["status"] = "attention"
    if not report["wave_count"]:
        report["status"] = "attention"
        report["reason"] = "no_real_waves"
    from . import business_observations
    report["business_outcomes"] = business_observations.compare(data_dir, night)
    return report
