#!/usr/bin/env python3
"""目标退出与个体旅程（G-NEXT-2）：目标契约、退出判定、只读旅程投影。

边界：
- 目标契约只登记「什么算达成」和观察窗口；达成与否由本地授权事件判定，
  缺数据时是 UNKNOWN，不补 0，也不把没观察写成失败；
- 退出（DNC/退订/不感兴趣/退信/抑制）是终态：本模块不提供解除命令；
- 身份只做显式归一（邮箱大小写/Unicode 宽度、identity_map）；相似名称不合并，
  空身份直接拒绝；
- 未决外部动作（indeterminate）只生成事故与人工裁决命令，不自动重试；
- 投影是追加式版本日志：迟到事件产生新版本并 supersedes 旧版本，旧行不改；
- 归因恒为 not_claimed：多触点只记录顺序，不写成因果；
- 本模块零网络、零发送；所有写入默认 dry-run，调用方显式 --apply 才落盘。
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .journey import normalize_identity
from .ledger import Ledger, _append_line, _read_jsonl_fail_loud
from .outcome_layers import (has_authorized_imports, qualification_states,
                             read_product_events)

CONTRACT_SCHEMA = "growovo.goal-exit-contract/1"
PROJECTION_SCHEMA = "growovo.journey-projection/1"
STOP_SCHEMA = "growovo.journey-stop/1"
VIEW_SCHEMA = "growovo.journey-view/1"
CONFLICT_SCHEMA = "growovo.journey-conflict/1"

GOAL_EVENT_TYPES = ("signup", "activation", "paid")
EXIT_KINDS = ("unsubscribe", "not_interested", "dnc", "bounced",
              "suppressed", "manual_stop")
EXIT_PRIORITY = ("dnc", "unsubscribe", "not_interested", "bounced",
                 "suppressed", "manual_stop")
EXIT_LABELS = {
    "dnc": "DNC（不要再联系）",
    "unsubscribe": "退订",
    "not_interested": "明确不感兴趣",
    "bounced": "地址退信",
    "suppressed": "在抑制名单",
    "manual_stop": "人工停止",
}
STATES = ("active", "goal_met", "paused", "expired", "exited", "blocked",
          "unknown")
ATTRIBUTION_STATES = ("direct_receipt", "assisted_observation",
                      "unattributed", "unknown")
STOP_STATES = ("goal_met", "exited", "expired", "blocked")


class GoalExitError(Exception):
    """目标契约/评估/停止的可预期操作错误（CLI 给业务可读提示）。"""


class GoalExitStoreError(GoalExitError):
    """旅程存储损坏：静默跳过会让退出状态和旧投影变成没发生。"""


def _paths(data_dir) -> Dict[str, Path]:
    base = Path(data_dir) / "outcomes" / "journey"
    return {
        "contracts": base / "goal-exit-contracts.jsonl",
        "projections": base / "journey-projections.jsonl",
        "stops": base / "stop-commands.jsonl",
        "conflicts": base / "journey-conflicts.jsonl",
    }


def _now(now=None) -> str:
    if now is None:
        return datetime.now().isoformat(timespec="seconds")
    if isinstance(now, datetime):
        return now.isoformat(timespec="seconds")
    return str(now)


def _text(value) -> str:
    return str(value if value is not None else "").strip()


def _one_line(value, limit: int = 200) -> str:
    text = " ".join(_text(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fingerprint(value) -> str:
    return "sha256:" + _sha(_canonical(value))


def _append(path: Path, rec: Dict[str, Any]) -> None:
    """与账本同一追加纪律：O_APPEND + flock + 单次 write。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    _append_line(path, json.dumps(rec, ensure_ascii=False) + "\n")


def _read(path: Path, kind: str) -> List[Dict[str, Any]]:
    try:
        return _read_jsonl_fail_loud(path, kind=kind)
    except Exception as exc:  # noqa: BLE001 统一转成可展示的存储错误
        raise GoalExitStoreError("%s读取失败：%s" % (kind, exc)) from exc


def _day(value) -> str:
    """取日期部分：接受 date/datetime/ISO 字符串；取不到返回空串。"""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _text(value)
    if not text:
        return ""
    probe = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(probe).date().isoformat()
    except ValueError:
        head = text[:10]
        try:
            return date.fromisoformat(head).isoformat()
        except ValueError:
            return ""


def _norm(value, identity_map=None) -> str:
    return normalize_identity(value, identity_map)


def _identity_basis(raw: str, normalized: str, identity_map=None) -> str:
    if normalized != normalize_identity(raw):
        return "explicit_map"
    if "@" in normalized and not any(ch.isspace() for ch in normalized):
        return "email_exact"
    if ":" in normalized:
        return "platform_id_exact"
    return "exact_label"


def _belongs(campaign: str, run_id: str) -> bool:
    """campaign 归属：等于 run id、以「run id-」开头，或为空（未标注）。"""
    campaign = _text(campaign)
    if not run_id:
        return True
    return campaign == "" or campaign == run_id or campaign.startswith(run_id + "-")


# ---------------------------------------------------------------------------
# 目标契约
# ---------------------------------------------------------------------------

def _exit_event(rec: Dict[str, Any], index: int) -> Dict[str, str]:
    if not isinstance(rec, dict):
        raise GoalExitError("exit_events 第 %d 项不是对象" % index)
    kind = _text(rec.get("kind"))
    if kind not in EXIT_KINDS:
        raise GoalExitError(
            "exit_events 第 %d 项的 kind 只允许 %s，得到 %r"
            % (index, "/".join(EXIT_KINDS), kind))
    source_ref = _text(rec.get("source_ref"))
    if not source_ref:
        raise GoalExitError(
            "exit_events 第 %d 项缺少 source_ref：退出事件必须有出处" % index)
    return {
        "kind": kind,
        "source_ref": source_ref,
        "subject_ref": _text(rec.get("subject_ref")),
        "at": _text(rec.get("at")),
    }


def _contract_payload(contract: Dict[str, Any]) -> Dict[str, Any]:
    goal = contract.get("goal") if isinstance(contract.get("goal"), dict) else {}
    window = (contract.get("observation_window")
              if isinstance(contract.get("observation_window"), dict) else {})
    attr = (contract.get("attribution_window")
            if isinstance(contract.get("attribution_window"), dict) else {})
    exit_events = contract.get("exit_events")
    exit_events = exit_events if isinstance(exit_events, list) else []
    try:
        target = int(goal.get("target") or 1)
    except (TypeError, ValueError):
        target = 1
    try:
        attr_days = int(attr.get("days", 7))
    except (TypeError, ValueError):
        attr_days = 7
    return {
        "run_id": _text(contract.get("run_id")),
        "goal": {
            "event_type": _text(goal.get("event_type")),
            "metric": _text(goal.get("metric")) or _text(goal.get("event_type")),
            "target": target,
            "label": _text(goal.get("label")),
            "qualifier": dict(goal.get("qualifier") or {}),
        },
        "observation_window": {
            "start": _day(window.get("start")) or _text(window.get("start")),
            "end": _day(window.get("end")) or _text(window.get("end")),
        },
        "attribution_window": {"days": attr_days},
        "exit_events": [_exit_event(rec, i)
                        for i, rec in enumerate(exit_events, 1)],
    }


def contract_fingerprint(contract: Dict[str, Any]) -> str:
    """语义指纹：不含创建时间/署名/备注，重复登记同一契约保持幂等。"""
    return _fingerprint(_contract_payload(contract))


def build_contract(*, run_id: str, goal_event: Dict[str, Any],
                   observation_window: Dict[str, Any],
                   exit_events: Optional[List[Dict[str, Any]]] = None,
                   attribution_window: Optional[Dict[str, Any]] = None,
                   created_by: str = "", note: str = "",
                   now=None) -> Dict[str, Any]:
    """校验并组装目标契约；无界窗口、空目标、坏退出事件一律拒绝。"""
    run_id = _text(run_id)
    if not run_id:
        raise GoalExitError("run_id 不能为空：目标契约必须绑定一次运行")
    if not isinstance(goal_event, dict):
        raise GoalExitError("goal_event 必须是对象")
    event_type = _text(goal_event.get("event_type"))
    if event_type not in GOAL_EVENT_TYPES:
        raise GoalExitError(
            "goal_event.event_type 只允许 %s，得到 %r"
            % ("/".join(GOAL_EVENT_TYPES), event_type))
    try:
        target = int(goal_event.get("target") or 1)
    except (TypeError, ValueError) as exc:
        raise GoalExitError(
            "goal_event.target 必须是整数：%r" % goal_event.get("target")
        ) from exc
    if target < 1:
        raise GoalExitError("goal_event.target 至少为 1")
    qualifier = goal_event.get("qualifier") or {}
    if not isinstance(qualifier, dict):
        raise GoalExitError("goal_event.qualifier 必须是对象")
    if not isinstance(observation_window, dict):
        raise GoalExitError("observation_window 必须是 {start,end}")
    start = _day(observation_window.get("start"))
    end = _day(observation_window.get("end"))
    if not start or not end:
        raise GoalExitError(
            "observation_window.start/end 都不能为空：不能用无界历史代替观察窗口")
    if start > end:
        raise GoalExitError("observation_window.start 不能晚于 end")
    attr = attribution_window if isinstance(attribution_window, dict) else {}
    try:
        attr_days = int(attr.get("days", 7))
    except (TypeError, ValueError) as exc:
        raise GoalExitError(
            "attribution_window.days 必须是整数：%r" % attr.get("days")
        ) from exc
    if attr_days < 0:
        raise GoalExitError("attribution_window.days 不能为负")

    contract = {
        "schema": CONTRACT_SCHEMA,
        "run_id": run_id,
        "goal": {
            "event_type": event_type,
            "metric": _text(goal_event.get("metric")) or event_type,
            "target": target,
            "label": _text(goal_event.get("label")),
            "qualifier": dict(qualifier),
        },
        "observation_window": {"start": start, "end": end},
        "attribution_window": {"days": attr_days},
        "exit_events": [_exit_event(rec, i)
                        for i, rec in enumerate(exit_events or [], 1)],
    }
    fingerprint = contract_fingerprint(contract)
    contract.update({
        "contract_id": "gec-" + fingerprint[7:23],
        "fingerprint": fingerprint,
        "created_at": _now(now),
        "created_by": _text(created_by),
        "note": _text(note),
    })
    return _ordered_contract(contract)


def _ordered_contract(contract: Dict[str, Any]) -> Dict[str, Any]:
    payload = _contract_payload(contract)
    return {
        "schema": CONTRACT_SCHEMA,
        "contract_id": _text(contract.get("contract_id")),
        "fingerprint": _text(contract.get("fingerprint")),
        "created_at": _text(contract.get("created_at")),
        "created_by": _text(contract.get("created_by")),
        "note": _text(contract.get("note")),
        "run_id": payload["run_id"],
        "goal": payload["goal"],
        "observation_window": payload["observation_window"],
        "attribution_window": payload["attribution_window"],
        "exit_events": payload["exit_events"],
    }


def load_contracts(data_dir) -> Dict[str, Dict[str, Any]]:
    """contract_id → 契约记录；坏行显式报错，不静默跳过。"""
    contracts: Dict[str, Dict[str, Any]] = {}
    for index, rec in enumerate(
            _read(_paths(data_dir)["contracts"], kind="目标契约"), 1):
        if not isinstance(rec, dict):
            raise GoalExitStoreError("目标契约第 %d 行不是对象" % index)
        cid = _text(rec.get("contract_id"))
        if not cid:
            raise GoalExitStoreError("目标契约第 %d 行缺少 contract_id" % index)
        contracts[cid] = rec
    return contracts


def get_contract(data_dir, contract_id: str) -> Dict[str, Any]:
    cid = _text(contract_id)
    if not cid:
        raise GoalExitError("contract_id 不能为空")
    contract = load_contracts(data_dir).get(cid)
    if contract is None:
        raise GoalExitError("找不到目标契约 %s" % cid)
    return contract


def _conflict(data_dir, *, kind: str, key: str,
              existing: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    _append(_paths(data_dir)["conflicts"], {
        "schema": CONFLICT_SCHEMA,
        "at": _now(),
        "kind": kind,
        "key": key,
        "existing": existing,
        "incoming": incoming,
    })


def write_contract(data_dir, contract: Dict[str, Any], *,
                   dry_run: bool = True) -> Dict[str, Any]:
    """登记契约：同 ID 同内容幂等；同 ID 不同内容进冲突，不覆盖。"""
    cid = _text(contract.get("contract_id"))
    if not cid:
        raise GoalExitError("契约缺少 contract_id")
    fingerprint = contract_fingerprint(contract)
    declared = _text(contract.get("fingerprint"))
    if declared and declared != fingerprint:
        raise GoalExitError(
            "契约指纹与内容不一致：声明 %s，重算 %s" % (declared, fingerprint))
    contract = _ordered_contract(dict(contract, fingerprint=fingerprint))
    existing = load_contracts(data_dir).get(cid)
    if existing is not None:
        existing_fp = contract_fingerprint(existing)
        if (existing_fp == fingerprint
                and _text(existing.get("fingerprint")) in ("", fingerprint)):
            return {"status": "unchanged", "contract_id": cid,
                    "fingerprint": fingerprint,
                    "path": str(_paths(data_dir)["contracts"])}
        _conflict(data_dir, kind="goal_exit_contract", key=cid,
                  existing={"contract_id": cid,
                            "fingerprint": _text(existing.get("fingerprint")),
                            "recomputed": existing_fp},
                  incoming={"contract_id": cid, "fingerprint": fingerprint})
        raise GoalExitError(
            "contract_id %s 已存在且内容不同：进冲突记录，不覆盖旧契约" % cid)

    target = _paths(data_dir)["contracts"]
    if dry_run:
        return {"status": "would_write", "contract_id": cid,
                "fingerprint": fingerprint, "path": str(target)}
    _append(target, contract)
    return {"status": "written", "contract_id": cid,
            "fingerprint": fingerprint, "path": str(target)}


# ---------------------------------------------------------------------------
# 评估
# ---------------------------------------------------------------------------

def _event_view(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "event_id": _text(rec.get("event_id")),
        "event_type": _text(rec.get("event_type")),
        "subject_id": _text(rec.get("subject_id")),
        "occurred_at": _text(rec.get("occurred_at")),
        "campaign_id": _text(rec.get("campaign_id")),
        "source_system": _text(rec.get("source_system")),
        "source_ref": _text(rec.get("source_ref")),
        "verification_state": _text(rec.get("verification_state")),
        "evidence_hash": _text(rec.get("evidence_hash")),
    }


def _partition_events(events: List[Dict[str, Any]], *,
                      goal: Dict[str, Any], subject: str,
                      identity_map, quals: Dict[str, bool],
                      start: str, end: str
                      ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]],
                                 List[Dict[str, Any]], List[Dict[str, Any]],
                                 List[Dict[str, Any]]]:
    """按目标类型/身份/资格/核验/窗口分区；重复 ID 与重复内容只计一次。"""
    event_type = _text(goal.get("event_type"))
    qualifier = goal.get("qualifier") if isinstance(goal.get("qualifier"), dict) else {}
    want_verification = _text(qualifier.get("verification_state"))
    matched: List[Dict[str, Any]] = []
    outside: List[Dict[str, Any]] = []
    undated: List[Dict[str, Any]] = []
    unqualified: List[Dict[str, Any]] = []
    unverified: List[Dict[str, Any]] = []
    seen_ids = set()
    seen_keys = set()

    for rec in events:
        if not isinstance(rec, dict):
            continue
        event_id = _text(rec.get("event_id"))
        if event_id:
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
        if _text(rec.get("event_type")) != event_type:
            continue
        if _norm(rec.get("subject_id"), identity_map) != subject:
            continue
        key = (event_type, subject, _text(rec.get("occurred_at")),
               _text(rec.get("source_ref")))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        view = _event_view(rec)
        if not bool(quals.get(event_id, True)):
            unqualified.append(dict(view, reason="资格判定为不计入"))
            continue
        if want_verification and view["verification_state"] != want_verification:
            unverified.append(dict(
                view, reason="核验状态不是 %s" % want_verification))
            continue
        day = _day(rec.get("occurred_at"))
        if not day:
            undated.append(dict(view, reason="缺少可解析的时间"))
            continue
        if day < start or day > end:
            outside.append(dict(view, reason="发生在观察窗口之外"))
            continue
        matched.append(view)
    return matched, outside, undated, unqualified, unverified


def _reply_cases(data_dir, subject: str, identity_map) -> List[Dict[str, Any]]:
    from . import reply_case
    latest = reply_case.load_latest(data_dir)
    matched = []
    for cid in sorted(latest):
        case = latest[cid]
        if _norm(case.get("target"), identity_map) != subject:
            continue
        matched.append(case)
    return matched


def _ledger_rows(data_dir) -> List[Dict[str, Any]]:
    rows = Ledger(Path(data_dir) / "ledger.jsonl").all()
    return [r for r in rows if isinstance(r, dict)]


def _ledger_ref(rec: Dict[str, Any], index: int) -> str:
    return _text(rec.get("ref")) or "ledger.jsonl#%d" % index


def _planned_actions(data_dir, *, run_id: str, subject: str, identity_map,
                     executed_keys) -> Tuple[List[Dict[str, Any]],
                                             List[str]]:
    planned: List[Dict[str, Any]] = []
    errors: List[str] = []
    queue_dir = Path(data_dir) / "queues"
    if not queue_dir.is_dir():
        return planned, errors
    for path in sorted(queue_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 读不了的队列进错误清单，不当空
            errors.append("%s 读取失败：%s: %s"
                          % (path.name, type(exc).__name__, exc))
            continue
        if not isinstance(data, dict):
            errors.append("%s 不是对象" % path.name)
            continue
        campaign = _text(data.get("campaign"))
        if not _belongs(campaign, run_id):
            continue
        channel = _text(data.get("channel"))
        action = _text(data.get("action"))
        items = data.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            target = _norm(item.get("target"), identity_map)
            if not target or target != subject:
                continue
            if (channel, action, target) in executed_keys:
                continue
            planned.append({
                "queue": path.name,
                "item_id": _text(item.get("id")),
                "kind": _text(item.get("kind")),
                "channel": channel,
                "action": action,
                "target": _text(item.get("target")),
                "scheduled_for": _text(item.get("scheduled_for")),
                "campaign": campaign,
            })
    return planned, errors


def evaluate(data_dir, contract: Dict[str, Any], *, subject_ref: str,
             identity_map=None, now=None) -> Dict[str, Any]:
    """把一个对象放进目标契约里评估；返回确定性的只读投影。"""
    data_dir = Path(data_dir)
    contract = dict(contract or {})
    cid = _text(contract.get("contract_id"))
    if not cid:
        raise GoalExitError("契约缺少 contract_id")
    fingerprint = contract_fingerprint(contract)
    if _text(contract.get("fingerprint")) not in ("", fingerprint):
        raise GoalExitError(
            "契约指纹与内容不一致：声明 %s，重算 %s"
            % (_text(contract.get("fingerprint")), fingerprint))

    raw_subject = _text(subject_ref)
    subject = _norm(raw_subject, identity_map)
    if not subject:
        raise GoalExitError("subject_ref 不能为空：要明确评估哪一个对象")
    basis = _identity_basis(raw_subject, subject, identity_map)

    payload = _contract_payload(contract)
    run_id = payload["run_id"]
    goal = payload["goal"]
    start = payload["observation_window"]["start"]
    end = payload["observation_window"]["end"]
    target = int(goal.get("target") or 1)

    blocked: List[Dict[str, Any]] = []
    incidents: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    errors: Dict[str, str] = {}

    events: List[Dict[str, Any]] = []
    quals: Dict[str, bool] = {}
    authorized: Optional[bool] = None
    try:
        events = read_product_events(data_dir, run_id=run_id)
        quals = qualification_states(data_dir)
        authorized = has_authorized_imports(data_dir, run_id=run_id)
    except Exception as exc:  # noqa: BLE001 读不到就 blocked，不猜
        errors["events"] = "%s: %s" % (type(exc).__name__, exc)
        blocked.append({"kind": "product_events_unreadable",
                        "detail": errors["events"],
                        "next_command": "先修授权事件存储，再重跑评估"})

    matched, outside, undated, unqualified, unverified = _partition_events(
        events, goal=goal, subject=subject, identity_map=identity_map,
        quals=quals, start=start, end=end)
    observed = len(matched)
    goal_hit = observed >= target

    replies: List[Dict[str, Any]] = []
    try:
        replies = _reply_cases(data_dir, subject, identity_map)
    except Exception as exc:  # noqa: BLE001 回复存储坏了可能漏掉 DNC
        errors["replies"] = "%s: %s" % (type(exc).__name__, exc)
        blocked.append({"kind": "reply_store_unreadable",
                        "detail": errors["replies"],
                        "next_command": "先修回复存储，再重跑评估"})

    suppressions = set()
    try:
        from .gates import load_suppressions
        suppressions = set(load_suppressions(data_dir))
    except Exception as exc:  # noqa: BLE001 抑制源读不到时 fail closed
        errors["suppression"] = "%s: %s" % (type(exc).__name__, exc)
        blocked.append({"kind": "suppression_unreadable",
                        "detail": errors["suppression"],
                        "next_command": "先修抑制源；读不到时按阻断处理"})

    ledger_rows: List[Dict[str, Any]] = []
    try:
        ledger_rows = _ledger_rows(data_dir)
    except Exception as exc:  # noqa: BLE001 账本坏了无法判断是否已执行
        errors["ledger"] = "%s: %s" % (type(exc).__name__, exc)
        blocked.append({"kind": "ledger_unreadable",
                        "detail": errors["ledger"],
                        "next_command": "先修账本，再重跑评估"})

    exits: List[Dict[str, Any]] = []
    exit_keys = set()

    def add_exit(kind: str, source_ref: str, at: str = "",
                 detail: str = "") -> None:
        if kind not in EXIT_KINDS:
            return
        key = (kind, source_ref)
        if key in exit_keys:
            return
        exit_keys.add(key)
        exits.append({"kind": kind, "source_ref": source_ref,
                      "at": _text(at), "detail": _one_line(detail)})

    declarations: List[Dict[str, Any]] = []
    for rec in payload["exit_events"]:
        declared_subject = _text(rec.get("subject_ref"))
        linked = bool(declared_subject) and \
            _norm(declared_subject, identity_map) == subject
        declarations.append(dict(rec, subject_linked=linked))
        if linked:
            add_exit(rec["kind"], rec["source_ref"], rec.get("at", ""),
                     "契约登记的退出事件")

    executed: List[Dict[str, Any]] = []
    attempts: List[Dict[str, Any]] = []
    executed_keys = set()
    for index, rec in enumerate(ledger_rows, 1):
        if _norm(rec.get("target"), identity_map) != subject:
            continue
        result = _text(rec.get("result"))
        channel = _text(rec.get("channel"))
        action = _text(rec.get("action"))
        ref = _ledger_ref(rec, index)
        row = {"ref": ref, "ts": _text(rec.get("ts")), "channel": channel,
               "action": action, "result": result,
               "campaign": _text(rec.get("campaign"))}
        if result in ("committed", "indeterminate"):
            executed.append(row)
            executed_keys.add((channel, action, subject))
        elif result in ("dry-run", "failed", "no-dm", "bounced", "suppressed"):
            attempts.append(row)
        if result == "indeterminate":
            incidents.append({
                "kind": "indeterminate_external_action",
                "event_id": ref,
                "detail": "外部动作结果未知；不自动重试，先人工裁决",
                "next_command": ("mkt ledger resolve %s --result <committed|failed>"
                                 " --by <name> --note <依据>" % ref),
            })
        if result == "bounced":
            add_exit("bounced", "ledger:" + ref, _text(rec.get("ts")),
                     "账本记录退信")
        if result == "suppressed":
            add_exit("suppressed", "ledger:" + ref, _text(rec.get("ts")),
                     "账本记录被抑制")

    for case in replies:
        case_id = _text(case.get("reply_case_id"))
        ref = "replies:" + case_id
        at = _text(case.get("received_at")) or _text(case.get("imported_at"))
        label = (_text(case.get("human_label"))
                 or _text(case.get("classification_candidate")))
        dnc_state = _text(case.get("dnc_state"))
        if dnc_state in ("pending", "active"):
            add_exit("dnc", ref, at, "回复案例 DNC 状态：%s" % dnc_state)
        if dnc_state == "failed":
            blocked.append({
                "kind": "dnc_write_failed",
                "detail": "回复案例 %s 的 DNC 写入失败" % case_id,
                "next_command": "mkt grow reply dnc %s --by <name>" % case_id,
            })
        if label == "unsubscribe":
            add_exit("unsubscribe", ref, at, "回复分类：退订")
        elif label == "not_interested":
            add_exit("not_interested", ref, at, "回复分类：不感兴趣")
        elif label in ("interested", "question"):
            unresolved.append({
                "kind": "reply_%s_not_goal" % label,
                "ref": ref,
                "note": "回复表示「%s」，不等于注册/激活/付费" % label,
            })
        if _text(case.get("link_state")) == "unresolved":
            unresolved.append({
                "kind": "unlinked_reply",
                "ref": ref,
                "note": "回复未关联到运行：只登记，不并入目标计数",
            })

    if raw_subject in suppressions or subject in suppressions:
        add_exit("suppressed", "suppression:" + raw_subject,
                 "", "抑制源命中；无法区分 DNC/退信时统一按退出处理")

    planned, queue_errors = _planned_actions(
        data_dir, run_id=run_id, subject=subject, identity_map=identity_map,
        executed_keys=executed_keys)
    for message in queue_errors:
        blocked.append({"kind": "queue_unreadable", "detail": message,
                        "next_command": "先修队列文件，再重跑评估"})

    try:
        from .safety import is_paused
        paused = bool(is_paused(data_dir))
    except Exception as exc:  # noqa: BLE001 暂停状态读不到时按暂停处理
        paused = True
        errors["pause"] = "%s: %s" % (type(exc).__name__, exc)
        blocked.append({"kind": "pause_state_unreadable",
                        "detail": errors["pause"],
                        "next_command": "先修 state 目录，再重跑评估"})

    exits.sort(key=lambda r: (EXIT_PRIORITY.index(r["kind"]),
                              r["at"], r["source_ref"]))
    if exits:
        state = "exited"
        primary = exits[0]
        state_reason = "对象已退出（%s）：不再发新动作" % \
            EXIT_LABELS.get(primary["kind"], primary["kind"])
    elif blocked or incidents:
        state = "blocked"
        state_reason = "存在未决外部动作或读取失败：先人工核对，禁止自动重试"
    elif authorized is False:
        state = "unknown"
        state_reason = "这次运行还没有授权导入的产品事件：结果是未知，不是 0"
    elif goal_hit:
        state = "goal_met"
        state_reason = "观察窗口内看到 %d 个目标事件（目标 %d）" % (
            observed, target)
    elif paused:
        state = "paused"
        state_reason = "工作区处于暂停状态：暂停期间不发新动作"
    elif _day(_now(now)) <= end:
        state = "active"
        state_reason = "观察窗口未结束：按计划继续，结果仍未定"
    else:
        state = "expired"
        state_reason = ("观察窗口结束，未观察到目标事件；这不等同失败，"
                        "也不写因果")

    next_action = {
        "exited": "none: DNC/退出是终态，本模块不提供解除",
        "blocked": "manual_resolve: 先人工核对，禁止自动重试",
        "unknown": "repair_source: 先接上来源或补身份，不补 0",
        "goal_met": "stop_new_actions: 先复现，再谈加量",
        "paused": "wait_unpause: 暂停解除后再评估",
        "active": "continue_planned: 按已批准的计划继续",
        "expired": "close_or_renew: 窗口已结束，不把没观察写成失败",
    }[state]

    verified = [e for e in matched
                if _text(e.get("verification_state")) == "verified"]
    touches = [{"ref": row["ref"], "ts": row["ts"],
                "channel": row["channel"], "action": row["action"]}
               for row in executed]
    if matched and verified:
        attribution_state = "direct_receipt"
    elif matched:
        attribution_state = "assisted_observation"
    else:
        attribution_state = "unknown"
    attribution = {
        "state": attribution_state,
        "causal_credit": "not_claimed",
        "touch_count": len(touches),
        "touch_refs": touches[:20],
        "touches_truncated": max(0, len(touches) - 20),
        "note": ("同一对象有多次触达；先后发生不证明目标事件由哪次触达带来"
                 if len(touches) > 1 else
                 "归因恒为 not_claimed：本模块只记录先后，不写因果"),
    }

    core: Dict[str, Any] = {
        "schema": PROJECTION_SCHEMA,
        "contract_id": cid,
        "contract_fingerprint": fingerprint,
        "run_id": run_id,
        "subject": {"ref": raw_subject, "normalized": subject,
                    "identity_basis": basis},
        "goal": goal,
        "observation_window": payload["observation_window"],
        "attribution_window": payload["attribution_window"],
        "state": state,
        "state_reason": state_reason,
        "goal_result": {
            "event_type": goal["event_type"],
            "metric": goal["metric"],
            "target": target,
            "observed": observed,
            "met": goal_hit,
            "events": matched,
        },
        "events_outside_window": outside,
        "events_undated": undated,
        "events_unqualified": unqualified,
        "events_unverified": unverified,
        "unresolved_evidence": unresolved,
        "exits": exits,
        "exit_declarations": declarations,
        "blocked": {
            "reasons": blocked,
            "incidents": incidents,
            "suppression_error": errors.get("suppression", ""),
            "reply_error": errors.get("replies", ""),
            "ledger_error": errors.get("ledger", ""),
            "event_error": errors.get("events", ""),
        },
        "actions": {
            "planned": planned,
            "executed": executed,
            "attempts_not_sent": attempts,
            "counts": {
                "planned": len(planned),
                "executed": len(executed),
                "attempts_not_sent": len(attempts),
            },
        },
        "attribution": attribution,
        "next_allowed_action": next_action,
    }
    fingerprint_input = _fingerprint(core)
    return dict(core, projection_id="jpr-" + fingerprint_input[7:23],
                input_fingerprint=fingerprint_input,
                evaluated_at=_now(now))


def render_text(projection: Dict[str, Any]) -> str:
    """中文纯文本：命令只展示，不执行；UNKNOWN/EXPIRED 不等于 0/失败。"""
    subject = projection.get("subject") or {}
    goal_result = projection.get("goal_result") or {}
    actions = projection.get("actions") or {}
    counts = actions.get("counts") or {}
    attribution = projection.get("attribution") or {}
    lines = [
        "目标退出与个体旅程（只读投影）",
        "契约：%s（运行 %s）" % (projection.get("contract_id", ""),
                              projection.get("run_id", "")),
        "对象：%s（%s）" % (subject.get("ref", ""),
                          subject.get("identity_basis", "")),
        "状态：%s — %s" % (projection.get("state", ""),
                          projection.get("state_reason", "")),
        "目标：%s ≥ %d，观察窗口 %s ~ %s，已看到 %d 个"
        % (goal_result.get("event_type", ""), goal_result.get("target", 0),
           (projection.get("observation_window") or {}).get("start", ""),
           (projection.get("observation_window") or {}).get("end", ""),
           goal_result.get("observed", 0)),
        "动作：计划 %d，已执行 %d，未发出 %d"
        % (counts.get("planned", 0), counts.get("executed", 0),
           counts.get("attempts_not_sent", 0)),
        "归因：%s；causal_credit=%s"
        % (attribution.get("state", ""), attribution.get("causal_credit", "")),
        "下一步：%s" % projection.get("next_allowed_action", ""),
    ]
    exits = projection.get("exits") or []
    if exits:
        lines.append("退出：")
        for item in exits:
            lines.append("- %s（来源 %s）：%s"
                         % (EXIT_LABELS.get(item.get("kind", ""),
                                           item.get("kind", "")),
                            item.get("source_ref", ""),
                            item.get("detail", "")))
    incidents = (projection.get("blocked") or {}).get("incidents") or []
    if incidents:
        lines.append("未决事故（不自动重试）：")
        for item in incidents:
            lines.append("- %s：%s" % (item.get("event_id", ""),
                                      item.get("next_command", "")))
    unresolved = projection.get("unresolved_evidence") or []
    if unresolved:
        lines.append("未决证据（只计数，不并入目标）：")
        for item in unresolved:
            lines.append("- %s：%s" % (item.get("ref", ""),
                                      item.get("note", "")))
    if projection.get("state") in ("unknown", "expired"):
        lines.append("说明：%s 表示没有观察到，不等于 0 或失败。"
                     % projection.get("state"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 投影与停止记录
# ---------------------------------------------------------------------------

def load_projections(data_dir) -> List[Dict[str, Any]]:
    return _read(_paths(data_dir)["projections"], kind="旅程投影")


def load_stops(data_dir) -> Dict[str, Dict[str, Any]]:
    stops: Dict[str, Dict[str, Any]] = {}
    for index, rec in enumerate(
            _read(_paths(data_dir)["stops"], kind="停止记录"), 1):
        if not isinstance(rec, dict):
            raise GoalExitStoreError("停止记录第 %d 行不是对象" % index)
        stop_id = _text(rec.get("stop_id"))
        if not stop_id:
            raise GoalExitStoreError("停止记录第 %d 行缺少 stop_id" % index)
        stops[stop_id] = rec
    return stops


def _latest_projections(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for rec in rows:
        if not isinstance(rec, dict):
            continue
        cid = _text(rec.get("contract_id"))
        subject = _text((rec.get("subject") or {}).get("normalized"))
        key = (cid, subject)
        previous = latest.get(key)
        if previous is None:
            latest[key] = rec
            continue
        if (int(rec.get("revision") or 0)
                >= int(previous.get("revision") or 0)):
            latest[key] = rec
    return [latest[key] for key in sorted(latest)]


def write_projection(data_dir, projection: Dict[str, Any], *,
                     dry_run: bool = True) -> Dict[str, Any]:
    """追加投影版本：同输入幂等；输入变化则新版本 supersedes 旧版本。"""
    cid = _text(projection.get("contract_id"))
    subject = _text((projection.get("subject") or {}).get("normalized"))
    projection_id = _text(projection.get("projection_id"))
    fingerprint = _text(projection.get("input_fingerprint"))
    if not cid or not subject or not projection_id or not fingerprint:
        raise GoalExitError(
            "投影缺少 contract_id/subject.normalized/projection_id/input_fingerprint")
    rows = load_projections(data_dir)
    previous = None
    for rec in rows:
        if not isinstance(rec, dict):
            continue
        if (_text(rec.get("contract_id")) == cid
                and _text((rec.get("subject") or {}).get("normalized")) == subject):
            if previous is None or int(rec.get("revision") or 0) >= \
                    int(previous.get("revision") or 0):
                previous = rec
    if previous is not None and \
            _text(previous.get("input_fingerprint")) == fingerprint:
        return {"status": "unchanged", "projection_id": projection_id,
                "revision": int(previous.get("revision") or 1),
                "supersedes": _text(previous.get("supersedes"))}
    revision = int(previous.get("revision") or 0) + 1 if previous else 1
    supersedes = _text(previous.get("projection_id")) if previous else ""
    record = dict(projection, revision=revision, supersedes=supersedes,
                  stored_at=_now())
    target = _paths(data_dir)["projections"]
    if dry_run:
        return {"status": "would_write", "projection_id": projection_id,
                "revision": revision, "supersedes": supersedes,
                "path": str(target)}
    _append(target, record)
    return {"status": "written", "projection_id": projection_id,
            "revision": revision, "supersedes": supersedes,
            "path": str(target)}


def write_stop(data_dir, projection: Dict[str, Any], *,
               dry_run: bool = True) -> Dict[str, Any]:
    """终态写停止记录；同 stop_id 幂等，不改旧记录。"""
    state = _text(projection.get("state"))
    projection_id = _text(projection.get("projection_id"))
    if not projection_id:
        raise GoalExitError("投影缺少 projection_id")
    if state not in STOP_STATES:
        return {"status": "skipped", "stop_id": "",
                "reason": "状态 %s 不需要停止记录" % state}
    stop_id = "stop-" + projection_id
    existing = load_stops(data_dir).get(stop_id)
    if existing is not None:
        return {"status": "unchanged", "stop_id": stop_id,
                "reason": _text(existing.get("reason"))}
    record = {
        "schema": STOP_SCHEMA,
        "stop_id": stop_id,
        "created_at": _now(),
        "scope": "new_actions",
        "state": state,
        "reason": _text(projection.get("state_reason")),
        "audit_ref": "projections:" + projection_id,
        "contract_id": _text(projection.get("contract_id")),
        "run_id": _text(projection.get("run_id")),
        "subject": _text((projection.get("subject") or {}).get("ref")),
        "note": "只停止新动作；已发出的外部动作不回滚",
    }
    target = _paths(data_dir)["stops"]
    if dry_run:
        return {"status": "would_write", "stop_id": stop_id,
                "state": state, "path": str(target)}
    _append(target, record)
    return {"status": "written", "stop_id": stop_id, "state": state,
            "path": str(target)}


# ---------------------------------------------------------------------------
# 运行详情页只读视图
# ---------------------------------------------------------------------------

def _contract_summary(contract: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "contract_id": _text(contract.get("contract_id")),
        "fingerprint": _text(contract.get("fingerprint")),
        "run_id": _text(contract.get("run_id")),
        "goal": contract.get("goal") or {},
        "observation_window": contract.get("observation_window") or {},
        "attribution_window": contract.get("attribution_window") or {},
        "exit_count": len(contract.get("exit_events") or []),
        "created_at": _text(contract.get("created_at")),
        "created_by": _text(contract.get("created_by")),
        "note": _text(contract.get("note")),
    }


def run_projection(data_dir, run_id: str, *, limit: int = 20) -> Dict[str, Any]:
    """P0 运行详情页的旅程区块：只读、失败显式、不展示猜测计数。"""
    data_dir = Path(data_dir)
    block: Dict[str, Any] = {
        "schema": VIEW_SCHEMA,
        "state": "empty",
        "error": "",
        "run_id": _text(run_id),
        "contracts": [],
        "projections": [],
        "counts": {},
        "unresolved": [],
        "stale": [],
        "stop_count": 0,
        "conflict_count": 0,
        "orphan_projections": 0,
        "note": "",
        "paths": {key: str(path) for key, path in _paths(data_dir).items()},
    }
    try:
        contracts = load_contracts(data_dir)
        projections = load_projections(data_dir)
        stops = load_stops(data_dir)
        conflicts = _read(_paths(data_dir)["conflicts"], kind="旅程冲突")
    except Exception as exc:  # noqa: BLE001 页面不因存储坏掉而崩
        block.update(state="unreadable",
                     error="%s: %s" % (type(exc).__name__, exc))
        return block

    resolved_run = _text(run_id)
    selected_contracts = [
        c for c in contracts.values()
        if not resolved_run or _text(c.get("run_id")) == resolved_run
    ]
    selected_contracts.sort(key=lambda c: (
        _text(c.get("created_at")), _text(c.get("contract_id"))))
    block["contracts"] = [_contract_summary(c) for c in selected_contracts]

    contract_ids = {_text(c.get("contract_id")) for c in selected_contracts}
    latest = _latest_projections([
        p for p in projections
        if (not resolved_run or _text(p.get("run_id")) == resolved_run)
        and (not contract_ids or _text(p.get("contract_id")) in contract_ids)
    ])
    block["orphan_projections"] = len([
        p for p in projections
        if resolved_run and _text(p.get("run_id")) == resolved_run
        and _text(p.get("contract_id")) not in contract_ids
    ])
    latest.sort(key=lambda p: (_text(p.get("evaluated_at")),
                               _text(p.get("projection_id"))), reverse=True)
    hidden = max(0, len(latest) - limit)
    counts: Dict[str, int] = {}
    for rec in latest:
        state = _text(rec.get("state"))
        counts[state] = counts.get(state, 0) + 1
    block["counts"] = counts
    block["projections"] = [{
        "projection_id": _text(p.get("projection_id")),
        "contract_id": _text(p.get("contract_id")),
        "run_id": _text(p.get("run_id")),
        "subject": _text((p.get("subject") or {}).get("ref")),
        "subject_normalized": _text((p.get("subject") or {}).get("normalized")),
        "identity_basis": _text((p.get("subject") or {}).get("identity_basis")),
        "state": _text(p.get("state")),
        "state_reason": _text(p.get("state_reason")),
        "observed": int((p.get("goal_result") or {}).get("observed") or 0),
        "target": int((p.get("goal_result") or {}).get("target") or 0),
        "revision": int(p.get("revision") or 0),
        "supersedes": _text(p.get("supersedes")),
        "evaluated_at": _text(p.get("evaluated_at")),
        "next_allowed_action": _text(p.get("next_allowed_action")),
        "exit_count": len(p.get("exits") or []),
        "unresolved_count": len(p.get("unresolved_evidence") or []),
    } for p in latest[:limit]]
    block["unresolved"] = [
        {"contract_id": p["contract_id"], "subject": p["subject"],
         "state": p["state"], "reason": p["state_reason"]}
        for p in block["projections"]
        if p["state"] in ("unknown", "blocked") or p["unresolved_count"]
    ]
    today = _day(_now())
    projected_contracts = {_text(p.get("contract_id")) for p in latest}
    block["stale"] = [
        {"contract_id": _text(c.get("contract_id")),
         "run_id": _text(c.get("run_id")),
         "window_end": _text((c.get("observation_window") or {}).get("end")),
         "reason": "观察窗口已结束但还没有投影"}
        for c in selected_contracts
        if _day((c.get("observation_window") or {}).get("end")) < today
        and _text(c.get("contract_id")) not in projected_contracts
    ]
    block["stop_count"] = len([
        s for s in stops.values()
        if not resolved_run or _text(s.get("run_id")) == resolved_run
    ])
    block["conflict_count"] = len(conflicts)
    if hidden:
        block["note"] = "另有 %d 条对象投影未在此页展开" % hidden
    if not selected_contracts:
        block["state"] = "empty"
        block["note"] = "还没有目标契约：用 mkt grow goal-exit contract 登记"
    elif latest:
        block["state"] = "ok"
    else:
        block["state"] = "empty"
        if not block["note"]:
            block["note"] = "已有契约但还没有对象投影：用 mkt grow goal-exit evaluate 评估"
    return block
