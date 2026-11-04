#!/usr/bin/env python3
"""GTM OS 方案模板只读注册表与 Deliverable 实例基础校验。

``fields`` 是以稳定 slot ID 为键的 flat dict。所有模板都要求 Base Envelope
和本专业字段；``evidence_refs`` 必须是非空列表，每项至少包含非空的
``source_id`` 与 ``locator``。校验只返回错误列表，包括未知模板在内的调用
错误也不会以异常形式抛出。
"""
from __future__ import annotations

import copy
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

_DATA_PATH = Path(__file__).with_name("template_data.json")
with _DATA_PATH.open("r", encoding="utf-8") as _handle:
    _DATA = json.load(_handle)

_TEMPLATES = _DATA["templates"]
_TEMPLATE_INDEX = {row["template_id"]: row for row in _TEMPLATES}
_BASE_REQUIRED_SLOTS = _DATA["base_required_slots"]
_EXPECTED_TEMPLATE_IDS = {"T%02d" % number for number in range(1, 33)}
_EXPECTED_ROLE_IDS = {"%02d" % number for number in range(7, 47)}
_SLOT_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def list_templates() -> List[Dict[str, Any]]:
    """返回全部 32 个模板，顺序为 T01–T32。"""
    return copy.deepcopy(_TEMPLATES)


def get_template(template_id: str) -> Optional[Dict[str, Any]]:
    """按 T01–T32 查询模板；未知 ID 返回 ``None``。"""
    if not isinstance(template_id, str):
        return None
    row = _TEMPLATE_INDEX.get(template_id)
    return copy.deepcopy(row) if row is not None else None


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set, frozenset)):
        return len(value) == 0
    return False


def _check_text_list(errors: List[str], value: Any, label: str) -> None:
    if not isinstance(value, list) or not value:
        errors.append("%s must be a non-empty list" % label)
        return
    for index, item in enumerate(value):
        if not _is_text(item):
            errors.append("%s[%d] must be non-empty text" % (label, index))


def _lint_evidence_refs(value: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(value, list):
        return ["evidence_refs must be a list of evidence objects"]
    if not value:
        return ["evidence_refs must contain at least one evidence object"]
    for index, reference in enumerate(value):
        label = "evidence_refs[%d]" % index
        if not isinstance(reference, dict):
            errors.append("%s must be an object" % label)
            continue
        for field in ("source_id", "locator"):
            if field not in reference:
                errors.append("%s missing %s" % (label, field))
            elif _is_empty(reference[field]):
                errors.append("%s.%s must not be empty" % (label, field))
        if "version" in reference and _is_empty(reference["version"]):
            errors.append("%s.version must not be empty when supplied" % label)
    return errors


def lint_instance(template_id: str, version: str,
                  fields: Dict[str, Any]) -> List[str]:
    """检查模板版本、必填 slot、空值和证据引用结构。

    额外的 flat-dict 字段允许存在，以便行业 / 平台 Overlay 增加约束。数字
    ``0`` 与布尔值 ``False`` 是有效值；``None``、空白字符串和空容器不是。
    未知模板、非 dict 的 fields 同样以错误列表返回。
    """
    if not isinstance(template_id, str) or template_id not in _TEMPLATE_INDEX:
        return ["unknown template: %s" % template_id]
    template = _TEMPLATE_INDEX[template_id]
    errors: List[str] = []
    if version != template["version"]:
        errors.append("template %s version mismatch: expected %s, got %s" %
                      (template_id, template["version"], version))
    if not isinstance(fields, dict):
        errors.append("fields must be a flat dictionary")
        return errors

    for slot in template["required_slots"]:
        if slot not in fields:
            errors.append("missing required slot: %s" % slot)
        elif _is_empty(fields[slot]):
            errors.append("empty required slot: %s" % slot)

    if "evidence_refs" in fields:
        errors.extend(_lint_evidence_refs(fields["evidence_refs"]))
    return errors


def lint_registry() -> List[str]:
    """校验 32 模板及其与 40 个岗位族的双向引用，返回错误列表。"""
    errors: List[str] = []
    if not isinstance(_TEMPLATES, list):
        return ["templates must be a list"]
    _check_text_list(errors, _BASE_REQUIRED_SLOTS, "base_required_slots")
    if len(set(_BASE_REQUIRED_SLOTS)) != len(_BASE_REQUIRED_SLOTS):
        errors.append("base_required_slots contains duplicates")
    for slot in _BASE_REQUIRED_SLOTS:
        if not isinstance(slot, str) or not _SLOT_ID_RE.match(slot):
            errors.append("invalid base slot ID: %s" % slot)

    template_ids = [row.get("template_id") for row in _TEMPLATES
                    if isinstance(row, dict)]
    for template_id, count in Counter(template_ids).items():
        if count > 1:
            errors.append("duplicate template_id: %s" % template_id)
    actual_ids = set(template_ids)
    for template_id in sorted(_EXPECTED_TEMPLATE_IDS - actual_ids):
        errors.append("missing template: %s" % template_id)
    for template_id in sorted(actual_ids - _EXPECTED_TEMPLATE_IDS, key=str):
        errors.append("unexpected template: %s" % template_id)

    known_role_ids = set(_EXPECTED_ROLE_IDS)
    role_rows: List[Dict[str, Any]] = []
    try:
        from . import capabilities
        role_rows = capabilities.list_role_families()
        known_role_ids = {row.get("role_id") for row in role_rows
                          if isinstance(row, dict)}
    except (ImportError, AttributeError, KeyError, TypeError, ValueError):
        pass

    for index, template in enumerate(_TEMPLATES):
        label = "templates[%d]" % index
        if not isinstance(template, dict):
            errors.append("%s must be an object" % label)
            continue
        template_id = template.get("template_id")
        label = "template %s" % template_id
        for field in ("template_id", "name"):
            if not _is_text(template.get(field)):
                errors.append("%s.%s must be non-empty text" % (label, field))
        version = template.get("version")
        if not _is_text(version) or not _VERSION_RE.match(version):
            errors.append("%s.version must be semantic version text" % label)

        roles = template.get("roles")
        if not isinstance(roles, list) or not roles:
            errors.append("%s.roles must be a non-empty list" % label)
        else:
            if len(set(roles)) != len(roles):
                errors.append("%s.roles contains duplicates" % label)
            for role_id in roles:
                if role_id not in known_role_ids:
                    errors.append("%s references unknown role family: %s" %
                                  (label, role_id))

        required_slots = template.get("required_slots")
        if not isinstance(required_slots, list) or not required_slots:
            errors.append("%s.required_slots must be a non-empty list" % label)
            required_slots = []
        else:
            if len(set(required_slots)) != len(required_slots):
                errors.append("%s.required_slots contains duplicates" % label)
            for slot in required_slots:
                if not isinstance(slot, str) or not _SLOT_ID_RE.match(slot):
                    errors.append("%s has invalid slot ID: %s" % (label, slot))
            base_count = len(_BASE_REQUIRED_SLOTS)
            if required_slots[:base_count] != _BASE_REQUIRED_SLOTS:
                errors.append("%s.required_slots must start with Base Envelope slots" % label)

        professional_fields = template.get("professional_fields")
        if not isinstance(professional_fields, list) or not professional_fields:
            errors.append("%s.professional_fields must be a non-empty list" % label)
            professional_fields = []
        field_ids: List[Any] = []
        for field_index, field in enumerate(professional_fields):
            field_label = "%s.professional_fields[%d]" % (label, field_index)
            if not isinstance(field, dict):
                errors.append("%s must be an object" % field_label)
                continue
            field_id = field.get("field_id")
            field_ids.append(field_id)
            if (not isinstance(field_id, str) or
                    not _SLOT_ID_RE.match(field_id)):
                errors.append("%s has invalid field_id: %s" % (field_label, field_id))
            if not _is_text(field.get("label")):
                errors.append("%s.label must be non-empty text" % field_label)
        if len(set(field_ids)) != len(field_ids):
            errors.append("%s.professional_fields contains duplicate field_id" % label)
        if set(field_ids) - set(required_slots):
            errors.append("%s professional field is absent from required_slots" % label)
        expected_slots = list(_BASE_REQUIRED_SLOTS) + field_ids
        if required_slots != expected_slots:
            errors.append("%s.required_slots must equal base slots plus professional fields" %
                          label)
        _check_text_list(errors, template.get("qa"), label + ".qa")

    # 模板 roles 与岗位 template_refs 必须对称，避免出现只在单侧声明的引用。
    for template in _TEMPLATES:
        if not isinstance(template, dict):
            continue
        template_id = template.get("template_id")
        for role in role_rows:
            role_id = role.get("role_id")
            role_has_ref = template_id in role.get("template_refs", [])
            template_has_role = role_id in template.get("roles", [])
            if role_has_ref != template_has_role:
                errors.append("template %s and role family %s references disagree" %
                              (template_id, role_id))
    for role in role_rows:
        role_id = role.get("role_id")
        for template_id in role.get("template_refs", []):
            if template_id not in actual_ids:
                errors.append("role family %s references unknown template: %s" %
                              (role_id, template_id))

    return errors
