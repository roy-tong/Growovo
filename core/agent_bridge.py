"""Agent 与伴随 GUI 之间的 handoff（第三波 G-GUI-SLICE-1）。

只登记“对象 + 入口 + 允许动作 + 回执”，不授予写权限：GUI 仍走各自的
领域命令（review_reply / confirm_dnc / Ledger.resolve / pause / resume），
具名、留痕、写后核验。本模块不发送、不审批、不解除 DNC。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = "growovo.agent-handoff/1"
RECEIPT_SCHEMA = "growovo.agent-receipt/1"
OBJECT_TYPES = ("run", "reply_case", "ledger_ref")
ALLOWED_ACTIONS = ("reply.review", "reply.dnc", "run.pause",
                   "ledger.resolve", "run.resume")
PRESENTATIONS = ("H0", "H1", "H2", "H3")
AWAITING_REVIEW = ("proposed", "pending_review")
TTL_MIN, TTL_MAX = 60, 86400
BOUNDARY = ("handoff 只定位对象和动作，不授予写权限；GUI 写操作仍需具名、"
            "留痕并写后核验；本模块不发送、不审批、不解除 DNC。")
UI_RESOURCE = "ui://growovo/risk-card"

_ROUTE_RE = re.compile(r"^/[A-Za-z0-9_\-./?=&%#:@+~]*$")
_LOCAL_URLS = ("http://127.0.0.1", "http://localhost", "http://[::1]")


class BridgeError(Exception):
    """handoff 校验或存储错误。code 供 GUI / CLI 区分处理。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _now(now=None) -> str:
    if now is None:
        return datetime.now().isoformat(timespec="seconds")
    if isinstance(now, datetime):
        return now.isoformat(timespec="seconds")
    return _text(now)


def _parse_at(value) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _dir(data_dir) -> Path:
    return Path(data_dir) / "handoffs"


def _path(data_dir) -> Path:
    return _dir(data_dir) / "agent_handoffs.jsonl"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _append(data_dir, record: Dict[str, Any]) -> None:
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _read(data_dir) -> List[Dict[str, Any]]:
    path = _path(data_dir)
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    bad: List[int] = []
    with path.open("r", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
        try:
            for number, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    bad.append(number)
                    continue
                if not isinstance(rec, dict):
                    bad.append(number)
                    continue
                out.append(rec)
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    if bad:
        raise BridgeError(
            "STORE_UNREADABLE",
            "handoff 存储无法解析：%s 第 %s 行（拒绝静默跳过坏行）"
            % (path, "、".join(str(n) for n in bad[:5])))
    return out


# ---------------------------------------------------------------------------
# 对象快照：版本与指纹的唯一来源
# ---------------------------------------------------------------------------

def _run_snapshot(data_dir, run_id: str) -> Dict[str, Any]:
    from . import grow_runbook as rb

    path = rb.run_path_for(data_dir, run_id)
    snap: Dict[str, Any] = {
        "exists": False, "object_type": "run", "object_id": run_id,
        "object_version": "", "input_fingerprint": "", "path": str(path),
        "status": "", "ux_state": "", "next_actor": "",
        "next_required_action": "", "detail": {},
    }
    if not path.is_file():
        return snap
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    snap.update(exists=True, object_version="sha256:" + digest[:16],
                input_fingerprint="sha256:" + digest[:16])
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("运行文件不是 JSON 对象")
        snap["status"] = _text(data.get("status"))
        snap["detail"] = {"go": bool(data.get("go")),
                          "schema": _text(data.get("schema"))}
    except (ValueError, UnicodeDecodeError) as exc:
        snap["status"] = "unreadable"
        snap["detail"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
        return snap
    try:
        explain = rb.explain_status(data_dir, path)
        snap["status"] = _text(explain.get("canonical_status")) or snap["status"]
        snap["ux_state"] = _text(explain.get("ux_state"))
        snap["next_actor"] = _text(explain.get("next_actor"))
        snap["next_required_action"] = _text(
            explain.get("next_required_action"))
        snap["detail"].update({
            "next_command": _text(explain.get("next_command")),
            "blocked_reason_code": _text(explain.get("blocked_reason_code")),
            "has_pending_external_effect": bool(
                explain.get("has_pending_external_effect")),
        })
    except Exception as exc:  # noqa: BLE001 解释失败不伪造状态
        snap["detail"]["explain_error"] = "%s: %s" % (type(exc).__name__, exc)
    return snap


def _reply_snapshot(data_dir, reply_case_id: str) -> Dict[str, Any]:
    from . import reply_case as rc

    snap: Dict[str, Any] = {
        "exists": False, "object_type": "reply_case",
        "object_id": reply_case_id, "object_version": "",
        "input_fingerprint": "", "path": "", "status": "", "ux_state": "",
        "next_actor": "", "next_required_action": "", "detail": {},
    }
    try:
        case = rc.get_case(data_dir, reply_case_id)
    except rc.ReplyError:
        return snap
    version = int(case.get("version") or 1)
    snap.update(
        exists=True,
        object_version="v%d" % version,
        input_fingerprint=_text(case.get("raw_text_hash")),
        path=str(_path(data_dir)),
        status=_text(case.get("review_state")),
        ux_state={
            "proposed": "NEEDS_REVIEW",
            "pending_review": "NEEDS_REVIEW",
            "confirmed": "READY",
            "overruled": "READY",
        }.get(_text(case.get("review_state")), "UNKNOWN"),
        next_actor=_text(case.get("next_actor")),
        next_required_action=_text(case.get("next_required_action")),
        detail={
            "run_id": _text(case.get("run_id")),
            "channel": _text(case.get("channel")),
            "target": _text(case.get("target")),
            "classification_candidate": _text(
                case.get("classification_candidate")),
            "human_label": _text(case.get("human_label")),
            "dnc_state": _text(case.get("dnc_state")),
            "dnc_ref": _text(case.get("dnc_ref")),
        })
    return snap


def _ledger_snapshot(data_dir, ref: str) -> Dict[str, Any]:
    from .ledger import Ledger

    snap: Dict[str, Any] = {
        "exists": False, "object_type": "ledger_ref", "object_id": ref,
        "object_version": "", "input_fingerprint": "", "path": "",
        "status": "", "ux_state": "", "next_actor": "",
        "next_required_action": "", "detail": {},
    }
    try:
        rows = Ledger(Path(data_dir) / "ledger.jsonl").all()
    except Exception as exc:  # noqa: BLE001 坏账本按不可读返回
        snap["status"] = "unreadable"
        snap["detail"] = {"error": "%s: %s" % (type(exc).__name__, exc)}
        return snap
    hit = None
    for row in rows:
        if _text(row.get("ref")) == ref:
            hit = row
            break
    if hit is None:
        return snap
    canonical = json.dumps({
        "ref": ref,
        "result": _text(hit.get("result")),
        "resolved_by": _text(hit.get("resolved_by")),
        "resolved_at": _text(hit.get("resolved_at")),
        "ts": _text(hit.get("ts")),
    }, ensure_ascii=False, sort_keys=True)
    status = _text(hit.get("result"))
    snap.update(
        exists=True,
        object_version="sha256:" + _sha(canonical)[:16],
        input_fingerprint="sha256:" + _sha(ref)[:16],
        path=str(Path(data_dir) / "ledger.jsonl"),
        status=status,
        ux_state="NEEDS_REVIEW" if status == "indeterminate" else "READY",
        next_actor="human" if status == "indeterminate" else "",
        next_required_action=("人工裁决：核对平台后台后记回结果" if
                              status == "indeterminate" else ""),
        detail={
            "channel": _text(hit.get("channel")),
            "action": _text(hit.get("action")),
            "target": _text(hit.get("target")),
            "campaign": _text(hit.get("campaign")),
            "resolved_by": _text(hit.get("resolved_by")),
            "resolved_at": _text(hit.get("resolved_at")),
            "ts": _text(hit.get("ts")),
        })
    return snap


def object_snapshot(data_dir, object_type: str, object_id: str) -> Dict[str, Any]:
    """读取对象当前版本与指纹。对象不存在时 exists=False，不抛异常。"""
    kind, oid = _text(object_type), _text(object_id)
    if kind not in OBJECT_TYPES:
        raise BridgeError("INVALID_OBJECT_TYPE",
                          "不支持的对象类型：%s（可选：%s）"
                          % (kind or "（空）", "、".join(OBJECT_TYPES)))
    if not oid:
        raise BridgeError("INVALID_OBJECT_ID", "对象 ID 不能为空")
    if kind == "run":
        return _run_snapshot(data_dir, oid)
    if kind == "reply_case":
        return _reply_snapshot(data_dir, oid)
    return _ledger_snapshot(data_dir, oid)


# ---------------------------------------------------------------------------
# 呈现级别与链接校验
# ---------------------------------------------------------------------------

def presentation_for(host: str = "", *, capabilities=None,
                     companion_url: str = "") -> Dict[str, Any]:
    """按宿主自报能力选 H0–H3。不按品牌名推断，缺省按 H1。

    宿主能力必须由调用方显式声明：
      remote=True            → H0（本机链接不可达，不给链接）
      mcp_apps=True          → H2（内嵌聚焦卡片）
      direct_workbench=True  → H3（用户直接打开完整工作台）
      其余且给了 companion_url → H1；没给 → H0。
    """
    caps = capabilities if isinstance(capabilities, dict) else {}
    if caps.get("remote") is True:
        return {
            "level": "H0", "embedded": False, "ui_resource": "",
            "companion_url": "",
            "fallback": ("远程 Agent 不能返回本机链接。请在本机终端运行 "
                         "`mkt grow serve`，再用同一份摘要里的对象 ID 打开工作台。"),
        }
    if caps.get("direct_workbench") is True:
        return {"level": "H3", "embedded": False, "ui_resource": "",
                "companion_url": _text(companion_url),
                "fallback": ""}
    if caps.get("mcp_apps") is True:
        return {"level": "H2", "embedded": True, "ui_resource": UI_RESOURCE,
                "companion_url": _text(companion_url),
                "fallback": ("内嵌卡片只做摘要与深链；完整队列和批量比较"
                             "仍在伴随工作台。")}
    if _text(companion_url):
        return {"level": "H1", "embedded": False, "ui_resource": "",
                "companion_url": _text(companion_url), "fallback": ""}
    return {
        "level": "H0", "embedded": False, "ui_resource": "",
        "companion_url": "",
        "fallback": "没有可用的伴随 GUI 地址：先运行 `mkt grow serve`。",
    }


def _check_route(route: str) -> str:
    route = _text(route)
    if not route:
        raise BridgeError("INVALID_ROUTE", "entry_route 不能为空")
    if len(route) > 512:
        raise BridgeError("INVALID_ROUTE", "entry_route 过长")
    if not route.startswith("/") or route.startswith("//"):
        raise BridgeError("INVALID_ROUTE", "entry_route 必须以单个 / 开头")
    if ".." in route or "\\" in route or "://" in route:
        raise BridgeError("INVALID_ROUTE", "entry_route 不允许路径穿越或绝对 URL")
    if re.search(r"[\s\x00-\x1f]", route):
        raise BridgeError("INVALID_ROUTE", "entry_route 不允许空白或控制字符")
    if not _ROUTE_RE.match(route):
        raise BridgeError("INVALID_ROUTE", "entry_route 含不支持的字符：%s" % route)
    return route


def _check_companion(url: str) -> str:
    url = _text(url).rstrip("/")
    if not url:
        return ""
    low = url.lower()
    for prefix in _LOCAL_URLS:
        if low.startswith(prefix):
            rest = low[len(prefix):]
            if not rest or rest[0] in (":", "/", "?"):
                return url
    raise BridgeError(
        "INVALID_COMPANION",
        "伴随 GUI 只接受本机地址（http://127.0.0.1 或 http://localhost）；"
        "远程 Agent 请改用 H0 摘要，不返回不可达链接")


def deep_link(companion_url: str, route: str) -> str:
    base = _check_companion(companion_url)
    route = _check_route(route)
    return (base + route) if base else route


def _default_route(object_type: str, object_id: str) -> str:
    if object_type == "run":
        return "/runs/%s" % object_id
    if object_type == "reply_case":
        return "/replies/%s" % object_id
    from urllib.parse import quote
    return "/ledger?ref=%s" % quote(object_id, safe="")


# ---------------------------------------------------------------------------
# handoff 生命周期
# ---------------------------------------------------------------------------

def _new_ref() -> str:
    return "hid_" + secrets.token_hex(8)


def _handoff_state(created: Dict[str, Any], revoked: Optional[Dict[str, Any]],
                   now: Optional[datetime]) -> str:
    if revoked is not None:
        return "revoked"
    expires = _parse_at(created.get("expires_at"))
    if expires is not None and now is not None and now >= expires:
        return "expired"
    return "active"


def create_handoff(data_dir, *, object_type: str, object_id: str,
                   entry_route: str = "", focus_ref: str = "",
                   queue_filter: str = "", queue_cursor: str = "",
                   requested_action: str = "", allowed_actions=None,
                   presentation: str = "", companion_url: str = "",
                   host: str = "", host_session_ref: str = "",
                   ttl_seconds: int = 900, by: str = "agent", now=None,
                   capabilities=None) -> Dict[str, Any]:
    """登记一次 handoff，返回读模型。对象不存在时拒绝（NOT_FOUND）。"""
    kind = _text(object_type)
    oid = _text(object_id)
    if kind not in OBJECT_TYPES:
        raise BridgeError("INVALID_OBJECT_TYPE",
                          "不支持的对象类型：%s（可选：%s）"
                          % (kind or "（空）", "、".join(OBJECT_TYPES)))
    if not oid:
        raise BridgeError("INVALID_OBJECT_ID", "对象 ID 不能为空")
    actor = _text(by)
    if not actor:
        raise BridgeError("BY_REQUIRED", "登记 handoff 必须署名（--by）")
    try:
        ttl = int(ttl_seconds)
    except (TypeError, ValueError):
        raise BridgeError("INVALID_TTL", "ttl 必须是整数秒") from None
    if ttl < TTL_MIN or ttl > TTL_MAX:
        raise BridgeError("INVALID_TTL",
                          "ttl 允许 %d–%d 秒（当前 %d）"
                          % (TTL_MIN, TTL_MAX, ttl))
    allowed = allowed_actions
    if allowed is None:
        allowed = []
    if not isinstance(allowed, (list, tuple)):
        raise BridgeError("INVALID_ACTION", "allowed_actions 必须是列表")
    allowed = [_text(a) for a in allowed if _text(a)]
    unknown = [a for a in allowed if a not in ALLOWED_ACTIONS]
    if unknown:
        raise BridgeError("INVALID_ACTION",
                          "不支持的动作：%s（可选：%s）"
                          % ("、".join(unknown), "、".join(ALLOWED_ACTIONS)))
    requested = _text(requested_action)
    if requested:
        if requested not in ALLOWED_ACTIONS:
            raise BridgeError("INVALID_ACTION",
                              "不支持的动作：%s" % requested)
        if not allowed:
            allowed = [requested]
        if requested not in allowed:
            raise BridgeError("INVALID_ACTION",
                              "requested_action 必须包含在 allowed_actions 内")
    companion = _check_companion(companion_url)
    route = _check_route(entry_route or _default_route(kind, oid))
    pres = presentation_for(host, capabilities=capabilities,
                            companion_url=companion)
    level = _text(presentation) or pres["level"]
    if level not in PRESENTATIONS:
        raise BridgeError("INVALID_PRESENTATION",
                          "呈现级别只允许 %s" % "、".join(PRESENTATIONS))
    snap = object_snapshot(data_dir, kind, oid)
    if not snap.get("exists"):
        raise BridgeError(
            "NOT_FOUND", "没有这个对象：%s %s（先确认 canonical 文件已存在）"
            % (kind, oid))
    at = _now(now)
    at_dt = _parse_at(at) or datetime.now()
    expires_at = (at_dt + timedelta(seconds=ttl)).isoformat(timespec="seconds")
    record = {
        "schema": SCHEMA,
        "event": "created",
        "at": at,
        "handoff_ref": _new_ref(),
        "object_type": kind,
        "object_id": oid,
        "object_version": snap.get("object_version", ""),
        "input_fingerprint": snap.get("input_fingerprint", ""),
        "entry_route": route,
        "focus_ref": _text(focus_ref),
        "queue_filter": _text(queue_filter),
        "queue_cursor": _text(queue_cursor),
        "requested_action": requested,
        "allowed_actions": allowed,
        "presentation": level,
        "ui_resource": UI_RESOURCE if level == "H2" else "",
        "companion_url": companion,
        "host": _text(host),
        "host_session_ref": _text(host_session_ref),
        "ttl_seconds": ttl,
        "expires_at": expires_at,
        "by": actor,
        "write_granted": False,
    }
    _append(data_dir, record)
    return get_handoff(data_dir, record["handoff_ref"], now=now)


def get_handoff(data_dir, handoff_ref: str, *, now=None) -> Dict[str, Any]:
    ref = _text(handoff_ref)
    if not ref:
        raise BridgeError("INVALID_REF", "handoff_ref 不能为空")
    created: Optional[Dict[str, Any]] = None
    revoked: Optional[Dict[str, Any]] = None
    receipts: List[Dict[str, Any]] = []
    for rec in _read(data_dir):
        if _text(rec.get("handoff_ref")) != ref:
            continue
        event = _text(rec.get("event"))
        if event == "created":
            created = rec
        elif event == "revoked":
            revoked = rec
        elif event == "receipt":
            receipts.append(rec)
    if created is None:
        raise BridgeError("NOT_FOUND", "没有这个 handoff：%s" % ref)
    now_dt = _parse_at(_now(now))
    state = _handoff_state(created, revoked, now_dt)
    out = {
        "schema": SCHEMA,
        "handoff_ref": ref,
        "state": state,
        "created_at": _text(created.get("at")),
        "expires_at": _text(created.get("expires_at")),
        "ttl_seconds": created.get("ttl_seconds"),
        "object_type": _text(created.get("object_type")),
        "object_id": _text(created.get("object_id")),
        "object_version": _text(created.get("object_version")),
        "input_fingerprint": _text(created.get("input_fingerprint")),
        "entry_route": _text(created.get("entry_route")),
        "focus_ref": _text(created.get("focus_ref")),
        "queue_filter": _text(created.get("queue_filter")),
        "queue_cursor": _text(created.get("queue_cursor")),
        "requested_action": _text(created.get("requested_action")),
        "allowed_actions": list(created.get("allowed_actions") or []),
        "presentation": _text(created.get("presentation")),
        "ui_resource": _text(created.get("ui_resource")),
        "companion_url": _text(created.get("companion_url")),
        "host": _text(created.get("host")),
        "host_session_ref": _text(created.get("host_session_ref")),
        "by": _text(created.get("by")),
        "expired": state == "expired",
        "revoked": ({"at": _text(revoked.get("at")),
                     "by": _text(revoked.get("by")),
                     "reason": _text(revoked.get("reason"))}
                    if revoked is not None else None),
        "receipts": receipts,
        "write_granted": False,
        "boundary": BOUNDARY,
    }
    return out


def revoke_handoff(data_dir, handoff_ref: str, *, by: str,
                   reason: str = "", now=None) -> Dict[str, Any]:
    actor = _text(by)
    if not actor:
        raise BridgeError("BY_REQUIRED", "撤销 handoff 必须署名（--by）")
    current = get_handoff(data_dir, handoff_ref, now=now)
    if current["state"] == "revoked":
        raise BridgeError("REVOKED", "这个 handoff 已撤销：%s"
                          % current["handoff_ref"])
    _append(data_dir, {
        "schema": SCHEMA,
        "event": "revoked",
        "at": _now(now),
        "handoff_ref": current["handoff_ref"],
        "by": actor,
        "reason": _text(reason),
    })
    return get_handoff(data_dir, current["handoff_ref"], now=now)


def list_handoffs(data_dir, *, now=None) -> List[Dict[str, Any]]:
    """按登记顺序返回每个 handoff 的读模型。"""
    created: Dict[str, Dict[str, Any]] = {}
    revoked: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for rec in _read(data_dir):
        ref = _text(rec.get("handoff_ref"))
        if not ref:
            continue
        event = _text(rec.get("event"))
        if event == "created":
            if ref not in created:
                order.append(ref)
            created[ref] = rec
        elif event == "revoked":
            revoked[ref] = rec
    now_dt = _parse_at(_now(now))
    out = []
    for ref in order:
        base = created[ref]
        out.append({
            "handoff_ref": ref,
            "state": _handoff_state(base, revoked.get(ref), now_dt),
            "object_type": _text(base.get("object_type")),
            "object_id": _text(base.get("object_id")),
            "entry_route": _text(base.get("entry_route")),
            "requested_action": _text(base.get("requested_action")),
            "presentation": _text(base.get("presentation")),
            "created_at": _text(base.get("at")),
            "expires_at": _text(base.get("expires_at")),
            "by": _text(base.get("by")),
        })
    return out


# ---------------------------------------------------------------------------
# 回执与幂等
# ---------------------------------------------------------------------------

def record_receipt(data_dir, handoff_ref: str, *, action: str, by: str,
                   reason: str, result: str, new_version: str,
                   audit_ref: str = "", idempotency_key: str = "",
                   request_hash: str = "", response=None,
                   now=None) -> Dict[str, Any]:
    """GUI 写后核验通过才登记回执。同一幂等键的不同请求会被拒绝。"""
    current = get_handoff(data_dir, handoff_ref)
    act = _text(action)
    if act not in ALLOWED_ACTIONS:
        raise BridgeError("INVALID_ACTION", "不支持的动作：%s" % act)
    actor = _text(by)
    if not actor:
        raise BridgeError("BY_REQUIRED", "回执必须署名（--by）")
    outcome = _text(result)
    if not outcome:
        raise BridgeError("INVALID_RESULT", "回执必须写结果")
    version = _text(new_version)
    if not version:
        raise BridgeError("INVALID_VERSION", "回执必须写写后版本")
    key = _text(idempotency_key)
    if key:
        hit = find_receipt(data_dir, key, _text(request_hash))
        if hit is not None:
            raise BridgeError(
                "DUPLICATE_RECEIPT",
                "幂等键已登记过：%s（重复写会被拒绝，请直接读旧回执）" % key)
    record = {
        "schema": RECEIPT_SCHEMA,
        "event": "receipt",
        "at": _now(now),
        "handoff_ref": current["handoff_ref"],
        "action": act,
        "by": actor,
        "reason": _text(reason),
        "result": outcome,
        "new_version": version,
        "audit_ref": _text(audit_ref),
        "idempotency_key": key,
        "request_hash": _text(request_hash),
        "response": response if isinstance(response, dict) else {},
    }
    _append(data_dir, record)
    return record


def find_receipt(data_dir, idempotency_key: str,
                 request_hash: str = "") -> Optional[Dict[str, Any]]:
    key = _text(idempotency_key)
    if not key:
        return None
    wanted = _text(request_hash)
    for rec in _read(data_dir):
        if _text(rec.get("event")) != "receipt":
            continue
        if _text(rec.get("idempotency_key")) != key:
            continue
        seen = _text(rec.get("request_hash"))
        if wanted and seen and seen != wanted:
            raise BridgeError(
                "IDEMPOTENCY_CONFLICT",
                "同一个幂等键对应了不同请求：拒绝重放，请刷新对象后重试")
        return rec
    return None


def resolve_handoff(data_dir, handoff_ref: str, *, now=None) -> Dict[str, Any]:
    """PRD §8.4 回链组：Agent 重新读取最新状态用。只读，不授予写权限。"""
    current = get_handoff(data_dir, handoff_ref, now=now)
    snap = object_snapshot(data_dir, current["object_type"],
                           current["object_id"])
    previous = current["object_version"]
    archive = snap.get("object_version", "") if snap.get("exists") else ""
    receipts = [{
        "at": _text(r.get("at")),
        "action": _text(r.get("action")),
        "by": _text(r.get("by")),
        "result": _text(r.get("result")),
        "new_version": _text(r.get("new_version")),
        "audit_ref": _text(r.get("audit_ref")),
        "idempotency_key": _text(r.get("idempotency_key")),
    } for r in current["receipts"]]
    return {
        "schema": "growovo.agent-handoff-resolution/1",
        "handoff_ref": current["handoff_ref"],
        "object_type": current["object_type"],
        "object_id": current["object_id"],
        "handoff_state": current["state"],
        "presentation": current["presentation"],
        "entry_route": current["entry_route"],
        "companion_url": current["companion_url"],
        "previous_version": previous,
        "current_version": archive,
        "changed": bool(archive) and archive != previous,
        "object_exists": bool(snap.get("exists")),
        "canonical_status": _text(snap.get("status")),
        "ux_status": _text(snap.get("ux_state")),
        "next_actor": _text(snap.get("next_actor")),
        "next_required_action": _text(snap.get("next_required_action")),
        "stale": current["state"] == "expired",
        "action_receipts": receipts,
        "latest_projection_ref": current["entry_route"],
        "read_only": True,
        "write_granted": False,
        "boundary": BOUNDARY,
    }


# ---------------------------------------------------------------------------
# 只读风险摘要（Agent 主入口的结构化摘要）
# ---------------------------------------------------------------------------

def _run_risk_block(data_dir, path: Path, run_id: str) -> Optional[Dict[str, Any]]:
    from . import run_detail

    rid = _text(run_id)
    try:
        detail = run_detail.build_run_detail(
            data_dir, rid, run_path=path, now=None)
    except Exception as exc:  # noqa: BLE001 单个 run 坏掉不拖垮摘要
        return {
            "run_id": rid or path.stem,
            "path": str(path),
            "object_version": "",
            "canonical_status": "unreadable",
            "ux_state": "UNKNOWN",
            "go": False,
            "next_actor": "human",
            "next_required_action": "运行详情读取失败：先修文件或存储，再重试",
            "next_command": "",
            "freshness": {"state": "unknown", "text": ""},
            "replies": {"state": "unknown", "error": str(exc)},
            "incidents": {"blocking": 1, "indeterminate": [], "items": []},
            "authorization": {"fingerprint_mismatch": False},
            "risks": [{"code": "RUN_UNREADABLE", "severity": "high",
                       "detail": "%s: %s" % (type(exc).__name__, exc),
                       "event_id": "", "next_command": ""}],
            "routes": {"run": "/runs/%s" % rid} if rid else {},
        }
    if detail.get("state") == "empty":
        return None
    obj = detail.get("object") or {}
    source = detail.get("source") or {}
    freshness = detail.get("freshness") or {}
    replies = detail.get("replies") or {}
    incidents = [i for i in detail.get("incidents") or []
                 if isinstance(i, dict)]
    blocking = [i for i in incidents
                if i.get("severity") == "blocking" and not i.get("resolved")]
    indeterminate = [i for i in blocking
                     if i.get("kind") == "indeterminate_external_action"]
    authorization = detail.get("authorization") or {}
    dnc = replies.get("dnc") or {}
    sha = _text(source.get("sha256"))
    version = ("sha256:" + sha[:16]) if sha else ""
    status = _text(obj.get("canonical_status"))
    ux = _text(obj.get("ux_state"))
    risks: List[Dict[str, Any]] = []

    def add(code: str, severity: str, text: str, *, event_id: str = "",
            next_command: str = "") -> None:
        risks.append({"code": code, "severity": severity, "detail": text,
                      "event_id": event_id,
                      "next_command": next_command})

    if detail.get("state") == "failed":
        add("RUN_UNREADABLE", "high", "运行文件无法解析：先修文件再操作")
    if replies.get("state") == "unreadable":
        add("REPLIES_UNREADABLE", "high",
            "回复存储不可读：页面显示未知，不显示 0；先修存储：%s"
            % _text(replies.get("error")))
    if status in ("paused", "needs_attention", "cancelled"):
        add("RUN_BLOCKED", "high",
            "运行处于 %s：需人工处理后才能继续" % (status or "未知状态"))
    for inc in blocking[:3]:
        add("INCIDENT_BLOCKING", "high",
            "%s：%s" % (_text(inc.get("title")) or _text(inc.get("code")),
                        _text(inc.get("detail"))),
            event_id=_text(inc.get("event_id")),
            next_command=_text(inc.get("next_command")))
    if dnc.get("failed"):
        add("DNC_FAILED", "high",
            "DNC 写入失败 %s 条：发送门禁必须保持暂停" % dnc.get("failed"))
    unverified = dnc.get("unverified") or []
    if unverified:
        add("DNC_UNVERIFIED", "high",
            "DNC 待核验 %d 条：目标不在当前抑制集，确认前不算已抑制"
            % len(unverified))
    if dnc.get("pending"):
        add("DNC_PENDING", "medium",
            "DNC 待确认 %s 条：需人工确认后写抑制" % dnc.get("pending"))
    if replies.get("conflict_count"):
        add("REPLY_CONFLICT", "medium",
            "回复导入冲突 %s 条：旧案例保留，需人工核对"
            % replies.get("conflict_count"))
    if authorization.get("fingerprint_mismatch"):
        add("AUTH_FINGERPRINT_MISMATCH", "high",
            "审批指纹与当前计划不一致：需要重新确认授权")
    if _text(freshness.get("state")) == "stale":
        add("STALE", "low",
            _text(freshness.get("text")) or "运行数据已过期：先刷新再判断")
    risks.sort(key=lambda r: {"high": 0, "medium": 1, "low": 2}.get(
        r["severity"], 3))
    return {
        "run_id": _text(obj.get("run_id")) or rid or path.stem,
        "path": str(path),
        "object_version": version,
        "canonical_status": status,
        "ux_state": ux,
        "go": bool(obj.get("go")),
        "next_actor": _text(obj.get("next_actor")) or "unknown",
        "next_required_action": _text(obj.get("next_required_action")),
        "next_command": _text(obj.get("next_command")),
        "freshness": {"state": _text(freshness.get("state")),
                      "text": _text(freshness.get("text")),
                      "age_minutes": freshness.get("age_minutes")},
        "replies": {
            "state": _text(replies.get("state")) or "unknown",
            "error": _text(replies.get("error")),
            "count": replies.get("count") or 0,
            "unresolved_count": replies.get("unresolved_count") or 0,
            "pending_review": len([
                c for c in replies.get("cases") or []
                if _text(c.get("review_state")) in AWAITING_REVIEW]),
            "conflict_count": replies.get("conflict_count") or 0,
            "dnc": {"pending": dnc.get("pending") or 0,
                    "active": dnc.get("active") or 0,
                    "failed": dnc.get("failed") or 0,
                    "unverified": len(unverified),
                    "error": _text(dnc.get("error"))},
        },
        "incidents": {
            "blocking": len(blocking),
            "indeterminate": [{
                "event_id": _text(i.get("event_id")),
                "kind": _text(i.get("kind")),
                "title": _text(i.get("title")),
                "detail": _text(i.get("detail")),
                "next_command": _text(i.get("next_command")),
            } for i in indeterminate],
            "items": [{
                "event_id": _text(i.get("event_id")),
                "kind": _text(i.get("kind")),
                "severity": _text(i.get("severity")),
                "title": _text(i.get("title")),
                "detail": _text(i.get("detail")),
                "next_command": _text(i.get("next_command")),
            } for i in blocking[:5]],
        },
        "authorization": {
            "fingerprint_mismatch": bool(
                authorization.get("fingerprint_mismatch")),
            "human_approved": bool(authorization.get("human_approved")),
            "note": _text(authorization.get("note")),
        },
        "risks": risks,
        "routes": {
            "run": "/runs/%s" % (_text(obj.get("run_id")) or rid or path.stem),
            "replies": "/runs/%s/replies" % (
                _text(obj.get("run_id")) or rid or path.stem),
            "incidents": "/runs/%s/incidents" % (
                _text(obj.get("run_id")) or rid or path.stem),
        },
    }


def _suggest_action(block: Dict[str, Any]) -> Dict[str, Any]:
    replies = block.get("replies") or {}
    dnc = replies.get("dnc") or {}
    codes = {r["code"] for r in block.get("risks") or []}
    if dnc.get("failed") or dnc.get("unverified") or dnc.get("pending"):
        return {"requested_action": "reply.dnc",
                "allowed_actions": ["reply.dnc", "reply.review"],
                "focus_ref": "",
                "reason": "先处理 DNC 待办：确认后才会抑制"}
    if "INCIDENT_BLOCKING" in codes:
        incident = (block.get("incidents") or {}).get("items") or [{}]
        first = incident[0]
        if first.get("kind") == "indeterminate_external_action":
            return {"requested_action": "ledger.resolve",
                    "allowed_actions": ["ledger.resolve", "run.resume"],
                    "focus_ref": _text(first.get("event_id")),
                    "reason": "先核对平台回执并裁决，再恢复运行"}
        return {"requested_action": "run.resume",
                "allowed_actions": ["run.resume", "run.pause"],
                "focus_ref": _text(first.get("event_id")),
                "reason": "先处理阻塞告警，再恢复运行"}
    if "RUN_BLOCKED" in codes:
        return {"requested_action": "run.resume",
                "allowed_actions": ["run.resume", "run.pause"],
                "focus_ref": "",
                "reason": "运行处于暂停/待处理：人工确认后恢复"}
    return {"requested_action": "reply.review",
            "allowed_actions": ["reply.review", "reply.dnc"],
            "focus_ref": "",
            "reason": "审阅回复并确认分类"}


def risk_summary(data_dir, *, run_id: str = "", limit: int = 10, now=None,
                 host: str = "", companion_url: str = "",
                 capabilities=None) -> Dict[str, Any]:
    """Agent 主入口的只读摘要：运行、风险、下一步、深链与 handoff 建议。"""
    from . import grow_runbook as rb

    data_dir = Path(data_dir)
    base = data_dir / "grow"
    wanted = _text(run_id)
    paths: List[Path] = []
    if wanted:
        path = rb.run_path_for(data_dir, wanted)
        if not path.is_file():
            raise BridgeError("NOT_FOUND", "没有这个运行：%s" % path)
        paths = [path]
    elif base.is_dir():
        candidates = []
        for path in base.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict) and _text(raw.get("schema")).startswith(
                    "growovo.grow-run"):
                candidates.append(path)
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        paths = candidates[:max(1, int(limit))]
    blocks: List[Dict[str, Any]] = []
    for path in paths:
        block = _run_risk_block(data_dir, path, path.stem)
        if block is not None:
            blocks.append(block)
    blocks.sort(key=lambda b: (
        -sum(1 for r in b["risks"] if r["severity"] == "high"),
        -len(b["risks"]),
        b["run_id"]))
    companion = _check_companion(companion_url)
    pres = presentation_for(host, capabilities=capabilities,
                            companion_url=companion)
    focus = blocks[0] if blocks else None
    suggestion = None
    if focus is not None:
        pick = _suggest_action(focus)
        suggestion = {
            "object_type": "run",
            "object_id": focus["run_id"],
            "entry_route": focus["routes"]["run"],
            "focus_ref": pick["focus_ref"],
            "requested_action": pick["requested_action"],
            "allowed_actions": pick["allowed_actions"],
            "reason": pick["reason"],
            "presentation": pres["level"],
            "companion_url": companion,
            "deep_link": deep_link(companion, focus["routes"]["run"])
            if companion else focus["routes"]["run"],
        }
        for key in ("run", "replies", "incidents"):
            route = focus["routes"][key]
            focus["routes"][key + "_link"] = (
                deep_link(companion, route) if companion else route)
    totals = {
        "runs": len(blocks),
        "risks": sum(len(b["risks"]) for b in blocks),
        "high": sum(1 for b in blocks for r in b["risks"]
                    if r["severity"] == "high"),
        "medium": sum(1 for b in blocks for r in b["risks"]
                      if r["severity"] == "medium"),
        "low": sum(1 for b in blocks for r in b["risks"]
                   if r["severity"] == "low"),
        "replies_pending": sum(
            (b["replies"] or {}).get("pending_review", 0) for b in blocks),
        "dnc_failed": sum((b["replies"] or {}).get("dnc", {}).get("failed", 0)
                          for b in blocks),
        "dnc_unverified": sum(
            (b["replies"] or {}).get("dnc", {}).get("unverified", 0)
            for b in blocks),
        "blocking_incidents": sum(
            (b["incidents"] or {}).get("blocking", 0) for b in blocks),
    }
    return {
        "schema": "growovo.agent-risk-summary/1",
        "generated_at": _now(now),
        "read_only": True,
        "boundary": BOUNDARY,
        "presentation": pres,
        "runs": blocks,
        "totals": totals,
        "next_actor": focus["next_actor"] if focus else "none",
        "next_required_action": (focus["next_required_action"] if focus
                                 else "没有待处理的运行"),
        "handoff_suggestion": suggestion,
        "notes": [] if blocks else [
            "没有找到运行：先用 mkt grow 创建并落盘运行文件"],
    }


def render_summary(summary: Dict[str, Any]) -> str:
    """CLI / Agent 文本摘要（plain language，不含命令执行）。"""
    totals = summary.get("totals") or {}
    lines = [
        "Growovo 风险摘要（只读） 生成于 %s" % summary.get("generated_at", ""),
        "运行 %d 个；风险：高 %d、中 %d、低 %d。"
        % (totals.get("runs", 0), totals.get("high", 0),
           totals.get("medium", 0), totals.get("low", 0)),
    ]
    for block in summary.get("runs") or []:
        lines.append("")
        lines.append("· %s [%s / %s] %s"
                     % (block.get("run_id", ""),
                        block.get("canonical_status", "") or "未知",
                        block.get("ux_state", "") or "未知",
                        (block.get("freshness") or {}).get("text", "")))
        replies = block.get("replies") or {}
        dnc = replies.get("dnc") or {}
        lines.append(
            "  回复 %s 条（待审 %s；DNC 待确认 %s、失败 %s、待核验 %s）"
            % (replies.get("count", 0), replies.get("pending_review", 0),
               dnc.get("pending", 0), dnc.get("failed", 0),
               dnc.get("unverified", 0)))
        if block.get("next_required_action"):
            lines.append("  下一步：%s" % block["next_required_action"])
        for risk in block.get("risks") or []:
            lines.append("  风险 %s（%s）：%s"
                         % (risk.get("code", ""), risk.get("severity", ""),
                            risk.get("detail", "")))
            if risk.get("event_id"):
                lines.append("    事件：%s" % risk["event_id"])
        routes = block.get("routes") or {}
        if routes.get("run_link"):
            lines.append("  深链：%s" % routes["run_link"])
    suggestion = summary.get("handoff_suggestion")
    if suggestion:
        lines.append("")
        lines.append("建议唤起的伴随 GUI：%s（动作 %s；%s）"
                     % (suggestion.get("deep_link", ""),
                        suggestion.get("requested_action", ""),
                        suggestion.get("reason", "")))
    pres = summary.get("presentation") or {}
    if pres.get("fallback"):
        lines.append("呈现：%s。%s" % (pres.get("level", ""),
                                      pres.get("fallback", "")))
    lines.append("")
    lines.append("边界：%s" % summary.get("boundary", BOUNDARY))
    return "\n".join(lines)


def render_handoff(handoff: Dict[str, Any]) -> str:
    """CLI 展示 handoff 读模型。"""
    lines = [
        "handoff %s（%s）" % (handoff.get("handoff_ref", ""),
                              handoff.get("state", "")),
        "对象：%s %s @ %s" % (handoff.get("object_type", ""),
                              handoff.get("object_id", ""),
                              handoff.get("object_version", "")),
        "入口：%s" % handoff.get("entry_route", ""),
        "呈现：%s%s" % (handoff.get("presentation", ""),
                        ("（%s）" % handoff["ui_resource"]
                         if handoff.get("ui_resource") else "")),
        "到期：%s" % handoff.get("expires_at", ""),
        "动作：请求 %s；允许 %s" % (
            handoff.get("requested_action") or "（无）",
            "、".join(handoff.get("allowed_actions") or []) or "（无）"),
        "写权限：未授予（handoff 只定位对象）",
    ]
    revoked = handoff.get("revoked")
    if revoked:
        lines.append("已撤销：%s（%s）%s" % (
            revoked.get("at", ""), revoked.get("by", ""),
            revoked.get("reason", "")))
    receipts = handoff.get("receipts") or []
    if receipts:
        lines.append("回执 %d 条：" % len(receipts))
        for rec in receipts[-5:]:
            lines.append("  · %s %s by %s → %s（%s）"
                         % (rec.get("at", ""), rec.get("action", ""),
                            rec.get("by", ""), rec.get("result", ""),
                            rec.get("new_version", "")))
    lines.append("边界：%s" % (handoff.get("boundary") or BOUNDARY))
    return "\n".join(lines)


def render_resolution(resolution: Dict[str, Any]) -> str:
    lines = [
        "handoff %s：%s" % (resolution.get("handoff_ref", ""),
                            resolution.get("handoff_state", "")),
        "对象：%s %s" % (resolution.get("object_type", ""),
                         resolution.get("object_id", "")),
        "版本：%s → %s（%s）" % (
            resolution.get("previous_version") or "（空）",
            resolution.get("current_version") or "（对象不存在）",
            "有变化" if resolution.get("changed") else "无变化"),
        "状态：%s / %s" % (resolution.get("canonical_status") or "未知",
                           resolution.get("ux_status") or "未知"),
        "下一步：%s（%s）" % (resolution.get("next_required_action") or "无",
                              resolution.get("next_actor") or "unknown"),
        "回执：%d 条" % len(resolution.get("action_receipts") or []),
        "写权限：未授予",
        "边界：%s" % (resolution.get("boundary") or BOUNDARY),
    ]
    return "\n".join(lines)
