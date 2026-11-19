#!/usr/bin/env python3
"""单次运行详情页（P0 页面级 PRD：Growovo G-P0-01～16）。

一条链路：canonical 运行文件 + 账本 + 队列 + 计划 + 分层读数
→ 只读投影（dict）→ 自包含 HTML / Markdown 摘要。

只读边界：
- 不写运行文件、账本、裁决、审批、队列、卡片；
- 不审批、不裁决、不恢复、不发送、不重试；
- 页面上的命令只展示，需由人复制执行。

缺失一律显式：字段缺 → 未登记；读数缺 → 未知（不是 0）；
来源读坏 → SOURCE_UNREADABLE，不静默降级成"没发生"。
"""
from __future__ import annotations

import hashlib
import html
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = "growovo.run-detail/1"
GENERATOR = "core.run_detail"
GENERATOR_VERSION = "1"
BOUNDARY = (
    "本页是 canonical 运行文件的只读投影：不写状态、不审批、不裁决、"
    "不恢复、不发送、不重试；页面上的命令只展示，需由人复制执行。"
)

NO_RETRY_RESULTS = {"indeterminate", "no-dm", "bounced", "suppressed"}

STATE_LABELS = {
    "EMPTY": "空",
    "FAILED": "失败",
    "PARTIAL": "部分",
    "STALE": "过期",
    "SOURCE_UNREADABLE": "来源不可读",
    "FIELD_UNREGISTERED": "字段未登记",
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _text(value) -> str:
    return "" if value is None else str(value)


def _h(value) -> str:
    return html.escape(_text(value), quote=True)


def _effective_go(detail: Dict[str, Any]) -> str:
    """运行开关不等于当前可执行：阻断、待开闸、待审都必须先经人。

    ux_state 是运行手册对"现在能不能跑"的判断，优先于 go 开关本身；
    blocked_reason_code 只描述"下一步差什么"，不一定是阻断。
    """
    obj = detail.get("object") or {}
    ux = _text(obj.get("ux_state"))
    blocked = _text(obj.get("blocked_reason_code"))
    if ux == "BLOCKED":
        if blocked:
            return "否（被阻断：%s）" % blocked
        return "否（被阻断）"
    if ux == "NEEDS_INPUT":
        return "否（需人工开闸）"
    if ux == "NEEDS_REVIEW":
        return "否（需要人工裁决后恢复）"
    if blocked:
        return "否（被阻断：%s）" % blocked
    if not obj.get("go"):
        return "否"
    return "是"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_ref(path, kind: str) -> Dict[str, Any]:
    p = Path(str(path)).expanduser()
    ref: Dict[str, Any] = {
        "path": str(p),
        "kind": kind,
        "exists": False,
        "bytes": None,
        "sha256": None,
        "mtime": None,
        "readable": False,
    }
    try:
        if not p.is_file():
            return ref
        stat = p.stat()
        ref["exists"] = True
        ref["bytes"] = stat.st_size
        ref["mtime"] = datetime.fromtimestamp(stat.st_mtime).isoformat(
            timespec="seconds")
        ref["sha256"] = _sha256(p)
        with p.open("rb") as fh:
            fh.read(1)
        ref["readable"] = True
    except OSError as exc:
        ref["error"] = _text(exc)
    return ref


def _now_dt(value=None) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip())
        except ValueError:
            pass
    return datetime.now()


def _naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _parse_stamp(value) -> Optional[datetime]:
    raw = _text(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return _naive(datetime.fromisoformat(raw))
    except ValueError:
        return None


def _belongs(campaign, run_id: str) -> bool:
    campaign = _text(campaign)
    run_id = _text(run_id)
    if not run_id:
        return False
    return campaign == run_id or campaign.startswith(run_id + "-")


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


def _human_age(minutes: Optional[float]) -> str:
    if minutes is None:
        return "未知"
    if minutes < 1:
        return "刚刚"
    if minutes < 60:
        return "%d 分钟前" % int(minutes)
    if minutes < 48 * 60:
        return "%.1f 小时前" % (minutes / 60.0)
    return "%.1f 天前" % (minutes / 1440.0)


# ---------------------------------------------------------------------------
# 各分区投影
# ---------------------------------------------------------------------------

def _object_header(data: Dict[str, Any], explain: Dict[str, Any],
                   run_id: str) -> Dict[str, Any]:
    status = _text(explain.get("canonical_status") or data.get("status"))
    ux_state = _text(explain.get("ux_state")) or _ux_fallback(status)
    return {
        "run_id": _text(data.get("id") or run_id),
        "canonical_status": status or "未登记",
        "ux_state": ux_state,
        "go": bool(explain.get("go", data.get("go"))),
        "owner": _text(data.get("owner")),
        "started": _text(data.get("started")),
        "next_actor": _text(explain.get("next_actor")) or "unknown",
        "next_required_action": _text(explain.get("next_required_action")),
        "next_command": _text(explain.get("next_command")),
        "blocked_reason_code": _text(explain.get("blocked_reason_code")),
        "last_updated_at": _text(explain.get("last_updated_at")),
        "canonical_schema": _text(data.get("schema")),
    }


def _ux_fallback(status: str) -> str:
    if status in ("paused", "cancelled"):
        return "BLOCKED"
    if status in ("draft", "ready_to_arm"):
        return "NEEDS_INPUT"
    if status == "needs_attention":
        return "NEEDS_REVIEW"
    if status == "completed":
        return "DELIVERED"
    return "READY" if status else "UNKNOWN"


def _freshness(last_updated_at: str, now_dt: datetime,
               heartbeat: Dict[str, Any], status: str) -> Dict[str, Any]:
    interval = 0
    try:
        interval = int((heartbeat or {}).get("interval_minutes") or 0)
    except (TypeError, ValueError):
        interval = 0
    limit = max(interval * 2, 24 * 60)
    stamp = _parse_stamp(last_updated_at)
    if stamp is None:
        return {"state": "unknown", "last_updated_at": last_updated_at,
                "age_minutes": None, "limit_minutes": limit,
                "text": "新鲜度未知：运行文件没有可解析的更新时间戳"}
    age = max(0.0, (_naive(now_dt) - stamp).total_seconds() / 60.0)
    exempt = status in ("completed", "cancelled")
    stale = age > limit and not exempt
    if stale:
        text = ("已过期：最近更新 %s（%s），阈值 %d 小时"
                % (stamp.isoformat(timespec="seconds"), _human_age(age),
                   limit // 60))
    else:
        text = ("最近更新 %s（%s）"
                % (stamp.isoformat(timespec="seconds"), _human_age(age)))
    return {"state": "stale" if stale else "fresh",
            "last_updated_at": stamp.isoformat(timespec="seconds"),
            "age_minutes": round(age, 1), "limit_minutes": limit,
            "text": text}


def _goal_block(data: Dict[str, Any], explain: Dict[str, Any]) -> Dict[str, Any]:
    goal = data.get("goal") if isinstance(data.get("goal"), dict) else {}
    progress = explain.get("goal_status") if isinstance(
        explain.get("goal_status"), dict) else {}
    window = explain.get("observation_window") if isinstance(
        explain.get("observation_window"), dict) else {}
    return {
        "id": _text(goal.get("id")),
        "family": _text(goal.get("family")),
        "name": _text(goal.get("name")),
        "metric": _text(goal.get("metric")),
        "metric_label": _text(goal.get("metric_label")),
        "target": goal.get("target"),
        "target_kind": _text(goal.get("target_kind")),
        "target_text": _text(goal.get("target_text")),
        "baseline": goal.get("baseline"),
        "proves": _text(goal.get("proves")),
        "does_not_prove": _text(goal.get("does_not_prove")),
        "leading": [str(x) for x in goal.get("leading") or []],
        "counter": [str(x) for x in goal.get("counter") or []],
        "progress": progress,
        "window": window,
    }


def _strategy_block(data: Dict[str, Any]) -> Dict[str, Any]:
    strategy = data.get("strategy") if isinstance(
        data.get("strategy"), dict) else {}
    primary = strategy.get("primary") if isinstance(
        strategy.get("primary"), dict) else {}
    support = [s for s in strategy.get("support") or [] if isinstance(s, dict)]
    not_doing = [str(s) for s in strategy.get("not_doing") or []]
    tactics = [s for s in strategy.get("tactics") or [] if isinstance(s, dict)]
    return {
        "primary": {
            "channel": _text(primary.get("channel")),
            "platform": _text(primary.get("platform")),
            "tier": _text(primary.get("tier")),
            "why": _text(primary.get("why")),
            "actions": [str(a) for a in primary.get("actions") or []],
            "residency_days": primary.get("residency_days"),
        },
        "support": [
            {"channel": _text(s.get("channel")),
             "platform": _text(s.get("platform")),
             "why": _text(s.get("why"))}
            for s in support],
        "not_doing": not_doing,
        "tactics": [
            {"name": _text(t.get("name") or t.get("id")),
             "why": _text(t.get("why"))}
            for t in tactics],
        "compiled_at": _text(strategy.get("compiled_at")),
        "fingerprint": _text(strategy.get("fingerprint")),
    }


def _quota_block(data: Dict[str, Any], explain: Dict[str, Any]) -> Dict[str, Any]:
    quota = data.get("quota") if isinstance(data.get("quota"), dict) else {}
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
    remaining = explain.get("quota_remaining")
    return {
        "actions_per_week": quota.get("actions_per_week",
                                       plan.get("actions_per_week")),
        "initial_actions_per_week": quota.get(
            "initial_actions_per_week", plan.get("initial_actions_per_week")),
        "max_per_run": quota.get("max_per_run"),
        "interval_seconds": quota.get("interval_seconds"),
        "daily_preview": quota.get("daily_preview"),
        "action_weights": quota.get("action_weights") or {},
        "remaining": remaining if isinstance(remaining, dict) else None,
    }


def _supply_block(data: Dict[str, Any]) -> Dict[str, Any]:
    supply = data.get("supply") if isinstance(data.get("supply"), dict) else {}
    state = data.get("supply_state") if isinstance(
        data.get("supply_state"), dict) else {}
    briefs = [b for b in supply.get("briefs") or [] if isinstance(b, dict)]
    return {
        "briefs": [{"id": _text(b.get("id")), "title": _text(b.get("title")),
                    "status": _text(b.get("status"))} for b in briefs],
        "min_cover_days": supply.get("min_cover_days"),
        "staged": state.get("staged", supply.get("staged")),
        "coverage_days": state.get("coverage_days", supply.get("coverage_days")),
        "gap": state.get("gap", supply.get("gap")),
        "note": _text(state.get("note")),
        "by_day": state.get("by_day") or {},
    }


def _measurement_block(data: Dict[str, Any]) -> Dict[str, Any]:
    measurement = data.get("measurement") if isinstance(
        data.get("measurement"), dict) else {}
    calibration = measurement.get("calibration") if isinstance(
        measurement.get("calibration"), dict) else {}
    verification = _text(measurement.get("verification")) or "UNVERIFIED"
    readers = [r for r in measurement.get("readers") or [] if isinstance(r, dict)]
    return {
        "verification": verification,
        "readers": [{"metric": _text(r.get("metric")),
                    "handle": _text(r.get("handle")),
                    "daily": bool(r.get("daily"))} for r in readers],
        "calibration": {
            "due": _text(calibration.get("due")),
            "streak_days": calibration.get("streak_days"),
            "promoted_at": _text(calibration.get("promoted_at")),
            "conflicted_at": _text(calibration.get("conflicted_at")),
            "checks": [c for c in calibration.get("checks") or []
                       if isinstance(c, dict)],
        },
        "business_campaign": _text(measurement.get("business_campaign")),
    }


def _learning_block(data: Dict[str, Any]) -> Dict[str, Any]:
    learning = data.get("learning") if isinstance(
        data.get("learning"), dict) else {}
    return {
        "on": _text(learning.get("on")),
        "sample": learning.get("sample"),
        "min_sample": learning.get("min_sample"),
        "recommendation": _text(learning.get("recommendation")),
        "changes": [c for c in learning.get("changes") or [] if isinstance(c, dict)],
    }


def _authorization_block(data_dir: Path, run_id: str) -> Dict[str, Any]:
    block: Dict[str, Any] = {
        "plans": [],
        "fingerprint_mismatch": False,
        "human_approved": False,
        "note": "",
        "error": "",
    }
    try:
        from . import approval
    except Exception as exc:  # noqa: BLE001
        block["error"] = "审批模块不可用：%s" % exc
        return block
    try:
        plans = approval.list_plans(data_dir)
    except Exception as exc:  # noqa: BLE001
        block["error"] = "读取计划目录失败：%s" % exc
        return block
    for plan, plan_path in plans:
        if not isinstance(plan, dict):
            continue
        plan_id = _text(plan.get("plan_id") or Path(plan_path).stem)
        related = _belongs(plan_id, run_id) or _belongs(
            plan.get("campaign"), run_id) or _belongs(plan.get("run"), run_id)
        if not related:
            for wave in plan.get("waves") or []:
                if isinstance(wave, dict) and _belongs(wave.get("campaign"), run_id):
                    related = True
                    break
        if not related:
            continue
        try:
            status = approval.status(plan, data_dir=data_dir)
        except Exception as exc:  # noqa: BLE001
            block["plans"].append({
                "plan_id": plan_id, "path": str(plan_path),
                "human_approved": bool((plan.get("review") or {}).get(
                    "human_approved")),
                "valid": None, "effective": False,
                "reason": "审批状态读取失败：%s" % exc,
                "legacy": False, "approved_by": "", "approved_at": "",
                "expires_at": "", "plan_sha256": "", "error": _text(exc)})
            continue
        signed = bool(status.get("human_approved"))
        valid = status.get("valid")
        effective = bool(signed and valid is True)
        reason = _text(status.get("reason"))
        block["plans"].append({
            "plan_id": plan_id,
            "path": str(plan_path),
            "human_approved": signed,
            "effective": effective,
            "approved_by": _text(status.get("approved_by")),
            "approved_at": _text(status.get("approved_at")),
            "expires_at": _text(status.get("expires_at")),
            "plan_sha256": _text(status.get("plan_sha256")),
            "valid": valid,
            "reason": reason,
            "legacy": bool(status.get("legacy")),
        })
        if effective:
            block["human_approved"] = True
        if signed and valid is False:
            if "指纹" in reason:
                block["fingerprint_mismatch"] = True
            if not block["note"]:
                block["note"] = "已签名但当前不可用：%s" % (
                    reason or "审批校验未通过")
    if not block["plans"] and not block["error"]:
        block["note"] = ("未登记：没有与该运行关联的审批记录"
                         "（运行文件本身不含审批指纹）")
    return block


def _content_block(data_dir: Path, run_id: str,
                   data: Dict[str, Any]) -> Dict[str, Any]:
    queues: List[Dict[str, Any]] = []
    errors: List[str] = []
    qdir = data_dir / "queues"
    if qdir.is_dir():
        for queue_path in sorted(qdir.glob("*.json")):
            try:
                payload = json.loads(queue_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append("%s：%s" % (queue_path.name, exc))
                continue
            if not isinstance(payload, dict):
                errors.append("%s：队列不是 JSON 对象" % queue_path.name)
                continue
            campaign = _text(payload.get("campaign"))
            if not _belongs(campaign, run_id):
                continue
            items = []
            for item in payload.get("items") or []:
                if not isinstance(item, dict):
                    continue
                items.append({
                    "id": _text(item.get("id")),
                    "kind": _text(item.get("kind") or payload.get("action")),
                    "target": _text(item.get("target")),
                    "target_text": _text(item.get("target_text")),
                    "body": _text(item.get("body")),
                    "scheduled_for": _text(item.get("scheduled_for")),
                    "staged_by": _text(item.get("staged_by")),
                    "formula": _text(item.get("formula")),
                    "content_score": item.get("content_score"),
                    "content_issues": [str(x) for x in item.get("content_issues") or []],
                    "author_tier": _text(item.get("author_tier")),
                })
            queues.append({
                "path": str(queue_path),
                "campaign": campaign,
                "channel": _text(payload.get("channel")),
                "action": _text(payload.get("action")),
                "items": items,
            })
    score_values = [i["content_score"] for q in queues for i in q["items"]
                    if isinstance(i.get("content_score"), (int, float))]
    with_issues = sum(1 for q in queues for i in q["items"] if i["content_issues"])
    content = data.get("content") if isinstance(data.get("content"), dict) else {}
    return {
        "state": "ok" if queues else "empty",
        "queues": queues,
        "count": sum(len(q["items"]) for q in queues),
        "with_issues": with_issues,
        "score_min": min(score_values) if score_values else None,
        "score_max": max(score_values) if score_values else None,
        "errors": errors,
        "mix": content.get("mix") or {},
        "layers": content.get("layers") or {},
    }


def _timeline_block(data_dir: Path, run_id: str) -> Dict[str, Any]:
    from .ledger import Ledger

    block: Dict[str, Any] = {
        "state": "empty", "rows": [], "counts": {}, "first_at": "",
        "last_at": "", "error": "",
    }
    try:
        records = Ledger(data_dir / "ledger.jsonl").all()
    except Exception as exc:  # noqa: BLE001
        block["error"] = "账本读取失败：%s" % exc
        block["state"] = "unreadable"
        return block
    rows = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if not _belongs(rec.get("campaign"), run_id):
            continue
        result = _text(rec.get("result")) or "unknown"
        rows.append({
            "ts": _text(rec.get("ts")),
            "channel": _text(rec.get("channel")),
            "transport": _text(rec.get("transport")),
            "action": _text(rec.get("action")),
            "target": _text(rec.get("target")),
            "result": result,
            "evidence": _text(rec.get("evidence")),
            "ref": _text(rec.get("ref")),
            "detail": _text(rec.get("detail")),
            "resolved_by": _text(rec.get("resolved_by")),
            "resolved_at": _text(rec.get("resolved_at")),
            "resolution_note": _text(rec.get("resolution_note")),
            "no_retry": result in NO_RETRY_RESULTS,
            "indeterminate": result == "indeterminate",
        })
    rows.sort(key=lambda r: (r["ts"], r["ref"], r["action"], r["target"]))
    counts: Dict[str, int] = {"attempted": len(rows)}
    for name in ("committed", "indeterminate", "failed", "dry-run",
                 "no-dm", "bounced", "suppressed"):
        counts[name] = sum(1 for r in rows if r["result"] == name)
    counts["unknown"] = counts["indeterminate"]
    counts["with_evidence"] = sum(1 for r in rows if r["evidence"])
    counts["unresolved_indeterminate"] = sum(
        1 for r in rows if r["indeterminate"] and not r["resolved_by"])
    block.update({
        "state": "ok" if rows else "empty",
        "rows": rows,
        "counts": counts,
        "first_at": rows[0]["ts"] if rows else "",
        "last_at": rows[-1]["ts"] if rows else "",
    })
    return block


def _empty_replies(note: str = "") -> Dict[str, Any]:
    return {
        "state": "empty", "error": "", "cases": [], "count": 0,
        "linked_count": 0, "unresolved_count": 0, "conflict_count": 0,
        "conflicts": [],
        "dnc": {"pending": 0, "active": 0, "failed": 0,
                "unverified": [], "error": ""},
        "queue_path": "", "batches": [],
        "note": note or "还没有导入回复：用 mkt grow reply import 导入授权回复文件",
    }


def _replies_block(data_dir: Path, run_id: str) -> Dict[str, Any]:
    try:
        from . import reply_case
        block = reply_case.run_projection(data_dir, run_id)
    except Exception as exc:  # noqa: BLE001 页面不因回复存储坏掉而崩
        block = _empty_replies()
        block["state"] = "unreadable"
        block["error"] = "%s: %s" % (type(exc).__name__, exc)
        return block
    if not isinstance(block, dict):
        block = _empty_replies()
        block["state"] = "unreadable"
        block["error"] = "回复投影返回值不是对象"
    return block


def _empty_journey(note: str = "") -> Dict[str, Any]:
    return {
        "schema": "growovo.journey-view/1", "state": "empty",
        "error": "", "run_id": "", "contracts": [], "projections": [],
        "counts": {}, "unresolved": [], "stale": [], "stop_count": 0,
        "conflict_count": 0, "orphan_projections": 0,
        "note": note or "还没有目标退出契约：用 mkt grow goal-exit contract 登记",
        "paths": {},
    }


def _journey_block(data_dir: Path, run_id: str) -> Dict[str, Any]:
    try:
        from . import goal_exit
        block = goal_exit.run_projection(data_dir, run_id)
    except Exception as exc:  # noqa: BLE001 页面不因旅程存储坏掉而崩
        block = _empty_journey()
        block["state"] = "unreadable"
        block["error"] = "%s: %s" % (type(exc).__name__, exc)
        return block
    if not isinstance(block, dict):
        block = _empty_journey()
        block["state"] = "unreadable"
        block["error"] = "旅程投影返回值不是对象"
    return block


def _empty_signals(note: str = "") -> Dict[str, Any]:
    return {
        "schema": "growovo.signal-view/1", "state": "empty", "run_id": "",
        "signals": [], "previews": [],
        "counts": {"total": 0, "usable": 0, "stale": 0, "revoked": 0,
                   "incomplete": 0, "unresolved_identity": 0},
        "stale": [], "gaps": [], "preview_stale": [], "delete_items": [],
        "preview_count": 0, "delete_count": 0, "conflicts": [],
        "incidents": [], "injection_note": "",
        "note": note or ("还没有信号：用 mkt grow signal add 登记来源与观察时间"),
        "paths": {},
    }


def _signals_block(data_dir: Path, run_id: str) -> Dict[str, Any]:
    try:
        from . import signal_envelope
        block = signal_envelope.signal_view(data_dir, run_id=run_id)
    except Exception as exc:  # noqa: BLE001 页面不因信号存储坏掉而崩
        block = _empty_signals()
        block["state"] = "unreadable"
        block["error"] = "%s: %s" % (type(exc).__name__, exc)
        return block
    if not isinstance(block, dict):
        block = _empty_signals()
        block["state"] = "unreadable"
        block["error"] = "信号投影返回值不是对象"
    return block


def _layers_block(data_dir: Path, run_id: str) -> Dict[str, Any]:
    try:
        from . import outcome_layers
        payload = outcome_layers.layer_summary(data_dir, run_id=run_id)
        if isinstance(payload, dict):
            payload.setdefault("state", "ok")
            return payload
    except Exception as exc:  # noqa: BLE001
        return {"state": "failed", "run": run_id, "window": None,
                "L1": {"state": "UNKNOWN"}, "L2": {"state": "UNKNOWN"},
                "L3": {"available": False, "state": "UNKNOWN",
                       "text": "分层读数不可用：%s" % exc},
                "attribution": {"state": "UNKNOWN"},
                "note": "分层读数读取失败：%s" % exc}
    return {"state": "failed", "run": run_id, "window": None,
            "L1": {"state": "UNKNOWN"}, "L2": {"state": "UNKNOWN"},
            "L3": {"available": False, "state": "UNKNOWN",
                   "text": "分层读数返回了非对象结果"},
            "attribution": {"state": "UNKNOWN"},
            "note": "分层读数返回了非对象结果"}


def _optimization_block(data_dir: Path, run_id: str) -> Dict[str, Any]:
    try:
        from . import optimization, scorecard
        card = scorecard.build(data_dir, campaign=run_id)
        payload = optimization.propose(card)
    except Exception as exc:  # noqa: BLE001
        return {"state": "unavailable", "proposals": [], "counts": {},
                "boundary": "", "note": "优化建议暂不可用：%s" % exc}
    proposals = []
    for proposal in payload.get("proposals") or []:
        if not isinstance(proposal, dict):
            continue
        item = dict(proposal)
        item["requires_human"] = True
        proposals.append(item)
    return {
        "state": "ok" if proposals else "empty",
        "proposals": proposals,
        "counts": payload.get("counts") or {},
        "boundary": _text(payload.get("boundary")),
        "note": "" if proposals else "暂无建议：样本或读数不足",
    }


def _order_incidents(incidents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items = [i for i in incidents if isinstance(i, dict)]
    items = sorted(items, key=lambda i: _text(i.get("detected_at")), reverse=True)

    def rank(item):
        if item.get("kind") == "indeterminate_external_action":
            return 0
        return 1 if item.get("severity") == "blocking" else 2

    return sorted(items, key=rank)


def _reply_states(replies: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    if not replies:
        return []
    states: List[Dict[str, str]] = []
    if replies.get("error"):
        states.append({"code": "SOURCE_UNREADABLE",
                       "detail": "回复案例读取失败：%s" % replies["error"]})
    dnc = replies.get("dnc") or {}
    if dnc.get("failed"):
        states.append({"code": "PARTIAL",
                       "detail": "DNC 写入失败 %s 条：发送门禁必须保持暂停"
                                 % dnc["failed"]})
    if dnc.get("unverified"):
        states.append({"code": "PARTIAL",
                       "detail": "DNC 待核验 %d 条：确认后才会抑制"
                                 % len(dnc["unverified"])})
    if replies.get("conflict_count"):
        states.append({"code": "PARTIAL",
                       "detail": "回复导入冲突 %d 条：旧案例保留，需人工核对"
                                 % replies["conflict_count"]})
    if replies.get("unresolved_count"):
        states.append({"code": "PARTIAL",
                       "detail": "未关联 run 的回复 %d 条：进统一队列，"
                                 "不归入本次运行" % replies["unresolved_count"]})
    return states


def _states_for(*, run_missing: bool, run_failed: bool,
                freshness: Dict[str, Any],
                authorization: Dict[str, Any],
                content: Dict[str, Any],
                timeline: Dict[str, Any],
                layers: Dict[str, Any],
                goal: Dict[str, Any],
                strategy: Dict[str, Any],
                replies: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    states: List[Dict[str, str]] = []
    if run_missing:
        states.append({"code": "EMPTY", "detail": "运行文件不存在，页面不渲染任何结论"})
        return states
    if run_failed:
        states.append({"code": "FAILED", "detail": "运行文件无法解析，页面只展示来源与边界"})
        return states
    if freshness.get("state") == "stale":
        states.append({"code": "STALE",
                       "detail": freshness.get("text") or "数据已过期"})
    for section, name in ((content, "内容队列"), (timeline, "执行账本")):
        if section.get("errors"):
            states.append({"code": "SOURCE_UNREADABLE",
                           "detail": "%s读取失败：%s" % (
                               name, "；".join(section.get("errors") or [])[:300])})
        elif section.get("state") == "unreadable":
            states.append({"code": "SOURCE_UNREADABLE",
                           "detail": section.get("error") or "%s不可读" % name})
    if authorization.get("error"):
        states.append({"code": "SOURCE_UNREADABLE",
                       "detail": authorization["error"]})
    if authorization.get("fingerprint_mismatch"):
        states.append({"code": "PARTIAL",
                       "detail": "审批指纹与当前计划不一致：旧批准不覆盖新内容"})
    l3 = (layers or {}).get("L3") or {}
    if not isinstance(l3, dict):
        l3 = {}
    if _text(l3.get("state")) == "UNKNOWN":
        states.append({"code": "PARTIAL",
                       "detail": "L3 业务读数未接入：是未知，不是 0"})
    for label, value in (("目标", goal.get("metric")),
                         ("目标值", goal.get("target_text") or goal.get("target")),
                         ("主策略", strategy["primary"].get("channel")),
                         ("配额", None)):
        if label == "配额":
            continue
        if value in (None, "", [], {}):
            states.append({"code": "FIELD_UNREGISTERED",
                           "detail": "运行文件缺少%s字段" % label})
    states.extend(_reply_states(replies))
    if not states:
        states.append({"code": "OK", "detail": "页面所需字段与来源均可读"})
    return states


def _overall_state(run_missing: bool, run_failed: bool,
                   states: List[Dict[str, str]]) -> str:
    if run_missing:
        return "empty"
    if run_failed:
        return "failed"
    codes = {s["code"] for s in states}
    if codes & {"STALE", "PARTIAL", "SOURCE_UNREADABLE", "FIELD_UNREGISTERED"}:
        return "partial"
    return "ok"


def _fallback_explain(data: Dict[str, Any], path: Path, error: str) -> Dict[str, Any]:
    status = _text(data.get("status"))
    goal = data.get("goal") if isinstance(data.get("goal"), dict) else {}
    return {
        "schema": "growovo.status-explain/1",
        "run": _text(data.get("id")),
        "canonical_status": status,
        "ux_state": _ux_fallback(status),
        "go": bool(data.get("go")),
        "next_actor": "human",
        "next_required_action": "状态解释不可用：%s；先人工核对运行文件" % error,
        "next_command": "mkt grow status %s" % _text(data.get("id")),
        "blocked_reason_code": "INTERNAL_ERROR",
        "last_updated_at": "",
        "has_pending_external_effect": None,
        "artifact_refs": [str(path)],
        "goal_status": {"metric": goal.get("metric"), "current": None,
                        "note": "目标进度读取失败"},
        "quota_remaining": None,
        "observation_window": {"start": goal.get("starts"),
                               "end": goal.get("ends"),
                               "days": goal.get("window_days")},
        "data_coverage": None,
        "incidents": [],
        "faults": [],
        "checks": [],
    }


def _empty_detail(*, run_id: str, generated_at: str, path: Path,
                  reason: str, missing: List[Dict[str, Any]],
                  replies: Optional[Dict[str, Any]] = None,
                  journey: Optional[Dict[str, Any]] = None,
                  signals: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "generated_at": generated_at,
        "read_only": True,
        "state": "empty",
        "states": [{"code": "EMPTY", "detail": reason}],
        "boundary": BOUNDARY,
        "object": {"run_id": run_id, "canonical_status": "未登记",
                   "ux_state": "UNKNOWN", "go": False, "owner": "",
                   "started": "", "next_actor": "unknown",
                   "next_required_action": reason, "next_command": "",
                   "blocked_reason_code": "", "last_updated_at": "",
                   "canonical_schema": ""},
        "source": {"path": str(path), "exists": False, "sha256": None,
                   "bytes": None, "mtime": None, "schema": "",
                   "generated_at": generated_at, "data_dir": "",
                   "generator": "%s v%s" % (GENERATOR, GENERATOR_VERSION)},
        "freshness": {"state": "unknown", "last_updated_at": "",
                      "age_minutes": None, "limit_minutes": None,
                      "text": "运行文件不存在"},
        "goal": {}, "strategy": {"primary": {}, "support": [], "not_doing": [],
                                 "tactics": [], "compiled_at": "",
                                 "fingerprint": ""},
        "quota": {}, "supply": {}, "measurement": {}, "learning": {},
        "authorization": {"plans": [], "fingerprint_mismatch": False,
                          "human_approved": False, "note": "", "error": ""},
        "content": {"state": "empty", "queues": [], "count": 0,
                    "with_issues": 0, "score_min": None, "score_max": None,
                    "errors": [], "mix": {}, "layers": {}},
        "timeline": {"state": "empty", "rows": [], "counts": {},
                     "first_at": "", "last_at": "", "error": ""},
        "replies": replies if replies is not None else _empty_replies(reason),
        "journey": journey if journey is not None else _empty_journey(reason),
        "signals": signals if signals is not None else _empty_signals(reason),
        "layers": {"state": "empty", "L1": {"state": "UNKNOWN"},
                   "L2": {"state": "UNKNOWN"},
                   "L3": {"available": False, "state": "UNKNOWN",
                          "text": "运行文件不存在，无法读取分层读数"},
                   "attribution": {"state": "UNKNOWN"}, "note": ""},
        "incidents": [], "faults": [], "checks": [],
        "optimization": {"state": "unavailable", "proposals": [],
                         "counts": {}, "boundary": "",
                         "note": "运行文件不存在，无法生成建议"},
        "artifacts": [dict(_file_ref(path, "run"))],
        "missing": missing,
        "notes": [reason],
    }


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------

def build_run_detail(data_dir, run_id: str = "", *, run_path=None,
                     on: Optional[str] = None,
                     now=None) -> Dict[str, Any]:
    """把一次运行投影成只读详情页数据。缺失/读坏都不抛异常。"""
    from . import grow_runbook as rb

    data_dir = Path(data_dir)
    now_dt = _now_dt(now)
    generated_at = _iso(now_dt) or ""
    day = _text(on) or _naive(now_dt).date().isoformat()
    path = Path(run_path).expanduser() if run_path else rb.run_path_for(
        data_dir, run_id)
    rid = _text(run_id)

    if not rid and run_path is None:
        return _empty_detail(
            run_id="", generated_at=generated_at, path=path,
            reason="没有指定运行 ID：页面不猜测对象",
            missing=[{"path": str(path), "kind": "run",
                      "reason": "缺少 --id"}])

    if not path.is_file():
        replies = _replies_block(data_dir, rid)
        detail = _empty_detail(
            run_id=rid, generated_at=generated_at, path=path,
            reason="运行文件不存在：%s" % path,
            missing=[{"path": str(path), "kind": "run",
                      "reason": "canonical 运行文件不存在"}],
            replies=replies, journey=_journey_block(data_dir, rid),
            signals=_signals_block(data_dir, rid))
        extra = _reply_states(replies)
        if extra:
            # 运行文件没了，DNC 失败等安全信号仍必须可见。
            detail["states"].extend(extra)
            detail["state"] = "partial"
        return detail

    notes: List[str] = []
    run_failed = False
    parse_error = ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("运行文件不是 JSON 对象")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        data = {}
        run_failed = True
        parse_error = "运行文件无法解析：%s" % exc
        notes.append(parse_error)

    rid = rid or _text(data.get("id"))

    if run_failed:
        detail = _empty_detail(
            run_id=rid, generated_at=generated_at, path=path,
            reason=parse_error,
            missing=[{"path": str(path), "kind": "run", "reason": parse_error}],
            journey=_journey_block(data_dir, rid),
            signals=_signals_block(data_dir, rid))
        detail["state"] = "failed"
        detail["states"] = [{"code": "FAILED", "detail": parse_error}]
        detail["source"].update({"exists": True, "bytes": path.stat().st_size})
        return detail

    try:
        explain = rb.explain_status(data_dir, path, on=day, now=generated_at)
    except Exception as exc:  # noqa: BLE001
        explain = _fallback_explain(data, path, _text(exc))
        notes.append("状态解释不可用：%s" % exc)

    goal = _goal_block(data, explain)
    strategy = _strategy_block(data)
    quota = _quota_block(data, explain)
    supply = _supply_block(data)
    measurement = _measurement_block(data)
    learning = _learning_block(data)
    authorization = _authorization_block(data_dir, rid)
    content = _content_block(data_dir, rid, data)
    timeline = _timeline_block(data_dir, rid)
    replies = _replies_block(data_dir, rid)
    journey = _journey_block(data_dir, rid)
    signals = _signals_block(data_dir, rid)
    layers = _layers_block(data_dir, rid)
    optimization = _optimization_block(data_dir, rid)
    incidents = _order_incidents(explain.get("incidents") or [])
    freshness = _freshness(explain.get("last_updated_at") or "",
                           now_dt, data.get("heartbeat") or {},
                           _text(explain.get("canonical_status")
                                 or data.get("status")))

    states = _states_for(run_missing=False, run_failed=False,
                         freshness=freshness, authorization=authorization,
                         content=content, timeline=timeline, layers=layers,
                         goal=goal, strategy=strategy, replies=replies)

    artifacts: List[Dict[str, Any]] = []
    seen = set()

    def add(ref: Dict[str, Any]) -> None:
        key = (ref.get("path"), ref.get("kind"))
        if key in seen:
            return
        seen.add(key)
        artifacts.append(ref)

    add(_file_ref(path, "run"))
    facts_path = _text(data.get("facts_ref"))
    if facts_path:
        ref = _file_ref(facts_path, "facts")
        add(ref)
        if not ref["exists"]:
            states.append({"code": "PARTIAL",
                           "detail": "事实包不存在：%s" % facts_path})
    for ref_path in explain.get("artifact_refs") or []:
        ref = _file_ref(ref_path, "artifact")
        add(ref)
        if not ref["exists"]:
            states.append({"code": "PARTIAL",
                           "detail": "引用工件不存在：%s" % ref_path})
    for queue in content.get("queues") or []:
        add(_file_ref(queue["path"], "queue"))
    for plan in authorization.get("plans") or []:
        add(_file_ref(plan["path"], "plan"))
    cards_dir = data_dir / "grow" / rid / "cards"
    if cards_dir.is_dir():
        for card in sorted(cards_dir.glob("*")):
            add(_file_ref(card, "card"))

    source = {
        "path": str(path),
        "exists": True,
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "mtime": datetime.fromtimestamp(
            path.stat().st_mtime).isoformat(timespec="seconds"),
        "schema": _text(data.get("schema")),
        "generated_at": generated_at,
        "data_dir": str(data_dir),
        "generator": "%s v%s" % (GENERATOR, GENERATOR_VERSION),
    }

    for section_name, section in (("内容队列", content), ("执行账本", timeline)):
        if section.get("state") == "unreadable":
            states.append({"code": "SOURCE_UNREADABLE",
                           "detail": section.get("error")
                           or "%s不可读" % section_name})

    overall = _overall_state(False, False, states)

    return {
        "schema": SCHEMA,
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "generated_at": generated_at,
        "read_only": True,
        "state": overall,
        "states": states,
        "boundary": BOUNDARY,
        "object": _object_header(data, explain, rid),
        "source": source,
        "freshness": freshness,
        "goal": goal,
        "strategy": strategy,
        "quota": quota,
        "supply": supply,
        "measurement": measurement,
        "learning": learning,
        "authorization": authorization,
        "content": content,
        "timeline": timeline,
        "replies": replies,
        "journey": journey,
        "signals": signals,
        "layers": layers,
        "incidents": incidents,
        "faults": [f for f in explain.get("faults") or [] if isinstance(f, dict)],
        "checks": [c for c in explain.get("checks") or [] if isinstance(c, dict)],
        "optimization": optimization,
        "artifacts": artifacts,
        "missing": [],
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# HTML 渲染（自包含、无脚本、无外链）
# ---------------------------------------------------------------------------

_CSS = """
:root{--ink:#1b1f24;--muted:#5b6673;--line:#d9dee5;--bg:#f6f7f9;--card:#fff;
--ok:#1f7a4d;--warn:#9a6400;--bad:#a3221b;--accent:#1f4e8c;--mono:#f1f3f6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,
"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
header.top{background:#fff;border-bottom:1px solid var(--line);padding:20px 24px 16px}
.wrap{max-width:1080px;margin:0 auto}
.crumb{margin:0 0 4px;color:var(--muted);font-size:12px;letter-spacing:.04em}
h1{margin:0 0 8px;font-size:24px;word-break:break-all}
h2{font-size:17px;margin:0 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:14px;margin:16px 0 6px;color:var(--muted)}
.badges{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;border:1px solid var(--line);
background:var(--mono);font-size:12px}
.st-ok{background:#e7f4ec;border-color:#b9dfc8;color:var(--ok)}
.st-partial{background:#fdf3df;border-color:#ecd19b;color:var(--warn)}
.st-empty,.st-failed{background:#f8e6e5;border-color:#e8b9b5;color:var(--bad)}
.stale{background:#f8e6e5;border-color:#e8b9b5;color:var(--bad)}
.readonly{margin:6px 0 0;color:var(--muted);font-size:13px}
nav{background:#fff;border-bottom:1px solid var(--line);padding:8px 24px}
nav .wrap{display:flex;flex-wrap:wrap;gap:14px;font-size:13px}
nav a{color:var(--accent);text-decoration:none}
main{padding:18px 24px 40px}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:16px 18px;margin:0 auto 16px;max-width:1080px}
.kv{display:grid;grid-template-columns:180px 1fr;gap:6px 14px;margin:0}
.kv dt{color:var(--muted);font-size:13px}
.kv dd{margin:0;word-break:break-word}
table{width:100%;border-collapse:collapse;font-size:13px;margin:6px 0}
th,td{text-align:left;vertical-align:top;padding:6px 8px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600;background:#fafbfc}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px}
code{background:var(--mono);padding:1px 4px;border-radius:3px;word-break:break-all}
pre{background:var(--mono);border:1px solid var(--line);border-radius:6px;
padding:10px;overflow-x:auto;white-space:pre-wrap;word-break:break-word}
.cmd{position:relative}
.cmd::after{content:"仅供人工复制，本页不执行";display:block;color:var(--muted);
font-size:11px;margin-top:4px}
.unknown{color:var(--muted);font-style:italic}
.empty{color:var(--muted);margin:6px 0}
.block{border-left:4px solid var(--bad);background:#fdf1f0;padding:12px 14px;
border-radius:0 6px 6px 0;margin:0 0 10px}
.block.warn{border-color:#c08a1e;background:#fdf8ec}
.block h3{margin:0 0 4px;color:var(--bad);font-size:14px}
.block.warn h3{color:var(--warn)}
.res{font-weight:600}
.res-indeterminate{color:var(--bad)}
.res-committed{color:var(--ok)}
.res-failed{color:var(--bad)}
.tl{list-style:none;padding:0;margin:0}
.tl>li{border:1px solid var(--line);border-radius:6px;padding:10px 12px;margin:0 0 8px}
.tl>li.indeterminate{border-color:#e8b9b5;background:#fdf6f5}
.tl-head{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}
details{margin:8px 0}
summary{cursor:pointer;color:var(--accent);font-size:13px}
footer{max-width:1080px;margin:0 auto 30px;padding:0 24px;color:var(--muted);
font-size:12.5px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:720px){.grid2{grid-template-columns:1fr}.kv{grid-template-columns:1fr}}
@media print{nav{display:none}section{break-inside:avoid}}
"""


def _h_or(value, fallback="未登记") -> str:
    if value is None or value == "" or value == [] or value == {}:
        return '<span class="unknown">%s</span>' % _h(fallback)
    if isinstance(value, bool):
        return _h("是" if value else "否")
    if isinstance(value, (list, tuple)):
        return _h("、".join(_text(x) for x in value))
    if isinstance(value, dict):
        return _h(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return _h(value)


def _kv_html(pairs, unknown="未登记") -> str:
    rows = []
    for label, value in pairs:
        rows.append("<dt>%s</dt><dd>%s</dd>" % (
            _h(label), _h_or(value, unknown)))
    return '<dl class="kv">%s</dl>' % "".join(rows)


def _table_html(headers, rows, empty="暂无记录") -> str:
    if not rows:
        return '<p class="empty">%s</p>' % _h(empty)
    head = "".join("<th>%s</th>" % _h(h) for h in headers)
    body = []
    for row in rows:
        body.append("<tr>%s</tr>" % "".join("<td>%s</td>" % cell for cell in row))
    return "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (
        head, "".join(body))


def _pre_html(text, empty="未登记") -> str:
    raw = _text(text)
    if not raw:
        return '<p class="empty">%s</p>' % _h(empty)
    return "<pre>%s</pre>" % _h(raw)


def _badge(text, css_class="") -> str:
    return '<span class="badge %s">%s</span>' % (_h(css_class), _h(text))


def _status_badge(code: str) -> str:
    mapping = {"ok": "st-ok", "partial": "st-partial", "empty": "st-empty",
               "failed": "st-failed", "fresh": "st-ok", "stale": "stale",
               "unknown": "st-partial", "unreadable": "st-failed"}
    return _badge("%s %s" % (code, STATE_LABELS.get(code, "")), mapping.get(code, ""))


def _header_html(detail: Dict[str, Any]) -> str:
    obj = detail.get("object") or {}
    fresh = detail.get("freshness") or {}
    states = detail.get("states") or []
    badges = [_status_badge(detail.get("state") or "ok")]
    for entry in states:
        code = _text(entry.get("code"))
        if code in ("OK",):
            continue
        badges.append(_badge(code, "st-partial" if code != "FAILED" else "st-failed"))
    if _text(fresh.get("state")) == "stale":
        badges.append(_badge("STALE", "stale"))
    return """
<header class="top"><div class="wrap">
<p class="crumb">Growovo · 单次运行详情（只读投影）</p>
<h1>%s</h1>
<div class="badges">%s</div>
<dl class="kv">
<dt>canonical 状态</dt><dd>%s / %s</dd>
<dt>运行开关 go</dt><dd>%s</dd>
<dt>当前是否可执行</dt><dd>%s</dd>
<dt>下一动作（人）</dt><dd>%s</dd>
<dt>阻断码</dt><dd>%s</dd>
<dt>最近更新</dt><dd>%s（%s）</dd>
<dt>生成时间 / 生成器</dt><dd>%s · %s v%s</dd>
</dl>
<p class="readonly">%s</p>
</div></header>""" % (
        _h(obj.get("run_id") or "（未登记运行）"),
        "".join(badges),
        _h(obj.get("canonical_status")), _h(obj.get("ux_state")),
        _h("是" if obj.get("go") else "否"),
        _h(_effective_go(detail)),
        _h(obj.get("next_required_action") or "未登记"),
        _h(obj.get("blocked_reason_code") or "无"),
        _h(obj.get("last_updated_at") or "未登记"),
        _h(fresh.get("text") or "新鲜度未知"),
        _h(detail.get("generated_at")), _h(GENERATOR), _h(GENERATOR_VERSION),
        _h(BOUNDARY),
    )


_NAV = """
<nav><div class="wrap">
<a href="#goal">目标与窗口</a>
<a href="#strategy">策略与配额</a>
<a href="#content">内容与授权</a>
<a href="#timeline">动作—回执</a>
<a href="#replies">回复与 DNC</a>
<a href="#journey">目标退出与旅程</a>
<a href="#signals">信号与变更预演</a>
<a href="#layers">L1/L2/L3</a>
<a href="#incidents">异常与恢复</a>
<a href="#optimization">优化建议</a>
<a href="#evidence">证据与来源</a>
<a href="#boundary">边界</a>
</div></nav>"""


def _blocking_html(detail: Dict[str, Any]) -> str:
    incidents = detail.get("incidents") or []
    timeline = detail.get("timeline") or {}
    out = []
    for incident in incidents:
        if incident.get("kind") != "indeterminate_external_action":
            continue
        out.append("""
<section class="block" id="blocking-indeterminate">
<h3>%s · 结果不确定，禁止自动重试</h3>
<dl class="kv">
<dt>事件 ID</dt><dd><code>%s</code></dd>
<dt>状态</dt><dd>%s / %s</dd>
<dt>发生了什么</dt><dd>%s</dd>
<dt>人工恢复路径</dt><dd>%s</dd>
</dl>
<h3>恢复命令（仅供人工复制，本页不执行）</h3>
<pre class="cmd">%s</pre>
</section>""" % (
            _h(incident.get("title") or "外部动作结果不确定"),
            _h(incident.get("event_id")),
            _h(incident.get("severity")), _h(incident.get("status")),
            _h(incident.get("detail")),
            _h(incident.get("recovery")),
            _h(incident.get("next_command")),
        ))
    unresolved = (timeline.get("counts") or {}).get("unresolved_indeterminate")
    if unresolved and not out:
        out.append("""
<section class="block" id="blocking-indeterminate">
<h3>账本中存在未裁决的外部动作</h3>
<p>共 %d 条 indeterminate：先核对平台回执并人工裁决，页面不会重试。</p>
</section>""" % int(unresolved))
    return "".join(out)


def _goal_html(detail: Dict[str, Any]) -> str:
    goal = detail.get("goal") or {}
    progress = goal.get("progress") if isinstance(goal.get("progress"), dict) else {}
    window = goal.get("window") if isinstance(goal.get("window"), dict) else {}
    current = progress.get("current")
    current_text = ("未知（还没有自动读数）" if current is None
                    else "%s" % _text(current))
    prog = progress.get("progress")
    prog_text = "未计算" if prog is None else "%s%%" % _text(prog)
    return """
<section id="goal"><h2>目标与观测窗口</h2>
%s
<h3>口径（证明 / 不证明）</h3>
%s
</section>""" % (
        _kv_html([
            ("目标名称", goal.get("name")),
            ("目标 ID", goal.get("id")),
            ("指标", "%s%s" % (
                _text(goal.get("metric")),
                ("（%s）" % goal.get("metric_label")) if goal.get("metric_label") else "")),
            ("目标值", goal.get("target_text") or goal.get("target")),
            ("目标类型", goal.get("target_kind")),
            ("基线", goal.get("baseline")),
            ("当前读数", current_text),
            ("进度", prog_text),
            ("进度口径说明", progress.get("note")),
            ("观测窗口", "%s → %s（%s 天，已观测 %s 天）" % (
                _text(window.get("start")), _text(window.get("end")),
                _text(window.get("days")), _text(window.get("days_observed")))),
        ]),
        _kv_html([
            ("证明什么", goal.get("proves")),
            ("不证明什么", goal.get("does_not_prove")),
            ("领先指标", "、".join(goal.get("leading") or [])),
            ("反向指标", "、".join(goal.get("counter") or [])),
        ], unknown="未登记"),
    )


def _strategy_html(detail: Dict[str, Any]) -> str:
    strategy = detail.get("strategy") or {}
    primary = strategy.get("primary") or {}
    quota = detail.get("quota") or {}
    supply = detail.get("supply") or {}
    support_rows = [
        [_h(s.get("channel")), _h(s.get("platform")), _h(s.get("why"))]
        for s in strategy.get("support") or []]
    return """
<section id="strategy"><h2>策略、配额与供给</h2>
<h3>主策略</h3>
%s
<h3>支撑策略</h3>
%s
<h3>明确不做</h3>
%s
<h3>配额</h3>
%s
<h3>供给</h3>
%s
</section>""" % (
        _kv_html([
            ("渠道", primary.get("channel")),
            ("平台", primary.get("platform")),
            ("层级", primary.get("tier")),
            ("为什么", primary.get("why")),
            ("动作", "、".join(primary.get("actions") or [])),
            ("驻留天数", primary.get("residency_days")),
            ("策略指纹", strategy.get("fingerprint")),
            ("编译时间", strategy.get("compiled_at")),
        ]),
        _table_html(["渠道", "平台", "理由"], support_rows, "未登记支撑策略"),
        ("<ul>%s</ul>" % "".join("<li>%s</li>" % _h(x)
                                  for x in strategy.get("not_doing") or []))
        if strategy.get("not_doing") else '<p class="empty">未登记</p>',
        _kv_html([
            ("每周动作数", quota.get("actions_per_week")),
            ("初始每周动作数", quota.get("initial_actions_per_week")),
            ("单轮上限", quota.get("max_per_run")),
            ("间隔秒数", quota.get("interval_seconds")),
            ("配额余量", (quota.get("remaining") or {}).get("text")
             if isinstance(quota.get("remaining"), dict) else quota.get("remaining")),
        ]),
        _kv_html([
            ("已备稿", supply.get("staged")),
            ("覆盖天数", supply.get("coverage_days")),
            ("缺口", supply.get("gap")),
            ("说明", supply.get("note")),
        ]),
    )


def _content_html(detail: Dict[str, Any]) -> str:
    content = detail.get("content") or {}
    auth = detail.get("authorization") or {}
    measurement = detail.get("measurement") or {}
    learning = detail.get("learning") or {}
    queues = content.get("queues") or []
    if queues:
        parts = []
        for queue in queues:
            rows = []
            for item in queue.get("items") or []:
                rows.append([
                    "<code>%s</code>" % _h(item.get("id")),
                    _h(item.get("kind")),
                    _h(item.get("scheduled_for")),
                    _h(item.get("staged_by")),
                    _h(item.get("content_score")),
                    _h("；".join(item.get("content_issues") or []) or "—"),
                ])
            parts.append(
                "<h3>队列 <code>%s</code>（%s / %s，%d 条）</h3>%s" % (
                    _h(queue.get("campaign")), _h(queue.get("channel")),
                    _h(queue.get("action")), len(queue.get("items") or []),
                    _table_html(["ID", "类型", "计划日", "备稿人", "内容分",
                                 "问题"], rows)))
            for item in queue.get("items") or []:
                parts.append(
                    "<details><summary>正文 · %s</summary><p>目标：<code>%s</code></p>"
                    "<pre>%s</pre></details>" % (
                        _h(item.get("id")), _h(item.get("target")),
                        _h(item.get("body"))))
        content_html = "".join(parts)
    else:
        content_html = '<p class="empty">暂无内容队列：没有为该运行备稿（不是"已发送"）</p>'
    if content.get("errors"):
        content_html += '<p class="block warn">队列读取失败：%s</p>' % _h(
            "；".join(content.get("errors") or []))

    plans = auth.get("plans") or []
    if plans:
        rows = []
        for plan in plans:
            if plan.get("human_approved") and plan.get("effective"):
                approval_label = "已批准（有效）"
            elif plan.get("human_approved"):
                approval_label = "已批准（当前不可用）"
            else:
                approval_label = "未批准"
            rows.append([
                "<code>%s</code>" % _h(plan.get("plan_id")),
                _h(approval_label),
                _h(plan.get("approved_by") or "—"),
                _h(plan.get("approved_at") or "—"),
                _h("有效" if plan.get("valid") else "无效"),
                _h(plan.get("reason") or "—"),
            ])
        auth_html = _table_html(
            ["计划", "人工批准", "批准人", "批准时间", "指纹校验", "说明"], rows)
    else:
        auth_html = '<p class="empty">%s</p>' % _h(
            auth.get("error") or auth.get("note") or "未登记审批记录")
    if auth.get("fingerprint_mismatch"):
        auth_html += ('<p class="block">执行指纹不一致：计划/队列/账号/工作区在批准后'
                      '被修改——需重新审批，旧批准不覆盖新内容。</p>')
    if auth.get("error"):
        auth_html = '<p class="block warn">%s</p>' % _h(auth.get("error"))

    return """
<section id="content"><h2>内容版本与授权</h2>
%s
<h3>审批记录</h3>
%s
<h3>读数可信度</h3>
%s
<h3>学习与配比</h3>
%s
</section>""" % (
        content_html, auth_html,
        _kv_html([
            ("verification", measurement.get("verification")),
            ("业务事件 campaign", measurement.get("business_campaign")),
            ("校准到期", (measurement.get("calibration") or {}).get("due")),
            ("连续校准天数", (measurement.get("calibration") or {}).get("streak_days")),
            ("业务读数口径",
             "UNVERIFIED：只作参考，不当已证明准确"
             if _text(measurement.get("verification")) == "UNVERIFIED" else None),
        ]),
        _kv_html([
            ("学习日期", learning.get("on")),
            ("样本", learning.get("sample")),
            ("最小样本", learning.get("min_sample")),
            ("建议", learning.get("recommendation")),
        ]),
    )


def _timeline_html(detail: Dict[str, Any]) -> str:
    timeline = detail.get("timeline") or {}
    if timeline.get("error"):
        return """
<section id="timeline"><h2>动作—回执时间线</h2>
<p class="block">%s</p>
<p>来源不可读时不展示计数：不能把读不到误当成"没发生"。</p>
</section>""" % _h(timeline.get("error"))
    rows = timeline.get("rows") or []
    if not rows:
        return """
<section id="timeline"><h2>动作—回执时间线</h2>
<p class="empty">暂无执行回执：没有已记录的外部动作（不是 0 次发送成功的结论）</p>
</section>"""
    counts = timeline.get("counts") or {}
    summary = _table_html(
        ["attempted", "committed", "indeterminate", "failed", "dry-run",
         "with_evidence"],
        [[_h(counts.get("attempted")), _h(counts.get("committed")),
          _h(counts.get("indeterminate")), _h(counts.get("failed")),
          _h(counts.get("dry-run")), _h(counts.get("with_evidence"))]])

    def row_html(row) -> str:
        css = "indeterminate" if row.get("indeterminate") else ""
        detail_bits = []
        if row.get("detail"):
            detail_bits.append("<div>说明：%s</div>" % _h(row.get("detail")))
        if row.get("resolved_by"):
            detail_bits.append(
                "<div>人工裁决：<code>%s</code> · %s · %s</div>" % (
                    _h(row.get("resolved_by")), _h(row.get("resolved_at")),
                    _h(row.get("resolution_note") or "无说明")))
        elif row.get("no_retry"):
            detail_bits.append(
                '<div class="block warn">该结果属于禁止自动重试的终态：'
                '%s。人工核对后再决定下一步。</div>' % _h(row.get("result")))
        return """
<li class="%s">
<div class="tl-head"><code>%s</code>
<strong>%s</strong> · %s
<span class="res res-%s">%s</span></div>
<div>渠道：%s · transport：%s</div>
<div>目标：<code>%s</code></div>
<div>回执 ref：<code>%s</code></div>
%s
<details><summary>回执原文</summary><pre>%s</pre></details>
</li>""" % (
            _h(css), _h(row.get("ts")), _h(row.get("action")),
            _h(row.get("channel")), _h(row.get("result")), _h(row.get("result")),
            _h(row.get("channel") or "—"), _h(row.get("transport") or "—"),
            _h(row.get("target")), _h(row.get("ref") or "—"),
            "".join(detail_bits), _h(row.get("evidence") or ""))

    blocked = [r for r in rows if r.get("indeterminate") and not r.get("resolved_by")]
    resolved = [r for r in rows if r.get("indeterminate") and r.get("resolved_by")]
    rest = [r for r in rows if not r.get("indeterminate")]
    blocked_html = ""
    if blocked:
        blocked_html = ("<h3>需要先处理的不确定回执（置顶，禁止重试）</h3>"
                        '<ul class="tl">%s</ul>' % "".join(
                            row_html(r) for r in blocked))
    rest_html = "".join(row_html(r) for r in rest + resolved)
    return """
<section id="timeline"><h2>动作—回执时间线</h2>
<p>共 %d 条，%s → %s。indeterminate 置顶展示并禁止自动重试。</p>
%s
%s
<h3>逐条回执（按时间）</h3>
%s
</section>""" % (
        len(rows), _h(timeline.get("first_at")), _h(timeline.get("last_at")),
        summary, blocked_html,
        ('<ul class="tl">%s</ul>' % rest_html) if rest_html else
        '<p class="empty">其余回执为空</p>')


def _layers_html(detail: Dict[str, Any]) -> str:
    layers = detail.get("layers") or {}
    l3 = layers.get("L3") if isinstance(layers.get("L3"), dict) else {}

    def layer_row(name, payload, extra_keys=()):
        payload = payload if isinstance(payload, dict) else {}
        state = _text(payload.get("state") or payload.get("available") or "UNKNOWN")
        text = _text(payload.get("text") or payload.get("note"))
        cells = [_h(name), _h(state), _h(text or "未登记")]
        for key in extra_keys:
            cells.append(_h(payload.get(key)))
        return cells

    extra = [k for k in ("activation", "signup", "paid")
             if k in l3]
    headers = ["层", "状态", "说明"] + [k for k in extra]
    rows = [
        layer_row("L1 执行", layers.get("L1")),
        layer_row("L2 注意力", layers.get("L2")),
        layer_row("L3 业务", l3, extra),
        layer_row("归因", layers.get("attribution")),
    ]
    l3_note = ""
    if _text(l3.get("state")) == "UNKNOWN":
        l3_note = ('<p class="block warn">L3 业务读数未接入：这是未知，不是 0。'
                   '页面不会把缺失折叠成零或 PASS。</p>')
    note = _text(layers.get("note"))
    return """
<section id="layers"><h2>L1 / L2 / L3 分层读数</h2>
%s
%s
%s
<p class="empty">口径：L1=我们做了什么，L2=平台给了多少注意力，L3=业务结果。
三层不可互相替代；L3 缺失时整页不给业务结论。</p>
</section>""" % (
        _table_html(headers, rows, "分层读数不可用"),
        l3_note,
        ('<p class="empty">%s</p>' % _h(note)) if note else "",
    )


def _incidents_html(detail: Dict[str, Any]) -> str:
    incidents = detail.get("incidents") or []
    if not incidents:
        return """
<section id="incidents"><h2>异常、裁决与恢复</h2>
<p class="empty">当前没有告警。没有告警不等于没有风险，只表示扫描未发现阻断项。</p>
</section>"""
    parts = []
    for incident in incidents:
        css = "block" if incident.get("severity") == "blocking" else "block warn"
        rows = _kv_html([
            ("事件 ID", incident.get("event_id")),
            ("类型", incident.get("kind")),
            ("严重度", incident.get("severity")),
            ("状态", incident.get("status")),
            ("发现时间", incident.get("detected_at")),
            ("错误码", incident.get("error_code")),
            ("禁止自动重试", "是" if incident.get("blocks_auto_retry") else "否"),
            ("恢复建议", incident.get("recovery")),
        ])
        cmd = _text(incident.get("next_command"))
        parts.append("""
<section class="%s">
<h3>%s</h3>
%s
<p>%s</p>
%s
<details><summary>证据</summary><pre>%s</pre></details>
</section>""" % (
            css, _h(incident.get("title") or incident.get("kind")),
            rows, _h(incident.get("detail") or ""),
            ('<pre class="cmd">%s</pre>' % _h(cmd)) if cmd else "",
            _h(json.dumps(incident.get("evidence") or [], ensure_ascii=False,
                          indent=1, sort_keys=True)),
        ))
    checks = detail.get("checks") or []
    checks_html = ""
    if checks:
        checks_html = "<h3>故障矩阵检查</h3>" + _table_html(
            ["检查", "状态", "说明", "修复"],
            [[_h(c.get("name")), _h(c.get("state")), _h(c.get("detail")),
              _h(c.get("fix"))] for c in checks])
    return """
<section id="incidents"><h2>异常、裁决与恢复</h2>
<p>阻断项优先展示；任何裁决与恢复都需人工执行，页面不代签、不代恢复。</p>
%s
%s
</section>""" % ("".join(parts), checks_html)


def _optimization_html(detail: Dict[str, Any]) -> str:
    opt = detail.get("optimization") or {}
    proposals = opt.get("proposals") or []
    if not proposals:
        body = '<p class="empty">%s</p>' % _h(
            opt.get("note") or "暂无优化建议")
    else:
        cards = []
        for proposal in proposals:
            evidence = proposal.get("evidence")
            evidence_text = (json.dumps(evidence, ensure_ascii=False, indent=1,
                                        sort_keys=True)
                             if evidence else "")
            cards.append("""
<div class="block warn">
<h3>%s · %s</h3>
<p>%s</p>
<dl class="kv">
<dt>建议编号</dt><dd><code>%s</code></dd>
<dt>类型 / 严重度</dt><dd>%s / %s</dd>
<dt>下一步</dt><dd>%s</dd>
<dt>需要人工</dt><dd>是（本页不自动改预算、不自动重放）</dd>
</dl>
<details><summary>证据</summary><pre>%s</pre></details>
</div>""" % (
                _h(proposal.get("severity")), _h(proposal.get("kind")),
                _h(proposal.get("statement")), _h(proposal.get("id")),
                _h(proposal.get("kind")), _h(proposal.get("severity")),
                _h(proposal.get("next_step")), _h(evidence_text)))
        body = "".join(cards)
    return """
<section id="optimization"><h2>优化建议（需人工决定）</h2>
<p class="empty">%s</p>
%s
</section>""" % (_h(opt.get("boundary") or
                  "建议来自已记录回执与业务事件的描述性比较；不证明渠道效果或因果关系，"
                  "不自动改预算、不自动重放，全部需要人工决定。"), body)


def _evidence_html(detail: Dict[str, Any]) -> str:
    artifacts = detail.get("artifacts") or []
    rows = []
    for art in artifacts:
        rows.append([
            "<code>%s</code>" % _h(art.get("path")),
            _h(art.get("kind")),
            _h("存在" if art.get("exists") else "缺失"),
            _h(art.get("bytes")),
            "<code>%s</code>" % _h((art.get("sha256") or "")[:32] or "—"),
            _h(art.get("mtime") or "—"),
        ])
    missing = detail.get("missing") or []
    missing_html = ""
    if missing:
        missing_html = "<h3>缺失工件</h3>" + _table_html(
            ["路径", "类型", "原因"],
            [["<code>%s</code>" % _h(m.get("path")), _h(m.get("kind")),
              _h(m.get("reason"))] for m in missing])
    notes = detail.get("notes") or []
    notes_html = ""
    if notes:
        notes_html = "<h3>生成备注</h3><ul>%s</ul>" % "".join(
            "<li>%s</li>" % _h(n) for n in notes)
    source = detail.get("source") or {}
    return """
<section id="evidence"><h2>证据与来源</h2>
<details open><summary>输入与指纹</summary>
%s
</details>
%s
%s
</section>""" % (
        _kv_html([
            ("运行文件", source.get("path")),
            ("schema", source.get("schema")),
            ("sha256", source.get("sha256")),
            ("字节数", source.get("bytes")),
            ("mtime", source.get("mtime")),
            ("数据目录", source.get("data_dir")),
            ("生成时间", source.get("generated_at")),
            ("生成器", source.get("generator")),
        ]),
        "<h3>引用的工件</h3>" + _table_html(
            ["路径", "类型", "状态", "字节", "sha256", "mtime"], rows),
        missing_html + notes_html,
    )


def _boundary_html(detail: Dict[str, Any]) -> str:
    return """
<section id="boundary"><h2>边界</h2>
<p>%s</p>
<ul>
<li>数字必须带单位、账期与来源；缺一项就显示缺口，不补 0。</li>
<li>外部文本全部转义；页面无脚本、无外部资源、离线可读。</li>
<li>空态不渲染 0、PASS 或"无风险"；未知显示为 UNKNOWN。</li>
<li>命令只展示与复制，不在页面里执行。</li>
</ul>
</section>
<footer>Growovo · %s v%s · 生成于 %s · 只读投影</footer>""" % (
        _h(detail.get("boundary") or BOUNDARY),
        _h(GENERATOR), _h(GENERATOR_VERSION), _h(detail.get("generated_at")))


def _replies_html(detail: Dict[str, Any]) -> str:
    replies = detail.get("replies") or {}
    if replies.get("error"):
        return """
<section id="replies"><h2>回复与 DNC</h2>
<p class="block warn">回复案例读取失败：%s</p>
<p>来源不可读时不展示计数：不能把读不到误当成"没有收到回复"。</p>
</section>""" % _h(replies.get("error"))
    dnc = replies.get("dnc") or {}
    unverified = dnc.get("unverified") or []
    warnings = []
    if dnc.get("failed"):
        warnings.append(
            '<p class="block warn">DNC 写入失败 %s 条：发送门禁必须保持暂停，'
            '人工核对后再决定。</p>' % _h(dnc.get("failed")))
    if unverified:
        items = []
        for entry in unverified:
            if isinstance(entry, dict):
                items.append("<li><code>%s</code> · %s</li>" % (
                    _h(entry.get("target") or entry.get("reply_case_id")),
                    _h(entry.get("reason") or "未说明")))
            else:
                items.append("<li><code>%s</code></li>" % _h(entry))
        warnings.append(
            '<p class="block warn">DNC 待核验 %d 条：核验前不会抑制任何发送。</p>'
            "<ul>%s</ul>" % (len(unverified), "".join(items)))
    conflict_rows = [
        [_h(c.get("at")), "<code>%s</code>" % _h(c.get("reply_case_id")),
         _h(c.get("source_ref")),
         "<code>%s</code>" % _h(_text(c.get("existing_source_hash"))[:32]),
         "<code>%s</code>" % _h(_text(c.get("incoming_source_hash"))[:32])]
        for c in replies.get("conflicts") or [] if isinstance(c, dict)]
    case_rows = []
    for case in replies.get("cases") or []:
        if not isinstance(case, dict):
            continue
        case_rows.append([
            "<code>%s</code>" % _h(case.get("reply_case_id")),
            _h(case.get("received_at")),
            _h(case.get("channel")),
            _h(case.get("target")),
            _h(case.get("classification_candidate")),
            _h(case.get("confidence")),
            _h(case.get("review_state")),
            _h(case.get("human_label") or "—"),
            _h(case.get("dnc_state")),
            _h(case.get("next_required_action") or case.get("next_actor")),
            _h(case.get("raw_excerpt")),
        ])
    batch_rows = [
        [_h(b.get("batch")), _h(b.get("at")), _h(b.get("imported_by")),
         _h(b.get("authorization_ref")), _h(b.get("created")),
         _h(b.get("relinked")), _h(b.get("skipped_duplicate")),
         _h(b.get("conflicts"))]
        for b in replies.get("batches") or [] if isinstance(b, dict)]
    return """
<section id="replies"><h2>回复与 DNC</h2>
<p>%s</p>
%s
<h3>统计</h3>
%s
<h3>导入冲突（旧案例保留，不覆盖）</h3>
%s
<h3>本次关联的回复案例</h3>
%s
<h3>最近导入批次</h3>
%s
<h3>命令（仅供人工复制，本页不执行）</h3>
<pre class="cmd">%s</pre>
</section>""" % (
        _h(replies.get("note") or "回复与 DNC 状态来自本地回复案例库"),
        "".join(warnings),
        _kv_html([
            ("案例总数", replies.get("count")),
            ("已关联本次运行", replies.get("linked_count")),
            ("未关联运行", replies.get("unresolved_count")),
            ("导入冲突", replies.get("conflict_count")),
            ("DNC 待确认", dnc.get("pending")),
            ("DNC 已生效", dnc.get("active")),
            ("DNC 写入失败", dnc.get("failed")),
            ("抑制集核验", dnc.get("error") or "可读"),
            ("队列文件", replies.get("queue_path")),
        ]),
        _table_html(["时间", "案例", "来源", "旧哈希", "新哈希"],
                    conflict_rows, "无冲突"),
        _table_html(["ID", "收到时间", "渠道", "目标", "候选", "置信度",
                     "审核", "改判", "DNC", "下一动作", "摘要"],
                    case_rows, replies.get("note") or "暂无关联回复"),
        _table_html(["批次", "时间", "导入人", "授权引用", "新建",
                     "补关联", "重复", "冲突"], batch_rows, "暂无导入批次"),
        _h("mkt grow reply import <file> --account <id> --channel email "
           "--by <name> --authorization <ref>\n\n"
           "mkt grow reply link <reply_case_id> --run <run_id> "
           "--by <name> --authorization <ref>\n\n"
           "mkt grow reply list --run <run_id>\n\n"
           "mkt grow reply dnc <reply_case_id> --by <name>"),
    )


def _journey_html(detail: Dict[str, Any]) -> str:
    journey = detail.get("journey") or {}
    if journey.get("error"):
        return """
<section id="journey"><h2>目标退出与旅程</h2>
<p class="block warn">旅程存储读取失败：%s</p>
<p>读不到时不展示计数：不能把没有观察写成失败，也不能写成 0。</p>
</section>""" % _h(journey.get("error"))
    counts = journey.get("counts") or {}
    count_rows = [[_h(state), _h(value)]
                  for state, value in sorted(counts.items())]
    contract_rows = []
    for contract in journey.get("contracts") or []:
        if not isinstance(contract, dict):
            continue
        goal = contract.get("goal") or {}
        window = contract.get("observation_window") or {}
        contract_rows.append([
            "<code>%s</code>" % _h(contract.get("contract_id")),
            _h(goal.get("event_type")),
            _h(goal.get("metric")),
            _h(goal.get("target")),
            _h("%s → %s" % (window.get("start") or "未登记",
                            window.get("end") or "未登记")),
            _h(contract.get("exit_count")),
            _h(contract.get("created_at")),
            _h(contract.get("created_by")),
        ])
    projection_rows = []
    for row in journey.get("projections") or []:
        if not isinstance(row, dict):
            continue
        projection_rows.append([
            "<code>%s</code>" % _h(row.get("projection_id")),
            "<code>%s</code>" % _h(row.get("subject")),
            _h("%s / %s" % (row.get("observed"), row.get("target"))),
            _h(row.get("state")),
            _h(row.get("state_reason")),
            _h(row.get("revision")),
            _h(row.get("next_allowed_action") or "—"),
        ])
    unresolved_rows = [
        ["<code>%s</code>" % _h(u.get("contract_id")), _h(u.get("subject")),
         _h(u.get("state")), _h(u.get("reason"))]
        for u in journey.get("unresolved") or [] if isinstance(u, dict)]
    stale_rows = [
        ["<code>%s</code>" % _h(s.get("contract_id")), _h(s.get("window_end")),
         _h(s.get("reason"))]
        for s in journey.get("stale") or [] if isinstance(s, dict)]
    warnings = []
    if journey.get("conflict_count"):
        warnings.append(
            '<p class="block warn">旅程冲突 %s 条：同 ID 不同内容不会覆盖，'
            '旧记录保留，需人工核对。</p>' % _h(journey.get("conflict_count")))
    if journey.get("orphan_projections"):
        warnings.append(
            '<p class="block warn">%s 条投影找不到对应契约：不猜测归属，'
            '按孤立记录展示。</p>' % _h(journey.get("orphan_projections")))
    if journey.get("stop_count"):
        warnings.append(
            '<p class="block">停止记录 %s 条：停止只作用于旅程推进，'
            '不解除 DNC，也不解除发送门禁。</p>' % _h(journey.get("stop_count")))
    return """
<section id="journey"><h2>目标退出与旅程</h2>
<p>%s</p>
%s
<h3>统计</h3>
%s
%s
<h3>目标退出契约</h3>
%s
<h3>对象投影</h3>
%s
<h3>未解析与待核对</h3>
%s
<h3>窗口已结束但没有投影</h3>
%s
<h3>命令（仅供人工复制，本页不执行）</h3>
<pre class="cmd">%s</pre>
<p class="muted">只读投影：契约登记、评估和停止都走本地 CLI；先后顺序不写成因果，
读不到的目标不写成 0 或失败。</p>
</section>""" % (
        _h(journey.get("note") or "目标退出与旅程状态来自本地旅程库"),
        "".join(warnings),
        _kv_html([
            ("契约数", len(journey.get("contracts") or [])),
            ("对象投影", len(journey.get("projections") or [])),
            ("停止记录", journey.get("stop_count")),
            ("冲突", journey.get("conflict_count")),
            ("孤立投影", journey.get("orphan_projections")),
        ]),
        _table_html(["状态", "条数"], count_rows, "暂无投影状态"),
        _table_html(["契约", "目标事件", "指标", "目标值", "观察窗口",
                     "退出事件", "建立时间", "建立人"],
                    contract_rows, "暂无契约"),
        _table_html(["投影", "对象", "已观察 / 目标", "状态", "原因",
                     "revision", "下一允许动作"],
                    projection_rows, "暂无对象投影"),
        _table_html(["契约", "对象", "状态", "原因"], unresolved_rows,
                    "无未解析项"),
        _table_html(["契约", "窗口止", "原因"], stale_rows, "窗口与投影一致"),
        _h("mkt grow goal-exit contract --run <run_id> --goal-type <type> "
           "--target <n> --window-start <date> --window-end <date> "
           "--by <name> --apply\n\n"
           "mkt grow goal-exit evaluate --contract <contract_id> "
           "--subject <subject> --apply\n\n"
           "mkt grow goal-exit view --run <run_id>"),
    )


def _signals_html(detail: Dict[str, Any]) -> str:
    view = detail.get("signals") or {}
    if view.get("error"):
        return """
<section id="signals"><h2>信号与变更预演</h2>
<p class="block warn">信号存储读取失败：%s</p>
<p class="muted">读不到时不展示计数：空与失败分开，缺失不补 0、不补 PASS。</p>
</section>""" % _h(view.get("error"))
    counts = view.get("counts") or {}
    signal_rows = []
    for row in view.get("signals") or []:
        if not isinstance(row, dict):
            continue
        identity = row.get("identity") or {}
        personal = ((row.get("permission") or {}).get("personalization")
                    or {})
        personal_text = "允许" if personal.get("allowed") else "拒绝"
        if personal.get("reason"):
            personal_text = "%s（%s）" % (personal_text, personal.get("reason"))
        reason = row.get("state_reason") or ", ".join(row.get("gaps") or [])
        signal_rows.append([
            "<code>%s</code>" % _h(row.get("signal_id")),
            _h(row.get("source_family")),
            _h(row.get("source_ref")),
            _h(row.get("observed_at")),
            _h(row.get("state")),
            _h(identity.get("state") or "unresolved"),
            _h(", ".join(row.get("allowed_use") or []) or "未声明"),
            _h(personal_text),
            _h(", ".join(row.get("content_claim_refs") or []) or "暂无"),
            _h(reason or "—"),
        ])
    preview_rows = []
    for preview in view.get("previews") or []:
        if not isinstance(preview, dict):
            continue
        check = preview.get("check") or {}
        pcounts = preview.get("counts") or {}
        preview_rows.append([
            "<code>%s</code>" % _h(preview.get("preview_id")),
            "<code>%s</code>" % _h(preview.get("plan_id")),
            _h(preview.get("dry_run")),
            _h(preview.get("external_effect")),
            _h(pcounts.get("add", "未登记")),
            _h(pcounts.get("modify", "未登记")),
            _h(pcounts.get("remove", "未登记")),
            _h(pcounts.get("unchanged", "未登记")),
            _h(preview.get("approval_state") or "none"),
            _h(check.get("state") or "未核对"),
        ])
    delete_rows = [
        ["<code>%s</code>" % _h(item.get("preview_id")),
         _h(item.get("kind")), _h(item.get("ref")), _h(item.get("summary"))]
        for item in view.get("delete_items") or [] if isinstance(item, dict)]
    stale_rows = [
        ["<code>%s</code>" % _h(row.get("signal_id")), _h(row.get("reason"))]
        for row in view.get("stale") or [] if isinstance(row, dict)]
    gap_rows = [
        ["<code>%s</code>" % _h(row.get("signal_id")),
         _h(", ".join(row.get("gaps") or []))]
        for row in view.get("gaps") or [] if isinstance(row, dict)]
    preview_stale_rows = [
        ["<code>%s</code>" % _h(row.get("preview_id")), _h(row.get("reason"))]
        for row in view.get("preview_stale") or [] if isinstance(row, dict)]
    conflict_rows = [
        [_h(row.get("kind")), "<code>%s</code>" % _h(row.get("id")),
         _h(row.get("reason"))]
        for row in view.get("conflicts") or [] if isinstance(row, dict)]
    incident_blocks = []
    commands = []
    for incident in view.get("incidents") or []:
        if not isinstance(incident, dict):
            continue
        row = [_h(incident.get("kind")), _h(incident.get("ref")),
               _h(incident.get("channel")), _h(incident.get("target")),
               _h(incident.get("at"))]
        incident_blocks.append(
            "<tr>%s</tr>" % "".join("<td>%s</td>" % cell for cell in row))
        command = _text(incident.get("next_command"))
        if command and command not in commands:
            commands.append(command)
    incidents_html = (
        "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (
            "".join("<th>%s</th>" % _h(h) for h in
                    ["类型", "引用", "渠道", "目标", "时间"]),
            "".join(incident_blocks))
        if incident_blocks else '<p class="empty">没有未裁决的外部效果</p>')
    warnings = []
    if view.get("conflicts"):
        warnings.append(
            '<p class="block warn">信号冲突 %s 条：同 ID 不同内容不会覆盖，'
            '旧记录保留，需人工核对。</p>' % _h(len(view.get("conflicts")
                                                    or [])))
    if view.get("preview_stale"):
        warnings.append(
            '<p class="block warn">%s 个预演已过期或基准变化：必须重新生成，'
            '旧预演不批准任何变更。</p>' % _h(len(view.get("preview_stale")
                                                or [])))
    if view.get("delete_count"):
        warnings.append(
            '<p class="block warn">预演包含 %s 个删除项：删除项单独列出，'
            '不会折叠，也不会因为预演通过而执行。</p>' % _h(
                view.get("delete_count")))
    return """
<section id="signals"><h2>信号与变更预演</h2>
<p>%s</p>
%s
<h3>统计</h3>
%s
<h3>信号证据</h3>
%s
<h3>变更预演</h3>
%s
<h3>删除项（不折叠）</h3>
%s
<h3>过期信号</h3>
%s
<h3>证据缺口</h3>
%s
<h3>需要重新生成的预演</h3>
%s
<h3>信号冲突</h3>
%s
<h3>未裁决的外部效果</h3>
%s
<h3>命令（仅供人工复制，本页不执行）</h3>
<pre class="cmd">%s</pre>
<p class="muted">只读投影：信号只证明来源在时间上出现过该内容；预演不批准、不执行。
预演是 dry-run，外部影响固定为 none；正式变更仍需计划审批。</p>
</section>""" % (
        _h(view.get("note") or "信号与预演来自本地证据库"),
        "".join(warnings),
        _kv_html([
            ("信号数", counts.get("total")),
            ("可用", counts.get("usable")),
            ("过期", counts.get("stale")),
            ("撤销", counts.get("revoked")),
            ("不完整", counts.get("incomplete")),
            ("身份未决", counts.get("unresolved_identity")),
            ("预演数", view.get("preview_count")),
            ("删除项", view.get("delete_count")),
            ("冲突", len(view.get("conflicts") or [])),
            ("未裁决", len(view.get("incidents") or [])),
        ]),
        _table_html(["信号", "来源族", "来源", "观察时间", "状态", "身份",
                     "允许用途", "个性化", "内容引用", "缺口或原因"],
                    signal_rows, "还没有信号"),
        _table_html(["预演", "计划", "dry_run", "外部影响", "新增", "修改",
                     "删除", "不变", "审批", "复核"],
                    preview_rows, "暂无变更预演"),
        _table_html(["预演", "对象类型", "对象", "摘要"], delete_rows,
                    "没有删除项"),
        _table_html(["信号", "原因"], stale_rows, "没有过期信号"),
        _table_html(["信号", "缺口"], gap_rows, "没有证据缺口"),
        _table_html(["预演", "原因"], preview_stale_rows,
                    "预演基准均未变化"),
        _table_html(["类型", "ID", "原因"], conflict_rows, "没有信号冲突"),
        incidents_html,
        _h("\n\n".join(commands) if commands else (
            "mkt grow signal add <source_ref> --family <family> "
            "--observed-at <iso> --by <name>\n\n"
            "mkt grow preview create --plan <plan_id> --change <op:kind:ref> "
            "--by <name>\n\n"
            "mkt grow preview check <preview_id>")),
    )


def render_html(detail: Dict[str, Any]) -> str:
    """把详情投影渲染成完全自包含的 HTML（无脚本、无外链）。"""
    detail = detail if isinstance(detail, dict) else {}
    state = _text(detail.get("state") or "ok")
    return """<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Growovo 运行详情 · %s</title>
<style>%s</style>
</head><body>
%s
%s
<main>
%s
%s
%s
%s
%s
%s
%s
%s
%s
%s
%s
%s
</main>
%s
</body></html>""" % (
        _h((detail.get("object") or {}).get("run_id") or "未登记"),
        _CSS,
        _header_html(detail),
        _NAV,
        _blocking_html(detail),
        _goal_html(detail),
        _strategy_html(detail),
        _content_html(detail),
        _timeline_html(detail),
        _replies_html(detail),
        _journey_html(detail),
        _signals_html(detail),
        _layers_html(detail),
        _incidents_html(detail),
        _optimization_html(detail),
        _evidence_html(detail),
        _boundary_html(detail),
    )


# ---------------------------------------------------------------------------
# 终端摘要
# ---------------------------------------------------------------------------

def render_summary(detail: Dict[str, Any]) -> str:
    """Markdown 摘要：不带 --html 时给终端看。"""
    detail = detail if isinstance(detail, dict) else {}
    obj = detail.get("object") or {}
    fresh = detail.get("freshness") or {}
    timeline = detail.get("timeline") or {}
    replies = detail.get("replies") or {}
    journey = detail.get("journey") or {}
    journey_counts = journey.get("counts") or {}
    signals = detail.get("signals") or {}
    signal_counts = signals.get("counts") or {}
    dnc = replies.get("dnc") or {}
    layers = detail.get("layers") or {}
    l3 = layers.get("L3") if isinstance(layers.get("L3"), dict) else {}
    opt = detail.get("optimization") or {}
    auth = detail.get("authorization") or {}
    content = detail.get("content") or {}
    counts = timeline.get("counts") or {}
    lines = [
        "# Growovo 运行详情（只读投影）",
        "",
        "- 运行：%s" % (obj.get("run_id") or "未登记"),
        "- 状态：%s / %s%s" % (
            obj.get("canonical_status") or "未登记",
            obj.get("ux_state") or "UNKNOWN",
            ("（阻断码 %s）" % obj.get("blocked_reason_code")
             if obj.get("blocked_reason_code") else "")),
        "- 页面状态：%s" % (detail.get("state") or "ok"),
        "- go（有效）：%s" % _effective_go(detail),
        "- 下一动作：%s" % (obj.get("next_required_action") or "未登记"),
        "- 最近更新：%s（%s）" % (
            obj.get("last_updated_at") or "未登记",
            fresh.get("text") or "新鲜度未知"),
        "",
        "## 内容与授权",
        "- 队列条数：%s" % (content.get("count") if content.get("queues") else "无队列"),
        "- 审批：%s" % (
            "指纹失配，需重新审批" if auth.get("fingerprint_mismatch")
            else ("已批准" if auth.get("human_approved")
                  else (auth.get("note") or "未登记"))),
        "- 读数可信度：%s" % (
            ((detail.get("measurement") or {}).get("verification")
             or "未登记")),
        "",
        "## 动作—回执",
        ("- 账本不可读：%s" % timeline.get("error"))
        if timeline.get("error") else
        ("- 共 %d 条：committed %s / indeterminate %s / failed %s / dry-run %s"
         % (counts.get("attempted") or 0, counts.get("committed"),
            counts.get("indeterminate"), counts.get("failed"),
            counts.get("dry-run")))
        if timeline.get("rows") else "- 暂无回执",
        "",
        "## 回复与 DNC",
        ("- 回复案例读取失败：%s" % replies.get("error"))
        if replies.get("error") else
        ("- 本次关联 %s 条；未关联 %s 条；冲突 %s 条" % (
            replies.get("linked_count"), replies.get("unresolved_count"),
            replies.get("conflict_count"))
         if replies.get("count") or replies.get("unresolved_count")
         or replies.get("conflict_count") else "- 暂无导入回复"),
        "- DNC：待确认 %s / 已生效 %s / 失败 %s%s" % (
            dnc.get("pending"), dnc.get("active"), dnc.get("failed"),
            ("；未核验 %d 条" % len(dnc.get("unverified") or []))
            if dnc.get("unverified") else ""),
        "",
        "## 目标退出与旅程",
        ("- 旅程读取失败：%s" % journey.get("error"))
        if journey.get("error") else
        ("- 契约 %s 个；对象投影 %s 条；状态 %s" % (
            len(journey.get("contracts") or []),
            len(journey.get("projections") or []),
            ("、".join("%s %s" % (k, v)
                      for k, v in sorted(journey_counts.items())) or "无"))
         if journey.get("contracts") else "- 暂无目标退出契约"),
        "- 停止记录 %s / 冲突 %s%s" % (
            journey.get("stop_count"), journey.get("conflict_count"),
            ("；孤立投影 %s 条" % journey.get("orphan_projections"))
            if journey.get("orphan_projections") else ""),
        "",
        "## 信号与变更预演",
        ("- 信号存储读取失败：%s" % signals.get("error"))
        if signals.get("error") else
        ("- 信号 %s 条：可用 %s / 过期 %s / 撤销 %s / 不完整 %s" % (
            signal_counts.get("total"), signal_counts.get("usable"),
            signal_counts.get("stale"), signal_counts.get("revoked"),
            signal_counts.get("incomplete"))
         if signal_counts.get("total") else "- 暂无信号"),
        "- 预演 %s 个；删除项 %s 个；需重新生成 %s 个；未裁决 %s 条" % (
            signals.get("preview_count") or 0,
            signals.get("delete_count") or 0,
            len(signals.get("preview_stale") or []),
            len(signals.get("incidents") or [])),
        "",
        "## 分层读数",
        "- L1：%s" % ((layers.get("L1") or {}).get("state") or "UNKNOWN"),
        "- L2：%s" % ((layers.get("L2") or {}).get("state") or "UNKNOWN"),
        "- L3：%s%s" % (
            l3.get("state") or "UNKNOWN",
            "（未接入：是未知，不是 0）" if l3.get("state") == "UNKNOWN" else ""),
        "",
        "## 建议与告警",
        "- 告警：%d 条" % len(detail.get("incidents") or []),
        "- 优化建议：%d 条（全部需要人工决定）" % len(opt.get("proposals") or []),
        "",
        "## 命令（仅供人工复制，本页不执行）",
        "```",
        obj.get("next_command") or "mkt grow status %s" % (
            obj.get("run_id") or "<id>"),
        "```",
        "",
        "> %s" % (detail.get("boundary") or BOUNDARY),
    ]
    return "\n".join(lines)
