#!/usr/bin/env python3
"""Template-bound deliverables and independent acceptance reviews."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

from .event_store import EventStore
from .permissions import require_actor
from .templates import get_template, lint_instance


class DeliverableError(ValueError):
    pass


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
        self.events.append("submitted", deliverable_id, {"version": item["version"]}, by=by)
        return self.get(deliverable_id, item["version"])

    def review(self, deliverable_id: str, *, version: Optional[str] = None,
               results: List[Dict[str, Any]], by: str, actor_type: str = "human") -> Dict[str, Any]:
        item = self.get(deliverable_id, version)
        if item["state"] != "submitted":
            raise DeliverableError(f"只有 submitted 可审核，当前 {item['state']}")
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
        }, by=by)
        return self.get(deliverable_id, item["version"])
