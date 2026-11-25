"""Observable growth cycle: request -> host proposal -> policy check -> execute -> receipt."""
from __future__ import annotations

import datetime as dt
import re
import uuid
from pathlib import Path

from . import growth_execution, growth_service, risk_policy
from .storage import AtomicJSONStore


REQUEST_SCHEMA = "growovo.growth-cycle-request/1"
PROPOSAL_SCHEMA = "growovo.growth-cycle-proposal/1"
CYCLE_SCHEMA = "growovo.growth-cycle/1"
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,119}$")


def _now():
    return dt.datetime.now().astimezone()


def _instant(value, label):
    text = str(value or "").strip()
    try:
        stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("%s 必须是 ISO 8601 时间" % label) from exc
    if stamp.tzinfo is None:
        raise ValueError("%s 必须包含时区" % label)
    return stamp


def path(data_dir, service_id, cycle_id):
    if not _ID.fullmatch(str(service_id or "")):
        raise ValueError("service_id 格式不正确")
    if not _ID.fullmatch(str(cycle_id or "")):
        raise ValueError("cycle_id 格式不正确")
    return Path(data_dir) / "state" / "growth-cycles" / service_id / (cycle_id + ".json")


def _load(data_dir, service_id, cycle_id):
    file_path = path(data_dir, service_id, cycle_id)
    if not file_path.is_file():
        raise ValueError("cycle %s 不存在" % cycle_id)
    return AtomicJSONStore(file_path).load()


def _save(data_dir, service_id, payload):
    return AtomicJSONStore(path(data_dir, service_id, payload["cycle_id"])).save(payload)


def request(data_dir, service_id, *, trigger="manual", observations=None,
            question="", deadline_hours=24, cycle_id=None, actor="operator"):
    service = growth_service.load(data_dir, service_id)
    if not str(actor or "").strip():
        raise ValueError("actor 不能为空")
    if isinstance(deadline_hours, bool) or not isinstance(deadline_hours, int) or deadline_hours <= 0:
        raise ValueError("deadline_hours 必须是正整数")
    cycle_id = str(cycle_id or ("cycle-" + uuid.uuid4().hex[:12]))
    if not _ID.fullmatch(cycle_id):
        raise ValueError("cycle_id 格式不正确")
    if path(data_dir, service_id, cycle_id).exists():
        raise ValueError("cycle %s 已存在" % cycle_id)
    observations = observations if observations is not None else []
    if not isinstance(observations, list):
        raise ValueError("observations 必须是数组")
    if not observations:
        nxt = growth_service.next_action(data_dir, service_id)
        if nxt:
            observations = [{"kind": "next_action", "action_id": nxt.get("id"),
                             "status": nxt.get("status"),
                             "channel": nxt.get("channel"),
                             "action": nxt.get("action")}]
    policy_snapshot = {}
    for action_class in risk_policy.CLASSES:
        policy_snapshot[action_class] = risk_policy.explain(
            data_dir, service_id, action_class=action_class)
    current = _now()
    cycle = {
        "schema": CYCLE_SCHEMA,
        "cycle_id": cycle_id,
        "service_id": service_id,
        "created_at": current.isoformat(timespec="seconds"),
        "deadline": (current + dt.timedelta(hours=deadline_hours)).isoformat(timespec="seconds"),
        "trigger": str(trigger or "manual")[:64],
        "actor": str(actor).strip(),
        "question": str(question or "").strip(),
        "request": {"schema": REQUEST_SCHEMA,
                    "observations": observations,
                    "allowed_actions": list(risk_policy.CLASSES),
                    "policy_snapshot": policy_snapshot},
        "proposal": None,
        "decisions": [],
        "executions": [],
        "status": "open",
    }
    _save(data_dir, service_id, cycle)
    return cycle


def _expire_if_needed(cycle):
    if cycle.get("status") in ("executed", "expired"):
        return cycle
    if _now() >= _instant(cycle["deadline"], "deadline"):
        cycle["status"] = "expired"
    return cycle


def propose(data_dir, service_id, cycle_id, proposal):
    cycle = _expire_if_needed(_load(data_dir, service_id, cycle_id))
    if cycle["status"] == "expired":
        _save(data_dir, service_id, cycle)
        raise ValueError("cycle 已过期；不执行迟到提案")
    if cycle.get("proposal") is not None:
        raise ValueError("cycle 已有提案；需要新提案请新建 cycle")
    if not isinstance(proposal, dict):
        raise ValueError("proposal 必须是对象")
    if proposal.get("schema") != PROPOSAL_SCHEMA:
        raise ValueError("proposal.schema 必须是 %s" % PROPOSAL_SCHEMA)
    producer = str(proposal.get("producer_id") or "").strip()
    if not producer:
        raise ValueError("producer_id 不能为空")
    items = proposal.get("proposals")
    if not isinstance(items, list) or not items:
        raise ValueError("proposals 必须是非空数组")
    seen = set()
    decisions = []
    for index, item in enumerate(items):
        label = "proposals[%d]" % index
        if not isinstance(item, dict):
            raise ValueError(label + " 必须是对象")
        proposal_id = str(item.get("proposal_id") or "").strip()
        if not _ID.fullmatch(proposal_id):
            raise ValueError(label + ".proposal_id 格式不正确")
        if proposal_id in seen:
            raise ValueError(label + ".proposal_id 重复")
        seen.add(proposal_id)
        action_class = item.get("action_class")
        if action_class not in risk_policy.CLASSES:
            raise ValueError(label + ".action_class 未知")
        if not str(item.get("rationale") or "").strip():
            raise ValueError(label + ".rationale 不能为空")
        evidence = item.get("evidence_refs")
        if evidence is not None and not isinstance(evidence, list):
            raise ValueError(label + ".evidence_refs 必须是数组")
        decision = risk_policy.explain(data_dir, service_id, action_class=action_class,
                                       scope=item.get("scope") or {})
        record = {"proposal_id": proposal_id, "action_class": action_class,
                  "action_id": item.get("action_id") or "",
                  "target": item.get("target") or "",
                  "allowed": bool(decision["allowed"]),
                  "reason": decision["reason"],
                  "grant_id": (decision.get("grant") or {}).get("grant_id") or "",
                  "requires": decision.get("requires")}
        decisions.append(record)
    cycle["proposal"] = {"schema": PROPOSAL_SCHEMA, "producer_id": producer,
                         "proposals": items, "received_at": _now().isoformat(timespec="seconds")}
    cycle["decisions"] = decisions
    cycle["status"] = "proposed"
    _save(data_dir, service_id, cycle)
    return {"cycle_id": cycle_id, "status": "proposed",
            "accepted": [row for row in decisions if row["allowed"]],
            "refused": [row for row in decisions if not row["allowed"]]}


def execute(data_dir, service_id, cycle_id, *, by):
    cycle = _expire_if_needed(_load(data_dir, service_id, cycle_id))
    if cycle["status"] == "expired":
        _save(data_dir, service_id, cycle)
        raise ValueError("cycle 已过期；不执行")
    if not str(by or "").strip():
        raise ValueError("执行必须署名")
    if cycle.get("status") != "proposed":
        raise ValueError("cycle 还没有可执行提案")
    results = []
    for decision in cycle.get("decisions") or []:
        if not decision.get("allowed"):
            results.append(dict(decision, execution="refused"))
            continue
        action_id = decision.get("action_id")
        if not action_id:
            results.append(dict(decision, execution="mapping_required"))
            continue
        payload = {}
        proposal = next((row for row in cycle["proposal"]["proposals"]
                         if row.get("proposal_id") == decision["proposal_id"]), {})
        for key in ("text", "target", "channels", "asset", "metrics"):
            if key in proposal:
                payload[key] = proposal[key]
        try:
            result = growth_execution.run(data_dir, service_id, action_id, by=by,
                                          payload=payload)
            results.append(dict(decision, execution="executed", result=result))
        except (ValueError, OSError, KeyError) as exc:
            results.append(dict(decision, execution="blocked", error=str(exc)[:300]))
    cycle["executions"] = results
    cycle["status"] = "executed" if any(row["execution"] == "executed" for row in results) else "blocked"
    cycle["executed_at"] = _now().isoformat(timespec="seconds")
    cycle["executed_by"] = str(by).strip()
    _save(data_dir, service_id, cycle)
    return {"cycle_id": cycle_id, "status": cycle["status"], "executions": results}


def status(data_dir, service_id, cycle_id):
    cycle = _expire_if_needed(_load(data_dir, service_id, cycle_id))
    return cycle


def list_cycles(data_dir, service_id, limit=50):
    folder = Path(data_dir) / "state" / "growth-cycles" / service_id
    if not folder.is_dir():
        return []
    rows = []
    for file_path in folder.glob("*.json"):
        try:
            rows.append(AtomicJSONStore(file_path).load())
        except (OSError, ValueError):
            continue
    rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return rows[:limit]


def execute_proposed(data_dir, service_id, *, by):
    if not str(by or "").strip():
        raise ValueError("execute_proposed 需要具名操作者")
    results = []
    for cycle in list_cycles(data_dir, service_id, limit=500):
        if cycle.get("status") != "proposed":
            continue
        try:
            result = execute(data_dir, service_id, cycle["cycle_id"], by=by)
        except (ValueError, OSError, KeyError) as exc:
            result = {"cycle_id": cycle["cycle_id"], "status": "blocked",
                      "error": str(exc)[:300]}
        results.append(result)
    return {"status": "observed", "executed_cycles": len(results),
            "executed_by": str(by).strip(), "results": results}


def due(data_dir, service_id):
    rows = []
    for cycle in list_cycles(data_dir, service_id, limit=500):
        if cycle.get("status") != "open":
            continue
        cycle = _expire_if_needed(cycle)
        if cycle.get("status") == "expired":
            _save(data_dir, service_id, cycle)
        rows.append({"cycle_id": cycle["cycle_id"], "status": cycle["status"],
                     "deadline": cycle.get("deadline")})
    return rows
