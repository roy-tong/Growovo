"""GEO experiments: baseline/intervention/control without inventing significance."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from . import geo_questions, geo_runs, growth_experiments
from .geo_entity import GeoError
from .storage import atomic_write_text


SCHEMA = "growovo.geo-experiment/1"
PRIMARY_METRIC = "recommendation_rate"


def _now():
    return dt.datetime.now().astimezone()


def _instant(value, label):
    text = str(value or "").strip()
    try:
        stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GeoError("%s 必须是 ISO 8601 时间" % label) from exc
    if stamp.tzinfo is None:
        raise GeoError("%s 必须包含时区" % label)
    return stamp


def _validate_design(data_dir, design):
    errors = []
    if not isinstance(design, dict):
        return ["design 必须是对象"]
    for key in ("entity_id", "set_id", "analysis_plan"):
        if not str(design.get(key) or "").strip():
            errors.append("design.%s 不能为空" % key)
    for key in ("baseline_runs", "intervention_runs", "intervention_question_ids",
                "control_question_ids", "surfaces"):
        value = design.get(key)
        if not isinstance(value, list) or not value:
            errors.append("design.%s 必须是非空数组" % key)
    if errors:
        return errors
    overlap = set(design["intervention_question_ids"]) & set(design["control_question_ids"])
    if overlap:
        errors.append("干预组与对照组共享问题：%s" % ", ".join(sorted(overlap)))
    try:
        question_set = geo_questions.load(data_dir, design["set_id"])
    except GeoError as exc:
        return [str(exc)]
    known = {row["id"] for row in question_set.get("questions") or []}
    for key in ("intervention_question_ids", "control_question_ids"):
        unknown = sorted(set(design[key]) - known)
        if unknown:
            errors.append("design.%s 含问题集之外的问题：%s" % (key, ", ".join(unknown)))
    for key in ("baseline_runs", "intervention_runs"):
        for run_id in design[key]:
            try:
                run = geo_runs.load(data_dir, run_id)
            except GeoError as exc:
                errors.append("%s: %s" % (key, exc))
                continue
            if run["set_id"] != design["set_id"]:
                errors.append("%s 的 run %s 使用了不同的问题集" % (key, run_id))
    samples = design.get("samples_per_question")
    if not isinstance(samples, int) or samples < 1:
        errors.append("design.samples_per_question 必须是 >= 1 的整数")
    mde = design.get("min_detectable_effect")
    if not isinstance(mde, (int, float)) or isinstance(mde, bool) or not (0 < mde <= 1):
        errors.append("design.min_detectable_effect 必须在 (0,1] 内")
    if design.get("primary_metric") != PRIMARY_METRIC:
        errors.append("design.primary_metric 只支持 %s" % PRIMARY_METRIC)
    try:
        preregistered = _instant(design.get("preregistered_at"), "preregistered_at")
    except GeoError as exc:
        errors.append(str(exc))
    else:
        if preregistered > _now():
            errors.append("preregistered_at 不能晚于当前时间")
    return errors


def create(data_dir, service_id, spec):
    if not isinstance(spec, dict):
        raise GeoError("实验必须是 JSON 对象", "GEO_EXPERIMENT_INVALID")
    design = spec.get("design")
    errors = _validate_design(data_dir, design)
    if errors:
        raise GeoError("GEO 实验无效：" + "；".join(errors), "GEO_EXPERIMENT_INVALID")
    payload = dict(spec)
    payload["kind"] = "geo"
    payload.setdefault("primary_metric", PRIMARY_METRIC)
    payload.setdefault("target", design["min_detectable_effect"])
    payload.setdefault("hypothesis", "GEO intervention changes recommendation rate")
    payload.setdefault("stop_rule", "stop if fact conflicts increase or no control delta after 8 weeks")
    payload["design"] = design
    experiment = growth_experiments.create(data_dir, service_id, payload)
    return experiment


def _load(data_dir, service_id, experiment_id):
    for row in growth_experiments.list_experiments(data_dir, service_id):
        if row.get("id") == experiment_id:
            if row.get("kind") != "geo":
                raise GeoError("实验 %s 不是 GEO 实验" % experiment_id, "GEO_EXPERIMENT_INVALID")
            return row
    raise GeoError("实验 %s 不存在" % experiment_id, "GEO_EXPERIMENT_MISSING")


def _question_rates(data_dir, run_id, question_ids):
    run = geo_runs.load(data_dir, run_id)
    observations = geo_runs.observations(data_dir, run_id)
    by_question = {}
    for question_id in question_ids:
        rows = [row for row in observations if row["question_id"] == question_id]
        if not rows:
            continue
        recommended = sum(1 for row in rows if row["judgement"]["recommended"])
        by_question[question_id] = {
            "n": len(rows),
            "recommended": recommended,
            "recommendation_rate": recommended / float(len(rows)),
        }
    return by_question


def _aggregate(data_dir, run_ids, question_ids):
    per_run = {}
    all_rates = []
    total_n = 0
    for run_id in run_ids:
        rates = _question_rates(data_dir, run_id, question_ids)
        per_run[run_id] = rates
        for row in rates.values():
            all_rates.append(row["recommendation_rate"])
            total_n += row["n"]
    return {
        "runs": per_run,
        "questions": len(all_rates),
        "observations": total_n,
        "mean_rate": (sum(all_rates) / len(all_rates)) if all_rates else None,
    }


def report(data_dir, service_id, experiment_id):
    experiment = _load(data_dir, service_id, experiment_id)
    design = experiment.get("design") or {}
    dates = {run_id: geo_runs.load(data_dir, run_id)["started_at"]
             for run_id in design.get("baseline_runs", []) + design.get("intervention_runs", [])}
    baseline_intervention = _aggregate(
        data_dir, design["baseline_runs"], design["intervention_question_ids"])
    baseline_control = _aggregate(
        data_dir, design["baseline_runs"], design["control_question_ids"])
    intervention = _aggregate(
        data_dir, design["intervention_runs"], design["intervention_question_ids"])
    control = _aggregate(
        data_dir, design["intervention_runs"], design["control_question_ids"])
    min_n = min(baseline_intervention["observations"], baseline_control["observations"],
                intervention["observations"], control["observations"])
    if min_n < 12 or None in (baseline_intervention["mean_rate"], baseline_control["mean_rate"],
                              intervention["mean_rate"], control["mean_rate"]):
        conclusion = "inconclusive"
        reason = "有效观测不足（每组至少 12 条才做描述性比较）"
    else:
        intervention_delta = intervention["mean_rate"] - baseline_intervention["mean_rate"]
        control_delta = control["mean_rate"] - baseline_control["mean_rate"]
        net = intervention_delta - control_delta
        mde = float(design["min_detectable_effect"])
        if net >= mde:
            conclusion, reason = "detectable", "干预组相对对照组的增量达到预注册阈值"
        elif abs(net) < mde:
            conclusion, reason = "not_detectable", "未观察到达到预注册阈值的对照增量"
        else:
            conclusion, reason = "not_detectable", "净变化为负，未支持干预有效"
    payload = {
        "schema": SCHEMA,
        "experiment_id": experiment_id,
        "service_id": service_id,
        "design": design,
        "runs": dates,
        "baseline": {"intervention": baseline_intervention, "control": baseline_control},
        "intervention": {"intervention": intervention, "control": control},
        "conclusion": conclusion,
        "reason": reason,
        "causality": "not_claimed",
        "limitations": [
            "准实验；同一实体/模型/问题集内对照组可能被污染。",
            "T1/T2 观测不代表平台官方曝光或全部用户。",
            "样本不足时只做描述，不做显著性结论。",
        ],
    }
    path = Path(data_dir) / "geo" / "experiments" / (experiment_id + ".json")
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=1,
                                       sort_keys=True) + "\n")
    payload["path"] = str(path)
    return payload


def status(data_dir, service_id, experiment_id):
    experiment = _load(data_dir, service_id, experiment_id)
    return {"experiment": experiment,
            "report_path": str(Path(data_dir) / "geo" / "experiments" / (experiment_id + ".json"))}
