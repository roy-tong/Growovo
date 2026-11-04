#!/usr/bin/env python3
"""Zero-network, zero-account GTM Work OS demonstration.

The demo materialises and approves a plan but deliberately never executes the
queue.  Business KPI observations come from independent fixture events.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict

from .approval import approve_plan
from .deliverables import DeliverableStore, _rubrics
from .handoffs import HandoffStore
from .metrics import MetricRegistry, measure
from .sop import SOPRegistry, SOPRunStore
from .templates import get_template
from .work import ContextStore, ObjectiveStore, WorkStore
from .work_actions import materialize


def _fields(template_id: str, deliverable_id: str, work_order_id: str) -> Dict:
    template = get_template(template_id)
    fields = {slot: f"demo:{slot}" for slot in template["required_slots"]}
    fields["deliverable_id"] = deliverable_id
    fields["work_order_id"] = work_order_id
    fields["objective_id"] = "obj-first-success-001"
    fields["evidence_refs"] = [{"source_id": "demo-source", "locator": "demo://evidence/1"}]
    return fields


def _review(template_id: str):
    return [{"rubric_id": row["rubric_id"], "verdict": "pass",
             "evidence_ref": f"demo:review:{row['rubric_id']}", "note": "demo"}
            for row in _rubrics(get_template(template_id))]


def run_work_demo(base: Path | str) -> int:
    base = Path(base)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S-%f")
    data = base / "data" / "demo" / "work-os" / stamp
    data.mkdir(parents=True, exist_ok=True)
    print(f"mkt work-demo —— 零网络零账号，沙盒 {data}\n")

    context = {
        "context_id": "ctx-example-product", "version": "2026-11-04.1",
        "source": {"kind": "user_declared", "ref": "demo:user-input"},
        "product_facts": [{"fact": "demo API"}],
        "value_event_definitions": [{"event": "first_successful_api_call"}],
        "non_goals": ["不自动发布", "不伪造业务结果"],
        "approved_by": "demo-human", "content_sha256": "demo-context-sha",
    }
    ContextStore(data).import_snapshot(context, by="demo-human")
    ObjectiveStore(data).create({
        "objective_id": "obj-first-success-001", "version": "1",
        "context_ref": {"context_id": context["context_id"], "version": context["version"]},
        "business_question": "开发者能否独立完成首次成功调用",
        "subject_type": "developer",
        "primary_metric_ref": {"metric_id": "developer_activation_rate", "version": "1.0.0"},
        "window": "2026-11", "owner": "demo-product-owner",
    }, by="demo-human")
    print("[1/8] 上下文与目标   PASS — 来源、版本、价值事件和主 KPI 已固定")

    order = json.loads((Path(__file__).resolve().parent / "work_order_demo.json").read_text(encoding="utf-8"))
    order["deliverable_specs"][0]["deliverable_id"] = "demo-tutorial"
    order["deliverable_specs"][1]["deliverable_id"] = "demo-distribution"
    work = WorkStore(data)
    work.create(order, by="demo-human")
    work.ready(order["work_order_id"], by="demo-human")
    runs = SOPRunStore(data)
    run = runs.start(order["work_order_id"], by="demo-human")
    print(f"[2/8] WorkOrder/SOP   PASS — ready 后启动 {run['run_id']}，绑定 SOP {run['sop_version']}")

    deliverables = DeliverableStore(data)
    deliverables.create({
        "deliverable_id": "demo-tutorial", "version": "1",
        "work_order_id": order["work_order_id"], "template_id": "T30", "template_version": "1.0.0",
        "deliverable_type": "developer_tutorial", "fields": _fields("T30", "demo-tutorial", order["work_order_id"]),
        "risk_class": "R3"}, by="demo-author")
    deliverables.submit("demo-tutorial", by="demo-author")
    deliverables.review("demo-tutorial", results=_review("T30"), by="demo-technical-reviewer")
    handoffs = HandoffStore(data)
    handoffs.create({
        "handoff_id": "demo-handoff", "work_order_id": order["work_order_id"],
        "from_role": "46", "to_role": "24", "receiver_id": "person-product-growth-owner",
        "sla_due": "2026-12-31T00:00:00+08:00",
        "artifact_refs": [{"id": "demo-tutorial", "version": "1"}],
        "acceptance_rules": ["产品增长接收人确认教程与激活事件一致"]}, by="demo-author")
    handoffs.accept("demo-handoff", by="person-product-growth-owner")
    print("[3/8] 交付物与交接   PASS — submitted≠accepted；技术审阅后由指定接收人签收")

    sop = SOPRegistry(data).get(run["sop_id"], run["sop_version"])
    for index, step in enumerate(sop["steps"][:3]):
        outputs = [{"deliverable_type": o["deliverable_type"],
                    "artifact_id": "demo-tutorial" if index < 2 else "demo-distribution-draft",
                    "version": "1", "evidence_ref": "demo:step-output"} for o in step["outputs"]]
        dod = [{"rule_id": d["rule_id"], "status": "pass", "evidence_ref": "demo:dod"} for d in step["dod"]]
        approval = {"by": "demo-technical-reviewer", "scope": "demo"} if step.get("approval", {}).get("required") else None
        run = runs.complete_step(run["run_id"], step["step_id"], outputs=outputs,
                                 dod_results=dod, by="demo-operator", approval=approval)
    print("[4/8] SOP 证据门     PASS — 每步有产物、DoD 和所需人审，已到授权执行步骤")

    deliverables.create({
        "deliverable_id": "demo-distribution", "version": "1",
        "work_order_id": order["work_order_id"], "template_id": "T07", "template_version": "1.0.0",
        "deliverable_type": "distribution_and_followup_pack",
        "fields": _fields("T07", "demo-distribution", order["work_order_id"]), "risk_class": "R3"}, by="demo-author")
    deliverables.submit("demo-distribution", by="demo-author")
    deliverables.review("demo-distribution", results=_review("T07"), by="demo-channel-reviewer")
    action = materialize(
        data, work_order_id=order["work_order_id"], step_id="authorize-execution",
        deliverable_id="demo-distribution", deliverable_version="1",
        strategy={"primary": {"id": "github", "name": "GitHub"}},
        targets=[{"target": "issue:demo/repo#1", "body": "demo：已验收教程评论，不会发送"}],
        campaign="work-demo-action", account="demo-account", by="demo-operator")
    print("[5/8] 执行桥         PASS — accepted 交付物生成既有 plan/queue；尚未发送")

    approve_plan(data, "work-demo-action", by="demo-human", note="demo：只审批计划，不执行队列")
    remaining = sop["steps"][3:]
    for step in remaining:
        outputs = [{"deliverable_type": o["deliverable_type"], "artifact_id": f"demo-{step['step_id']}",
                    "version": "1", "evidence_ref": "demo:step-output"} for o in step["outputs"]]
        dod = [{"rule_id": d["rule_id"], "status": "pass", "evidence_ref": "demo:dod"} for d in step["dod"]]
        approval = {"by": "demo-human", "scope": "demo"} if step.get("approval", {}).get("required") else None
        run = runs.complete_step(run["run_id"], step["step_id"], outputs=outputs,
                                 dod_results=dod, by="demo-operator", approval=approval)
    work.transition(order["work_order_id"], "review", by="demo-operator")
    work.transition(order["work_order_id"], "accepted", by="demo-human")
    print("[6/8] 审批与工作验收 PASS — 计划获具名审批，SOP done，工作交付 accepted；queue 仍未执行")

    events = [
        {"event_type": "eligible_tutorial_cohort_entry", "developer_id": "dev-1"},
        {"event_type": "eligible_tutorial_cohort_entry", "developer_id": "dev-2"},
        {"event_type": "first_successful_api_call", "developer_id": "dev-1", "verified": True, "approximated": False},
        {"event_type": "first_successful_api_call", "developer_id": "dev-2", "verified": False, "approximated": False},
    ]
    metric = MetricRegistry(data).get("developer_activation_rate", "1.0.0")
    result = measure(metric, events)
    assert result["numerator"] == 1 and result["denominator"] == 2 and result["value"] == 0.5
    print("[7/8] KPI            PASS — 独立产品事件：1/2 激活；未核验事件不计入，发布量不参与计算")

    plan_exists = Path(action["plan"]).exists()
    queue_exists = Path(action["queue"]).exists()
    no_ledger = not (data / "ledger.jsonl").exists()
    ok = run["state"] == "done" and plan_exists and queue_exists and no_ledger
    print(f"[8/8] 边界           {'PASS' if ok else 'FAIL'} — plan/queue 已落盘且审批，无发送账本、无网络、无账号")
    print("\n真实下一步：mkt review work-demo-action → 人工确认真实载荷后，才可另行决定 queue dry-run/go。")
    return 0 if ok else 1
