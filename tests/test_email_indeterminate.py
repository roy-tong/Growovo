"""G-13：SMTP 未知回执必须停批、非零退出、不可自动重试、留操作日志。

全部离线：假 SMTP 在 send_message 里抛 TimeoutError，模拟 DATA 已提交但
回执丢失（服务端可能已收下）。这不是 failed——重发会重复投递。
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import mkt
from channels import email_smtp
from core import operations_log
from core.ledger import Ledger


class _FakeSMTPChannel:
    """send_message 抛超时：模拟提交后未知结果；记录真实调用次数。"""

    transport = "smtp"

    def __init__(self):
        self.user = "me@example.com"
        self.sent = []

    def _client(self):
        return self

    def send_message(self, message):
        self.sent.append(message["To"])
        raise TimeoutError("timed out waiting for end-of-DATA reply")

    def close(self):
        pass


def _queue(root: Path, count: int = 2, campaign: str = "g13") -> Path:
    path = root / (campaign + ".json")
    path.write_text(json.dumps({
        "campaign": campaign,
        "channel": "email",
        "action": "message",
        "items": [
            {"id": "e%d" % index, "target": "user%d@example.com" % index,
             "subject": "hello", "body": "body %d" % index}
            for index in range(1, count + 1)
        ],
    }), encoding="utf-8")
    return path


class SendQueueIndeterminateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.queue = _queue(self.root)
        self.fake = _FakeSMTPChannel()
        patcher = mock.patch.object(email_smtp, "SMTPChannel", lambda: self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_queue(self, **kwargs):
        options = dict(go=True, adhoc=True, data_dir=self.data,
                       log=lambda *_a, **_k: None)
        options.update(kwargs)
        return email_smtp.send_queue(self.queue, **options)

    def test_batch_halts_after_first_indeterminate(self):
        records = self.run_queue()
        self.assertEqual(1, len(records), "未知回执后不得继续发送后续条目")
        self.assertEqual("indeterminate", records[0]["result"])
        self.assertTrue(records[0].get("halted"))
        self.assertEqual(["user1@example.com"], self.fake.sent)

    def test_ledger_marks_target_unknown_and_blocks_auto_retry(self):
        self.run_queue()
        rows = Ledger(self.data / "ledger.jsonl").all()
        self.assertEqual(1, len(rows))
        self.assertEqual("indeterminate", rows[0]["result"])
        self.assertIn("user1@example.com",
                      Ledger(self.data / "ledger.jsonl").no_retry("email"))

        # 第二次跑：第一封被账本不重试名单拒绝（不会重发），第二封才被尝试。
        self.fake.sent.clear()
        again = self.run_queue()
        self.assertEqual("rejected", again[0]["result"])
        self.assertIn("不重试名单", again[0]["detail"])
        self.assertEqual(["user2@example.com"], self.fake.sent)

    def test_operations_log_gets_one_complete_entry_per_attempt(self):
        self.run_queue()
        rows = operations_log.OperationsLog(self.data).all()
        self.assertEqual(1, len(rows))
        entry = rows[0]
        self.assertEqual("indeterminate", entry["result"])
        self.assertEqual("indeterminate", entry["error"]["code"])
        self.assertFalse(entry["error"]["retryable"])
        self.assertEqual(operations_log.content_sha256("body 1"),
                         entry["content_sha256"])

    def test_ops_log_failure_leaves_audit_gap_and_marks_record(self):
        with mock.patch.object(operations_log, "record_action",
                               side_effect=operations_log.OperationsLogError("disk full")):
            records = self.run_queue()
        self.assertIn("audit_gap", records[0])
        gaps = (self.data / operations_log.AUDIT_GAP_NAME).read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(1, len(gaps))
        gap = json.loads(gaps[0])
        self.assertEqual("audit_gap", gap["kind"])
        self.assertEqual("indeterminate", gap["result"])
        self.assertFalse(gap["complete_verification"])
        self.assertFalse(gap["retryable"])


class EmailCliExitCodeTests(unittest.TestCase):
    def call(self, records):
        args = SimpleNamespace(action="message", queue="/tmp/g13.json", go=True,
                               adhoc=True, only=None, campaign=None)
        with mock.patch("channels.email_smtp.send_queue", return_value=records):
            return mkt.cmd_email(args)

    def test_indeterminate_is_non_zero(self):
        self.assertEqual(3, self.call([{"result": "indeterminate"}]))

    def test_audit_gap_is_non_zero(self):
        self.assertEqual(3, self.call([{
            "result": "committed", "audit_gap": "/data/audit-gaps.jsonl",
            "audit_gap_reason": "OperationsLogError: disk full"}]))

    def test_committed_and_dry_run_are_success(self):
        self.assertEqual(0, self.call([{"result": "committed"},
                                       {"result": "dry-run"}]))

    def test_failed_is_non_zero_but_distinct(self):
        self.assertEqual(1, self.call([{"result": "failed"}]))


class QueueRunnerSingleLogTests(unittest.TestCase):
    """队列入口不重复补录：操作日志只由 send_queue 写一次。"""

    def test_run_queue_does_not_write_a_second_operations_row(self):
        from core import queue_runner
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            queue = _queue(root)
            record = {"id": "e1", "channel": "email", "action": "message",
                      "target": "user1@example.com", "campaign": "g13",
                      "result": "committed", "started_at": "2026-11-18T00:00:00",
                      "finished_at": "2026-11-18T00:00:01"}
            with mock.patch("channels.email_smtp.send_queue",
                            return_value=[record]) as sender:
                out = queue_runner.run_queue(queue, data_dir=data, go=True,
                                             log=lambda *_a, **_k: None)
            self.assertEqual([record], out)
            self.assertTrue(sender.called)
            self.assertFalse((data / operations_log.OPERATIONS_LOG_NAME).exists(),
                             "queue_runner 不得替 send_queue 再写一条操作日志")


if __name__ == "__main__":
    unittest.main()
