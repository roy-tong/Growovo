#!/usr/bin/env python3
"""跨渠道队列执行器 —— 一份队列在任何渠道都能跑（MCP mkt_run_queue 的实现）。

队列仍是写操作的批粒度单位：先过 queue.lint（编辑期门禁），再统一做
暂停/UNVERIFIED/人审计检查，然后逐条交给 action_runner（同一套管道与账本）。
email 保持原 send_queue 整队执行（SMTP 有批量语义与退信处理）。

X 支持 reply 与单条 publish 队列；publish 逻辑目标绑定到原账本，保留未知结果防重试。
message/engage 仍通过显式动作执行。
"""
import random
import time
from pathlib import Path

from . import account_identity
from . import action_gateway
from .action_runner import ActionError, gate_account_identity, precheck

run_action = action_gateway.execute
from .queue import item_text, lint_queue, load_queue
from .safety import is_paused
from .throttle import Throttle
from .workspace import resolve

QUEUE_ACTIONS = {"publish", "reply", "message"}

# 拒绝原因分层：账本防重发是幂等命中（跳过即可），抑制名单是永久无效目标
# （跳过即可），其余拒绝（内容不合规/预算/人审/未知）必须停下来给
# 人看。混为一谈会把坏内容当成"已交付"。
DUPLICATE_MARKERS = ("防重发", "已成功发过")
SUPPRESSION_MARKERS = ("抑制名单", "不重试名单")

# browser-attach 的单条拒绝里，有些只影响当前目标或正文（换一条就继续），
# 有些说明整条渠道此刻跑不了（暂停/额度/标签页/授权/桥接），要整渠道顺延。
ITEM_LEVEL_BLOCKS = {"suppressed", "target_invalid", "text_empty", "target_required"}


def _followup_record(channel, action, campaign, item, result, detail, next_step):
    return {
        "channel": channel,
        "action": action,
        "ref": item.get("id", ""),
        "target": item.get("target") or item.get("url") or "",
        "campaign": campaign,
        "result": result,
        "detail": detail,
        "next": next_step,
    }


def _browser_recovery(exc):
    """把 browser-attach 异常折成 (当前条结果, 说明, 下一步)；不猜发送结果。"""
    from .browser_attach import BridgeError
    from .browser_write import StopChannel

    if isinstance(exc, StopChannel):
        return ("blocked",
                "平台风控信号，渠道停手：%s" % getattr(exc, "reason", exc),
                "平台风控提示：停该渠道，不改身份、不换代理、不重试本条；人工检查页面后隔天再跑，"
                "并把频率降到平台建议的节奏（降频、拆批、顺延）")
    if isinstance(exc, BridgeError):
        return ("deferred",
                "桥接/浏览器不可用：%s" % exc,
                "确认浏览器已打开、目标标签页可见、桥接在线；恢复后重跑未完成条目")
    code = str(getattr(exc, "code", "") or "")
    if code == "paused":
        return ("deferred", "全局暂停中：%s" % exc,
                "确认要继续后执行 mkt pause --off，再重跑未完成条目")
    if code == "quota_exhausted":
        return ("deferred", "渠道额度已用完：%s" % exc,
                "等额度窗口重置后重跑；或降低每日配额、把剩余条目拆到明后天")
    if code == "tab_hidden":
        return ("deferred", "标签页不可见：%s" % exc,
                "把该平台标签页切到前台（或解锁屏幕）后重跑；本条未发送、不占额度")
    if code == "unsupported":
        return ("deferred", "动作不在 browser-attach 白名单：%s" % exc,
                "检查队列动作；改用支持的动作或走 vendor/API 通道")
    return ("deferred", "传输门禁未通过：%s" % exc,
            "按提示补齐授权/身份校验后重跑；不要换号或换身份绕行")


class QueueRunError(RuntimeError):
    pass


def _pace_seconds(interval_seconds) -> float:
    """把 (min,max) 或单值转成本次等待秒数；无配置返回 0。"""
    try:
        if isinstance(interval_seconds, (int, float)):
            return max(0.0, float(interval_seconds))
        low, high = list(interval_seconds)[:2]
    except (TypeError, ValueError):
        return 0.0
    low, high = float(low), float(high)
    if high < low:
        low, high = high, low
    return random.uniform(low, high)


def rejection_kind(rec) -> str:
    """把一条 rejected 记录归为 duplicate / suppression / other。"""
    detail = str((rec or {}).get("detail") or "")
    if any(m in detail for m in DUPLICATE_MARKERS):
        return "duplicate"
    if any(m in detail for m in SUPPRESSION_MARKERS):
        return "suppression"
    return "other"


def run_queue(queue_path, *, data_dir=None, go=False, allow_unverified=False,
              only=None, only_ids=None, log=print, routine=False, account=None,
              continue_on_duplicate=False, interval_seconds=None, sleeper=None,
              operator="", run_id="", resume_batch=False, transport=None,
              legacy_cdp=False):
    """执行队列，返回逐条记录列表。

    account=None 继承队列顶层 account；显式字符串覆盖它。账号范围始终按
    routine policy 精确校验，routine=False 时这些新参数不参与旧审批逻辑。
    transport=None 继承队列顶层 transport；browser-attach 走 owner-attested
    独立授权，跳过通用 CDP 身份和 plan/routine 预检，其余写门禁保留。
    operator/run_id 只进操作日志（§4.2），不影响任何门禁。
    resume_batch 只转给 email 通道：人工裁决上一批未知回执后清除批次阻塞标记；
    MCP 不暴露它，Agent 不能自行解除人工裁决。
    """
    data = Path(data_dir or resolve())
    queue_path = Path(queue_path)
    operator = str(operator or ("queue:%s" % queue_path.name))
    q = load_queue(queue_path)
    campaign = q.get("campaign") or queue_path.stem
    channel, action = q.get("channel"), q.get("action")
    account = q.get("account", "") if account is None else account
    queue_transport = str(q.get("transport") or "").strip().lower()
    transport = queue_transport if transport is None else str(transport or "").strip().lower()
    if transport not in {"", "browser-attach"}:
        raise QueueRunError("未知队列传输 %r" % transport)
    if queue_transport and transport != queue_transport:
        raise QueueRunError("命令行 transport 与队列顶层 transport 不一致")
    if transport == "browser-attach" and routine:
        raise QueueRunError("browser-attach 使用 owner-attested 独立授权，不与 routine policy 混用")

    errs = lint_queue(q, path=queue_path)
    if errs:
        raise QueueRunError("队列未过编辑期门禁：" + "；".join(errs[:5]))

    items = list(q.get("items") or [])
    if only:
        items = [i for i in items if i.get("id") == only or i.get("target") == only]
        if not items:
            raise QueueRunError(f"队列里没有 id/target = {only!r} 的项")
    if only_ids:
        wanted = {str(x) for x in only_ids}
        items = [i for i in items
                 if str(i.get("id") or "") in wanted
                 or str(i.get("target") or "") in wanted]
        if not items:
            raise QueueRunError("配额切片为空：选中的条目不在队列里（可能已被账本跳过）")

    browser_errors = ()
    if channel == "email":
        if transport:
            raise QueueRunError("email 不支持 browser-attach；请使用既有 SMTP 队列")
        from channels.email_smtp import BatchBlocked, send_queue
        kwargs = {"go": go, "only": only, "only_ids": only_ids,
                  "campaign": campaign, "data_dir": data, "log": log,
                  "operator": operator, "run_id": run_id,
                  "resume_batch": resume_batch}
        if routine is True:
            kwargs.update(routine=True, account=account)
        # send_queue 自己写统一操作日志：同一次发送只有一个凭证入口，
        # 这里不再补录（重复记录会让一次动作看起来像两次）。
        try:
            return send_queue(queue_path, **kwargs)
        except BatchBlocked as exc:
            raise QueueRunError("批次拒跑：%s" % exc) from exc

    if channel == "x" and action not in ("reply", "publish"):
        raise QueueRunError("x 队列支持 reply / 单条 publish；"
                            "message/engage 用 mkt x 或 mkt action 透传 vendor 参数")
    if action not in QUEUE_ACTIONS:
        raise QueueRunError(f"{channel}/{action} 不是队列可执行动作（{sorted(QUEUE_ACTIONS)}）")
    if transport == "browser-attach":
        from .browser_attach import BridgeError
        from .browser_write import ActionBlocked, StopChannel
        from .platform_compliance import OWNER_ATTESTED_ACTIONS
        browser_errors = (ActionBlocked, StopChannel, BridgeError)
        if action not in OWNER_ATTESTED_ACTIONS.get(channel, set()):
            raise QueueRunError("%s/%s 不在 browser-attach 能力白名单内" % (channel, action))

    # G-01 账号身份门禁：绑定身份的授权在真实发送前必须观测到与授权一致
    # 的实际平台账号（登录界面标记不算）；未绑定授权不介入。dry-run 只提示。
    # browser-attach 在逐条传输适配器内观测当前页面身份，不走这套 CDP 预检。
    identity_evidence = None
    if transport != "browser-attach" and channel in account_identity.IDENTITY_CHANNELS:
        policy = account_identity.load_policy(data)
        blockers = account_identity.identity_blockers(
            data, account, policy=policy, policy_loaded=True)
        if blockers:
            blocker = blockers[0]
            if not go:
                for item in blockers:
                    log("[identity] dry-run 阻塞项：%s %s；下一步：%s"
                        % (item["code"], item["detail"], item["next"]))
            else:
                raise QueueRunError(
                    "账号身份门禁拒绝整队执行：%s %s；下一步：%s"
                    % (blocker["code"], blocker["detail"], blocker["next"]))
        elif not go:
            log("[identity] dry-run：未做实际账号身份校验；"
                "实跑（go）将强制校验浏览器实际账号")
        else:
            try:
                identity_evidence = gate_account_identity(channel, data, account)
            except ActionError as exc:
                raise QueueRunError(f"账号身份门禁拒绝整队执行：{exc}") from exc

    deferred_records = []
    if transport != "browser-attach":
        routine_kwargs = {}
        if identity_evidence:
            routine_kwargs["platform_user_id"] = identity_evidence[
                "observed_platform_identity"]
        if routine is True:
            precheck(channel, action, data, campaign, go, allow_unverified,
                     routine=True, account=account, **routine_kwargs)
        else:
            precheck(channel, action, data, campaign, go, allow_unverified)
        throttle = Throttle(data / "state")
        # 限额不再整队拒跑：超出单次上限或当日剩余额度的部分切成 deferred，
        # 能跑的照常跑；平台风控的节奏建议随 deferred 一起返回。
        requested = len(items)
        if go:
            run_cap = throttle.run_cap(channel, action)
            remaining = throttle.remaining(channel, action)
            limit = requested
            if run_cap is not None:
                limit = min(limit, run_cap)
            if remaining is not None:
                limit = min(limit, remaining)
            limit = max(0, limit)
            if limit < requested:
                hint = throttle.advice(channel, action, requested)
                reasons = []
                if run_cap is not None and requested > run_cap:
                    reasons.append("单次上限 %d" % run_cap)
                if remaining is not None and requested > remaining:
                    reasons.append("今日剩余 %d" % remaining)
                detail = "限额切片（%s）" % "、".join(reasons) if reasons else "限额切片"
                log("限额切片：%s/%s 本次 %d 条，先跑 %d 条，其余 %d 条记 deferred。%s"
                    % (channel, action, requested, limit, requested - limit, hint))
                for item in items[limit:]:
                    deferred_records.append({
                        "channel": channel,
                        "action": action,
                        "ref": item.get("id", ""),
                        "target": item.get("target") or item.get("url") or "",
                        "campaign": campaign,
                        "result": "deferred",
                        "detail": detail,
                        "next": hint,
                    })
                items = items[:limit]

    results = []
    channel_stop = None
    for idx, item in enumerate(items):
        target = item.get("target") or item.get("url") or ""
        text = item_text(item)
        if channel_stop is not None:
            # 渠道级问题（风控信号/暂停/额度/标签页/桥接）只停这条渠道：
            # 剩余条目记 deferred 留待重跑，不丢队列、不把业务判死。
            results.append(_followup_record(
                channel, action, campaign, item, "deferred",
                "渠道已停：%s" % channel_stop[1], channel_stop[2]))
            log(f"[{idx + 1}/{len(items)}] {item.get('id', '')} {target}: deferred（渠道已停）")
            continue
        action_kwargs = {
            "channel": channel, "action": action, "data_dir": data,
            "campaign": campaign, "go": go,
            "allow_unverified": allow_unverified, "target": target, "text": text,
            "title": item.get("title"), "url": item.get("url"),
            "subreddit": item.get("subreddit"), "number": item.get("number"),
            "extra": item.get("extra"), "log": log, "transport": transport,
            "actor": item.get("actor") or item.get("handle") or item.get("author") or "",
        }
        if legacy_cdp:
            action_kwargs["legacy_cdp"] = True
        if routine is True:
            action_kwargs.update(routine=True, account=account,
                                 operator=operator, run_id=run_id,
                                 queue_item_id=str(item.get("id") or ""))
            if identity_evidence:
                # G-01：把队列级观测到的实际 user_id 传给逐条执行——
                # 审批校验（allows）在 require_identity 下需要它；传的是
                # 观测值，不是调用方自报值。
                action_kwargs["platform_user_id"] = identity_evidence[
                    "observed_platform_identity"]
        else:
            action_kwargs.update(operator=operator, run_id=run_id,
                                 queue_item_id=str(item.get("id") or ""))
        try:
            rec = run_action(**action_kwargs)
        except SystemExit as exc:
            raise QueueRunError(f"执行器中止：{exc}") from exc
        except browser_errors as exc:
            code = str(getattr(exc, "code", "") or "")
            if code in ITEM_LEVEL_BLOCKS:
                # 只影响本条（目标/正文/抑制名单），换一条就继续。
                next_step = {
                    "suppressed": "该目标在抑制名单/不重试名单，别再触达；换目标或人工确认后移出名单",
                    "target_invalid": "按平台要求改目标格式后重跑本条；其余条目继续",
                    "text_empty": "补正文后重跑本条；其余条目继续",
                    "target_required": "补目标后重跑本条；其余条目继续",
                }[code]
                results.append(_followup_record(
                    channel, action, campaign, item, "rejected",
                    str(exc), next_step))
                log(f"[{idx + 1}/{len(items)}] {item.get('id', '')} {target}: "
                    f"rejected（{code}，继续下一条）")
                continue
            outcome, detail, next_step = _browser_recovery(exc)
            channel_stop = (outcome, detail, next_step)
            from . import action_resolution
            recovery = action_resolution.recovery(
                {"kind": "channel", "channel": channel, "action": action},
                {"code": code or outcome, "detail": detail}, route="browser-attach")
            followup = _followup_record(
                channel, action, campaign, item, outcome, detail, next_step)
            followup["recovery"] = recovery
            results.append(followup)
            log(f"channel stopped: {detail}")
            log(f"[{idx + 1}/{len(items)}] {item.get('id', '')} {target}: {outcome}")
            continue
        rec = dict(rec)
        rec.setdefault("campaign", campaign)
        rec.setdefault("channel", channel)
        rec.setdefault("action", action)
        rec.setdefault("ref", item.get("id", ""))
        if identity_evidence:
            rec.setdefault("identity", dict(identity_evidence))
        if account and "account" not in rec:
            rec["account"] = account
        if rec.get("result") in {"failed", "indeterminate", "rejected"}:
            from . import action_resolution
            rec.setdefault("recovery", action_resolution.recovery(
                {"kind": "channel", "channel": channel, "action": action},
                {"code": str(rec.get("result") or "failed"),
                 "detail": str(rec.get("detail") or rec.get("error") or "")},
                route=transport))
        results.append(rec)
        log(f"[{idx + 1}/{len(items)}] {item.get('id', '')} {target}: {rec.get('result')}")
        # 人类节奏：真实发送之间随机停顿；dry-run 不碰平台，不需要等。
        if go and interval_seconds and rec.get("result") == "committed" \
                and idx + 1 < len(items):
            wait = _pace_seconds(interval_seconds)
            if wait > 0:
                log(f"pace: {wait:.0f}s before next item")
                if sleeper is not None:
                    sleeper(wait)
                else:
                    time.sleep(wait)
        if rec.get("result") in {"failed", "indeterminate", "rejected"}:
            # 账本幂等命中不是故障：继续跑后面的新条目，否则每天复用的
            # 队列永远卡在第一条已发送的旧内容上。
            if continue_on_duplicate and rec.get("result") == "rejected" \
                    and rejection_kind(rec) == "duplicate":
                log("Already sent in this campaign; skipping and continuing.")
                continue
            log("Queue paused: inspect the last result before resuming.")
            break
    return results + deferred_records
