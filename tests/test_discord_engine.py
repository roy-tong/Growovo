#!/usr/bin/env python3
"""Discord 发帖引擎：提交路径走页面内 keydown，不再依赖 CDP 按键。

全部用例替换 WebBridge 调用，不连接浏览器、不发真实消息。
"""
import json
import unittest
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.bridge import discord_engine


class EnterTest(unittest.TestCase):
    def test_enter_dispatches_keydown_inside_page(self):
        # CDP Input.dispatchKeyEvent never reached the page through this bridge,
        # so the composer kept its text and nothing was sent. The fix has to stay
        # a page-level event: a regression here silently stops every Discord post.
        with mock.patch.object(discord_engine.wb, "ev",
                               return_value={"value": {"ok": True, "prevented": True}}) as ev, \
                mock.patch.object(discord_engine.wb, "call") as call:
            out = discord_engine._enter(198571695)
        self.assertTrue(out["ok"])
        script = ev.call_args[0][1]
        self.assertIn("KeyboardEvent", script)
        self.assertIn("key: 'Enter'", script)
        self.assertIn(json.dumps(discord_engine.COMPOSER), script)
        call.assert_not_called()

    def test_enter_returns_empty_when_bridge_gives_nothing(self):
        with mock.patch.object(discord_engine.wb, "ev", return_value={}):
            self.assertEqual(discord_engine._enter(1), {})


class NewRowTest(unittest.TestCase):
    def test_row_count_growth_is_a_send(self):
        self.assertTrue(discord_engine._new_row({"mine": 2, "lastId": "1"},
                                                {"mine": 3, "lastId": "2"}))

    def test_changed_last_id_is_a_send(self):
        self.assertTrue(discord_engine._new_row({"mine": 2, "lastId": "1"},
                                                {"mine": 2, "lastId": "2"}))

    def test_same_rows_are_not_a_send(self):
        self.assertFalse(discord_engine._new_row({"mine": 2, "lastId": "1"},
                                                 {"mine": 2, "lastId": "1"}))

    def test_empty_channel_readback_is_not_a_send(self):
        self.assertFalse(discord_engine._new_row({"mine": 0}, {"mine": 0, "gone": True}))


if __name__ == "__main__":
    unittest.main()
