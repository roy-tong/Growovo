"""GEO sampling runs and append-only observations."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid
from pathlib import Path

from .geo_entity import GeoError
from .geo_questions import load as load_questions
from .storage import AppendOnlyJSONL, AtomicJSONStore


SCHEMA = "growovo.geo-run/1"
OBSERVATION_SCHEMA = "growovo.geo-observation/1"
TIERS = ("T0", "T1", "T2")
CAPTURE_MODES = ("api", "ui_observed", "manual_import")
SESSIONS = ("fresh", "unknown")
SEVERITIES = ("minor", "major", "critical")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,119}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def directory(data_dir, run_id: str) -> Path:
    if not _ID.fullmatch(str(run_id or "")):
        raise GeoError("run_id 只能使用小写字母、数字、下划线和连字符", "GEO_RUN_ID")
    return Path(data_dir) / "geo" / "runs" / run_id


def run_path(data_dir, run_id: str) -> Path:
    return directory(data_dir, run_id) / "run.json"


def observations_path(data_dir, run_id: str) -> Path:
    return directory(data_dir, run_id) / "observations.jsonl"


def answers_dir(data_dir, run_id: str) -> Path:
    path = directory(data_dir, run_id) / "answers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def captures_dir(data_dir, run_id: str) -> Path:
    path = directory(data_dir, run_id) / "captures"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _instant(value, label: str) -> str:
    text = str(value or "").strip()
    try:
        stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GeoError("%s 必须是 ISO 8601 时间" % label) from exc
    if stamp.tzinfo is None:
        raise GeoError("%s 必须包含时区" % label)
    return text


def manifest_sha256(run: dict) -> str:
    payload = {
        "set_id": run.get("set_id"),
        "set_version": run.get("set_version"),
        "set_sha256": run.get("set_sha256"),
        "entity_version": run.get("entity_version"),
        "surfaces": run.get("surfaces"),
        "samples_per_question": run.get("samples_per_question"),
        "question_ids": run.get("question_ids"),
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _validate_surfaces(surfaces):
    errors = []
    if not isinstance(surfaces, list) or not surfaces:
        return ["surfaces 必须是非空数组"]
    seen = set()
    for index, item in enumerate(surfaces):
        label = "surfaces[%d]" % index
        if not isinstance(item, dict):
            errors.append(label + " 必须是对象")
            continue
        name = str(item.get("name") or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
            errors.append(label + ".name 只能使用小写字母、数字、下划线和连字符")
        elif name in seen:
            errors.append(label + ".name 重复")
        seen.add(name)
        if item.get("tier") not in TIERS:
            errors.append(label + ".tier 必须是 %s" % " / ".join(TIERS))
        if item.get("capture_mode") not in CAPTURE_MODES:
            errors.append(label + ".capture_mode 必须是 %s" % " / ".join(CAPTURE_MODES))
        if item.get("session") not in SESSIONS:
            errors.append(label + ".session 必须是 fresh 或 unknown")
    return errors


def validate_run(run) -> list:
    errors = []
    if not isinstance(run, dict):
        return ["run 必须是 JSON 对象"]
    if run.get("schema") != SCHEMA:
        errors.append("schema 必须是 %s" % SCHEMA)
    if not _ID.fullmatch(str(run.get("run_id") or "")):
        errors.append("run_id 格式不正确")
    if not _ID.fullmatch(str(run.get("set_id") or "")):
        errors.append("set_id 格式不正确")
    if not isinstance(run.get("set_version"), int) or run.get("set_version", 0) < 1:
        errors.append("set_version 必须是 >= 1 的整数")
    if not _HEX.fullmatch(str(run.get("set_sha256") or "")):
        errors.append("set_sha256 必须是 64 位十六进制")
    if not isinstance(run.get("entity_version"), int) or run.get("entity_version", 0) < 1:
        errors.append("entity_version 必须是 >= 1 的整数")
    try:
        _instant(run.get("started_at"), "started_at")
    except GeoError as exc:
        errors.append(str(exc))
    if run.get("finished_at"):
        try:
            _instant(run["finished_at"], "finished_at")
        except GeoError as exc:
            errors.append(str(exc))
    if not str(run.get("operator") or "").strip():
        errors.append("operator 不能为空")
    errors.extend(_validate_surfaces(run.get("surfaces")))
    if not isinstance(run.get("samples_per_question"), int) or run.get("samples_per_question", 0) < 1:
        errors.append("samples_per_question 必须是 >= 1 的整数")
    question_ids = run.get("question_ids")
    if not isinstance(question_ids, list) or not question_ids:
        errors.append("question_ids 必须是非空数组")
    elif any(not _ID.fullmatch(str(row)) for row in question_ids):
        errors.append("question_ids 含格式不正确的 id")
    elif len(set(question_ids)) != len(question_ids):
        errors.append("question_ids 含重复项")
    return errors


def start(data_dir, set_id: str, *, surfaces, samples_per_question: int = 3,
          operator: str = "", set_version=None, question_ids=None, run_id=None):
    question_set = load_questions(data_dir, set_id, set_version)
    if not question_set.get("frozen_at"):
        raise GeoError("问题集尚未冻结；先运行 mkt geo questions freeze", "GEO_SET_NOT_FROZEN")
    from . import geo_entity
    entity = geo_entity.load(data_dir)
    all_questions = question_set.get("questions") or []
    available = [row["id"] for row in all_questions]
    selected = list(question_ids) if question_ids else available
    unknown = sorted(set(selected) - set(available))
    if unknown:
        raise GeoError("问题集没有这些 id：%s" % ", ".join(unknown), "GEO_QUESTION_UNKNOWN")
    run_id = str(run_id or ("geo-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]))
    if not _ID.fullmatch(run_id):
        raise GeoError("run_id 只能使用小写字母、数字、下划线和连字符", "GEO_RUN_ID")
    if run_path(data_dir, run_id).exists():
        raise GeoError("run %s 已存在" % run_id, "GEO_RUN_EXISTS")
    errors = _validate_surfaces(surfaces)
    if errors:
        raise GeoError("surfaces 无效：" + "；".join(errors), "GEO_SURFACE_INVALID")
    samples_per_question = int(samples_per_question)
    if samples_per_question < 1:
        raise GeoError("samples_per_question 必须是 >= 1 的整数", "GEO_SAMPLE_COUNT")
    operator = str(operator or "").strip()
    if not operator:
        raise GeoError("operator 不能为空", "GEO_OPERATOR")
    run = {
        "schema": SCHEMA,
        "run_id": run_id,
        "set_id": set_id,
        "set_version": question_set["version"],
        "set_sha256": question_set.get("content_sha256") or "",
        "entity_version": int(entity["version"]),
        "started_at": _now(),
        "finished_at": "",
        "operator": operator,
        "surfaces": surfaces,
        "samples_per_question": samples_per_question,
        "question_ids": selected,
        "not_run": [],
        "observation_count": 0,
    }
    run["manifest_sha256"] = manifest_sha256(run)
    errors = validate_run(run)
    if errors:
        raise GeoError("run 无效：" + "；".join(errors), "GEO_RUN_INVALID")
    AtomicJSONStore(run_path(data_dir, run_id)).save(run)
    return run


def load(data_dir, run_id: str):
    file_path = run_path(data_dir, run_id)
    if not file_path.is_file():
        raise GeoError("run %s 不存在" % run_id, "GEO_RUN_MISSING")
    run = AtomicJSONStore(file_path).load()
    errors = validate_run(run)
    if errors:
        raise GeoError("run 无效：" + "；".join(errors), "GEO_RUN_INVALID")
    return run


def observations(data_dir, run_id: str) -> list:
    rows = AppendOnlyJSONL(observations_path(data_dir, run_id)).all()
    for row in rows:
        errors = validate_observation(row)
        if errors:
            raise GeoError("观测记录无效：" + "；".join(errors), "GEO_OBSERVATION_INVALID")
    return rows


def validate_observation(row) -> list:
    errors = []
    if not isinstance(row, dict):
        return ["观测必须是 JSON 对象"]
    if row.get("schema") != OBSERVATION_SCHEMA:
        errors.append("schema 必须是 %s" % OBSERVATION_SCHEMA)
    if not _ID.fullmatch(str(row.get("observation_id") or "")):
        errors.append("observation_id 格式不正确")
    if not _ID.fullmatch(str(row.get("question_id") or "")):
        errors.append("question_id 格式不正确")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", str(row.get("surface") or "")):
        errors.append("surface 格式不正确")
    if row.get("tier") not in TIERS:
        errors.append("tier 必须是 %s" % " / ".join(TIERS))
    if row.get("capture_mode") not in CAPTURE_MODES:
        errors.append("capture_mode 必须是 %s" % " / ".join(CAPTURE_MODES))
    if row.get("session") not in SESSIONS:
        errors.append("session 必须是 fresh 或 unknown")
    try:
        _instant(row.get("sampled_at"), "sampled_at")
    except GeoError as exc:
        errors.append(str(exc))
    if not str(row.get("operator") or "").strip():
        errors.append("operator 不能为空")
    answer = row.get("answer")
    if not isinstance(answer, dict):
        errors.append("answer 必须是对象")
    else:
        if not str(answer.get("path") or "").strip():
            errors.append("answer.path 不能为空")
        if not _HEX.fullmatch(str(answer.get("sha256") or "")):
            errors.append("answer.sha256 必须是 64 位十六进制")
        if not isinstance(answer.get("chars"), int) or answer.get("chars", -1) < 0:
            errors.append("answer.chars 必须是 >= 0 的整数")
    mentions = row.get("mentions")
    if not isinstance(mentions, list):
        errors.append("mentions 必须是数组")
        mentions = []
    for index, item in enumerate(mentions):
        label = "mentions[%d]" % index
        if not isinstance(item, dict):
            errors.append(label + " 必须是对象")
            continue
        if not str(item.get("entity") or "").strip():
            errors.append(label + ".entity 不能为空")
        if not isinstance(item.get("is_target"), bool):
            errors.append(label + ".is_target 必须是布尔值")
        if item.get("position") is not None and (
                not isinstance(item.get("position"), int) or item["position"] < 1):
            errors.append(label + ".position 必须是正整数或 null")
        if not str(item.get("verbatim") or "").strip():
            errors.append(label + ".verbatim 不能为空")
    cited = row.get("cited_urls")
    if not isinstance(cited, list) or any(
            not str(url).startswith(("http://", "https://")) for url in cited):
        errors.append("cited_urls 必须是 http(s) URL 数组")
    judgement = row.get("judgement")
    if not isinstance(judgement, dict):
        errors.append("judgement 必须是对象")
    else:
        if not isinstance(judgement.get("recommended"), bool):
            errors.append("judgement.recommended 必须是布尔值")
        if judgement.get("accurate") not in (True, False, None):
            errors.append("judgement.accurate 必须是布尔值或 null")
        if judgement.get("recommended") and not str(judgement.get("basis") or "").strip():
            errors.append("judgement.basis 在 recommended=true 时不能为空")
    conflicts = row.get("fact_conflicts")
    if not isinstance(conflicts, list):
        errors.append("fact_conflicts 必须是数组")
        conflicts = []
    for index, item in enumerate(conflicts):
        label = "fact_conflicts[%d]" % index
        if not isinstance(item, dict):
            errors.append(label + " 必须是对象")
            continue
        for key in ("fact_id", "expected", "observed"):
            if not str(item.get(key) or "").strip():
                errors.append("%s.%s 不能为空" % (label, key))
        if item.get("severity") not in SEVERITIES:
            errors.append(label + ".severity 必须是 %s" % " / ".join(SEVERITIES))
    evidence = row.get("capture_evidence")
    if evidence is not None:
        if not isinstance(evidence, dict) or not str(evidence.get("path") or "").strip():
            errors.append("capture_evidence.path 不能为空")
        elif not _HEX.fullmatch(str(evidence.get("sha256") or "")):
            errors.append("capture_evidence.sha256 必须是 64 位十六进制")
    return errors


def _surface(run, name):
    for item in run.get("surfaces") or []:
        if item.get("name") == name:
            return item
    raise GeoError("run 没有 surface %s" % name, "GEO_SURFACE_UNKNOWN")


def import_observations(data_dir, run_id: str, rows):
    run = load(data_dir, run_id)
    if run.get("finished_at"):
        raise GeoError("run 已关闭，不能追加观测", "GEO_RUN_CLOSED")
    if not isinstance(rows, list) or not rows:
        raise GeoError("没有可导入的观测", "GEO_OBSERVATION_EMPTY")
    known = {row.get("observation_id") for row in observations(data_dir, run_id)}
    added = 0
    duplicates = 0
    store = AppendOnlyJSONL(observations_path(data_dir, run_id))
    for row in rows:
        errors = validate_observation(row)
        if errors:
            raise GeoError("观测记录无效：" + "；".join(errors), "GEO_OBSERVATION_INVALID")
        if row["question_id"] not in run["question_ids"]:
            raise GeoError("观测引用了 run 之外的问题 %s" % row["question_id"], "GEO_QUESTION_UNKNOWN")
        surface = _surface(run, row["surface"])
        if (row["tier"], row["capture_mode"], row["session"]) != (
                surface["tier"], surface["capture_mode"], surface["session"]):
            raise GeoError("观测的 tier/capture_mode/session 与 surface 配置不一致",
                           "GEO_SURFACE_MISMATCH")
        if row["observation_id"] in known:
            duplicates += 1
            continue
        store.append(row)
        known.add(row["observation_id"])
        added += 1
    if added:
        run["observation_count"] = len(known)
        AtomicJSONStore(run_path(data_dir, run_id)).save(run)
    return {"run_id": run_id, "added": added, "duplicates": duplicates,
            "observation_count": len(known)}


def add_not_run(data_dir, run_id: str, question_id: str, surface: str, reason: str):
    run = load(data_dir, run_id)
    if run.get("finished_at"):
        raise GeoError("run 已关闭，不能追加 not_run", "GEO_RUN_CLOSED")
    if question_id not in run["question_ids"]:
        raise GeoError("问题 %s 不在 run 中" % question_id, "GEO_QUESTION_UNKNOWN")
    _surface(run, surface)
    reason = str(reason or "").strip()
    if not reason:
        raise GeoError("not_run 必须写原因", "GEO_NOT_RUN_REASON")
    key = (question_id, surface)
    if any((row.get("question_id"), row.get("surface")) == key for row in run.get("not_run") or []):
        return {"status": "unchanged", "run_id": run_id, "question_id": question_id,
                "surface": surface}
    run.setdefault("not_run", []).append(
        {"question_id": question_id, "surface": surface, "reason": reason, "recorded_at": _now()})
    AtomicJSONStore(run_path(data_dir, run_id)).save(run)
    return {"status": "recorded", "run_id": run_id, "question_id": question_id,
            "surface": surface, "reason": reason}


def close(data_dir, run_id: str):
    run = load(data_dir, run_id)
    rows = observations(data_dir, run_id)
    run["observation_count"] = len(rows)
    if not run.get("finished_at"):
        run["finished_at"] = _now()
    run["manifest_sha256"] = manifest_sha256(run)
    AtomicJSONStore(run_path(data_dir, run_id)).save(run)
    return run


def status(data_dir, run_id: str):
    run = load(data_dir, run_id)
    rows = observations(data_dir, run_id)
    by_surface = {}
    for row in rows:
        by_surface[row["surface"]] = by_surface.get(row["surface"], 0) + 1
    return {
        "run_id": run_id,
        "set_id": run["set_id"],
        "set_version": run["set_version"],
        "finished": bool(run.get("finished_at")),
        "questions": len(run["question_ids"]),
        "samples_per_question": run["samples_per_question"],
        "expected": len(run["question_ids"]) * len(run["surfaces"]) * run["samples_per_question"],
        "observations": len(rows),
        "by_surface": by_surface,
        "not_run": len(run.get("not_run") or []),
        "manifest_sha256": run.get("manifest_sha256") or "",
    }
