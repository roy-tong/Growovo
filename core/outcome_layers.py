#!/usr/bin/env python3
"""三层结果与真实激活回流（G-03）。

这一层只做三件事：把**经授权的产品事件**导进来、把**执行/互动/业务三层**各自
记账并给出凭证、把同一个人跨账本的记录归并成只读进度。它不发送、不联网、
不调用模型。

数据落点（都在现有 data/outcomes 下，不另建第二套账本）：

  outcomes/product_events.jsonl   授权导入的产品事件（导入凭证 + 幂等索引）
  outcomes/qualifications.jsonl  人工改资格（append-only 审计，原事件不改）
  outcomes/receipts.jsonl          L1/L2/L3 三层凭证（append-only）
  outcomes/events.jsonl           既有的 OutcomeLedger 事件台账（唯一业务漏斗源）

为什么 product_events 不是「第二套账本」：它记录的是**这次导入的授权凭据**
（谁导的、什么来源、schema 版本、evidence_hash、是否核验），通过
``import_key=product_events:<event_id>`` 把通过核验的业务事件投影进既有
OutcomeLedger。漏斗、结果卡、metrics/learn/review 仍然只读 OutcomeLedger；
本模块的三层汇总是它的**按层视图**，不是另一份计数。

三层允许的声明（PRD §4.2，C-01 凭证语义）：

  L1 执行  action_receipt      平台已确认动作；不累计 L2/L3
  L2 互动  engagement_receipt  发生可解释的互动；不把赞/曝光当有效线索
  L3 业务  activation_receipt  发生可核验的首次激活/注册/付款；不从点击推断付款

纪律：
1. 时间先后不是因果。归因状态只有三种**证据链口径**：direct_receipt（同一条
   追踪链上有已确认回执）、assisted_observation（只有弱辅助证据）、
   unattributed（无法归因）。三者都不是因果证明；``causal_credit`` 恒为
   ``not_claimed``，不把最后一条触达说成「带来」了转化。
2. 供应商宣称「已成交」而没有买方确认（``verified_by`` 与 ``evidence_hash``
   至少要能对上来源）时记 ``verification_state=unverified``，不计入付费。
3. 同一个主体在同一阶段只计一次（首次）；重复注册/重复激活单列，不重复计数。
4. 人工改资格只追加审计记录，原事件一字不改；读侧按最新审计生效。
5. 没有独立产品事件源时，L3 是 ``UNKNOWN``，不是 ``0``——「未连接」和「没有」
   是两件事，不能写成 0。
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import json
import secrets
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .attribution import OutcomeLedger
from .gates import load_suppressions
from .ledger import Ledger, _append_line, _read_jsonl_fail_loud

#: 导入事件的 schema 版本（写进每条事件与每张凭证，读取侧据此判断兼容性）。
EVENT_SCHEMA_VERSION = "growovo.product-event/1"
RECEIPT_SCHEMA_VERSION = "growovo.outcome-receipt/1"
QUALIFICATION_SCHEMA_VERSION = "growovo.qualification-change/1"
SUMMARY_SCHEMA = "growovo.outcome-layers/1"
JOURNEY_SCHEMA = "growovo.prospect-journey/1"

#: 允许导入的事件类型（PRD G-03 输入）。
EVENT_TYPES = ("action_committed", "meaningful_conversation",
               "signup", "activation", "paid")
#: 计入 L3 业务层的类型；注册不等于激活，但三者都算业务事件。
L3_TYPES = ("signup", "activation", "paid")
#: 价值事件：核验方必须在支付/买方控制域，且 evidence_hash 可复算。
DEFAULT_TRUSTED_VERIFIERS = frozenset({
    "stripe", "paypal", "paddle", "chargebee", "lemonsqueezy", "adyen",
    "braintree", "billing", "payments", "payment-db", "product-db",
    "product_db", "db",
})
#: 供应商/卖方自称永远不是买方核验：即使字段都填了也不算。
_VERIFIER_DENY = ("vendor", "supplier", "seller", "sales", "reseller",
                  "agency", "partner")
#: 证据 hash 绑定的字段（测试侧同口径冻结）。
EVIDENCE_KEYS = ("event_id", "event_type", "subject_id", "source_system",
                 "source_ref", "amount", "currency", "payment_ref", "paid_at")
VALUE_EVENT_TYPES = ("activation", "paid")
EVENT_FIELDS = ("event_id", "event_type", "subject_id", "occurred_at",
                "source_system", "source_ref", "campaign_id",
                "evidence_hash", "verified_by",
                "amount", "currency", "payment_ref", "paid_at")

LAYERS = ("L1", "L2", "L3")
#: 每层只认一种凭证类型：层与凭证对不上就是记错账。
LAYER_KINDS = {"L1": "action_receipt",
               "L2": "engagement_receipt",
               "L3": "activation_receipt"}
#: 每层允许的声明（PRD §4.2 表格右两列）：超出证据的宣称直接拒绝写入。
ALLOWED_ASSERTIONS = {
    "L1": ("platform_action_confirmed",),
    "L2": ("engagement_observed",),
    "L3": ("signup_observed", "activation_verified",
           "payment_verified", "unverified_claim"),
}
ASSERTION_LABELS = {
    "platform_action_confirmed": "平台已确认动作",
    "engagement_observed": "发生可解释的互动",
    "signup_observed": "发生注册（尚未激活）",
    "activation_verified": "发生可核验首次激活",
    "payment_verified": "发生已核验付款",
    "unverified_claim": "仅有来源方宣称，未核验",
}

ATTRIBUTION_STATES = ("direct_receipt", "assisted_observation", "unattributed")
ATTRIBUTION_LABELS = {
    "direct_receipt": "同一追踪链上有已确认回执",
    "assisted_observation": "只有弱辅助证据（同期发生在同一 campaign）",
    "unattributed": "没有 campaign 线索，无法归因",
}

#: 自动触达三要素（G-03 准入）：缺任一项不进可自动触达集合。
OUTREACH_ELEMENTS = ("their_problem", "why_we_help", "expected_action")
OUTREACH_LABELS = {
    "their_problem": "对方的具体问题",
    "why_we_help": "我们能帮上的理由",
    "expected_action": "希望对方做的具体动作",
}


class OutcomeLayerError(RuntimeError):
    """三层账本的写入/读取拒绝：参数不合规、跨层宣称、来源不明。"""


# ---------------------------------------------------------------------------
# 落盘与读取
# ---------------------------------------------------------------------------

def _paths(data_dir) -> Dict[str, Path]:
    base = Path(data_dir) / "outcomes"
    return {
        "events": base / "product_events.jsonl",
        "qualifications": base / "qualifications.jsonl",
        "receipts": base / "receipts.jsonl",
    }


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _append(path: Path, rec: Dict[str, Any]) -> None:
    """与账本同一追加纪律：O_APPEND + flock + 单次 write。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    _append_line(path, json.dumps(rec, ensure_ascii=False) + "\n")


def _read(path: Path, kind: str) -> List[Dict[str, Any]]:
    return _read_jsonl_fail_loud(path, kind=kind)


def _text(value) -> str:
    return str(value if value is not None else "").strip()


def _day(value) -> str:
    """取日期部分：接受 date/datetime/ISO 字符串；取不到返回空串。"""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _text(value)
    if not text:
        return ""
    try:
        return _parse_time(text).date().isoformat()
    except (OutcomeLayerError, AttributeError):
        return text[:10]


def _parse_time(value) -> datetime:
    """解析 ISO 8601；容忍结尾 Z 与纯日期。失败抛 OutcomeLayerError。"""
    text = _text(value)
    if not text:
        raise OutcomeLayerError("时间不能为空（需要 ISO 8601，如 2026-11-03T10:00:00）")
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        try:
            return datetime.fromisoformat(text[:10])
        except ValueError as exc:
            raise OutcomeLayerError(
                f"时间不是 ISO 8601：{text!r}（示例 2026-11-03T10:00:00）") from exc


def _bounds(window) -> Optional[Tuple[str, str]]:
    """时间窗 {'start','end'} → (start, end)；空窗口返回 None（不限制）。"""
    if not window:
        return None
    if not isinstance(window, dict):
        raise OutcomeLayerError("时间窗必须是 {'start': 'YYYY-MM-DD', 'end': ...}")
    start = _day(window.get("start")) if window.get("start") else ""
    end = _day(window.get("end")) if window.get("end") else ""
    for value in (start, end):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise OutcomeLayerError(
                    f"时间窗日期不合法：{value!r}（要 YYYY-MM-DD）") from exc
    if start and end and end < start:
        raise OutcomeLayerError(f"时间窗结束日早于开始日：{start} > {end}")
    if not start and not end:
        return None
    return start, end


def _in_bounds(value, bounds: Optional[Tuple[str, str]]) -> bool:
    if bounds is None:
        return True
    day = _day(value)
    if not day:
        return False
    start, end = bounds
    if start and day < start:
        return False
    if end and day > end:
        return False
    return True


def _same_campaign(left: str, right: str) -> bool:
    """同一条 campaign 链：相等，或一方是另一方的按日队列前缀（g1 ↔ g1-20261103）。

    Run 级 campaign（g1）与它派生出的按日队列（g1-20261103）是同一批动作，
    不应该因为命名粒度不同就把 direct_receipt 降级成 assisted_observation。"""
    left, right = _text(left), _text(right)
    if not left or not right:
        return False
    return (left == right or left.startswith(right + "-")
            or right.startswith(left + "-"))


def _belongs(campaign: str, run_id: Optional[str]) -> bool:
    """campaign 归属：等于 run id 或以「run id-」开头（与账本同一口径）。"""
    if not run_id:
        return True
    campaign = _text(campaign)
    return campaign == "" or campaign == run_id or campaign.startswith(run_id + "-")


def _event_key(rec: Dict[str, Any]) -> str:
    return "product_events:%s" % _text(rec.get("event_id"))


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------

def _load_rows(path) -> List[Any]:
    """读事件文件：JSON 数组 / {"events": [...], "items": [...]} / JSONL / CSV。"""
    source = Path(path)
    if not source.is_file():
        raise OutcomeLayerError(f"找不到事件文件：{source}")
    try:
        text = source.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise OutcomeLayerError(f"事件文件不是合法 UTF-8：{source}（{exc}）") from exc
    if not text.strip():
        raise OutcomeLayerError(f"事件文件是空的：{source}")
    suffix = source.suffix.lower()
    if suffix in (".jsonl", ".ndjson"):
        try:
            return _read_jsonl_fail_loud(source, kind="事件导入文件")
        except RuntimeError as exc:  # LedgerCorruptError：坏行不能静默跳过
            raise OutcomeLayerError(str(exc)) from exc
    if suffix == ".csv":
        return [dict(row) for row in csv.DictReader(text.splitlines())]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OutcomeLayerError(
            f"事件文件不是合法 JSON：{exc}（第 {exc.lineno} 行）") from exc
    if isinstance(payload, dict):
        payload = payload.get("events") or payload.get("items") or []
    if not isinstance(payload, list):
        raise OutcomeLayerError("事件文件应为数组，或含 events/items 数组")
    return list(payload)


def evidence_hash_for(row: Dict[str, Any]) -> str:
    """按冻结口径复算证据 hash：事件身份 + 结构化支付字段。"""
    payload = {}
    for key in EVIDENCE_KEYS:
        value = _text(row.get(key))
        if key == "currency":
            value = value.upper()
        payload[key] = value
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _trusted_verifier(name: str, trusted) -> bool:
    verifier = _text(name).lower()
    if not verifier:
        return False
    if any(token in verifier for token in _VERIFIER_DENY):
        return False
    if verifier in trusted:
        return True
    return verifier.startswith(("buyer:", "buyer_", "customer:"))


def _paid_fields_ok(row: Dict[str, Any]) -> bool:
    amount = _text(row.get("amount")).replace(",", "").replace(" ", "")
    try:
        if float(amount) <= 0:
            return False
    except ValueError:
        return False
    currency = _text(row.get("currency")).upper()
    if len(currency) != 3 or not currency.isalpha():
        return False
    if len(_text(row.get("payment_ref"))) < 3:
        return False
    if _text(row.get("paid_at")):
        try:
            _parse_time(row["paid_at"])
        except OutcomeLayerError:
            return False
    return True


def _verification_state(row: Dict[str, str],
                        trusted=DEFAULT_TRUSTED_VERIFIERS) -> str:
    """核验状态：付款/激活必须在支付或买方控制域，且证据 hash 可复算。

    L2 的「可解释互动」只要求有人工署名（谁说这句话算实质回应）。L3 的
    激活/付款不接受「字段非空」：供应商销售写 ``verified_by`` 与任意
    ``evidence_hash`` 仍只是宣称；付款还要求金额/币种/支付流水存在。
    """
    etype = row["event_type"]
    if etype in VALUE_EVENT_TYPES:
        if not _trusted_verifier(row.get("verified_by", ""), trusted):
            return "unverified"
        provided = _text(row.get("evidence_hash")).lower()
        if not provided or not hmac.compare_digest(
                provided, evidence_hash_for(row)):
            return "unverified"
        if etype == "paid" and not _paid_fields_ok(row):
            return "unverified"
        return "verified"
    if etype == "meaningful_conversation":
        return "verified" if row["verified_by"] else "unverified"
    return "observed"


def _normalize_event(row, *, index: int, allowed_sources: set,
                     bounds: Optional[Tuple[str, str]], batch: str,
                     imported_by: str,
                     trusted_verifiers=DEFAULT_TRUSTED_VERIFIERS
                     ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """校验并规范化一行事件。返回 (rec, None) 或 (None, error)。"""
    def bad(reason: str, detail: str) -> Tuple[None, Dict[str, Any]]:
        return None, {"row": index,
                      "event_id": _text((row or {}).get("event_id")
                                        if isinstance(row, dict) else ""),
                      "reason": reason, "detail": detail}

    if not isinstance(row, dict):
        return bad("not_an_object", "每一行必须是一个 JSON 对象")
    rec = {name: _text(row.get(name)) for name in EVENT_FIELDS}
    if not rec["event_id"]:
        return bad("missing_event_id", "event_id 必填：导入去重靠它，空值无法幂等")
    if rec["event_type"] not in EVENT_TYPES:
        return bad("invalid_event_type",
                   f"event_type 只允许 {'/'.join(EVENT_TYPES)}，得到 {rec['event_type']!r}")
    if not rec["subject_id"]:
        return bad("blank_subject",
                   "subject_id 必填：不能把空白主体算成一个用户")
    if len(rec["subject_id"]) > 200 or any(ord(ch) < 32 for ch in rec["subject_id"]):
        return bad("invalid_subject",
                   "subject_id 含换行/控制字符或超过 200 字："
                   "这种主体串无法可靠去重，先回来源系统修数据")
    try:
        when = _parse_time(rec["occurred_at"])
    except OutcomeLayerError as exc:
        return bad("invalid_occurred_at", str(exc))
    rec["occurred_at"] = when.isoformat()
    if not rec["source_system"]:
        return bad("missing_source_system",
                   "source_system 必填：事件来自哪个系统要能回查")
    if rec["source_system"] not in allowed_sources:
        return bad("source_not_authorized",
                   f"来源 {rec['source_system']!r} 不在授权清单 "
                   f"{sorted(allowed_sources)}；未授权来源的事件一律不收")
    if not rec["source_ref"]:
        return bad("missing_source_ref",
                   "source_ref 必填：每条事件都要能指回来源记录")
    if not _in_bounds(rec["occurred_at"], bounds):
        return bad("out_of_window",
                   f"occurred_at={rec['occurred_at']} 在导入时间窗 {bounds} 之外")
    state = _verification_state(rec, trusted_verifiers)
    rec.update({
        "schema": EVENT_SCHEMA_VERSION,
        "schema_version": EVENT_SCHEMA_VERSION,
        "verification_state": state,
        "import_batch": batch,
        "imported_by": imported_by,
        "imported_at": _now(),
    })
    return rec, None


def import_events(data_dir, path, *, allowed_sources: Optional[Iterable[str]] = None,
                  imported_by: str = "",
                  window: Optional[Dict[str, str]] = None,
                  dry_run: bool = False,
                  trusted_verifiers: Optional[Iterable[str]] = None
                  ) -> Dict[str, Any]:
    """导入授权产品事件。写不写由 ``dry_run`` 决定：库调用默认写，CLI 默认只校验。

    匿名行为也可导入：给一个稳定的匿名主体 ID（如 ``anon-xxxx``），不要留空——
    空主体无法去重也无法回答「这是不是同一个人」。

    去重键是 ``event_id``；重复导入只记 ``skipped_duplicate``，不重复计数。
    校验失败的行不写（不会写出半条事件），错误带 reason 供调用方判断怎么补；
    同批其余合法行照常导入，此时 ``partial=True``——调用方必须同时看
    ``created`` 与 ``len(errors)``，不能只看其中一个。
    """
    data_dir = Path(data_dir)
    sources = {_text(s) for s in (allowed_sources or []) if _text(s)}
    trusted = ({_text(v).lower() for v in trusted_verifiers if _text(v)}
               if trusted_verifiers is not None
               else set(DEFAULT_TRUSTED_VERIFIERS))
    if not sources:
        raise OutcomeLayerError(
            "导入事件前先声明授权来源（--allowed-source，如 product_export）："
            "不声明来源就无法判断这条事件该不该收")
    if not _text(imported_by):
        raise OutcomeLayerError(
            "导入必须署名（--by）：没有署名的导入无法审计是谁授权的")
    rows = _load_rows(path)
    bounds = _bounds(window)
    batch = datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)

    seen = {_text(r.get("event_id")) for r in read_product_events(data_dir)}
    errors: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    duplicates: List[Dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        rec, error = _normalize_event(row, index=index, allowed_sources=sources,
                                      bounds=bounds, batch=batch,
                                      imported_by=imported_by,
                                      trusted_verifiers=trusted)
        if error is not None:
            errors.append(error)
            continue
        if rec["event_id"] in seen:
            duplicates.append(rec)
            continue
        seen.add(rec["event_id"])
        pending.append(rec)

    result: Dict[str, Any] = {
        "schema": EVENT_SCHEMA_VERSION,
        "path": str(path),
        "batch": batch,
        "allowed_sources": sorted(sources),
        "imported_by": imported_by,
        "window": window or {},
        "dry_run": bool(dry_run),
        "created": 0,
        "skipped_duplicate": len(duplicates),
        "repaired": 0,
        "errors": errors,
        "event_ids": [r["event_id"] for r in pending],
        "receipts": 0,
        "projected": 0,
        "partial": bool(pending and errors),
        "warnings": [],
    }
    if dry_run:
        result["would_create"] = len(pending)
        return result

    paths = _paths(data_dir)
    for rec in pending:
        _append(paths["events"], rec)
        result["created"] += 1
        try:
            result["receipts"] += _write_receipts_for_event(data_dir, rec)
            result["projected"] += _project_event(data_dir, rec)
        except Exception as exc:  # noqa: BLE001 派生件失败不吞掉导入凭证
            result["warnings"].append(
                f"{rec['event_id']} 已导入，但凭证/漏斗派生失败："
                f"{type(exc).__name__}: {exc}")
    # 幂等重导自我修复：事件已在库里但派生凭证缺失时补上（事件是源，凭证是派生）。
    known = {_text(r.get("source_ref")) for r in read_receipts(data_dir)}
    for rec in duplicates:
        if _event_key(rec) in known:
            continue
        try:
            result["repaired"] += _write_receipts_for_event(data_dir, rec)
        except Exception as exc:  # noqa: BLE001
            result["warnings"].append(
                f"{rec['event_id']} 凭证补写失败：{type(exc).__name__}: {exc}")
    return result


def _write_receipts_for_event(data_dir, rec: Dict[str, Any]) -> int:
    """一条导入事件 → 一张指向它的凭证（L2 互动或 L3 业务）。"""
    etype = rec["event_type"]
    common = dict(subject_id=rec["subject_id"], campaign_id=rec["campaign_id"],
                  occurred_at=rec["occurred_at"],
                  evidence_hash=rec["evidence_hash"], by=rec["imported_by"],
                  verification_state=rec["verification_state"],
                  ts=rec["imported_at"])
    if etype == "meaningful_conversation":
        write_receipt(data_dir, layer="L2", kind="engagement_receipt",
                      source_ref=_event_key(rec),
                      assertion="engagement_observed", **common)
        return 1
    if etype in L3_TYPES:
        if etype in VALUE_EVENT_TYPES and rec["verification_state"] != "verified":
            assertion = "unverified_claim"
        else:
            assertion = {"signup": "signup_observed",
                         "activation": "activation_verified",
                         "paid": "payment_verified"}[etype]
        write_receipt(data_dir, layer="L3", kind="activation_receipt",
                      source_ref=_event_key(rec), assertion=assertion, **common)
        return 1
    return 0


def _project_event(data_dir, rec: Dict[str, Any]) -> int:
    """把通过核验的业务事件投影进既有 OutcomeLedger（唯一漏斗源）。

    未核验的宣称不投影：供应商说「已成交」不能变成一条付费事件。
    """
    etype = rec["event_type"]
    if etype not in ("signup", "activation", "paid"):
        return 0
    if etype in VALUE_EVENT_TYPES and rec["verification_state"] != "verified":
        return 0
    key = _event_key(rec)
    ol = OutcomeLedger(data_dir)
    if any(_text(r.get("import_key")) == key for r in ol.records()):
        return 0
    verification = None
    if etype in VALUE_EVENT_TYPES:
        verification = {"method": rec["verified_by"] or rec["source_system"],
                        "ref": rec["evidence_hash"]}
    ol.add_event(subject=rec["subject_id"], event_type=etype,
                 source_ref=rec["source_ref"], verification=verification,
                 campaign=rec["campaign_id"], import_key=key,
                 ts=rec["occurred_at"])
    return 1


def read_product_events(data_dir, *, run_id: Optional[str] = None,
                        window: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """读授权导入的产品事件（原样，按文件顺序）。"""
    records = _read(_paths(data_dir)["events"], kind="产品事件导入记录")
    bounds = _bounds(window)
    out = []
    for rec in records:
        if run_id and not _belongs(_text(rec.get("campaign_id")), run_id):
            continue
        if not _in_bounds(rec.get("occurred_at"), bounds):
            continue
        out.append(rec)
    return out


def has_authorized_imports(data_dir, *, run_id: Optional[str] = None) -> bool:
    """这个 run 名下有没有授权导入的产品事件（决定结果卡用哪套口径）。"""
    return bool(read_product_events(Path(data_dir), run_id=run_id))


def unknown_summary(reason: str = "") -> Dict[str, Any]:
    """读不到三层账本时的降级形状：全部 UNKNOWN，绝不当成 0。

    ``degraded=True`` 与「来源未接入」区分开：读取失败要把原因带出来，
    不能静默显示成「没接入」——那会把数据问题说成业务结论。"""
    hint = ("；注意：%s" % reason[:160]) if reason else ""

    def block(layer: str, text: str) -> Dict[str, Any]:
        return {"layer": layer, "available": False, "state": "UNKNOWN",
                "text": text + hint}
    return {
        "schema": SUMMARY_SCHEMA,
        "run": "",
        "window": {},
        "degraded": True,
        "L1": block("L1", "执行记录读不到"),
        "L2": block("L2", "互动事件未接入（没有可解释的互动记录）：不是 0"),
        "L3": block("L3", "激活事件未接入（未获得业务事件）：不是 0，是未知"),
        "attribution": {
            "summary": {state: 0 for state in ATTRIBUTION_STATES},
            "activations": [],
            "note": reason or "三层账本读取失败：这是读取问题，不是「没有结果」",
        },
    }


# ---------------------------------------------------------------------------
# 人工改资格（append-only，原事件不改）
# ---------------------------------------------------------------------------

def _qualification_map(data_dir) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for rec in read_qualifications(data_dir):
        event_id = _text(rec.get("event_id"))
        if event_id:
            out[event_id] = bool(rec.get("qualifies"))
    return out


def set_event_qualification(data_dir, event_id: str, *, qualifies: bool,
                            by: str, note: str,
                            dry_run: bool = False) -> Dict[str, Any]:
    """人工改一条事件的资格。只追加审计记录，不覆盖原事件。

    必须有署名与依据：资格判断影响的是「算不算这个人」，没有出处的改动不可复核。
    ``dry_run=True`` 只做校验并返回将要写入的记录（不落盘）。
    """
    data_dir = Path(data_dir)
    event_id = _text(event_id)
    if not event_id:
        raise OutcomeLayerError("要改哪条事件：event_id 不能为空")
    if not _text(by):
        raise OutcomeLayerError("改资格必须署名（by）：没有署名无法审计是谁判断的")
    if not _text(note):
        raise OutcomeLayerError("改资格必须写依据（note）：例如「内部测试账号，不算激活」")
    events = {_text(r.get("event_id")): r for r in read_product_events(data_dir)}
    if event_id not in events:
        raise OutcomeLayerError(f"没有这条产品事件：{event_id}")
    previous = None
    for rec in read_qualifications(data_dir):
        if _text(rec.get("event_id")) == event_id:
            previous = bool(rec.get("qualifies"))
    record = {
        "schema": QUALIFICATION_SCHEMA_VERSION,
        "ts": _now(),
        "event_id": event_id,
        "qualifies": bool(qualifies),
        "previous": previous,
        "by": _text(by),
        "note": _text(note),
        "event_type": _text(events[event_id].get("event_type")),
        "subject_id": _text(events[event_id].get("subject_id")),
        "occurred_at": _text(events[event_id].get("occurred_at")),
        "applied": not dry_run,
    }
    if not dry_run:
        _append(_paths(data_dir)["qualifications"], record)
    return record


def read_qualifications(data_dir) -> List[Dict[str, Any]]:
    return _read(_paths(data_dir)["qualifications"], kind="资格变更审计")


def qualification_states(data_dir) -> Dict[str, bool]:
    """event_id → 最新人工资格判定（没改过的按导入时的 True）。"""
    return _qualification_map(data_dir)


# ---------------------------------------------------------------------------
# 三层凭证
# ---------------------------------------------------------------------------

def write_receipt(data_dir, *, layer: str, kind: str, source_ref: str,
                  assertion: str, subject_id: str = "", campaign_id: str = "",
                  occurred_at: str = "", evidence_hash: str = "",
                  verification_state: str = "observed", by: str = "",
                  note: str = "", causality: str = "not_claimed",
                  ts: Optional[str] = None) -> Dict[str, Any]:
    """写一张层凭证。层、凭证类型、允许的声明三者必须一致，否则拒绝。

    append-only：这里只追加，从不改写已有凭证；撤销靠追加新的判断记录。
    """
    layer = _text(layer).upper()
    if layer not in LAYER_KINDS:
        raise OutcomeLayerError(f"layer 只允许 {'/'.join(LAYERS)}，得到 {layer!r}")
    if _text(kind) != LAYER_KINDS[layer]:
        raise OutcomeLayerError(
            f"{layer} 的凭证类型必须是 {LAYER_KINDS[layer]}，得到 {_text(kind)!r}")
    assertion = _text(assertion)
    if assertion not in ALLOWED_ASSERTIONS[layer]:
        raise OutcomeLayerError(
            f"{layer} 不允许宣称 {assertion!r}；这一层能说的是 "
            f"{'、'.join(ALLOWED_ASSERTIONS[layer])}。"
            "超出证据的宣称（如 L1 说业务增长）一律拒绝写入")
    if not _text(source_ref):
        raise OutcomeLayerError("凭证必须指向来源（source_ref）：没有出处的凭证不可复核")
    record = {
        "schema": RECEIPT_SCHEMA_VERSION,
        "ts": ts or _now(),
        "layer": layer,
        "kind": LAYER_KINDS[layer],
        "source_ref": _text(source_ref),
        "assertion": assertion,
        "assertion_label": ASSERTION_LABELS.get(assertion, assertion),
        "causality": causality if causality else "not_claimed",
        "verification_state": _text(verification_state) or "observed",
        "subject_id": _text(subject_id),
        "campaign_id": _text(campaign_id),
        "occurred_at": _text(occurred_at),
        "evidence_hash": _text(evidence_hash),
        "by": _text(by),
        "note": _text(note),
    }
    _append(_paths(data_dir)["receipts"], record)
    return record


def read_receipts(data_dir, *, layer: Optional[str] = None) -> List[Dict[str, Any]]:
    records = _read(_paths(data_dir)["receipts"], kind="三层结果凭证")
    if layer:
        want = _text(layer).upper()
        records = [r for r in records if _text(r.get("layer")).upper() == want]
    return records


def _ledger_ref(rec: Dict[str, Any]) -> str:
    ref = _text(rec.get("ref"))
    if ref:
        return ref
    seed = "|".join(_text(rec.get(k)) for k in
                    ("ts", "channel", "action", "target", "campaign"))
    return "local-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def sync_action_receipts(data_dir, *, run_id: Optional[str] = None,
                         window: Optional[Dict[str, str]] = None,
                         by: str = "local-ledger",
                         dry_run: bool = False) -> Dict[str, Any]:
    """把账本里 committed 的执行记录补成 L1 凭证（幂等，只在缺失时追加）。

    ``dry_run=True`` 只统计会补多少张，不写文件。
    """
    data_dir = Path(data_dir)
    bounds = _bounds(window)
    ledger = Ledger(data_dir / "ledger.jsonl")
    existing = {(_text(r.get("layer")), _text(r.get("source_ref")))
                for r in read_receipts(data_dir)}
    scanned = 0
    written = 0
    would_write = 0
    for rec in ledger.all():
        scanned += 1
        if _text(rec.get("result")) != "committed":
            continue
        campaign = _text(rec.get("campaign"))
        if run_id and not _belongs(campaign, run_id):
            continue
        if not _in_bounds(rec.get("ts"), bounds):
            continue
        source_ref = "ledger:" + _ledger_ref(rec)
        if ("L1", source_ref) in existing:
            continue
        existing.add(("L1", source_ref))
        if dry_run:
            would_write += 1
            continue
        write_receipt(data_dir, layer="L1", kind="action_receipt",
                      source_ref=source_ref,
                      assertion="platform_action_confirmed",
                      campaign_id=campaign,
                      occurred_at=_text(rec.get("ts")),
                      verification_state="observed", by=by,
                      note=f"{_text(rec.get('channel'))}:{_text(rec.get('action'))} → "
                           f"{_text(rec.get('target'))}（{_text(rec.get('result'))}）",
                      ts=_text(rec.get("ts")) or None)
        written += 1
    return {"written": written, "would_write": would_write, "scanned": scanned,
            "dry_run": bool(dry_run), "run": run_id, "window": window or {}}


# ---------------------------------------------------------------------------
# 三层汇总
# ---------------------------------------------------------------------------

def _canon(ol: OutcomeLedger, subject) -> str:
    return _text(ol.canonical_subject(_text(subject)))


def _same_subject(ol: OutcomeLedger, left, right) -> bool:
    a = _canon(ol, left)
    b = _canon(ol, right)
    return bool(a) and a == b


def _business_rows(data_dir: Path, ol: OutcomeLedger,
                   prov: Sequence[Dict[str, Any]],
                   qual: Dict[str, bool]) -> List[Dict[str, Any]]:
    """合并业务事件：OutcomeLedger 是唯一台账，导入凭证补授权与核验状态。

    投影过的事件只按台账记一次（用 import_key 关联回导入记录）；未投影的
    未核验宣称只出现在导入记录里，单独列为 unverified，不进计数。
    """
    by_key = {_event_key(r): r for r in prov}
    rows: List[Dict[str, Any]] = []
    linked: set = set()
    for ev in ol.records():
        if _text(ev.get("kind")) != "event":
            continue
        etype = _text(ev.get("event_type"))
        if etype not in L3_TYPES:
            continue
        key = _text(ev.get("import_key"))
        record = by_key.get(key)
        if key:
            linked.add(key)
        if record is not None:
            if qual.get(_text(record.get("event_id"))) is False:
                continue  # 人工判定不计入：读侧生效，原事件不动
            rows.append({
                "event_id": _text(record.get("event_id")),
                "event_type": etype,
                "subject_id": _text(record.get("subject_id")),
                "occurred_at": _text(record.get("occurred_at")),
                "campaign_id": _text(record.get("campaign_id")),
                "source_ref": _text(record.get("source_ref")),
                "verification_state": _text(record.get("verification_state")),
                "source": "product_events",
            })
            continue
        rows.append({
            "event_id": "",
            "event_type": etype,
            "subject_id": _text(ev.get("subject")),
            "occurred_at": _text(ev.get("ts")),
            "campaign_id": _text(ev.get("campaign")),
            "source_ref": _text(ev.get("source_ref")),
            "verification_state": "verified" if ev.get("verification") else "unverified",
            "source": "outcome_ledger",
        })
    for record in prov:
        etype = _text(record.get("event_type"))
        if etype not in L3_TYPES:
            continue
        if _event_key(record) in linked:
            continue
        if qual.get(_text(record.get("event_id"))) is False:
            continue
        rows.append({
            "event_id": _text(record.get("event_id")),
            "event_type": etype,
            "subject_id": _text(record.get("subject_id")),
            "occurred_at": _text(record.get("occurred_at")),
            "campaign_id": _text(record.get("campaign_id")),
            "source_ref": _text(record.get("source_ref")),
            "verification_state": _text(record.get("verification_state")),
            "source": "product_events",
        })
    rows.sort(key=lambda r: (r["occurred_at"], r["event_id"]))
    return rows


def _first_and_repeats(ol: OutcomeLedger,
                       rows: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]],
                                                               Dict[str, List[Dict[str, Any]]],
                                                               List[Dict[str, Any]]]:
    """同阶段同主体只算一次首次；重复的进 repeats；未核验价值事件进 unverified。"""
    first: Dict[str, Dict[str, Any]] = {stage: {} for stage in L3_TYPES}
    repeats: Dict[str, List[Dict[str, Any]]] = {stage: [] for stage in L3_TYPES}
    unverified: List[Dict[str, Any]] = []
    for row in rows:
        stage = row["event_type"]
        if stage in VALUE_EVENT_TYPES and row["verification_state"] != "verified":
            unverified.append({
                "event_id": row["event_id"],
                "event_type": stage,
                "subject_id": row["subject_id"],
                "occurred_at": row["occurred_at"],
                "source_ref": row["source_ref"],
                "campaign_id": row["campaign_id"],
                "verification_state": "unverified",
                "reason": "来源方宣称，但缺支付/买方控制域核验或证据 hash 无法复算：不计入",
            })
            continue
        subject = _canon(ol, row["subject_id"])
        hit = first[stage].get(subject)
        if hit is None:
            first[stage][subject] = row
            continue
        repeats[stage].append({
            "event_id": row["event_id"],
            "subject_id": row["subject_id"],
            "occurred_at": row["occurred_at"],
            "first_event_id": hit.get("event_id", ""),
            "first_occurred_at": hit.get("occurred_at", ""),
        })
    return first, repeats, unverified


def _attribution(ol: OutcomeLedger, data_dir: Path,
                 activations: Dict[str, Dict[str, Any]],
                 run_id: Optional[str]) -> Dict[str, Any]:
    """激活的归因状态：只描述证据链可追溯程度，不制造因果。"""
    ledger = Ledger(data_dir / "ledger.jsonl")
    touches_all = []
    for rec in ledger.all():
        campaign = _text(rec.get("campaign"))
        if run_id and not _belongs(campaign, run_id):
            continue
        touches_all.append(rec)
    rows = []
    for subject, row in sorted(activations.items()):
        touches = [t for t in touches_all
                   if _same_subject(ol, t.get("target"), row["subject_id"])]
        campaign = _text(row.get("campaign_id"))
        if not campaign:
            status = "unattributed"
        elif any(_same_campaign(_text(t.get("campaign")), campaign)
                 and _text(t.get("result")) == "committed"
                 for t in touches):
            status = "direct_receipt"
        else:
            status = "assisted_observation"
        rows.append({
            "subject": subject,
            "subject_id": row["subject_id"],
            "event_id": row["event_id"],
            "occurred_at": row["occurred_at"],
            "campaign_id": campaign,
            "attribution": status,
            "attribution_label": ATTRIBUTION_LABELS[status],
            "causal_credit": "not_claimed",
            "touches": [{
                "ts": _text(t.get("ts")),
                "channel": _text(t.get("channel")),
                "action": _text(t.get("action")),
                "result": _text(t.get("result")),
                "campaign": _text(t.get("campaign")),
                "ref": _text(t.get("ref")),
            } for t in touches],
        })
    summary = {state: 0 for state in ATTRIBUTION_STATES}
    for row in rows:
        summary[row["attribution"]] += 1
    summary["total"] = len(rows)
    return {
        "summary": summary,
        "activations": rows,
        "note": ("归因状态只描述证据链能追到哪一步，非因果：direct_receipt 是同一条"
                 "追踪链上有已确认回执，assisted_observation 是同期只有弱辅助证据，"
                 "unattributed 是无法归因。任何状态都不把最后一条触达算成转化原因。"),
    }


def _l1_summary(data_dir: Path, prov: Sequence[Dict[str, Any]], *,
                run_id: Optional[str],
                bounds: Optional[Tuple[str, str]]) -> Dict[str, Any]:
    ledger = Ledger(Path(data_dir) / "ledger.jsonl")
    records = []
    for rec in ledger.all():
        if run_id and not _belongs(_text(rec.get("campaign")), run_id):
            continue
        if not _in_bounds(rec.get("ts"), bounds):
            continue
        records.append(rec)
    committed = [r for r in records if _text(r.get("result")) == "committed"]
    by_channel: Dict[str, Dict[str, int]] = {}
    for rec in committed:
        channel = _text(rec.get("channel")) or "?"
        action = _text(rec.get("action")) or "?"
        bucket = by_channel.setdefault(channel, {})
        bucket[action] = bucket.get(action, 0) + 1
    receipts = [r for r in read_receipts(data_dir, layer="L1")
                if run_id is None or _belongs(_text(r.get("campaign_id")), run_id)]
    imported_actions = [r for r in prov
                        if _text(r.get("event_type")) == "action_committed"
                        and _in_bounds(r.get("occurred_at"), bounds)]
    available = bool(records)
    state = "OBSERVED" if available else "UNKNOWN"
    if available:
        text = (f"已确认动作 {len(committed)} 条"
                + (f"（{len(records) - len(committed)} 条非 committed 终态）"
                   if len(records) != len(committed) else ""))
    else:
        text = "还没有执行记录：不是「没发」，是这段窗口里没有账本记录"
    return {
        "layer": "L1",
        "available": available,
        "state": state,
        "text": text,
        "actions": len(committed),
        "records": len(records),
        "by_channel": by_channel,
        "receipts": len(receipts),
        "imported_actions": len(imported_actions),
        "note": ("L1 只证明平台已确认动作，不累计 L2/L3；导入的 action_committed "
                 "事件只作对照，L1 以本地账本为准，避免重复计数"),
    }


def _l2_summary(data_dir: Path, ol: OutcomeLedger,
                prov: Sequence[Dict[str, Any]], *,
                run_id: Optional[str],
                bounds: Optional[Tuple[str, str]]) -> Dict[str, Any]:
    scoped = [r for r in prov
              if _text(r.get("event_type")) == "meaningful_conversation"
              and _belongs(_text(r.get("campaign_id")), run_id)]
    in_window = [r for r in scoped if _in_bounds(r.get("occurred_at"), bounds)]
    verified = [r for r in in_window if _text(r.get("verification_state")) == "verified"]
    unique = {_canon(ol, r.get("subject_id")) for r in verified}
    unverified = [{
        "event_id": _text(r.get("event_id")),
        "subject_id": _text(r.get("subject_id")),
        "occurred_at": _text(r.get("occurred_at")),
        "verification_state": "unverified",
        "reason": "缺人工署名：没有说话人确认这条回应算实质互动，不计数",
    } for r in in_window if _text(r.get("verification_state")) != "verified"]
    if in_window:
        available, state = True, "OBSERVED"
        text = f"{len(verified)} 条可解释互动，涉及 {len(unique)} 个人"
        if unverified:
            text += f"；另有 {len(unverified)} 条未署名不计入"
    elif scoped:
        available, state = False, "NO_EVENTS_IN_WINDOW"
        text = "互动事件来源已接入，但这个窗口内没有可解释互动"
    else:
        available, state = False, "UNKNOWN"
        text = ("互动事件未接入（没有可解释的互动记录）：不是 0。"
                "点赞和曝光不算互动，要看对方有没有实质回应")
    return {
        "layer": "L2",
        "available": available,
        "state": state,
        "text": text,
        "unique_subjects": len(unique),
        "responses": len(verified),
        "unverified": unverified,
        "note": ("L2 只认非机器人、与产品问题相关、对方有实质回应的互动；"
                 "赞/曝光/关注不算有效销售线索"),
    }


def _l3_summary(data_dir: Path, ol: OutcomeLedger,
                prov: Sequence[Dict[str, Any]], qual: Dict[str, bool], *,
                run_id: Optional[str],
                bounds: Optional[Tuple[str, str]]) -> Tuple[Dict[str, Any],
                                                            Dict[str, Dict[str, Any]]]:
    rows = _business_rows(data_dir, ol, prov, qual)
    run_rows = [r for r in rows if _belongs(r["campaign_id"], run_id)]
    connected = bool(run_rows)
    in_window = [r for r in run_rows if _in_bounds(r["occurred_at"], bounds)]
    first, repeats, unverified = _first_and_repeats(ol, in_window)
    counts = {stage: len(first[stage]) for stage in L3_TYPES}
    counted = sum(counts.values())
    if not connected:
        available, state = False, "UNKNOWN"
        text = "激活事件未接入（未获得业务事件）：不是 0，是未知"
    elif not in_window:
        available, state = False, "NO_EVENTS_IN_WINDOW"
        text = ("业务事件来源已接入，但这个窗口内没有可核验事件"
                "（窗口外的事件不算本期口径）")
    elif counted == 0 and unverified:
        available, state = False, "PARTIAL"
        text = (f"有 {len(unverified)} 条未核验的业务事件："
                "来源方宣称不等于已发生，全部不计入")
    elif unverified:
        available, state = True, "PARTIAL"
        text = (f"首次激活 {counts['activation']} 人（已核验）；"
                f"另有 {len(unverified)} 条未核验不计入")
    else:
        available, state = True, "OBSERVED"
        text = (f"首次激活 {counts['activation']} 人｜注册 {counts['signup']} 人｜"
                f"付费 {counts['paid']} 人（均来自已核验事件）")
    return {
        "layer": "L3",
        "available": available,
        "state": state,
        "text": text,
        "activation": counts["activation"],
        "signup": counts["signup"],
        "paid": counts["paid"],
        "repeated_activations": repeats["activation"],
        "repeated_signups": repeats["signup"],
        "repeated_payments": repeats["paid"],
        "unverified": unverified,
        "events": len(in_window),
        "source_connected": connected,
        "note": ("首次激活 = 同一主体第一次可核验的价值事件；重复激活单列不重复"
                 "计数；注册不等于激活；付款要有支付/买方核验记录；去重键是"
                 "event_id，多来源描述同一事件时要人工核对"),
    }, first["activation"]


def layer_summary(data_dir, *, run_id: Optional[str] = None,
                  window: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """三层汇总：执行 / 互动 / 业务 + 激活归因。只读，不写任何文件。"""
    data_dir = Path(data_dir)
    bounds = _bounds(window)
    ol = OutcomeLedger(data_dir)
    prov = read_product_events(data_dir)
    qual = _qualification_map(data_dir)
    l1 = _l1_summary(data_dir, prov, run_id=run_id, bounds=bounds)
    l2 = _l2_summary(data_dir, ol, prov, run_id=run_id, bounds=bounds)
    l3, activations = _l3_summary(data_dir, ol, prov, qual,
                                  run_id=run_id, bounds=bounds)
    attribution = _attribution(ol, data_dir, activations, run_id)
    return {
        "schema": SUMMARY_SCHEMA,
        "run": run_id or "",
        "window": window or {},
        "L1": l1,
        "L2": l2,
        "L3": l3,
        "attribution": attribution,
        "note": ("三层互不推导：L1 只证明发出，L2 只证明有回应，L3 只证明产品内"
                 "发生了可核验事件；三层相加不构成因果链"),
    }


# ---------------------------------------------------------------------------
# ProspectJourney：只读归并投影（§12.2）
# ---------------------------------------------------------------------------

def _contact_rows(data_dir: Path) -> List[Dict[str, Any]]:
    path = Path(data_dir) / "contacts.jsonl"
    out: List[Dict[str, Any]] = []
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _contact_for(rows: Sequence[Dict[str, Any]], ref: str) -> Optional[Dict[str, Any]]:
    for rec in rows:
        platforms = rec.get("platforms")
        platforms = platforms if isinstance(platforms, dict) else {}
        if _text(rec.get("id")) == ref:
            return rec
        if ref and any(_text(v) == ref for v in platforms.values()):
            return rec
    return None


def _suppression_state(data_dir: Path, targets: set,
                       contacts: Sequence[Dict[str, Any]]) -> str:
    if not targets:
        return "none"
    try:
        suppressed = load_suppressions(data_dir)
    except Exception:  # noqa: BLE001 抑制源坏了：发送门禁会 fail-closed，这里如实标 unknown
        return "unknown"
    if not (targets & suppressed):
        return "none"
    for rec in contacts:
        state = rec.get("state") if isinstance(rec.get("state"), dict) else {}
        platforms = rec.get("platforms") if isinstance(rec.get("platforms"), dict) else {}
        values = {_text(v) for v in platforms.values()} | {_text(rec.get("id"))}
        if values & targets:
            if state.get("dnc"):
                return "dnc"
            if state.get("bounced"):
                return "bounced"
    return "suppressed"


def prospect_journey(data_dir, *, prospect_ref: str,
                     run_id: Optional[str] = None) -> Dict[str, Any]:
    """把账本、导入事件、抑制状态归并成一个人的只读进度投影。

    只是投影：不改任何账本；平台回执与业务事件才是状态变更依据，这里不做
    推断（比如不会因为对方回了一句就把状态改成「有兴趣」）。
    """
    data_dir = Path(data_dir)
    ref = _text(prospect_ref)
    if not ref:
        raise OutcomeLayerError("prospect_ref 不能为空（要查谁：邮箱/平台 ID/联系人 id）")
    ol = OutcomeLedger(data_dir)
    canonical = _canon(ol, ref)
    if canonical == ref and ":" in ref:
        tail = ref.split(":", 1)[1]
        if tail:
            canonical = _canon(ol, tail)
    canonical = canonical or ref
    targets = {ref, canonical}

    contacts = _contact_rows(data_dir)
    contact = _contact_for(contacts, canonical) or _contact_for(contacts, ref)
    if contact is not None:
        if canonical != _text(contact.get("id")):
            canonical = _text(contact.get("id")) or canonical
        platforms = contact.get("platforms")
        platforms = platforms if isinstance(platforms, dict) else {}
        # 抑制集合按平台身份登记（邮箱/handle/ID），所以比对时把该联系人的
        # 全部平台身份都算进来，避免「人已认出但抑制没命中」。
        targets = {ref, canonical} | {_text(v) for v in platforms.values()
                                      if _text(v)}

    touches = []
    runs = set()
    for rec in Ledger(data_dir / "ledger.jsonl").all():
        target = _text(rec.get("target"))
        if not target:
            continue
        if not any(_same_subject(ol, target, want) for want in targets):
            continue
        campaign = _text(rec.get("campaign"))
        if run_id and not _belongs(campaign, run_id):
            continue
        if campaign:
            runs.add(campaign)
        result = _text(rec.get("result"))
        if result not in ("committed", "indeterminate"):
            continue  # dry-run/failed 不算触达过这个人
        touches.append({
            "ts": _text(rec.get("ts")),
            "channel": _text(rec.get("channel")),
            "action": _text(rec.get("action")),
            "campaign": campaign,
            "result": result,
            "ref": "ledger:" + _ledger_ref(rec),
        })
    touches.sort(key=lambda r: r["ts"])

    events = []
    for rec in read_product_events(data_dir):
        if not any(_same_subject(ol, rec.get("subject_id"), want) for want in targets):
            continue
        campaign = _text(rec.get("campaign_id"))
        if run_id and not _belongs(campaign, run_id):
            continue
        if campaign:
            runs.add(campaign)
        events.append(rec)
    product_events = [e for e in events if _text(e.get("event_type")) in L3_TYPES]
    responses = [e for e in events
                 if _text(e.get("event_type")) == "meaningful_conversation"]

    state_flags = sorted({_text(e.get("verification_state")) for e in product_events})
    suppression = _suppression_state(data_dir, targets, contacts)

    source_refs = []
    source_refs.extend(t["ref"] for t in touches)
    source_refs.extend(_event_key(e) for e in events)
    if contact is not None:
        source_refs.append("contacts:%s" % _text(contact.get("id")))
    source_refs = sorted({r for r in source_refs if r})

    activated = [e for e in product_events
                 if _text(e.get("event_type")) == "activation"
                 and _text(e.get("verification_state")) == "verified"]
    paid = [e for e in product_events
            if _text(e.get("event_type")) == "paid"
            and _text(e.get("verification_state")) == "verified"]
    signup = [e for e in product_events if _text(e.get("event_type")) == "signup"]
    qualified_evidence = ""
    if contact is not None:
        verification = contact.get("verification")
        verification = verification if isinstance(verification, dict) else {}
        qualified_evidence = _text(verification.get("grade"))

    if suppression in ("dnc", "bounced", "suppressed"):
        current_state = "suppressed"
    elif suppression == "unknown":
        current_state = "unknown"  # 抑制源读不了：既不能说可触达，也不能说已抑制
    elif activated or paid:
        current_state = "activated"
    elif responses:
        current_state = "responded"
    elif touches:
        current_state = "contact_attempted"
    elif contact is not None and qualified_evidence in ("A", "B"):
        current_state = "qualified"
    else:
        current_state = "discovered"

    if suppression == "dnc":
        next_action = "不再触达：DNC 状态在后续 run 继续生效，改策略或换任务都不解除"
    elif suppression == "bounced":
        next_action = "不再用这个地址：退信是渠道级终态，换可用地址前不重试"
    elif suppression == "unknown":
        next_action = "先修抑制源（contacts.jsonl 解析失败）：发送门禁会拒绝，不要绕过"
    elif current_state == "activated":
        next_action = "不需要自动触达：等下一次已核验的产品事件，不要把激活说成我们带来的"
    elif current_state == "responded":
        next_action = "由人读对方回应后决定下一步：Agent 不替人判断「有兴趣」"
    elif current_state == "contact_attempted":
        next_action = "对方还没回应前不重复触达；到窗口再按三要素准入准备下一条"
    else:
        next_action = "先人工审对象与内容：审过、三要素齐了才进可自动触达集合"

    if paid:
        relevance = "已付费（有已核验付款事件；对账凭证仍要另存）"
    elif activated:
        relevance = "已激活（产品内第一次可核验价值事件）"
    elif signup:
        relevance = "已注册，尚未激活（注册不等于激活）"
    elif responses:
        relevance = "有实质回应，但还没有产品事件：业务相关性未知"
    else:
        relevance = "没有业务事件：不能判断业务相关性"

    basis = []
    if contact is not None:
        basis.append("contacts.jsonl 有档案"
                     + (f"（证据等级 {qualified_evidence}）" if qualified_evidence else ""))
    else:
        basis.append("没有联系人档案：身份未归一，不能确认是同一人")
    if product_events:
        basis.append(f"产品事件 {len(product_events)} 条（核验状态 {'/'.join(state_flags)}）")

    stamps = [t["ts"] for t in touches] + [_text(e.get("occurred_at")) for e in events]
    stamps = [s for s in stamps if s]
    return {
        "schema": JOURNEY_SCHEMA,
        "prospect_ref": canonical,
        "source_refs": source_refs,
        "qualification_basis": "；".join(basis),
        "business_relevance": relevance,
        "runs_seen": sorted(runs),
        "contacted_actions": touches,
        "responses": [{
            "event_id": _text(e.get("event_id")),
            "occurred_at": _text(e.get("occurred_at")),
            "campaign_id": _text(e.get("campaign_id")),
            "verification_state": _text(e.get("verification_state")),
            "source_ref": _text(e.get("source_ref")),
        } for e in responses],
        "product_events": [{
            "event_id": _text(e.get("event_id")),
            "event_type": _text(e.get("event_type")),
            "occurred_at": _text(e.get("occurred_at")),
            "campaign_id": _text(e.get("campaign_id")),
            "verification_state": _text(e.get("verification_state")),
        } for e in product_events],
        "suppression_state": suppression,
        "current_state": current_state,
        "next_allowed_action": next_action,
        "evidence_updated_at": max(stamps) if stamps else None,
        "prospect_count": 1,
        "note": ("这是账本与产品事件的只读归并：状态变更只认平台回执与已核验业务"
                 "事件，Agent 的推测不提升状态"),
    }


# ---------------------------------------------------------------------------
# 自动触达三要素准入
# ---------------------------------------------------------------------------

def outreach_admission(item: Dict[str, Any]) -> Dict[str, Any]:
    """判断一条队列内容能不能进可自动触达集合。

    三要素一个都没写：``outreach_ready=None``（未评估，既有手工条目照旧可发）。
    写了任意一个：必须三个都写全，缺的列进 ``outreach_missing``。
    """
    item = item if isinstance(item, dict) else {}
    provided = [name for name in OUTREACH_ELEMENTS if _text(item.get(name))]
    if not provided:
        return {"outreach_ready": None, "outreach_missing": []}
    missing = [name for name in OUTREACH_ELEMENTS if not _text(item.get(name))]
    return {"outreach_ready": not missing, "outreach_missing": missing}


def describe_missing(missing: Sequence[str]) -> str:
    return "、".join(OUTREACH_LABELS.get(name, name) for name in missing)


def is_outreach_ready(item: Dict[str, Any]) -> bool:
    """选条目用：只有明确判为未就绪才拦；未评估的旧条目照旧放行。"""
    return item.get("outreach_ready") is not False


# ---------------------------------------------------------------------------
# 人读渲染（CLI 用；也是「未接入 ≠ 0」的文案出口）
# ---------------------------------------------------------------------------

def _fmt_number(value) -> str:
    return "取不到" if value is None else f"{value:,}"


def render_summary(summary: Dict[str, Any]) -> str:
    scope = summary.get("run") or "（全部 run）"
    window = summary.get("window") or {}
    span = (f"{window.get('start') or '不限'} ~ {window.get('end') or '不限'}"
            if window else "不限时间窗")
    lines = [f"# 三层结果 · {scope}（{span}）", ""]
    for key in LAYERS:
        block = summary.get(key) or {}
        lines.append(f"- {key}（{block.get('state', 'UNKNOWN')}）：{block.get('text', '')}")
    attribution = summary.get("attribution") or {}
    counts = attribution.get("summary") or {}
    lines += [
        "",
        "## 激活归因（非因果）",
        "",
        "| 状态 | 人数 | 含义 |",
        "|---|---|---|",
    ]
    for state in ATTRIBUTION_STATES:
        lines.append(f"| {state} | {_fmt_number(counts.get(state, 0))} | "
                     f"{ATTRIBUTION_LABELS[state]} |")
    activations = attribution.get("activations") or []
    if activations:
        lines += ["", "一个人一条："]
        for row in activations:
            lines.append(
                f"- {row['subject']}：{row['attribution']}"
                f"（{len(row.get('touches') or [])} 条触达，"
                f"causal_credit={row['causal_credit']}）")
    lines += ["", f"说明：{attribution.get('note', '')}", ""]
    return "\n".join(lines)


def render_journey(journey: Dict[str, Any]) -> str:
    lines = [
        f"# 一个人的进度 · {journey.get('prospect_ref')}",
        "",
        f"- 当前状态：{journey.get('current_state')}（{journey.get('business_relevance')}）",
        f"- 抑制状态：{journey.get('suppression_state')}",
        f"- 触达记录：{len(journey.get('contacted_actions') or [])} 条"
        f"｜对方回应：{len(journey.get('responses') or [])} 条"
        f"｜产品事件：{len(journey.get('product_events') or [])} 条",
        f"- 资格依据：{journey.get('qualification_basis')}",
        f"- 出现在这些 run：{'、'.join(journey.get('runs_seen') or []) or '取不到'}",
        f"- 下一步允许：{journey.get('next_allowed_action')}",
        f"- 证据更新时间：{journey.get('evidence_updated_at') or '取不到'}",
        f"- 来源：{'；'.join(journey.get('source_refs') or []) or '取不到'}",
        "",
        "这只是只读归并：状态只认平台回执与已核验业务事件，猜测不提升状态。",
        "",
    ]
    return "\n".join(lines)
