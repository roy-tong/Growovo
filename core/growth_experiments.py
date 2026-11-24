from __future__ import annotations

import copy
import datetime as dt
import re
from pathlib import Path

from . import growth_runtime


def _safe(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-_]*", str(value or "")):
        raise ValueError("id 格式不正确")
    return value


def _path(data_dir, service_id, experiment_id):
    return (Path(data_dir) / "state" / "growth-experiments" / _safe(service_id)
            / (_safe(experiment_id) + ".json"))


def create(data_dir, service_id, spec):
    for key in ("id", "hypothesis", "primary_metric", "target", "window", "stop_rule"):
        if not spec.get(key):
            raise ValueError(f"experiment.{key} 必填")
    experiment = {
        "schema": "growovo.growth-experiment/1",
        "id": _safe(spec["id"]),
        "service_id": _safe(service_id),
        "hypothesis": spec["hypothesis"],
        "primary_metric": spec["primary_metric"],
        "target": spec["target"],
        "window": copy.deepcopy(spec["window"]),
        "stop_rule": spec["stop_rule"],
        "events": [],
        "status": "active",
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    def mutate(current):
        if current:
            raise ValueError(f"实验已存在 {experiment['id']}")
        current.update(copy.deepcopy(experiment))
        return current

    object_id = f"{service_id}/{experiment['id']}"
    growth_runtime.update_domain_state(
        data_dir, "growth_experiments", object_id,
        _path(data_dir, service_id, experiment["id"]), mutate, default={})
    return experiment


def _load(data_dir, service_id, experiment_id):
    object_id = f"{service_id}/{experiment_id}"
    return growth_runtime.load_domain_state(
        data_dir, "growth_experiments", object_id, _path(data_dir, service_id, experiment_id))


def list_experiments(data_dir, service_id):
    _safe(service_id)
    prefix = f"{service_id}/"
    experiments = growth_runtime.list_domain_states(
        data_dir, "growth_experiments", object_id_prefix=prefix)
    known = {f"{service_id}/{row['id']}" for row in experiments}
    directory = Path(data_dir) / "state" / "growth-experiments" / service_id
    if directory.is_dir():
        for path in directory.glob("*.json"):
            object_id = f"{service_id}/{path.stem}"
            if object_id not in known:
                experiments.append(_load(data_dir, service_id, path.stem))
    return sorted(experiments, key=lambda row: (row["created_at"], row["id"]))


def observe(data_dir, service_id, experiment_id, events):
    if not isinstance(events, list):
        raise ValueError("events 必须是数组")

    def mutate(experiment):
        known = {row.get("id") for row in experiment["events"]}
        added = 0
        for event in events:
            for key in ("id", "type", "subject", "source", "at"):
                if not isinstance(event, dict) or not event.get(key):
                    raise ValueError(f"event.{key} 必填")
            dt.datetime.fromisoformat(str(event["at"]).replace("Z", "+00:00"))
            if event["id"] in known:
                continue
            experiment["events"].append(copy.deepcopy(event))
            known.add(event["id"])
            added += 1
        return {"added": added, "duplicates": len(events) - added}

    return growth_runtime.update_domain_state(
        data_dir, "growth_experiments", f"{service_id}/{experiment_id}",
        _path(data_dir, service_id, experiment_id), mutate)


def diagnose(data_dir, service_id, experiment_id, secondary_targets=None):
    experiment = _load(data_dir, service_id, experiment_id)
    funnel = {}
    for event in experiment["events"]:
        funnel.setdefault(event["type"], set()).add(event["subject"])
    funnel = {name: len(subjects) for name, subjects in funnel.items()}
    for name in ("signal", "click", "install", "activation", "star", "follow"):
        funnel.setdefault(name, 0)
    metric = experiment["primary_metric"]
    primary_count = funnel.get(metric, 0)
    if not experiment["events"]:
        kind, action = "no_data", "先取得带主体、来源和时间的事件，不增加发布量"
    elif primary_count < experiment["target"] and funnel.get("click", 0) == 0:
        kind, action = "message_or_audience", "复查人群、选题与价值表达，不增加相似回复"
    elif primary_count < experiment["target"]:
        kind, action = "first_value_path", "检查安装、首次成功和承接资产，不增加曝光"
    else:
        unmet = [name for name, target in (secondary_targets or {}).items()
                 if funnel.get(name, 0) < target]
        if unmet:
            kind, action = "secondary_assets", "主目标已达成；复查辅助资产与受众，不拉票、不加量"
        else:
            kind, action = "repeat_small_canary", "先按同一合同复现一次，再决定是否放大"
    return {
        "experiment_id": experiment_id,
        "kind": kind,
        "next_action": action,
        "funnel": funnel,
        "primary_metric": metric,
        "primary_count": primary_count,
        "primary_target": experiment["target"],
        "causal_credit": "not_claimed",
    }
