"""Every real SMTP attempt must identify the authenticated sender account.

All transports are local fakes. No network, credentials or real email are used.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from channels import email_smtp
from core import operations_log


class _FakeSMTPChannel:
    transport = "smtp"

    def __init__(self, *, indeterminate=False):
        self.user = "actual-sender@example.com"
        self.indeterminate = indeterminate
        self.sent = []

    def _client(self):
        return self

    def send_message(self, message):
        self.sent.append(message["To"])
        if self.indeterminate:
            raise TimeoutError("post-DATA timeout")

    def close(self):
        pass


class EmailSenderIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = self.root / "data"
        self.queue = self.root / "queue.json"
        self.queue.write_text(json.dumps({
            "campaign": "sender-identity", "channel": "email", "action": "message",
            "items": [{"id": "e1", "target": "recipient@example.com",
                       "subject": "hello", "body": "offline body"}],
        }), encoding="utf-8")

    def _run(self, fake, *, go=True):
        with mock.patch.object(email_smtp, "SMTPChannel", lambda: fake):
            return email_smtp.send_queue(
                self.queue, go=go, adhoc=True, data_dir=self.data,
                log=lambda *_args, **_kwargs: None)

    def test_committed_record_contains_authenticated_sender_not_credentials(self):
        fake = _FakeSMTPChannel()
        records = self._run(fake)
        self.assertEqual("committed", records[0]["result"])
        row = operations_log.OperationsLog(self.data).all()[0]
        account = row["account"]
        self.assertEqual("actual-sender@example.com", account["platform_user_id"])
        self.assertEqual("actual-sender@example.com", account["handle"])
        self.assertEqual("smtp:authenticated-login", account["proof_ref"])
        self.assertTrue(account["observed_at"])
        serialized = json.dumps(row).lower()
        self.assertNotIn("password", serialized)
        self.assertNotIn("credential", serialized)

    def test_indeterminate_record_retains_sender_for_reconciliation(self):
        fake = _FakeSMTPChannel(indeterminate=True)
        records = self._run(fake)
        self.assertEqual("indeterminate", records[0]["result"])
        row = operations_log.OperationsLog(self.data).all()[0]
        self.assertEqual("actual-sender@example.com",
                         row["account"]["platform_user_id"])
        self.assertEqual("indeterminate", row["result"])

    def test_dry_run_does_not_invent_an_observed_sender(self):
        records = self._run(None, go=False)
        self.assertEqual("dry-run", records[0]["result"])
        row = operations_log.OperationsLog(self.data).all()[0]
        self.assertIsNone(row["account"]["platform_user_id"])
        self.assertIsNone(row["account"]["proof_ref"])


if __name__ == "__main__":
    unittest.main()
