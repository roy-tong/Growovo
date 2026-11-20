#!/usr/bin/env python3
"""Template-bound deliverables and independent acceptance reviews."""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .event_store import EventStore
from .permissions import require_actor
from .templates import get_template, lint_instance

PLACEHOLDER_RE = re.compile(r"raft:|TODO|TBD|XXX|\{\{|\}\}")

# 模板残留：字段非空但仍是写模板时留下的记号，不能当交付内容。
RESIDUE_PATTERNS = (
    ("RAFT_SLOT", re.compile(r"raft:")),
    ("TODO", re.compile(r"\bTODO\b", re.I)),
    ("TBD", re.compile(r"\bTBD\b", re.I)),
    ("XXX", re.compile(r"\bXXX\b", re.I)),
    ("MUSTACHE", re.compile(r"\{\{|\}\}")),
    ("LOREM", re.compile(r"lorem ipsum", re.I)),
    ("FILL_HERE", re.compile(r"待补充|待填写|此处填写|请填写")),
    ("SLOT_MARKER", re.compile(r"<[a-z][a-z0-9_]{2,}>")),
)

# 最小语义段：少于这个可见字符数的字段无法承载业务信息。
MIN_SLOT_VISIBLE = 8
MIN_LIST_ITEM_VISIBLE = 4
_VISIBLE_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")

# 标识符槽位：只需非空且不含残留，不按散文长度或空泛度衡量。
# 边界：以下槽位承载的是 ID、枚举或时间，不是散文；其"对不对"仍由
# work_order_id / objective_id / receiver / business_context_ref 的一致性核对判定。
IDENTIFIER_SLOTS = ("deliverable_id", "work_order_id", "objective_id",
                    "business_context_ref", "owner", "receiver",
                    "timezone", "due")

# 标识符型叶子字段：嵌套结构里的 ID、引用、定位符、版本等机器可读值
# （如 evidence_refs[].source_id、locator、version）。它们只查模板残留，
# 不按散文长度或空泛度衡量；父级结构仍由模板 lint 与一致性核对把关。
IDENTIFIER_LEAVES = ("id", "locator", "url", "uri", "path", "sha256",
                     "version", "timezone", "slug", "handle", "due",
                     "timestamp", "created_at", "updated_at", "expires_at")
IDENTIFIER_SUFFIXES = ("_id", "_ref", "_refs", "_key", "_code", "_sha256",
                       "_url", "_uri", "_path", "_version")
# 标识符也不接受"空占位"：这些值看起来非空，但不指向任何对象。
_ID_PLACEHOLDERS = {"n/a", "na", "none", "null", "nil", "-", "--", "?"}


def _is_identifier_leaf(name: str) -> bool:
    return name in IDENTIFIER_LEAVES or name.endswith(IDENTIFIER_SUFFIXES)

# 空泛建议：只有套话，没有对象、数字或可核对的事实。
VAGUE_PHRASES = ("最佳实践", "按计划", "持续优化", "提升效果", "加强运营",
                 "视情况", "酌情", "尽快推进", "高质量完成", "注意风险")
_VAGUE_IDENTIFIER_RE = re.compile(r"[a-z]{2,}")
_DIGIT_RE = re.compile(r"[0-9]")


class DeliverableError(ValueError):
    pass


class ContentUnusableError(DeliverableError):
    """字段非空、格式合法，但内容不可用：残留、过短、空泛或与工单不一致。"""

    def __init__(self, problems: List[Dict[str, Any]]):
        self.problems = problems
        codes = sorted({p["code"] for p in problems})
        fields = sorted({p["field"] for p in problems})
        shown = "、".join(fields[:8]) + ("…" if len(fields) > 8 else "")
        super().__init__(
            f"交付物内容不可用（{len(problems)} 处，涉及字段：{shown}）；"
            f"拒绝原因：{'、'.join(codes)}。修复后再提交。")
        self.payload = {
            "status": "error",
            "reason_code": "CONTENT_UNUSABLE",
            "problems": problems,
            "count": len(problems),
            "codes": codes,
            "hint": _CONTENT_HINT,
        }


class PlaceholderContentError(DeliverableError):
    """字段非空但仍是模板占位内容：验收不能把占位符当交付。"""

    def __init__(self, hits: List[Dict[str, Any]], total: int):
        names = [h["field"] for h in hits]
        shown = "、".join(names[:8]) + ("…" if len(names) > 8 else "")
        super().__init__(
            f"交付物含 {total} 处占位符（{len(names)} 个字段：{shown}）；"
            "请替换为真实内容后再提交")
        self.payload = {
            "status": "error",
            "reason_code": "PLACEHOLDER_CONTENT",
            "fields": names,
            "count": total,
            "hint": "raft:<slot>、TODO、TBD、{{…}} 都是草稿占位；用真实内容替换后再 submit。",
        }


def placeholder_hits(fields: Any) -> tuple:
    """返回 (命中列表, 占位符总数)。命中列表含字段路径与样例。"""
    hits: List[Dict[str, Any]] = []
    total = 0

    def walk(prefix: str, value: Any) -> None:
        nonlocal total
        if isinstance(value, str):
            found = PLACEHOLDER_RE.findall(value)
            if found:
                hits.append({"field": prefix or "(root)", "count": len(found),
                             "sample": value[:60]})
                total += len(found)
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(f"{prefix}.{key}" if prefix else str(key), item)
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                walk(f"{prefix}[{index}]", item)

    walk("", fields)
    return hits, total


_CONTENT_HINT = (
    "交付物要能被接收人直接使用：替换所有 raft:/TODO/{{…}} 残留，"
    "每个必填槽位写出对象、动作与可核对事实，并让 work_order_id、objective_id、"
    "business_context_ref、receiver 与工单一致。"
)


def _visible_len(value: str) -> int:
    return len(_VISIBLE_RE.findall(value))


def _mentions(value: Any, needle: str) -> bool:
    if not needle:
        return True
    if isinstance(value, str):
        return needle in value
    if isinstance(value, dict):
        return any(_mentions(item, needle) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_mentions(item, needle) for item in value)
    return False


def content_problems(item: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    """内容可用性检查：残留、最小语义段、空泛建议、与工单一致性。

    返回 ``(problems, work_order_unresolved)``。一致性只在能解析到
    WorkOrder 时执行；解析不到工单的裸存储路径靠 ``work_order_unresolved``
    显式标记，不静默当作"已核对"，也不阻止内容门本身生效。
    """
    problems: List[Dict[str, Any]] = []
    fields = item.get("fields") or {}
    template = get_template(item.get("template_id")) or {}
    required = list(template.get("required_slots") or [])

    def scan(field: str, value: Any, prose: bool = True) -> None:
        if isinstance(value, str):
            if not value.strip():
                return
            for code, pattern in RESIDUE_PATTERNS:
                if pattern.search(value):
                    problems.append({"field": field, "code": "TEMPLATE_RESIDUE",
                                     "detail": f"{code}: {value[:60]}"})
                    break
            if not prose:
                if value.strip().lower() in _ID_PLACEHOLDERS:
                    problems.append({"field": field, "code": "TOO_THIN",
                                     "detail": f"标识符是空占位：{value.strip()}"})
                return
            visible = _visible_len(value)
            if visible < MIN_SLOT_VISIBLE:
                problems.append({"field": field, "code": "TOO_THIN",
                                 "detail": f"仅 {visible} 个有效字符，无法承载业务信息"})
            elif (visible < 30 and not _DIGIT_RE.search(value)
                  and not _VAGUE_IDENTIFIER_RE.search(value)
                  and any(p in value for p in VAGUE_PHRASES)):
                problems.append({"field": field, "code": "VAGUE_ADVICE",
                                 "detail": f"只有套话，没有对象或可核对事实：{value[:60]}"})
        elif isinstance(value, dict):
            for key, nested in value.items():
                scan(f"{field}.{key}", nested,
                     prose and not _is_identifier_leaf(str(key)))
        elif isinstance(value, (list, tuple)):
            for index, nested in enumerate(value):
                if isinstance(nested, str):
                    if prose and nested.strip() and _visible_len(nested) < MIN_LIST_ITEM_VISIBLE:
                        problems.append({"field": f"{field}[{index}]", "code": "TOO_THIN",
                                         "detail": f"仅 {_visible_len(nested)} 个有效字符"})
                else:
                    scan(f"{field}[{index}]", nested, prose)

    for slot in required:
        scan(slot, fields.get(slot), prose=slot not in IDENTIFIER_SLOTS)

    order = None
    try:
        from .work import WorkStore
        order = WorkStore(Path(item.get("_data_dir") or ".")).get(item["work_order_id"])
    except Exception:  # 工单不存在或存储不可读：内容门照常生效，一致性标记为未核对
        return problems, True

    if str(fields.get("work_order_id") or "").strip() != item["work_order_id"]:
        problems.append({"field": "work_order_id", "code": "WRONG_TARGET",
                         "detail": "fields.work_order_id 与交付物所属工单不一致"})
    objective = (order.get("objective_ref") or {}).get("objective_id")
    if objective and str(fields.get("objective_id") or "").strip() != objective:
        problems.append({"field": "objective_id", "code": "WRONG_TARGET",
                         "detail": f"应为 {objective}，实际 {fields.get('objective_id')}"})
    context_id = (order.get("context_ref") or {}).get("context_id")
    if context_id and not _mentions(fields.get("business_context_ref"), context_id):
        problems.append({"field": "business_context_ref", "code": "WRONG_FACTS",
                         "detail": f"未引用工单绑定的产品事实包 {context_id}"})
    receiver_id = (order.get("receiver") or {}).get("actor_id")
    if receiver_id and not _mentions(fields.get("receiver"), receiver_id):
        problems.append({"field": "receiver", "code": "WRONG_AUDIENCE",
                         "detail": f"接收人应为工单指定的 {receiver_id}"})
    return problems, False


def _content_gate(store: "DeliverableStore", item: Dict[str, Any]) -> bool:
    """在 submit/review 两处执行同一条内容门，返回工单是否可核对。"""
    probe = dict(item)
    probe["_data_dir"] = str(store.data_dir)
    problems, unresolved = content_problems(probe)
    if problems:
        raise ContentUnusableError(problems)
    return not unresolved


def _rubrics(template: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = template.get("rubric") or template.get("qa") or []
    out = []
    for index, item in enumerate(raw, 1):
        if isinstance(item, str):
            out.append({"rubric_id": f"qa-{index:03d}", "label": item, "required": True})
        else:
            row = dict(item)
            row["rubric_id"] = row.get("rubric_id") or row.get("id") or row.get("rule_id") or f"qa-{index:03d}"
            row["required"] = row.get("required", True)
            out.append(row)
    return out


class DeliverableStore:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.events = EventStore(self.data_dir / "deliverables" / "events.jsonl", "deliverable")

    def _versions(self, deliverable_id: str) -> Dict[str, Dict[str, Any]]:
        versions: Dict[str, Dict[str, Any]] = {}
        for event in self.events.events(deliverable_id):
            kind = event["event_type"]
            payload = event["payload"]
            if kind in ("deliverable.created", "deliverable.revised"):
                item = copy.deepcopy(payload["deliverable"])
                versions[item["version"]] = item
            elif kind == "deliverable.submitted":
                item = versions[payload["version"]]
                item["state"] = "submitted"
                item["submitted_by"] = event["by"]
                item["submitted_at"] = event["ts"]
            elif kind == "deliverable.reviewed":
                item = versions[payload["version"]]
                item["state"] = payload["overall"]
                item.setdefault("reviews", []).append({
                    "reviewer_id": event["by"],
                    "reviewed_at": event["ts"],
                    "results": copy.deepcopy(payload["results"]),
                    "overall": payload["overall"],
                })
        return versions

    def list(self) -> List[Dict[str, Any]]:
        ids = []
        for event in self.events.all():
            if event["entity_id"] not in ids:
                ids.append(event["entity_id"])
        return [self.get(deliverable_id) for deliverable_id in ids]

    def get(self, deliverable_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        versions = self._versions(deliverable_id)
        if not versions:
            raise DeliverableError(f"找不到 Deliverable: {deliverable_id}")
        if version is None:
            version = list(versions)[-1]
        if version not in versions:
            raise DeliverableError(f"找不到 Deliverable: {deliverable_id}@{version}")
        return copy.deepcopy(versions[version])

    def create(self, spec: Dict[str, Any], *, by: str) -> Dict[str, Any]:
        deliverable_id = str(spec.get("deliverable_id") or "").strip()
        work_order_id = str(spec.get("work_order_id") or "").strip()
        template_id = str(spec.get("template_id") or "").strip()
        version = str(spec.get("version") or "1")
        if not deliverable_id or not work_order_id:
            raise DeliverableError("deliverable_id/work_order_id 必填")
        template = get_template(template_id)
        if template is None:
            raise DeliverableError(f"未知模板: {template_id}")
        template_version = str(spec.get("template_version") or template["version"])
        if template_version != template["version"]:
            raise DeliverableError(f"未知模板版本: {template_id}@{template_version}")
        try:
            self.get(deliverable_id, version)
            raise DeliverableError(f"Deliverable 版本已存在: {deliverable_id}@{version}")
        except DeliverableError as exc:
            if "已存在" in str(exc):
                raise
        item = {
            "deliverable_id": deliverable_id,
            "version": version,
            "work_order_id": work_order_id,
            "template_id": template_id,
            "template_version": template_version,
            "deliverable_type": str(spec.get("deliverable_type") or ""),
            "fields": copy.deepcopy(spec.get("fields") or {}),
            "created_by": str(by),
            "risk_class": str(spec.get("risk_class") or "R1"),
            "state": "draft",
            "reviews": [],
        }
        if item["risk_class"] not in ("R0", "R1", "R2", "R3", "R4"):
            raise DeliverableError("risk_class 必须是 R0/R1/R2/R3/R4")
        self.events.append("created", deliverable_id, {"deliverable": item}, by=by)
        return self.get(deliverable_id, version)

    def revise(self, deliverable_id: str, fields: Dict[str, Any], *, by: str) -> Dict[str, Any]:
        current = self.get(deliverable_id)
        if current["state"] not in ("draft", "rejected"):
            raise DeliverableError("只有 draft/rejected 可修订；accepted 后应创建新交付版本")
        try:
            next_version = str(int(current["version"]) + 1)
        except ValueError as exc:
            raise DeliverableError("当前版本号不是可递增整数，请显式创建新版本") from exc
        spec = dict(current)
        spec["version"] = next_version
        spec["fields"] = copy.deepcopy(fields)
        spec["parent_version"] = current["version"]
        item = {
            **{k: copy.deepcopy(v) for k, v in spec.items() if k not in ("reviews", "state", "submitted_by", "submitted_at")},
            "state": "draft",
            "created_by": by,
            "reviews": [],
        }
        self.events.append("revised", deliverable_id, {"deliverable": item}, by=by)
        return self.get(deliverable_id, next_version)

    def submit(self, deliverable_id: str, *, version: Optional[str] = None, by: str) -> Dict[str, Any]:
        item = self.get(deliverable_id, version)
        if item["state"] != "draft":
            raise DeliverableError(f"只有 draft 可提交，当前 {item['state']}")
        errors = lint_instance(item["template_id"], item["template_version"], item["fields"])
        if errors:
            raise DeliverableError("交付物模板校验失败：" + "；".join(errors))
        hits, total = placeholder_hits(item["fields"])
        if hits:
            raise PlaceholderContentError(hits, total)
        checked = _content_gate(self, item)
        self.events.append("submitted", deliverable_id, {
            "version": item["version"],
            "content_gate": {"ok": True, "work_order_unresolved": not checked},
        }, by=by)
        return self.get(deliverable_id, item["version"])

    def review(self, deliverable_id: str, *, version: Optional[str] = None,
               results: List[Dict[str, Any]], by: str, actor_type: str = "human") -> Dict[str, Any]:
        item = self.get(deliverable_id, version)
        if item["state"] != "submitted":
            raise DeliverableError(f"只有 submitted 可审核，当前 {item['state']}")
        hits, total = placeholder_hits(item["fields"])
        if hits:
            raise PlaceholderContentError(hits, total)
        checked = _content_gate(self, item)
        try:
            require_actor(actor_id=by, actor_type=actor_type, operation="deliverable.review",
                          author_id=item["created_by"], risk_class=item["risk_class"])
        except ValueError as exc:
            raise DeliverableError(str(exc)) from exc
        template = get_template(item["template_id"])
        required = [r for r in _rubrics(template) if r.get("required", True)]
        if not required:
            raise DeliverableError("模板缺少必需 QA rubric，拒绝无标准验收")
        overall = "accepted"
        for rubric in required:
            result = next((r for r in results if r.get("rubric_id") == rubric["rubric_id"]), None)
            if result is None:
                raise DeliverableError(f"缺 QA 审核项: {rubric['rubric_id']}")
            verdict = result.get("verdict")
            if verdict not in ("pass", "fail", "not_applicable"):
                raise DeliverableError(f"{rubric['rubric_id']} verdict 非法")
            if not str(result.get("evidence_ref") or "").strip():
                raise DeliverableError(f"{rubric['rubric_id']} 缺 evidence_ref")
            if verdict == "not_applicable" and not str(result.get("note") or "").strip():
                raise DeliverableError(f"{rubric['rubric_id']} 为 NA 时必须写理由")
            if verdict == "fail":
                overall = "rejected"
        self.events.append("reviewed", deliverable_id, {
            "version": item["version"], "results": copy.deepcopy(results), "overall": overall,
            "content_gate": {"ok": True, "work_order_unresolved": not checked},
        }, by=by)
        return self.get(deliverable_id, item["version"])
