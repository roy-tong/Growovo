#!/usr/bin/env python3
"""统一操作日志 —— 每次真实/干跑动作一条 append-only 完整记录。

评审 2026-11-17 §4.2 的「账号日志最低字段清单」落地：

    operation_id / run_id / queue_item_id
    channel / action / target / content_sha256
    browser_context_ref（不保存凭据）
    account.platform_user_id / handle / observed_at / proof_ref
    operator / approval_ref / policy_sha256
    started_at / finished_at / result
    receipt.id / receipt.url / readback_sha256
    artifacts[].path / sha256
    error.code / retryable / recovery_ref

纪律：
- append-only：与账本同一追加纪律（O_APPEND + flock + 单次 write），不改历史行；
- fail loud：最低字段缺失/类型不对/工件哈希对不上文件时抛 OperationsLogError，
  绝不落半条记录（半条记录比没有记录更危险——它会冒充完整证据）；
- 账号不串号：每条记录只携带**本条动作**观测到的账号；观测身份相对上一条
  （同渠道）发生变化时记 account.switched_from，人工可审计；
- 回读一致性：receipt.readback_sha256（或 readback.status=mismatched）与
  content_sha256 不一致 → result 强制 indeterminate + error.readback_mismatch
  （retryable=false，复用 unknown 回执语义：禁止自动重发，人工裁决后恢复）。
"""
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ledger import _append_line, _read_jsonl_fail_loud

SCHEMA = "growovo.operations/1"
OPERATIONS_LOG_NAME = "operations.jsonl"

#: §4.2 最低字段清单（docs/delivery-contract.json 的 operations_log_minimum_fields
#: 必须与这份完全一致——合同与代码不允许各说各话）。
MINIMUM_FIELDS = (
    "operation_id", "run_id", "queue_item_id",
    "channel", "action", "target", "content_sha256",
    "browser_context_ref",
    "account",
    "operator", "approval_ref", "policy_sha256",
    "started_at", "finished_at", "result",
    "receipt",
    "artifacts",
    "error",
)

# 带正文的写动作：除 rejected（没发出去）外必须有 content_sha256。
# engage（关注/点赞）没有正文，不强制。
CONTENT_BEARING_WRITE_ACTIONS = frozenset({"publish", "reply", "message"})

OPERATION_RESULTS = frozenset({
    "committed", "indeterminate", "failed", "dry-run",
    "no-dm", "bounced", "suppressed", "rejected",
})
READBACK_RESULTS = frozenset({"verified", "mismatched", "unknown", "indeterminate"})

_HEX64 = set("0123456789abcdef")


class OperationsLogError(RuntimeError):
    """操作日志写入校验失败：字段缺失/类型不对/工件哈希不一致。"""


def _is_sha256(value: str) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and set(text) <= _HEX64


def content_sha256(text) -> str:
    """发送正文的规范哈希：对精确发送文本（UTF-8）做 sha256。"""
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def file_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def browser_context_ref(*, env=None) -> str:
    """浏览器上下文引用：只记端点，绝不记 cookie/token/凭据。"""
    environ = os.environ if env is None else env
    endpoint = (environ.get("MKT_CDP_IDENTITY_URL")
                or environ.get("X_CDP_URL")
                or environ.get("MKT_CDP_URL") or "").strip()
    return ("cdp:%s" % endpoint) if endpoint else ""


def approval_evidence(data_dir, *, campaign="", routine=False, record=None) -> Dict[str, str]:
    """从动作现场推导审批依据与策略哈希（只读，不重跑门禁）。

    routine-policy 附带 policy 文件 sha256；plan 附带 plan_id；adhoc 如实标注。
    """
    record = record if isinstance(record, dict) else {}
    if record.get("approval") == "routine-policy" or routine is True:
        from . import execution_policy
        path = execution_policy.policy_path(data_dir)
        digest = file_sha256(path) if path.is_file() else ""
        return {"approval_ref": "routine-policy", "policy_sha256": digest}
    if "adhoc=true" in str(record.get("detail") or ""):
        return {"approval_ref": "adhoc", "policy_sha256": ""}
    try:
        from .planlint import governing_plan
        plan, _ = governing_plan(data_dir, campaign)
    except Exception:  # noqa: BLE001 只读推导失败不阻断日志
        plan = None
    if isinstance(plan, dict) and plan.get("plan_id"):
        return {"approval_ref": "plan:%s" % plan["plan_id"], "policy_sha256": ""}
    return {"approval_ref": "", "policy_sha256": ""}


def _normalize_receipt(receipt) -> Dict[str, str]:
    receipt = dict(receipt) if isinstance(receipt, dict) else {}
    return {"id": str(receipt.get("id") or ""),
            "url": str(receipt.get("url") or ""),
            "readback_sha256": str(receipt.get("readback_sha256") or "")}


def _normalize_account(account) -> Dict[str, Any]:
    account = dict(account) if isinstance(account, dict) else {}
    return {
        "platform_user_id": str(account.get("platform_user_id") or ""),
        "handle": str(account.get("handle") or ""),
        "observed_at": str(account.get("observed_at") or ""),
        "proof_ref": str(account.get("proof_ref") or ""),
        **({"switched_from": dict(account["switched_from"])}
           if isinstance(account.get("switched_from"), dict) else {}),
    }


def _normalize_error(error) -> Optional[Dict[str, Any]]:
    if error is None:
        return None
    error = dict(error) if isinstance(error, dict) else {}
    return {"code": str(error.get("code") or "unknown"),
            "retryable": bool(error.get("retryable", False)),
            "recovery_ref": str(error.get("recovery_ref") or "")}


class OperationsLog:
    """data/<workspace>/operations.jsonl 的 append-only 读写门面。"""

    def __init__(self, data_dir):
        self.path = Path(data_dir) / OPERATIONS_LOG_NAME

    def all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        return _read_jsonl_fail_loud(self.path, kind="操作日志")

    def latest(self, *, channel: Optional[str] = None,
               kind: str = "operation") -> Optional[Dict[str, Any]]:
        rows = [r for r in self.all()
                if (channel is None or r.get("channel") == channel)
                and r.get("kind", "operation") == kind]
        return rows[-1] if rows else None

    # ---- 写入（唯一入口：校验 + 账号切换 + 回读终判 + 追加） ----

    def append(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        record = self._validated(dict(entry))
        record = self._mark_account_switch(record)
        record = self._finalize_readback(record)
        _append_line(self.path, json.dumps(record, ensure_ascii=False) + "\n")
        return record

    # ---- 校验：最低字段齐全且类型正确，否则整条拒绝 ----

    def _validated(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        def _fail(problem: str):
            raise OperationsLogError(
                "操作日志记录不完整（%s）：拒绝落半条记录。最低字段清单见 "
                "core/operations_log.MINIMUM_FIELDS；problem=%s"
                % (problem, json.dumps(entry, ensure_ascii=False, default=str)[:400]))

        for field in MINIMUM_FIELDS:
            if field not in entry:
                _fail("缺字段 %s" % field)
        kind = str(entry.get("kind") or "operation")
        for field in ("operation_id", "channel", "action", "started_at",
                      "finished_at", "result", "operator"):
            if not str(entry.get(field) or "").strip():
                _fail("%s 为空" % field)
        for field in ("run_id", "queue_item_id", "target",
                      "content_sha256", "browser_context_ref",
                      "approval_ref", "policy_sha256"):
            if not isinstance(entry.get(field), str):
                _fail("%s 必须是字符串（可为空串）" % field)

        result = str(entry["result"])
        allowed = READBACK_RESULTS if kind == "readback" else OPERATION_RESULTS
        if result not in allowed:
            _fail("result %r 不在 %s 词汇表" % (result, sorted(allowed)))

        content_sha = str(entry["content_sha256"]).lower()
        if content_sha and not _is_sha256(content_sha):
            _fail("content_sha256 不是 64 位十六进制")
        action = str(entry["action"])
        if (kind == "operation"
                and action in CONTENT_BEARING_WRITE_ACTIONS
                and result != "rejected"
                and not content_sha):
            _fail("%s 是带正文的写动作且未被拒绝，必须携带 content_sha256" % action)

        account = _normalize_account(entry["account"])
        if (str(entry["channel"]) == "x" and result == "committed"
                and not account["platform_user_id"]):
            _fail("x 渠道 committed 记录必须携带观测到的 platform_user_id")
        receipt = _normalize_receipt(entry["receipt"])
        if receipt["readback_sha256"] and not _is_sha256(receipt["readback_sha256"]):
            _fail("receipt.readback_sha256 不是 64 位十六进制")
        if entry["policy_sha256"] and not _is_sha256(entry["policy_sha256"]):
            _fail("policy_sha256 不是 64 位十六进制")

        artifacts = entry["artifacts"]
        if not isinstance(artifacts, list):
            _fail("artifacts 必须是数组")
        norm_artifacts = []
        for art in artifacts:
            if not isinstance(art, dict) or not str(art.get("path") or "").strip():
                _fail("artifacts[] 必须是 {path, sha256} 对象")
            path = Path(str(art["path"]))
            digest = str(art.get("sha256") or "").lower()
            if not digest:
                _fail("artifacts[] 缺 sha256")
            if path.is_file() and file_sha256(path) != digest:
                _fail("artifact %s 的 sha256 与文件内容不一致" % path)
            norm_artifacts.append({"path": str(art["path"]), "sha256": digest})
        readback = entry.get("readback")
        if readback is not None and not isinstance(readback, dict):
            _fail("readback 必须是对象 {status, source, note}")

        entry.update({
            "schema": SCHEMA, "kind": kind, "account": account,
            "receipt": receipt, "artifacts": norm_artifacts,
            "error": _normalize_error(entry["error"]),
            "content_sha256": content_sha,
            "policy_sha256": str(entry["policy_sha256"]).lower(),
        })
        if readback is not None:
            entry["readback"] = {
                "status": str(readback.get("status") or "unknown"),
                "source": str(readback.get("source") or ""),
                "note": str(readback.get("note") or "")[:300],
            }
        return entry

    # ---- 账号切换：同渠道上一条 operation 的观测身份不同 → switched_from ----

    def _mark_account_switch(self, record: Dict[str, Any]) -> Dict[str, Any]:
        if record["kind"] != "operation":
            return record
        current = record["account"].get("platform_user_id")
        if not current:
            return record
        prev = self.latest(channel=record["channel"], kind="operation")
        if not prev:
            return record
        prev_account = prev.get("account") or {}
        prev_id = str(prev_account.get("platform_user_id") or "")
        if prev_id and prev_id != current:
            record["account"]["switched_from"] = {
                "platform_user_id": prev_id,
                "handle": str(prev_account.get("handle") or ""),
                "operation_id": str(prev.get("operation_id") or ""),
            }
        return record

    # ---- 回读终判：内容哈希不一致 → indeterminate 停机 ----

    def _finalize_readback(self, record: Dict[str, Any]) -> Dict[str, Any]:
        mismatch = False
        receipt = record["receipt"]
        if (receipt["readback_sha256"] and record["content_sha256"]
                and receipt["readback_sha256"].lower() != record["content_sha256"]):
            mismatch = True
        readback = record.get("readback")
        if isinstance(readback, dict) and readback.get("status") == "mismatched":
            mismatch = True
        if not mismatch:
            return record
        record["result"] = "indeterminate"
        record["halted"] = True
        record["error"] = {
            "code": "readback_mismatch",
            "retryable": False,
            "recovery_ref": "人工在平台后台核对后执行 mkt ledger resolve "
                            "（indeterminate 禁止自动重发）",
        }
        return record


# --------------------------------------------------------------------------- #
# 动作现场 → 操作日志（发送入口共用的落日志路径）
# --------------------------------------------------------------------------- #

_RESULT_ERRORS = {
    "failed": ("action_failed", "mkt grow incident <run> --list；修复后重试"),
    "indeterminate": ("indeterminate", "人工核对平台后台后 mkt ledger resolve"),
    "rejected": ("rejected_by_gate", "按账本 detail 中的下一步处理"),
}


def record_action(data_dir, record: Dict[str, Any], *, channel=None, action=None,
                  target=None, text=None, campaign="", go=False, routine=False,
                  operator="", queue_item_id="", run_id="",
                  started_at=None, finished_at=None) -> Dict[str, Any]:
    """把一条动作回执（账本行）扩写成完整操作日志记录并落盘。

    发送入口（queue_runner / action_runner / mkt x 直通）在动作返回后调用；
    字段从动作现场推导：正文哈希、审批依据、策略哈希、观测账号、工件哈希。
    """
    if not isinstance(record, dict):
        raise OperationsLogError("record 必须是账本记录对象")
    data_dir = Path(data_dir)
    channel = str(channel or record.get("channel") or "").strip().lower()
    action = str(action or record.get("action") or "").strip().lower()
    target = target if target is not None else record.get("target")
    result = str(record.get("result") or "").strip().lower() or "failed"

    text_value = text if text is not None else record.get("text")
    content = content_sha256(text_value) if text_value else ""

    identity = record.get("identity") if isinstance(record.get("identity"), dict) else {}
    account = _normalize_account({
        "platform_user_id": identity.get("observed_platform_identity")
        or record.get("observed_platform_identity") or "",
        "handle": identity.get("handle") or record.get("handle") or "",
        "observed_at": identity.get("observed_at", ""),
        "proof_ref": identity.get("identity_proof_ref")
        or record.get("identity_proof_ref") or "",
    }) if (identity or record.get("observed_platform_identity")) else {}

    approval = approval_evidence(data_dir, campaign=campaign, routine=routine,
                                 record=record)
    evidence_path = str(record.get("evidence") or "")
    artifacts = []
    if evidence_path and Path(evidence_path).is_file():
        artifacts.append({"path": evidence_path,
                          "sha256": file_sha256(evidence_path)})

    code, recovery = _RESULT_ERRORS.get(result, ("", ""))
    error = ({"code": code, "retryable": False, "recovery_ref": recovery}
             if code else None)

    entry = {
        "kind": "operation",
        "operation_id": str(record.get("ref") or "") or (
            "op-%s-%s" % (channel or "ch", uuid.uuid4().hex[:12])),
        "run_id": str(run_id or ""),
        "queue_item_id": str(queue_item_id or record.get("queue_item_id") or ""),
        "channel": channel,
        "action": action,
        "target": str(target or ""),
        "content_sha256": content,
        "browser_context_ref": browser_context_ref()
        or str(identity.get("browser_context") or ""),
        "account": account,
        "operator": str(operator or "growovo:action_runner"),
        "approval_ref": approval["approval_ref"],
        "policy_sha256": approval["policy_sha256"],
        "started_at": str(started_at or record.get("started_at")
                          or record.get("ts") or _now_iso()),
        "finished_at": str(finished_at or record.get("finished_at")
                           or record.get("ts") or _now_iso()),
        "result": result,
        "receipt": _normalize_receipt(record.get("receipt")),
        "artifacts": artifacts,
        "error": error,
    }
    return OperationsLog(data_dir).append(entry)


class LedgerRecorder:  # 兼容 Ledger 接口的记账+捕获包装（mkt x 直通用）
    """包装 Ledger.append：动作期间捕获新增账本行，结束后统一落操作日志。"""

    def __init__(self, ledger):
        self._ledger = ledger
        self.captured: List[Dict[str, Any]] = []

    def __getattr__(self, name):  # 只读方法透传
        return getattr(self._ledger, name)

    def append(self, **kw):
        rec = self._ledger.append(**kw)
        self.captured.append(rec)
        return rec

    def flush_operations(self, data_dir, **ctx) -> List[Dict[str, Any]]:
        out = []
        for rec in self.captured:
            out.append(record_action(data_dir, rec, **ctx))
        return out
