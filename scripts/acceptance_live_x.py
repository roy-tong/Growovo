#!/usr/bin/env python3
"""Growovo 受控真实 X 动作验收链（评审 2026-11-17 §6 测试纪律 / §9 交付物 2）。

承诺链：目标/渠道/内容确认 → CDP 登录与账号观测 → 授权 → 单条低风险动作
→ 平台回读（ID/URL/内容哈希）→ 完整操作日志 → 实际读数或 UNKNOWN。

两种模式：

  --dry-run（默认安全；测试覆盖的就是这条）
      全链离线彩排：真实的 policy/身份/动作执行器代码路径，只有 vendor
      浏览器执行与平台回读用离线替身。不碰网络、不碰浏览器、不碰
      9222、不碰 profiles/。

  live（必须显式 --authorize-by <人名>）
      隔离 profile + 独立 CDP 端口（默认 9223，拒绝 9222——campaign 占用）
      + 专用测试推文，执行一条真实 reply，随后回读与操作日志。
      真实发送绝不进 CI/unittest；本脚本由用户显式执行。

退出码：0 链路完成（dry-run 彩排通过 / live 动作+回读+日志闭环）；
        1 回读不一致停机（indeterminate，禁止自动重发）；
        2 前置守卫/阻塞（缺授权、边界违规、账号读不到）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

CAMPAIGN = "acceptance-live-x"
DEFAULT_CDP = "http://127.0.0.1:9223"   # 独立端口：9222 被 X campaign 占用
FORBIDDEN_PORT = "9222"
DEFAULT_TEXT = ("[growovo-acceptance] 受控验收回复：本条来自 Growovo 验收链，"
                "用于验证动作-回读-日志闭环，稍后会删除。")

# 评审 §6 测试环境纪律：绝不动 campaign 的 profiles/。
FORBIDDEN_PROFILE_MARKERS = ("/profiles", "/Growovo/profiles")


def _say(msg: str) -> None:
    print(msg, flush=True)


def _blocked(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=1), flush=True)
    return 2


# --------------------------------------------------------------------------- #
# 守卫：live 模式的隔离边界
# --------------------------------------------------------------------------- #

def guard_live(args) -> int | None:
    if not str(args.authorize_by or "").strip():
        _say("live 验收必须显式 --authorize-by <人名>：真实发送由人授权并担责，"
             "脚本不代替人做这个决定。")
        _say("先离线彩排：python3 scripts/acceptance_live_x.py --dry-run")
        return 2
    if FORBIDDEN_PORT in str(args.cdp_url or ""):
        _say(f"拒绝 CDP 端点 {args.cdp_url}：端口 {FORBIDDEN_PORT} 被 X launch "
             "campaign（至 2026-11-19）占用，验收链绝不触碰。")
        _say("请用独立端口（默认 %s）+ 隔离 profile 启动专用调试浏览器。" % DEFAULT_CDP)
        return 2
    profile = str(args.profile_dir or "").strip()
    if profile:
        norm = profile.rstrip("/")
        if any(norm.endswith(m.split("/")[-1]) and m.rstrip("/") in norm
               for m in FORBIDDEN_PROFILE_MARKERS):
            pass  # 具体目录名 profiles 才拦，下面统一判断
        if norm.endswith("/profiles") or norm == "/profiles" \
                or "/profiles/" in (norm + "/"):
            _say(f"拒绝共享 profile 目录 {profile}：验收链只允许一次性隔离 "
                 "profile（--profile-dir 指向空目录）。")
            return 2
    if not str(args.target_url or "").strip():
        _say("live 验收需要 --target-url <专用测试推文 URL>：不得用真实客户/"
             "陌生人的帖子做验收。")
        return 2
    return None


# --------------------------------------------------------------------------- #
# 六方分离表
# --------------------------------------------------------------------------- #

def print_six_parties(*, authorize_by, operator, approval_ref, policy_sha,
                      account, browser_ref, evidence_paths, readback_note):
    _say("")
    _say("六方分离表（谁授权 / 谁操作 / 凭什么批 / 哪个账号 / 哪个浏览器 / 怎么核验）")
    _say(f"  ① 授权人（用户）      ：{authorize_by}")
    _say(f"  ② 操作者（引擎入口）  ：{operator}")
    _say(f"  ③ 审批依据            ：{approval_ref}"
         + (f"（policy sha256={policy_sha[:12]}…）" if policy_sha else "（无）"))
    ident = account.get("platform_user_id") or "（未观测到）"
    handle = account.get("handle") or "?"
    _say(f"  ④ 执行账号（平台观测）：{ident} @ {handle}"
         f"（observed_at={account.get('observed_at') or '?'}）")
    _say(f"  ⑤ 浏览器上下文        ：{browser_ref}（不保存凭据）")
    _say(f"  ⑥ 回读与证据          ：{readback_note}")
    for label, path in evidence_paths:
        _say(f"       {label}：{path}")
    _say("")


# --------------------------------------------------------------------------- #
# 离线 vendor 替身（dry-run 用）
# --------------------------------------------------------------------------- #

def _offline_vendor(log_lines: list):
    def run(action, extra_args, *, go=False, campaign="", ledger=None,
            log=print, data_dir=None, identity=None, **kw):
        argv = [str(a) for a in extra_args]
        url = argv[argv.index("--tweet-url") + 1] if "--tweet-url" in argv else ""
        text = argv[argv.index("--text") + 1] if "--text" in argv else ""
        log_lines.append(f"[offline-vendor] {action} -> {url or '(no url)'}")
        if ledger is not None:
            ledger.append(channel="x", transport="cdp", action=action,
                          campaign=campaign, target=url or "?",
                          result="committed" if go else "dry-run", ref="")
        return 0
    return run


# --------------------------------------------------------------------------- #
# live 回读（真实浏览器；失败=unknown，绝不猜测）
# --------------------------------------------------------------------------- #

def _normalize(text: str) -> str:
    import re
    return re.sub(r"\s+", " ", str(text or "").strip())


def live_readback(cdp_url: str, tweet_url: str, handle: str, text: str) -> dict:
    """打开目标推文页，找本账号最新回复，回读 URL/ID/内容哈希。

    返回 {status: matched|mismatched|unknown, url, id, sha256, note}。
    任何异常都归 unknown——回读不可用不是发送失败，但也不能宣称核验通过。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "unknown", "url": "", "id": "", "sha256": "",
                "note": "playwright 不可用"}
    from core.operations_log import content_sha256
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp_url)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()
            page.goto(tweet_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
            articles = page.locator('article[data-testid="tweet"]')
            found = None
            for i in range(articles.count() - 1, -1, -1):
                art = articles.nth(i)
                try:
                    body = _normalize(art.inner_text(timeout=2000))
                except Exception:  # noqa: BLE001
                    continue
                if _normalize(text) and _normalize(text) in body:
                    found = (art, body)
                    break
                if handle and (f"@{handle}" in body) and len(body) > 40:
                    found = (art, body)
            if found is None:
                return {"status": "unknown", "url": "", "id": "", "sha256": "",
                        "note": "回读页上没有找到本账号的回复（可能未渲染/被折叠）"}
            art, body = found
            url, rid = "", ""
            try:
                href = art.locator("a[href*='/status/']").last.get_attribute("href")
                if href:
                    url = str(href)
                    rid = url.rstrip("/").split("/")[-1]
            except Exception:  # noqa: BLE001
                pass
            sha = content_sha256(body)
            status = "matched" if sha == content_sha256(text) else "mismatched"
            return {"status": status, "url": url, "id": rid, "sha256": sha,
                    "note": f"readback from {cdp_url}"}
    except Exception as exc:  # noqa: BLE001 回读失败=unknown，不猜测
        return {"status": "unknown", "url": "", "id": "", "sha256": "",
                "note": f"live 回读异常：{str(exc)[:160]}"}


# --------------------------------------------------------------------------- #
# 只读 live 前置检查（不发送、不建授权、不碰 profiles）
# --------------------------------------------------------------------------- #

def preflight(args) -> int:
    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    authorize_by = str(args.authorize_by or "").strip()
    add("authorized_by", bool(authorize_by),
        "已署名" if authorize_by else "缺少 --authorize-by（正式发送前必须由人署名）")

    target = str(args.target_url or "").strip()
    parsed_target = urllib.parse.urlparse(target)
    target_ok = (parsed_target.scheme == "https"
                 and parsed_target.hostname in ("x.com", "www.x.com", "twitter.com",
                                                "www.twitter.com")
                 and "/status/" in parsed_target.path)
    add("dedicated_test_target", target_ok,
        "专用测试推文 URL" if target_ok else "需要 https://x.com/<测试账号>/status/<id>")

    cdp = str(args.cdp_url or "").strip().rstrip("/")
    parsed_cdp = urllib.parse.urlparse(cdp)
    port = parsed_cdp.port or 80
    cdp_shape_ok = (parsed_cdp.scheme == "http"
                    and parsed_cdp.hostname in ("127.0.0.1", "::1", "localhost")
                    and not parsed_cdp.username and not parsed_cdp.password)
    add("independent_cdp_endpoint", cdp_shape_ok and str(port) != FORBIDDEN_PORT,
        cdp if cdp_shape_ok and str(port) != FORBIDDEN_PORT
        else "CDP 必须是回环 http 端点且不能使用 campaign 的 9222")

    profile = str(args.profile_dir or "").strip()
    profile_path = Path(profile) if profile else None
    profile_norm = profile.rstrip("/")
    shared_profile = (profile_norm.endswith("/profiles")
                      or "/profiles/" in (profile_norm + "/"))
    profile_ok = bool(profile_path and profile_path.is_dir() and not shared_profile)
    add("isolated_profile", profile_ok,
        str(profile_path) if profile_ok
        else "需要独立的已登录 browser profile 目录（--profile-dir，不能是共享 profiles/）")

    text = str(args.text or "").strip()
    add("content", bool(text) and len(text) <= 280,
        "%d 字符" % len(text) if text else "回复正文为空")

    endpoint = None
    if cdp_shape_ok:
        try:
            with urllib.request.urlopen(cdp + "/json/version", timeout=2) as resp:
                payload = json.loads(resp.read(4096).decode("utf-8", "replace"))
                endpoint = str(payload.get("Browser") or "reachable")
            add("browser_endpoint", True, endpoint)
            # Read-only login check on the same endpoint: opening the X home
            # page is enough; never credentials, never a write.
            try:
                from core import cdp as cdp_mod, session_gate
                session_gate.clear_cache()
                login = session_gate.check_login(
                    "x", connect=lambda: cdp_mod.connect(cdp))
                add("x_login", login.get("status") == session_gate.LOGGED_IN,
                    "status=%s%s%s" % (
                        login.get("status"),
                        " url=%s" % login.get("url") if login.get("url") else "",
                        " error=%s" % login.get("error") if login.get("error") else ""))
            except Exception as exc:  # noqa: BLE001
                add("x_login", False, "登录态检查失败：%s" % str(exc)[:160])
        except (urllib.error.URLError, OSError, ValueError) as exc:
            add("browser_endpoint", False, "无法连接 %s：%s" % (cdp, str(exc)[:160]))
    else:
        add("browser_endpoint", False, "CDP 端点本身不合法，未尝试连接")

    ready = all(item["ok"] for item in checks)
    next_steps = [item["detail"] for item in checks if not item["ok"]]
    print(json.dumps({
        "mode": "preflight",
        "live_proven": False,
        "ready": ready,
        "checks": checks,
        "next_required_actions": next_steps,
        "note": ("只读前置检查：未发送、未创建授权、未触碰 profiles；"
                 "ready=true 仍需要人显式执行 live 命令并承担发送责任"),
    }, ensure_ascii=False, indent=1))
    return 0 if ready else 2


# --------------------------------------------------------------------------- #
# 主链
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="全链离线彩排：不碰网络/浏览器/9222/profiles")
    ap.add_argument("--preflight", action="store_true",
                    help="只读检查 live 前置条件：不发送/不建授权/不碰 profiles")
    ap.add_argument("--authorize-by", default="",
                    help="live 必填：授权并担责的人名")
    ap.add_argument("--cdp-url", default=DEFAULT_CDP,
                    help=f"live 用独立 CDP 端点（默认 {DEFAULT_CDP}；拒绝 9222）")
    ap.add_argument("--profile-dir", default="",
                    help="live 用一次性隔离 profile 目录（拒绝共享 profiles/）")
    ap.add_argument("--target-url", default="",
                    help="专用测试推文 URL（live 必填；dry-run 默认合成目标）")
    ap.add_argument("--text", default=DEFAULT_TEXT, help="回复正文")
    ap.add_argument("--workspace", default="",
                    help="验收工作区目录（默认临时目录；operations.jsonl 落在这里）")
    ap.add_argument("--simulate-readback-mismatch", action="store_true",
                    help="仅 dry-run：彩排「回读内容哈希不一致→indeterminate 停机」分支")
    args = ap.parse_args(argv)

    if args.preflight and args.dry_run:
        _say("--preflight 与 --dry-run 不能同时使用。")
        return 2
    if args.preflight:
        return preflight(args)
    if args.simulate_readback_mismatch and not args.dry_run:
        _say("--simulate-readback-mismatch 只用于 --dry-run 彩排。")
        return 2
    if not args.dry_run:
        guard = guard_live(args)
        if guard is not None:
            return guard

    from core import account_identity, execution_policy, operations_log
    from core.action_runner import run_action
    from core.ledger import Ledger

    mode = "dry-run" if args.dry_run else "live"
    workspace = Path(args.workspace or tempfile.mkdtemp(prefix="growovo-acceptance-"))
    workspace.mkdir(parents=True, exist_ok=True)
    authorize_by = str(args.authorize_by or "").strip() or "acceptance-owner(dry-run)"
    operator = (f"acceptance-live-x:dry-run" if args.dry_run
                else f"acceptance-live-x:{args.authorize_by.strip()}")
    target_url = str(args.target_url or "").strip() or \
        "https://x.com/growovo_test/status/0000000000000000000"
    text = str(args.text or DEFAULT_TEXT)

    _say(f"[1/7] 模式：{mode}"
         + ("（offline 彩排：不碰网络/浏览器/9222/profiles）" if args.dry_run
            else f"（CDP={args.cdp_url}，隔离 profile={args.profile_dir or '（用户自管）'}）"))

    # 2. 授权：observe-account 常驻授权（routine-policy，1 天，只允许 x:reply）
    policy = execution_policy.authorize(
        workspace, by=authorize_by, actions=["x:reply"], days=1,
        observe_identity=True)
    policy_sha = operations_log.file_sha256(
        execution_policy.policy_path(workspace))
    _say(f"[2/7] 常驻授权已签发：routine-policy x:reply（by={authorize_by}，"
         f"identity_mode=cdp_observed，sha256={policy_sha[:12]}…）")

    # 3. 账号观测（dry-run 注入离线探测器；live 走真实 CDP）
    if args.dry_run:
        proof_holder = {}

        def _offline_reader(channel):
            proof_holder["proof"] = account_identity.AccountIdentityProof(
                channel=channel, user_id="dryrun-000000",
                handle="dryrun_accept", browser_context="offline:synthetic",
                source_ref="offline:dry-run")
            return proof_holder["proof"]

        account_identity.set_reader(_offline_reader)
        env = None
    else:
        env = {**os.environ, "MKT_CDP_IDENTITY": "1",
               "MKT_CDP_IDENTITY_URL": args.cdp_url, "X_CDP_URL": args.cdp_url}
        os.environ.update(env)
    proof = account_identity.observe("x", data_dir=workspace, env=env)
    if proof is None:
        return _blocked({
            "terminal_state": "BLOCKED_USER_INPUT",
            "reason": "identity_unreadable",
            "item": "可读取账号的已登录调试浏览器",
            "why": "CDP 观测不到稳定平台账号（未登录/端口未开/读取关闭）",
            "user_action": "在隔离 profile 的浏览器里登录测试账号并开放 "
                           f"{args.cdp_url}（MKT_CDP_IDENTITY=1）",
            "alternatives": ["改用 --dry-run 离线彩排"],
            "transaction_executed": False,
        })
    account = {"platform_user_id": proof.user_id, "handle": proof.handle,
               "observed_at": proof.observed_at, "proof_ref": proof.source_ref}
    _say(f"[3/7] 账号观测：{proof.user_id} @ {proof.handle}"
         f"（observed_at={proof.observed_at}）")

    browser_ref = (operations_log.browser_context_ref(env=env)
                   or "offline:synthetic")

    # 4. 六方分离表（在动作之前打印：先讲清楚谁是谁，再动手）
    print_six_parties(
        authorize_by=authorize_by, operator=operator,
        approval_ref="routine-policy", policy_sha=policy_sha,
        account=account, browser_ref=browser_ref,
        evidence_paths=[("操作日志", str(workspace / "operations.jsonl")),
                        ("账本", str(workspace / "ledger.jsonl"))],
        readback_note=("动作后回读 ID/URL/内容哈希并与预期比对"
                       + ("；本链只验收单条动作，连续读数走 mkt grow watch"
                          if not args.dry_run else "（dry-run 用离线回读替身）")))

    # 5. 单条低风险动作（真实执行器路径；dry-run 只有 vendor 是替身）
    started = time.time()
    if args.dry_run:
        vendor_log: list = []
        with patch("channels.x.adapter.run", _offline_vendor(vendor_log)):
            rec = run_action(channel="x", action="reply", data_dir=workspace,
                             campaign=CAMPAIGN, target=target_url, text=text,
                             go=False, routine=True, operator=operator,
                             log=lambda *a, **k: None)
    else:
        rec = run_action(channel="x", action="reply", data_dir=workspace,
                         campaign=CAMPAIGN, target=target_url, text=text,
                         go=True, routine=True, allow_unverified=True,
                         operator=operator, log=_say)
    finished = time.time()
    result = str(rec.get("result") or "")
    _say(f"[5/7] 动作完成：result={result}（{finished - started:.1f}s）"
         + (f"；vendor：{vendor_log[0]}" if args.dry_run and vendor_log else ""))
    if result not in ("committed", "dry-run"):
        _say(f"动作未成功（{result}）：保留现场，链路停止。")
        return 1

    # 6. 回读核验：ID/URL/内容哈希 vs 预期
    if args.dry_run:
        if args.simulate_readback_mismatch:
            rb_text, rb_status = text + "（平台改写）", "mismatched"
        else:
            rb_text, rb_status = text, "matched"
        readback = {"status": rb_status, "url": target_url,
                    "id": "1790000000000000000",
                    "sha256": operations_log.content_sha256(rb_text),
                    "note": "offline synthetic readback"}
    else:
        readback = live_readback(args.cdp_url, target_url,
                                 proof.handle or "", text)
    receipt = {"id": str(readback.get("id") or ""),
               "url": str(readback.get("url") or ""),
               "readback_sha256": str(readback.get("sha256") or "")}
    op_rows = operations_log.OperationsLog(workspace).all()
    op_id = str((op_rows[-1] or {}).get("operation_id") or "op-unknown")
    readback_entry = operations_log.OperationsLog(workspace).append({
        "kind": "readback",
        "operation_id": f"{op_id}::readback",
        "run_id": None, "campaign": CAMPAIGN, "queue_item_id": None,
        "channel": "x", "action": "reply", "target": target_url,
        "content_sha256": operations_log.content_sha256(text),
        "browser_context_ref": browser_ref,
        "account": dict(account),
        "operator": operator, "approval_ref": "routine-policy",
        "policy_sha256": policy_sha,
        "started_at": account_identity._now_iso(),
        "finished_at": account_identity._now_iso(),
        "result": "verified" if readback.get("status") == "matched"
                  else str(readback.get("status") or "unknown"),
        "receipt": receipt, "artifacts": [], "error": None,
        "readback": {"status": str(readback.get("status") or "unknown"),
                     "source": "synthetic" if args.dry_run else "cdp",
                     "note": str(readback.get("note") or "")[:300]},
    })
    rb_result = readback_entry["result"]
    _say(f"[6/7] 回读核验：status={readback.get('status')} → 操作日志 result={rb_result}")
    if rb_result == "indeterminate":
        # 复用 unknown 回执机制：账本落 indeterminate，no_retry 拦住后续自动重发
        Ledger(workspace / "ledger.jsonl").append(
            channel="x", transport="cdp", action="reply", campaign=CAMPAIGN,
            target=target_url, result="indeterminate",
            ref=f"{op_id}::readback",
            detail="回读内容哈希不一致（readback_mismatch）：indeterminate "
                   "禁止自动重发，人工在平台后台核对后 mkt ledger resolve")
        _say("停机：回读内容哈希不一致，动作结果为 indeterminate。")
        _say("禁止自动重发；人工核对平台后台后执行 mkt ledger resolve。")
        return 1

    # 7. 实际读数或 UNKNOWN（本链只验收单条动作；读数诚实报告 UNKNOWN）
    _say("[7/7] 结果读数：UNKNOWN（本链只验收单条动作与回执；连续读数走 "
         "mkt grow watch / mkt x stats，首周人工对数）")

    summary = {
        "mode": mode,
        "live_proven": False if args.dry_run else None,
        "chain": "policy→identity→action(%s)→readback(%s)→operations-log→reading"
                 % (result, readback.get("status")),
        "operation_id": op_id,
        "account": account,
        "receipt": receipt,
        "evidence": {
            "operations_log": str(workspace / "operations.jsonl"),
            "ledger": str(workspace / "ledger.jsonl"),
        },
    }
    if args.dry_run:
        summary["note"] = "dry-run 彩排通过：真实执行需人工运行本脚本并显式 --authorize-by"
    else:
        summary["terminal_state"] = "DELIVERED" if result == "committed" else result
    _say("")
    _say(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
