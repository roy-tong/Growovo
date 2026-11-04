#!/usr/bin/env python3
"""GTM OS 能力域与岗位族只读注册表。

数据来自 ``capability_data.json``。所有公开读取函数均返回深拷贝，调用方可以
自由修改结果而不会污染进程内注册表。``lint_registry`` 同时检查编号覆盖、
唯一主能力域、岗位必需字段、上下游引用及模板引用。
"""
from __future__ import annotations

import copy
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

_DATA_PATH = Path(__file__).with_name("capability_data.json")
with _DATA_PATH.open("r", encoding="utf-8") as _handle:
    _DATA = json.load(_handle)

_CAPABILITIES = _DATA["capabilities"]
_ROLE_FAMILIES = _DATA["role_families"]
_CAPABILITY_INDEX = {row["capability_id"]: row for row in _CAPABILITIES}
_ROLE_INDEX = {row["role_id"]: row for row in _ROLE_FAMILIES}

_EXPECTED_CAPABILITY_IDS = {"C%02d" % number for number in range(1, 19)}
_EXPECTED_ROLE_IDS = {"%02d" % number for number in range(7, 47)}
_COMMON_TEMPLATE_REFS = {"T01", "T02", "T31", "T32"}
_TEMPLATE_ID_RE = re.compile(r"^T(?:0[1-9]|[12][0-9]|3[0-2])$")


def list_capabilities() -> List[Dict[str, Any]]:
    """返回全部 18 个能力域，顺序为 C01–C18。"""
    return copy.deepcopy(_CAPABILITIES)


def list_role_families() -> List[Dict[str, Any]]:
    """返回全部 40 个岗位族，顺序为 07–46。"""
    return copy.deepcopy(_ROLE_FAMILIES)


def get_capability(capability_id: str) -> Optional[Dict[str, Any]]:
    """按 C01–C18 查询能力域；未知 ID 返回 ``None``。"""
    if not isinstance(capability_id, str):
        return None
    row = _CAPABILITY_INDEX.get(capability_id)
    return copy.deepcopy(row) if row is not None else None


def get_role_family(role_id: str) -> Optional[Dict[str, Any]]:
    """按两位岗位编号 07–46 查询；未知 ID 返回 ``None``。"""
    if not isinstance(role_id, str):
        return None
    row = _ROLE_INDEX.get(role_id)
    return copy.deepcopy(row) if row is not None else None


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_text_list(errors: List[str], value: Any, label: str) -> None:
    if not isinstance(value, list) or not value:
        errors.append("%s must be a non-empty list" % label)
        return
    for index, item in enumerate(value):
        if not _is_text(item):
            errors.append("%s[%d] must be non-empty text" % (label, index))


def lint_registry() -> List[str]:
    """校验注册表完整性并返回错误列表；空列表表示通过。

    除字段检查外，还验证 07–46 恰好各有一个主能力域，能力域与岗位的
    双向映射一致，handoff 指向已知岗位，以及每个岗位都链接四个通用模板。
    """
    errors: List[str] = []
    if not isinstance(_CAPABILITIES, list) or not isinstance(_ROLE_FAMILIES, list):
        return ["capabilities and role_families must both be lists"]

    capability_ids = [row.get("capability_id") for row in _CAPABILITIES
                      if isinstance(row, dict)]
    role_ids = [row.get("role_id") for row in _ROLE_FAMILIES
                if isinstance(row, dict)]

    for capability_id, count in Counter(capability_ids).items():
        if count > 1:
            errors.append("duplicate capability_id: %s" % capability_id)
    for role_id, count in Counter(role_ids).items():
        if count > 1:
            errors.append("duplicate role_id: %s" % role_id)

    actual_capabilities = set(capability_ids)
    actual_roles = set(role_ids)
    for capability_id in sorted(_EXPECTED_CAPABILITY_IDS - actual_capabilities):
        errors.append("missing capability: %s" % capability_id)
    for capability_id in sorted(actual_capabilities - _EXPECTED_CAPABILITY_IDS,
                                key=str):
        errors.append("unexpected capability: %s" % capability_id)
    for role_id in sorted(_EXPECTED_ROLE_IDS - actual_roles):
        errors.append("missing role family: %s" % role_id)
    for role_id in sorted(actual_roles - _EXPECTED_ROLE_IDS, key=str):
        errors.append("unexpected role family: %s" % role_id)

    known_template_ids = {"T%02d" % number for number in range(1, 33)}
    try:
        from . import templates
        known_template_ids = {
            row.get("template_id") for row in templates.list_templates()
            if isinstance(row, dict)
        }
    except (ImportError, AttributeError, KeyError, TypeError, ValueError):
        # 编号规则仍能发现拼写错误；模板模块存在时会进一步做真实引用检查。
        pass

    primary_assignments: Counter = Counter()
    for index, capability in enumerate(_CAPABILITIES):
        label = "capabilities[%d]" % index
        if not isinstance(capability, dict):
            errors.append("%s must be an object" % label)
            continue
        capability_id = capability.get("capability_id")
        label = "capability %s" % capability_id
        for field in ("capability_id", "name", "mission"):
            if not _is_text(capability.get(field)):
                errors.append("%s.%s must be non-empty text" % (label, field))
        _check_text_list(errors, capability.get("outputs"), label + ".outputs")
        assigned = capability.get("role_ids")
        if not isinstance(assigned, list) or not assigned:
            errors.append("%s.role_ids must be a non-empty list" % label)
            continue
        if len(set(assigned)) != len(assigned):
            errors.append("%s.role_ids contains duplicates" % label)
        for role_id in assigned:
            primary_assignments[role_id] += 1
            if role_id not in actual_roles:
                errors.append("%s references unknown role family: %s" %
                              (label, role_id))

    for index, role in enumerate(_ROLE_FAMILIES):
        label = "role_families[%d]" % index
        if not isinstance(role, dict):
            errors.append("%s must be an object" % label)
            continue
        role_id = role.get("role_id")
        label = "role family %s" % role_id
        for field in ("role_id", "name", "domain", "mission"):
            if not _is_text(role.get(field)):
                errors.append("%s.%s must be non-empty text" % (label, field))
        domain = role.get("domain")
        if domain not in actual_capabilities:
            errors.append("%s references unknown capability: %s" % (label, domain))
        for field in ("responsibilities", "result_kpis", "process_kpis",
                      "guardrail_kpis", "deliverables", "human_approval", "dod"):
            _check_text_list(errors, role.get(field), "%s.%s" % (label, field))

        steps = role.get("sop_steps")
        if not isinstance(steps, list) or not 5 <= len(steps) <= 8:
            errors.append("%s.sop_steps must contain 5–8 steps" % label)
        else:
            step_ids: List[Any] = []
            for step_index, step in enumerate(steps):
                step_label = "%s.sop_steps[%d]" % (label, step_index)
                if not isinstance(step, dict):
                    errors.append("%s must be an object" % step_label)
                    continue
                step_ids.append(step.get("step_id"))
                for field in ("step_id", "summary"):
                    if not _is_text(step.get(field)):
                        errors.append("%s.%s must be non-empty text" %
                                      (step_label, field))
            if len(set(step_ids)) != len(step_ids):
                errors.append("%s.sop_steps contains duplicate step_id" % label)

        refs = role.get("template_refs")
        _check_text_list(errors, refs, label + ".template_refs")
        if isinstance(refs, list):
            if len(set(refs)) != len(refs):
                errors.append("%s.template_refs contains duplicates" % label)
            missing_common = _COMMON_TEMPLATE_REFS - set(refs)
            if missing_common:
                errors.append("%s missing common templates: %s" %
                              (label, ", ".join(sorted(missing_common))))
            for template_id in refs:
                if (not isinstance(template_id, str) or
                        not _TEMPLATE_ID_RE.match(template_id) or
                        template_id not in known_template_ids):
                    errors.append("%s references unknown template: %s" %
                                  (label, template_id))

        handoff = role.get("handoff")
        if not isinstance(handoff, dict):
            errors.append("%s.handoff must be an object" % label)
        else:
            for field in ("package", "acceptance"):
                if not _is_text(handoff.get(field)):
                    errors.append("%s.handoff.%s must be non-empty text" %
                                  (label, field))
            receivers = handoff.get("receivers")
            if not isinstance(receivers, list) or not receivers:
                errors.append("%s.handoff.receivers must be a non-empty list" % label)
            else:
                if len(set(receivers)) != len(receivers):
                    errors.append("%s.handoff.receivers contains duplicates" % label)
                for receiver in receivers:
                    if receiver not in actual_roles:
                        errors.append("%s handoff references unknown role family: %s" %
                                      (label, receiver))
                    if receiver == role_id:
                        errors.append("%s cannot hand off to itself" % label)

    for role_id in sorted(_EXPECTED_ROLE_IDS):
        if primary_assignments[role_id] != 1:
            errors.append("role family %s must have exactly one primary capability; got %d" %
                          (role_id, primary_assignments[role_id]))
    for role in _ROLE_FAMILIES:
        if not isinstance(role, dict):
            continue
        role_id = role.get("role_id")
        capability = _CAPABILITY_INDEX.get(role.get("domain"))
        if isinstance(capability, dict) and role_id not in capability.get("role_ids", []):
            errors.append("role family %s and capability %s mappings disagree" %
                          (role_id, role.get("domain")))

    return errors
