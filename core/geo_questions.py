"""Frozen GEO question sets: the only stable unit for before/after comparison."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from .geo_entity import GeoError
from .storage import AtomicJSONStore


SCHEMA = "growovo.geo-question-set/1"
INTENTS = (
    "category_discovery",
    "recommendation",
    "comparison",
    "capability_lookup",
    "procurement_intent",
    "error_fix",
)
BRAND_SCOPES = ("brand", "nonbrand")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def directory(data_dir, set_id: str) -> Path:
    if not _ID.fullmatch(str(set_id or "")):
        raise GeoError("set_id 只能使用小写字母、数字和连字符", "GEO_SET_ID")
    return Path(data_dir) / "geo" / "question-sets" / set_id


def path(data_dir, set_id: str, version: int) -> Path:
    return directory(data_dir, set_id) / ("%d.json" % int(version))


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_sha256(document) -> str:
    payload = {"set_id": document.get("set_id"), "questions": document.get("questions") or []}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _instant(value, label: str) -> str:
    text = str(value or "").strip()
    try:
        stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GeoError("%s 必须是 ISO 8601 时间" % label) from exc
    if stamp.tzinfo is None:
        raise GeoError("%s 必须包含时区" % label)
    return text


def validate(document) -> list:
    errors = []
    if not isinstance(document, dict):
        return ["问题集必须是 JSON 对象"]
    if document.get("schema") != SCHEMA:
        errors.append("schema 必须是 %s" % SCHEMA)
    set_id = str(document.get("set_id") or "").strip()
    if not _ID.fullmatch(set_id):
        errors.append("set_id 只能使用小写字母、数字和连字符")
    if not isinstance(document.get("version"), int) or document.get("version", 0) < 1:
        errors.append("version 必须是 >= 1 的整数")
    if not _ID.fullmatch(str(document.get("entity_id") or "").strip()):
        errors.append("entity_id 只能使用小写字母、数字和连字符")
    for key in ("language", "locale"):
        if not str(document.get(key) or "").strip():
            errors.append("%s 不能为空" % key)
    questions = document.get("questions")
    if not isinstance(questions, list) or not questions:
        errors.append("questions 必须是非空数组")
        questions = []
    seen_ids = set()
    seen_text = set()
    for index, item in enumerate(questions):
        label = "questions[%d]" % index
        if not isinstance(item, dict):
            errors.append(label + " 必须是对象")
            continue
        question_id = str(item.get("id") or "").strip()
        if not _ID.fullmatch(question_id):
            errors.append(label + ".id 只能使用小写字母、数字和连字符")
        elif question_id in seen_ids:
            errors.append(label + ".id 重复")
        seen_ids.add(question_id)
        text = str(item.get("text") or "").strip()
        if not text:
            errors.append(label + ".text 不能为空")
        else:
            normalized = " ".join(text.lower().split())
            if normalized in seen_text:
                errors.append(label + ".text 重复")
            seen_text.add(normalized)
        if item.get("intent") not in INTENTS:
            errors.append(label + ".intent 必须是 %s" % " / ".join(INTENTS))
        if item.get("brand_scope") not in BRAND_SCOPES:
            errors.append(label + ".brand_scope 必须是 brand 或 nonbrand")
        for key in ("success_reason", "must_not"):
            if not str(item.get(key) or "").strip():
                errors.append("%s.%s 不能为空" % (label, key))
        competitors = item.get("competitors")
        if competitors is not None:
            if not isinstance(competitors, list) or any(
                    not str(row).strip() for row in competitors):
                errors.append(label + ".competitors 必须是非空字符串数组")
        weight = item.get("weight", 1)
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
            errors.append(label + ".weight 必须是正数")
    if document.get("frozen_at"):
        try:
            _instant(document["frozen_at"], "frozen_at")
        except GeoError as exc:
            errors.append(str(exc))
        declared = str(document.get("content_sha256") or "")
        if declared != content_sha256(document):
            errors.append("content_sha256 与当前问题内容不一致")
    return errors


def _versions(data_dir, set_id: str) -> list:
    folder = directory(data_dir, set_id)
    if not folder.is_dir():
        return []
    versions = []
    for item in folder.glob("*.json"):
        try:
            versions.append(int(item.stem))
        except ValueError:
            continue
    return sorted(versions)


def load(data_dir, set_id: str, version=None):
    versions = _versions(data_dir, set_id)
    if not versions:
        raise GeoError("问题集 %s 不存在" % set_id, "GEO_SET_MISSING")
    if version is None:
        version = versions[-1]
    try:
        version = int(version)
    except (TypeError, ValueError) as exc:
        raise GeoError("version 必须是整数", "GEO_SET_VERSION") from exc
    file_path = path(data_dir, set_id, version)
    if not file_path.is_file():
        raise GeoError("问题集 %s v%s 不存在" % (set_id, version), "GEO_SET_MISSING")
    document = AtomicJSONStore(file_path).load()
    errors = validate(document)
    if errors:
        raise GeoError("问题集无效：" + "；".join(errors), "GEO_SET_INVALID")
    return document


def list_sets(data_dir) -> list:
    root = Path(data_dir) / "geo" / "question-sets"
    if not root.is_dir():
        return []
    rows = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or not _ID.fullmatch(folder.name):
            continue
        versions = _versions(data_dir, folder.name)
        if not versions:
            continue
        latest = load(data_dir, folder.name, versions[-1])
        rows.append({
            "set_id": folder.name,
            "latest_version": versions[-1],
            "versions": versions,
            "questions": len(latest.get("questions") or []),
            "frozen": bool(latest.get("frozen_at")),
            "content_sha256": latest.get("content_sha256") or "",
        })
    return rows


def import_set(data_dir, document):
    errors = validate(document)
    if errors:
        raise GeoError("问题集无效：" + "；".join(errors), "GEO_SET_INVALID")
    set_id = document["set_id"]
    versions = _versions(data_dir, set_id)
    requested = document.get("version")
    if requested is None:
        requested = (versions[-1] + 1) if versions else 1
    requested = int(requested)
    if requested in versions:
        existing = load(data_dir, set_id, requested)
        if content_sha256(existing) == content_sha256(document):
            return {"status": "unchanged", "set_id": set_id, "version": requested,
                    "path": str(path(data_dir, set_id, requested))}
        raise GeoError("问题集 %s v%s 已存在且内容不同；冻结版本不可改，请升版本"
                       % (set_id, requested), "GEO_SET_IMMUTABLE")
    if versions and requested <= versions[-1]:
        raise GeoError("新版本号必须大于当前最新版本 v%s" % versions[-1], "GEO_SET_VERSION")
    value = copy.deepcopy(document)
    value.pop("frozen_at", None)
    value.pop("content_sha256", None)
    value["version"] = requested
    AtomicJSONStore(path(data_dir, set_id, requested)).save(value)
    return {"status": "imported", "set_id": set_id, "version": requested,
            "path": str(path(data_dir, set_id, requested))}


def freeze(data_dir, set_id: str, version=None):
    document = load(data_dir, set_id, version)
    version = document["version"]
    digest = content_sha256(document)
    if document.get("frozen_at"):
        if document.get("content_sha256") != digest:
            raise GeoError("已冻结问题集的 content_sha256 不一致", "GEO_SET_INVALID")
        return {"status": "already_frozen", "set_id": set_id, "version": version,
                "content_sha256": digest, "frozen_at": document["frozen_at"]}
    document["frozen_at"] = _now()
    document["content_sha256"] = digest
    errors = validate(document)
    if errors:
        raise GeoError("问题集冻结失败：" + "；".join(errors), "GEO_SET_INVALID")
    AtomicJSONStore(path(data_dir, set_id, version)).save(document)
    return {"status": "frozen", "set_id": set_id, "version": version,
            "content_sha256": digest, "frozen_at": document["frozen_at"]}


def questions(document, *, intents=None, brand_scope=None) -> list:
    rows = []
    for item in document.get("questions") or []:
        if intents and item.get("intent") not in intents:
            continue
        if brand_scope and item.get("brand_scope") != brand_scope:
            continue
        rows.append(item)
    return rows
