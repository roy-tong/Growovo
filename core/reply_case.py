#!/usr/bin/env python3
"""回复案例（G-NEXT-1）：授权回复导入、统一队列、人工改判、DNC。

边界：
- 只读本地导入的回复文件；不连邮箱、不自动回复、不自动预约；
- 自动分类只产生候选，改判必须具名；interested 不等于有效线索或 L3；
- DNC 只写本地抑制源：contacts.jsonl（原子替换），失败兜底 suppress.txt，
  两者都失败则写 state/PAUSED 并保持失败态（fail closed）；
- 原文只作数据：投影与队列只给截断摘要，页面渲染前统一转义；
- 回复案例是追加式版本日志：改判/DNC 追加新版本，旧候选永不覆盖。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .ledger import _append_line, _read_jsonl_fail_loud

SCHEMA = "growovo.reply-case/1"
CONFLICT_SCHEMA = "growovo.reply-conflict/1"
IMPORT_SCHEMA = "growovo.reply-import/1"
EVENT_SCHEMA = "growovo.reply-event/1"
QUEUE_SCHEMA = "growovo.reply-review-queue/1"

LABELS = ("unsubscribe", "not_interested", "OOO", "interested", "question",
          "unknown")
CHANNELS = ("email", "x", "linkedin", "reddit", "github")
DNC_STATES = ("unknown", "not_requested", "pending", "active", "failed")

QUEUE_HEADERS = [
    "reply_case_id", "received_at", "account_id", "channel", "target",
    "run_id", "link_state", "classification_candidate", "confidence",
    "review_state", "human_label", "dnc_state", "next_actor",
    "next_required_action", "raw_excerpt",
]


class ReplyError(Exception):
    """回复导入/改判/DNC 的可预期操作错误（CLI 给业务可读提示）。"""


class ReplyStoreError(ReplyError):
    """回复案例存储损坏：静默跳过会让旧候选/DNC 记录变成没发生。"""


def _paths(data_dir) -> Dict[str, Path]:
    base = Path(data_dir) / "replies"
    return {
        "cases": base / "reply-cases.jsonl",
        "conflicts": base / "reply-import-conflicts.jsonl",
        "imports": base / "reply-imports.jsonl",
        "events": base / "reply-events.jsonl",
        "queue": base / "reply-review-queue.csv",
    }


def _now(now=None) -> str:
    if now is None:
        return datetime.now().isoformat(timespec="seconds")
    if isinstance(now, datetime):
        return now.isoformat(timespec="seconds")
    return str(now)


def _text(value) -> str:
    return str(value if value is not None else "").strip()


def _body(value) -> str:
    """原文保留原样（不 strip）：空白变化也是内容变化，要能被导入冲突发现。"""
    return "" if value is None else str(value)


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_hash(*, source_ref: str, raw_text: str, target: str,
                 received_at: str, subject: str = "",
                 external_id: str = "", structured: bool = False) -> str:
    return _sha(_canonical({
        "source_ref": source_ref,
        "raw_text_hash": _sha(raw_text),
        "target": target,
        "received_at": received_at,
        "subject": subject,
        "external_id": external_id,
        "structured_unsubscribe": bool(structured),
    }))


def case_id_for(source_ref: str) -> str:
    """来源身份决定稳定 ID：同一 source_ref 永远落回同一案例。"""
    return "rc-" + _sha(_text(source_ref))[:16]


def _append(path: Path, rec: Dict[str, Any]) -> None:
    """与账本同一追加纪律：O_APPEND + flock + 单次 write。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    _append_line(path, json.dumps(rec, ensure_ascii=False) + "\n")


def _read(path: Path, kind: str) -> List[Dict[str, Any]]:
    try:
        return _read_jsonl_fail_loud(path, kind=kind)
    except Exception as exc:  # noqa: BLE001 统一转成可展示的存储错误
        raise ReplyStoreError("%s读取失败：%s" % (kind, exc)) from exc


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.tmp-%d" % (path.name, os.getpid()))
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 读侧：最新版本
# ---------------------------------------------------------------------------

def load_latest(data_dir) -> Dict[str, Dict[str, Any]]:
    """返回 reply_case_id → 最高版本记录；坏行/缺 ID/坏版本号都显式报错。"""
    latest: Dict[str, Dict[str, Any]] = {}
    for index, rec in enumerate(
            _read(_paths(data_dir)["cases"], kind="回复案例"), 1):
        if not isinstance(rec, dict):
            raise ReplyStoreError("回复案例第 %d 行不是对象" % index)
        cid = _text(rec.get("reply_case_id"))
        if not cid:
            raise ReplyStoreError("回复案例第 %d 行缺少 reply_case_id" % index)
        try:
            version = int(rec.get("version") or 0)
        except (TypeError, ValueError) as exc:
            raise ReplyStoreError(
                "回复案例 %s 的 version 不是整数：%r" % (cid, rec.get("version"))
            ) from exc
        previous = latest.get(cid)
        if previous is None or version >= int(previous.get("version") or 0):
            latest[cid] = rec
    return latest


def case_history(data_dir, reply_case_id: str) -> List[Dict[str, Any]]:
    cid = _text(reply_case_id)
    rows = [r for r in _read(_paths(data_dir)["cases"], kind="回复案例")
            if isinstance(r, dict) and _text(r.get("reply_case_id")) == cid]
    rows.sort(key=lambda r: int(r.get("version") or 0))
    return rows


def get_case(data_dir, reply_case_id: str) -> Dict[str, Any]:
    cid = _text(reply_case_id)
    case = load_latest(data_dir).get(cid)
    if case is None:
        raise ReplyError("没有这个回复案例：%s" % (cid or "（空）"))
    return case


# ---------------------------------------------------------------------------
# 分类候选（只产生候选，不做决定）
# ---------------------------------------------------------------------------

_LABEL_ALIASES = {
    "unsubscribe": "unsubscribe", "unsub": "unsubscribe", "opt_out": "unsubscribe",
    "退订": "unsubscribe",
    "not_interested": "not_interested", "not interested": "not_interested",
    "不感兴趣": "not_interested",
    "ooo": "OOO", "out_of_office": "OOO", "out of office": "OOO",
    "休假": "OOO",
    "interested": "interested", "感兴趣": "interested",
    "question": "question", "问题": "question",
    "unknown": "unknown", "未知": "unknown",
}

_UNSUB_WORDS = (
    "unsubscribe", "opt out", "opt-out", "remove me", "stop emailing",
    "stop contacting", "do not contact", "don't contact",
    "退订", "取消订阅", "不要再发", "别再发", "不要联系", "别再联系", "请勿再",
)
_OOO_WORDS = (
    "out of office", "away from the office", "on leave", "annual leave",
    "vacation", "maternity leave", "parental leave",
    "休假", "请假", "不在办公室", "出差", "年假",
)
_NOT_INTERESTED_WORDS = (
    "not interested", "no thanks", "no thank you", "not for us",
    "not a fit", "不感兴趣", "暂时不需要", "不需要", "没兴趣", "暂不考虑",
)
_INTERESTED_WORDS = (
    "interested", "tell me more", "learn more", "would like to know",
    "happy to chat", "book a call", "schedule a call", "let's talk",
    "sounds good", "有兴趣", "感兴趣", "想了解", "了解一下", "可以聊",
    "约个时间", "安排沟通",
)


def normalize_label(value) -> str:
    text = _text(value)
    if not text:
        return ""
    return _LABEL_ALIASES.get(text.lower().replace("-", "_"), "")


def classify_text(subject: str, body: str) -> str:
    blob = ("%s\n%s" % (_text(subject), _body(body))).lower()
    if any(word in blob for word in _UNSUB_WORDS):
        return "unsubscribe"
    if any(word in blob for word in _OOO_WORDS):
        return "OOO"
    if any(word in blob for word in _NOT_INTERESTED_WORDS):
        return "not_interested"
    if any(word in blob for word in _INTERESTED_WORDS):
        return "interested"
    if "?" in blob or "？" in blob:
        return "question"
    return "unknown"


# ---------------------------------------------------------------------------
# 导入：字段映射、幂等、冲突
# ---------------------------------------------------------------------------

COLUMN_ALIASES = {
    "source_ref": ("source_ref", "ref", "message_id", "message-id", "id"),
    "raw_text": ("raw_text", "text", "body", "content", "message"),
    "subject": ("subject", "title"),
    "received_at": ("received_at", "ts", "timestamp", "date", "received"),
    "target": ("target", "from", "sender", "email", "from_email", "author"),
    "external_id": ("external_id", "uid"),
    "run_id": ("run_id", "run"),
    "action_ref": ("action_ref", "action", "ledger_ref"),
    "conversation_ref": ("conversation_ref", "thread_id", "thread",
                         "conversation"),
    "classification": ("classification", "candidate", "category", "label"),
    "confidence": ("confidence", "score"),
    "unsubscribe": ("unsubscribe", "list_unsubscribe", "list-unsubscribe"),
}


def _time_text(value) -> str:
    text = _text(value)
    if not text:
        return ""
    raw = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return ""
    return dt.isoformat(timespec="seconds")


def _truthy(value) -> bool:
    text = _text(value).lower()
    return text not in ("", "0", "false", "no", "n", "off", "否")


def _confidence(value) -> Optional[float]:
    text = _text(value)
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if number < 0 or number > 1:
        return None
    return round(number, 4)


def _load_rows(path) -> List[Any]:
    p = Path(path)
    if not p.is_file():
        raise ReplyError("回复文件不存在：%s" % p)
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ReplyError("回复文件不是合法 UTF-8：%s" % exc) from exc
    suffix = p.suffix.lower()
    try:
        if suffix == ".csv":
            return [dict(row) for row in csv.DictReader(text.splitlines())]
        if suffix in (".jsonl", ".ndjson"):
            out = []
            for number, raw in enumerate(text.splitlines(), 1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ReplyError(
                        "回复文件第 %d 行不是合法 JSON：%s" % (number, exc)
                    ) from exc
            return out
        if suffix == ".json":
            data = json.loads(text)
            if isinstance(data, dict):
                data = data.get("items") or data.get("replies") or []
            if not isinstance(data, list):
                raise ReplyError("JSON 回复文件应为数组或 {items:[...]}")
            return data
    except json.JSONDecodeError as exc:
        raise ReplyError("回复文件不是合法 JSON：%s" % exc) from exc
    raise ReplyError("回复文件格式只支持 .csv / .jsonl / .json：%s" % p)


def _map_row(row: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    mapped: Dict[str, Any] = {}
    unknown: List[str] = []
    for key, value in row.items():
        name = _text(key)
        if not name:
            continue
        hit = None
        lowered = name.lower()
        for canonical, aliases in COLUMN_ALIASES.items():
            if lowered in aliases:
                hit = canonical
                break
        if hit is None:
            unknown.append(name)
            continue
        if hit in mapped and _text(mapped[hit]) != "":
            continue
        mapped[hit] = value
    return mapped, unknown


def _clean_target(value, channel: str) -> str:
    text = _text(value)
    if channel == "email":
        match = re.search(r"[<\(]([^<>\(\)]+@[^<>\(\)]+)[>\)]", text)
        if match:
            return match.group(1).strip()
    return text


def _excerpt(raw_text: str, limit: int = 160) -> str:
    text = _body(raw_text).replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _next_action(candidate: str, *, structured: bool,
                 link_state: str) -> str:
    if candidate == "unsubscribe":
        base = ("结构化退订：可直接执行 grow reply dnc 做确定性抑制"
                if structured else
                "复核退订意图后执行 grow reply dnc；确认前保持待处理")
    elif candidate == "not_interested":
        base = "人工确认后登记 DNC 或结案"
    elif candidate == "interested":
        base = "人工判断是否回复；不自动升级为线索或商机"
    elif candidate == "question":
        base = "人工起草回复"
    elif candidate == "OOO":
        base = "只记录恢复候选；人工排期后再跟进，不自动恢复"
    else:
        base = "人工判读后改判"
    if link_state == "unresolved":
        return "先补登 run/action_ref（当前未关联运行），再：%s" % base
    return base


def _build_case(*, source_ref: str, source_hash: str, account_id: str,
                channel: str, target: str, external_id: str,
                run_id: str, action_ref: str, conversation_ref: str,
                received_at: str, imported_at: str, raw_text: str,
                candidate: str, confidence: Optional[float],
                structured: bool, review_state: str, batch: str,
                imported_by: str, authorization_ref: str,
                warnings: List[str]) -> Dict[str, Any]:
    cid = case_id_for(source_ref)
    link_state = "linked" if run_id else "unresolved"
    if candidate == "unsubscribe" and target:
        dnc_state = "pending"
    elif candidate == "unsubscribe":
        dnc_state = "unknown"
        warnings.append("没有对方身份，无法登记 DNC：%s" % source_ref)
    else:
        dnc_state = "not_requested"
    return {
        "schema": SCHEMA,
        "version": 1,
        "reply_case_id": cid,
        "source_ref": source_ref,
        "source_hash": source_hash,
        "account_id": account_id,
        "channel": channel,
        "target": target,
        "external_id": external_id,
        "run_id": run_id or None,
        "action_ref": action_ref or None,
        "conversation_ref": conversation_ref,
        "link_state": link_state,
        "received_at": received_at,
        "imported_at": imported_at,
        "raw_text": raw_text,
        "raw_text_hash": _sha(raw_text),
        "classification_candidate": candidate,
        "confidence": confidence,
        "calibrated": False,
        "review_state": review_state,
        "reviewer": "",
        "reviewed_at": "",
        "human_label": "",
        "review_note": "",
        "dnc_state": dnc_state,
        "dnc_ref": "",
        "dnc_by": "",
        "dnc_at": "",
        "dnc_reason": "",
        "ooo_return_candidate": candidate == "OOO",
        "structured_unsubscribe": bool(structured),
        "next_actor": "operator",
        "next_required_action": "",
        "imported_by": imported_by,
        "authorization_ref": authorization_ref,
        "import_batch": batch,
        "warnings": warnings,
    }


def _validate_account(data_dir, account_id: str, channel: str) -> str:
    from .accounts import AccountRegistry

    path = Path(data_dir) / "accounts.json"
    if not path.is_file():
        raise ReplyError(
            "账号库不存在：%s；先登记授权账号，再导入回复" % path)
    try:
        account = AccountRegistry(path).get(account_id)
    except KeyError as exc:
        raise ReplyError("未知账号：%s" % account_id) from exc
    if _text(account.get("status")) == "retired":
        raise ReplyError("账号已退役，不能再导入其回复：%s" % account_id)
    platform = _text(account.get("platform"))
    if platform and platform != channel:
        raise ReplyError(
            "账号 %s 的平台是 %s，与 --channel %s 不一致"
            % (account_id, platform, channel))
    return platform


def import_replies(data_dir, path, *, account_id: str, channel: str = "email",
                   imported_by: str, authorization_ref: str,
                   run_id: str = "", ignore_unmapped: bool = False,
                   auto_dnc_structured: bool = False, dry_run: bool = False,
                   now=None) -> Dict[str, Any]:
    """导入授权回复文件；只产生候选与待办，不发送、不自动回复。"""
    data_dir = Path(data_dir)
    account_id = _text(account_id)
    imported_by = _text(imported_by)
    authorization_ref = _text(authorization_ref)
    channel = _text(channel).lower()
    if not account_id:
        raise ReplyError("导入回复必须绑定授权账号（--account）")
    if not imported_by:
        raise ReplyError("导入必须署名（--by）：没有署名的导入无法审计是谁授权的")
    if not authorization_ref:
        raise ReplyError("导入必须给授权引用（--authorization）：工单/邮件/批次")
    if channel not in CHANNELS:
        raise ReplyError("不支持的渠道：%s（可选：%s）"
                         % (channel, "、".join(CHANNELS)))
    _validate_account(data_dir, account_id, channel)

    source_path = Path(path).expanduser()
    rows = _load_rows(source_path)
    mapped_rows = []
    unknown_columns: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ReplyError("回复文件每行必须是对象：%s" % source_path)
        mapped, unknown = _map_row(row)
        mapped_rows.append(mapped)
        for name in unknown:
            if name not in unknown_columns:
                unknown_columns.append(name)
    if unknown_columns and not ignore_unmapped:
        raise ReplyError(
            "存在未审字段映射：%s；确认字段含义后重新导出，或用 "
            "--ignore-unmapped 显式放行（会写入警告）"
            % "、".join(sorted(unknown_columns)))

    at = _now(now)
    batch = "reply-" + _now(now).replace("-", "").replace(":", "").replace(
        "+", "").replace("T", "T")
    batch = "%s-%s" % (batch, _sha(_canonical([str(source_path), at]))[:6])
    latest = load_latest(data_dir)
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    pending: List[Dict[str, Any]] = []
    duplicates: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    relinks: List[Dict[str, Any]] = []
    relink_ids = set()
    if unknown_columns:
        warnings.append("已放行未映射字段：%s" % "、".join(sorted(unknown_columns)))

    for index, mapped in enumerate(mapped_rows, 1):
        raw_text = _body(mapped.get("raw_text"))
        if not raw_text.strip():
            errors.append({"row": index, "reason": "raw_text 为空",
                           "source_ref": _text(mapped.get("source_ref"))})
            continue
        external_id = _text(mapped.get("external_id"))
        source_ref = _text(mapped.get("source_ref"))
        if not source_ref:
            source_ref = "%s#%s" % (source_path.name,
                                    external_id or ("row-%d" % index))
            warnings.append("第 %d 行没有 source_ref，已按文件+行号生成：%s"
                            % (index, source_ref))
        received_at = _time_text(mapped.get("received_at"))
        if not received_at:
            errors.append({"row": index, "reason": "received_at 缺失或不是 ISO 时间",
                           "source_ref": source_ref})
            continue
        target = _clean_target(mapped.get("target"), channel)
        subject = _text(mapped.get("subject"))
        raw_candidate = _text(mapped.get("classification"))
        if raw_candidate:
            candidate = normalize_label(raw_candidate)
            if not candidate:
                errors.append({"row": index,
                               "reason": "分类候选不在枚举：%s" % raw_candidate,
                               "source_ref": source_ref})
                continue
        else:
            candidate = classify_text(subject, raw_text)
        structured = _truthy(mapped.get("unsubscribe"))
        confidence = _confidence(mapped.get("confidence"))
        if _text(mapped.get("confidence")) and confidence is None:
            warnings.append("第 %d 行 confidence 不是 0～1 的数字，按未校准处理"
                            % index)
        row_run = _text(mapped.get("run_id")) or _text(run_id)
        action_ref = _text(mapped.get("action_ref"))
        conversation_ref = _text(mapped.get("conversation_ref"))
        digest = _source_hash(
            source_ref=source_ref, raw_text=raw_text, target=target,
            received_at=received_at, subject=subject,
            external_id=external_id, structured=structured)
        cid = case_id_for(source_ref)
        previous = latest.get(cid)
        if previous is not None:
            if _text(previous.get("source_hash")) == digest:
                if (row_run and cid not in relink_ids
                        and _text(previous.get("link_state")) == "unresolved"):
                    relink_ids.add(cid)
                    relinks.append({"case": previous, "run_id": row_run,
                                    "action_ref": action_ref,
                                    "reason": "重复导入时携带 run 补关联"})
                    continue
                duplicates.append({"reply_case_id": cid,
                                   "source_ref": source_ref})
                continue
            conflicts.append({
                "schema": CONFLICT_SCHEMA,
                "at": at,
                "reply_case_id": cid,
                "source_ref": source_ref,
                "existing_version": int(previous.get("version") or 1),
                "existing_source_hash": _text(previous.get("source_hash")),
                "incoming_source_hash": digest,
                "incoming_raw_text_hash": _sha(raw_text),
                "incoming_excerpt": _excerpt(raw_text),
                "import_batch": batch,
                "imported_by": imported_by,
                "reason": "同一 source_ref 的内容与旧案例不一致；旧案例保留",
            })
            continue
        review_state = ("pending_review"
                        if candidate == "unsubscribe" and not structured
                        else "proposed")
        case = _build_case(
            source_ref=source_ref, source_hash=digest, account_id=account_id,
            channel=channel, target=target, external_id=external_id,
            run_id=row_run, action_ref=action_ref,
            conversation_ref=conversation_ref, received_at=received_at,
            imported_at=at, raw_text=raw_text, candidate=candidate,
            confidence=confidence, structured=structured,
            review_state=review_state, batch=batch,
            imported_by=imported_by, authorization_ref=authorization_ref,
            warnings=list(warnings))
        case["next_required_action"] = _next_action(
            candidate, structured=structured, link_state=case["link_state"])
        pending.append(case)
        latest[cid] = case

    result: Dict[str, Any] = {
        "schema": IMPORT_SCHEMA,
        "path": str(source_path),
        "batch": batch,
        "account_id": account_id,
        "channel": channel,
        "imported_by": imported_by,
        "authorization_ref": authorization_ref,
        "run_id": _text(run_id),
        "received_at": at,
        "dry_run": bool(dry_run),
        "created": 0,
        "would_create": len(pending),
        "relinked": 0,
        "would_relink": len(relinks),
        "skipped_duplicate": len(duplicates),
        "conflicts": len(conflicts),
        "errors": errors,
        "warnings": warnings,
        "unknown_columns": sorted(unknown_columns),
        "case_ids": [c["reply_case_id"] for c in pending],
        "auto_dnc": 0,
        "auto_dnc_failed": 0,
        "queue_path": str(_paths(data_dir)["queue"]),
        "partial": bool(pending and errors),
    }
    if dry_run:
        return result

    paths = _paths(data_dir)
    for case in pending:
        case["warnings"] = list(warnings)
        _append(paths["cases"], case)
        result["created"] += 1
    for item in relinks:
        previous = item["case"]
        updated = _linked_version(
            previous, run_id=item["run_id"], action_ref=item["action_ref"])
        _append(paths["cases"], updated)
        _append(paths["events"], {
            "schema": EVENT_SCHEMA,
            "event": "link",
            "at": at,
            "reply_case_id": _text(previous.get("reply_case_id")),
            "actor": imported_by,
            "run_id": item["run_id"],
            "action_ref": _text(updated.get("action_ref")),
            "authorization_ref": authorization_ref,
            "from_link_state": _text(previous.get("link_state")),
            "to_link_state": "linked",
            "note": "%s（批次 %s）" % (item["reason"], batch),
        })
        latest[_text(previous.get("reply_case_id"))] = updated
        result["relinked"] += 1
    for conflict in conflicts:
        _append(paths["conflicts"], conflict)

    if auto_dnc_structured:
        for case in pending:
            if not (case.get("structured_unsubscribe")
                    and case.get("classification_candidate") == "unsubscribe"):
                continue
            dnc = confirm_dnc(data_dir, case["reply_case_id"],
                              operator=imported_by,
                              reason="结构化退订（导入批次 %s）" % batch,
                              now=now)
            if dnc.get("ok"):
                result["auto_dnc"] += 1
            else:
                result["auto_dnc_failed"] += 1
                result["warnings"].append(
                    "结构化退订 %s 的 DNC 未生效：%s"
                    % (case["reply_case_id"], dnc.get("error") or "未知原因"))

    _append(paths["imports"], {
        "schema": IMPORT_SCHEMA,
        "batch": batch,
        "at": at,
        "file": str(source_path),
        "account_id": account_id,
        "channel": channel,
        "imported_by": imported_by,
        "authorization_ref": authorization_ref,
        "run_id": _text(run_id),
        "created": result["created"],
        "relinked": result["relinked"],
        "skipped_duplicate": result["skipped_duplicate"],
        "conflicts": result["conflicts"],
        "errors": len(errors),
        "unknown_columns": sorted(unknown_columns),
        "auto_dnc": result["auto_dnc"],
    })
    if (result["created"] or result["relinked"] or result["auto_dnc"]
            or not paths["queue"].exists()):
        rebuild_queue(data_dir)
    return result


def _linked_version(case: Dict[str, Any], *, run_id: str,
                    action_ref: str = "") -> Dict[str, Any]:
    """构造补链版本：只改关联字段，原文、候选与人工审核结果原样保留。"""
    updated = dict(case)
    updated.update({
        "version": int(case.get("version") or 1) + 1,
        "run_id": run_id,
        "action_ref": (_text(action_ref)
                       or _text(case.get("action_ref"))) or None,
        "link_state": "linked",
        "next_actor": "operator",
        "next_required_action": _next_action(
            _text(case.get("classification_candidate")),
            structured=bool(case.get("structured_unsubscribe")),
            link_state="linked"),
    })
    return updated


def link_reply(data_dir, reply_case_id: str, *, run_id: str,
               action_ref: str = "", operator: str,
               authorization_ref: str = "", note: str = "",
               now=None) -> Dict[str, Any]:
    """给未关联回复补登 run/action_ref：追加新版本，旧版本保留。"""
    run_id = _text(run_id)
    operator = _text(operator)
    authorization_ref = _text(authorization_ref)
    if not run_id:
        raise ReplyError("补关联必须给 run id（--run）")
    if not operator:
        raise ReplyError("补关联必须署名（--by）")
    if not authorization_ref:
        raise ReplyError("补关联必须给授权引用（--authorization）：工单/邮件/批次")
    at = _now(now)
    case = get_case(data_dir, reply_case_id)
    if _text(case.get("link_state")) == "linked":
        current_run = _text(case.get("run_id"))
        if current_run == run_id and (
                not action_ref
                or _text(case.get("action_ref")) == _text(action_ref)):
            result = dict(case)
            result["already_linked"] = True
            return result
        raise ReplyError(
            "案例 %s 已关联 %s；改绑要先人工核对，不能用补关联覆盖"
            % (_text(case.get("reply_case_id")), current_run or "（空）"))
    updated = _linked_version(case, run_id=run_id, action_ref=action_ref)
    paths = _paths(data_dir)
    _append(paths["cases"], updated)
    _append(paths["events"], {
        "schema": EVENT_SCHEMA,
        "event": "link",
        "at": at,
        "reply_case_id": _text(case.get("reply_case_id")),
        "actor": operator,
        "run_id": run_id,
        "action_ref": _text(updated.get("action_ref")),
        "authorization_ref": authorization_ref,
        "from_link_state": _text(case.get("link_state")),
        "to_link_state": "linked",
        "note": _text(note) or "CLI 补关联",
    })
    rebuild_queue(data_dir)
    return updated


# ---------------------------------------------------------------------------
# 人工改判
# ---------------------------------------------------------------------------

_REVIEW_NEXT = {
    "unsubscribe": "执行 grow reply dnc 完成抑制",
    "not_interested": "确认是否登记 DNC 或结案",
    "interested": "人工回复；不自动升级为线索或商机",
    "question": "人工起草回复",
    "OOO": "记录恢复候选；人工排期后再跟进",
    "unknown": "人工继续判读",
}


def review_reply(data_dir, reply_case_id: str, *, reviewer: str, label: str,
                 note: str = "", now=None) -> Dict[str, Any]:
    """具名改判：追加新版本，原候选与旧版本保留。"""
    reviewer = _text(reviewer)
    if not reviewer:
        raise ReplyError("改判必须署名（--by）")
    normalized = normalize_label(label)
    if not normalized:
        raise ReplyError("不支持的分类：%s（可选：%s）"
                         % (label, "、".join(LABELS)))
    at = _now(now)
    case = get_case(data_dir, reply_case_id)
    candidate = _text(case.get("classification_candidate"))
    updated = dict(case)
    updated.update({
        "version": int(case.get("version") or 1) + 1,
        "human_label": normalized,
        "review_state": ("confirmed" if normalized == candidate else "overruled"),
        "reviewer": reviewer,
        "reviewed_at": at,
        "review_note": _text(note),
        "next_actor": "operator",
        "next_required_action": _REVIEW_NEXT[normalized],
    })
    target = _text(case.get("target"))
    if normalized == "unsubscribe" and not target:
        updated["dnc_state"] = "unknown"
        updated["next_required_action"] = "没有对方身份，无法登记 DNC：先补 target"
    elif normalized == "unsubscribe" and updated.get("dnc_state") not in (
            "active", "failed"):
        updated["dnc_state"] = "pending"
    elif (normalized != "unsubscribe"
          and updated.get("dnc_state") == "pending"):
        updated["dnc_state"] = "not_requested"
        updated["next_required_action"] += "（未确认的 DNC 待办已撤回）"
    paths = _paths(data_dir)
    _append(paths["cases"], updated)
    _append(paths["events"], {
        "schema": EVENT_SCHEMA,
        "event": "review",
        "at": at,
        "reply_case_id": _text(case.get("reply_case_id")),
        "actor": reviewer,
        "from_label": candidate,
        "to_label": normalized,
        "review_state": updated["review_state"],
        "dnc_state": updated.get("dnc_state"),
        "note": _text(note),
    })
    rebuild_queue(data_dir)
    return updated


# ---------------------------------------------------------------------------
# DNC：唯一抑制写入路径
# ---------------------------------------------------------------------------

def _same_identity(left, right) -> bool:
    a, b = _text(left), _text(right)
    if not a or not b:
        return False
    if "@" in a and "@" in b:
        return a.lower() == b.lower()
    return a == b or a.lower() == b.lower()


def _read_contacts(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for number, raw in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReplyError(
                "contacts.jsonl 第 %d 行不是合法 JSON：%s" % (number, exc)
            ) from exc
        if not isinstance(rec, dict):
            raise ReplyError("contacts.jsonl 第 %d 行不是对象" % number)
        rows.append(rec)
    return rows


def _contact_index(rows: List[Dict[str, Any]], channel: str,
                   target: str) -> int:
    for index, rec in enumerate(rows):
        platforms = rec.get("platforms")
        platforms = platforms if isinstance(platforms, dict) else {}
        if channel in platforms and _same_identity(platforms.get(channel), target):
            return index
        for key in ("email", "handle", "target"):
            if _same_identity(rec.get(key), target):
                return index
    return -1


def _next_contact_id(rows: List[Dict[str, Any]]) -> str:
    used = {_text(r.get("id")).lower() for r in rows if _text(r.get("id"))}
    top = 0
    for rec in rows:
        match = re.fullmatch(r"contact-(\d+)", _text(rec.get("id")))
        if match:
            top = max(top, int(match.group(1)))
    number = top + 1
    while ("contact-%04d" % number) in used:
        number += 1
    return "contact-%04d" % number


@contextmanager
def _contacts_lock(data_dir):
    """与联系人库写入串行化；锁不可用则失败（不降级为无锁重写）。"""
    path = Path(data_dir) / "contacts.jsonl.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError) as exc:
            raise ReplyError("联系人库文件锁不可用：%s" % exc) from exc
        yield
    finally:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        handle.close()


def _write_contacts(path: Path, rows: List[Dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    _atomic_write_text(path, text)


def _write_dnc_contact(data_dir: Path, *, case_id: str, channel: str,
                       target: str, operator: str, reason: str,
                       at: str) -> Tuple[str, List[str]]:
    """写 contacts.jsonl；返回 (contact_id, warnings)。失败抛异常由调用方兜底。"""
    from .gates import load_contact_suppressions

    contacts_path = data_dir / "contacts.jsonl"
    # 先做完整性校验：坏行/坏 schema 不得被整文件重写"洗掉"。
    load_contact_suppressions(data_dir)
    with _contacts_lock(data_dir):
        rows = _read_contacts(contacts_path)
        index = _contact_index(rows, channel, target)
        warnings: List[str] = []
        if index >= 0:
            rec = rows[index]
            contact_id = _text(rec.get("id")) or _next_contact_id(rows)
            rec["id"] = contact_id
        else:
            contact_id = _next_contact_id(rows)
            rec = {
                "id": contact_id,
                "name": target,
                "org": "",
                "title": "",
                "platforms": {channel: target},
                "source": "reply-case:%s" % case_id,
                "hooks": [],
                "verification": {"grade": ""},
                "state": {"dnc": False, "bounced": None, "replies": 0,
                          "last_touch": {}},
                "tier": 2,
                "created_at": at,
            }
            rows.append(rec)
            warnings.append("联系人库没有该目标，已建最小联系人记录 %s" % contact_id)
        state = rec.get("state")
        if not isinstance(state, dict):
            raise ReplyError("联系人 %s 的 state 不是对象，拒绝覆盖" % contact_id)
        state.update({
            "dnc": True,
            "dnc_at": at,
            "dnc_by": operator,
            "dnc_reason": reason,
            "dnc_case": case_id,
        })
        platforms = rec.get("platforms")
        if not isinstance(platforms, dict):
            platforms = {}
        if not _text(platforms.get(channel)):
            platforms[channel] = target
            rec["platforms"] = platforms
        _write_contacts(contacts_path, rows)
    return contact_id, warnings


def _append_suppress(path: Path, target: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(target + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _pause(data_dir: Path, message: str) -> str:
    from .safety import set_pause

    set_pause(data_dir, True, message)
    return str(Path(data_dir) / "state" / "PAUSED")


def confirm_dnc(data_dir, reply_case_id: str, *, operator: str,
                reason: str = "", now=None) -> Dict[str, Any]:
    """确认 DNC：写联系人库，失败兜底 suppress.txt，双失败暂停并留失败版本。

    不提供解除命令：回滚只能关闭新导入/投影，不能削弱已确认的抑制。
    """
    data_dir = Path(data_dir)
    operator = _text(operator)
    if not operator:
        raise ReplyError("DNC 必须署名（--by）")
    at = _now(now)
    case = get_case(data_dir, reply_case_id)
    case_id = _text(case.get("reply_case_id"))
    channel = _text(case.get("channel")) or "email"
    target = _text(case.get("target"))
    if not target:
        raise ReplyError("回复案例没有对方身份，无法登记 DNC：%s" % case_id)

    result: Dict[str, Any] = {
        "schema": EVENT_SCHEMA,
        "ok": False,
        "reply_case_id": case_id,
        "channel": channel,
        "target": target,
        "dnc_state": "",
        "dnc_ref": "",
        "contact_id": "",
        "warnings": [],
        "error": "",
    }
    contact_error = ""
    dnc_ref = ""
    try:
        contact_id, warnings = _write_dnc_contact(
            data_dir, case_id=case_id, channel=channel, target=target,
            operator=operator, reason=_text(reason), at=at)
        result["warnings"].extend(warnings)
        result["contact_id"] = contact_id
        # 写后核验：发送门禁看到的集合里必须真的有这个目标。
        from .gates import load_contact_suppressions
        if target not in load_contact_suppressions(data_dir):
            raise ReplyError("contacts.jsonl 写入后目标不在抑制集")
        dnc_ref = "contacts.jsonl#%s" % contact_id
    except Exception as exc:  # noqa: BLE001 兜底前保留原始错误
        contact_error = "%s: %s" % (type(exc).__name__, exc)
        result["warnings"].append("contacts.jsonl 写入失败：%s" % contact_error)
        try:
            suppress_path = data_dir / "suppress.txt"
            _append_suppress(suppress_path, target)
            from .gates import load_suppress
            if target not in load_suppress(suppress_path):
                raise ReplyError("suppress.txt 写入后目标不在抑制名单")
            dnc_ref = "suppress.txt"
            result["warnings"].append(
                "已退到 suppress.txt 兜底；联系人库里的 DNC 状态未更新")
        except Exception as exc2:  # noqa: BLE001
            error = ("DNC 双通道写入失败：contacts %s；suppress %s: %s"
                     % (contact_error, type(exc2).__name__, exc2))
            result.update({"dnc_state": "failed", "error": error})
            paused = _pause(data_dir, "DNC 写入失败（%s）：%s" % (case_id, error))
            result["paused_path"] = paused
            updated = dict(case)
            updated.update({
                "version": int(case.get("version") or 1) + 1,
                "dnc_state": "failed",
                "dnc_ref": "",
                "dnc_by": operator,
                "dnc_at": at,
                "dnc_reason": _text(reason),
                "next_actor": "operator",
                "next_required_action": (
                    "DNC 写入失败：先修复 contacts.jsonl/suppress.txt 权限，"
                    "再决定人工补录；发送必须保持暂停"),
            })
            _append(_paths(data_dir)["cases"], updated)
            _append(_paths(data_dir)["events"], {
                "schema": EVENT_SCHEMA, "event": "dnc_failed", "at": at,
                "reply_case_id": case_id, "actor": operator,
                "error": error, "target": target,
            })
            rebuild_queue(data_dir)
            return result

    updated = dict(case)
    updated.update({
        "version": int(case.get("version") or 1) + 1,
        "dnc_state": "active",
        "dnc_ref": dnc_ref,
        "dnc_by": operator,
        "dnc_at": at,
        "dnc_reason": _text(reason),
        "review_state": "confirmed",
        "reviewer": _text(case.get("reviewer")) or operator,
        "reviewed_at": _text(case.get("reviewed_at")) or at,
        "human_label": _text(case.get("human_label")) or "unsubscribe",
        "next_actor": "operator",
        "next_required_action": (
            "DNC 已生效；新队列会被发送门禁抑制。"
            "本命令不解除 DNC；如需改判分类用 grow reply review"),
    })
    _append(_paths(data_dir)["cases"], updated)
    _append(_paths(data_dir)["events"], {
        "schema": EVENT_SCHEMA,
        "event": "dnc_active",
        "at": at,
        "reply_case_id": case_id,
        "actor": operator,
        "target": target,
        "channel": channel,
        "dnc_ref": dnc_ref,
        "reason": _text(reason),
    })
    rebuild_queue(data_dir)
    result.update({"ok": True, "dnc_state": "active", "dnc_ref": dnc_ref})
    return result


# ---------------------------------------------------------------------------
# 队列与只读投影
# ---------------------------------------------------------------------------

def queue_rows(latest: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    cases = sorted(
        latest.values(),
        key=lambda r: (_text(r.get("received_at")),
                       _text(r.get("reply_case_id"))),
        reverse=True)
    rows = []
    for case in cases:
        rows.append({
            "reply_case_id": _text(case.get("reply_case_id")),
            "received_at": _text(case.get("received_at")),
            "account_id": _text(case.get("account_id")),
            "channel": _text(case.get("channel")),
            "target": _text(case.get("target")),
            "run_id": _text(case.get("run_id")),
            "link_state": _text(case.get("link_state")),
            "classification_candidate": _text(
                case.get("classification_candidate")),
            "confidence": ("" if case.get("confidence") is None
                           else case.get("confidence")),
            "review_state": _text(case.get("review_state")),
            "human_label": _text(case.get("human_label")),
            "dnc_state": _text(case.get("dnc_state")),
            "next_actor": _text(case.get("next_actor")),
            "next_required_action": _text(case.get("next_required_action")),
            "raw_excerpt": _excerpt(case.get("raw_text") or ""),
        })
    return rows


def rebuild_queue(data_dir) -> Path:
    """重建人工审阅队列 CSV（原子替换）。"""
    path = _paths(data_dir)["queue"]
    rows = queue_rows(load_latest(data_dir))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=QUEUE_HEADERS,
                            lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    _atomic_write_text(path, buffer.getvalue())
    return path


def list_cases(data_dir, *, run_id: str = "", state: str = "",
               limit: int = 0) -> List[Dict[str, Any]]:
    latest = load_latest(data_dir)
    cases = list(latest.values())
    if _text(run_id):
        cases = [c for c in cases if _text(c.get("run_id")) == _text(run_id)]
    wanted = _text(state)
    if wanted:
        if wanted == "unresolved":
            cases = [c for c in cases
                     if _text(c.get("link_state")) == "unresolved"]
        elif wanted == "dnc_pending":
            cases = [c for c in cases if _text(c.get("dnc_state")) == "pending"]
        else:
            cases = [c for c in cases
                     if wanted in (_text(c.get("review_state")),
                                   _text(c.get("dnc_state")))]
    cases.sort(key=lambda r: (_text(r.get("received_at")),
                              _text(r.get("reply_case_id"))), reverse=True)
    return cases[:limit] if limit and limit > 0 else cases


def _project_case(case: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "reply_case_id": _text(case.get("reply_case_id")),
        "source_ref": _text(case.get("source_ref")),
        "source_hash": _text(case.get("source_hash")),
        "received_at": _text(case.get("received_at")),
        "imported_at": _text(case.get("imported_at")),
        "channel": _text(case.get("channel")),
        "target": _text(case.get("target")),
        "run_id": _text(case.get("run_id")),
        "action_ref": _text(case.get("action_ref")),
        "conversation_ref": _text(case.get("conversation_ref")),
        "link_state": _text(case.get("link_state")),
        "classification_candidate": _text(case.get("classification_candidate")),
        "confidence": case.get("confidence"),
        "calibrated": bool(case.get("calibrated")),
        "review_state": _text(case.get("review_state")),
        "human_label": _text(case.get("human_label")),
        "reviewer": _text(case.get("reviewer")),
        "reviewed_at": _text(case.get("reviewed_at")),
        "dnc_state": _text(case.get("dnc_state")),
        "dnc_ref": _text(case.get("dnc_ref")),
        "next_actor": _text(case.get("next_actor")),
        "next_required_action": _text(case.get("next_required_action")),
        "raw_excerpt": _excerpt(case.get("raw_text") or ""),
        "raw_text_hash": _text(case.get("raw_text_hash")),
        "structured_unsubscribe": bool(case.get("structured_unsubscribe")),
        "ooo_return_candidate": bool(case.get("ooo_return_candidate")),
    }


def run_projection(data_dir, run_id: str, *, limit: int = 20) -> Dict[str, Any]:
    """P0 运行详情页的回复区块：只读、失败显式、DNC 写后核验。"""
    data_dir = Path(data_dir)
    block: Dict[str, Any] = {
        "state": "empty",
        "error": "",
        "cases": [],
        "count": 0,
        "linked_count": 0,
        "pending_review": 0,
        "unresolved_count": 0,
        "conflict_count": 0,
        "conflicts": [],
        "dnc": {"pending": 0, "active": 0, "failed": 0,
                "unverified": [], "error": ""},
        "queue_path": str(_paths(data_dir)["queue"]),
        "batches": [],
        "note": "",
    }
    try:
        latest = load_latest(data_dir)
        paths = _paths(data_dir)
        conflicts = _read(paths["conflicts"], kind="回复导入冲突")
        imports = _read(paths["imports"], kind="回复导入批次")
    except Exception as exc:  # noqa: BLE001 页面不因存储坏掉而崩
        block.update(state="unreadable",
                     error="%s: %s" % (type(exc).__name__, exc))
        return block

    resolved_run = _text(run_id)
    linked = [c for c in latest.values()
              if resolved_run and _text(c.get("run_id")) == resolved_run]
    linked.sort(key=lambda r: (_text(r.get("received_at")),
                               _text(r.get("reply_case_id"))), reverse=True)
    hidden = max(0, len(linked) - limit)
    block["cases"] = [_project_case(c) for c in linked[:limit]]
    block["count"] = len(linked)
    block["linked_count"] = len(linked)
    block["pending_review"] = len(
        [c for c in linked
         if _text(c.get("review_state")) in ("proposed", "pending_review")])
    block["unresolved_count"] = len(
        [c for c in latest.values()
         if _text(c.get("link_state")) == "unresolved"])
    block["conflict_count"] = len(conflicts)
    block["conflicts"] = [{
        "at": _text(c.get("at")),
        "reply_case_id": _text(c.get("reply_case_id")),
        "source_ref": _text(c.get("source_ref")),
        "existing_source_hash": _text(c.get("existing_source_hash")),
        "incoming_source_hash": _text(c.get("incoming_source_hash")),
    } for c in conflicts[-10:]]
    block["dnc"]["pending"] = len(
        [c for c in latest.values()
         if _text(c.get("dnc_state")) == "pending"])
    block["dnc"]["active"] = len(
        [c for c in latest.values()
         if _text(c.get("dnc_state")) == "active"])
    block["dnc"]["failed"] = len(
        [c for c in latest.values()
         if _text(c.get("dnc_state")) == "failed"])

    suppressions = set()
    suppression_error = ""
    try:
        from .gates import load_suppressions
        suppressions = load_suppressions(data_dir)
    except Exception as exc:  # noqa: BLE001 抑制源坏掉时门禁本身 fail closed
        suppression_error = "%s: %s" % (type(exc).__name__, exc)
    if suppression_error:
        block["dnc"]["error"] = suppression_error
    for case in latest.values():
        if _text(case.get("dnc_state")) != "active":
            continue
        target = _text(case.get("target"))
        if target and target in suppressions:
            continue
        block["dnc"]["unverified"].append({
            "reply_case_id": _text(case.get("reply_case_id")),
            "target": target,
            "reason": suppression_error or "目标不在当前抑制集",
        })
    block["batches"] = [{
        "batch": _text(b.get("batch")),
        "at": _text(b.get("at")),
        "imported_by": _text(b.get("imported_by")),
        "authorization_ref": _text(b.get("authorization_ref")),
        "created": b.get("created"),
        "relinked": b.get("relinked"),
        "skipped_duplicate": b.get("skipped_duplicate"),
        "conflicts": b.get("conflicts"),
    } for b in imports[-3:]]
    if hidden:
        block["note"] = "另有 %d 条已关联回复未在此页展开" % hidden
    if not latest:
        block["state"] = "empty"
        block["note"] = "还没有导入回复：用 mkt grow reply import 导入授权回复文件"
    elif linked:
        block["state"] = "ok"
    else:
        block["state"] = "empty"
        if not block["note"]:
            block["note"] = ("本次运行没有关联回复；统一队列里另有 %d 条"
                             "未关联回复" % block["unresolved_count"])
    return block
