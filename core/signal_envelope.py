#!/usr/bin/env python3
"""信号证据包与变更预演（G-NEXT-3）。

两个对象都只读、只追加、零网络、零外部效果：

- ``growovo.signal-envelope/1``：把已有信号封成证据卡。信号只证明对应来源在
  对应时间出现过对应内容，不证明身份、意图、需求或购买倾向。
- ``growovo.mutation-preview/1``：基于当前 plan 的 dry-run 差异。preview 本身
  不产生审批、不写队列、不执行；正式执行仍走既有 plan/approval 指纹。

身份未可靠解析时保持 unresolved；相似名称、域名或时间不自动合并。来源撤销、
信号过期、用途越界、preview 基准漂移、外部效果未知都阻止正式使用；外部效果
未知只能走 ``mkt ledger resolve`` 与 ``--after-reconciliation``，不提供 retry。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import approval
from .journey import normalize_identity
from .ledger import Ledger, LedgerCorruptError, _append_line, _read_jsonl_fail_loud

SIGNAL_SCHEMA = "growovo.signal-envelope/1"
PREVIEW_SCHEMA = "growovo.mutation-preview/1"
VIEW_SCHEMA = "growovo.signal-view/1"
REVOCATION_SCHEMA = "growovo.signal-revocation/1"
CONFLICT_SCHEMA = "growovo.signal-conflict/1"

ALLOWED_USES = ("personalization", "segmentation", "analysis")
IDENTITY_BASES = ("verified_email", "account_id", "explicit_map")
CHANGE_OPS = ("add", "modify", "remove", "unchanged")
AFFECTED_KINDS = ("account", "queue", "content", "quota", "workspace", "policy")

DEFAULT_MAX_AGE_DAYS = 30
DEFAULT_PREVIEW_HOURS = 24
DEFAULT_PERMISSIONS = ("plan.approve", "plan.execute")

EVIDENCE_BOUNDARY = ("信号只证明对应来源在对应时间出现的内容，不证明身份、意图、"
                     "需求或购买倾向；预演只读，不产生审批，不写队列，不执行。")
INJECTION_NOTE = "外部文本只作为数据展示，不改变权限、变更范围或审批状态。"


class SignalError(ValueError):
    """信号或预演输入不成立：拒绝登记而不是猜测补齐。"""


class SignalStoreError(SignalError):
    """信号/预演存储读取失败：不把坏数据当成没有数据。"""


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

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


def _parse_dt(value) -> Optional[datetime]:
    """解析 ISO/日期字符串；带时区统一换到 UTC 朴素时间，取不到返回 None。"""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        text = _text(value)
        if not text:
            return None
        probe = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            dt = datetime.fromisoformat(probe)
        except ValueError:
            try:
                dt = datetime.strptime(text[:10], "%Y-%m-%d")
            except ValueError:
                return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _now_dt(now=None) -> datetime:
    parsed = _parse_dt(now) if now is not None else None
    return parsed or datetime.now()


def _append(path: Path, rec: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _append_line(path, json.dumps(rec, ensure_ascii=False) + "\n")


def _read(path: Path, kind: str) -> List[Dict[str, Any]]:
    try:
        return _read_jsonl_fail_loud(path, kind=kind)
    except Exception as exc:  # noqa: BLE001 统一转成可展示的存储错误
        raise SignalStoreError("%s读取失败：%s" % (kind, exc)) from exc


def _paths(data_dir) -> Dict[str, Path]:
    root = Path(data_dir) / "signals"
    return {
        "root": root,
        "signals": root / "signal-envelopes.jsonl",
        "revocations": root / "source-revocations.jsonl",
        "previews": root / "mutation-previews.jsonl",
        "conflicts": root / "signal-conflicts.jsonl",
    }


def _dedupe(values) -> List[str]:
    out = []
    for value in values or []:
        text = _text(value)
        if text and text not in out:
            out.append(text)
    return out


# ---------------------------------------------------------------------------
# 信号证据包
# ---------------------------------------------------------------------------

def _freshness(observed_at, max_age_days: int, now=None) -> Dict[str, Any]:
    text = _text(observed_at)
    max_age = int(max_age_days)
    if not text:
        return {"state": "unknown",
                "reason": "信号缺观察时间：不进入新内容",
                "observed_at": "", "expires_at": "", "age_days": None,
                "max_age_days": max_age}
    observed = _parse_dt(text)
    if observed is None:
        return {"state": "unknown",
                "reason": "观察时间无法解析：不进入新内容",
                "observed_at": text, "expires_at": "", "age_days": None,
                "max_age_days": max_age}
    current = _now_dt(now)
    expires = observed + timedelta(days=max_age)
    age = (current - observed).total_seconds() / 86400.0
    if current > expires:
        state = "stale"
        reason = "信号已过期：观察于 %s，最大 %s 天，%s 失效" % (
            observed.isoformat(timespec="seconds"), max_age,
            expires.isoformat(timespec="seconds"))
    else:
        state = "fresh"
        reason = ""
    return {"state": state, "reason": reason, "observed_at": text,
            "expires_at": expires.isoformat(timespec="seconds"),
            "age_days": round(age, 2), "max_age_days": max_age}


def _identity(subject_ref, identity_basis, identity_map) -> Dict[str, Any]:
    raw = _text(subject_ref)
    basis = _text(identity_basis)
    normalized = normalize_identity(raw, identity_map) if raw else ""
    if not raw:
        return {"state": "unresolved", "basis": basis, "normalized": "",
                "reason": "未登记主体：保持 unresolved"}
    if basis not in IDENTITY_BASES:
        return {"state": "unresolved", "basis": basis, "normalized": normalized,
                "reason": "身份依据不足（需要 %s 之一）：保持 unresolved，"
                          "不按相似名称或域名合并" % "/".join(IDENTITY_BASES)}
    if basis == "verified_email" and ("@" not in raw
                                      or any(ch.isspace() for ch in raw)):
        return {"state": "unresolved", "basis": basis, "normalized": "",
                "reason": "身份依据为 verified_email，但主体不是邮箱形态："
                          "保持 unresolved，不按相似名称或域名合并"}
    if not normalized:
        return {"state": "unresolved", "basis": basis, "normalized": "",
                "reason": "主体归一后为空：保持 unresolved"}
    return {"state": "resolved", "basis": basis, "normalized": normalized,
            "reason": ""}


def _permissions(allowed_use, consent_ref, state: str, *,
                 revoked: bool = False,
                 revoke_reason: str = "") -> Dict[str, Any]:
    """逐用途给出许可；任何一条不满足都拒绝并写清原因。"""
    uses = set(allowed_use or [])
    out: Dict[str, Any] = {}
    blocked: List[str] = []
    for use in ALLOWED_USES:
        reasons: List[str] = []
        if revoked:
            reasons.append("来源已撤销%s" % (
                "：%s" % revoke_reason if revoke_reason else ""))
        if state == "stale":
            reasons.append("信号已过期")
        if state == "incomplete":
            reasons.append("信号证据不完整")
        if state == "revoked" and not revoked:
            reasons.append("来源已撤销")
        if not uses:
            reasons.append("未声明允许用途（allowed_use 为空）")
        elif use not in uses:
            reasons.append("允许用途（allowed_use）不含 %s" % use)
        if use == "personalization" and not _text(consent_ref):
            reasons.append("缺授权引用（consent_ref）：不允许个性化")
        allowed = not reasons
        out[use] = {"allowed": allowed,
                    "reason": "" if allowed else "；".join(reasons)}
        if not allowed:
            blocked.append(use)
    out["blocked"] = blocked
    return out


def signal_fingerprint(envelope: Dict[str, Any]) -> str:
    """业务指纹：时间戳、新鲜度和展示摘要不参与，重复导入同一信号不算冲突。"""
    keys = ("schema", "signal_id", "source_family", "source_ref", "observed_at",
            "subject_ref", "subject_normalized", "identity", "raw_hash",
            "allowed_use", "consent_ref", "content_claim_refs", "run_id")
    return _fingerprint({key: (envelope or {}).get(key) for key in keys})


def build_signal(*, signal_id: str = "", source_family: str, source_ref: str,
                 observed_at: str = "", imported_at=None, raw_text: str = "",
                 raw_hash: str = "", subject_ref: str = "",
                 identity_basis: str = "", identity_map: Any = None,
                 allowed_use: Optional[List[str]] = None,
                 consent_ref: str = "", content_claim_refs=None,
                 max_age_days: int = DEFAULT_MAX_AGE_DAYS,
                 run_id: str = "", note: str = "", now=None) -> Dict[str, Any]:
    family = _text(source_family)
    ref = _text(source_ref)
    if not family:
        raise SignalError("source_family 不能为空：信号必须能回到来源族")
    if not ref:
        raise SignalError("source_ref 不能为空：信号必须能回到原始定位")
    try:
        max_age = int(max_age_days)
    except (TypeError, ValueError):
        raise SignalError("max_age_days 必须是正整数") from None
    if max_age <= 0:
        raise SignalError("max_age_days 必须大于 0")

    uses = _dedupe(ALLOWED_USES if allowed_use is None else allowed_use)
    unknown = [use for use in uses if use not in ALLOWED_USES]
    if unknown:
        raise SignalError("未知允许用途 %s：只接受 %s"
                          % (", ".join(unknown), "/".join(ALLOWED_USES)))
    if allowed_use is None:
        uses = ["analysis"]

    observed = _text(observed_at)
    fresh = _freshness(observed, max_age, now)
    identity = _identity(subject_ref, identity_basis, identity_map)
    digest = _text(raw_hash) or "sha256:" + _sha(raw_text)
    claims = _dedupe(content_claim_refs)
    consent = _text(consent_ref)

    gaps: List[str] = []
    if not observed:
        gaps.append("missing_observed_at")
    elif fresh["state"] == "unknown":
        gaps.append("unparseable_observed_at")
    if fresh["state"] == "stale":
        gaps.append("stale_observed_at")
    if not uses:
        gaps.append("use_not_allowed")
    if "personalization" in uses and not consent:
        gaps.append("missing_consent_ref")
    if identity["state"] != "resolved":
        gaps.append("identity_unresolved")
    if not _text(raw_text) and not _text(raw_hash):
        gaps.append("missing_raw")

    state = "usable"
    if fresh["state"] == "unknown":
        state = "incomplete"
    elif fresh["state"] == "stale":
        state = "stale"

    imported = _text(imported_at) or _now(now)
    sid = _text(signal_id) or "sig-" + _sha(_canonical(
        [family, ref, observed]))[:16]

    return {
        "schema": SIGNAL_SCHEMA,
        "signal_id": sid,
        "source_family": family,
        "source_ref": ref,
        "observed_at": observed,
        "imported_at": imported,
        "created_at": imported,
        "freshness": fresh,
        "subject_ref": _text(subject_ref),
        "subject_normalized": identity["normalized"],
        "identity_basis": _text(identity_basis),
        "identity": identity,
        "raw_hash": digest,
        "raw_excerpt": _one_line(raw_text, 280),
        "allowed_use": uses,
        "consent_ref": consent,
        "content_claim_refs": claims,
        "permission": _permissions(uses, consent, state),
        "state": state,
        "gaps": gaps,
        "run_id": _text(run_id),
        "note": _text(note),
        "evidence_boundary": EVIDENCE_BOUNDARY,
        "injection_note": INJECTION_NOTE,
    }


def load_signals(data_dir) -> List[Dict[str, Any]]:
    return [row for row in _read(_paths(data_dir)["signals"], "信号库")
            if isinstance(row, dict)]


def get_signal(data_dir, signal_id: str) -> Optional[Dict[str, Any]]:
    target = _text(signal_id)
    for row in load_signals(data_dir):
        if row.get("signal_id") == target:
            return row
    return None


def write_signal(data_dir, envelope: Dict[str, Any], *,
                 dry_run: bool = True) -> Dict[str, Any]:
    if not isinstance(envelope, dict):
        raise SignalError("信号必须是对象")
    signal_id = _text(envelope.get("signal_id"))
    if not signal_id:
        raise SignalError("信号缺 signal_id：拒绝登记无名证据")
    paths = _paths(data_dir)
    fingerprint = signal_fingerprint(envelope)
    existing = [row for row in load_signals(data_dir)
                if row.get("signal_id") == signal_id]
    if existing:
        old = existing[-1]
        old_fingerprint = signal_fingerprint(old)
        if old_fingerprint == fingerprint:
            return {"status": "unchanged", "signal_id": signal_id,
                    "fingerprint": fingerprint, "path": str(paths["signals"])}
        conflict = {
            "schema": CONFLICT_SCHEMA,
            "kind": "signal",
            "id": signal_id,
            "at": _now(),
            "existing_fingerprint": old_fingerprint,
            "incoming_fingerprint": fingerprint,
            "existing_source": {"family": old.get("source_family"),
                                "ref": old.get("source_ref"),
                                "observed_at": old.get("observed_at")},
            "incoming_source": {"family": envelope.get("source_family"),
                                "ref": envelope.get("source_ref"),
                                "observed_at": envelope.get("observed_at")},
            "reason": "同 signal_id 不同内容：不覆盖，保留旧记录",
        }
        if not dry_run:
            _append(paths["conflicts"], conflict)
        raise SignalError(
            "信号 %s 已存在且内容不同（%s ≠ %s）：不覆盖；请换 signal_id 或核对来源"
            % (signal_id, old_fingerprint, fingerprint))
    if dry_run:
        return {"status": "would_write", "signal_id": signal_id,
                "fingerprint": fingerprint, "path": str(paths["signals"]),
                "conflict": False}
    _append(paths["signals"], envelope)
    return {"status": "written", "signal_id": signal_id,
            "fingerprint": fingerprint, "path": str(paths["signals"])}


def revoke_source(data_dir, *, source_family: str, source_ref: str, by: str,
                  reason: str = "", dry_run: bool = True) -> Dict[str, Any]:
    family = _text(source_family)
    ref = _text(source_ref)
    actor = _text(by)
    why = _text(reason)
    if not family or not ref:
        raise SignalError("撤销来源需要 source_family 与 source_ref")
    if not actor:
        raise SignalError("撤销来源需要 --by 署名")
    if not why:
        raise SignalError("撤销来源需要原因（--reason）")
    key = _sha(_canonical([family, ref]))[:16]
    revocation_id = "rev-" + key
    existing = [row for row in load_revocations(data_dir)
                if row.get("source_family") == family
                and row.get("source_ref") == ref]
    if existing:
        return {"status": "unchanged", "revocation_id":
                existing[-1].get("revocation_id") or revocation_id,
                "path": str(_paths(data_dir)["revocations"])}
    record = {
        "schema": REVOCATION_SCHEMA,
        "revocation_id": revocation_id,
        "source_family": family,
        "source_ref": ref,
        "by": actor,
        "reason": why,
        "at": _now(),
        "boundary": "撤销只作用于信号可用于新内容的判定；不删除历史，也不改账本。",
    }
    path = _paths(data_dir)["revocations"]
    if dry_run:
        return {"status": "would_revoke", "revocation_id": revocation_id,
                "path": str(path), "revocation": record}
    _append(path, record)
    return {"status": "revoked", "revocation_id": revocation_id,
            "path": str(path), "revocation": record}


def load_revocations(data_dir) -> List[Dict[str, Any]]:
    return [row for row in _read(_paths(data_dir)["revocations"], "来源撤销库")
            if isinstance(row, dict)]


def content_reference(signal: Dict[str, Any], *, claim_ref: str) -> Dict[str, Any]:
    """把一条信号绑定到一条个性化论断；不允许时仍给可追溯字段和原因。"""
    claim = _text(claim_ref)
    if not claim:
        raise SignalError("claim_ref 不能为空：内容引用必须能定位到具体论断")
    signal = signal if isinstance(signal, dict) else {}
    state = _text(signal.get("state")) or "incomplete"
    permission = signal.get("permission")
    if not isinstance(permission, dict):
        permission = _permissions(signal.get("allowed_use") or [],
                                  signal.get("consent_ref") or "", state)
    personal = permission.get("personalization") or {}
    allowed = bool(personal.get("allowed")) and state == "usable"
    reasons = []
    if state != "usable":
        reasons.append("信号状态为 %s" % state)
    if not personal.get("allowed"):
        reasons.append(_text(personal.get("reason"))
                       or "个性化用途未获许可")
    return {
        "claim_ref": claim,
        "signal_id": signal.get("signal_id"),
        "source_family": signal.get("source_family"),
        "source_ref": signal.get("source_ref"),
        "observed_at": signal.get("observed_at"),
        "raw_hash": signal.get("raw_hash"),
        "freshness": signal.get("freshness") or {},
        "identity_state": (signal.get("identity") or {}).get("state")
                          or "unresolved",
        "allowed_use": signal.get("allowed_use") or [],
        "permission": permission,
        "allowed": allowed,
        "reason": "" if allowed else "；".join(reasons),
        "raw_excerpt": signal.get("raw_excerpt") or "",
        "boundary": EVIDENCE_BOUNDARY,
    }


# ---------------------------------------------------------------------------
# 变更预演
# ---------------------------------------------------------------------------

def _queue_signature(plan: Dict[str, Any], data_dir) -> str:
    """与 approval._queue_payloads 同一路径解析：queue 内容变则指纹变。"""
    rows = []
    for wave in (plan or {}).get("waves") or []:
        ref = _text((wave or {}).get("queue"))
        if not ref:
            continue
        candidates = [Path(ref)]
        if not Path(ref).is_absolute():
            candidates.extend([Path(data_dir) / ref,
                               Path(data_dir) / "queues" / Path(ref).name])
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            rows.append({"path": ref, "missing": True, "items": []})
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SignalError("队列 %s 读取失败：%s" % (ref, exc)) from exc
        items = payload.get("items") or []
        rows.append({
            "path": str(path.resolve()),
            "items": sorted(items, key=lambda row: json.dumps(
                row, ensure_ascii=False, sort_keys=True)),
        })
    return _fingerprint(rows)


def _normalize_changes(changes) -> List[Dict[str, Any]]:
    if not isinstance(changes, (list, tuple)) or not changes:
        raise SignalError("preview 至少需要一项 changes")
    out = []
    for raw in changes:
        if isinstance(raw, str):
            op, _, rest = raw.partition(":")
            kind, _, tail = rest.partition(":")
            ref, _, summary = tail.partition(":")
            raw = {"op": op, "kind": kind, "ref": ref, "summary": summary}
        if not isinstance(raw, dict):
            raise SignalError("change 必须是对象或 op:kind:ref[:summary]")
        op = _text(raw.get("op"))
        kind = _text(raw.get("kind"))
        ref = _text(raw.get("ref"))
        if op not in CHANGE_OPS:
            raise SignalError("未知变更操作 %r：只接受 %s"
                              % (op, "/".join(CHANGE_OPS)))
        if kind not in AFFECTED_KINDS:
            raise SignalError("未知影响对象类型 %r：只接受 %s"
                              % (kind, "/".join(AFFECTED_KINDS)))
        if not ref:
            raise SignalError("change 缺 ref：拒绝无名变更")
        out.append({
            "op": op,
            "kind": kind,
            "ref": ref,
            "summary": _one_line(raw.get("summary") or "", 300),
            "before": _one_line(raw.get("before") or "", 400),
            "after": _one_line(raw.get("after") or "", 400),
        })
    out.sort(key=lambda row: (row["op"], row["kind"], row["ref"]))
    return out


def _affected_objects(changes, supplied) -> List[Dict[str, Any]]:
    objects: List[Dict[str, Any]] = []
    if supplied:
        for raw in supplied:
            if isinstance(raw, str):
                kind, _, ref = raw.partition(":")
                raw = {"kind": kind, "ref": ref}
            if not isinstance(raw, dict):
                raise SignalError("affected_objects 必须是对象或 kind:ref")
            kind = _text(raw.get("kind"))
            ref = _text(raw.get("ref"))
            if kind not in AFFECTED_KINDS or not ref:
                raise SignalError("affected_objects 需要 kind（%s 之一）与 ref"
                                  % "/".join(AFFECTED_KINDS))
            objects.append({"kind": kind, "ref": ref})
    else:
        objects = [{"kind": row["kind"], "ref": row["ref"]} for row in changes]
    seen = set()
    out = []
    for obj in sorted(objects, key=lambda row: (row["kind"], row["ref"])):
        key = (obj["kind"], obj["ref"])
        if key not in seen:
            seen.add(key)
            out.append(obj)
    return out


def _components(plan: Dict[str, Any], data_dir, *, account=None,
                workspace=None) -> Dict[str, str]:
    return {
        "plan": "sha256:" + approval.fingerprint(plan),
        "queue": _queue_signature(plan, data_dir),
        "account": _fingerprint(str(account or plan.get("account") or "default")),
        "workspace": _fingerprint(str(
            workspace or plan.get("workspace") or Path(data_dir).resolve())),
    }


def build_preview(data_dir, *, plan_id: str, changes,
                  affected_objects=None, permission_required=None,
                  approval_ref: str = "", created_by: str = "",
                  expires_at: str = "", run_id: str = "", note: str = "",
                  now=None) -> Dict[str, Any]:
    plan, _path = approval.find_plan(data_dir, _text(plan_id))
    if plan is None:
        raise SignalError("没有这个计划：%s" % _text(plan_id))
    try:
        exec_hex = approval.execution_fingerprint(plan, data_dir=data_dir)
    except approval.ApprovalError as exc:
        raise SignalError("无法绑定计划执行内容：%s" % exc) from exc
    current = _now_dt(now)
    expires = _text(expires_at) or (
        current + timedelta(hours=DEFAULT_PREVIEW_HOURS)
    ).isoformat(timespec="seconds")
    normalized = _normalize_changes(changes)
    affected = _affected_objects(normalized, affected_objects)
    permissions = _dedupe(permission_required) or list(DEFAULT_PERMISSIONS)
    components = _components(plan, data_dir)
    counts = {op: sum(1 for row in normalized if row["op"] == op)
              for op in CHANGE_OPS}
    preview_id = "pv-" + _sha(_canonical([
        _text(plan_id),
        "sha256:" + exec_hex,
        normalized,
        affected,
        permissions,
    ]))[:16]
    approval_state = "referenced" if _text(approval_ref) else "none"
    return {
        "schema": PREVIEW_SCHEMA,
        "preview_id": preview_id,
        "plan_id": _text(plan_id),
        "run_id": _text(run_id),
        "base_plan_fingerprint": "sha256:" + approval.fingerprint(plan),
        "base_execution_fingerprint": "sha256:" + exec_hex,
        "base_components": components,
        "changes": normalized,
        "counts": counts,
        "affected_objects": affected,
        "permission_required": permissions,
        "approval_ref": _text(approval_ref),
        "approval_state": approval_state,
        "dry_run": True,
        "external_effect": "none",
        "delete_count": counts.get("remove", 0),
        "expires_at": expires,
        "stale_reason": "",
        "created_at": current.isoformat(timespec="seconds"),
        "created_by": _text(created_by),
        "note": _text(note),
        "boundary": ("preview 是只读预演：dry_run=true、external_effect=none；"
                     "不产生审批、不写队列、不执行。正式执行仍需 plan 审批。"),
        "injection_note": INJECTION_NOTE,
    }


def preview_fingerprint(preview: Dict[str, Any]) -> str:
    keys = ("schema", "preview_id", "plan_id", "run_id",
            "base_plan_fingerprint", "base_execution_fingerprint",
            "changes", "affected_objects", "permission_required",
            "approval_ref", "expires_at")
    return _fingerprint({key: (preview or {}).get(key) for key in keys})


def load_previews(data_dir) -> List[Dict[str, Any]]:
    return [row for row in _read(_paths(data_dir)["previews"], "变更预演库")
            if isinstance(row, dict)]


def get_preview(data_dir, preview_id: str) -> Optional[Dict[str, Any]]:
    target = _text(preview_id)
    for row in load_previews(data_dir):
        if row.get("preview_id") == target:
            return row
    return None


def write_preview(data_dir, preview: Dict[str, Any], *,
                  dry_run: bool = True) -> Dict[str, Any]:
    if not isinstance(preview, dict):
        raise SignalError("preview 必须是对象")
    preview_id = _text(preview.get("preview_id"))
    if not preview_id:
        raise SignalError("preview 缺 preview_id")
    if preview.get("dry_run") is not True or \
            _text(preview.get("external_effect")) != "none":
        raise SignalError("preview 必须 dry_run=true 且 external_effect=none")
    paths = _paths(data_dir)
    fingerprint = preview_fingerprint(preview)
    existing = [row for row in load_previews(data_dir)
                if row.get("preview_id") == preview_id]
    if existing:
        old = existing[-1]
        old_fingerprint = preview_fingerprint(old)
        if old_fingerprint == fingerprint:
            return {"status": "unchanged", "preview_id": preview_id,
                    "fingerprint": fingerprint, "path": str(paths["previews"])}
        conflict = {
            "schema": CONFLICT_SCHEMA,
            "kind": "preview",
            "id": preview_id,
            "at": _now(),
            "existing_fingerprint": old_fingerprint,
            "incoming_fingerprint": fingerprint,
            "reason": "同 preview_id 不同内容：不覆盖，保留旧记录",
        }
        if not dry_run:
            _append(paths["conflicts"], conflict)
        raise SignalError("预演 %s 已存在且内容不同：不覆盖" % preview_id)
    if dry_run:
        return {"status": "would_write", "preview_id": preview_id,
                "fingerprint": fingerprint, "path": str(paths["previews"])}
    _append(paths["previews"], preview)
    return {"status": "written", "preview_id": preview_id,
            "fingerprint": fingerprint, "path": str(paths["previews"])}


def _incidents(data_dir, *, limit: int = 20) -> List[Dict[str, Any]]:
    try:
        rows = Ledger(Path(data_dir) / "ledger.jsonl").all()
    except (LedgerCorruptError, OSError):
        return [{
            "kind": "ledger_unreadable",
            "ref": "ledger.jsonl",
            "next_command": "人工检查账本后再恢复；不要用 retry 绕过未知外部效果",
        }]
    out = []
    for index, rec in enumerate(rows):
        if not isinstance(rec, dict):
            continue
        if _text(rec.get("result")) != "indeterminate":
            continue
        ref = _text(rec.get("ref")) or "ledger.jsonl#%d" % index
        out.append({
            "kind": "indeterminate_external_action",
            "ref": ref,
            "channel": rec.get("channel"),
            "target": rec.get("target"),
            "at": rec.get("ts"),
            "next_command": (
                "mkt ledger resolve %s --result <committed|failed> "
                "--by <name> --note <依据>；再 mkt grow resume "
                "--after-reconciliation" % ref),
            "boundary": "外部效果未知时禁止 retry：先人工裁决，再按账本恢复。",
        })
    return out[-limit:]


def check_preview(data_dir, preview: Dict[str, Any], *, account=None,
                  workspace=None, now=None) -> Dict[str, Any]:
    """重算基准指纹；preview 后任何分量变化都标 stale。"""
    preview = preview if isinstance(preview, dict) else {}
    base = preview.get("base_components") or {}
    reasons: List[str] = []
    plan, _path = approval.find_plan(data_dir, _text(preview.get("plan_id")))
    if plan is None:
        return {
            "state": "stale",
            "reasons": ["基准计划不存在：预演失效，必须重新生成"],
            "plan_changed": True,
            "queue_changed": None,
            "account_changed": None,
            "workspace_changed": None,
            "approval_state": preview.get("approval_state") or "none",
            "approval_valid": False if preview.get("approval_ref") else None,
            "approval_reason": "基准计划缺失，无法核对审批",
            "incidents": _incidents(data_dir),
            "injection_note": INJECTION_NOTE,
        }
    current = _components(plan, data_dir, account=account,
                          workspace=workspace)
    plan_changed = current["plan"] != _text(base.get("plan"))
    queue_changed = current["queue"] != _text(base.get("queue"))
    account_changed = current["account"] != _text(base.get("account"))
    workspace_changed = current["workspace"] != _text(base.get("workspace"))
    plan_hex = _text(preview.get("base_plan_fingerprint"))
    if plan_changed:
        reasons.append("基准计划在预演后发生变化（基准 %s，当前 %s）"
                       % (plan_hex or "未登记", current["plan"]))
    if queue_changed:
        reasons.append("队列内容在预演后发生变化")
    if account_changed:
        reasons.append("发送账号在预演后发生变化")
    if workspace_changed:
        reasons.append("工作区在预演后发生变化")

    expires = _parse_dt(preview.get("expires_at"))
    expired = False
    if expires is not None and _now_dt(now) > expires:
        expired = True
        reasons.append("预演已于 %s 过期" % preview.get("expires_at"))
    elif expires is None and _text(preview.get("expires_at")):
        expired = True
        reasons.append("预演有效期无法解析：按过期处理")

    state = "expired" if expired else ("stale" if reasons else "current")

    approval_state = "referenced" if _text(preview.get("approval_ref")) \
        else "none"
    approval_valid: Optional[bool] = None
    approval_reason = ""
    if approval_state == "none":
        approval_reason = ("preview 本身不产生审批：正式执行仍需 "
                           "mkt plan approve 的有效指纹")
    else:
        try:
            approval.verify_approval(
                plan, data_dir=data_dir, account=account,
                workspace=workspace or plan.get("workspace"))
            approval_valid = True
            approval_reason = "既有审批与当前执行内容一致"
        except approval.ApprovalError as exc:
            approval_valid = False
            approval_reason = "%s" % exc

    return {
        "state": state,
        "reasons": reasons,
        "plan_changed": plan_changed,
        "queue_changed": queue_changed,
        "account_changed": account_changed,
        "workspace_changed": workspace_changed,
        "approval_state": approval_state,
        "approval_valid": approval_valid,
        "approval_reason": approval_reason,
        "incidents": _incidents(data_dir),
        "injection_note": INJECTION_NOTE,
        "boundary": ("检查只读：不批准、不写队列、不执行、不 retry；"
                     "外部效果未知先走 mkt ledger resolve。"),
    }


# ---------------------------------------------------------------------------
# 只读视图与终端渲染
# ---------------------------------------------------------------------------

def _revocation_key(family, ref) -> str:
    return _canonical([_text(family), _text(ref)])


def signal_view(data_dir, *, run_id: str = "", now=None) -> Dict[str, Any]:
    """运行页/CLI 共用的只读投影；撤销与新鲜度在这里叠加。"""
    signals = load_signals(data_dir)
    revocations = load_revocations(data_dir)
    previews = load_previews(data_dir)
    conflicts = _read(_paths(data_dir)["conflicts"], "信号冲突库")
    if _text(run_id):
        signals = [row for row in signals
                   if _text(row.get("run_id")) == _text(run_id)]
        previews = [row for row in previews
                    if _text(row.get("run_id")) == _text(run_id)]
        conflicts = [row for row in conflicts
                     if not row.get("run_id")
                     or _text(row.get("run_id")) == _text(run_id)]
    revoked = {_revocation_key(rec.get("source_family"),
                               rec.get("source_ref")): rec
               for rec in revocations if isinstance(rec, dict)}

    rows = []
    counts = {"total": 0, "usable": 0, "stale": 0, "revoked": 0,
              "incomplete": 0, "unresolved_identity": 0}
    stale: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        max_age = int((signal.get("freshness") or {}).get(
            "max_age_days") or DEFAULT_MAX_AGE_DAYS)
        fresh = _freshness(signal.get("observed_at"), max_age, now)
        rev = revoked.get(_revocation_key(signal.get("source_family"),
                                          signal.get("source_ref")))
        base_state = signal.get("state") or "incomplete"
        if fresh["state"] == "unknown":
            base_state = "incomplete"
        elif fresh["state"] == "stale":
            base_state = "stale"
        if rev:
            state = "revoked"
            reason = "来源已撤销：%s" % _text(rev.get("reason"))
        elif base_state == "incomplete":
            state = "incomplete"
            reason = fresh.get("reason") or "信号证据不完整"
        elif base_state == "stale":
            state = "stale"
            reason = fresh.get("reason") or "信号已过期"
        else:
            state = "usable"
            reason = ""
        permission = _permissions(signal.get("allowed_use") or [],
                                  signal.get("consent_ref") or "",
                                  state,
                                  revoked=bool(rev),
                                  revoke_reason=_text(rev.get("reason")) if rev else "")
        row = dict(signal)
        row["freshness"] = fresh
        row["state"] = state
        row["state_reason"] = reason
        row["permission"] = permission
        row["revocation"] = ({"revocation_id": rev.get("revocation_id"),
                              "by": rev.get("by"),
                              "reason": rev.get("reason"),
                              "at": rev.get("at")} if rev else {})
        rows.append(row)

        counts["total"] += 1
        counts[state if state in counts else "incomplete"] += 1
        if (signal.get("identity") or {}).get("state") != "resolved":
            counts["unresolved_identity"] += 1
        if state == "stale":
            stale.append({"signal_id": signal.get("signal_id"),
                          "reason": reason})
        if row.get("gaps"):
            gaps.append({"signal_id": signal.get("signal_id"),
                         "gaps": row["gaps"]})

    preview_rows = []
    preview_stale: List[Dict[str, Any]] = []
    delete_items: List[Dict[str, Any]] = []
    for preview in previews:
        if not isinstance(preview, dict):
            continue
        row = dict(preview)
        try:
            row["check"] = check_preview(data_dir, preview, now=now)
        except Exception as exc:  # noqa: BLE001 页面不因单条预演崩掉
            row["check"] = {"state": "unreadable",
                            "reasons": ["%s: %s" % (type(exc).__name__, exc)],
                            "incidents": []}
        check = row.get("check") or {}
        if check.get("state") in ("stale", "expired"):
            preview_stale.append({"preview_id": preview.get("preview_id"),
                                  "reason": "；".join(check.get("reasons") or [])})
        for change in preview.get("changes") or []:
            if isinstance(change, dict) and change.get("op") == "remove":
                delete_items.append({
                    "preview_id": preview.get("preview_id"),
                    "kind": change.get("kind"),
                    "ref": change.get("ref"),
                    "summary": change.get("summary") or "",
                })
        preview_rows.append(row)

    incidents: List[Dict[str, Any]] = []
    for row in preview_rows:
        for incident in (row.get("check") or {}).get("incidents") or []:
            if incident not in incidents:
                incidents.append(incident)

    has_data = bool(rows or preview_rows)
    return {
        "schema": VIEW_SCHEMA,
        "state": "ok" if has_data else "empty",
        "run_id": _text(run_id),
        "signals": rows,
        "previews": preview_rows,
        "counts": counts,
        "stale": stale,
        "gaps": gaps,
        "preview_stale": preview_stale,
        "delete_items": delete_items,
        "preview_count": len(preview_rows),
        "delete_count": sum(int(row.get("delete_count") or 0)
                            for row in preview_rows),
        "conflicts": [row for row in conflicts if isinstance(row, dict)],
        "incidents": incidents,
        "injection_note": INJECTION_NOTE,
        "note": ("信号与预演来自本地证据库；缺失显示空/UNKNOWN，"
                 "不补 0、不补 PASS"
                 if has_data else
                 "还没有信号：用 mkt grow signal add 登记来源与观察时间"),
        "paths": {key: str(path) for key, path in _paths(data_dir).items()},
    }


def render_signal_text(signal: Dict[str, Any]) -> str:
    signal = signal if isinstance(signal, dict) else {}
    identity = signal.get("identity") or {}
    fresh = signal.get("freshness") or {}
    personal = (signal.get("permission") or {}).get("personalization") or {}
    lines = [
        "信号 %s" % (signal.get("signal_id") or "未登记"),
        "  来源：%s / %s" % (signal.get("source_family") or "未登记",
                            signal.get("source_ref") or "未登记"),
        "  观察 / 导入：%s / %s" % (signal.get("observed_at") or "未登记",
                                   signal.get("imported_at") or "未登记"),
        "  新鲜度：%s（%s）" % (fresh.get("state") or "unknown",
                               fresh.get("reason") or "可用"),
        "  身份：%s（%s）%s" % (
            identity.get("state") or "unresolved",
            identity.get("basis") or "未登记依据",
            identity.get("reason") or ""),
        "  允许用途：%s" % (", ".join(signal.get("allowed_use") or [])
                            or "未声明"),
        "  个性化：%s%s" % (
            "允许" if personal.get("allowed") else "拒绝",
            "（%s）" % personal.get("reason")
            if personal.get("reason") else ""),
        "  内容引用：%s" % (", ".join(signal.get("content_claim_refs") or [])
                            or "暂无"),
        "  原文指纹：%s" % (signal.get("raw_hash") or "未登记"),
    ]
    if signal.get("gaps"):
        lines.append("  证据缺口：%s" % ", ".join(signal.get("gaps") or []))
    lines.append("  边界：%s" % (signal.get("evidence_boundary")
                                 or EVIDENCE_BOUNDARY))
    return "\n".join(lines)


def render_preview_text(preview: Dict[str, Any],
                        check: Optional[Dict[str, Any]] = None) -> str:
    preview = preview if isinstance(preview, dict) else {}
    counts = preview.get("counts") or {}
    lines = [
        "预演 %s（plan %s）" % (preview.get("preview_id") or "未登记",
                               preview.get("plan_id") or "未登记"),
        "  基准计划指纹：%s" % (preview.get("base_plan_fingerprint")
                               or "未登记"),
        "  基准执行指纹：%s" % (preview.get("base_execution_fingerprint")
                               or "未登记"),
        "  变更：新增 %s / 修改 %s / 删除 %s / 不变 %s"
        % (counts.get("add", 0), counts.get("modify", 0),
           counts.get("remove", 0), counts.get("unchanged", 0)),
        "  所需权限：%s" % ", ".join(preview.get("permission_required") or []),
        "  审批引用：%s（%s）" % (preview.get("approval_ref") or "无",
                                 preview.get("approval_state") or "none"),
        "  预演性质：dry_run=%s，external_effect=%s，有效期至 %s"
        % (preview.get("dry_run"), preview.get("external_effect"),
           preview.get("expires_at") or "未登记"),
    ]
    removes = [row for row in preview.get("changes") or []
               if isinstance(row, dict) and row.get("op") == "remove"]
    if removes:
        lines.append("  删除项（不折叠）：")
        for row in removes:
            lines.append("    - %s %s：%s"
                         % (row.get("kind"), row.get("ref"),
                            row.get("summary") or ""))
    if check:
        lines.append("  当前状态：%s" % (check.get("state") or "unknown"))
        for reason in check.get("reasons") or []:
            lines.append("    - %s" % reason)
        if check.get("approval_reason"):
            lines.append("  审批：%s" % check.get("approval_reason"))
        for incident in check.get("incidents") or []:
            lines.append("  incident：%s → %s"
                         % (incident.get("ref"),
                            incident.get("next_command")))
    lines.append("  边界：%s" % (preview.get("boundary")
                                 or "只读预演，不产生审批与外部效果"))
    return "\n".join(lines)
