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
from pathlib import Path


def _norm(s):
    """归一化：去首尾空白 + 折叠连续空白（对齐 publish_x.fill_text 的比较口径）。"""
    return re.sub(r"\s+", " ", (s or "").strip())


class WritePipeline:
    def __init__(self, *, gate, ledger, throttle, pause_file=None, approval=None):
        self.gate = gate
        self.ledger = ledger
        self.throttle = throttle
        self.pause_file = pause_file  # core.safety.pause_file(data_dir)；存在即一切写操作拒跑
        self.approval = approval      # callable(campaign) -> "plan"|"adhoc"（planlint.enforce_approval）；
                                      # 实跑（非 dry-run）时强制，抛 PlanNotApproved 即整条拒跑

    def run_one(self, item, *, channel, action, campaign, transport,
                expect, target, compose, fill, readback, send, confirm,
                shot=None, dry_run=True, max_len=None):
        """执行一条队列项，返回终态 record dict（同时已入账本）。

        expect   期望进入编辑器/邮件正文的文本（回读比对基准）
        target   渠道目标（@handle / email / url / tweet:url…）
        Gate 拒绝 / 全局暂停 / 人审门禁（实跑） 返回 result="rejected"（正常逐条结果，不抛异常）。
        """
        rec = {
            "id": item.get("id", ""),
            "channel": channel, "action": action, "campaign": campaign,
            "transport": transport, "target": target,
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        if self.pause_file is not None and self.pause_file.exists():
            rec.update(result="rejected", detail="全局暂停中（mkt pause --off 恢复）")
            return self._finish(rec)
        if self.approval is not None and not dry_run:
            try:
                mode = self.approval(campaign)
                rec["approval"] = mode
            except Exception as exc:  # PlanNotApproved：实跑未绑定已人审 plan
                rec.update(result="rejected", detail=f"人审门禁: {exc}")
                return self._finish(rec)
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
            # 发送前原子预留日预算：预算不足时绝不进入 send；并发进程也不会
            # 在“检查后、发送前”同时越过日上限。
            if self.throttle is not None:
                try:
                    self.throttle.reserve(channel, action, 1)
                except Exception as exc:
                    rec.update(result="rejected", detail=f"日预算门禁: {exc}")
                    return self._finish(rec)
            send(item)                       # SEND（异常→FAILED）
            ok = confirm(item)
            rec.update(result="committed" if ok else "indeterminate")
        except Exception as exc:
            rec.update(result="failed", detail=str(exc)[:300])
        return self._finish(rec)

    def _finish(self, rec):
        rec["finished_at"] = datetime.now().isoformat(timespec="seconds")
        detail = rec.get("detail", "")
        if rec.get("approval") == "adhoc":
            detail = (detail + "; " if detail else "") + "adhoc=true"
        self.ledger.append(
            channel=rec["channel"], action=rec["action"], target=rec["target"],
            result=rec["result"], transport=rec["transport"], campaign=rec["campaign"],
            evidence=rec.get("evidence"), ref=rec.get("id"), detail=detail,
        )
        return rec


def standard_pipeline(data_dir, ledger, *, adhoc=False):
    """渠道写原语的标准管道：Gate + Throttle + 全局暂停闸 + 人审门禁（实跑强制）。

    所有渠道写路径一律从这里构造——审批/暂停不再依赖各渠道自行接线
    （此前 reddit/linkedin/github/hn 未接，email 接错目录，形同虚设）。
    adhoc 仅为 CLI 人工通道（--adhoc）；MCP 不传，agent 不得自我授权。
    """
    from .gates import Gate, load_suppressions
    from .planlint import enforce_approval
    from .safety import pause_file
    from .throttle import Throttle
    data_dir = Path(data_dir)
    return WritePipeline(
        gate=Gate(ledger, suppress=load_suppressions(data_dir)),
        ledger=ledger, throttle=Throttle(data_dir / "state"),
        pause_file=pause_file(data_dir),
        approval=lambda c: enforce_approval(data_dir, c, adhoc=adhoc))
