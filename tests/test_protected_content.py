#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import protected_content
from core.bridge import x_engine


class ProtectedContentTest(unittest.TestCase):
    def test_detects_chinese_and_english_protected_messages(self):
        chinese = "这些帖子受到保护只有经过批准的关注者才可查看 @VeroVo 的帖子。若要申请访问，点击关注。了解更多"
        english = "These posts are protected. Only approved followers can view them."
        self.assertTrue(protected_content.is_protected_text(chinese))
        self.assertTrue(protected_content.is_protected_text(english))
        self.assertFalse(protected_content.is_protected_text("A normal public agent discussion"))

    def test_mark_protected_is_persistent_and_covers_other_posts_by_handle(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            protected_content.mark_protected(
                data, "https://x.com/Toscane/status/123", evidence="protected posts")
            self.assertTrue(protected_content.is_target_suppressed(
                data, "https://x.com/Toscane/status/456"))
            self.assertTrue(protected_content.is_target_suppressed(data, "@Toscane"))
            self.assertFalse(protected_content.is_target_suppressed(
                data, "https://x.com/other/status/456"))
            protected_content.mark_protected(data, "https://x.com/Toscane/status/123")
            record = json.loads((data / "state" / "protected-accounts.json").read_text())
            self.assertEqual(["toscane"], list(record["accounts"]))
            self.assertEqual("protected_account", record["accounts"]["toscane"]["reason"])
            lines = (data / "suppress.txt").read_text().splitlines()
            self.assertEqual(len(lines), len(set(lines)))

    def test_reply_does_not_navigate_or_reload_for_protected_account(self):
        with mock.patch.object(x_engine.wb, "ev",
                               return_value={"value": {"ok": False, "err": "protected_account"}}), \
                mock.patch.object(x_engine, "_full_nav") as full_nav, \
                mock.patch.object(x_engine, "_reload") as reload_page:
            got = x_engine.reply_native(11, "https://x.com/Toscane/status/123", "hello")
        self.assertFalse(got["ok"])
        self.assertEqual("protected_account", got["err"])
        full_nav.assert_not_called()
        reload_page.assert_not_called()

    def test_supply_merge_rejects_protected_handle(self):
        from core import supply_pool
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            protected_content.mark_protected(data, "https://x.com/Toscane/status/123")
            got = supply_pool.merge(data, [{
                "channel": "x", "action": "reply",
                "target": "https://x.com/Toscane/status/456", "actor": "Toscane",
                "context": "agent observability and billing", "text": "reply",
            }])
            self.assertEqual(1, got["excluded_touched"])
            self.assertEqual("protected_account", got["rejected"][0]["reason"])

    def test_read_stats_does_not_open_browser_for_protected_handle(self):
        from channels.x import read_stats
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            protected_content.mark_protected(data, "https://x.com/Toscane/status/123")
            with mock.patch.object(read_stats, "BrowserAttach") as attach:
                got = read_stats.read("Toscane", data / "stats", data_dir=data)
            attach.assert_not_called()
            self.assertEqual("protected_account", got["login_status"])
            self.assertIsNone(got["followers"])


class ProtectedSuppressionSourceTest(unittest.TestCase):
    """抑制不能只依赖 suppress.txt 的副本（2026-11-26 全量回归发现）。"""

    def test_record_alone_suppresses_the_handle(self):
        from core.gates import load_suppressions
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "state").mkdir()
            (data / "state" / "protected-accounts.json").write_text(json.dumps({
                "accounts": {"toscane": {"handle": "Toscane",
                                         "reason": "protected_account"}}}),
                encoding="utf-8")
            self.assertFalse((data / "suppress.txt").exists())
            suppressed = load_suppressions(data)
            self.assertIn("Toscane", suppressed)
            self.assertIn("@Toscane", suppressed)
            self.assertTrue(protected_content.is_target_suppressed(
                data, "https://x.com/Toscane/status/999"))

    def test_damaged_record_fails_closed(self):
        from core.gates import SuppressionIntegrityError, load_suppressions
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "state").mkdir()
            (data / "state" / "protected-accounts.json").write_text(
                '{"accounts": {"toscane": ', encoding="utf-8")
            with self.assertRaises(SuppressionIntegrityError):
                load_suppressions(data)

    def test_union_does_not_change_other_targets(self):
        from core.gates import Gate, GateError, load_suppressions
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "state").mkdir()
            (data / "state" / "protected-accounts.json").write_text(json.dumps({
                "accounts": {"toscane": {"handle": "Toscane"}}}), encoding="utf-8")
            gate = Gate(suppress=load_suppressions(data))
            with self.assertRaises(GateError):
                gate.check(channel="x", action="reply", target="Toscane")
            self.assertEqual("@healthy",
                             gate.check(channel="x", action="reply", target="@healthy"))


if __name__ == "__main__":
    unittest.main()
