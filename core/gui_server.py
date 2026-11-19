#!/usr/bin/env python3
"""Growovo 伴随 GUI —— 本地只读投影 + 受控写。

设计边界（PRD v1.2 §6/§8）：

- 页面是现有真相源（run_detail / reply_case / grow_runbook / ledger）的投影，
  不新增第二套状态。
- 写操作只调用领域函数：review_reply / confirm_dnc / Ledger.resolve /
  GrowRunV2.pause / grow_runbook.resume_run。
- 每次写前校验 object_version（可选 input_fingerprint），写后重新读取核验；
  核验通过才登记回执。版本冲突一律 409，不覆盖别人的修改。
- 不发送内容、不审批计划、不解除 DNC、不自动重试外部动作。
- 只绑定 127.0.0.1；POST 需要启动时生成的随机 token。
"""
from __future__ import annotations

import hashlib
import html
import json
import secrets
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import agent_bridge

SCHEMA = "growovo.gui-response/1"
RECEIPT_SCHEMA = "growovo.gui-receipt/1"
BOUNDARY = ("本页是本地只读投影＋受控写：不发送内容、不审批计划、不解除 DNC、"
            "不重试外部动作；写操作必须具名，写后核验通过才登记回执。")
HOST = "127.0.0.1"
PORT = 8765
MAX_BODY = 64 * 1024
LEDGER_RESULTS = ("committed", "failed", "suppressed", "bounced", "no-dm")
ACTION_OBJECT = {
    "reply.review": "reply_case",
    "reply.dnc": "reply_case",
    "ledger.resolve": "ledger_ref",
    "run.pause": "run",
    "run.resume": "run",
}
_POST_ROUTES = {
    "/api/reply/review": "reply.review",
    "/api/reply/dnc": "reply.dnc",
    "/api/ledger/resolve": "ledger.resolve",
    "/api/run/pause": "run.pause",
    "/api/run/resume": "run.resume",
}
_HTTP = {"ok": 200, "conflict": 409, "failed": 422, "invalid": 400,
         "denied": 403, "not_found": 404, "unavailable": 503}


class GuiError(Exception):
    """带 HTTP 语义的拒绝。字段不足、版本冲突、权限不符都走这里。"""

    def __init__(self, status: str, code: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    @property
    def http(self) -> int:
        return _HTTP.get(self.status, 400)

    def body(self, action: str = "", object_type: str = "",
             object_id: str = "") -> Dict[str, Any]:
        out = {
            "schema": SCHEMA,
            "status": self.status,
            "code": self.code,
            "detail": self.message,
            "action": action,
            "object_type": object_type,
            "object_id": object_id,
            "verification": {"ok": False, "checks": []},
            "boundary": BOUNDARY,
        }
        out.update(self.extra)
        return out


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_idem() -> str:
    return "gui_" + secrets.token_hex(10)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _e(value) -> str:
    return html.escape(_text(value), quote=True)


def _short(value, limit: int = 96) -> str:
    text = " ".join(_text(value).split())
    if len(text) <= limit:
        return text
    return text[:max(1, limit - 1)] + "…"


def _safe_id(value, *, kind: str) -> str:
    ident = _text(value)
    if not ident or ident in (".", "..") or "/" in ident or "\\" in ident \
            or "\x00" in ident:
        raise GuiError("invalid", "INVALID_ID", "%s ID 不合法：%r"
                       % (kind, value))
    return ident


# ---------------------------------------------------------------------------
# GUI 写入回执（与 Agent handoff 回执分开：没有 handoff 的写也要可重放）
# ---------------------------------------------------------------------------

def _receipts_path(data_dir) -> Path:
    return Path(data_dir) / "handoffs" / "gui_receipts.jsonl"


class _StoreLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        except ImportError:
            self.handle.close()
            raise GuiError("unavailable", "LOCK_UNAVAILABLE",
                           "平台缺少 fcntl，无法保证回执并发安全")
        return self.handle

    def __exit__(self, exc_type, exc, tb):
        try:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        self.handle.close()
        return False


def _read_gui_receipts(data_dir) -> List[Dict[str, Any]]:
    path = _receipts_path(data_dir)
    if not path.exists():
        return []
    rows = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(),
                                 1):
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GuiError("unavailable", "STORE_UNREADABLE",
                           "GUI 回执第 %d 行不是合法 JSON：%s" % (number, exc))
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def _find_gui_receipt(data_dir, key: str, request_hash: str
                      ) -> Optional[Dict[str, Any]]:
    wanted = _text(key)
    if not wanted:
        return None
    for rec in _read_gui_receipts(data_dir):
        if _text(rec.get("idempotency_key")) != wanted:
            continue
        seen = _text(rec.get("request_hash"))
        if request_hash and seen and seen != request_hash:
            raise GuiError(
                "conflict", "IDEMPOTENCY_CONFLICT",
                "同一个幂等键对应了不同请求：拒绝重放，请刷新页面后重试")
        return rec
    return None


def _append_gui_receipt(data_dir, record: Dict[str, Any]) -> None:
    path = _receipts_path(data_dir)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with _StoreLock(path) as handle:
        handle.write(line)
        handle.flush()
        try:
            import os
            os.fsync(handle.fileno())
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 写接口：校验 → 调领域函数 → 写后核验 → 回执
# ---------------------------------------------------------------------------

def _check_handoff(data_dir, handoff_ref: str, action: str,
                   object_type: str, object_id: str) -> Optional[Dict[str, Any]]:
    ref = _text(handoff_ref)
    if not ref:
        return None
    try:
        handoff = agent_bridge.get_handoff(data_dir, ref)
    except agent_bridge.BridgeError as exc:
        if exc.code == "NOT_FOUND":
            raise GuiError("not_found", "HANDOFF_NOT_FOUND", str(exc))
        raise GuiError("conflict", "HANDOFF_UNREADABLE", str(exc))
    if handoff["state"] != "active":
        raise GuiError(
            "conflict", "HANDOFF_INACTIVE",
            "handoff 已%s：请让 Agent 重新定位对象" %
            ("过期" if handoff["state"] == "expired" else "撤销"))
    if action not in handoff["allowed_actions"]:
        raise GuiError(
            "denied", "ACTION_NOT_ALLOWED",
            "handoff 未授权该动作：%s（允许：%s）" %
            (action, "、".join(handoff["allowed_actions"])))
    if (handoff["object_type"] != object_type
            or handoff["object_id"] != object_id):
        raise GuiError(
            "conflict", "OBJECT_MISMATCH",
            "handoff 指向 %s/%s，与本次提交的 %s/%s 不一致" %
            (handoff["object_type"], handoff["object_id"],
             object_type, object_id))
    return handoff


def _load_run(data_dir, run_id: str):
    path = Path(data_dir) / "grow" / ("%s.json" % run_id)
    if not path.is_file():
        raise GuiError("not_found", "NOT_FOUND", "没有这个运行：%s" % path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GuiError("unavailable", "RUN_UNREADABLE",
                       "运行文件不可读：%s: %s" % (type(exc).__name__, exc))
    if not isinstance(raw, dict):
        raise GuiError("unavailable", "RUN_UNREADABLE",
                       "运行文件不是 JSON 对象：%s" % path)
    return path, raw


def _apply_review(data_dir, object_id, *, by, reason, label, previous_version,
                  snapshot, **_) -> Dict[str, Any]:
    from . import reply_case

    try:
        updated = reply_case.review_reply(
            data_dir, object_id, reviewer=by, label=label, note=reason)
    except reply_case.ReplyError as exc:
        raise GuiError("invalid", "REVIEW_REJECTED", str(exc))
    try:
        case = reply_case.get_case(data_dir, object_id)
    except reply_case.ReplyError as exc:
        raise GuiError("failed", "VERIFY_FAILED",
                       "写后读不到案例：%s" % exc)
    expected_review = ("confirmed"
                       if label == _text(case.get("classification_candidate"))
                       else "overruled")
    version_now = int(case.get("version") or 0)
    checks = [
        {"name": "案例版本 = 写后版本", "ok": version_now
         == int(updated.get("version") or 0)},
        {"name": "人工分类已落盘", "ok": _text(case.get("human_label")) == label},
        {"name": "复核状态符合候选比对", "ok":
         _text(case.get("review_state")) == expected_review},
        {"name": "操作人已具名", "ok": _text(case.get("reviewer")) == by},
        {"name": "版本已推进", "ok": ("v%d" % version_now) != previous_version},
    ]
    new_version = "v%d" % version_now
    return {
        "ok": all(c["ok"] for c in checks),
        "new_version": new_version,
        "audit_ref": "reply-case:%s@%s" % (object_id, new_version),
        "next_action": ("回到 Agent 对话说“继续”，Agent 会重读最新状态；"
                        "或继续审核队列里的下一条"),
        "detail": "改判已追加为新版本，原候选与旧版本保留",
        "checks": checks,
        "data": {
            "review_state": _text(case.get("review_state")),
            "human_label": _text(case.get("human_label")),
            "dnc_state": _text(case.get("dnc_state")),
            "version": version_now,
        },
    }


def _apply_dnc(data_dir, object_id, *, by, reason, previous_version,
               snapshot, **_) -> Dict[str, Any]:
    from . import reply_case

    try:
        result = reply_case.confirm_dnc(
            data_dir, object_id, operator=by, reason=reason)
    except reply_case.ReplyError as exc:
        raise GuiError("invalid", "DNC_REJECTED", str(exc))
    try:
        case = reply_case.get_case(data_dir, object_id)
    except reply_case.ReplyError:
        case = {}
    version_now = int(case.get("version") or 0)
    checks = [
        {"name": "案例 dnc_state = active", "ok":
         _text(case.get("dnc_state")) == "active"},
        {"name": "返回 dnc_ref 非空", "ok": bool(_text(result.get("dnc_ref")))},
        {"name": "版本已推进", "ok": ("v%d" % version_now) != previous_version},
        {"name": "写后核验未报失败", "ok": bool(result.get("ok"))},
    ]
    if not result.get("ok"):
        return {
            "ok": False,
            "status": "failed",
            "new_version": "v%d" % version_now if version_now else "",
            "audit_ref": _text(result.get("dnc_ref")),
            "next_action": ("保持暂停：人工检查 contacts.jsonl 与 suppress.txt，"
                            "修复后用新的幂等键重试；不要发送"),
            "detail": _text(result.get("error")) or "DNC 双通道写入失败",
            "checks": checks,
            "data": {
                "dnc_state": _text(result.get("dnc_state")),
                "contact_id": _text(result.get("contact_id")),
                "warnings": result.get("warnings") or [],
                "paused_path": _text(result.get("paused_path")),
            },
        }
    return {
        "ok": all(c["ok"] for c in checks),
        "new_version": "v%d" % version_now,
        "audit_ref": _text(result.get("dnc_ref")),
        "next_action": ("DNC 已生效：该目标后续发送门禁会拦截；"
                        "回到 Agent 继续，GUI 不提供解除按钮"),
        "detail": "DNC 已写入抑制集并通过写后核验",
        "checks": checks,
        "data": {
            "dnc_state": _text(case.get("dnc_state")),
            "dnc_ref": _text(case.get("dnc_ref")),
            "contact_id": _text(result.get("contact_id")),
            "warnings": result.get("warnings") or [],
        },
    }


def _apply_ledger(data_dir, object_id, *, by, reason, result, note,
                  previous_version, snapshot, **_) -> Dict[str, Any]:
    from .ledger import Ledger

    basis = _text(note) or _text(reason)
    try:
        record = Ledger(Path(data_dir) / "ledger.jsonl").resolve(
            object_id, result=result, by=by, note=basis)
    except ValueError as exc:
        raise GuiError("invalid", "RESOLVE_REJECTED", str(exc))
    after = agent_bridge.object_snapshot(data_dir, "ledger_ref", object_id)
    checks = [
        {"name": "账本最新裁决 = %s" % result, "ok":
         _text(after.get("status")) == result},
        {"name": "版本已推进", "ok":
         _text(after.get("object_version")) != previous_version},
    ]
    return {
        "ok": all(c["ok"] for c in checks),
        "new_version": _text(after.get("object_version")),
        "audit_ref": "%s#%s@%s" % (object_id, result,
                                  _text(record.get("ts"))),
        "next_action": ("回到运行页恢复：resume 需要具名并写清处理依据；"
                        "不要在未知效果上重试"),
        "detail": "裁决已追加（原账本行不改），发送门禁按最新裁决判断",
        "checks": checks,
        "data": {"ledger_status": _text(after.get("status")),
                 "resolved_by": by, "note": basis},
    }


def _apply_pause(data_dir, object_id, *, by, reason, previous_version,
                 snapshot, **_) -> Dict[str, Any]:
    path, raw = _load_run(data_dir, object_id)
    if raw.get("schema") == "growovo.grow-run/2":
        from .grow_v2 import GrowError, GrowRunV2
        run, error_cls = GrowRunV2(path), GrowError
    else:
        from .grow import GrowError, GrowRun
        run, error_cls = GrowRun(path), GrowError
    try:
        data = run.pause(reason=reason, by=by)
    except error_cls as exc:
        raise GuiError("invalid", "PAUSE_REJECTED", str(exc))
    after = agent_bridge.object_snapshot(data_dir, "run", object_id)
    already = _text(snapshot.get("status")) == "paused"
    checks = [
        {"name": "运行状态为 paused", "ok": _text(after.get("status"))
         == "paused"},
        {"name": "版本已推进（或此前已暂停）", "ok":
         _text(after.get("object_version")) != previous_version or already},
    ]
    return {
        "ok": all(c["ok"] for c in checks),
        "new_version": _text(after.get("object_version")),
        "audit_ref": "%s#pause@%s" % (object_id, _now()),
        "next_action": ("下一轮 tick 不再选中条目；恢复需具名并写清处理了什么"
                        if not already else "已是暂停状态，无需重复操作"),
        "detail": ("原本就是 paused" if already else
                   "已暂停：%s" % _text(data.get("status"))),
        "checks": checks,
        "data": {"status": _text(after.get("status")), "already": already},
    }


def _apply_resume(data_dir, object_id, *, by, reason, incident_id,
                  previous_version, snapshot, **_) -> Dict[str, Any]:
    from . import grow_runbook as rb

    path, _raw = _load_run(data_dir, object_id)
    try:
        result = rb.resume_run(
            data_dir, path, by=by, note=reason,
            after_reconciliation=_text(incident_id))
    except rb.RunbookError as exc:
        raise GuiError("conflict", exc.code or "RESUME_BLOCKED", str(exc))
    except Exception as exc:  # noqa: BLE001 GrowRunV2 的 GrowError
        raise GuiError("invalid", "RESUME_REJECTED", str(exc))
    after = agent_bridge.object_snapshot(data_dir, "run", object_id)
    status_now = _text(after.get("status")) or _text(result.get("status"))
    resumed = bool(result.get("resumed"))
    if not resumed and status_now != "active":
        raise GuiError(
            "conflict", "RESUME_NOT_APPLICABLE",
            "当前状态 %s 不需要恢复" % (status_now or "未知"))
    checks = [
        {"name": "运行状态为 active", "ok": status_now == "active"},
        {"name": "版本已推进（或此前已 active）", "ok":
         _text(after.get("object_version")) != previous_version
         or status_now == "active"},
    ]
    return {
        "ok": all(c["ok"] for c in checks),
        "new_version": _text(after.get("object_version")),
        "audit_ref": "%s#resume@%s" % (object_id, _now()),
        "next_action": ("回到 Agent 对话说“继续”；外部动作不会因恢复而重试，"
                        "待执行条目按原门禁重新评估"),
        "detail": ("已恢复为 active" if resumed
                   else "当前已是 active，无需恢复"),
        "checks": checks,
        "data": {"status": status_now, "resumed": resumed,
                 "incident": _text(incident_id)},
    }


_APPLIERS = {
    "reply.review": _apply_review,
    "reply.dnc": _apply_dnc,
    "ledger.resolve": _apply_ledger,
    "run.pause": _apply_pause,
    "run.resume": _apply_resume,
}


def _response(status: str, action: str, object_type: str, object_id: str,
              previous_version: str, new_version: str, verification,
              audit_ref: str, next_action: str, detail: str,
              data=None, replayed: bool = False,
              handoff_ref: str = "", code: str = "") -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": status,
        "code": code,
        "action": action,
        "object_type": object_type,
        "object_id": object_id,
        "previous_version": previous_version,
        "new_version": new_version,
        "verification": verification,
        "audit_ref": audit_ref,
        "next_action": next_action,
        "detail": detail,
        "data": data or {},
        "replayed": replayed,
        "handoff_ref": handoff_ref,
        "boundary": BOUNDARY,
    }


def apply_write(data_dir, action: str, payload: Dict[str, Any]
                ) -> Tuple[int, Dict[str, Any]]:
    """校验 → 幂等重放 → handoff 权限 → 版本 → 领域写 → 核验 → 回执。"""
    data_dir = Path(data_dir)
    if action not in ACTION_OBJECT:
        raise GuiError("not_found", "UNKNOWN_ACTION",
                       "不支持的动作：%s" % action)
    if not isinstance(payload, dict):
        raise GuiError("invalid", "INVALID_BODY", "请求体必须是 JSON 对象")

    object_type = ACTION_OBJECT[action]
    object_id = _text(payload.get("object_id"))
    by = _text(payload.get("by"))
    reason = _text(payload.get("reason"))
    version = _text(payload.get("object_version"))
    fingerprint = _text(payload.get("input_fingerprint"))
    handoff_ref = _text(payload.get("handoff_ref"))
    idem = _text(payload.get("idempotency_key")) or _new_idem()

    missing = []
    if not object_id:
        missing.append("object_id")
    if not by:
        missing.append("by（具名操作人）")
    if not version:
        missing.append("object_version（先刷新读取最新版本）")
    if missing:
        raise GuiError("invalid", "INPUT_REQUIRED",
                       "缺少必填字段：%s" % "、".join(missing))

    label = result = ""
    note = _text(payload.get("note"))
    incident_id = _text(payload.get("incident_id"))
    if action == "reply.review":
        from . import reply_case
        label = reply_case.normalize_label(_text(payload.get("label"))) or ""
        if not label:
            raise GuiError("invalid", "INVALID_LABEL",
                           "分类不在允许集合：%s" % _text(payload.get("label")))
    if action == "ledger.resolve":
        result = _text(payload.get("result"))
        if result not in LEDGER_RESULTS:
            raise GuiError("invalid", "INVALID_RESULT",
                           "裁决只允许：%s" % "、".join(LEDGER_RESULTS))
        if not (note or reason):
            raise GuiError("invalid", "NOTE_REQUIRED",
                           "裁决必须写依据（note：平台后台链接/截图路径）")
    if action == "run.resume" and not reason:
        raise GuiError("invalid", "NOTE_REQUIRED",
                       "恢复必须写处理了什么（哪条队列、结论、谁修的）")

    request_hash = _sha(_canonical({
        "action": action,
        "payload": {k: v for k, v in payload.items()
                    if k not in ("idempotency_key", "token")},
    }))
    hit = _find_gui_receipt(data_dir, idem, request_hash)
    if hit is not None:
        body = dict(hit.get("response") or {})
        if not body:
            body = _response("ok", action, object_type, object_id, "", "", {},
                             "", "", "已重放上次结果")
        body["replayed"] = True
        return _HTTP.get(_text(body.get("status")), 200), body

    handoff = _check_handoff(data_dir, handoff_ref, action, object_type,
                             object_id)
    snap = agent_bridge.object_snapshot(data_dir, object_type, object_id)
    if not snap.get("exists"):
        if _text(snap.get("status")) == "unreadable":
            raise GuiError("unavailable", "OBJECT_UNREADABLE",
                           "对象存在但不可读：%s %s" % (object_type, object_id))
        raise GuiError("not_found", "NOT_FOUND", "对象不存在：%s %s"
                       % (object_type, object_id))
    if _text(snap.get("status")) == "unreadable":
        raise GuiError("unavailable", "OBJECT_UNREADABLE",
                       "对象不可读：%s %s" % (object_type, object_id))
    previous = _text(snap.get("object_version"))
    if version != previous:
        raise GuiError(
            "conflict", "VERSION_CONFLICT",
            "对象已被改写：当前版本 %s，你提交的是 %s；刷新后再决定"
            % (previous, version), current_version=previous)
    if fingerprint and snap.get("input_fingerprint") \
            and fingerprint != _text(snap.get("input_fingerprint")):
        raise GuiError(
            "conflict", "FINGERPRINT_CONFLICT",
            "输入指纹不一致：对象来源已变化，请刷新后重新核对",
            current_version=previous)

    applied = _APPLIERS[action](
        data_dir, object_id, by=by, reason=reason, label=label,
        result=result, note=note, incident_id=incident_id,
        previous_version=previous, snapshot=snap)
    checks = applied.get("checks") or []
    verified = bool(applied.get("ok")) and all(c.get("ok") for c in checks)
    status = "ok" if verified else "failed"
    if applied.get("status"):
        status = applied["status"]

    body = _response(
        status, action, object_type, object_id, previous,
        _text(applied.get("new_version")),
        {"ok": verified, "checks": checks},
        _text(applied.get("audit_ref")),
        _text(applied.get("next_action")),
        _text(applied.get("detail")),
        data=applied.get("data") or {},
        handoff_ref=handoff_ref,
        code="" if verified else "VERIFY_FAILED",
    )

    if status == "ok" and handoff is not None:
        try:
            receipt = agent_bridge.record_receipt(
                data_dir, handoff["handoff_ref"], action=action, by=by,
                reason=reason or note, result="ok",
                new_version=_text(applied.get("new_version")),
                audit_ref=_text(applied.get("audit_ref")),
                idempotency_key=idem, request_hash=request_hash,
                response=body)
            body["receipt_at"] = _text(receipt.get("at"))
        except agent_bridge.BridgeError as exc:
            body.update({
                "status": "failed",
                "code": "RECEIPT_FAILED",
                "detail": "写已生效并核验，但 Agent 回执登记失败：%s" % exc,
                "verification": {"ok": False, "checks": checks},
                "next_action": "刷新页面确认状态；让 Agent 重新读取 handoff",
            })
            status = "failed"

    record = {
        "schema": RECEIPT_SCHEMA,
        "at": _now(),
        "action": action,
        "object_type": object_type,
        "object_id": object_id,
        "by": by,
        "reason": reason,
        "idempotency_key": idem,
        "request_hash": request_hash,
        "result": status,
        "response": body,
    }
    try:
        _append_gui_receipt(data_dir, record)
    except (OSError, GuiError) as exc:
        return 500, {
            "schema": SCHEMA,
            "status": "failed",
            "code": "RECEIPT_STORE_FAILED",
            "detail": "写已生效，但本地回执无法记录：%s" % exc,
            "action": action,
            "object_type": object_type,
            "object_id": object_id,
            "new_version": _text(applied.get("new_version")),
            "verification": {"ok": False, "checks": checks},
            "next_action": "刷新页面确认对象状态；不要重复提交",
            "boundary": BOUNDARY,
        }
    return _HTTP.get(body.get("status"), 200), body


# ---------------------------------------------------------------------------
# HTML 渲染
# ---------------------------------------------------------------------------

_STYLE = """
:root{--bg:#f5f5f2;--panel:#fff;--ink:#1e1e1c;--muted:#6c6c66;
--line:#deded8;--accent:#265c4b;--warn:#8a5c00;--bad:#8f2f2f;--chip:#eceae4}
@media (prefers-color-scheme:dark){:root{--bg:#171717;--panel:#202020;
--ink:#ececea;--muted:#a4a49e;--line:#343434;--accent:#82c6ac;
--warn:#dfb25a;--bad:#e39a9a;--chip:#2a2a2a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Segoe UI",
sans-serif}header{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:center;
padding:11px 20px;border-bottom:1px solid var(--line);background:var(--panel);
position:sticky;top:0;z-index:5}header .brand{font-weight:650;color:var(--ink);
text-decoration:none}header nav a{color:var(--accent);text-decoration:none;
margin-right:12px}header .boundary{font-size:12px;color:var(--muted)}
main{max-width:1180px;margin:0 auto;padding:20px 20px 60px}
h1{font-size:21px;margin:4px 0 14px}h2{font-size:16px;margin:26px 0 10px}
p{margin:8px 0}.muted{color:var(--muted)}.small{font-size:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
gap:10px;margin:12px 0 18px}.card{background:var(--panel);border:1px solid
var(--line);border-radius:10px;padding:12px 14px}.card .k{font-size:12px;
color:var(--muted)}.card .v{font-size:19px;font-weight:600;margin-top:2px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;margin:12px 0}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--muted);font-weight:500;padding:7px 8px;
border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:8px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
a{color:var(--accent)}code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,
monospace;font-size:12.5px}pre.raw{white-space:pre-wrap;word-break:break-word;
background:var(--chip);border-radius:8px;padding:12px;margin:0;
max-height:420px;overflow:auto}
pre.result{white-space:pre-wrap;word-break:break-word;background:var(--chip);
border-left:3px solid var(--accent);border-radius:6px;padding:10px;
margin-top:10px;max-height:360px;overflow:auto}
pre.result[data-status=failed],pre.result[data-status=conflict],
pre.result[data-status=denied],pre.result[data-status=invalid],
pre.result[data-status=not_found]{border-left-color:var(--bad)}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;
font-size:12px;border:1px solid var(--line);background:var(--chip);
white-space:nowrap}.badge.ok{border-color:var(--accent);color:var(--accent)}
.badge.warn{border-color:var(--warn);color:var(--warn)}.badge.bad{border-color:
var(--bad);color:var(--bad)}
.cols{display:grid;grid-template-columns:280px minmax(0,1fr) 360px;gap:16px;
align-items:start}@media(max-width:980px){.cols{grid-template-columns:1fr}}
.queue{list-style:none;margin:0;padding:0;max-height:680px;overflow:auto}
.queue li{border-bottom:1px solid var(--line)}.queue a{display:block;
padding:9px 8px;text-decoration:none;color:var(--ink)}
.queue li.sel a{background:var(--chip)}
.queue .meta{font-size:12px;color:var(--muted);margin-top:2px}
form.write{border-top:1px solid var(--line);margin-top:14px;padding-top:12px}
label{display:block;margin:9px 0 4px;font-size:13px;color:var(--muted)}
input,select,textarea{width:100%;padding:7px 9px;border:1px solid var(--line);
border-radius:7px;background:var(--panel);color:var(--ink);font:inherit}
textarea{min-height:64px;resize:vertical}
button{margin-top:12px;padding:8px 16px;border-radius:8px;border:1px solid
var(--accent);background:var(--accent);color:#fff;font:inherit;cursor:pointer}
button.secondary{background:transparent;color:var(--accent)}
button:disabled{opacity:.5;cursor:default}
.notice{border-left:3px solid var(--warn);background:var(--panel);
padding:9px 12px;margin:10px 0;border-radius:6px;font-size:14px}
.danger{border-left:3px solid var(--bad)}
.kv{display:grid;grid-template-columns:150px minmax(0,1fr);gap:4px 12px;
font-size:14px}.kv dt{color:var(--muted)}.kv dd{margin:0;word-break:break-word}
"""

_SCRIPT = """
(function(){
var meta=document.querySelector('meta[name="growovo-gui-token"]');
var TOKEN=meta?meta.content:"";
var OUT=document.getElementById('result');
function show(data,http){
  if(!OUT){return;}
  OUT.hidden=false;
  OUT.dataset.status=(data&&data.status)||('http_'+http);
  OUT.textContent=JSON.stringify(data,null,2);
  if(data&&data.status==='ok'){setTimeout(function(){location.reload();},900);}
}
function key(){
  if(window.crypto&&crypto.randomUUID){return crypto.randomUUID();}
  return 'gui_'+Date.now().toString(16)+Math.random().toString(16).slice(2);
}
document.querySelectorAll('form[data-write]').forEach(function(form){
  if(!form.querySelector('input[name=idempotency_key]')){
    var i=document.createElement('input');
    i.type='hidden';i.name='idempotency_key';i.value=key();
    form.appendChild(i);
  }
});
window.submitForm=function(event,url){
  event.preventDefault();
  var form=event.target;
  var payload={};
  new FormData(form).forEach(function(v,k){payload[k]=v;});
  form.querySelectorAll('input[type=checkbox]').forEach(function(cb){
    payload[cb.name]=cb.checked;
  });
  var buttons=form.querySelectorAll('button');
  buttons.forEach(function(b){b.disabled=true;});
  fetch(url,{method:'POST',headers:{'Content-Type':'application/json',
    'X-Growovo-Gui-Token':TOKEN},body:JSON.stringify(payload)})
    .then(function(res){return res.json().then(function(data){
      show(data,res.status);
      if(!data||data.status!=='ok'){buttons.forEach(function(b){
        b.disabled=false;});}
    });})
    .catch(function(err){
      show({status:'network_error',detail:String(err)},0);
      buttons.forEach(function(b){b.disabled=false;});
    });
  return false;
};
})();
"""


def _badge(value) -> str:
    text = _text(value)
    if not text:
        return '<span class="badge">未知</span>'
    low = text.lower()
    cls = ""
    if low in ("active", "ready", "ok", "confirmed", "committed",
               "suppressed", "bounced", "no-dm"):
        cls = " ok"
    elif low in ("needs_attention", "needs_review", "pending", "indeterminate",
                 "stale", "warning", "paused", "unknown", "unverified",
                 "overruled", "open"):
        cls = " warn"
    elif low in ("failed", "blocked", "unreadable", "error", "revoked",
                 "expired", "high"):
        cls = " bad"
    return '<span class="badge%s">%s</span>' % (cls, _e(text))


def _page(title: str, body: str, token: str = "") -> str:
    return (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="growovo-gui-token" content="%s">'
        '<meta name="referrer" content="no-referrer">'
        '<title>%s · Growovo 伴随 GUI</title><style>%s</style></head><body>'
        '<header><a class="brand" href="/runs">Growovo 伴随 GUI</a>'
        '<nav><a href="/runs">运行</a><a href="/api/risk">风险 JSON</a></nav>'
        '<span class="boundary">本地只读投影＋受控写；不发送、不审批、'
        '不解除 DNC</span></header><main>%s</main>'
        '<footer class="muted small" style="max-width:1180px;margin:0 auto;'
        'padding:0 20px 40px">%s</footer>'
        '<script>%s</script></body></html>'
        % (_e(token), _e(title), _STYLE, body, _e(BOUNDARY), _SCRIPT))


def _error_page(title: str, detail: str, token: str = "") -> str:
    return _page(title, '<h1>%s</h1><div class="panel"><p>%s</p>'
                 '<p><a href="/runs">返回运行列表</a></p></div>'
                 % (_e(title), _e(detail)), token)


def _form(action: str, hidden: Dict[str, Any], inner: str) -> str:
    fields = "".join(
        '<input type="hidden" name="%s" value="%s">' % (_e(k), _e(v))
        for k, v in hidden.items())
    return ('<form class="write" data-write onsubmit="return submitForm'
            '(event,\'%s\')">%s%s<pre id="result" class="result" hidden>'
            '</pre></form>' % (_e(action), fields, inner))


def _person_field() -> str:
    return ('<label>操作人（具名，写入审计）</label>'
            '<input name="by" required placeholder="你的名字">')


def _handoff_hidden(handoff_ref: str) -> Dict[str, str]:
    return {"handoff_ref": handoff_ref} if _text(handoff_ref) else {}


def _handoff_notice(data_dir, handoff_ref: str) -> str:
    ref = _text(handoff_ref)
    if not ref:
        return ""
    try:
        handoff = agent_bridge.get_handoff(data_dir, ref)
    except agent_bridge.BridgeError as exc:
        return '<div class="notice danger">handoff %s 不可用：%s</div>' % (
            _e(ref), _e(exc))
    return ('<div class="notice">本页由 handoff <code>%s</code> 唤起（%s）。'
            'handoff 只定位对象与动作，不授予写权限；处理完回到 Agent 对话'
            '说“继续”。</div>' % (_e(ref), _e(handoff["state"])))


def _table(headers: List[str], rows: List[List[str]],
           empty: str = "没有数据") -> str:
    if not rows:
        return '<p class="muted">%s</p>' % _e(empty)
    head = "".join("<th>%s</th>" % _e(h) for h in headers)
    body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % cell
                                           for cell in row)
                   for row in rows)
    return "<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (
        head, body)


def render_runs(data_dir, token: str = "") -> str:
    summary = agent_bridge.risk_summary(data_dir)
    totals = summary.get("totals") or {}
    cards = "".join(
        '<div class="card"><div class="k">%s</div><div class="v">%s</div></div>'
        % (_e(k), _e(v)) for k, v in (
            ("运行", totals.get("runs", 0)),
            ("风险条目", totals.get("risks", 0)),
            ("高风险", totals.get("high", 0)),
            ("待审回复", totals.get("replies_pending", 0)),
            ("DNC 失败", totals.get("dnc_failed", 0)),
            ("阻塞告警", totals.get("blocking_incidents", 0)),
        ))
    rows: List[List[str]] = []
    for run in summary.get("runs") or []:
        rid = _text(run.get("run_id"))
        routes = run.get("routes") or {}
        replies = run.get("replies") or {}
        rstate = _text(replies.get("state"))
        if rstate in ("unreadable", "error"):
            reply_cell = "%s <span class=\"small muted\">%s</span>" % (
                _badge("unknown"), _short(replies.get("error"), 60))
        elif rstate == "empty":
            reply_cell = "0 <span class=\"small muted\">未导入</span>"
        else:
            reply_cell = "%d 待审 / %d 未解决" % (
                int(replies.get("pending_review") or 0),
                int(replies.get("unresolved_count") or 0))
        dnc = replies.get("dnc") or {}
        dnc_cell = "待 %d · 生效 %d · 失败 %d" % (
            int(dnc.get("pending") or 0), int(dnc.get("active") or 0),
            int(dnc.get("failed") or 0))
        if dnc.get("unverified"):
            dnc_cell += ' <span class="badge bad">未核实 %d</span>' % len(
                dnc.get("unverified") or [])
        incidents = run.get("incidents") or {}
        inc_cell = "%d 条" % int(incidents.get("blocking") or 0)
        if incidents.get("indeterminate"):
            inc_cell += ' <span class="badge warn">待裁决 %d</span>' % len(
                incidents.get("indeterminate") or [])
        links = ('<a href="%s">详情</a> · <a href="%s">回复</a> · '
                 '<a href="%s">告警</a>' % (
                     _e("/runs/" + quote(rid)),
                     _e("/runs/" + quote(rid) + "/replies"),
                     _e("/runs/" + quote(rid) + "/incidents")))
        rows.append([
            '<a href="/runs/%s"><strong>%s</strong></a>' % (
                _e(quote(rid)), _e(rid)),
            "%s %s" % (_badge(run.get("canonical_status")),
                       _badge(run.get("ux_state"))),
            _e((run.get("freshness") or {}).get("text")),
            reply_cell,
            dnc_cell,
            inc_cell,
            _e(_short(run.get("next_required_action"), 110)),
            links,
        ])
    suggestion = summary.get("handoff_suggestion")
    suggestion_html = ""
    if suggestion:
        suggestion_html = (
            '<div class="panel"><strong>建议的 Agent 交接</strong>'
            '<dl class="kv"><dt>对象</dt><dd>%s / %s</dd>'
            '<dt>建议动作</dt><dd>%s</dd><dt>原因</dt><dd>%s</dd>'
            '<dt>入口</dt><dd><a href="%s">%s</a></dd></dl></div>'
            % (_e(suggestion.get("object_type")),
               _e(suggestion.get("object_id")),
               _e(suggestion.get("requested_action")),
               _e(suggestion.get("reason")),
               _e(suggestion.get("entry_route") or "/runs"),
               _e(suggestion.get("entry_route") or "/runs")))
    notes = "".join('<p class="muted">%s</p>' % _e(n)
                    for n in summary.get("notes") or [])
    table_html = '<div class="panel">%s</div>' % _table(
        ["运行", "状态", "新鲜度", "回复", "DNC", "阻塞告警", "下一步", "入口"],
        rows, "没有找到运行：先创建并落盘运行文件")
    body = ('<h1>运行与风险</h1>'
            '<p class="muted">Agent 主入口的只读摘要；点进运行后再做受控处理。'
            '摘要生成于 %s。</p>' % _e(summary.get("generated_at"))
            + cards + notes + table_html + suggestion_html)
    return _page("运行与风险", body, token)


def render_run(data_dir, run_id: str, detail: Dict[str, Any],
               token: str = "") -> str:
    obj = detail.get("object") or {}
    fresh = detail.get("freshness") or {}
    auth = detail.get("authorization") or {}
    replies = detail.get("replies") or {}
    incidents = detail.get("incidents") or []
    states = detail.get("states") or []
    source = detail.get("source") or {}
    rid = _text(obj.get("run_id")) or run_id
    state_html = "".join('<div class="notice">%s：%s</div>'
                         % (_e(s.get("code")), _e(s.get("detail")))
                         for s in states)
    cards = "".join(
        '<div class="card"><div class="k">%s</div><div class="v">%s</div></div>'
        % (_e(k), v) for k, v in (
            ("canonical 状态", _badge(obj.get("canonical_status"))),
            ("UX 状态", _badge(obj.get("ux_state"))),
            ("新鲜度", _badge(fresh.get("state"))),
            ("待审回复", _e(replies.get("pending_review", 0))),
            ("阻塞告警", _e(len([i for i in incidents
                                if i.get("severity") == "blocking"]))),
            ("DNC 失败", _e((replies.get("dnc") or {}).get("failed", 0))),
        ))
    info = ('<div class="panel"><dl class="kv">'
            '<dt>运行文件</dt><dd><code>%s</code></dd>'
            '<dt>schema</dt><dd>%s</dd>'
            '<dt>下一步</dt><dd>%s</dd>'
            '<dt>下一步命令</dt><dd><code>%s</code></dd>'
            '<dt>新鲜度</dt><dd>%s</dd>'
            '<dt>授权</dt><dd>%s</dd></dl></div>') % (
        _e(source.get("path")), _e(source.get("schema")),
        _e(obj.get("next_required_action")), _e(obj.get("next_command")),
        _e(fresh.get("text")), _e(auth.get("note")))
    inc_rows = []
    for item in incidents[:20]:
        ref = ""
        evidence = item.get("evidence")
        if isinstance(evidence, dict):
            ref = _text(evidence.get("ref"))
        inc_rows.append([
            _e(item.get("event_id")),
            _badge(item.get("severity")),
            '<strong>%s</strong><div class="small muted">%s</div>' % (
                _e(item.get("title")), _e(item.get("detail"))),
            _e(item.get("error_code")),
            ('<a href="/ledger?ref=%s">去裁决账本</a>' % _e(quote(ref)))
            if ref else '<span class="muted">无可裁决引用</span>',
        ])
    inc_html = _table(["事件", "级别", "说明", "错误码", "动作"], inc_rows,
                      "没有未处理的告警")
    links = ('<p><a href="/runs/%s/replies">打开回复工作台</a> · '
             '<a href="/runs/%s/incidents">查看全部告警</a> · '
             '<a href="/api/run/%s">运行详情 JSON</a></p>'
             % (_e(quote(rid)), _e(quote(rid)), _e(quote(rid))))
    if not source.get("exists"):
        actions = ('<div class="notice danger">运行文件不存在或不可读，'
                   '本页不提供写操作。</div>')
    else:
        incident_hint = "、".join(
            _text(i.get("event_id")) for i in incidents
            if _text(i.get("error_code")) in ("INPUT_REQUIRED",
                                              "REVIEW_REQUIRED")
            or _text(i.get("kind")) == "indeterminate_effect"
        ) or "（没有待核对事件时留空）"
        pause_inner = (
            '<label>暂停原因</label><textarea name="reason" '
            'placeholder="为什么暂停：哪条队列、什么风险"></textarea>'
            + _person_field() +
            '<button type="submit">暂停运行</button>'
            '<p class="small muted">暂停后下一轮 tick 不再选中条目；'
            '不会撤回已发出的内容。</p>')
        resume_inner = (
            '<label>处理说明（必填）</label><textarea name="reason" required '
            'placeholder="处理了什么：哪条队列、结论、谁修的"></textarea>'
            '<label>已核对的事件 ID（可选）</label>'
            '<input name="incident_id" placeholder="INC-…（%s）" '
            'value="">' % _e(incident_hint) +
            _person_field() +
            '<button type="submit" class="secondary">恢复运行</button>'
            '<p class="small muted">有阻塞告警时必须先裁决账本并填写事件 ID；'
            '恢复不会重试未知外部动作。</p>')
        actions = (
            '<h2>受控处理</h2>'
            + _form("/api/run/pause",
                    {"object_id": rid,
                     "object_version": _text((agent_bridge.object_snapshot(
                         data_dir, "run", rid)).get("object_version")),
                     "input_fingerprint": _text((agent_bridge.object_snapshot(
                         data_dir, "run", rid)).get("input_fingerprint"))},
                    pause_inner)
            + _form("/api/run/resume",
                    {"object_id": rid,
                     "object_version": _text((agent_bridge.object_snapshot(
                         data_dir, "run", rid)).get("object_version")),
                     "input_fingerprint": _text((agent_bridge.object_snapshot(
                         data_dir, "run", rid)).get("input_fingerprint"))},
                    resume_inner))
    body = ('<h1>%s</h1>%s%s%s<div class="panel">%s</div>'
            '<h2>告警</h2><div class="panel">%s</div>%s%s'
            % (_e(rid), state_html, cards, info, links, inc_html, actions,
               '<p class="small muted">%s</p>' % _e(detail.get("boundary"))))
    return _page("运行 " + rid, body, token)


def render_replies(data_dir, run_id: str, case_id: str,
                   handoff_ref: str = "", token: str = "") -> str:
    from . import reply_case

    rid = run_id
    proj: Dict[str, Any] = {}
    proj_error = ""
    try:
        proj = reply_case.run_projection(data_dir, rid)
    except Exception as exc:  # noqa: BLE001 页面不因存储坏掉而崩
        proj_error = "%s: %s" % (type(exc).__name__, exc)
    try:
        cases = reply_case.list_cases(data_dir, run_id=rid)
        list_error = ""
    except Exception as exc:  # noqa: BLE001
        cases = []
        list_error = "%s: %s" % (type(exc).__name__, exc)
    selected = None
    select_error = ""
    if case_id:
        try:
            selected = reply_case.get_case(data_dir, case_id)
        except reply_case.ReplyError as exc:
            select_error = str(exc)
    queue_items = []
    for case in cases:
        cid = _text(case.get("reply_case_id"))
        cls = "sel" if cid == case_id else ""
        label = _text(case.get("human_label")) or _text(
            case.get("classification_candidate")) or "未分类"
        queue_items.append(
            '<li class="%s"><a href="/runs/%s/replies?case=%s%s">'
            '<strong>%s</strong> %s<div class="meta">%s · %s · 版本 v%s</div>'
            '</a></li>' % (
                cls, _e(quote(rid)), _e(quote(cid)),
                _e(("&handoff=" + quote(handoff_ref)) if handoff_ref else ""),
                _e(_short(case.get("target") or cid, 40)), _badge(label),
                _e(case.get("channel")),
                _e(case.get("review_state")), _e(case.get("version"))))
    queue_html = ('<ul class="queue">%s</ul>' % "".join(queue_items)
                  if queue_items else '<p class="muted">这个运行还没有回复。'
                  '用 Agent 或 mkt grow reply import 导入授权回复文件。</p>')
    right = ""
    middle = ""
    if select_error:
        middle = '<div class="notice danger">%s</div>' % _e(select_error)
    elif selected is not None:
        cid = _text(selected.get("reply_case_id"))
        version = "v%d" % int(selected.get("version") or 1)
        candidate = _text(selected.get("classification_candidate"))
        current = _text(selected.get("human_label")) or candidate
        dnc_state = _text(selected.get("dnc_state"))
        info = ('<dl class="kv">'
                '<dt>案例</dt><dd><code>%s</code></dd>'
                '<dt>来源</dt><dd>%s · %s</dd>'
                '<dt>目标</dt><dd>%s</dd>'
                '<dt>收到时间</dt><dd>%s</dd>'
                '<dt>候选分类</dt><dd>%s %s</dd>'
                '<dt>人工分类</dt><dd>%s</dd>'
                '<dt>复核状态</dt><dd>%s</dd>'
                '<dt>DNC</dt><dd>%s %s</dd>'
                '<dt>下一步</dt><dd>%s</dd></dl>') % (
            _e(cid), _e(selected.get("channel")),
            _e(selected.get("conversation_ref") or selected.get("source_ref")),
            _e(selected.get("target")), _e(selected.get("received_at")),
            _e(candidate), _badge(selected.get("review_state")),
            _e(current), _badge(selected.get("review_state")),
            _badge(dnc_state), _e(selected.get("dnc_ref")),
            _e(selected.get("next_required_action")))
        raw = _text(selected.get("raw_text")) or _text(
            selected.get("raw_excerpt")) or "（没有原文）"
        middle = ('<div class="panel"><h2>原文</h2><pre class="raw">%s</pre>'
                  '</div><div class="panel"><h2>案例字段</h2>%s</div>'
                  % (_e(raw), info))
        hidden = {
            "object_id": cid,
            "object_version": version,
            "input_fingerprint": _text(selected.get("raw_text_hash")),
        }
        hidden.update(_handoff_hidden(handoff_ref))
        options = "".join(
            '<option value="%s"%s>%s</option>' % (
                _e(lab), " selected" if lab == current else "", _e(lab))
            for lab in reply_case.LABELS)
        review_inner = (
            '<label>人工分类</label><select name="label">%s</select>'
            '<label>改判依据</label><textarea name="reason" '
            'placeholder="为什么这样判（原文依据/上下文）"></textarea>' % options
            + _person_field() +
            '<button type="submit">保存改判</button>'
            '<p class="small muted">保存会追加新版本，原候选与旧版本保留；'
            '不会自动发送任何内容。</p>')
        right = ('<div class="panel"><h2>改判</h2>'
                 + _form("/api/reply/review", hidden, review_inner) + '</div>')
        if dnc_state == "active":
            right += ('<div class="notice">DNC 已生效：<code>%s</code>。'
                      '本页没有解除按钮；如需更正，人工在联系人库处理。</div>'
                      % _e(selected.get("dnc_ref")))
        else:
            dnc_inner = (
                '<label>退订处理原因</label><textarea name="reason" '
                'placeholder="对方在哪句话/哪个渠道要求退订"></textarea>'
                + _person_field() +
                '<button type="submit" class="secondary">确认 DNC</button>'
                '<p class="small muted">确认后写入抑制名单，后续发送门禁立即'
                '拦截该目标；没有解除入口。</p>')
            warning = ""
            if dnc_state == "failed":
                warning = ('<div class="notice danger">上次 DNC 双通道写入失败；'
                           '运行应已强制暂停。先人工检查 contacts.jsonl 与 '
                           'suppress.txt。</div>')
            right += ('<div class="panel"><h2>DNC</h2>%s'
                      % warning
                      + _form("/api/reply/dnc", hidden, dnc_inner) + '</div>')
    else:
        middle = ('<div class="panel"><p class="muted">从左侧选择一条回复；'
                  '三栏分别是队列、原文、证据与处理。</p></div>')
    banner = ""
    if proj_error:
        banner += ('<div class="notice danger">回复存储不可读：%s。'
                   '数量与状态显示为未知，不猜测。</div>' % _e(proj_error))
    if list_error:
        banner += ('<div class="notice danger">回复队列不可读：%s</div>'
                   % _e(list_error))
    if proj.get("state") == "unreadable":
        banner += ('<div class="notice danger">投影不可读：%s</div>'
                   % _e(proj.get("error")))
    dnc = proj.get("dnc") or {}
    strip = ('<p class="muted">共 %s 条；待审 %s；未解决 %s；冲突 %s；'
             'DNC 待处理 %s / 生效 %s / 失败 %s。</p>') % (
        _e(proj.get("count")), _e(proj.get("pending_review", "")),
        _e(proj.get("unresolved_count")), _e(proj.get("conflict_count")),
        _e(dnc.get("pending")), _e(dnc.get("active")), _e(dnc.get("failed")))
    if proj.get("state") == "empty":
        strip = ('<p class="muted">%s</p>' % _e(
            proj.get("note") or "这个运行还没有导入回复。"))
    if proj.get("conflicts"):
        strip += ('<div class="notice danger">有 %d 条导入冲突需要人工核对。'
                  '</div>' % len(proj.get("conflicts") or []))
    body = ('<h1>回复工作台 · %s</h1>%s%s%s'
            '<div class="cols"><div class="panel"><h2>队列</h2>%s</div>'
            '<div>%s</div><div>%s</div></div>'
            % (_e(rid), _handoff_notice(data_dir, handoff_ref), banner,
               strip, queue_html, middle, right))
    return _page("回复 · " + rid, body, token)


def render_reply_standalone(data_dir, case_id: str,
                            handoff_ref: str = "", token: str = "") -> str:
    from . import reply_case

    try:
        case = reply_case.get_case(data_dir, case_id)
    except reply_case.ReplyError as exc:
        return _error_page("找不到回复案例", str(exc), token)
    rid = _text(case.get("run_id"))
    if rid:
        target = "/runs/%s/replies?case=%s" % (quote(rid), quote(case_id))
        if handoff_ref:
            target += "&handoff=" + quote(handoff_ref)
        return target
    version = "v%d" % int(case.get("version") or 1)
    hidden = {
        "object_id": case_id,
        "object_version": version,
        "input_fingerprint": _text(case.get("raw_text_hash")),
    }
    hidden.update(_handoff_hidden(handoff_ref))
    options = "".join(
        '<option value="%s"%s>%s</option>' % (
            _e(lab),
            " selected" if lab == (_text(case.get("human_label"))
                                   or _text(case.get("classification_candidate")))
            else "", _e(lab))
        for lab in reply_case.LABELS)
    review_inner = ('<label>人工分类</label><select name="label">%s</select>'
                    '<label>改判依据</label><textarea name="reason"></textarea>'
                    % options + _person_field() +
                    '<button type="submit">保存改判</button>')
    info = ('<dl class="kv"><dt>案例</dt><dd><code>%s</code></dd>'
            '<dt>运行</dt><dd>%s</dd><dt>目标</dt><dd>%s</dd>'
            '<dt>候选</dt><dd>%s</dd><dt>复核状态</dt><dd>%s</dd>'
            '<dt>DNC</dt><dd>%s</dd></dl>') % (
        _e(case_id), _e(rid or "未关联"), _e(case.get("target")),
        _e(case.get("classification_candidate")),
        _badge(case.get("review_state")), _badge(case.get("dnc_state")))
    dnc_inner = ('<label>退订处理原因</label><textarea name="reason">'
                 '</textarea>' + _person_field() +
                 '<button type="submit" class="secondary">确认 DNC</button>')
    body = ('<h1>回复案例 · %s</h1>%s<div class="notice">该回复未关联运行；'
            '处理仍然只写回复存储。</div><div class="panel"><h2>原文</h2>'
            '<pre class="raw">%s</pre>%s</div>'
            '<div class="panel"><h2>改判</h2>%s</div>'
            '<div class="panel"><h2>DNC</h2>%s</div>'
            % (_e(case_id), _handoff_notice(data_dir, handoff_ref),
               _e(case.get("raw_text")), info,
               _form("/api/reply/review", hidden, review_inner),
               _form("/api/reply/dnc", hidden, dnc_inner)))
    return _page("回复案例 " + case_id, body, token)


def render_incidents(data_dir, run_id: str, handoff_ref: str = "",
                     token: str = "") -> str:
    from . import grow_runbook as rb

    rid = run_id
    try:
        block = rb.list_incidents(data_dir, run_id=rid)
        error = ""
    except Exception as exc:  # noqa: BLE001 页面不因扫描失败而崩
        block = {}
        error = "%s: %s" % (type(exc).__name__, exc)
    rows = []
    for item in block.get("incidents") or []:
        ref = ""
        evidence = item.get("evidence")
        if isinstance(evidence, dict):
            ref = _text(evidence.get("ref"))
        rows.append([
            _e(item.get("event_id")),
            _badge(item.get("severity")),
            '<strong>%s</strong><div class="small muted">%s</div>'
            % (_e(item.get("title")), _e(item.get("detail"))),
            _e(item.get("error_code")),
            "是" if item.get("blocks_auto_retry") else "否",
            ('<a href="/ledger?ref=%s%s">去裁决</a>' % (
                _e(quote(ref)),
                _e(("&handoff=" + quote(handoff_ref)) if handoff_ref else "")))
            if ref else '<span class="muted">—</span>',
            '<code>%s</code>' % _e(item.get("next_command")),
        ])
    banner = ""
    if error:
        banner = '<div class="notice danger">告警扫描失败：%s</div>' % _e(error)
    else:
        banner = ('<p class="muted">阻塞 %s 条；待核对外部效果：%s。</p>'
                  % (_e(block.get("blocking", 0)),
                     "是" if block.get("has_pending_external_effect")
                     else "否"))
    body = ('<h1>告警 · %s</h1>%s%s<div class="panel">%s</div>'
            '<p><a href="/runs/%s">回运行页做恢复</a></p>'
            % (_e(rid), _handoff_notice(data_dir, handoff_ref), banner,
               _table(["事件", "级别", "说明", "错误码", "阻止自动重试",
                       "裁决", "下一步命令"], rows),
               _e(quote(rid))))
    return _page("告警 · " + rid, body, token)


def render_ledger(data_dir, ref: str, handoff_ref: str = "",
                  token: str = "") -> str:
    reference = _text(ref)
    if not reference:
        return _error_page("没有指定账本记录", "链接缺少 ?ref=…", token)
    snap = agent_bridge.object_snapshot(data_dir, "ledger_ref", reference)
    if not snap.get("exists"):
        detail = ("账本记录不存在或不可读：%s" % reference
                  if snap.get("status") != "unreadable"
                  else "账本不可读：%s" % (snap.get("detail") or {}).get("error"))
        return _error_page("找不到账本记录", detail, token)
    info = snap.get("detail") or {}
    current = _text(snap.get("status"))
    checks = ('<dl class="kv"><dt>ref</dt><dd><code>%s</code></dd>'
              '<dt>当前结果</dt><dd>%s</dd>'
              '<dt>渠道</dt><dd>%s</dd><dt>动作</dt><dd>%s</dd>'
              '<dt>目标</dt><dd>%s</dd><dt>campaign</dt><dd>%s</dd>'
              '<dt>上次裁决</dt><dd>%s %s</dd></dl>') % (
        _e(reference), _badge(current), _e(info.get("channel")),
        _e(info.get("action")), _e(info.get("target")),
        _e(info.get("campaign") or "—"),
        _e(info.get("resolved_by") or "—"), _e(info.get("resolved_at") or ""))
    options = "".join('<option value="%s"%s>%s</option>' % (
        _e(r), " selected" if r == "committed" else "", _e(r))
        for r in LEDGER_RESULTS)
    hidden = {
        "object_id": reference,
        "object_version": _text(snap.get("object_version")),
        "input_fingerprint": _text(snap.get("input_fingerprint")),
    }
    hidden.update(_handoff_hidden(handoff_ref))
    inner = ('<label>裁决结果</label><select name="result">%s</select>'
             '<label>依据（必填：平台后台链接/截图路径/回读证据）</label>'
             '<textarea name="note" required></textarea>' % options
             + _person_field() +
             '<button type="submit">保存裁决</button>'
             '<p class="small muted">裁决只追加覆盖，不改原账本行；'
             '解决不了外部效果时不要选 committed。</p>')
    banner = ""
    if current == "indeterminate":
        banner = ('<div class="notice">这条记录处于 indeterminate：'
                  '可能已发送但未确认。先到平台后台核对，再记回结果；'
                  '不要在未知状态上重试。</div>')
    body = ('<h1>账本裁决</h1>%s%s<div class="panel">%s</div>'
            '<div class="panel"><h2>记录裁决</h2>%s</div>'
            '<p><a href="/runs">回运行列表</a></p>'
            % (_handoff_notice(data_dir, handoff_ref), banner, checks,
               _form("/api/ledger/resolve", hidden, inner)))
    return _page("账本裁决", body, token)


def render_handoff(data_dir, handoff_ref: str, token: str = "") -> str:
    try:
        handoff = agent_bridge.get_handoff(data_dir, handoff_ref)
    except agent_bridge.BridgeError as exc:
        return _error_page("handoff 不可用", str(exc), token)
    snap = agent_bridge.object_snapshot(
        data_dir, handoff["object_type"], handoff["object_id"])
    route = _text(handoff.get("entry_route")) or "/runs"
    if handoff["state"] == "active" and snap.get("exists"):
        sep = "&" if "?" in route else "?"
        return route + sep + "handoff=" + quote(handoff["handoff_ref"])
    detail = {
        "handoff_ref": handoff["handoff_ref"],
        "state": handoff["state"],
        "expires_at": handoff["expires_at"],
        "object": "%s/%s" % (handoff["object_type"], handoff["object_id"]),
        "object_exists": bool(snap.get("exists")),
        "requested_action": handoff["requested_action"],
        "allowed_actions": "、".join(handoff["allowed_actions"]),
    }
    rows = [[_e(k), _e(v)] for k, v in detail.items()]
    body = ('<h1>handoff 已失效</h1>'
            '<div class="notice">%s。你仍可按正常权限打开对象，但本链接不再'
            '携带上下文；写操作仍要署名为人并通过版本校验。</div>'
            '<div class="panel">%s</div><p><a href="%s">按正常入口打开对象</a>'
            ' · <a href="/runs">回运行列表</a></p>'
            % ("已过期" if handoff["state"] == "expired" else "已撤销",
               _table(["字段", "值"], rows), _e(route)))
    return _page("handoff", body, token)


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------

def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")


class _GuiHandler(BaseHTTPRequestHandler):
    server_version = "GrowovoGui/1"
    sys_version = ""

    def log_message(self, fmt, *args):  # noqa: D401 静默：CLI 前台运行
        return

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._send_json(405, {"schema": SCHEMA, "status": "invalid",
                              "detail": "只支持 GET / POST",
                              "boundary": BOUNDARY})

    do_DELETE = do_PUT
    do_PATCH = do_PUT

    # ----- 基础响应 -----

    def _headers(self, status: int, ctype: str, length: int):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()

    def _send_html(self, status: int, text: str):
        payload = text.encode("utf-8")
        self._headers(status, "text/html; charset=utf-8", len(payload))
        self.wfile.write(payload)

    def _send_json(self, status: int, value):
        payload = _json_bytes(value)
        self._headers(status, "application/json; charset=utf-8", len(payload))
        self.wfile.write(payload)

    def _redirect(self, location: str, code: int = 302):
        body = b""
        self.send_response(code)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    # ----- 分发 -----

    def _dispatch(self, method: str):
        parsed = urlparse(self.path)
        path = unquote(parsed.path or "/")
        query = parse_qs(parsed.query, keep_blank_values=True)
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/") or "/"
        segments = [s for s in path.split("/") if s]
        try:
            if any(s in (".", "..") or "\\" in s or "\x00" in s
                   for s in segments):
                raise GuiError("not_found", "NOT_FOUND", "路径不合法")
            if method == "GET":
                self._get(path, segments, query)
            else:
                self._post(path, query)
        except GuiError as exc:
            if path.startswith("/api/") or path == "/api":
                self._send_json(exc.http, exc.body())
            else:
                self._send_html(exc.http, _error_page(
                    "请求被拒绝：%s" % exc.code, exc.message,
                    getattr(self.server, "token", "")))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # noqa: BLE001 不向页面泄露堆栈
            detail = "%s: %s" % (type(exc).__name__, exc)
            if path.startswith("/api"):
                self._send_json(500, {
                    "schema": SCHEMA, "status": "failed",
                    "code": "INTERNAL", "detail": detail,
                    "boundary": BOUNDARY})
            else:
                self._send_html(500, _error_page(
                    "服务内部错误", detail, getattr(self.server, "token", "")))

    # ----- GET -----

    def _get(self, path: str, segments: List[str],
             query: Dict[str, List[str]]):
        data_dir = self.server.data_dir
        token = self.server.token
        first = _first(query, "run") or _first(query, "id")

        if path == "/api/risk":
            summary = agent_bridge.risk_summary(
                data_dir, run_id=first,
                limit=int(_first(query, "limit") or 10))
            self._send_json(200, summary)
            return
        if len(segments) == 2 and segments[0] == "api" \
                and segments[1] == "handoff":
            raise GuiError("not_found", "NOT_FOUND", "handoff 路径缺 ID")
        if len(segments) == 3 and segments[0] == "api" \
                and segments[1] == "handoff":
            try:
                resolved = agent_bridge.resolve_handoff(data_dir, segments[2])
            except agent_bridge.BridgeError as exc:
                status = "not_found" if exc.code == "NOT_FOUND" else "conflict"
                raise GuiError(status, exc.code, exc.message)
            self._send_json(200, resolved)
            return
        if len(segments) == 3 and segments[0] == "api" \
                and segments[1] == "run":
            from . import run_detail
            self._send_json(200, run_detail.build_run_detail(
                data_dir, segments[2]))
            return
        if len(segments) == 2 and segments[0] == "api" \
                and segments[1] == "replies":
            from . import reply_case
            rid = _first(query, "run")
            cid = _first(query, "case")
            if cid:
                try:
                    case = reply_case.get_case(data_dir, cid)
                except reply_case.ReplyError as exc:
                    raise GuiError("not_found", "NOT_FOUND", str(exc))
                self._send_json(200, {
                    "schema": "growovo.reply-case/1",
                    "read_only": True, "case": case,
                    "boundary": BOUNDARY})
            else:
                self._send_json(200, {
                    "schema": "growovo.reply-projection/1",
                    "read_only": True,
                    "projection": reply_case.run_projection(
                        data_dir, rid,
                        limit=int(_first(query, "limit") or 20)),
                    "boundary": BOUNDARY})
            return
        if path == "/api" or (segments and segments[0] == "api"):
            raise GuiError("not_found", "NOT_FOUND", "没有这个接口")

        if path == "/":
            self._redirect("/runs")
            return
        if path == "/runs":
            self._send_html(200, render_runs(data_dir, token))
            return
        if path == "/ledger":
            self._send_html(200, render_ledger(
                data_dir, _first(query, "ref"),
                _first(query, "handoff"), token))
            return
        if len(segments) == 2 and segments[0] == "runs":
            rid = _safe_id(segments[1], kind="运行")
            from . import run_detail
            detail = run_detail.build_run_detail(data_dir, rid)
            self._send_html(200, render_run(data_dir, rid, detail, token))
            return
        if len(segments) == 3 and segments[0] == "runs" \
                and segments[2] == "replies":
            rid = _safe_id(segments[1], kind="运行")
            self._send_html(200, render_replies(
                data_dir, rid, _first(query, "case"),
                _first(query, "handoff"), token))
            return
        if len(segments) == 3 and segments[0] == "runs" \
                and segments[2] == "incidents":
            rid = _safe_id(segments[1], kind="运行")
            self._send_html(200, render_incidents(
                data_dir, rid, _first(query, "handoff"), token))
            return
        if len(segments) == 2 and segments[0] == "replies":
            cid = _safe_id(segments[1], kind="回复")
            target = render_reply_standalone(
                data_dir, cid, _first(query, "handoff"), token)
            if target.startswith("/runs/"):
                self._redirect(target)
            else:
                self._send_html(200, target)
            return
        if len(segments) == 2 and segments[0] == "handoff":
            target = render_handoff(data_dir, segments[1], token)
            if target.startswith("/"):
                self._redirect(target)
            else:
                self._send_html(200, target)
            return
        if len(segments) == 1 and segments[0] == "favicon.ico":
            self._send_html(204, "")
            return
        raise GuiError("not_found", "NOT_FOUND",
                       "没有这个页面：%s" % path)

    # ----- POST -----

    def _post(self, path: str, query: Dict[str, List[str]]):
        action = _POST_ROUTES.get(path)
        if action is None:
            raise GuiError("not_found", "NOT_FOUND",
                           "没有这个写接口：%s" % path)
        expected = _text(getattr(self.server, "token", ""))
        supplied = _text(self.headers.get("X-Growovo-Gui-Token"))
        if expected and not secrets.compare_digest(expected, supplied):
            self._send_json(403, {
                "schema": SCHEMA, "status": "denied",
                "code": "TOKEN_REQUIRED",
                "detail": "缺少或错误的 GUI token：请从本页重新提交",
                "boundary": BOUNDARY})
            return
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype.lower() != "application/json":
            raise GuiError("invalid", "CONTENT_TYPE",
                           "写接口只接受 application/json")
        raw_length = _text(self.headers.get("Content-Length"))
        try:
            length = int(raw_length)
        except ValueError:
            raise GuiError("invalid", "LENGTH_REQUIRED", "缺少 Content-Length")
        if length < 0 or length > MAX_BODY:
            raise GuiError("invalid", "BODY_TOO_LARGE",
                           "请求体超过 %d 字节" % MAX_BODY)
        payload_raw = self.rfile.read(length)
        try:
            payload = json.loads(payload_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GuiError("invalid", "INVALID_JSON",
                           "请求体不是合法 JSON：%s" % exc)
        if not isinstance(payload, dict):
            raise GuiError("invalid", "INVALID_BODY", "请求体必须是 JSON 对象")
        status, body = apply_write(self.server.data_dir, action, payload)
        self._send_json(status, body)


def _first(query: Dict[str, List[str]], key: str) -> str:
    values = query.get(key) or []
    return _text(values[0]) if values else ""


class GuiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, data_dir, address, token: str):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.token = token
        ThreadingHTTPServer.__init__(self, address, _GuiHandler)

    @property
    def base_url(self) -> str:
        host, port = self.server_address[0], self.server_address[1]
        return "http://%s:%d" % (host, port)


def make_server(data_dir, port: int = 0, host: str = HOST,
                token: Optional[str] = None) -> GuiServer:
    """绑定端口并返回可 shutdown 的 server（测试用 port=0）。"""
    path = Path(data_dir).expanduser()
    if not path.is_dir():
        raise GuiError("invalid", "DATA_DIR_MISSING",
                       "数据目录不存在：%s" % path)
    assigned = token if token is not None else secrets.token_urlsafe(24)
    return GuiServer(path, (host, int(port)), assigned)


def serve(data_dir, port: int = PORT, host: str = HOST,
          open_browser: bool = True, token: Optional[str] = None) -> int:
    """前台运行；Ctrl-C 退出。返回进程退出码。"""
    server = make_server(data_dir, port=port, host=host, token=token)
    url = server.base_url
    print("Growovo 伴随 GUI：%s" % url)
    print("边界：%s" % BOUNDARY)
    print("只监听本机；关闭终端即停止。写操作需要页面内 token。")
    if open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


def routes_table() -> List[Dict[str, Any]]:
    """CLI --dry-run 与文档用：路由清单。"""
    return [
        {"method": "GET", "path": "/runs",
         "purpose": "运行与风险摘要（只读）", "write": False},
        {"method": "GET", "path": "/runs/{id}",
         "purpose": "单次运行详情（只读）", "write": False},
        {"method": "GET", "path": "/runs/{id}/replies",
         "purpose": "回复三栏工作台（只读＋改判/DNC 表单）", "write": False},
        {"method": "GET", "path": "/runs/{id}/incidents",
         "purpose": "告警与账本裁决入口（只读）", "write": False},
        {"method": "GET", "path": "/replies/{case_id}",
         "purpose": "独立回复案例（重定向到运行工作台）", "write": False},
        {"method": "GET", "path": "/ledger?ref=",
         "purpose": "单条账本记录与裁决表单", "write": False},
        {"method": "GET", "path": "/handoff/{ref}",
         "purpose": "Agent handoff 唤起：校验后深链到对象", "write": False},
        {"method": "GET", "path": "/api/risk",
         "purpose": "风险摘要 JSON（Agent 重读用）", "write": False},
        {"method": "GET", "path": "/api/run/{id}",
         "purpose": "运行详情 JSON", "write": False},
        {"method": "GET", "path": "/api/replies?run=&case=",
         "purpose": "回复投影 / 单案例 JSON", "write": False},
        {"method": "GET", "path": "/api/handoff/{ref}",
         "purpose": "handoff 回链解析 JSON", "write": False},
        {"method": "POST", "path": "/api/reply/review",
         "purpose": "具名改判（追加版本）", "write": True},
        {"method": "POST", "path": "/api/reply/dnc",
         "purpose": "确认 DNC（写抑制集；无解除）", "write": True},
        {"method": "POST", "path": "/api/ledger/resolve",
         "purpose": "账本裁决（只追加）", "write": True},
        {"method": "POST", "path": "/api/run/pause",
         "purpose": "暂停运行", "write": True},
        {"method": "POST", "path": "/api/run/resume",
         "purpose": "具名恢复（有阻塞告警需先裁决）", "write": True},
    ]


def render_routes_text() -> str:
    lines = ["Growovo 伴随 GUI 路由（默认 %s:%d）" % (HOST, PORT), ""]
    for row in routes_table():
        mark = "写入" if row["write"] else "只读"
        lines.append("%-5s %-28s %-4s %s" % (
            row["method"], row["path"], mark, row["purpose"]))
    lines.append("")
    lines.append("边界：%s" % BOUNDARY)
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(serve("data", open_browser=False))
