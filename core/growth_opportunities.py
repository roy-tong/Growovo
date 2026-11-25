from __future__ import annotations

import copy
import datetime as dt
import re
from pathlib import Path

from . import growth_runtime


def _path(data_dir, service_id):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-_]*", str(service_id or "")):
        raise ValueError("service_id 格式不正确")
    return Path(data_dir) / "state" / "growth-opportunities" / f"{service_id}.json"


def _load(data_dir, service_id):
    return growth_runtime.load_domain_state(
        data_dir, "growth_opportunities", service_id, _path(data_dir, service_id),
        default={"opportunities": []})


def _save(data_dir, service_id, payload):
    snapshot = copy.deepcopy(payload)

    def replace(current):
        current.clear()
        current.update(snapshot)
        return current

    return growth_runtime.update_domain_state(
        data_dir, "growth_opportunities", service_id, _path(data_dir, service_id), replace)


def evaluate(candidate):
    intent = {"solution_seeking": 35, "workaround": 25, "technical_discussion": 5}.get(
        candidate.get("intent"), 0)
    fit = {"high": 20, "medium": 10, "low": 0}.get(candidate.get("fit", "low"), 0)
    evidence = candidate.get("evidence") or []
    evidence_points = min(20, len(evidence) * 5)
    asset = 15 if candidate.get("asset_reproduced") is True else 0
    relationship = 20 if candidate.get("existing_user") is True else 0
    human = int(candidate.get("human_minutes") or 0)
    risk_name = candidate.get("risk") or "low"
    risk = {"low": 0, "medium": 15, "high": 40}.get(risk_name, 40)
    score = intent + fit + evidence_points + asset + relationship - human - risk
    if risk_name == "high":
        decision = "ignore"
    elif not asset:
        decision = "prepare_asset"
    elif score >= 60:
        decision = "engage"
    elif score >= 35:
        decision = "research"
    else:
        decision = "ignore"
    reasons = []
    for name, value, label in (
            ("intent", intent, "需求意图"), ("fit", fit, "产品匹配"),
            ("evidence", evidence_points, "证据"), ("asset", asset, "已复现资产"),
            ("relationship", relationship, "既有关系"), ("human", -human, "人工成本"),
            ("risk", -risk, "风险成本")):
        if value:
            reasons.append({"factor": name, "points": value, "label": label})
    return {
        "score": score,
        "score_kind": "explainable_resource_priority_not_probability",
        "decision": decision,
        "confidence": "high" if len(evidence) >= 2 else ("medium" if evidence else "low"),
        "reasons": reasons,
    }


def _instant(value, label):
    text = str(value or "").strip()
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("%s 必须是 ISO 8601 时间" % label) from exc
    if parsed.tzinfo is None:
        raise ValueError("%s 必须包含时区" % label)
    return parsed


def _moment(value=None):
    return _instant(value, "now") if value else dt.datetime.now().astimezone()


def _validity(row):
    observed = _instant(row.get("observed_at"), "observed_at")
    if row.get("valid_until"):
        valid_until = _instant(row["valid_until"], "valid_until")
    else:
        hours = row.get("freshness_hours", 48)
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or hours <= 0:
            raise ValueError("freshness_hours 必须是正数")
        valid_until = observed + dt.timedelta(hours=hours)
    if valid_until <= observed:
        raise ValueError("valid_until 必须晚于 observed_at")
    return valid_until


def import_opportunities(data_dir, service_id, rows):
    if not isinstance(rows, list):
        raise ValueError("opportunities 必须是数组")

    def mutate(payload):
        added = 0
        for raw in rows:
            for key in ("source", "external_id", "author", "problem", "trigger"):
                if not isinstance(raw, dict) or not raw.get(key):
                    raise ValueError(f"opportunity.{key} 必填")
            source = re.sub(r"[^a-z0-9]+", "-", raw["source"].lower()).strip("-")
            external = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw["external_id"]).strip("-")
            oid = f"opp-{source}-{external}"
            if any(row["id"] == oid for row in payload["opportunities"]):
                continue
            _instant(raw.get("observed_at"), "observed_at")
            valid_until = _validity(raw)
            evaluation = evaluate(raw)
            row = copy.deepcopy(raw)
            row.update(id=oid, service_id=service_id, status="pending",
                       valid_until=valid_until.isoformat(), **evaluation)
            if evaluation["decision"] == "ignore":
                row["status"] = "ignored"
            payload["opportunities"].append(row)
            added += 1
        return {"added": added, "duplicates": len(rows) - added}

    return growth_runtime.update_domain_state(
        data_dir, "growth_opportunities", service_id, _path(data_dir, service_id), mutate,
        default={"opportunities": []})


def list_opportunities(data_dir, service_id, *, now=None):
    moment = _moment(now)
    rows = []
    for stored in _load(data_dir, service_id)["opportunities"]:
        row = copy.deepcopy(stored)
        if not row.get("observed_at"):
            row["freshness_status"] = "unknown"
        else:
            valid_until = _instant(row.get("valid_until"), "valid_until") \
                if row.get("valid_until") else _validity(row)
            row["valid_until"] = valid_until.isoformat()
            row["freshness_status"] = "expired" if moment >= valid_until else "current"
            if row["freshness_status"] == "expired" and row.get("status") == "pending":
                row["status"] = "expired"
        rows.append(row)
    return rows


def next_action(data_dir, service_id, *, now=None):
    rows = [row for row in list_opportunities(data_dir, service_id, now=now)
            if row["status"] == "pending" and row.get("freshness_status") == "current"]
    if not rows:
        return None
    row = sorted(rows, key=lambda item: (-item["score"], item["human_minutes"], item["id"]))[0]
    return dict(row, id="opportunity:" + row["id"], opportunity_id=row["id"],
                kind="opportunity", priority=90 + row["score"] / 100,
                next_step=row["decision"], execution={"intent": "live"})
