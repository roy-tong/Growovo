#!/usr/bin/env python3
"""跨渠道队列执行器 —— 一份队列在任何渠道都能跑（MCP mkt_run_queue 的实现）。

队列仍是写操作的批粒度单位：先过 queue.lint（编辑期门禁），再统一做
暂停/UNVERIFIED/人审计检查，然后逐条交给 action_runner（同一套管道与账本）。
email 保持原 send_queue 整队执行（SMTP 有批量语义与退信处理）。

X 的 publish/message/engage 不是"逐目标触达"形状（线程文件 / vendor DM 队列），
队列只支持 x/reply；其余用 mkt action 或 mkt x 显式执行。
"""
from pathlib import Path

from .action_runner import ActionError, precheck, run_action
from .queue import item_text, lint_queue, load_queue
from .safety import is_paused
from .throttle import Throttle
from .workspace import resolve

QUEUE_ACTIONS = {"publish", "reply", "message"}


class QueueRunError(RuntimeError):
    pass


def run_queue(queue_path, *, data_dir=None, go=False, allow_unverified=False,
              only=None, log=print):
    """执行队列，返回逐条记录列表。"""
    data = Path(data_dir or resolve())
    queue_path = Path(queue_path)
    q = load_queue(queue_path)
    campaign = q.get("campaign") or queue_path.stem
    channel, action = q.get("channel"), q.get("action")

    errs = lint_queue(q, path=queue_path)
    if errs:
        raise QueueRunError("队列未过编辑期门禁：" + "；".join(errs[:5]))

    items = list(q.get("items") or [])
    if only:
        items = [i for i in items if i.get("id") == only or i.get("target") == only]
        if not items:
            raise QueueRunError(f"队列里没有 id/target = {only!r} 的项")

    if channel == "email":
        from channels.email_smtp import send_queue
        return send_queue(queue_path, go=go, only=only, campaign=campaign,
                          data_dir=data, log=log)

    if channel == "x" and action != "reply":
        raise QueueRunError("x 队列只支持 reply；publish 用 mkt action x publish，"
                            "message/engage 用 mkt x 或 mkt action 透传 vendor 参数")
    if action not in QUEUE_ACTIONS:
        raise QueueRunError(f"{channel}/{action} 不是队列可执行动作（{sorted(QUEUE_ACTIONS)}）")

    precheck(channel, action, data, campaign, go, allow_unverified)
    throttle = Throttle(data / "state")
    if go and not throttle.daily_allows(channel, action, len(items)):
        raise QueueRunError(f"日预算不足：{channel}/{action} 今日已用 "
                            f"{throttle.daily_used(channel, action)}，本次 {len(items)} 条")

    results = []
    for idx, item in enumerate(items):
        target = item.get("target") or item.get("url") or ""
        text = item_text(item)
        rec = run_action(
            channel=channel, action=action, data_dir=data, campaign=campaign,
            go=go, allow_unverified=allow_unverified, target=target, text=text,
            title=item.get("title"), url=item.get("url"),
            subreddit=item.get("subreddit"), number=item.get("number"),
            extra=item.get("extra"), log=log)
        rec = dict(rec)
        rec.setdefault("campaign", campaign)
        rec.setdefault("channel", channel)
        rec.setdefault("action", action)
        rec.setdefault("ref", item.get("id", ""))
        results.append(rec)
        log(f"[{idx + 1}/{len(items)}] {item.get('id', '')} {target}: {rec.get('result')}")
    return results
