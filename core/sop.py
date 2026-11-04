#!/usr/bin/env python3
"""Versioned professional SOP registry and evidence-gated run state machine.

SOP steps can request an external action but cannot execute one.  The only
execution bridge is core.work_actions, which materialises the existing plan and
queue contracts for the standard approval pipeline.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .event_store import EventStore
from .work import WorkStore


class SOPError(ValueError):
    pass


def _builtin() -> List[Dict[str, Any]]:
    data = json.loads((Path(__file__).resolve().parent / "sop_data.json").read_text(encoding="utf-8"))
    return data["sops"]


def lint_sop(sop: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for key in ("sop_id", "version", "capability_id"):
        if not str(sop.get(key) or "").strip():
            errors.append(f"sop 缺 {key}")
    steps = sop.get("steps")
    if not isinstance(steps, list) or not 5 <= len(steps) <= 8:
        errors.append("SOP 必须包含 5–8 个主要步骤")
        return errors
    ids = [s.get("step_id") for s in steps]
    if len(ids) != len(set(ids)) or any(not x for x in ids):
        errors.append("SOP step_id 必须非空且唯一")
    required = ("purpose", "actor", "inputs", "method", "outputs", "dod", "on_pass", "on_fail", "on_unknown")
    for index, step in enumerate(steps):
        for key in required:
            if key not in step or step[key] in (None, "", []):
                errors.append(f"steps[{index}] 缺 {key}")
        for tool in step.get("tools") or []:
            if tool.get("kind") not in ("read_only", "draft", "action_request"):
                errors.append(f"steps[{index}] 非法工具类型: {tool.get('kind')}")
        for rule in step.get("dod") or []:
            if not rule.get("rule_id"):
                errors.append(f"steps[{index}] DoD 缺 rule_id")
        for output in step.get("outputs") or []:
            if not output.get("deliverable_type"):
                errors.append(f"steps[{index}] output 缺 deliverable_type")
        if step.get("on_pass") != "close" and step.get("on_pass") not in ids:
            errors.append(f"steps[{index}] on_pass 指向未知步骤或 close: {step.get('on_pass')}")
    return errors


class SOPRegistry:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.events = EventStore(self.data_dir / "sop" / "versions.jsonl", "sop")

    def list(self) -> List[Dict[str, Any]]:
        rows = copy.deepcopy(_builtin())
        rows.extend(copy.deepcopy(e["payload"]["sop"]) for e in self.events.all()
                    if e["event_type"] == "sop.registered")
        return rows

    def get(self, sop_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        rows = [s for s in self.list() if s["sop_id"] == sop_id]
        if version is not None:
            rows = [s for s in rows if s["version"] == version]
        if not rows:
            raise SOPError(f"找不到 SOP: {sop_id}@{version or 'latest'}")
        return rows[-1]

    def register(self, sop: Dict[str, Any], *, by: str) -> Dict[str, Any]:
        errors = lint_sop(sop)
        if errors:
            raise SOPError("；".join(errors))
        key = (sop["sop_id"], sop["version"])
        if any((s["sop_id"], s["version"]) == key for s in self.list()):
            raise SOPError(f"SOP 版本已存在且不可覆盖: {key[0]}@{key[1]}")
        self.events.append("registered", sop["sop_id"], {"sop": copy.deepcopy(sop)}, by=by)
        return copy.deepcopy(sop)


class SOPRunStore:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.events = EventStore(self.data_dir / "work" / "sop-runs.jsonl", "sop_run")

    def list(self) -> List[Dict[str, Any]]:
        ids = []
        for event in self.events.all():
            if event["entity_id"] not in ids:
                ids.append(event["entity_id"])
        return [self.get(run_id) for run_id in ids]

    def get(self, run_id: str) -> Dict[str, Any]:
        current = None
        for event in self.events.events(run_id):
            kind = event["event_type"]
            if kind == "sop_run.started":
                current = copy.deepcopy(event["payload"]["run"])
            elif kind == "sop_run.step_completed":
                current["steps"][event["payload"]["step_id"]].update({
                    "state": "done",
                    "outputs": copy.deepcopy(event["payload"]["outputs"]),
                    "dod_results": copy.deepcopy(event["payload"]["dod_results"]),
                    "approval": copy.deepcopy(event["payload"].get("approval")),
                    "completed_by": event["by"],
                    "completed_at": event["ts"],
                })
                current["state"] = event["payload"]["run_state"]
                current["current_step_id"] = event["payload"].get("next_step_id")
                next_id = event["payload"].get("next_step_id")
                if next_id:
                    current["steps"][next_id]["state"] = "ready"
            elif kind == "sop_run.step_blocked":
                current["steps"][event["payload"]["step_id"]]["state"] = "blocked"
                current["state"] = "blocked"
                current["blocked_reason"] = event["payload"]["reason"]
            elif kind == "sop_run.step_reopened":
                current["steps"][event["payload"]["step_id"]]["state"] = "ready"
                current["state"] = "running"
                current["current_step_id"] = event["payload"]["step_id"]
                current["blocked_reason"] = ""
        if current is None:
            raise SOPError(f"找不到 SOP run: {run_id}")
        return current

    def start(self, work_order_id: str, *, by: str) -> Dict[str, Any]:
        work = WorkStore(self.data_dir)
        order = work.get(work_order_id)
        if order["state"] not in ("ready", "running"):
            raise SOPError(f"WorkOrder 当前 {order['state']}，必须先 ready")
        if any(r["work_order_id"] == work_order_id and r["state"] in ("running", "blocked") for r in self.list()):
            raise SOPError(f"WorkOrder 已有未结束 SOP run: {work_order_id}")
        ref = order["sop_version_ref"]
        sop = SOPRegistry(self.data_dir).get(ref["sop_id"], ref["version"])
        run_id = f"run-{work_order_id}-{len(self.list()) + 1:04d}"
        steps = {}
        for index, step in enumerate(sop["steps"]):
            steps[step["step_id"]] = {
                "state": "ready" if index == 0 else "pending",
                "outputs": [],
                "dod_results": [],
                "approval": None,
            }
        run = {
            "run_id": run_id,
            "work_order_id": work_order_id,
            "sop_id": sop["sop_id"],
            "sop_version": sop["version"],
            "state": "running",
            "current_step_id": sop["steps"][0]["step_id"],
            "steps": steps,
        }
        self.events.append("started", run_id, {"run": run}, by=by)
        if order["state"] == "ready":
            work.transition(work_order_id, "running", by=by, reason=f"启动 {run_id}")
        return self.get(run_id)

    @staticmethod
    def _validate_completion(step: Dict[str, Any], outputs: List[Dict[str, Any]],
                             dod_results: List[Dict[str, Any]], approval: Optional[Dict[str, Any]]) -> None:
        required_outputs = [o for o in step["outputs"] if o.get("required", True)]
        for expected in required_outputs:
            matches = [o for o in outputs if o.get("deliverable_type") == expected["deliverable_type"]]
            if not matches:
                raise SOPError(f"缺步骤产物: {expected['deliverable_type']}")
            for item in matches:
                for key in ("artifact_id", "version", "evidence_ref"):
                    if not str(item.get(key) or "").strip():
                        raise SOPError(f"步骤产物 {expected['deliverable_type']} 缺 {key}")
        for rule in step["dod"]:
            if not rule.get("required", True):
                continue
            result = next((r for r in dod_results if r.get("rule_id") == rule["rule_id"]), None)
            if result is None or result.get("status") != "pass":
                raise SOPError(f"必需 DoD 未 pass: {rule['rule_id']}")
            if not str(result.get("evidence_ref") or "").strip():
                raise SOPError(f"DoD {rule['rule_id']} 缺 evidence_ref")
        if (step.get("approval") or {}).get("required"):
            if not approval or not str(approval.get("by") or "").strip() or not str(approval.get("scope") or "").strip():
                raise SOPError("该步骤需要具名 approval(by,scope)")

    def complete_step(self, run_id: str, step_id: str, *, outputs: List[Dict[str, Any]],
                      dod_results: List[Dict[str, Any]], by: str,
                      approval: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        run = self.get(run_id)
        if run["state"] != "running" or run["current_step_id"] != step_id:
            raise SOPError(f"步骤 {step_id} 当前不可完成；run={run['state']} current={run['current_step_id']}")
        if run["steps"][step_id]["state"] != "ready":
            raise SOPError(f"步骤 {step_id} 不是 ready")
        sop = SOPRegistry(self.data_dir).get(run["sop_id"], run["sop_version"])
        step = next(s for s in sop["steps"] if s["step_id"] == step_id)
        self._validate_completion(step, outputs, dod_results, approval)
        next_id = step["on_pass"]
        run_state = "done" if next_id == "close" else "running"
        if next_id != "close" and next_id not in run["steps"]:
            raise SOPError(f"下一分支不是自动推进步骤: {next_id}；需人工分支处理")
        self.events.append("step_completed", run_id, {
            "step_id": step_id,
            "outputs": copy.deepcopy(outputs),
            "dod_results": copy.deepcopy(dod_results),
            "approval": copy.deepcopy(approval),
            "next_step_id": None if next_id == "close" else next_id,
            "run_state": run_state,
        }, by=by)
        return self.get(run_id)

    def block_step(self, run_id: str, step_id: str, *, reason: str, by: str) -> Dict[str, Any]:
        if not reason.strip():
            raise SOPError("阻塞必须写原因")
        run = self.get(run_id)
        if run["current_step_id"] != step_id:
            raise SOPError("只能阻塞当前步骤")
        self.events.append("step_blocked", run_id, {"step_id": step_id, "reason": reason}, by=by)
        return self.get(run_id)

    def reopen_step(self, run_id: str, step_id: str, *, by: str, reason: str) -> Dict[str, Any]:
        if not reason.strip():
            raise SOPError("重开必须写人工处理依据")
        run = self.get(run_id)
        if run["state"] != "blocked" or run["current_step_id"] != step_id:
            raise SOPError("只有当前 blocked 步骤可重开")
        self.events.append("step_reopened", run_id, {"step_id": step_id, "reason": reason}, by=by)
        return self.get(run_id)
