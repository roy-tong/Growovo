"""Deterministic GEO scoring with denominators, Wilson intervals and explicit exclusions."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlparse

from . import geo_entity
from .geo_entity import GeoError
from .geo_questions import load as load_questions
from .geo_runs import load as load_run, observations as load_observations
from .storage import AtomicJSONStore


SCHEMA = "growovo.geo-score/1"
SCORE_VERSION = "1.0.0"
DECISION_N = 24
EXPLORATORY_N = 12


def score_path(data_dir, run_id: str) -> Path:
    return Path(data_dir) / "geo" / "scores" / (run_id + ".json")


def wilson(successes: int, total: int, z: float = 1.96):
    if total <= 0:
        return [None, None]
    p = float(successes) / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denominator
    half = z * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total)) / denominator
    return [round(max(0.0, center - half), 6), round(min(1.0, center + half), 6)]


def _rate(successes: int, total: int):
    if total <= 0:
        return {"value": None, "ci95": [None, None], "n": 0}
    return {"value": round(float(successes) / total, 6),
            "ci95": wilson(successes, total), "n": total}


def _hosts(entity) -> set:
    urls = (entity.get("canonical") or {}).get("urls") or {}
    hosts = set()
    for value in urls.values():
        parsed = urlparse(str(value))
        if parsed.hostname:
            hosts.add(parsed.hostname.lower())
    return hosts


def _cites_target(urls, hosts) -> bool:
    for value in urls or []:
        parsed = urlparse(str(value))
        host = (parsed.hostname or "").lower()
        if host and any(host == target or host.endswith("." + target) for target in hosts):
            return True
    return False


def _position(row):
    positions = [item.get("position") for item in row.get("mentions") or []
                 if item.get("is_target") and isinstance(item.get("position"), int)]
    if not positions:
        return "absent"
    position = min(positions)
    if position == 1:
        return "first"
    if position <= 3:
        return "top3"
    return "listed"


def _group_key(question, surface):
    return (question.get("intent"), question.get("brand_scope"), surface.get("tier"))


def _inputs_sha256(run, rows):
    payload = {
        "score_version": SCORE_VERSION,
        "run_id": run["run_id"],
        "set_sha256": run.get("set_sha256"),
        "entity_version": run.get("entity_version"),
        "observation_ids": sorted(row["observation_id"] for row in rows),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def score(data_dir, run_id: str):
    run = load_run(data_dir, run_id)
    question_set = load_questions(data_dir, run["set_id"], run["set_version"])
    entity = geo_entity.load(data_dir)
    hosts = _hosts(entity)
    questions = {row["id"]: row for row in question_set.get("questions") or []}
    surfaces = {row["name"]: row for row in run["surfaces"]}
    rows = load_observations(data_dir, run_id)
    groups = {}
    for row in rows:
        question = questions.get(row["question_id"])
        if question is None:
            raise GeoError("观测引用了问题集之外的问题 %s" % row["question_id"],
                           "GEO_QUESTION_UNKNOWN")
        surface = surfaces.get(row["surface"])
        if surface is None:
            raise GeoError("观测引用了 run 之外的 surface %s" % row["surface"],
                           "GEO_SURFACE_UNKNOWN")
        key = _group_key(question, surface)
        bucket = groups.setdefault(key, {"rows": [], "not_run": 0})
        bucket["rows"].append(row)
    for item in run.get("not_run") or []:
        question = questions.get(item.get("question_id"))
        surface = surfaces.get(item.get("surface"))
        if question is None or surface is None:
            continue
        key = _group_key(question, surface)
        groups.setdefault(key, {"rows": [], "not_run": 0})["not_run"] += 1

    result_groups = []
    for (intent, brand_scope, tier), bucket in sorted(groups.items(),
                                                     key=lambda item: (item[0][0] or "",
                                                                       item[0][1] or "",
                                                                       item[0][2] or "")):
        rows_in_group = bucket["rows"]
        n = len(rows_in_group)
        mentions = [row for row in rows_in_group if any(
            item.get("is_target") for item in row.get("mentions") or [])]
        target_mentions = [item for row in rows_in_group for item in row.get("mentions") or []
                           if item.get("is_target")]
        cited = [row for row in rows_in_group if _cites_target(row.get("cited_urls"), hosts)]
        recommended = [row for row in rows_in_group if row["judgement"]["recommended"]]
        conflict_mentions = [item for row in rows_in_group for item in row.get("mentions") or []
                             if item.get("is_target") and row.get("fact_conflicts")]
        position_mix = {"first": 0, "top3": 0, "listed": 0, "absent": 0}
        for row in rows_in_group:
            position_mix[_position(row)] += 1
        accuracy_total = len(target_mentions)
        accuracy_success = accuracy_total - len(conflict_mentions)
        sample_status = ("decision_grade" if n >= DECISION_N
                         else "exploratory" if n >= EXPLORATORY_N
                         else "descriptive")
        result_groups.append({
            "intent": intent,
            "brand_scope": brand_scope,
            "tier": tier,
            "n": n,
            "sample_status": sample_status,
            "mention_rate": _rate(len(mentions), n),
            "citation_rate": _rate(len(cited), n),
            "recommendation_rate": _rate(len(recommended), n),
            "accuracy_rate": _rate(accuracy_success, accuracy_total) if accuracy_total else
                             {"value": None, "ci95": [None, None], "n": 0},
            "position_mix": position_mix,
            "not_run": bucket["not_run"],
        })
    excluded = []
    seen = set()
    for row in rows:
        if row["observation_id"] in seen:
            excluded.append({"observation_id": row["observation_id"], "reason": "duplicate"})
        seen.add(row["observation_id"])
    result = {
        "schema": SCHEMA,
        "run_id": run_id,
        "score_version": SCORE_VERSION,
        "inputs_sha256": _inputs_sha256(run, rows),
        "groups": result_groups,
        "excluded": excluded,
    }
    AtomicJSONStore(score_path(data_dir, run_id)).save(result)
    return result


def load(data_dir, run_id: str):
    path = score_path(data_dir, run_id)
    if not path.is_file():
        raise GeoError("run %s 还没有评分；先运行 mkt geo score" % run_id, "GEO_SCORE_MISSING")
    return AtomicJSONStore(path).load()
