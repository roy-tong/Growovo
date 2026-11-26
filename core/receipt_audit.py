"""Read-only wave-to-ledger receipt audits; never resolve or retry automatically."""
from __future__ import annotations

import ast
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


def live_verify(record: Dict[str, Any], browser=None, data_dir=None) -> Dict[str, Any]:
    """只读回执核验：绝不与进行中的写入共用标签页。

    先按路线看有没有任何账号在写（写入方可能用默认账号名），再用回执自带的
    账号名拿同一把租约；两层都过才真正驱动浏览器。
    """
    if data_dir is not None:
        from . import growth_runtime
        from .action_gateway import AccountBusy, lease
        channel = str(record.get("channel") or "")
        account = str(record.get("identity_handle") or record.get("account") or "").strip()
        if growth_runtime.execution_lane_busy(data_dir, channel)["busy"]:
            return {"status": "unknown", "detail": "browser_write_in_progress"}
        try:
            with lease(data_dir, account=account or "default", route=channel):
                return _live_verify(record, browser=browser)
        except AccountBusy:
            return {"status": "unknown", "detail": "browser_write_in_progress"}
    return _live_verify(record, browser=browser)


def _live_verify(record: Dict[str, Any], browser=None) -> Dict[str, Any]:
    evidence = str(record.get("evidence") or "").strip()
    channel = str(record.get("channel") or "")
    if channel == "github":
        match = _GITHUB_RECEIPT.fullmatch(evidence)
        if not match:
            return {"status": "unknown", "detail": "invalid_github_receipt"}
        repo, _, comment_id = match.groups()
        got = gh_post.run(["api", "--method", "GET", "repos/%s/issues/comments/%s" % (repo, comment_id)])
        if got.get("ok"):
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
        return _browser_verify(record, browser=browser)
    if channel in {"x", "discord"}:
        return _browser_verify(record, browser=browser)
    return {"status": "unknown", "detail": "live_verifier_not_implemented"}


def _browser_verify(record: Dict[str, Any], browser=None) -> Dict[str, Any]:
    if browser is None:
        from .browser_attach import BrowserAttach
        browser = BrowserAttach("ovo-night-run")
    channel = str(record.get("channel") or "")
    evidence = str(record.get("evidence") or "").strip()
    try:
        if channel == "github":
            match = _GITHUB_RECEIPT.fullmatch(evidence)
            if not match:
                return {"status": "unknown", "detail": "invalid_github_receipt"}
            comment_id = match.group(3)
            tab = browser.attach_site("github")
            code = ("fetch(%r, {credentials: 'include'}).then(async response => {"
                    "const text = await response.text(); return {status: response.status, "
                    "found: text.includes(%r)};})" % (evidence, "issuecomment-" + comment_id))
            got = browser.eval(tab, code) or {}
            if got.get("status") == 200 and got.get("found"):
                return {"status": "verified", "detail": "browser_comment_read_back", "observed": evidence}
            if got.get("status") in {404, 410}:
                return {"status": "mismatch", "detail": "comment_or_repository_not_found", "observed": got.get("status")}
            return {"status": "unknown", "detail": "browser_comment_unverified", "observed": got}
        if channel == "x":
            handle = str(record.get("identity_handle") or "")
            if not evidence.isdigit() or not handle:
                return {"status": "unknown", "detail": "missing_x_receipt_or_identity"}
            url = "https://x.com/%s/status/%s" % (handle, evidence)
            tab = browser.attach_site("x")
            browser.route_spa(tab, url)
            text = browser.read_page(tab, max_chars=20000)
            actual_url = str(browser.eval(tab, "location.href") or "")
            unavailable = any(marker in text for marker in (
                "This page doesn’t exist", "This page doesn't exist", "此页面不存在", "No such post"))
            if unavailable:
                return {"status": "mismatch", "detail": "status_not_found", "observed": actual_url}
            if evidence in actual_url and handle in text and "status" in actual_url:
                return {"status": "verified", "detail": "browser_status_read_back", "observed": actual_url}
            return {"status": "unknown", "detail": "browser_status_unverified", "observed": actual_url}
        if channel == "discord":
            try:
                payload = ast.literal_eval(evidence)
            except (ValueError, SyntaxError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            detail = str(record.get("detail") or "")
            actual_id = re.search(r"['\"](?:serverId|lastIdAfter)['\"]\s*:\s*['\"](\d+)['\"]", detail)
            message_id = (str(record.get("operations_receipt_id") or "") or
                          (actual_id.group(1) if actual_id else
                           str(payload.get("serverId") or payload.get("lastId") or "")))
            content = str(payload.get("content") or payload.get("lastText") or "")
            if not message_id:
                matched_id = re.search(r"['\"]lastId['\"]\s*:\s*['\"](\d+)['\"]", evidence)
                message_id = matched_id.group(1) if matched_id else ""
            if not content:
                matched_text = re.search(r"['\"]lastText['\"]\s*:\s*['\"](.*?)['\"]\s*,\s*['\"]mine['\"]", evidence, re.S)
                content = matched_text.group(1) if matched_text else ""
            channel_id = str(record.get("target") or "")
            if not message_id or not content or not channel_id.isdigit():
                return {"status": "unknown", "detail": "missing_discord_receipt"}
            tab = browser.attach_site("discord")
            current_url = str(browser.eval(tab, "location.href") or "")
            guild = re.search(r"/channels/(\d+)/", current_url)
            if not guild:
                return {"status": "unknown", "detail": "discord_guild_unavailable"}
            browser.route_spa(tab, "https://discord.com/channels/%s/%s" % (guild.group(1), channel_id))
            expected_row = "chat-messages-%s-%s" % (channel_id, message_id)
            identity = browser.observe_identity("discord", tab)
            if str(record.get("identity_handle") or "") != str(identity.get("handle") or ""):
                return {"status": "mismatch", "detail": "discord_identity_mismatch"}
            code = ("Array.from(document.querySelectorAll('[id^=\"chat-messages-\"]'))"
                    ".map(node => ({id: node.id, text: node.innerText}))")
            rows = browser.eval(tab, code) or []
            matched = next((row for row in rows if row.get("id") == expected_row), None)
            if matched and content in str(matched.get("text") or ""):
                return {"status": "verified", "detail": "browser_message_read_back", "observed": message_id}
            if matched:
                return {"status": "mismatch", "detail": "discord_content_mismatch", "observed": message_id}
            return {"status": "unknown", "detail": "discord_message_not_in_current_view", "observed": message_id}
    except Exception as exc:
        return {"status": "unknown", "detail": "browser_verifier_unavailable: %s" % type(exc).__name__}
    return {"status": "unknown", "detail": "live_verifier_not_implemented"}


def audit(data_dir, *, night: str, scheduled_only: bool = False, live: bool = False,
          verifier=None) -> Dict[str, Any]:
    from . import cycle_report
    data_dir = Path(data_dir)
    _, paths = cycle_report._source_paths(data_dir, night)
    ledger = Ledger(data_dir / "ledger.jsonl").all()
    by_ref = {}
    for record in ledger:
        by_ref.setdefault((str(record.get("campaign") or ""), str(record.get("ref") or "")), []).append(record)
    operations_by_ref = {}
    operations_path = data_dir / "operations.jsonl"
    if operations_path.is_file():
        with operations_path.open(encoding="utf-8") as handle:
            for raw in handle:
                if raw.strip():
                    operation = json.loads(raw)
                    operations_by_ref[str(operation.get("operation_id") or "")] = operation
    items = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        wave_id = str(payload.get("wave") or path.stem.replace("wave-", "", 1))
        if payload.get("dry") or (scheduled_only and not (payload.get("scheduled_job_id") or cycle_report._scheduled_wave(wave_id))):
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
            record = dict(candidates[-1]) if candidates else None
            if record is not None:
                operation = operations_by_ref.get(ref) or {}
                receipt_id = (operation.get("receipt") or {}).get("id")
                if receipt_id:
                    record["operations_receipt_id"] = str(receipt_id)
            if record is None:
                base.update(status="missing_ledger", detail="ledger_record_not_found")
                items.append(base)
                continue
            base.update(evidence=str(record.get("evidence") or ""),
                        resolved_by=str(record.get("resolved_by") or ""),
                        resolution_note=str(record.get("resolution_note") or ""))
            if str(record.get("result") or "") != "committed":
                if live and str(record.get("result") or "") == "indeterminate":
                    checked = verifier(record) if verifier else live_verify(record, data_dir=data_dir)
                    if checked.get("status") == "verified":
                        base.update(status="verified", detail=str(checked.get("detail") or ""))
                    else:
                        base.update(detail="ledger_state_indeterminate; %s" % str(checked.get("detail") or ""))
                else:
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
                checked = verifier(record) if verifier else live_verify(record, data_dir=data_dir)
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
