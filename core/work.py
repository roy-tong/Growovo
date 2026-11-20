#!/usr/bin/env python3
"""BusinessContext, Objective and WorkOrder contracts for the GTM work OS.

The module owns business state only.  It never sends to a platform.  External
writes are separate ActionRequests materialised through the existing approval
and queue pipeline.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .event_store import EventStore


class WorkError(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ref_ok(ref: Any, *keys: str) -> bool:
    return isinstance(ref, dict) and all(_text(ref.get(key)) for key in keys)


WORK_ORDER_FIELDS = {
    "schema_version", "kind", "work_order_id", "workstream_id", "objective_ref",
    "title", "trigger", "business_purpose", "context_ref", "subject_refs", "scope",
    "capability_id", "role_family", "collaborating_role_families", "input_bindings",
    "sop_version_ref", "dependencies", "depends_on", "deliverable_specs", "owner",
    "receiver", "schedule", "budget", "risk_class", "permission_ref",
    "result_metric_refs", "process_metric_refs", "guardrail_metric_refs",
    "stop_rollback", "observation_plan", "due",
}


def _due_value(order: Dict[str, Any]) -> str:
    return _text(order.get("due") or (order.get("schedule") or {}).get("due"))


def lint_context(snapshot: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for key in ("context_id", "version", "source", "approved_by", "content_sha256"):
        if not _text(snapshot.get(key)):
            errors.append(f"context 缺必填字段: {key}")
    source = snapshot.get("source") or {}
    if source.get("kind") not in ("askovo", "user_declared"):
        errors.append("context.source.kind 必须是 askovo 或 user_declared")
    if not _text(source.get("ref")):
        errors.append("context.source.ref 必填")
    elif source.get("kind") == "askovo":
        # askovo 快照必须指向真实文件，且哈希与声明一致；否则下游会把
        # 一个不存在/已被改写的上游当成“不可变上下文”。
        ref = str(source.get("ref") or "").strip()
        path = Path(ref)
        if not path.is_file():
            errors.append(f"context.source.ref 文件不存在或不可读: {ref}")
        else:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            declared = _text(snapshot.get("content_sha256")).lower()
            if actual != declared:
                errors.append(
                    "context.content_sha256 与 source.ref 不一致: "
                    f"declared={declared[:12]}… actual={actual[:12]}…")
    for key in ("value_event_definitions", "non_goals"):
        if not isinstance(snapshot.get(key), list) or not snapshot.get(key):
            errors.append(f"context.{key} 必须是非空数组")
    claims = snapshot.get("claims")
    if not isinstance(claims, list) or not claims:
        errors.append("context.claims 必须是非空数组：每条结论写 claim_id、statement、status、evidence_refs")
        claims = []
    statuses = ("evidence_supported", "hypothesis", "insufficient_evidence")
    seen_claims = set()
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            errors.append(f"context.claims[{index}] 必须是对象")
            continue
        claim_id = _text(claim.get("claim_id"))
        label = claim_id or f"claims[{index}]"
        if not claim_id:
            errors.append(f"context.claims[{index}] 缺 claim_id")
        elif claim_id in seen_claims:
            errors.append(f"context.claims claim_id 重复: {claim_id}")
        else:
            seen_claims.add(claim_id)
        if len(_text(claim.get("statement"))) < 8:
            errors.append(f"context.{label}.statement 至少 8 个字，写清结论本身")
        status = claim.get("status")
        if status not in statuses:
            errors.append(f"context.{label}.status 必须是 " + "/".join(statuses))
        refs = claim.get("evidence_refs")
        if not isinstance(refs, list):
            errors.append(f"context.{label}.evidence_refs 必须是数组")
            refs = []
        if status == "hypothesis":
            if claim.get("assumption") is not True:
                errors.append(f"context.{label} 是 hypothesis，必须显式写 assumption: true")
        elif status in ("evidence_supported", "insufficient_evidence") and not refs:
            errors.append(f"context.{label} 状态 {status} 至少要一条 evidence_refs")
        for ref_index, ref in enumerate(refs):
            if (not isinstance(ref, dict) or not _text(ref.get("evidence_id"))
                    or not _text(ref.get("source"))):
                errors.append(f"context.{label}.evidence_refs[{ref_index}] 需要 evidence_id 和 source")
    return errors


class ContextStore:
    def __init__(self, data_dir: Path | str):
        self.events = EventStore(Path(data_dir) / "context" / "snapshots.jsonl", "context")

    def import_snapshot(self, snapshot: Dict[str, Any], *, by: str) -> Dict[str, Any]:
        errors = lint_context(snapshot)
        if errors:
            raise WorkError("；".join(errors))
        key = (snapshot["context_id"], snapshot["version"])
        if any((r["context_id"], r["version"]) == key for r in self.list()):
            raise WorkError(f"上下文版本已存在且不可覆盖: {key[0]}@{key[1]}")
        self.events.append("imported", snapshot["context_id"], {"snapshot": copy.deepcopy(snapshot)}, by=by)
        return copy.deepcopy(snapshot)

    def list(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for event in self.events.all():
            if event["event_type"] == "context.imported":
                out.append(copy.deepcopy(event["payload"]["snapshot"]))
        return out

    def get(self, context_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        rows = [r for r in self.list() if r["context_id"] == context_id]
        if version is not None:
            rows = [r for r in rows if r["version"] == version]
        if not rows:
            raise WorkError(f"找不到上下文: {context_id}@{version or 'latest'}")
        return rows[-1]


def lint_objective(objective: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for key in ("objective_id", "version", "business_question", "subject_type", "window", "owner"):
        if not _text(objective.get(key)):
            errors.append(f"objective 缺必填字段: {key}")
    if not _ref_ok(objective.get("context_ref"), "context_id", "version"):
        errors.append("objective.context_ref 必须固定 context_id/version")
    if not _ref_ok(objective.get("primary_metric_ref"), "metric_id", "version"):
        errors.append("objective 必须有一个带版本的主结果指标 primary_metric_ref")
    return errors


class ObjectiveStore:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.events = EventStore(self.data_dir / "work" / "objectives.jsonl", "objective")

    def create(self, objective: Dict[str, Any], *, by: str) -> Dict[str, Any]:
        errors = lint_objective(objective)
        if errors:
            raise WorkError("；".join(errors))
        ref = objective["context_ref"]
        ContextStore(self.data_dir).get(ref["context_id"], ref["version"])
        metric = objective["primary_metric_ref"]
        from .metrics import MetricRegistry
        MetricRegistry(self.data_dir).get(metric["metric_id"], metric["version"])
        key = (objective["objective_id"], objective["version"])
        if any((r["objective_id"], r["version"]) == key for r in self.list()):
            raise WorkError(f"目标版本已存在: {key[0]}@{key[1]}")
        self.events.append("created", objective["objective_id"], {"objective": copy.deepcopy(objective)}, by=by)
        return copy.deepcopy(objective)

    def list(self) -> List[Dict[str, Any]]:
        return [copy.deepcopy(e["payload"]["objective"]) for e in self.events.all()
                if e["event_type"] == "objective.created"]

    def get(self, objective_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        rows = [r for r in self.list() if r["objective_id"] == objective_id]
        if version is not None:
            rows = [r for r in rows if r["version"] == version]
        if not rows:
            raise WorkError(f"找不到目标: {objective_id}@{version or 'latest'}")
        return rows[-1]


def sop_capability_errors(order: Dict[str, Any], sop: Dict[str, Any]) -> List[str]:
    """工单的 SOP 必须属于同一能力域，否则步骤产物与审阅岗位对不上。"""
    capability_id = _text(order.get("capability_id"))
    sop_capability = _text(sop.get("capability_id"))
    if capability_id == sop_capability:
        return []
    return [f"capability_id 是 {capability_id or '空'}，SOP {sop.get('sop_id')} 属于 "
            f"{sop_capability or '未知'}：换成 {capability_id or '该能力域'} 的 SOP，"
            f"或把 capability_id 改成 {sop_capability or 'SOP 对应能力域'}"]


def lint_sop_binding(order: Dict[str, Any], sop_registry: Any) -> List[str]:
    """在已加载的 SOP 注册表里核对工单绑定的 SOP 版本与能力域。"""
    ref = order.get("sop_version_ref") or {}
    if not _ref_ok(ref, "sop_id", "version"):
        return []
    sop_id, version = _text(ref["sop_id"]), _text(ref["version"])
    matches = [s for s in sop_registry.list()
               if s.get("sop_id") == sop_id and s.get("version") == version]
    if not matches:
        return [f"找不到 SOP {sop_id}@{version}：先 mkt sop list 确认可用版本"]
    return sop_capability_errors(order, matches[-1])


def lint_work_order(order: Dict[str, Any], *, sop_registry: Any = None) -> List[str]:
    errors: List[str] = []
    for key in ("work_order_id", "title", "business_purpose", "capability_id", "role_family"):
        if not _text(order.get(key)):
            errors.append(f"work_order 缺必填字段: {key}")
    if not _ref_ok(order.get("objective_ref"), "objective_id", "version"):
        errors.append("objective_ref 必须固定 objective_id/version")
    if not _ref_ok(order.get("context_ref"), "context_id", "version"):
        errors.append("context_ref 必须固定 context_id/version")
    if not _ref_ok(order.get("sop_version_ref"), "sop_id", "version"):
        errors.append("sop_version_ref 必须固定 sop_id/version")
    if not _due_value(order):
        errors.append("缺 due：在 schedule.due 写截止时间，或显式写 not_applicable")
    depends_on = order.get("depends_on")
    if depends_on != "not_applicable":
        if not isinstance(depends_on, list) or not depends_on:
            errors.append("depends_on 必须是 work_order_id 数组，或显式写 not_applicable")
        else:
            for index, dep in enumerate(depends_on):
                dep_id = dep if isinstance(dep, str) else (dep.get("work_order_id") if isinstance(dep, dict) else "")
                if not _text(dep_id):
                    errors.append(f"depends_on[{index}] 必须是 work_order_id 字符串或带 work_order_id 的对象")
    owner = order.get("owner") or {}
    receiver = order.get("receiver") or {}
    if not _text(owner.get("actor_id")):
        errors.append("必须有一个主责 owner.actor_id")
    if not _text(receiver.get("actor_id")):
        errors.append("必须有明确 receiver.actor_id")
    bindings = order.get("input_bindings")
    if not isinstance(bindings, list) or not bindings:
        errors.append("input_bindings 必须是非空数组")
        bindings = []
    for index, binding in enumerate(bindings):
        for key in ("name", "artifact_id", "version", "acceptance_status"):
            if not _text(binding.get(key)):
                errors.append(f"input_bindings[{index}] 缺 {key}")
        if binding.get("required", True) and binding.get("acceptance_status") != "accepted":
            errors.append(f"input_bindings[{index}] 未验收，不能 ready")
    specs = order.get("deliverable_specs")
    if not isinstance(specs, list) or not specs:
        errors.append("deliverable_specs 至少一个")
        specs = []
    for index, spec in enumerate(specs):
        for key in ("deliverable_type", "template_id", "template_version", "reviewer_role"):
            if not _text(spec.get(key)):
                errors.append(f"deliverable_specs[{index}] 缺 {key}")
    metrics = order.get("result_metric_refs")
    if not isinstance(metrics, list) or not metrics:
        errors.append("至少一个 result_metric_refs")
    else:
        for ref in metrics:
            if not _ref_ok(ref, "metric_id", "version"):
                errors.append("result_metric_refs 必须固定 metric_id/version")
    if order.get("risk_class") not in ("R0", "R1", "R2", "R3", "R4"):
        errors.append("risk_class 必须是 R0/R1/R2/R3/R4")
    try:
        from .capabilities import get_capability, get_role_family
        capability = get_capability(_text(order.get("capability_id")))
        role = get_role_family(_text(order.get("role_family")))
        if capability is None or role is None:
            raise ValueError("未知 capability_id 或 role_family")
        if (role.get("capability_id") or role.get("domain")) != capability["capability_id"]:
            errors.append("role_family 不属于指定 capability_id")
    except (ImportError, KeyError, ValueError) as exc:
        errors.append(f"能力 / 岗位引用无效: {exc}")
    if sop_registry is not None:
        errors.extend(lint_sop_binding(order, sop_registry))
    return errors


class WorkStore:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.events = EventStore(self.data_dir / "work" / "orders.jsonl", "work_order")

    def _project(self, work_order_id: str) -> Dict[str, Any]:
        current: Optional[Dict[str, Any]] = None
        for event in self.events.events(work_order_id):
            kind = event["event_type"]
            if kind in ("work_order.created", "work_order.updated"):
                current = copy.deepcopy(event["payload"]["work_order"])
            elif kind == "work_order.state_changed":
                if current is None:
                    raise WorkError(f"状态事件缺少工作快照: {work_order_id}")
                current["state"] = event["payload"]["state"]
                current["state_reason"] = event["payload"].get("reason", "")
                current["updated_at"] = event["ts"]
                current["updated_by"] = event["by"]
        if current is None:
            raise WorkError(f"找不到 WorkOrder: {work_order_id}")
        return current

    def create(self, order: Dict[str, Any], *, by: str) -> Dict[str, Any]:
        work_order_id = _text(order.get("work_order_id"))
        if not work_order_id:
            raise WorkError("work_order_id 不能为空")
        if any(r["work_order_id"] == work_order_id for r in self.list()):
            raise WorkError(f"WorkOrder 已存在: {work_order_id}")
        snapshot = copy.deepcopy(order)
        snapshot["state"] = "draft"
        snapshot["revision"] = 1
        self.events.append("created", work_order_id, {"work_order": snapshot}, by=by)
        return self.get(work_order_id)

    def replace(self, work_order_id: str, order: Dict[str, Any], *, by: str) -> Dict[str, Any]:
        current = self.get(work_order_id)
        if current["state"] not in ("draft", "blocked"):
            raise WorkError("只有 draft/blocked WorkOrder 可替换；运行中应创建新修订流程")
        snapshot = copy.deepcopy(order)
        snapshot["work_order_id"] = work_order_id
        snapshot["state"] = current["state"]
        snapshot["revision"] = int(current.get("revision", 1)) + 1
        self.events.append("updated", work_order_id, {"work_order": snapshot}, by=by)
        return self.get(work_order_id)

    def update(self, work_order_id: str, patch: Dict[str, Any], *, by: str) -> Dict[str, Any]:
        current = self.get(work_order_id)
        if current["state"] not in ("draft", "blocked"):
            raise WorkError("只有 draft/blocked WorkOrder 可局部更新；运行中应创建新修订流程")
        if not isinstance(patch, dict) or not patch:
            raise WorkError("update 需要非空对象：只写要改的字段")
        reserved = sorted({"state", "revision"} & set(patch))
        if reserved:
            raise WorkError("以下字段由状态迁移维护，不能在 update 里改: " + "、".join(reserved))
        allowed = set(current) | WORK_ORDER_FIELDS
        unknown = sorted(set(patch) - allowed)
        if unknown:
            raise WorkError("未知字段: " + "、".join(unknown)
                            + "；允许字段: " + "、".join(sorted(allowed)))
        snapshot = copy.deepcopy(current)
        merged = copy.deepcopy(patch)
        for key, value in merged.items():
            if isinstance(value, dict) and isinstance(snapshot.get(key), dict):
                snapshot[key] = {**snapshot[key], **value}
            else:
                snapshot[key] = value
        snapshot["work_order_id"] = work_order_id
        snapshot["revision"] = int(current.get("revision", 1)) + 1
        self.events.append("updated", work_order_id, {"work_order": snapshot}, by=by)
        return self.get(work_order_id)

    def list(self) -> List[Dict[str, Any]]:
        ids: List[str] = []
        for event in self.events.all():
            entity = event["entity_id"]
            if entity not in ids:
                ids.append(entity)
        out = []
        for entity in ids:
            try:
                out.append(self._project(entity))
            except WorkError:
                continue
        return out

    def get(self, work_order_id: str) -> Dict[str, Any]:
        return self._project(work_order_id)

    def lint(self, work_order_id: str) -> List[str]:
        from .sop import SOPRegistry
        return lint_work_order(self.get(work_order_id), sop_registry=SOPRegistry(self.data_dir))

    def _set_state(self, work_order_id: str, state: str, *, by: str, reason: str = "") -> Dict[str, Any]:
        self.events.append("state_changed", work_order_id,
                           {"state": state, "reason": reason}, by=by)
        return self.get(work_order_id)

    def block(self, work_order_id: str, *, by: str, reason: str) -> Dict[str, Any]:
        if not _text(reason):
            raise WorkError("blocked 必须写原因")
        return self._set_state(work_order_id, "blocked", by=by, reason=reason)

    def ready(self, work_order_id: str, *, by: str) -> Dict[str, Any]:
        order = self.get(work_order_id)
        if order["state"] not in ("draft", "blocked"):
            raise WorkError(f"当前状态 {order['state']} 不能 ready")
        errors = lint_work_order(order)
        cref = order.get("context_ref", {})
        oref = order.get("objective_ref", {})
        try:
            ContextStore(self.data_dir).get(cref.get("context_id", ""), cref.get("version"))
            ObjectiveStore(self.data_dir).get(oref.get("objective_id", ""), oref.get("version"))
            sref = order.get("sop_version_ref", {})
            from .sop import SOPRegistry
            sop = SOPRegistry(self.data_dir).get(sref.get("sop_id", ""), sref.get("version"))
            errors.extend(sop_capability_errors(order, sop))
        except (WorkError, KeyError, ValueError) as exc:
            errors.append(str(exc))
        for dependency in list(order.get("dependencies") or []) + (
                order.get("depends_on") if isinstance(order.get("depends_on"), list) else []):
            dep_id = dependency if isinstance(dependency, str) else dependency.get("work_order_id", "")
            try:
                dep = self.get(dep_id)
                if dep["state"] not in ("accepted", "closed"):
                    errors.append(f"依赖 {dep_id} 当前 {dep['state']}，未验收")
            except WorkError as exc:
                errors.append(str(exc))
        if errors:
            self._set_state(work_order_id, "blocked", by=by, reason="；".join(errors))
            raise WorkError("WorkOrder 不能 ready：" + "；".join(errors))
        return self._set_state(work_order_id, "ready", by=by)

    def transition(self, work_order_id: str, target: str, *, by: str, reason: str = "") -> Dict[str, Any]:
        current = self.get(work_order_id)
        allowed = {
            "ready": {"running"},
            "running": {"review", "blocked"},
            "review": {"running", "accepted"},
            "accepted": {"closed"},
            "blocked": {"draft"},
        }
        if target not in allowed.get(current["state"], set()):
            raise WorkError(f"非法状态迁移: {current['state']} → {target}")
        if target == "accepted":
            from .deliverables import DeliverableStore
            store = DeliverableStore(self.data_dir)
            for spec in current.get("deliverable_specs") or []:
                if not spec.get("required", True):
                    continue
                deliverable_id = spec.get("deliverable_id")
                if not deliverable_id:
                    raise WorkError("验收前 deliverable_specs 必须绑定 deliverable_id")
                item = store.get(deliverable_id)
                if item["state"] != "accepted":
                    raise WorkError(f"必需交付物 {deliverable_id} 未 accepted")
        return self._set_state(work_order_id, target, by=by, reason=reason)

    def next_actions(self) -> List[Dict[str, Any]]:
        attention = {"blocked": 0, "review": 1, "ready": 2, "running": 3}
        rows = [r for r in self.list() if r["state"] in attention]
        rows.sort(key=lambda r: (attention[r["state"]],
                                 "9999" if _due_value(r) in ("", "not_applicable") else _due_value(r),
                                 r["work_order_id"]))
        return rows
