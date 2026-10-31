#!/usr/bin/env python3
"""Email SMTP 写通道 —— 验证等级：PORTED。

模式来源（PROVEN）：newoutreach/do_send.py + smtp_followup.py（事故后已实跑 1300 封量级）；
本封装接入框架管道后未整跑，首跑清单见 03 渠道文档 §5。

凭据只走环境变量：MKT_EMAIL_USER / MKT_EMAIL_APP_PASSWORD（App Password）。
事务语义：SMTP 协议层 sendmail 无异常 = COMMITTED；异常 = FAILED。
（协议通道不产生 INDETERMINATE——这正是选 SMTP 做写通道的原因。）
"""
import json
import os
import smtplib
import time
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
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
               throttle=None, data_dir=None, adhoc=False, log=log):
    """跑一个邮件队列。go=False 全程 dry-run（不建 SMTP 连接、不碰网络）。
    campaign 缺省取队列文件的 campaign 字段（其次文件名）。
    go=True 必须绑定已人审 plan；无 plan 管辖需 adhoc=True（CLI --adhoc，账本记 adhoc=true）。"""
    data_dir = Path(data_dir or DATA)
    ledger = ledger or Ledger(data_dir / "ledger.jsonl")
    throttle = throttle or Throttle(data_dir / "state")
    qcampaign, items = load_queue(queue_file)
    campaign = campaign or qcampaign
    if go:
        from core.planlint import enforce_approval
        enforce_approval(data_dir, campaign, adhoc=adhoc)  # fail-fast；逐条记账由管道 approval 复核
    if only:
        items = [i for i in items if i.get("id") == only or i.get("target") == only]

    throttle.check_run("email", "message", len(items))
    if not throttle.daily_allows("email", "message", len(items)):
        raise SystemExit("日预算已满（email/message），明天再跑或调 campaign 配额")

    ch = SMTPChannel() if go else None
    gate = Gate(ledger, suppress=load_suppressions(data_dir))
    from core.safety import pause_file
    from core.planlint import enforce_approval
    pipe = WritePipeline(gate=gate, ledger=ledger, throttle=throttle,
                         pause_file=pause_file(data_dir),
                         approval=lambda c: enforce_approval(data_dir, c, adhoc=adhoc))
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

            def _send(item, _it=it, _ch=ch, _go=go):
                if not _go:
                    return  # dry-run 管道在 send 前已拦截，这里兜底
                msg = MIMEText(_it["_body"], "plain", "utf-8")
                msg["Subject"] = Header(_it.get("subject", ""), "utf-8")
                msg["From"] = formataddr(
                    (str(Header(_it.get("from_name", ""), "utf-8")), _ch.user))
                msg["To"] = _it.get("target") or _it.get("to")
                _ch._client().send_message(msg)

            def _confirm(item, timeout=0):
                return True  # 协议层无异常即 committed

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
