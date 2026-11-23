from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from . import verovo_handoff


class ProofExportError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _hash_value(value: Dict[str, Any]) -> str:
    return _hash_bytes(_canonical(value))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_id(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "-" for char in value).strip("-") or "run"


def _status(checks: List[Dict[str, Any]]) -> str:
    statuses = {item["status"] for item in checks}
    if "FAIL" in statuses:
        return "FAIL"
    if statuses & {"UNCHECKED", "INDETERMINATE"}:
        return "BLOCKED"
    if "EXPIRED" in statuses:
        return "EXPIRED"
    return "PASS"


def _trace_status(result: str) -> str:
    if result == "committed":
        return "ok"
    if result in ("failed", "rejected"):
        return "failed"
    if result == "indeterminate":
        return "unknown"
    return "skipped"


def _evidence(entry_id: str, source_ref: str, source_hash: str, observed_at: str,
              kind: str = "log", trust: str = "observed") -> Dict[str, Any]:
    return {
        "evidence_id": entry_id, "kind": kind, "source_ref": source_ref,
        "source_hash": source_hash, "observed_at": observed_at,
        "collected_by": "growovo proof export", "trust": trust,
    }


def _check(check_id: str, level: str, status: str, claim: str, independence: str,
           evidence_refs: List[str], scope: str, reason: str = "") -> Dict[str, Any]:
    return {
        "check_id": check_id, "level": level, "status": status, "claim": claim,
        "checker": {"name": "growovo proof export", "version": "1", "independence": independence},
        "evidence_refs": evidence_refs, "scope": scope, "reason": reason, "valid_until": None,
    }


def export_proof(data_dir: str | Path, run_id: str, out_dir: str | Path, *, by: str,
                 classification: str = "private") -> Dict[str, Any]:
    if not by.strip():
        raise ProofExportError("REVIEW_REQUIRED: named proof reviewer is required")
    if classification not in ("private", "synthetic", "public-redacted"):
        raise ProofExportError("INPUT_REQUIRED: unknown classification")
    if Path(out_dir).exists():
        raise ProofExportError("OUTPUT_EXISTS: choose a new output directory")
    data_dir = Path(data_dir).resolve()
    out_dir = Path(out_dir).resolve()
    handoff = verovo_handoff.build_handoff(data_dir, run_id)
    operations = handoff["operations"]
    readbacks = handoff.get("readbacks") or []
    events = handoff["business_events"]
    public = classification == "public-redacted"
    generated_at = _now()
    safe_run = _safe_id(run_id)
    contract_id = "growovo-%s-proof" % safe_run
    trace_id = "trace-%s-proof" % safe_run
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=out_dir.name + ".tmp-", dir=str(out_dir.parent)))
    try:
        summary = {
            "schema": "growovo.proof-summary/1",
            "run_id": run_id,
            "generated_at": generated_at,
            "operations": [{
                "operation_id": row.get("operation_id"), "channel": row.get("channel"),
                "action": row.get("action"), "result": row.get("result"),
                "content_sha256": row.get("content_sha256"),
                "receipt_id": None if public else (row.get("receipt") or {}).get("id"),
                "readback_sha256": (row.get("receipt") or {}).get("readback_sha256"),
            } for row in operations],
            "business_events": [{
                "event_id": row.get("event_id"), "event_type": row.get("event_type"),
                "verification_state": row.get("verification_state") or "unknown",
            } for row in events],
            "causality": "not_claimed",
            "private_fields_excluded": ["target", "account", "body", "subject_id", "source_ref", "authorization"],
        }
        summary_path = temp / "artifacts" / "run-summary.json"
        _write_json(summary_path, summary)
        summary_hash = _hash_bytes(summary_path.read_bytes())
        criterion = "Run summary 只含脱敏操作与事件投影，原始目标、正文、账号和主体未复制"
        contract = {
            "schema": "ovo.task-contract/1", "contract_id": contract_id, "version": "1",
            "created_at": generated_at, "owner": by, "product": "growovo",
            "goal": "复核 run %s 的执行回执、最终状态与外部业务事件" % run_id,
            "inputs": [{"ref": "operations.jsonl", "kind": "operation_log"},
                       {"ref": "outcomes/product_events.jsonl", "kind": "product_event_log"}],
            "deliverables": [{
                "id": "growth-proof", "description": "脱敏运行摘要、分层检查和重放清单",
                "acceptance_criteria": [criterion],
                "out_of_scope": ["自动宣称增长、交付验收、收入或因果", "复制私信、联系方式、正文或授权文件"],
            }],
            "approved_by": by,
        }
        steps = [{
            "step_id": "export-growth-proof", "action": "export_proof",
            "started_at": generated_at, "ended_at": generated_at, "status": "ok",
            "tool": "growovo.proof_export", "output_ref": "artifacts/run-summary.json",
        }]
        entries = []
        checks = []
        for row in operations:
            operation_id = str(row.get("operation_id") or "unknown")
            evidence_id = "operation-" + _safe_id(operation_id)
            result = str(row.get("result") or "indeterminate")
            receipt = row.get("receipt") or {}
            step = {
                "step_id": operation_id, "action": "%s.%s" % (row.get("channel"), row.get("action")),
                "started_at": str(row.get("started_at") or generated_at),
                "ended_at": str(row.get("finished_at") or generated_at),
                "status": _trace_status(result), "tool": "growovo.operations/2",
                "output_ref": "operations.jsonl#%s" % operation_id,
            }
            external_id = None if public else (str(receipt.get("id") or "") or None)
            if external_id is not None:
                step["external_id"] = external_id
            steps.append(step)
            entries.append(_evidence(evidence_id, "operations.jsonl#%s" % operation_id,
                                     _hash_value(row), str(row.get("finished_at") or generated_at)))
            if result == "committed":
                execution_status, refs, reason = "PASS", [evidence_id], ""
            elif result == "failed":
                execution_status, refs, reason = "FAIL", [evidence_id], "操作日志记录 failed"
            elif result == "indeterminate":
                execution_status, refs, reason = "INDETERMINATE", [evidence_id], "外部提交结果未裁决，禁止自动重试"
            else:
                execution_status, refs, reason = "UNCHECKED", [], "结果 %s 不代表已发生外部写动作" % result
            checks.append(_check(
                "execution:" + operation_id, "P0", execution_status,
                "操作 %s 的执行回执终态" % operation_id, "executor_observed", refs,
                "只证明该操作记录与回执，不证明互动、激活或收入", reason))
            if result == "committed":
                content_hash = str(row.get("content_sha256") or "")
                candidates = [rb for rb in readbacks
                              if str(rb.get("operation_id") or "") == operation_id + "::readback"]
                rb = candidates[-1] if candidates else {}
                rb_receipt = rb.get("receipt") or {}
                rb_account = rb.get("account") or {}
                op_account = row.get("account") or {}
                account_fields = ("platform_user_id", "handle")
                account_match = any(str(op_account.get(field) or "") and
                                    str(op_account.get(field) or "") == str(rb_account.get(field) or "")
                                    for field in account_fields)
                account_conflict = any(op_account.get(field) and rb_account.get(field) and
                                       str(op_account[field]) != str(rb_account[field])
                                       for field in account_fields)
                readback_ok = bool(
                    rb.get("result") == "verified" and content_hash and
                    rb.get("channel") == row.get("channel") and
                    rb.get("action") == row.get("action") and
                    rb.get("target") == row.get("target") and
                    rb.get("content_sha256") == content_hash and
                    rb_receipt.get("readback_sha256") == content_hash and
                    (rb_receipt.get("id") or rb_receipt.get("url")) and
                    (not receipt.get("id") or receipt.get("id") == rb_receipt.get("id")) and
                    account_match and not account_conflict and rb.get("finished_at") and
                    (rb.get("readback") or {}).get("source"))
                readback_evidence = "readback-" + _safe_id(operation_id)
                if readback_ok:
                    entries.append(_evidence(
                        readback_evidence, "operations.jsonl#%s" % rb.get("operation_id"),
                        _hash_value(rb), str(rb.get("finished_at")), "independent_state", "observed"))
                checks.append(_check(
                    "final-state:" + operation_id, "P2", "PASS" if readback_ok else "UNCHECKED",
                    "目标系统独立回读与当次动作、账号和全文一致", "independent_state",
                    [readback_evidence] if readback_ok else [],
                    "仅限该渠道、动作、账号和回读时间；不外推业务语义",
                    "" if readback_ok else "缺少绑定当次动作、账号、全文哈希与时间的独立 readback 记录"))
        event_entries = 0
        for row in events:
            event_id = str(row.get("event_id") or "unknown")
            evidence_id = "event-" + _safe_id(event_id)
            entries.append(_evidence(evidence_id, "outcomes/product_events.jsonl#%s" % event_id,
                                     _hash_value(row), str(row.get("occurred_at") or generated_at),
                                     "third_party", "reported"))
            event_entries += 1
            verified = row.get("verification_state") == "verified"
            checks.append(_check(
                "external:" + event_id, "P4", "PASS" if verified else "INDETERMINATE",
                "外部产品事件 %s 的来源与核验状态" % event_id, "external_observation",
                [evidence_id], "仅该事件来源和时间窗；causality=not_claimed",
                "" if verified else "事件未带来源核验资格"))
        if not event_entries:
            checks.append(_check(
                "external-business-events", "P4", "UNCHECKED", "外部互动、激活或成交事件",
                "external_observation", [], "该 run 的业务事件覆盖",
                "没有随包提供外部产品事件；未知不是 0"))
        entries.append(_evidence("run-summary", "artifacts/run-summary.json", summary_hash,
                                 generated_at, "source_document", "derived"))
        checks.insert(0, _check(
            "summary-readable", "P1", "PASS", "脱敏运行摘要存在且哈希可复核",
            "executor_observed", ["run-summary"], "只证明摘要可读与字段最小化"))
        trace = {
            "schema": "ovo.execution-trace/1", "trace_id": trace_id, "contract_id": contract_id,
            "started_at": min((step["started_at"] for step in steps), default=generated_at),
            "finished_at": max((step["ended_at"] for step in steps), default=generated_at),
            "actor": "growovo proof export", "steps": steps,
        }
        bundle = {
            "schema": "ovo.acceptance-bundle/1", "task_contract": contract,
            "execution_trace": trace,
            "evidence_ledger": {"schema": "ovo.evidence-ledger/1", "ledger_id": "ledger-" + safe_run,
                                "contract_id": contract_id, "entries": entries},
            "acceptances": [{
                "schema": "ovo.acceptance-record/1", "acceptance_id": "accept-growth-proof",
                "contract_id": contract_id, "trace_id": trace_id, "deliverable_id": "growth-proof",
                "criterion": criterion, "verdict": "PASS", "evidence_refs": ["run-summary"],
                "checked_by": by, "checked_at": generated_at,
                "notes": "只验收脱敏投影可读，不验收增长业务结果",
            }],
            "escalations": [],
        }
        proof = {
            "schema": "ovo.proof/1", "proof_id": "proof-" + safe_run,
            "contract_id": contract_id, "product": "growovo", "generated_at": generated_at,
            "environment": {"product_version": _version(), "runtime": "Python " + sys.version.split()[0],
                            "platform": sys.platform},
            "artifacts": [{"artifact_id": "run-summary", "path": "artifacts/run-summary.json",
                           "sha256": summary_hash, "classification": classification}],
            "replay": {"manifest": "replay/manifest.json", "status": "UNAVAILABLE",
                       "missing": ["operations.jsonl"] + (
                           ["outcomes/product_events.jsonl"] if events else [])},
            "limitations": [
                "committed 只证明执行回执，不证明业务增长",
                "P4 外部事件不带来因果功劳；causality=not_claimed",
                "目标、正文、账号、主体和授权原文未复制，只保留必要的安全引用与哈希",
            ],
        }
        missing_inputs = ["operations.jsonl"] + (["outcomes/product_events.jsonl"] if events else [])
        replay = {
            "schema": "ovo.replay-manifest/1", "contract_id": contract_id, "status": "UNAVAILABLE",
            "commands": [{"name": "validate-proof", "argv": ["python3", "-m", "ovo_acceptance", "proof", "${PROOF_DIR}"]}],
            "inputs": [{"ref": entry["source_ref"], "sha256": entry["source_hash"]} for entry in entries],
            "missing": missing_inputs,
        }
        _write_json(temp / "bundle.json", bundle)
        _write_json(temp / "contract.json", contract)
        with (temp / "trace.jsonl").open("w", encoding="utf-8") as handle:
            for step in steps:
                handle.write(json.dumps(step, ensure_ascii=False) + "\n")
        _write_json(temp / "checks.json", {"schema": "ovo.proof-checks/1", "contract_id": contract_id, "checks": checks})
        _write_json(temp / "proof.json", proof)
        _write_json(temp / "replay" / "manifest.json", replay)
        temp.rename(out_dir)
        return {"status": "exported", "schema": "growovo.proof-export/1", "out": str(out_dir),
                "proof_status": _status(checks), "operations": len(operations),
                "business_events": len(events), "external_action_executed_by_export": False,
                "causality": "not_claimed"}
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def _version() -> str:
    try:
        return importlib.metadata.version("growovo")
    except importlib.metadata.PackageNotFoundError:
        return "dev"
