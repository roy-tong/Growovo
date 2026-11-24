"""GEO entity facts: one canonical description that pages, packages and reports must match."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from .storage import AtomicJSONStore, atomic_write_text


SCHEMA = "growovo.geo-entity/1"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_HEX = re.compile(r"^[0-9a-f]{64}$")


class GeoError(ValueError):
    """Input or state error that the CLI can present without a traceback."""

    def __init__(self, message: str, code: str = "GEO_INPUT"):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def path(data_dir) -> Path:
    return Path(data_dir) / "geo" / "entity.json"


def history_path(data_dir, digest: str) -> Path:
    return Path(data_dir) / "geo" / "entity-history" / (digest + ".json")


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(document) -> str:
    payload = {key: value for key, value in document.items()
               if key not in ("version", "updated_at")}
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


def _https(value, label: str) -> str:
    text = str(value or "").strip()
    if not text.startswith("https://"):
        raise GeoError("%s 必须是 https URL" % label)
    return text


def validate(document) -> list:
    errors = []
    if not isinstance(document, dict):
        return ["事实表必须是 JSON 对象"]
    if document.get("schema") != SCHEMA:
        errors.append("schema 必须是 %s" % SCHEMA)
    entity_id = str(document.get("entity_id") or "").strip()
    if not _ID.fullmatch(entity_id):
        errors.append("entity_id 只能使用小写字母、数字和连字符")
    if not isinstance(document.get("version"), int) or document.get("version", 0) < 1:
        errors.append("version 必须是 >= 1 的整数")
    owner = str(document.get("owner") or "").strip()
    if not owner:
        errors.append("owner 不能为空")
    if document.get("updated_at"):
        try:
            _instant(document["updated_at"], "updated_at")
        except GeoError as exc:
            errors.append(str(exc))

    canonical = document.get("canonical")
    if not isinstance(canonical, dict):
        errors.append("canonical 必须是对象")
        canonical = {}
    for key in ("name", "one_line", "category", "pricing_posture"):
        if not str(canonical.get(key) or "").strip():
            errors.append("canonical.%s 不能为空" % key)
    one_line = str(canonical.get("one_line") or "")
    if len(one_line) > 160:
        errors.append("canonical.one_line 不能超过 160 字符")
    icp = canonical.get("icp")
    if not isinstance(icp, list) or not icp:
        errors.append("canonical.icp 必须是非空数组")
    elif any(not str(item).strip() for item in icp):
        errors.append("canonical.icp 不能含空项")

    capabilities = canonical.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        errors.append("canonical.capabilities 必须是非空数组")
        capabilities = []
    seen = set()
    for index, item in enumerate(capabilities):
        label = "canonical.capabilities[%d]" % index
        if not isinstance(item, dict):
            errors.append(label + " 必须是对象")
            continue
        cap_id = str(item.get("id") or "").strip()
        if not _ID.fullmatch(cap_id):
            errors.append(label + ".id 只能使用小写字母、数字和连字符")
        elif cap_id in seen:
            errors.append(label + ".id 重复")
        seen.add(cap_id)
        for key in ("statement", "limits"):
            if not str(item.get(key) or "").strip():
                errors.append("%s.%s 不能为空" % (label, key))

    urls = canonical.get("urls")
    if not isinstance(urls, dict):
        errors.append("canonical.urls 必须是对象")
        urls = {}
    for key in ("homepage", "repo"):
        try:
            _https(urls.get(key), "canonical.urls.%s" % key)
        except GeoError as exc:
            errors.append(str(exc))
    for key in ("docs", "package"):
        if urls.get(key):
            try:
                _https(urls[key], "canonical.urls.%s" % key)
            except GeoError as exc:
                errors.append(str(exc))

    sources = document.get("fact_sources")
    if not isinstance(sources, list) or not sources:
        errors.append("fact_sources 必须是非空数组")
        sources = []
    source_ids = set()
    for index, item in enumerate(sources):
        label = "fact_sources[%d]" % index
        if not isinstance(item, dict):
            errors.append(label + " 必须是对象")
            continue
        source_id = str(item.get("id") or "").strip()
        if not _ID.fullmatch(source_id):
            errors.append(label + ".id 只能使用小写字母、数字和连字符")
        elif source_id in source_ids:
            errors.append(label + ".id 重复")
        source_ids.add(source_id)
        try:
            _https(item.get("url"), label + ".url")
        except GeoError as exc:
            errors.append(str(exc))
        try:
            _instant(item.get("checked_at"), label + ".checked_at")
        except GeoError as exc:
            errors.append(str(exc))
        if not _HEX.fullmatch(str(item.get("sha256") or "").lower()):
            errors.append(label + ".sha256 必须是 64 位十六进制")
    return errors


def load(data_dir, *, required: bool = True):
    document = AtomicJSONStore(path(data_dir)).load()
    if not document:
        if required:
            raise GeoError("GEO 实体事实表不存在；先运行 mkt geo entity init --file <json>",
                           "GEO_ENTITY_MISSING")
        return None
    errors = validate(document)
    if errors:
        raise GeoError("GEO 实体事实表无效：" + "；".join(errors), "GEO_ENTITY_INVALID")
    return document


def save(data_dir, document, *, expected_version=None):
    errors = validate(document)
    if errors:
        raise GeoError("GEO 实体事实表无效：" + "；".join(errors), "GEO_ENTITY_INVALID")
    existing = load(data_dir, required=False)
    value = copy.deepcopy(document)
    if existing is None:
        value.setdefault("version", 1)
    else:
        expected = expected_version if expected_version is not None else value.get("version")
        if int(expected or 0) != int(existing["version"]):
            raise GeoError(
                "事实表版本冲突：当前 version=%s，提交的是 %s" % (existing["version"], expected),
                "GEO_VERSION_CONFLICT")
        value["version"] = int(existing["version"]) + 1
    value["updated_at"] = _now()
    digest = fingerprint(value)
    history = history_path(data_dir, digest)
    if not history.is_file():
        atomic_write_text(history, json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    AtomicJSONStore(path(data_dir)).save(value)
    return {"entity": value, "fingerprint": digest, "path": str(path(data_dir))}


def init(data_dir, document):
    existing = load(data_dir, required=False)
    if existing is not None:
        if fingerprint(existing) == fingerprint(document):
            return {"entity": existing, "fingerprint": fingerprint(existing),
                    "path": str(path(data_dir)), "status": "unchanged"}
        if int(document.get("version") or 0) != int(existing["version"]):
            raise GeoError(
                "事实表已存在；更新必须带当前 version=%s" % existing["version"],
                "GEO_VERSION_CONFLICT")
        return save(data_dir, document, expected_version=existing["version"])
    return save(data_dir, document)


def check(data_dir, *, file=None):
    if file:
        try:
            document = json.loads(Path(file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GeoError("事实表不可读：%s" % exc, "GEO_ENTITY_UNREADABLE") from exc
    else:
        document = load(data_dir)
    errors = validate(document)
    return {"schema": SCHEMA, "ok": not errors, "errors": errors,
            "fingerprint": fingerprint(document) if not errors else ""}
