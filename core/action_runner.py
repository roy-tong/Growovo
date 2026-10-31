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


def precheck(channel, action, data_dir, campaign, go, allow_unverified, target=None):
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
    if not go:
        return
    if is_paused(data_dir):
        raise ActionError("全局暂停中（mkt pause --off 恢复）——写操作拒跑")
    if reg[channel].get("status") == "UNVERIFIED" and not allow_unverified:
        raise ActionError(f"{channel} 渠道为 UNVERIFIED，拒绝实跑（人工首跑校准后加 allow_unverified=true）")
    try:
        enforce_approval(data_dir, campaign)
    except Exception as exc:
        # 统一成 ActionError，让 CLI/MCP 的拒跑路径一致；原因保留原文。
        raise ActionError(f"人审门禁: {exc}") from exc


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


def run_action(*, channel, action, data_dir=None, campaign="", go=False,
               allow_unverified=False, target=None, text=None, title=None, url=None,
               subreddit=None, number=None, extra=None, log=print):
    """执行单条写动作，返回账本记录 dict。"""
    data = Path(data_dir or resolve())
    channel = (channel or "").strip().lower()
    action = (action or "").strip().lower()
    precheck(channel, action, data, campaign, go, allow_unverified, target=target)
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
        return rec

    if channel == "github":
        from channels import github
        if number and "/" in str(target or "") and "#" not in str(target):
            slug, num = str(target).strip(), int(number)
        else:
            slug, num = _parse_issue(target)
        if not text:
            raise ActionError("github reply 需要 text")
        return github.reply(slug, num, text, go=go, campaign=campaign,
                            ledger=ledger, data_dir=data, log=log)

    if channel == "reddit":
        from channels import reddit
        if action == "publish":
            sub = subreddit or str(target or "").replace("r/", "")
            if not (sub and title and text):
                raise ActionError("reddit publish 需要 subreddit/target、title、text")
            return reddit.publish(sub, title, text, go=go, campaign=campaign,
                                  ledger=ledger, data_dir=data, log=log)
        if not (target and text):
            raise ActionError("reddit reply 需要 target（帖子 URL）与 text")
        return reddit.reply(target, text, go=go, campaign=campaign,
                            ledger=ledger, data_dir=data, log=log)

    if channel == "linkedin":
        import os
        from channels import linkedin
        if go and os.environ.get("MKT_LINKEDIN_AUTO", "") != "1":
            raise ActionError("LinkedIn 半自动需要终端确认，MCP/队列无法交互；"
                              "实跑请设 MKT_LINKEDIN_AUTO=1 并自行承担全自动风险")
        if not (target and text):
            raise ActionError("linkedin message 需要 target（profile URL）与 text")
        return linkedin.message(target, text, go=go, campaign=campaign,
                                ledger=ledger, data_dir=data, log=log)

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
        code = xrun(action, args, go=go, campaign=campaign, ledger=ledger,
                    data_dir=data, log=log)
        fallback = {"channel": "x", "action": action, "campaign": campaign,
                    "target": target or "", "result":
                    ("committed" if go else "dry-run") if code == 0 else "failed",
                    "detail": f"vendor exit {code}"}
        return _x_last_record(ledger, campaign, action, fallback)

    if channel == "email":
        raise ActionError("email 只支持队列执行（mkt email message --queue …）")

    raise ActionError(f"{channel}/{action} 未接入统一动作执行器")
