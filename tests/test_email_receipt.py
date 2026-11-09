#!/usr/bin/env python3
"""G-5（review 2026-11-09）：email committed 必须携带受理回执工件。

冻结缺口：此前 email 的 committed 记录零回执——GitHub committed 有 html_url、
X 有 vendor 结果文件，email 只有「代码没抛异常」；且模块文档声称「协议通道不
产生 INDETERMINATE」与管道实际行为（send 后异常含已受理后断连 = indeterminate）
不符。修复后：
  - committed 记录在结果工件里带 receipt 路径，回执含 message_id/accepted_at，
    并如实标注 delivery_verified=false（受理 ≠ 投递）；
  - send 抛异常（含服务器已受理后断连）→ indeterminate，无回执；
  - dry-run 行为不变。
"""
from __future__ import annotations

import json
import smtplib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from channels import email_smtp  # noqa: E402


class _FakeSMTP:
    """替身 SMTPChannel：记录发送的邮件，可按用例抛协议异常。"""

    user = "sender@example.com"

    def __init__(self):
        self.sent: list[object] = []
        self.raise_on_send: Exception | None = None

    def _client(self):
        return self

    def send_message(self, msg):
        if self.raise_on_send is not None:
            raise self.raise_on_send
        self.sent.append(msg)

    def close(self):
        pass


def _queue_file(tmp: Path, name: str = "q1") -> Path:
    q = tmp / f"{name}.json"
    q.write_text(json.dumps({
        "campaign": "receipt-test",
        "items": [{"id": "i1", "target": "a@b.c", "subject": "s", "body": "hello"}],
    }), encoding="utf-8")
    return q


class EmailReceiptHonesty(unittest.TestCase):
    def _run(self, tmp: Path, *, go: bool, fake: _FakeSMTP):
        with mock.patch.object(email_smtp, "SMTPChannel", return_value=fake):
            records = email_smtp.send_queue(
                _queue_file(tmp), go=go, adhoc=True, data_dir=str(tmp),
                log=lambda *a, **k: None)
        return records

    def test_committed_carries_acceptance_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            fake = _FakeSMTP()
            records = self._run(tmp, go=True, fake=fake)
            self.assertEqual(1, len(records))
            rec = records[0]
            self.assertEqual("committed", rec["result"], rec)
            # 回执工件存在且字段如实
            receipt = Path(rec["receipt"])
            self.assertTrue(receipt.is_file(), rec)
            data = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertEqual("smtp_accepted", data["stage"])
            self.assertTrue(data["message_id"])
            self.assertTrue(data["accepted_at"])
            self.assertFalse(data["delivery_verified"])
            # 回执里的 message_id 就是实际发出的邮件头
            self.assertEqual(1, len(fake.sent))
            self.assertEqual(data["message_id"], fake.sent[0]["Message-ID"])

    def test_send_exception_after_acceptance_is_indeterminate(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            fake = _FakeSMTP()
            fake.raise_on_send = smtplib.SMTPServerDisconnected(
                "connection dropped after DATA")  # 服务器可能已受理
            records = self._run(tmp, go=True, fake=fake)
            self.assertEqual("indeterminate", records[0]["result"], records)
            self.assertNotIn("receipt", records[0])
            receipts = list((tmp / "shots" / "receipt-test").glob("email-receipt-*"))
            self.assertEqual(0, len(receipts), receipts)

    def test_dry_run_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            records = self._run(tmp, go=False, fake=_FakeSMTP())
            self.assertEqual("dry-run", records[0]["result"], records)
            self.assertNotIn("receipt", records[0])


if __name__ == "__main__":
    unittest.main()
