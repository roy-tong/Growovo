#!/usr/bin/env python3
"""计划审批 —— 把"人审"从布尔字段升级为可复核的审批事件。

问题：原实现只检查 review.human_approved，谁批的、批的是哪一版、批完有没有被
改过、什么时候失效，全都不可查；而实验引擎已经有 sha256 预注册指纹，计划反而
没有。本模块补上这个不对称：

1. 指纹 plan_sha256 = 计划内容（不含 review 块）的规范化 JSON sha256；
   批准后任何实质修改都会让指纹失配，实跑被拒（必须重新审批）。
2. 审批写两条记录：计划内 review 块（供执行侧快速判断）+ data/approvals.jsonl
   追加事件（供审计：谁、何时、批的哪一版、有效期）。
3. 有效期可选（--days），过期即拒跑。

向后兼容：旧计划只有 human_approved=true、没有指纹时仍放行（legacy），
但会在审批状态里标 legacy，且新审批一律带指纹。
"""
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path


class ApprovalError(RuntimeError):
    pass


def fingerprint(plan):
    """计划内容指纹：排除 review 块（审批元数据不能改变自己的指纹）。"""
    payload = {k: v for k, v in (plan or {}).items() if k != "review"}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _queue_payloads(plan, data_dir=None):
    """读取计划声明的队列；声明了却缺失时拒绝审批，而不是批准一个空壳。"""
    payloads = []
    for wave in (plan or {}).get("waves", []):
        ref = str(wave.get("queue") or "").strip()
        if not ref:
            continue
        candidates = [Path(ref)]
        if data_dir is not None and not Path(ref).is_absolute():
            candidates.extend([Path(data_dir) / ref,
                               Path(data_dir) / "queues" / Path(ref).name])
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            raise ApprovalError(f"计划引用的队列不存在，无法绑定实际发送内容: {ref}")
        queue = _read_json(path)
        items = queue.get("items") or []
        queue["items"] = sorted(items, key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True))
        payloads.append({"path": str(path.resolve()), "queue": queue})
    return payloads


def execution_fingerprint(plan, *, data_dir=None, workspace=None, account=None):
    """审批实际执行内容：plan + queue + account + workspace。

    queue 内 targets/body/action/platform 都会改变哈希；账号与工作区也会参与，
    因此批准后换队列内容、换目标账号或换工作目录都必须重新审批。
    """
    payload = {
        "plan": {k: v for k, v in (plan or {}).items() if k != "review"},
        "queues": _queue_payloads(plan, data_dir=data_dir),
        "workspace": str(workspace or (plan or {}).get("workspace") or
                           (Path(data_dir).resolve() if data_dir is not None else "")),
        "account": str(account or (plan or {}).get("account") or "default"),
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def plans_dir(data_dir):
    return Path(data_dir) / "plans"


def approvals_path(data_dir):
    return Path(data_dir) / "approvals.jsonl"


def list_plans(data_dir):
    d = plans_dir(data_dir)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            plan = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append((plan, f))
    return out


def find_plan(data_dir, plan_id):
    """按 plan_id 精确定位；未命中返回 (None, None)。"""
    for plan, f in list_plans(data_dir):
        if plan.get("plan_id") == plan_id:
            return plan, f
    return None, None


def save_plan(plan, path):
    Path(path).write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
    return plan


def approval_events(data_dir, plan_id=None):
    p = approvals_path(data_dir)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if plan_id is None or rec.get("plan_id") == plan_id:
            out.append(rec)
    return out


def approve_plan(data_dir, plan_id, *, by, note="", days=None, account=None, workspace=None):
    """校验 → 指纹 → 写 review → 追加审批事件。返回 (plan, path, event)。

    校验未过的计划不允许批准（先 lint 再人审，而不是边审边补）。
    """
    from .planlint import lint_plan

    if not str(by or "").strip():
        raise ApprovalError("必须用 --by 署名审批人（无署名的审批不是审批）")
    plan, path = find_plan(data_dir, plan_id)
    if plan is None:
        raise ApprovalError(f"找不到计划 {plan_id!r}（data/plans/<plan_id>.json）")
    errs = lint_plan(plan)
    if errs:
        raise ApprovalError("计划校验未过，不能批准：" + "；".join(errs))

    now = datetime.now()
    fp = execution_fingerprint(plan, data_dir=data_dir, workspace=workspace, account=account)
    review = {
        "human_approved": True,
        "approved_at": now.isoformat(timespec="seconds"),
        "approved_by": by,
        "plan_sha256": fp,
        "fingerprint_scope": "plan+queue+account+workspace",
        "account": str(account or plan.get("account") or "default"),
        "workspace": str(workspace or plan.get("workspace") or Path(data_dir).resolve()),
    }
    if note:
        review["note"] = note
    if days:
        review["expires_at"] = (now + timedelta(days=int(days))).isoformat(timespec="seconds")
    plan["review"] = review
    save_plan(plan, path)
    # 批准版本的快照：即使计划之后被改，也能取回"当时批的是什么"
    snap_dir = Path(data_dir) / "approvals"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap = snap_dir / f"{plan_id}-{fp[:12]}.json"
    snap.write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    event = {
        "ts": review["approved_at"],
        "plan_id": plan_id,
        "action": "approve",
        "by": by,
        "note": note,
        "plan_sha256": fp,
        "fingerprint_scope": review["fingerprint_scope"],
        "account": review["account"],
        "workspace": review["workspace"],
        "expires_at": review.get("expires_at"),
        "snapshot": str(snap),
    }
    p = approvals_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return plan, path, event


def revoke_plan(data_dir, plan_id, *, by, note=""):
    """撤销审批：写事件并把 review.human_approved 置回 false。"""
    if not str(by or "").strip():
        raise ApprovalError("撤销审批必须署名（--by）")
    plan, path = find_plan(data_dir, plan_id)
    if plan is None:
        raise ApprovalError(f"找不到计划 {plan_id!r}")
    review = plan.get("review") or {}
    plan["review"] = {
        "human_approved": False,
        "approved_at": review.get("approved_at"),
        "revoked_at": datetime.now().isoformat(timespec="seconds"),
        "revoked_by": by,
        "note": note or review.get("note", ""),
    }
    save_plan(plan, path)
    event = {"ts": plan["review"]["revoked_at"], "plan_id": plan_id,
             "action": "revoke", "by": by, "note": note}
    p = approvals_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    return plan, event


def verify_approval(plan, *, data_dir=None, account=None, workspace=None):
    """执行前校验。抛 ApprovalError；通过返回 status dict。

    通过条件：human_approved=true；有指纹则必须与当前执行内容一致；未过期。
    旧计划（无指纹）放行但标 legacy。
    """
    review = (plan or {}).get("review") or {}
    if not review.get("human_approved"):
        raise ApprovalError("review.human_approved=false，拒绝执行（人审是执行前置条件）")
    fp = review.get("plan_sha256")
    if fp:
        current = execution_fingerprint(
            plan, data_dir=data_dir,
            workspace=workspace or review.get("workspace"),
            account=account or review.get("account"))
        if fp != current:
            raise ApprovalError(
                "计划/队列/账号/工作区在批准后被修改（执行指纹不一致）——需重新审批，旧批准不覆盖新内容")
    expires = review.get("expires_at")
    if expires:
        try:
            if datetime.now() > datetime.fromisoformat(expires):
                raise ApprovalError(f"批准已于 {expires} 过期——需重新审批")
        except ValueError:
            raise ApprovalError(f"批准的有效期字段无法解析: {expires!r}")
    return {
        "mode": "plan",
        "approved_by": review.get("approved_by"),
        "approved_at": review.get("approved_at"),
        "plan_sha256": fp,
        "fingerprint_scope": review.get("fingerprint_scope", "plan+queue+account+workspace"),
        "account": review.get("account"),
        "workspace": review.get("workspace"),
        "expires_at": expires,
        "legacy": not bool(fp),
    }


def status(plan, *, data_dir=None):
    """只读审批状态（报告用，不抛异常）。"""
    review = (plan or {}).get("review") or {}
    st = {
        "human_approved": bool(review.get("human_approved")),
        "approved_by": review.get("approved_by"),
        "approved_at": review.get("approved_at"),
        "expires_at": review.get("expires_at"),
        "plan_sha256": review.get("plan_sha256"),
        "legacy": bool(review.get("human_approved")) and not review.get("plan_sha256"),
        "valid": False,
        "reason": "",
    }
    if not st["human_approved"]:
        st["reason"] = "未审批"
        return st
    try:
        verify_approval(plan, data_dir=data_dir)
        st["valid"] = True
    except ApprovalError as exc:
        st["reason"] = str(exc)
    return st
