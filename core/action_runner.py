#!/usr/bin/env python3
"""统一动作执行器 —— 单条写动作走同一套门禁/审批/账本（MCP 与 CLI 共用）。

为什么不是"再加一个发一条的工具"：动作粒度只是队列粒度之外的第二形态，
两形态共用同一条安全边界——全局暂停、UNVERIFIED 渠道实跑门槛、已人审 plan、
统一账本。没有绕过留痕的路径。

支持的写动作：
  hn       publish / reply
  github   reply
  reddit   publish / reply
  linkedin message（实跑要求 MKT_LINKEDIN_AUTO=1，MCP 无 TTY 不做交互确认）
  x        publish / reply / message / engage（message/engage 透传 vendor 参数）
  discord  publish / reply（仅 transport=browser-attach）
  email    仅队列（action_runner 拒绝单条发信）

transport=browser-attach 是 X publish/reply、GitHub reply、Discord publish/reply 的
独立授权路线：跳过通用 CDP 身份与 plan/routine 预检，由 browser_write 强制
owner-attested、页面身份、pause/DNC/quota/readback；统一回执、任务结果与操作日志。
"""
import os
from datetime import datetime, timezone
from pathlib import Path

from .ledger import Ledger
from .planlint import enforce_approval
from .safety import is_paused
from .workspace import resolve

WRITE_ACTIONS = {"publish", "reply", "message", "engage"}
READ_ACTIONS = {"search", "inbox", "audit"}


class ActionError(RuntimeError):
    pass


def _registry():
    from channels import REGISTRY
    return REGISTRY


def precheck(channel, action, data_dir, campaign, go, allow_unverified, target=None,
             routine=False, account="", platform_user_id=None, login_probe=None):
    """Run shared write gates; return the approval mode for a real write.

    routine=False uses the original approval call verbatim.  routine=True only
    adds operation identity to that approval check; all earlier and later
    safety gates remain unchanged.
    """
    reg = _registry()
    if channel not in reg:
        raise ActionError(f"未知渠道 {channel!r}（支持: {sorted(reg)}）")
    if action not in reg[channel].get("actions", set()):
        raise ActionError(f"{channel} 不支持动作 {action}（能力: {sorted(reg[channel]['actions'])}）")
    if action in READ_ACTIONS:
        return
    # DNC/bounced 是执行约束，不是 UI 隐藏条件；dry-run 也必须预检。
    if target:
        from .gates import load_suppressions
        suppressed = load_suppressions(data_dir)
        value = str(target).strip()
        variants = {value, value.lstrip("@"), "@" + value.lstrip("@")}
        if any(v in suppressed for v in variants):
            raise ActionError(f"目标在抑制名单（DNC/bounced/suppressed）: {value}")
    # 暂停总闸拦一切写路径（含 dry-run：干跑也会写 evidence/账本留痕）。
    if is_paused(data_dir):
        raise ActionError("全局暂停中（mkt pause --off 恢复）——写操作拒跑")
    if not go:
        return
    if routine and channel != "x":
        from .platform_compliance import policy_row
        permission = policy_row(channel)
        if not permission.get("autonomous_write"):
            raise ActionError(
                f"平台许可不允许 {channel}/{action} 进入 routine 自主执行："
                f"{permission.get('reason') or '未声明自主写许可'}；"
                f"{permission.get('next') or '改用人工辅助'}")
    action_status = (reg[channel].get("action_status") or {}).get(action)
    if action_status == "UNVERIFIED" and not allow_unverified:
        raise ActionError(f"{channel}/{action} 为 UNVERIFIED，拒绝实跑（人工首跑校准后加 allow_unverified=true）")
    if reg[channel].get("status") == "UNVERIFIED" and not allow_unverified:
        raise ActionError(f"{channel} 渠道为 UNVERIFIED，拒绝实跑（人工首跑校准后加 allow_unverified=true）")
    try:
        if routine is True:
            approval_mode = enforce_approval(
                data_dir, campaign, routine=True,
                channel=channel, action=action, account=account,
                platform_user_id=platform_user_id)
        else:
            enforce_approval(data_dir, campaign)
            approval_mode = None
    except Exception as exc:
        # 统一成 ActionError，让 CLI/MCP 的拒跑路径一致；原因保留原文。
        raise ActionError(f"人审门禁: {exc}") from exc
    gate_browser_login(channel, reg[channel], login_probe=login_probe)
    return approval_mode


def gate_browser_login(channel, entry, *, login_probe=None):
    """账号=浏览器登录态：CDP 渠道实跑前检测登录态，缺失明确报缺。

    授权边界先过、再探浏览器（探测要开页，不该先于授权发生）；
    dry-run 不进此门（保持零浏览器副作用）。缺失抛 ActionError →
    账本记 rejected，而不是 vendor rc=3 之后的 indeterminate。
    """
    from .session_gate import ACCOUNT_LOCKED, ACCOUNT_SUSPENDED, CDP_UNAVAILABLE, LOGGED_IN, PROBES, check_login
    # 只拦「声明了登录探测」的渠道：email 这类 smtp 主通道（cdp 仅降级）不按浏览器登录态门控。
    if channel not in PROBES or "cdp" not in entry.get("transports", set()):
        return
    probe = login_probe or check_login
    state = probe(channel) or {}
    status = state.get("status")
    if status == CDP_UNAVAILABLE:
        raise ActionError(f"{channel} 浏览器通道不可用：先运行 ./launch.command 启动调试 Chrome，再重试")
    if status != LOGGED_IN:
        raise ActionError(f"{channel} 浏览器登录态缺失：请在浏览器登录该站点后重试（引擎只检测登录态，不管理账号）")
    account_state = state.get("account_state")
    if account_state in (ACCOUNT_SUSPENDED, ACCOUNT_LOCKED):
        raise ActionError(
            f"{channel} 登录态正常但账号不可用（account_state={account_state}）："
            "平台已冻结/锁定该账号，任何动作都会被拒绝；请换一个可用账号的浏览器会话再试")


def gate_account_identity(channel, data_dir, account=""):
    """G-01b 实际账号身份门禁：登录界面标记 ≠ 授权账号身份。

    - 旧授权（policy 可读但没声明身份绑定）：直接拒绝真实发送
      （AUTH_REQUIRED），并给出 `mkt policy migrate-identity` 迁移命令；
      不先探测浏览器——问题在授权，不在观测。
    - 绑定身份：必须观测到与授权记录精确一致的实际平台账号。
    - 无 policy / policy 无效：本门禁不裁决（审批门禁另行 fail closed）。

    拒绝抛 ActionError → 账本记 rejected，绝不带病发送。返回证据 dict
    （未绑定为 None），供回执记录 authorized_account / observed identity /
    identity_proof_ref。
    """
    from . import account_identity
    channel = str(channel or "").strip().lower()
    if channel not in account_identity.IDENTITY_CHANNELS:
        return None
    policy = account_identity.load_policy(data_dir)
    blockers = account_identity.identity_blockers(
        data_dir, account, policy=policy, policy_loaded=True)
    if blockers:
        blocker = blockers[0]
        raise ActionError(
            "账号身份门禁: %s %s: %s；下一步：%s"
            % (blocker["code"], blocker["reason"], blocker["detail"],
               blocker["next"]))
    state = account_identity.policy_state(policy, account)
    if state["mode"] in ("absent", "invalid"):
        return None
    # G-10：身份必须来自真实执行所用的那个调试浏览器。端点不一致就直接拒绝，
    # 免得「在 A 上验证、在 B 上发送」。
    from . import cdp_identity
    try:
        cdp_identity.assert_identity_endpoint_matches(channel)
    except cdp_identity._CdpError as exc:
        raise ActionError(
            "账号身份门禁: %s %s：%s" % (cdp_identity.ENDPOINT_MISMATCH, exc.reason, exc)) from exc
    except cdp_identity.EndpointRejected as exc:
        raise ActionError("账号身份门禁: identity_endpoint_invalid：%s" % exc) from exc
    # CDP-observed mode: the user provides the browser; force the read-only
    # identity probe on for this call so the ledger can record which account
    # actually performed the operation.
    env = ({**os.environ, "MKT_CDP_IDENTITY": "1"}
           if state["mode"] == "observed" else None)
    proof = account_identity.observe(channel, data_dir=data_dir, env=env)
    try:
        return account_identity.enforce_identity(
            data_dir, channel, account, proof, policy=policy)
    except account_identity.IdentityBlocked as exc:
        raise ActionError(f"账号身份门禁: {exc}") from exc


def _parse_issue(target):
    import re
    text = str(target or "").strip()
    url = re.match(r"^https?://(?:www\.)?github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)",
                   text)
    if url:
        return url.group(1), int(url.group(2))
    m = re.match(r"^(?:issue:)?([\w.-]+/[\w.-]+)#(\d+)$", text)
    if not m:
        raise ActionError("github reply 的 target 应为 issue:owner/repo#N 或 issue URL")
    return m.group(1), int(m.group(2))


def _x_last_record(ledger, campaign, action, fallback):
    recs = [r for r in ledger.results(channel="x", campaign=campaign, action=action)]
    if recs:
        return dict(recs[-1], channel="x", action=action, campaign=campaign)
    return fallback


def _dispatch_browser_attach(*, channel, action, data_dir, campaign, go,
                             target, text, actor="", log=print):
    """把 browser-attach 适配成统一账本回执；传输层已经记账，这里不重复 append。"""
    from .browser_write import execute

    rec = dict(execute(channel, action, data_dir, text=text, target=target,
                       go=go, campaign=campaign, actor=actor, log=log))
    if rec.get("skipped"):
        state = "skipped"
    elif not go or rec.get("dry"):
        state = "dry-run"
    else:
        state = str(rec.get("ledger_result") or "").strip().lower()
        if state not in {"committed", "failed", "indeterminate", "rejected",
                         "target_rejected", "unavailable_or_unrenderable"}:
            # 真实动作没有可核验账本终态时，绝不能把 success/ok 当成 committed。
            state = "indeterminate"
            rec["error"] = rec.get("error") or "browser-attach 返回缺少有效 ledger_result"
    rec.update({
        "ok": state in {"committed", "dry-run", "skipped"},
        "result": state,
        "ledger_result": state,
        "channel": channel,
        "action": action,
        "campaign": campaign,
        "target": target or rec.get("target") or "",
        "transport": "browser-attach",
        "mode": "owner_attested_session",
    })
    return rec


def _dispatch_action_inner(*, channel, action, data_dir=None, campaign="", go=False,
                           allow_unverified=False, target=None, text=None, title=None, url=None,
                           subreddit=None, number=None, extra=None, log=print,
                           routine=False, account="", platform_user_id=None,
                           transport="", actor=""):
    """执行单条写动作，返回账本记录 dict。"""
    data = Path(data_dir or resolve())
    channel = (channel or "").strip().lower()
    action = (action or "").strip().lower()
    transport = str(transport or "").strip().lower()
    if transport not in {"", "browser-attach"}:
        raise ActionError("未知传输 %r（可选: browser-attach；缺省走原渠道适配器）" % transport)
    if action in READ_ACTIONS:
        raise ActionError("读动作请用 mkt search/inbox/audit，不走 run_action")
    if not go:
        log(f"[action] {channel}/{action} dry-run（加 --go 才实跑）")
    if transport == "browser-attach":
        if routine:
            raise ActionError("browser-attach 使用 owner-attested 独立授权，不与 routine policy 混用")
        return _dispatch_browser_attach(
            channel=channel, action=action, data_dir=data, campaign=campaign,
            go=go, target=target, text=text, actor=actor, log=log)

    # G-01b：真实发送前校验实际账号身份（队列/单动作/MCP 共用此路径）。
    # 必须先于审批：require_identity 的授权在审批里需要 *观测到的* user_id，
    # 而调用方自报值不算证据。dry-run 保持零浏览器副作用，只由队列/CLI 层提示。
    from . import account_identity
    identity_evidence = None
    if go and channel in account_identity.IDENTITY_CHANNELS:
        identity_evidence = gate_account_identity(channel, data, account)
        if identity_evidence:
            # 观测值同时传给审批校验和适配器的嵌套 legacy 复核；不是调用方自报值。
            platform_user_id = identity_evidence["observed_platform_identity"]
            if routine is True:
                from .planlint import bind_scope_identity
                bind_scope_identity(platform_user_id)

    if routine is True:
        approval_mode = precheck(
            channel, action, data, campaign, go, allow_unverified, target=target,
            routine=True, account=account, platform_user_id=platform_user_id)
    else:
        approval_mode = precheck(
            channel, action, data, campaign, go, allow_unverified, target=target)

    def _record(rec):
        if routine is True and approval_mode == "routine-policy" and isinstance(rec, dict):
            rec = dict(rec)
            # Preserve a nested pipeline's actual mode (notably plan), and do
            # not label an approval rejection with the earlier precheck mode.
            if not rec.get("approval") and rec.get("result") != "rejected":
                rec["approval"] = "routine-policy"
        if identity_evidence and isinstance(rec, dict) and "identity" not in rec:
            rec = dict(rec)
            rec["identity"] = dict(identity_evidence)
        if account and isinstance(rec, dict) and "account" not in rec:
            rec = dict(rec)
            rec["account"] = account
        return rec
    ledger = Ledger(data / "ledger.jsonl")
    extra = list(extra or [])

    if channel == "hn":
        from channels import hn
        if action == "publish":
            t = title or (text or "")[:80]
            rec = hn.publish(t, url=url, text=text, go=go, campaign=campaign,
                             ledger=ledger, data_dir=data, log=log)
        else:
            if not target or not text:
                raise ActionError("hn reply 需要 target（item:N/URL）与 text")
            rec = hn.reply(target, text, go=go, campaign=campaign,
                           ledger=ledger, data_dir=data, log=log)
        return _record(rec)

    if channel == "github":
        from channels import github
        if number and "/" in str(target or "") and "#" not in str(target):
            slug, num = str(target).strip(), int(number)
        else:
            slug, num = _parse_issue(target)
        if not text:
            raise ActionError("github reply 需要 text")
        return _record(github.reply(
            slug, num, text, go=go, campaign=campaign,
            ledger=ledger, data_dir=data, log=log))

    if channel == "reddit":
        from channels import reddit
        if action == "publish":
            sub = subreddit or str(target or "").replace("r/", "")
            if not (sub and title and text):
                raise ActionError("reddit publish 需要 subreddit/target、title、text")
            return _record(reddit.publish(
                sub, title, text, go=go, campaign=campaign,
                ledger=ledger, data_dir=data, log=log))
        if not (target and text):
            raise ActionError("reddit reply 需要 target（帖子 URL）与 text")
        return _record(reddit.reply(
            target, text, go=go, campaign=campaign,
            ledger=ledger, data_dir=data, log=log))

    if channel == "linkedin":
        import os
        from channels import linkedin
        if go and os.environ.get("MKT_LINKEDIN_AUTO", "") != "1":
            raise ActionError("LinkedIn 半自动需要终端确认，MCP/队列无法交互；"
                              "实跑请设 MKT_LINKEDIN_AUTO=1 并自行承担全自动风险")
        if not (target and text):
            raise ActionError("linkedin message 需要 target（profile URL）与 text")
        return _record(linkedin.message(
            target, text, go=go, campaign=campaign,
            ledger=ledger, data_dir=data, log=log))

    if channel == "x":
        from channels.x.adapter import run as xrun
        if action == "reply":
            if not (target and text):
                raise ActionError("x reply 需要 target（推文 URL）与 text")
            args = ["--tweet-url", target, "--text", text]
        elif action == "publish":
            if not text:
                raise ActionError("x publish 需要 text（单条推文）")
            tmp = data / "state" / f"x-publish-{campaign or 'adhoc'}.json"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            import json
            tmp.write_text(json.dumps({"tweets": [text]}, ensure_ascii=False), encoding="utf-8")
            args = ["--file", str(tmp)]
        else:  # message / engage：vendor 队列与子参数由调用方给
            if not extra:
                raise ActionError(f"x {action} 需要 extra 参数（透传 vendor，如 "
                                  f"['--only','handle'] 或 ['follow','@a','@b']）")
            args = extra
        publish_options = {"logical_target": target} if action == "publish" and target else {}
        code = xrun(action, args, go=go, campaign=campaign, ledger=ledger,
                    data_dir=data, log=log, identity=identity_evidence,
                    execution_mode="autonomous" if routine else "user_visible_cdp",
                    **publish_options)
        fallback = {"channel": "x", "action": action, "campaign": campaign,
                    "target": target or "", "result":
                    ("committed" if go else "dry-run") if code == 0 else "failed",
                    "detail": f"vendor exit {code}"}
        return _record(_x_last_record(ledger, campaign, action, fallback))

    if channel == "email":
        raise ActionError("email 只支持队列执行（mkt email message --queue …）")

    raise ActionError(f"{channel}/{action} 未接入统一动作执行器")


def _dispatch_action(platform_user_id=None, **kwargs):
    """Bridge explicit routine approval through legacy channel re-checks.

    Channel functions keep their existing signatures.  The ContextVar scope is
    entered only for go=True *and* routine=True, is bound to this workspace and
    operation, and is reset before returning.  Dry-run and legacy calls never
    enter it.
    """
    if platform_user_id is not None:
        kwargs["platform_user_id"] = platform_user_id
    if not (kwargs.get("go") and kwargs.get("routine") is True):
        return _dispatch_action_inner(**kwargs)
    from .planlint import routine_scope

    data = Path(kwargs.get("data_dir") or resolve())
    channel = (kwargs.get("channel") or "").strip().lower()
    action = (kwargs.get("action") or "").strip().lower()
    with routine_scope(data, channel, action, kwargs.get("account", "")):
        return _dispatch_action_inner(**kwargs)


# --------------------------------------------------------------------------- #
# task-outcome recording: a ledger result is an action fact, never task success
# --------------------------------------------------------------------------- #

_OUTCOME_MAP = {
    "committed": ("sent", "unknown", ""),
    "failed": ("failed", "unresolved", ""),
    "indeterminate": ("unknown", "unknown",
                      "platform result is unknown; reconcile before any retry"),
    "suppressed": ("failed", "unresolved", ""),
    "bounced": ("failed", "unresolved", ""),
    "no-dm": ("failed", "unresolved", ""),
    # 目标级永久失败：这个目标不该再试，不等于平台或任务失败。
    "target_rejected": ("failed", "unresolved",
                        "target rejected this action; isolate the target, not the channel"),
    "unavailable_or_unrenderable": ("failed", "unresolved",
                                    "target unavailable or unrenderable; do not retry it"),
}


def record_ledger_outcome(data_dir, record, *, log=print):
    """Append a task-outcome record derived from one ledger record.

    A ``committed`` send becomes ``action_result=sent`` and
    ``task_outcome=unknown``: the platform accepted an action, not a task.
    ``indeterminate`` becomes ``action_result=unknown`` with an escalation
    reason, which is exactly the state that forbids retrying.
    """
    if not isinstance(record, dict):
        raise ActionError("ledger record must be an object")
    result = str(record.get("result") or "").strip().lower()
    if result not in _OUTCOME_MAP:
        return None
    action_result, task_outcome, escalation = _OUTCOME_MAP[result]
    from . import task_outcome as contract

    channel = str(record.get("channel") or "")
    action = str(record.get("action") or "")
    target = str(record.get("target") or "")
    payload = contract.build_record(
        task_id="%s:%s:%s" % (channel or "?", action or "?", target or record.get("ts") or "?"),
        channel=channel,
        ts=str(record.get("ts") or "") or None,
        action_taken="%s/%s" % (channel or "?", action or "?"),
        action_result=action_result,
        task_outcome=task_outcome,
        business_outcome="inconclusive",
        decision_basis="action_runner ledger result; a platform action is not task completion",
        escalation_reason=escalation,
        metadata={
            "ledger_result": result,
            "campaign": str(record.get("campaign") or ""),
            "target": target,
            "transport": str(record.get("transport") or ""),
        },
    )
    contract.append_outcome(data_dir, payload)
    return payload


def run_action(**kwargs):
    """Execute one write action, then record its task outcome and operations log.

    The dispatch path is unchanged. Recording is best-effort: a send that
    already happened must not be reported as failed because a local JSONL write
    failed, so the error is logged and the ledger record is returned unchanged.

    Operations-log context (operator / queue_item_id / run_id / attempt_id) is consumed here
    and never reaches the channel dispatch.
    """
    ops_context = {key: kwargs.pop(key, None)
                   for key in ("operator", "queue_item_id", "run_id", "attempt_id")}
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = _dispatch_action(**kwargs)
    finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if kwargs.get("go") and isinstance(record, dict):
        data = Path(kwargs.get("data_dir") or resolve())
        try:
            record_ledger_outcome(data, record, log=kwargs.get("log") or print)
        except Exception as exc:  # recording must not mask the action result
            (kwargs.get("log") or print)(f"[outcome] task outcome not recorded: {exc}")
    # 统一操作日志（§4.2 最低字段）：单动作/队列/MCP 共用此路径，dry-run 也落。
    if isinstance(record, dict):
        data = Path(kwargs.get("data_dir") or resolve())
        try:
            from . import operations_log
            operations_log.record_action(
                data, record,
                channel=kwargs.get("channel"), action=kwargs.get("action"),
                target=kwargs.get("target"), text=kwargs.get("text"),
                campaign=kwargs.get("campaign"),
                go=bool(kwargs.get("go")), routine=kwargs.get("routine"),
                started_at=started_at, finished_at=finished_at,
                **ops_context)
        except Exception as exc:  # 日志失败不掩盖动作结果，但必须大声可见
            (kwargs.get("log") or print)(f"[opslog] 操作日志写入失败：{exc}")
    return record
