#!/usr/bin/env python3
"""Growovo 默认 X 执行链验收：WebBridge 受管标签页、owner 授权、执行与回读。

默认 publish/reply 与产品主入口使用同一个 action_gateway → browser-attach 路径。
本脚本不调用 verify_credentials，不连接 9222/9223，不读取或迁移浏览器 profile。
旧 CDP 只能通过产品 CLI 的显式 --legacy-cdp 入口使用，不能用于默认健康验收。

--dry-run 只预览，不连接浏览器或平台。live 必须显式 --authorize-by，并在工作区
已有匹配的 owner-attested 授权；脚本不会代替操作者创建授权。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

CAMPAIGN = "acceptance-live-x"
DEFAULT_TEXT = ("[growovo-acceptance] 受控验收：验证默认 WebBridge 执行、"
                "真实回读与操作日志闭环。")
SYNTHETIC_TARGET = "https://x.com/growovo_test/status/0000000000000000000"
SYNTHETIC_ID = "1790000000000000000"


def _say(msg):
    print(msg, flush=True)


def _blocked(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=1), flush=True)
    return 2


def _target_handle(url):
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme != "https" or parsed.hostname not in {
            "x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    return parts[0].lstrip("@").casefold() if len(parts) >= 3 and parts[1] == "status" else ""


def _valid_target(action, url):
    if action == "publish":
        return not str(url or "").strip()
    return bool(_target_handle(url))


def _managed_browser(data_dir, action):
    from core.browser_attach import BrowserAttach
    from core.platform_compliance import require_owner_attested_browser_write

    attach = BrowserAttach()
    tab = attach.ensure_managed_tab(
        data_dir, "x", "https://x.com/home",
        group_title="OVO X acceptance", prefer_visible=True)
    identity = attach.observe_identity("x", tab)
    permit = require_owner_attested_browser_write(
        "x", action, data_dir, go=True,
        actual_transport="browser-attach", identity=identity)
    return attach, tab, identity, permit


def preflight(args):
    from core import action_resolution, platform_compliance
    from core.operations_log import content_sha256

    action = str(getattr(args, "action", "reply") or "reply")
    target = str(getattr(args, "target_url", "") or "")
    text = str(getattr(args, "text", "") or "")
    data_dir = Path(getattr(args, "workspace", "") or "data")
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("authorized_by", bool(str(getattr(args, "authorize_by", "") or "").strip()),
        "已署名确认" if str(getattr(args, "authorize_by", "") or "").strip()
        else "缺少 --authorize-by")
    add("dedicated_test_target", _valid_target(action, target),
        "自有账号专用测试推文" if action == "reply" else "原创发布不需要目标 URL")
    add("content", bool(text.strip()) and len(text) <= 280,
        "%d 字符；sha256=%s" % (len(text), content_sha256(text)[:12]))
    add("transport", True, "browser-attach（WebBridge 受管标签页；禁止隐式 legacy CDP）")

    recovery = None
    identity = None
    try:
        attach, tab, identity, permit = _managed_browser(data_dir, action)
        add("managed_tab", True, "tabId=%s；session=%s" % (tab, attach.session))
        add("identity", True, "@%s；%s" % (
            identity.get("handle"), identity.get("observed_platform_identity")))
        if action == "reply":
            target_handle = _target_handle(target)
            add("own_test_target", target_handle == str(identity.get("handle") or "").casefold(),
                "目标与当前账号一致" if target_handle == str(identity.get("handle") or "").casefold()
                else "验收只能回复当前账号自己的测试推文，不使用陌生用户目标")
        add("owner_authorization", permit.get("route") == "owner_attested_session",
            "%s；window_end=%s" % (permit.get("route"), permit.get("window_end")))
    except Exception as exc:
        code = str(getattr(exc, "code", "") or type(exc).__name__)
        add("managed_browser", False, "%s：%s" % (code, str(exc)[:240]))
        recovery = action_resolution.recovery(
            {"kind": "channel", "channel": "x", "action": action},
            {"code": code, "detail": str(exc)}, route="browser-attach")

    ready = all(item["ok"] for item in checks)
    payload = {
        "mode": "preflight",
        "transport": "browser-attach",
        "live_proven": False,
        "ready": ready,
        "checks": checks,
        "identity": ({"handle": identity.get("handle"),
                      "browser_context": identity.get("browser_context")}
                     if identity else None),
        "next_required_actions": [item["detail"] for item in checks if not item["ok"]],
        "recovery": recovery,
        "writes": 0,
        "note": "只读检查：复用或创建一个 WebMonitor 受管标签页，不发送、不读取旧 CDP、不迁移 profile",
    }
    _say(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0 if ready else 2


def guard_live(args):
    if getattr(args, "legacy_cdp", False) or str(getattr(args, "cdp_url", "") or ""):
        _say("默认验收拒绝旧 CDP 参数（包括 9222/9223）；请去掉参数并使用 WebBridge 受管标签页。")
        return 2
    if str(getattr(args, "profile_dir", "") or ""):
        _say("默认验收不读取或迁移 profiles/；请去掉 --profile-dir。")
        return 2
    if not str(args.authorize_by or "").strip():
        _say("live 验收必须显式 --authorize-by <确认人>；工作区还须已有 owner-attested 授权。")
        return 2
    if not _valid_target(args.action, args.target_url):
        _say("reply 需要自有账号的专用测试推文 URL；publish 不应传 --target-url。")
        return 2
    return None


def print_six_parties(*, authorize_by, operator, approval_ref, policy_sha,
                      account, browser_ref, evidence_paths, readback_note):
    _say("")
    _say("六方分离表（谁授权 / 谁操作 / 凭什么批 / 哪个账号 / 哪个浏览器 / 怎么核验）")
    _say("  ① 授权人（用户）      ：%s" % authorize_by)
    _say("  ② 操作者（引擎入口）  ：%s" % operator)
    _say("  ③ 审批依据            ：%s" % approval_ref
         + ("（policy sha256=%s…）" % policy_sha[:12] if policy_sha else "（无）"))
    _say("  ④ 执行账号（平台观测）：%s @ %s（observed_at=%s）" % (
        account.get("platform_user_id") or "（未观测到）",
        account.get("handle") or "?", account.get("observed_at") or "?"))
    _say("  ⑤ 浏览器上下文        ：%s（不保存凭据）" % browser_ref)
    _say("  ⑥ 回读与证据          ：%s" % readback_note)
    for label, path in evidence_paths:
        _say("       %s：%s" % (label, path))
    _say("")


def _proof(identity, dry_run):
    from core.account_identity import AccountIdentityProof

    if dry_run:
        return AccountIdentityProof(
            channel="x", user_id="dryrun-000000", handle="dryrun_accept",
            browser_context="offline:synthetic", source_ref="offline:dry-run")
    return AccountIdentityProof(
        channel="x", user_id=identity["observed_platform_identity"],
        handle=identity["handle"], observed_at=identity["observed_at"],
        browser_context=identity["browser_context"],
        source_ref=identity["identity_proof_ref"])


def _readback_from_record(record, text, dry_run, simulate_mismatch):
    from core.operations_log import content_sha256

    if dry_run:
        value = text + "（平台改写）" if simulate_mismatch else text
        return {
            "status": "mismatched" if simulate_mismatch else "matched",
            "url": "https://x.com/dryrun_accept/status/%s" % SYNTHETIC_ID,
            "id": SYNTHETIC_ID, "sha256": content_sha256(value),
            "note": "offline synthetic readback",
        }
    receipt = record.get("receipt") or {}
    status = record.get("engine", {}).get("status") or {}
    rid = str(receipt.get("id") or status.get("id") or "")
    url = str(receipt.get("url") or status.get("url") or "")
    verified = record.get("result") == "committed" and bool(rid and url)
    return {
        "status": "matched" if verified else "unknown",
        "url": url, "id": rid, "sha256": "",
        "note": "同一 WebBridge 执行器的状态/回读绑定：%s" % (
            record.get("status_binding") or status.get("source") or "unknown"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="离线预览，不连接浏览器或平台")
    ap.add_argument("--preflight", action="store_true", help="只读预检，不执行外部写")
    ap.add_argument("--action", choices=["publish", "reply"], default="reply")
    ap.add_argument("--authorize-by", default="", help="live 必填：确认使用现有 owner 授权的人")
    ap.add_argument("--target-url", default="", help="reply 的自有账号专用测试推文 URL")
    ap.add_argument("--text", default=DEFAULT_TEXT)
    ap.add_argument("--workspace", default="", help="owner 授权与账本工作区；live 默认产品工作区")
    ap.add_argument("--simulate-readback-mismatch", action="store_true")
    ap.add_argument("--legacy-cdp", action="store_true", help="拒绝用于默认验收；仅提示兼容入口")
    ap.add_argument("--cdp-url", default="", help="已禁用于默认验收")
    ap.add_argument("--profile-dir", default="", help="已禁用于默认验收")
    args = ap.parse_args(argv)

    if args.preflight and args.dry_run:
        return _blocked({"ready": False, "reason": "preflight_and_dry_run_are_exclusive"})
    if args.simulate_readback_mismatch and not args.dry_run:
        return _blocked({"ready": False, "reason": "readback_mismatch_is_dry_run_only"})
    if args.preflight:
        return preflight(args)
    if not args.dry_run:
        guard = guard_live(args)
        if guard is not None:
            return guard
    elif args.legacy_cdp or args.cdp_url or args.profile_dir:
        return _blocked({"ready": False, "reason": "default_acceptance_is_browser_attach_only"})

    from core import account_identity, action_gateway, operations_log, platform_compliance
    from core import workspace as workspace_config
    from core.ledger import Ledger

    dry_run = bool(args.dry_run)
    mode = "dry-run" if dry_run else "live"
    workspace = Path(args.workspace) if args.workspace else (
        Path(tempfile.mkdtemp(prefix="growovo-acceptance-")) if dry_run
        else Path(workspace_config.resolve()))
    workspace.mkdir(parents=True, exist_ok=True)
    target_url = str(args.target_url or "").strip()
    if args.action == "reply" and dry_run and not target_url:
        target_url = SYNTHETIC_TARGET
    text = str(args.text or "").strip()
    if not text or len(text) > 280:
        return _blocked({"ready": False, "reason": "text_required_with_max_280_chars"})
    authorize_by = str(args.authorize_by or "").strip() or "acceptance-owner(dry-run)"
    operator = "acceptance-live-x:dry-run" if dry_run else "acceptance-live-x:%s" % authorize_by

    _say("[1/7] 模式：%s；action=%s；transport=browser-attach；不使用旧 CDP" % (mode, args.action))
    identity = None
    permit = None
    if not dry_run:
        try:
            attach, tab, identity, permit = _managed_browser(workspace, args.action)
        except Exception as exc:
            return _blocked({
                "terminal_state": "BLOCKED_USER_INPUT",
                "reason": "managed_browser_preflight_failed",
                "detail": str(exc)[:300],
                "transaction_executed": False,
                "recovery": {
                    "route": "browser-attach",
                    "operations": ["ensure_managed_tab", "observe_identity", "owner_attested_preflight"],
                },
            })
        if args.action == "reply" and _target_handle(target_url) != str(identity["handle"]).casefold():
            return _blocked({
                "terminal_state": "BLOCKED_USER_INPUT",
                "reason": "acceptance_target_must_belong_to_current_account",
                "transaction_executed": False,
            })
    proof = _proof(identity, dry_run)
    account = {
        "platform_user_id": proof.user_id, "handle": proof.handle,
        "observed_at": proof.observed_at, "proof_ref": proof.source_ref,
    }
    browser_ref = proof.browser_context
    approval_ref = "owner_attested_session"
    policy_path = platform_compliance.owner_attested_path(workspace, "x")
    policy_sha = operations_log.file_sha256(policy_path) if policy_path.is_file() else ""
    actual_owner = (permit or {}).get("risk_owner") or authorize_by
    _say("[2/7] 授权：%s；账号：@%s；浏览器：%s" % (approval_ref, proof.handle, browser_ref))

    print_six_parties(
        authorize_by=actual_owner, operator=operator,
        approval_ref=approval_ref, policy_sha=policy_sha,
        account=account, browser_ref=browser_ref,
        evidence_paths=[("操作日志", str(workspace / "operations.jsonl")),
                        ("账本", str(workspace / "ledger.jsonl"))],
        readback_note="同一 WebBridge 执行器返回真实 ID/URL；业务结果另行观察")

    started = time.time()
    rec = action_gateway.execute(
        channel="x", action=args.action, data_dir=workspace,
        campaign=CAMPAIGN, target=target_url, text=text,
        go=not dry_run, transport="browser-attach",
        operator=operator, log=_say)
    result = str(rec.get("result") or "")
    _say("[3/7] 动作：result=%s（%.1fs）" % (result, time.time() - started))
    if result not in ("committed", "dry-run"):
        return _blocked({
            "terminal_state": "INDETERMINATE" if result == "indeterminate" else result,
            "reason": "action_not_committed", "record": rec,
            "next": "结果未知时先对账，禁止自动重发",
        })

    readback = _readback_from_record(rec, text, dry_run, args.simulate_readback_mismatch)
    receipt = {
        "id": str(readback.get("id") or ""), "url": str(readback.get("url") or ""),
        "readback_sha256": str(readback.get("sha256") or "") or None,
    }
    op_rows = operations_log.OperationsLog(workspace).all()
    op_id = str((op_rows[-1] if op_rows else {}).get("operation_id") or rec.get("ref") or "op-unknown")
    readback_entry = operations_log.OperationsLog(workspace).append({
        "kind": "readback", "operation_id": "%s::readback" % op_id,
        "run_id": None, "campaign": CAMPAIGN, "queue_item_id": None,
        "channel": "x", "action": args.action, "target": target_url,
        "content_sha256": operations_log.content_sha256(text),
        "browser_context_ref": browser_ref, "account": dict(account),
        "operator": operator, "approval_ref": approval_ref,
        "policy_sha256": policy_sha,
        "started_at": account_identity._now_iso(), "finished_at": account_identity._now_iso(),
        "result": "verified" if readback.get("status") == "matched"
                  else str(readback.get("status") or "unknown"),
        "receipt": receipt, "artifacts": [], "error": None,
        "readback": {"status": str(readback.get("status") or "unknown"),
                     "source": "synthetic" if dry_run else "browser-attach",
                     "note": str(readback.get("note") or "")[:300]},
    })
    rb_result = readback_entry["result"]
    _say("[4/7] 回读：status=%s；operation=%s" % (readback.get("status"), rb_result))
    if rb_result == "indeterminate":
        Ledger(workspace / "ledger.jsonl").append(
            channel="x", transport="browser-attach", action=args.action,
            campaign=CAMPAIGN, target=target_url or "-", result="indeterminate",
            ref="%s::readback" % op_id,
            detail="回读不一致：禁止自动重发，先核对平台对象")
        _say("禁止自动重发；先核对平台对象与回执。")
        return 1

    _say("[5/7] 操作日志与账本已更新；外部写次数：%d" % (0 if dry_run else 1))
    _say("[6/7] 业务读数：UNKNOWN（动作回执不等于粉丝、Star、激活或收入）")
    _say("[7/7] 验收完成")
    summary = {
        "mode": mode, "action": args.action, "transport": "browser-attach",
        "live_proven": False if dry_run else None,
        "chain": "owner-policy→identity→action(%s)→readback(%s)→operations-log" % (
            result, readback.get("status")),
        "operation_id": op_id, "account": account, "receipt": receipt,
        "evidence": {"operations_log": str(workspace / "operations.jsonl"),
                     "ledger": str(workspace / "ledger.jsonl")},
    }
    if dry_run:
        summary["note"] = "dry-run 预览通过，不代表真实平台交付"
    else:
        summary["terminal_state"] = "DELIVERED" if result == "committed" else result
    _say(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
