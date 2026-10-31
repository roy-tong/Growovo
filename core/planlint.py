#!/usr/bin/env python3
"""计划校验与强制（05 产品方案 §2.4）——规划层与执行内核之间的契约。

两级：
  lint_plan(plan)    生成后校验：schema/能力矩阵/上限/预注册。不过 = 不进执行
  enforce_approval   执行前强制：--go 写操作必须绑定已人审 plan；
                     无 plan 管辖默认拒跑，人工通道 adhoc=True 才放行（账本记 adhoc=true）

上限是内核属性不是计划属性：plan 里的 size 超过 Throttle 日预算 → lint 直接拒。
"""
import json
from pathlib import Path

from .approval import ApprovalError, verify_approval
from .throttle import INTERVALS

REQUIRED_TOP = ["plan_id", "goal", "created", "hypothesis", "icp", "audience",
                "waves", "success_criteria", "review"]


class PlanNotApproved(RuntimeError):
    pass


def load_plan(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def lint_plan(plan, *, registry=None, intervals=None):
    """返回错误列表；空列表 = 通过。registry/channels 见 channels.REGISTRY。"""
    if registry is None:
        from channels import REGISTRY as registry
    intervals = intervals or INTERVALS
    errs = []

    for k in REQUIRED_TOP:
        if k not in plan or plan[k] in (None, "", [], {}):
            errs.append(f"缺必填字段: {k}")

    icp = plan.get("icp") or {}
    if isinstance(icp, dict) and not icp.get("beachhead"):
        errs.append("icp.beachhead 必填（单一 beachhead 纪律）")

    aud = plan.get("audience") or {}
    if isinstance(aud, dict) and not aud.get("spec"):
        errs.append("audience.spec 必填（客群检索式/来源）")

    sc = plan.get("success_criteria") or {}
    if isinstance(sc, dict):
        for k in ("metric", "target", "stop"):
            if not sc.get(k):
                errs.append(f"success_criteria.{k} 必填（失败判据预注册）")

    waves = plan.get("waves") or []
    if not isinstance(waves, list) or not waves:
        return errs + ["waves 不能为空"]
    for i, w in enumerate(waves, 1):
        wtag = f"wave[{i}]"
        ch, act = w.get("channel"), w.get("action")
        if not ch or not act:
            errs.append(f"{wtag}: channel/action 必填")
            continue
        chinfo = registry.get(ch)
        if chinfo is None:
            errs.append(f"{wtag}: 未知渠道 {ch}")
            continue
        if act not in chinfo.get("actions", set()):
            errs.append(f"{wtag}: {ch} 不支持动作 {act}（能力: {sorted(chinfo['actions'])}）")
        size = w.get("size")
        if not isinstance(size, int) or size <= 0:
            errs.append(f"{wtag}: size 必须为正整数")
        else:
            pol = intervals.get((ch, act))
            daily_cap = pol[3] if pol else None
            if daily_cap is not None and size > daily_cap:
                errs.append(f"{wtag}: size {size} 超过内核日预算 {daily_cap}（{ch}/{act}）——上限是内核属性，改计划或拆波次")
        if not w.get("content_policy"):
            errs.append(f"{wtag}: content_policy 必填（千人千面方针）")

    review = plan.get("review") or {}
    if not isinstance(review, dict) or not isinstance(review.get("human_approved"), bool):
        errs.append("review.human_approved 必须为 bool（人审门禁）")
    return errs


def _plan_governs(plan, campaign):
    """计划是否管辖该 campaign：队列文件名、plan_id 本身、或 plan_id+波次号 命中即算。"""
    pid = plan.get("plan_id", "")
    if campaign == pid:
        return True
    for w in plan.get("waves", []):
        q = w.get("queue", "")
        if q and Path(q).stem == campaign:
            return True
        if campaign == f"{pid}-w{w.get('wave', '')}":
            return True
    return False


def governing_plan(data_dir, campaign):
    """返回管辖该 campaign 的计划及路径；无则 (None, None)。"""
    plans_dir = Path(data_dir) / "plans"
    if not plans_dir.exists() or not campaign:
        return None, None
    for f in sorted(plans_dir.glob("*.json")):
        try:
            plan = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if _plan_governs(plan, campaign):
            return plan, f
    return None, None


def enforce_approval(data_dir, campaign, adhoc=False):
    """执行前强制：--go 写操作必须绑定已人审 plan（05 产品方案 §2.4）。

    - campaign 受某 plan 管辖：校验审批（未批准/批准后被改/过期）→ 拒跑
    - 无任何 plan 管辖：默认同样拒跑；显式 adhoc=True 才放行（CLI --adhoc 人工通道，
      账本记 adhoc=true；MCP 不暴露该通道——agent 不得自我授权）

    返回放行方式："plan"（受管辖且已人审）或 "adhoc"（显式临时放行）。
    """
    plan, _ = governing_plan(data_dir, campaign)
    if plan is not None:
        # 人审不是“某个布尔字段为真”：计划本身必须先过 lint。否则一个缺必填
        # 字段的旧计划只要写了 human_approved=true 就能绕过执行纪律。
        errs = lint_plan(plan)
        if errs:
            raise PlanNotApproved(
                f"campaign '{campaign}' 属于计划 {plan.get('plan_id')}，"
                "计划校验未过：" + "；".join(errs[:5]))
        try:
            verify_approval(plan)
        except ApprovalError as exc:
            raise PlanNotApproved(
                f"campaign '{campaign}' 属于计划 {plan.get('plan_id')}，{exc}")
        return "plan"
    if not adhoc:
        raise PlanNotApproved(
            f"campaign '{campaign or '（未命名）'}' 无已人审 plan 管辖："
            "--go 实跑必须绑定已人审 plan，或人工通道显式 --adhoc 放行"
            "（账本将记 adhoc=true）")
    return "adhoc"
