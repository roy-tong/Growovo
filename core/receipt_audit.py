"""Read-only wave-to-ledger receipt audits; never resolve or retry automatically."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Dict, Optional

from .bridge import gh_post
from .ledger import Ledger

_GITHUB_RECEIPT = re.compile(
    r"^https://github\.com/([^/]+/[^/]+)/(?:issues|pull)/(\d+)#issuecomment-(\d+)$")


def _evidence_format(record: Dict[str, Any]) -> str:
    evidence = str(record.get("evidence") or "").strip()
    channel = str(record.get("channel") or "")
    if channel == "github":
        return "ok" if _GITHUB_RECEIPT.fullmatch(evidence) else "invalid_github_receipt"
    if channel == "x":
        return "ok" if evidence.isdigit() else "invalid_x_receipt"
    if channel == "discord":
        return "ok" if evidence else "invalid_discord_receipt"
    return "unsupported_channel_receipt"


def live_verify(record: Dict[str, Any]) -> Dict[str, Any]:
    """Independently read a GitHub comment; other channels stay explicitly unknown."""
    evidence = str(record.get("evidence") or "").strip()
    match = _GITHUB_RECEIPT.fullmatch(evidence)
    if str(record.get("channel") or "") != "github":
        return {"status": "unknown", "detail": "live_verifier_not_implemented"}
    if not match:
        return {"status": "unknown", "detail": "invalid_github_receipt"}
    repo, _, comment_id = match.groups()
    got = gh_post.run(["api", "--method", "GET", "repos/%s/issues/comments/%s" % (repo, comment_id)])
    if not got.get("ok"):
        return {"status": "unknown", "detail": str(got.get("err") or got.get("code") or "read failed")[:300]}
    try:
        payload = json.loads(got.get("out") or "")
    except (TypeError, json.JSONDecodeError):
        return {"status": "unknown", "detail": "unparseable_comment_response"}
    actual = str(payload.get("html_url") or "")
    if not actual:
        return {"status": "unknown", "detail": "comment_url_missing"}
    if actual != evidence:
        return {"status": "mismatch", "detail": "comment_url_mismatch", "observed": actual}
    return {"status": "verified", "detail": "comment_read_back", "observed": actual}


def audit(data_dir, *, night: str, scheduled_only: bool = False, live: bool = False,
          verifier=None) -> Dict[str, Any]:
    from . import cycle_report
    data_dir = Path(data_dir)
    _, paths = cycle_report._source_paths(data_dir, night)
    ledger = Ledger(data_dir / "ledger.jsonl").all()
    by_ref = {}
    for record in ledger:
        by_ref.setdefault((str(record.get("campaign") or ""), str(record.get("ref") or "")), []).append(record)
    items = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        wave_id = str(payload.get("wave") or path.stem.replace("wave-", "", 1))
        if payload.get("dry") or (scheduled_only and not cycle_report._scheduled_wave(wave_id)):
            continue
        campaign = str(payload.get("campaign") or "")
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        for index, result in enumerate(results):
            if not isinstance(result, dict):
                continue
            ref = str(result.get("ref") or "")
            wave_state = str(result.get("ledger_result") or result.get("result") or "unknown")
            base = {
                "wave": wave_id, "campaign": campaign, "index": index, "ref": ref,
                "channel": str(result.get("channel") or ""),
                "action": str(result.get("action") or ""),
                "target": str(result.get("target") or ""), "wave_state": wave_state,
                "status": "needs_reconciliation", "detail": "", "evidence": "",
                "resolved_by": "", "resolution_note": "",
            }
            candidates = by_ref.get((campaign, ref), []) if ref else []
            record = candidates[-1] if candidates else None
            if record is None:
                base.update(status="missing_ledger", detail="ledger_record_not_found")
                items.append(base)
                continue
            base.update(evidence=str(record.get("evidence") or ""),
                        resolved_by=str(record.get("resolved_by") or ""),
                        resolution_note=str(record.get("resolution_note") or ""))
            if str(record.get("result") or "") != "committed":
                base.update(detail="ledger_state_%s" % (record.get("result") or "unknown"))
                items.append(base)
                continue
            if wave_state != "committed" and not record.get("resolved_by"):
                base.update(detail="wave_not_committed_without_resolution")
                items.append(base)
                continue
            if not str(record.get("evidence") or "").strip():
                base.update(status="missing_evidence", detail="receipt_evidence_missing")
                items.append(base)
                continue
            format_state = _evidence_format(record)
            if format_state != "ok":
                base.update(status="invalid_receipt", detail=format_state)
                items.append(base)
                continue
            if live:
                checked = (verifier or live_verify)(record)
                base.update(status=str(checked.get("status") or "unknown"),
                            detail=str(checked.get("detail") or "")[:300])
            else:
                base.update(status="structural_valid", detail="wave_ledger_receipt_shape_match")
            items.append(base)
    counts: Dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    accepted = {"verified"} if live else {"structural_valid"}
    return {
        "schema_version": "growovo.receipt-audit/1",
        "night": night, "level": "live" if live else "structural",
        "scheduled_only": bool(scheduled_only),
        "ok": all(item["status"] in accepted for item in items),
        "counts": dict(sorted(counts.items())), "items": items,
        "writes": 0, "retries": 0,
        "note": "只读核对，不修改账本、不自动重发；structural_valid 不等于线上已验证，unknown/mismatch 必须人工裁决。",
    }
