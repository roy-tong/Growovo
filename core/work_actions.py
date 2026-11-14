#!/usr/bin/env python3
"""Bridge accepted business work to the existing approved execution pipeline.

This module only materialises plan + queue.  It never sends.  Human approval and
all dry-run/go semantics remain in mkt plan/queue and core.action_runner.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .deliverables import DeliverableStore
from .event_store import EventStore
from .execute import build as build_execution
from .planlint import lint_plan
from .sop import SOPRegistry, SOPRunStore
from .work import WorkStore


class WorkActionError(ValueError):
    pass


def _sha(value: Any) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def materialize(data_dir: Path | str, *, work_order_id: str, step_id: str,
                deliverable_id: str, deliverable_version: Optional[str],
                strategy: Dict[str, Any], targets: List[Dict[str, str]], campaign: str,
                account: str = "", workspace: str = "", by: str,
                allow_unbound: bool = False, binding_note: str = "") -> Dict[str, Any]:
    data_dir = Path(data_dir)
    order = WorkStore(data_dir).get(work_order_id)
    if order["state"] not in ("ready", "running"):
        raise WorkActionError(f"WorkOrder 当前 {order['state']}，不能生成执行请求")
    runs = [r for r in SOPRunStore(data_dir).list() if r["work_order_id"] == work_order_id]
    if not runs:
        raise WorkActionError("先启动 WorkOrder 对应的 SOP run")
    run = runs[-1]
    if run["state"] != "running" or run["current_step_id"] != step_id:
        raise WorkActionError(f"SOP 当前步骤不是 {step_id}")
    sop = SOPRegistry(data_dir).get(run["sop_id"], run["sop_version"])
    step = next((s for s in sop["steps"] if s["step_id"] == step_id), None)
    if step is None or not any(t.get("kind") == "action_request" for t in step.get("tools") or []):
        raise WorkActionError("该 SOP 步骤未声明 action_request，不能生成外部执行计划")
    deliverable = DeliverableStore(data_dir).get(deliverable_id, deliverable_version)
    if deliverable["state"] != "accepted":
        raise WorkActionError("执行载荷必须来自 accepted Deliverable")
    if deliverable["work_order_id"] != work_order_id:
        raise WorkActionError("Deliverable 与 WorkOrder 不匹配")
    payload = deliverable["fields"].get("execution_payload")
    payload_sha = _sha({"strategy": strategy, "targets": targets})
    if payload is None:
        if not allow_unbound:
            raise WorkActionError(
                "Deliverable 未声明 execution_payload；执行载荷无法追溯到已验收内容。"
                "请在交付物中加入 execution_payload，或显式传 allow_unbound 并记录原因")
        binding_mode = "unbound_explicit"
    else:
        expected = _sha({"strategy": payload.get("strategy"),
                         "targets": payload.get("targets")})
        if expected != payload_sha:
            raise WorkActionError("执行载荷与 accepted Deliverable 的 execution_payload 不一致")
        binding_mode = "deliverable"
    if (data_dir / "plans" / f"{campaign}.json").exists() or (data_dir / "queues" / f"{campaign}.json").exists():
        raise WorkActionError("campaign 已有 plan/queue，拒绝覆盖既有审批对象")
    request = {
        "action_request_id": f"action-{work_order_id}-{step_id}-{len(runs):02d}",
        "work_order_id": work_order_id,
        "run_id": run["run_id"],
        "step_id": step_id,
        "sop_version_ref": {"sop_id": run["sop_id"], "version": run["sop_version"]},
        "deliverable_ref": {
            "deliverable_id": deliverable_id,
            "version": deliverable["version"],
            "content_sha256": _sha(deliverable["fields"]),
        },
        "payload_binding": {
            "mode": binding_mode,
            "payload_sha256": payload_sha,
            "note": binding_note,
        },
        "campaign": campaign,
        "account": account or "default",
        "workspace": workspace or str(data_dir.resolve()),
        "status": "draft",
    }
    request["request_sha256"] = _sha(request)
    try:
        result = build_execution(
            strategy, targets, campaign=campaign, data_dir=data_dir,
            account=account or None, workspace=workspace or None,
        )
    except Exception as exc:
        raise WorkActionError(f"既有执行桥拒绝生成: {exc}") from exc
    plan_path = Path(result["plan"])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["provenance"] = copy.deepcopy(request)
    errors = lint_plan(plan)
    if errors:
        raise WorkActionError("业务溯源计划未过既有 plan lint：" + "；".join(errors))
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    request["status"] = "materialized"
    request["plan_path"] = str(plan_path)
    request["queue_path"] = result["queue"]
    events = EventStore(data_dir / "work" / "action-requests.jsonl", "work_action")
    events.append("materialized", request["action_request_id"], {"request": request}, by=by)
    return {
        "request": request,
        "plan": result["plan"],
        "queue": result["queue"],
        "next": [
            f"mkt review {campaign}",
            f"mkt plan approve --id {campaign} --by <human>",
            f"mkt queue run {result['queue']} --go  # 仅人工授权后",
        ],
        "boundary": "只生成计划与队列，未发送；执行审批指纹覆盖 plan.provenance 与实际 queue/account/workspace。",
    }
