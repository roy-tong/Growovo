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
  email    仅队列（action_runner 拒绝单条发信）
"""
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
    from .session_gate import CDP_UNAVAILABLE, LOGGED_IN, PROBES, check_login
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


def _parse_issue(target):
    import re
    m = re.match(r"^(?:issue:)?([\w.-]+/[\w.-]+)#(\d+)$", str(target or "").strip())
    if not m:
        raise ActionError("github reply 的 target 应为 issue:owner/repo#N")
    return m.group(1), int(m.group(2))


def _x_last_record(ledger, campaign, action, fallback):
    recs = [r for r in ledger.results(channel="x", campaign=campaign, action=action)]
    if recs:
        return dict(recs[-1], channel="x", action=action, campaign=campaign)
    return fallback


def _dispatch_action_inner(*, channel, action, data_dir=None, campaign="", go=False,
                           allow_unverified=False, target=None, text=None, title=None, url=None,
                           subreddit=None, number=None, extra=None, log=print,
                           routine=False, account="", platform_user_id=None):
    """执行单条写动作，返回账本记录 dict。"""
    data = Path(data_dir or resolve())
    channel = (channel or "").strip().lower()
    action = (action or "").strip().lower()
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
        return rec
    if action in READ_ACTIONS:
        raise ActionError("读动作请用 mkt search/inbox/audit，不走 run_action")
    if not go:
        log(f"[action] {channel}/{action} dry-run（加 --go 才实跑）")

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
                    data_dir=data, log=log, **publish_options)
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
    """Execute one write action, then record its task outcome.

    The dispatch path is unchanged. Recording is best-effort: a send that
    already happened must not be reported as failed because a local JSONL write
    failed, so the error is logged and the ledger record is returned unchanged.
    """
    record = _dispatch_action(**kwargs)
    if kwargs.get("go") and isinstance(record, dict):
        data = Path(kwargs.get("data_dir") or resolve())
        try:
            record_ledger_outcome(data, record, log=kwargs.get("log") or print)
        except Exception as exc:  # recording must not mask the action result
            (kwargs.get("log") or print)(f"[outcome] task outcome not recorded: {exc}")
    return record
