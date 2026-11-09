#!/usr/bin/env python3
"""Email SMTP 写通道 —— 验证等级：PORTED。

模式来源（PROVEN）：newoutreach/do_send.py + smtp_followup.py（事故后已实跑 1300 封量级）；
本封装接入框架管道后未整跑，首跑清单见 03 渠道文档 §5。

凭据只走环境变量：MKT_EMAIL_USER / MKT_EMAIL_APP_PASSWORD（App Password）。
事务语义（G-5 修正，review 2026-11-09）：
  send 无异常 = SMTP 服务器已受理（DATA 后收到 2xx）→ COMMITTED，
  并落回执工件（message_id + accepted_at；投递不可回读验证，回执里明说）；
  send 抛异常（含服务器已受理但连接在应答前中断）= INDETERMINATE，交人工裁决。
  回读为同构构造文本：VERIFIED 关只校验队列解析一致性，不校验协议层内容。
"""
import json
import os
import smtplib
import time
from datetime import datetime
from email.header import Header
from email.message import Message
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from pathlib import Path

from core.evidence import Evidence, log
from core.gates import Gate, load_suppressions
from core.ledger import Ledger
from core.pipeline import WritePipeline
from core.throttle import Throttle

DATA = Path(__file__).resolve().parents[1] / "data"  # 仓库根/data（原 parents[2] 误指仓库外层，人审/暂停闸查空目录）


def load_queue(path):
    q = json.loads(Path(path).read_text(encoding="utf-8"))
    items = q.get("items", [])
    for it in items:
        it["_body"] = Path(it["body_file"]).read_text(encoding="utf-8") \
            if it.get("body_file") else it.get("body", "")
    return q.get("campaign", Path(path).stem), items


class SMTPChannel:
    transport = "smtp"

    def __init__(self):
        self.user = os.environ.get("MKT_EMAIL_USER", "")
        self.pw = os.environ.get("MKT_EMAIL_APP_PASSWORD", "")
        self._smtp = None

    def _client(self):
        if self._smtp is None:
            if not self.user or not self.pw:
                raise RuntimeError("缺 MKT_EMAIL_USER / MKT_EMAIL_APP_PASSWORD 环境变量")
            self._smtp = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60)
            self._smtp.login(self.user, self.pw)
        return self._smtp

    def close(self):
        if self._smtp is not None:
            try:
                self._smtp.quit()
            finally:
                self._smtp = None


def send_queue(queue_file, *, go=False, only=None, campaign=None, ledger=None,
               throttle=None, data_dir=None, adhoc=False, routine=False,
               account="", log=log):
    """跑一个邮件队列。go=False 全程 dry-run（不建 SMTP 连接、不碰网络）。
    campaign 缺省取队列文件的 campaign 字段（其次文件名）。
    go=True 必须绑定已人审 plan；无 plan 管辖需 adhoc=True（CLI --adhoc，账本记 adhoc=true）。
    显式 routine=True 可改用命中 channel/action/account 的有效 routine-policy。"""
    data_dir = Path(data_dir or DATA)
    ledger = ledger or Ledger(data_dir / "ledger.jsonl")
    throttle = throttle or Throttle(data_dir / "state")
    qcampaign, items = load_queue(queue_file)
    campaign = campaign or qcampaign
    from core.planlint import enforce_approval

    def _approval(c, **kwargs):
        if not kwargs:
            return enforce_approval(data_dir, c, adhoc=adhoc)
        return enforce_approval(data_dir, c, adhoc=adhoc, **kwargs)

    if go:
        if routine is True:
            _approval(campaign, routine=True, channel="email",
                      action="message", account=account)  # fail-fast；逐条由管道复核
        else:
            _approval(campaign)  # 原 plan/adhoc fail-fast，行为不变
    if only:
        items = [i for i in items if i.get("id") == only or i.get("target") == only]

    throttle.check_run("email", "message", len(items))
    if not throttle.daily_allows("email", "message", len(items)):
        raise SystemExit("日预算已满（email/message），明天再跑或调 campaign 配额")

    ch = SMTPChannel() if go else None
    gate = Gate(ledger, suppress=load_suppressions(data_dir))
    from core.safety import pause_file
    pipe = WritePipeline(gate=gate, ledger=ledger, throttle=throttle,
                         pause_file=pause_file(data_dir), approval=_approval,
                         routine=routine, account=account)
    ev = Evidence(data_dir / "shots" / campaign)

    log(f"email 队列 {campaign}: {len(items)} 条（{'实跑' if go else 'dry-run'}）")
    results = []
    try:
        for idx, it in enumerate(items):
            target = it.get("target") or it.get("to", "")
            subject = it.get("subject", "")
            body = it.get("_body", "")
            expect = f"{subject}\n\n{body}"  # 门禁同时覆盖主题与正文；回读同构

            def _fill(item, _ch=ch, _go=go):
                if _go:
                    _ch._client()  # 惰性建连：首条时登录一次

            def _readback(item, _e=expect):
                return _e  # 协议通道：读回=构造的全文本身

            message_id = make_msgid(domain="growovo-send.local")  # 发前生成，回执可追溯到本条

            def _send(item, _it=it, _ch=ch, _go=go, _mid=message_id):
                if not _go:
                    return  # dry-run 管道在 send 前已拦截，这里兜底
                msg = MIMEText(_it["_body"], "plain", "utf-8")
                msg["Subject"] = Header(_it.get("subject", ""), "utf-8")
                msg["From"] = formataddr(
                    (str(Header(_it.get("from_name", ""), "utf-8")), _ch.user))
                msg["To"] = _it.get("target") or _it.get("to")
                msg["Message-ID"] = _mid
                _ch._client().send_message(msg)

            receipt_path: Path | None = None

            def _confirm(item, _ev=ev, _c=campaign, _it=it, _mid=message_id):
                # 只在 send 无异常后到达：即服务器已受理（DATA 后 2xx）。
                # 回执如实记录"受理"而非"已投递"——协议层无法验证投递。
                nonlocal receipt_path
                receipt_path = _ev.write_json(
                    f"email-receipt-{_it.get('id', 'item')}",
                    {
                        "message_id": _mid,
                        "accepted_at": datetime.now().isoformat(timespec="seconds"),
                        "transport": "smtp_ssl",
                        "server": "smtp.gmail.com",
                        "stage": "smtp_accepted",
                        "delivery_verified": False,
                        "meaning": "SMTP 服务器受理回执；投递（收件箱/垃圾箱分级）协议不可回读验证",
                    },
                )
                return True

            def _shot(item, _it=it, _ev=ev):
                p = _ev.shot_path(f"{_it.get('id', 'item')}.txt")
                p.write_text(
                    f"To: {_it.get('target') or _it.get('to')}\n"
                    f"Subject: {_it.get('subject', '')}\n\n{_it['_body']}",
                    encoding="utf-8")
                return p

            rec = pipe.run_one(
                it, channel="email", action="message", campaign=campaign,
                transport="smtp", expect=expect, target=target,
                compose=lambda item: None, fill=_fill, readback=_readback,
                send=_send, confirm=_confirm, shot=_shot, dry_run=not go,
            )
            if receipt_path is not None:
                rec["receipt"] = str(receipt_path)  # 结果工件携带回执位置；账本行不含（后续经管道 API 扩展）
            results.append(rec)
            log(f"[{idx + 1}/{len(items)}] {target}: {rec['result']}"
                + (f" ({rec.get('detail', '')})" if rec.get("detail") else ""))
            if go and idx < len(items) - 1:  # 人类节奏只约束真实发送；干跑要快
                wait = throttle.interval("email", "message")
                log(f"等待 {wait}s…")
                time.sleep(wait)
    finally:
        if ch:
            ch.close()
    out = ev.write_json("email-result", {"campaign": campaign, "records": results})
    log(f"结果已写入 {out}")
    return results
