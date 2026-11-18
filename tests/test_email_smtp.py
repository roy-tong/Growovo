"""Email SMTP 通道：附件、代理、超时与发送前检查（全部离线）。"""
import os
import tempfile
import unittest
from email import message_from_bytes
from pathlib import Path
from unittest import mock

from channels import email_smtp
from channels.email_smtp import (
    SMTPChannel, build_message, guess_attachment_type, parse_socks_proxy,
    validate_attachments,
)


class AttachmentTypeTests(unittest.TestCase):
    def test_known_and_unknown(self):
        self.assertEqual(guess_attachment_type("a.pdf"), ("application", "pdf"))
        self.assertEqual(guess_attachment_type("A.PNG"), ("image", "png"))
        self.assertEqual(guess_attachment_type("a.zip"), ("application", "octet-stream"))


class SocksProxyTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_socks_proxy("127.0.0.1:7890"), ("127.0.0.1", 7890))

    def test_rejects_bad_format(self):
        for value in ("", "127.0.0.1", "127.0.0.1:abc", "127.0.0.1:0", "127.0.0.1:70000"):
            with self.assertRaises(RuntimeError, msg=value):
                parse_socks_proxy(value)


class AttachmentValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "ok.pdf").write_bytes(b"%PDF-1.4")
        (self.root / "empty.txt").write_bytes(b"")

    def test_missing_and_empty_are_reported(self):
        items = [
            {"id": "a", "attachments": ["ok.pdf"]},
            {"id": "b", "attachments": ["nope.pdf"]},
            {"id": "c", "attachments": ["empty.txt"]},
        ]
        problems = validate_attachments(items, self.root / "queue.json")
        self.assertTrue(any("b:" in p and "不存在" in p for p in problems))
        self.assertTrue(any("c:" in p and "空文件" in p for p in problems))
        self.assertFalse(any(p.startswith("a:") for p in problems))

    def test_relative_paths_resolve_against_queue_dir(self):
        items = [{"id": "a", "attachments": ["ok.pdf"]}]
        self.assertEqual(validate_attachments(items, self.root / "queue.json"), [])


class BuildMessageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pdf = self.root / "简介.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 test")
        self.png = self.root / "cover.png"
        self.png.write_bytes(b"\x89PNG test")

    def test_attachment_parts(self):
        item = {
            "_body": "正文", "subject": "主题", "from_name": "Roy",
            "target": "a@example.com", "attachments": [str(self.pdf), str(self.png)],
        }
        raw = build_message(item, "me@example.com").as_bytes()
        msg = message_from_bytes(raw)
        parts = list(msg.walk())
        names = [part.get_filename() for part in parts if part.get_filename()]
        self.assertEqual(len(names), 2)
        self.assertIn("简介.pdf", names)
        self.assertIn("cover.png", names)
        types = {part.get_content_type() for part in parts}
        self.assertIn("application/pdf", types)
        self.assertIn("image/png", types)

    def test_no_attachments_is_plain_text(self):
        item = {"_body": "正文", "subject": "主题", "target": "a@example.com"}
        msg = message_from_bytes(build_message(item, "me@example.com").as_bytes())
        self.assertEqual(msg.get_content_type(), "text/plain")


class TimeoutTests(unittest.TestCase):
    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            self.host, self.port, self.timeout = host, port, timeout
            self.logged_in = None

        def login(self, user, password):
            self.logged_in = (user, password)

        def quit(self):
            pass

    def _client(self, **env):
        base = {"MKT_EMAIL_USER": "me@example.com", "MKT_EMAIL_APP_PASSWORD": "x"}
        base.update(env)
        with mock.patch.dict(os.environ, base, clear=False):
            channel = SMTPChannel()
            with mock.patch.object(email_smtp.smtplib, "SMTP_SSL", self._FakeSMTP):
                client = channel._client()
        return client

    def test_default_timeout(self):
        os.environ.pop("MKT_EMAIL_TIMEOUT", None)
        client = self._client()
        self.assertEqual(client.timeout, 60.0)
        self.assertEqual(client.logged_in, ("me@example.com", "x"))

    def test_env_timeout(self):
        client = self._client(MKT_EMAIL_TIMEOUT="300")
        self.assertEqual(client.timeout, 300.0)

    def test_invalid_timeout(self):
        with self.assertRaises(RuntimeError):
            self._client(MKT_EMAIL_TIMEOUT="soon")


if __name__ == "__main__":
    unittest.main()
