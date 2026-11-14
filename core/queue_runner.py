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

from .action_runner import ActionError, precheck, run_action
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
              continue_on_duplicate=False, interval_seconds=None, sleeper=None):
    """执行队列，返回逐条记录列表。

    account=None 继承队列顶层 account；显式字符串覆盖它。账号范围始终按
    routine policy 精确校验，routine=False 时这些新参数不参与旧审批逻辑。
    """
    data = Path(data_dir or resolve())
    queue_path = Path(queue_path)
    q = load_queue(queue_path)
    campaign = q.get("campaign") or queue_path.stem
    channel, action = q.get("channel"), q.get("action")
    account = q.get("account", "") if account is None else account

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

    if channel == "email":
        from channels.email_smtp import send_queue
        kwargs = {"go": go, "only": only, "only_ids": only_ids,
                  "campaign": campaign, "data_dir": data, "log": log}
        if routine is True:
            kwargs.update(routine=True, account=account)
        return send_queue(queue_path, **kwargs)

    if channel == "x" and action not in ("reply", "publish"):
        raise QueueRunError("x 队列支持 reply / 单条 publish；"
                            "message/engage 用 mkt x 或 mkt action 透传 vendor 参数")
    if action not in QUEUE_ACTIONS:
        raise QueueRunError(f"{channel}/{action} 不是队列可执行动作（{sorted(QUEUE_ACTIONS)}）")

    if routine is True:
        precheck(channel, action, data, campaign, go, allow_unverified,
                 routine=True, account=account)
    else:
        precheck(channel, action, data, campaign, go, allow_unverified)
    throttle = Throttle(data / "state")
    if go and not throttle.daily_allows(channel, action, len(items)):
        raise QueueRunError(f"日预算不足：{channel}/{action} 今日已用 "
                            f"{throttle.daily_used(channel, action)}，本次 {len(items)} 条")

    results = []
    for idx, item in enumerate(items):
        target = item.get("target") or item.get("url") or ""
        text = item_text(item)
        action_kwargs = {
            "channel": channel, "action": action, "data_dir": data,
            "campaign": campaign, "go": go,
            "allow_unverified": allow_unverified, "target": target, "text": text,
            "title": item.get("title"), "url": item.get("url"),
            "subreddit": item.get("subreddit"), "number": item.get("number"),
            "extra": item.get("extra"), "log": log,
        }
        if routine is True:
            action_kwargs.update(routine=True, account=account)
        try:
            rec = run_action(**action_kwargs)
        except SystemExit as exc:
            raise QueueRunError(f"执行器中止：{exc}") from exc
        rec = dict(rec)
        rec.setdefault("campaign", campaign)
        rec.setdefault("channel", channel)
        rec.setdefault("action", action)
        rec.setdefault("ref", item.get("id", ""))
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
    return results
