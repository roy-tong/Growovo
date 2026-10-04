#!/usr/bin/env python3
"""统一写路径事务管道（架构文档 §3）——所有渠道写原语的公共执行壳。

状态机：COMPOSE → FILL → VERIFIED → [截图] →（dry-run 终态 / SEND → COMMITTED|INDETERMINATE）

渠道以回调接入，CDP 与 SMTP 同构：
  compose(item)            打开编辑器 / 准备草稿（SMTP 为 no-op）
  fill(item)               输入内容（CDP 逐字符；SMTP 构造 MIME）
  readback(item) -> str    回读文本（CDP 读编辑器；SMTP 返回构造正文）
  send(item)               提交（点发送 / smtp.sendmail；抛异常 = FAILED）
  confirm(item) -> bool    成功确认（URL 变化 / 框清空 / 协议层 True）
  shot(item) -> path|None  发送前截图（无头绪返回 None）

铁律（449 空正文事故的固化）：readback 与 expect 归一化比对不一致 →
终态 FAILED，绝不进入 SEND。dry-run 永远停在 SEND 之前并留截图。
"""
import re
from datetime import datetime


def _norm(s):
    """归一化：去首尾空白 + 折叠连续空白（对齐 publish_x.fill_text 的比较口径）。"""
    return re.sub(r"\s+", " ", (s or "").strip())


class WritePipeline:
    def __init__(self, *, gate, ledger, throttle):
        self.gate = gate
        self.ledger = ledger
        self.throttle = throttle

    def run_one(self, item, *, channel, action, campaign, transport,
                expect, target, compose, fill, readback, send, confirm,
                shot=None, dry_run=True, max_len=None):
        """执行一条队列项，返回终态 record dict（同时已入账本）。

        expect   期望进入编辑器/邮件正文的文本（回读比对基准）
        target   渠道目标（@handle / email / url / tweet:url…）
        Gate 拒绝返回 result="rejected"（正常逐条结果，不抛异常）。
        """
        rec = {
            "id": item.get("id", ""),
            "channel": channel, "action": action, "campaign": campaign,
            "transport": transport, "target": target,
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            self.gate.check(channel=channel, action=action, target=target,
                            campaign=campaign, content=expect, max_len=max_len)
        except Exception as exc:  # GateError：逐条拒绝
            rec.update(result="rejected", detail=str(exc))
            return self._finish(rec)

        try:
            compose(item)                    # COMPOSE
            fill(item)                       # FILL
            got = readback(item)             # VERIFIED
            if _norm(got) != _norm(expect):
                rec.update(result="failed", detail=f"回读不一致: 读回 {len(got or '')}/{len(expect or '')} 字符，拒绝发送")
                return self._finish(rec)
            shot_path = shot(item) if shot else None
            if dry_run:
                rec.update(result="dry-run", evidence=str(shot_path or ""))
                return self._finish(rec)
            send(item)                       # SEND（异常→FAILED）
            ok = confirm(item)
            rec.update(result="committed" if ok else "indeterminate")
            if ok:
                self.throttle.daily_charge(channel, action)
        except Exception as exc:
            rec.update(result="failed", detail=str(exc)[:300])
        return self._finish(rec)

    def _finish(self, rec):
        rec["finished_at"] = datetime.now().isoformat(timespec="seconds")
        self.ledger.append(
            channel=rec["channel"], action=rec["action"], target=rec["target"],
            result=rec["result"], transport=rec["transport"], campaign=rec["campaign"],
            evidence=rec.get("evidence"), ref=rec.get("id"), detail=rec.get("detail", ""),
        )
        return rec
