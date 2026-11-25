"""Read-only shared experiment projection for persisted Growovo growth experiments."""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import re
from pathlib import Path

from . import growth_experiments

SCHEMA = "ovo.experiment-contract/1"
GRADES = {"observed", "reported", "derived", "independently_verified"}


def product_version():
    manifest = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if manifest.is_file():
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', manifest.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    try:
        return importlib.metadata.version("growovo")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def design_hash(document):
    projection = {key: document.get(key) for key in
                  ("hypothesis", "design", "window", "stop_conditions")}
    canonical = json.dumps(projection, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _event_hash(event):
    canonical = json.dumps(event, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def from_growth_experiment(data_dir, service_id, experiment_id, spec, origin_version):
    if not isinstance(spec, dict):
        raise ValueError("experiment export design must be an object")
    for key in ("falsification_condition", "design", "registered_by", "registered_at"):
        if not spec.get(key):
            raise ValueError("experiment export requires %s" % key)
    if "external_actions" not in spec:
        raise ValueError("experiment export requires external_actions")
    if spec["external_actions"] is True and not spec.get("authorization_refs"):
        raise ValueError("external_actions requires authorization_refs")
    experiment = growth_experiments._load(data_dir, service_id, experiment_id)
    bindings = spec.get("observation_bindings") or {}
    observations = []
    for event in experiment.get("events") or []:
        event_id = str(event["id"])
        binding = bindings.get(event_id)
        if not isinstance(binding, dict):
            raise ValueError("event %s needs an explicit observation binding" % event_id)
        eligible = binding.get("eligible") is True
        grade = str(binding.get("evidence_grade") or "reported")
        if grade not in GRADES:
            raise ValueError("event %s has unknown evidence_grade" % event_id)
        reason = str(binding.get("exclusion_reason") or "").strip()
        if not eligible and not reason:
            reason = "Event is not qualified as experiment evidence."
        observations.append({
            "observation_id": event_id,
            "arm_id": str(binding.get("arm_id") or ""),
            "metric_id": str(binding.get("metric_id") or ""),
            "subject_ref": str(event["subject"]),
            "observed_at": str(event["at"]),
            "value_number": 1,
            "source_ref": "growovo-growth-experiment:%s/%s/%s" % (
                service_id, experiment_id, event_id),
            "source_hash": _event_hash(event),
            "evidence_grade": grade,
            "eligible": eligible,
            "exclusion_reason": reason,
        })
    outcome = copy.deepcopy(spec.get("outcome") or {})
    if not outcome:
        outcome = {
            "status": "inconclusive" if observations else "not_evaluated",
            "causality": "descriptive_association" if observations else "not_claimed",
            "evidence_refs": [row["observation_id"] for row in observations],
            "evaluated_by": "",
            "evaluated_at": "",
            "notes": "Events are exported without inferring a hypothesis decision or causal effect.",
        }
    state = {
        "active": "running",
        "completed": "completed",
        "stopped": "stopped",
        "cancelled": "cancelled",
    }.get(str(experiment.get("status") or ""), "registered")
    document = {
        "schema": SCHEMA,
        "experiment_id": "%s/%s" % (service_id, experiment_id),
        "origin_product": "growovo",
        "origin_version": str(origin_version),
        "task_id": str(service_id),
        "title": str(spec.get("title") or experiment["hypothesis"]),
        "state": state,
        "hypothesis": {
            "statement": str(experiment["hypothesis"]),
            "falsification_condition": str(spec["falsification_condition"]),
        },
        "design": copy.deepcopy(spec["design"]),
        "window": copy.deepcopy(experiment["window"]),
        "stop_conditions": [str(experiment["stop_rule"])],
        "external_actions": spec["external_actions"],
        "authorization_refs": list(spec.get("authorization_refs") or []),
        "registered_by": str(spec["registered_by"]),
        "registered_at": str(spec["registered_at"]),
        "observations": observations,
        "outcome": outcome,
    }
    if spec.get("decision"):
        document["decision"] = copy.deepcopy(spec["decision"])
    document["design_sha256"] = design_hash(document)
    return document


def write_new(document, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return target
