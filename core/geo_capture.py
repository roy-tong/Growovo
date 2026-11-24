"""Capture templates and import for GEO observations.

Capture stays import-first: Growovo never logs in, solves challenges or drives a
consumer AI product automatically. T1/T2 rows are recorded by a named operator.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path

from .geo_entity import GeoError
from .geo_questions import load as load_questions
from .geo_runs import (add_not_run, answers_dir, captures_dir, import_observations,
                       load as load_run, observations as load_observations)


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _read_rows(file_path) -> list:
    path = Path(file_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GeoError("采集文件不可读：%s" % exc, "GEO_CAPTURE_UNREADABLE") from exc
    if not text.strip():
        raise GeoError("采集文件为空", "GEO_CAPTURE_EMPTY")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        rows = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise GeoError("采集文件第 %d 行不是 JSON" % number, "GEO_CAPTURE_INVALID") from exc
        return rows
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    raise GeoError("采集文件必须是对象、数组或 JSONL", "GEO_CAPTURE_INVALID")


def template(data_dir, run_id: str) -> list:
    run = load_run(data_dir, run_id)
    question_set = load_questions(data_dir, run["set_id"], run["set_version"])
    questions = {row["id"]: row for row in question_set.get("questions") or []}
    rows = []
    for question_id in run["question_ids"]:
        question = questions[question_id]
        for surface in run["surfaces"]:
            for sample in range(1, run["samples_per_question"] + 1):
                rows.append({
                    "observation_id": "%s-%s-%s-%d" % (run_id, question_id, surface["name"], sample),
                    "question_id": question_id,
                    "surface": surface["name"],
                    "tier": surface["tier"],
                    "capture_mode": surface["capture_mode"],
                    "session": surface["session"],
                    "sampled_at": "",
                    "operator": run["operator"],
                    "answer_text": "",
                    "mentions": [],
                    "cited_urls": [],
                    "judgement": {"recommended": False, "accurate": None, "basis": ""},
                    "fact_conflicts": [],
                    "notes": "",
                    "question": question["text"],
                    "success_reason": question["success_reason"],
                    "must_not": question["must_not"],
                })
    return rows


def _surface(run, name):
    for item in run.get("surfaces") or []:
        if item.get("name") == name:
            return item
    raise GeoError("run 没有 surface %s" % name, "GEO_SURFACE_UNKNOWN")


def _resolve(path_text, base: Path) -> Path:
    path = Path(str(path_text)).expanduser()
    if not path.is_absolute():
        path = base / path
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prepare(data_dir, run_id: str, row):
    run = load_run(data_dir, run_id)
    if run.get("finished_at"):
        raise GeoError("run 已关闭，不能导入观测", "GEO_RUN_CLOSED")
    if not isinstance(row, dict):
        raise GeoError("每条观测必须是 JSON 对象", "GEO_CAPTURE_INVALID")
    if row.get("not_run"):
        return {"kind": "not_run", "row": row}
    value = dict(row)
    value["schema"] = "growovo.geo-observation/1"
    value.setdefault("operator", run["operator"])
    surface = _surface(run, value.get("surface"))
    value.setdefault("tier", surface["tier"])
    value.setdefault("capture_mode", surface["capture_mode"])
    value.setdefault("session", surface["session"])
    value.setdefault("mentions", [])
    value.setdefault("cited_urls", [])
    value.setdefault("fact_conflicts", [])
    value.setdefault("notes", "")
    judgement = value.get("judgement")
    if not isinstance(judgement, dict):
        raise GeoError("judgement 必须是对象", "GEO_CAPTURE_INVALID")
    judgement = dict(judgement)
    judgement.setdefault("accurate", None)
    judgement.setdefault("basis", "")
    value["judgement"] = judgement
    observation_id = str(value.get("observation_id") or "").strip()
    if not observation_id:
        raise GeoError("observation_id 不能为空；用 capture template 生成", "GEO_CAPTURE_INVALID")
    source_dir = Path(row.get("_source_dir") or ".")
    if value.get("answer_text"):
        text = str(value["answer_text"])
        answer_path = answers_dir(data_dir, run_id) / (observation_id + ".md")
        answer_path.write_text(text, encoding="utf-8")
    elif value.get("answer_path"):
        source = _resolve(value["answer_path"], source_dir)
        if not source.is_file():
            raise GeoError("answer_path 不存在：%s" % source, "GEO_CAPTURE_UNREADABLE")
        text = source.read_text(encoding="utf-8")
        answer_path = answers_dir(data_dir, run_id) / (observation_id + ".md")
        answer_path.write_text(text, encoding="utf-8")
    else:
        raise GeoError("每条观测需要 answer_text 或 answer_path", "GEO_CAPTURE_INVALID")
    value["answer"] = {"path": str(answer_path), "sha256": _sha256(answer_path),
                       "chars": len(text)}
    evidence = value.get("capture_evidence")
    if evidence:
        source = _resolve(evidence.get("path"), source_dir)
        if not source.is_file():
            raise GeoError("capture_evidence.path 不存在：%s" % source, "GEO_CAPTURE_UNREADABLE")
        target = captures_dir(data_dir, run_id) / (observation_id + source.suffix)
        shutil.copyfile(source, target)
        value["capture_evidence"] = {"path": str(target), "sha256": _sha256(target)}
    for key in ("answer_text", "answer_path", "_source_dir", "question",
                "success_reason", "must_not"):
        value.pop(key, None)
    return {"kind": "observation", "row": value}


def import_file(data_dir, run_id: str, file_path):
    rows = _read_rows(file_path)
    source_dir = Path(file_path).resolve().parent
    existing = {row["observation_id"] for row in load_observations(data_dir, run_id)}
    prepared = []
    duplicates = 0
    for row in rows:
        if isinstance(row, dict):
            row = dict(row)
            row["_source_dir"] = str(source_dir)
            if str(row.get("observation_id") or "") in existing:
                duplicates += 1
                continue
        prepared.append(_prepare(data_dir, run_id, row))
    not_run_rows = [item["row"] for item in prepared if item["kind"] == "not_run"]
    observation_rows = [item["row"] for item in prepared if item["kind"] == "observation"]
    for row in not_run_rows:
        add_not_run(data_dir, run_id, row.get("question_id"), row.get("surface"),
                    row.get("reason") or row.get("note") or "")
    result = {"run_id": run_id, "not_run": len(not_run_rows), "duplicates": duplicates}
    if observation_rows:
        imported = import_observations(data_dir, run_id, observation_rows)
        result.update(imported)
        result["duplicates"] = int(imported.get("duplicates") or 0) + duplicates
    else:
        result.update({"added": 0, "observation_count": len(existing)})
    return result
