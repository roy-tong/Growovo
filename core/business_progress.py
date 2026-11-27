"""Persistent, business-readable progress distinct from receipts and outcomes."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path

from .storage import AppendOnlyJSONL


SCHEMA = "growovo.business-progress/1"
FILENAME = "business-progress.jsonl"
EVENT_TYPES = {
    "scope_check": "配额核准",
    "evidence_confirmation": "证据确认",
    "diagnosis": "问题定位",
    "recovery_decision": "恢复决定",
    "preparation": "备料进展",
    "verification": "修复验证",
    "execution_result": "执行结果",
}
ACTION_LABELS = {
    "x/publish": "X 原创", "x/reply": "X 回帖", "x/message": "X 私信",
    "github/publish": "GitHub 发布", "github/reply": "GitHub 回帖",
    "email/message": "邮件", "discord/publish": "Discord 发布", "discord/reply": "Discord 回帖",
}
RECOVERY_LABELS = {
    "wait": "等待当前作业结束，不新建作业",
    "retry_same_job": "沿用原作业重试，不新建活动",
    "approve_same_job": "按原波次范围完成审批，不新建活动",
    "reload_job": "重新加载已登记的作业字段，不新建活动",
    "diagnose_build": "沿用原作业检查构建路径和版本，不新建活动",
    "reconcile": "先独立核对平台结果，不重发、不新建活动",
    "prepare_same_job": "继续补齐原波次缺少的合格素材",
    "new_recovery_job": "原审批范围和窗口均有效时，才创建关联的恢复作业",
    "inspect_results": "查看逐条执行结果，并继续观察真实业务结果",
    "manual_review": "人工核对原范围和阻断原因，不通过换作业 ID 绕过",
}


def _path(data_dir):
    return Path(data_dir) / FILENAME


def _fingerprint(event):
    value = {key: event[key] for key in sorted(event)
             if key not in {"schema", "event_id", "occurred_at", "recorded"}}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _last(data_dir):
    path = _path(data_dir)
    if not path.is_file() or path.stat().st_size == 0:
        return None
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 65536))
        lines = handle.read().splitlines()
    for raw in reversed(lines):
        if raw.strip():
            return json.loads(raw.decode("utf-8"))
    return None


def record(data_dir, event, *, now=None):
    row = copy.deepcopy(event)
    for field in ("event_type", "title", "fact", "impact", "next_action"):
        if not str(row.get(field) or "").strip():
            raise ValueError("业务进展缺少字段：%s" % field)
    if row["event_type"] not in EVENT_TYPES:
        raise ValueError("未知业务进展类型：%s" % row["event_type"])
    row.setdefault("job_id", "")
    row.setdefault("wave_id", "")
    row.setdefault("counts", {})
    row.setdefault("evidence_refs", [])
    row.setdefault("technical", {})
    fingerprint = _fingerprint(row)
    previous = _last(data_dir)
    if previous and _fingerprint(previous) == fingerprint:
        return dict(previous, recorded=False)
    row["schema"] = SCHEMA
    row["event_id"] = "bp-" + fingerprint[:20]
    row["occurred_at"] = str(now or dt.datetime.now().astimezone().isoformat())
    AppendOnlyJSONL(_path(data_dir)).append(row)
    return dict(row, recorded=True)


def events(data_dir, *, job_id="", wave_id="", event_type="", limit=20):
    rows = AppendOnlyJSONL(_path(data_dir)).all()
    rows = [row for row in rows
            if (not job_id or row.get("job_id") == job_id)
            and (not wave_id or row.get("wave_id") == wave_id)
            and (not event_type or row.get("event_type") == event_type)]
    return rows[-max(1, int(limit)):]


def render(event, *, include_technical=False):
    lines = ["[%s] %s" % (event.get("title") or EVENT_TYPES.get(event.get("event_type"), "业务进展"),
                           event["fact"]),
             "影响：%s" % event["impact"],
             "下一步：%s" % event["next_action"]]
    refs = event.get("evidence_refs") or []
    if refs:
        lines.append("证据：%s" % "；".join(str(ref) for ref in refs))
    if include_technical and event.get("technical"):
        lines.append("技术附录：%s" % json.dumps(event["technical"], ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)


def _action_text(counts, verb):
    parts = []
    for key, value in sorted((counts or {}).items()):
        if not value:
            continue
        product, _, action = str(key).partition("/")
        label = ACTION_LABELS.get(action, action or key)
        product_label = product.capitalize() + " " if product else ""
        parts.append("%s%s%s %d 条" % (product_label, label, verb, value))
    return "、".join(parts)


def from_preparation(preparation):
    wave_id = str(preparation.get("wave_id") or "")
    status = str(preparation.get("wave_status") or "")
    shortfall = preparation.get("shortfall") or {}
    surplus = preparation.get("surplus") or {}
    scope_errors = preparation.get("scope_errors") or []
    executed = int(preparation.get("executed") or 0)
    snapshot = preparation.get("snapshot") or {}
    counts = {
        "missing_supply": sum(shortfall.values()), "surplus_supply": sum(surplus.values()),
        "preparation_tasks": executed, "snapshot_items": int(snapshot.get("item_count") or 0),
        "external_writes": int(preparation.get("external_writes") or 0),
    }
    technical = {"wave_status": status, "shortfall": shortfall,
                 "surplus": surplus, "scope_errors": scope_errors}
    evidence_refs = [preparation["state_path"]] if preparation.get("state_path") else []
    base = {"wave_id": wave_id, "job_id": str(preparation.get("job_id") or wave_id), "counts": counts,
            "technical": technical, "evidence_refs": evidence_refs}
    if surplus or scope_errors or status == "supply_scope_invalid":
        findings = []
        if surplus:
            findings.append(_action_text(surplus, "多"))
        if "duplicate_target" in scope_errors:
            findings.append("发现跨波次重复目标")
        return dict(base, event_type="scope_check", title="配额核准",
                    fact="%s 备料超出范围：%s；尚未外发。" % (wave_id, "；".join(findings)),
                    impact="超配或重复部分不能发送，也不计入合格动作；其余合格素材仍需重新核准。",
                    next_action="先移除超配或重复素材，再按原波次范围重新审批。")
    if status == "waiting_wave_approval":
        return dict(base, event_type="recovery_decision", title="恢复决定",
                    fact="%s 素材已齐备，正在等待原波次审批；尚未外发。" % wave_id,
                    impact="没有有效审批就不能发送，不需要新建活动或更换作业 ID。",
                    next_action=RECOVERY_LABELS["approve_same_job"])
    if status in {"ready_to_send", "waiting_reconciliation"}:
        waiting = status == "waiting_reconciliation"
        return dict(base, event_type="recovery_decision" if waiting else "preparation",
                    title="恢复决定" if waiting else "备料进展",
                    fact="%s 已冻结 %d 条获批素材。" % (wave_id, counts["snapshot_items"]),
                    impact="发送结果需要独立回读；未知结果不能自动重发。" if waiting else "冻结只代表可以进入审批，不代表已经发送或产生业务结果。",
                    next_action=RECOVERY_LABELS["reconcile"] if waiting else "按原审批范围执行并逐条回读。")
    missing_text = _action_text(shortfall, "还缺")
    fact = "%s 正在备料，本轮完成 %d 项任务" % (wave_id, executed)
    if missing_text:
        fact += "；%s" % missing_text
    fact += "；尚未外发。"
    return dict(base, event_type="preparation", title="备料进展", fact=fact,
                impact="合格素材不足时不能发送这一波，也不能用不相关对象凑数。",
                next_action="继续只补齐缺少的产品、平台和动作素材，齐备后冻结审批。")


def _action_gap(card, key):
    return int(((card.get("actions") or {}).get(key) or {}).get("gap") or 0)


def _blocker_guidance(card):
    facts = []
    actions = []
    for reason, count in (card.get("blockers") or {}).items():
        code = str(reason)
        if code.startswith("github:") and "403" in code:
            facts.append("GitHub 预检返回 403，%d 条回帖未执行" % _action_gap(card, "github/reply"))
            actions.append("先核对 GitHub 403 的具体类别")
        elif code == "stop_probe_pending":
            facts.append("%d 条 GitHub 回帖在半开探测后延后" % count)
            actions.append("延后者不计成功，也不自动补发")
        elif code in {"unsupported", "managed_tab_required"}:
            missing = _action_gap(card, "x/message") or int(count)
            facts.append("%d 条私信因路线不支持未发送" % missing)
            actions.append("私信路线验收前不计入可完成量")
        elif code == "x_link_in_body":
            facts.append("%d 条原创因链接规则未通过" % _action_gap(card, "x/publish"))
            actions.append("先修正链接策略再重新预检")
    return facts, actions


def from_job(job, card, recovery):
    execution = card.get("execution") or {}
    action = str((recovery or {}).get("action") or "manual_review")
    counts = {key: int(execution.get(key) or 0)
              for key in ("target", "committed", "failed", "indeterminate", "skipped", "deferred")}
    facts, guidance = _blocker_guidance(card)
    fact = str(card.get("headline") or "")
    if facts:
        fact += "；" + "；".join(facts) + "。"
    next_action = "；".join(guidance) if guidance else (
        RECOVERY_LABELS.get(action, "") if action != "inspect_results"
        else str(card.get("next_action") or ""))
    base = {
        "job_id": str(job.get("id") or ""), "wave_id": str((job.get("wave") or job.get("id") or "")),
        "counts": counts, "technical": {"reason": (recovery or {}).get("reason"),
                                        "recovery_action": action,
                                        "business_outcome": card.get("business_outcome")},
        "evidence_refs": ([card["summary_path"]] if card.get("summary_path") else []),
    }
    if action != "inspect_results" or counts["indeterminate"]:
        return dict(base, event_type="recovery_decision", title="恢复决定",
                    fact=fact or "当前波次需要恢复处理。",
                    impact="当前波次没有完成全部计划动作；不能把阻断、过期或未知结果当作发送成功。",
                    next_action=next_action or RECOVERY_LABELS.get(action, RECOVERY_LABELS["manual_review"]))
    if counts["committed"]:
        return dict(base, event_type="evidence_confirmation", title="证据确认",
                    fact=fact or "平台动作已有回执。",
                    impact="这些记录只证明平台动作，不证明首用、Star、粉丝或业务增长；业务结果仍为未测量。",
                    next_action=next_action or RECOVERY_LABELS["inspect_results"])
    return dict(base, event_type="execution_result", title="执行结果",
                fact=fact or "本波没有平台动作。",
                impact="没有发送成功记录，也没有业务结果证据。",
                next_action=next_action or RECOVERY_LABELS["inspect_results"])
