"""Controlled local host-agent wake-up for open growth cycles."""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

from . import growth_cycle

SCHEMA = "growovo.host-adapter/1"
OUTPUT_LIMIT = 1024 * 1024


def load_adapter(value):
    if isinstance(value, dict):
        spec = value
    else:
        spec = json.loads(Path(value).read_text(encoding="utf-8"))
    if spec.get("schema") != SCHEMA:
        raise ValueError("host adapter schema must be %s" % SCHEMA)
    for key in ("adapter_id", "version", "producer_id", "argv", "authorization"):
        if not spec.get(key):
            raise ValueError("host adapter.%s is required" % key)
    if not isinstance(spec["argv"], list) or not spec["argv"]:
        raise ValueError("host adapter.argv must be a non-empty array")
    if not str(spec["authorization"].get("authorized_by") or "").strip():
        raise ValueError("host adapter authorization.authorized_by is required")
    return spec


def _environment(spec):
    env = {"PYTHONUNBUFFERED": "1"}
    for key in ("PATH", "HOME", "SYSTEMROOT", "COMSPEC"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    for key in spec.get("env_passthrough") or []:
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _argv(spec, request_path, workspace, cycle_id):
    values = []
    for value in spec["argv"]:
        values.append(str(value).replace("{request}", str(request_path))
                      .replace("{workspace}", str(workspace))
                      .replace("{cycle_id}", cycle_id))
    if not any("{request}" in str(value) for value in spec["argv"]):
        values.append(str(request_path))
    return values


def wake(data_dir, service_id, cycle_id, adapter):
    spec = load_adapter(adapter)
    cycle = growth_cycle.status(data_dir, service_id, cycle_id)
    if cycle.get("status") == "proposed":
        return {"status": "already_proposed", "cycle_id": cycle_id,
                "producer_id": cycle["proposal"]["producer_id"]}
    if cycle.get("status") != "open":
        return {"status": "blocked", "cycle_id": cycle_id,
                "reason": "cycle_not_open"}
    root = (Path(data_dir) / "state" / "host-wake" / service_id / cycle_id
            / ("wake-" + uuid.uuid4().hex[:12]))
    root.mkdir(parents=True)
    request = dict(cycle["request"], cycle_id=cycle_id, service_id=service_id,
                   question=cycle.get("question") or "", deadline=cycle.get("deadline"))
    request_path = root / "request.json"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    result = {"cycle_id": cycle_id, "adapter_id": spec["adapter_id"],
              "version": spec["version"], "workspace": str(root),
              "external_execution": "proposal_only"}
    error = ""
    stdout = ""
    stderr = ""
    try:
        completed = subprocess.run(
            _argv(spec, request_path, root, cycle_id), cwd=root,
            env=_environment(spec), text=True, capture_output=True,
            timeout=min(600, max(1, int(spec.get("timeout_seconds", 60)))))
        stdout = completed.stdout[:OUTPUT_LIMIT]
        stderr = completed.stderr[:OUTPUT_LIMIT]
        if completed.returncode != 0:
            error = "host_exit_%d" % completed.returncode
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        error = type(exc).__name__ + ": " + str(exc)[:300]
    (root / "stdout.txt").write_text(stdout, encoding="utf-8")
    (root / "stderr.txt").write_text(stderr, encoding="utf-8")
    if error:
        result.update(status="blocked", reason=error)
        (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return result
    try:
        proposal_path = root / "proposal.json"
        if proposal_path.is_file():
            proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
        else:
            proposal = json.loads(stdout)
        if not isinstance(proposal, dict):
            raise ValueError("proposal must be an object")
        if proposal.get("producer_id") != spec["producer_id"]:
            raise ValueError("producer_id does not match the authorized adapter")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result.update(status="blocked", reason="invalid_proposal: %s" % str(exc)[:300])
        (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return result
    try:
        proposed = growth_cycle.propose(data_dir, service_id, cycle_id, proposal)
    except (ValueError, OSError, KeyError) as exc:
        result.update(status="blocked", reason="proposal_rejected: %s" % str(exc)[:300])
    else:
        result.update(proposed)
        result["status"] = "proposed"
        result["proposal_ref"] = str(root / "proposal.json")
        if not proposal_path.is_file():
            proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
    (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    return result


def wake_due(data_dir, service_id, adapter):
    spec = load_adapter(adapter)
    results = []
    for row in growth_cycle.due(data_dir, service_id):
        if row.get("status") != "open":
            continue
        results.append(wake(data_dir, service_id, row["cycle_id"], spec))
    return {"status": "observed", "wake_count": len(results), "results": results,
            "execution": "proposal_only"}


def create_followups(data_dir, service_id, execution_report, adapter):
    spec = load_adapter(adapter)
    if spec.get("feedback_enabled") is not True:
        return {"status": "disabled", "created": 0, "results": []}
    maximum = int(spec.get("max_feedback_depth", 3))
    if maximum <= 0:
        raise ValueError("max_feedback_depth must be positive")
    created = []
    for execution in (execution_report or {}).get("results") or []:
        cycle_id = execution.get("cycle_id")
        if not cycle_id:
            continue
        cycle = growth_cycle.status(data_dir, service_id, cycle_id)
        if not cycle.get("executions") or cycle.get("feedback_followup"):
            continue
        depth = int(cycle.get("feedback_depth") or 0)
        if depth >= maximum:
            continue
        observations = []
        for row in cycle["executions"]:
            observations.append({
                "kind": "execution_result",
                "proposal_id": row.get("proposal_id"),
                "action_class": row.get("action_class"),
                "action_id": row.get("action_id"),
                "execution": row.get("execution"),
                "result_status": str((row.get("result") or {}).get("status") or ""),
                "error": str(row.get("error") or "")[:300],
            })
        followup = growth_cycle.request(
            data_dir, service_id, trigger="execution_feedback",
            observations=observations,
            question="Review these actual execution results and propose the next bounded action.",
            actor=str(cycle.get("executed_by") or "growth-worker"))
        followup["feedback_depth"] = depth + 1
        followup["parent_cycle_id"] = cycle_id
        growth_cycle._save(data_dir, service_id, followup)
        cycle["feedback_followup"] = {
            "cycle_id": followup["cycle_id"], "depth": depth + 1,
            "observation_count": len(observations),
        }
        growth_cycle._save(data_dir, service_id, cycle)
        created.append({"parent_cycle_id": cycle_id, "cycle_id": followup["cycle_id"],
                        "depth": depth + 1})
    return {"status": "observed", "created": len(created), "results": created,
            "max_feedback_depth": maximum}
