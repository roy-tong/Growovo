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
    def test_new_row_without_content_identity_is_not_valid_readback(self):
        self.assertFalse(discord_engine._valid_readback(
            {"mine": 2, "lastId": "1"},
            {"mine": 3, "lastId": "2", "lastText": "different", "author": "Roy"},
            "expected", author="Roy"))


class _Clock:
    """假时钟：sleep 推进时间，避免 3s/2s 轮询在测试里真的等待。"""

    def __init__(self, start=1_000_000.0):
        self.now = float(start)

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, float(seconds))


def _reply(handlers, *, mid="222", text="answer"):
    clock = _Clock()

    def ev(_tab, script):
        for marker, value in handlers:
            if marker in script:
                return {"value": value() if callable(value) else value}
        raise AssertionError("unexpected script: %s" % script[:120])

    ev_mock = mock.Mock(side_effect=ev)
    call_mock = mock.Mock()
    with mock.patch.object(discord_engine.wb, "ev", ev_mock), \
            mock.patch.object(discord_engine.wb, "call", call_mock), \
            mock.patch.object(discord_engine, "time", clock):
        out = discord_engine.reply(11, "111", mid, text)
    return out, ev_mock, call_mock


def _handlers(**overrides):
    values = {
        "guildsnav___": {"ok": True, "already": True},
        "emptyHint": {"ready": True},
        "mounted: rows.length": {"ok": True, "id": "chat-messages-111-222"},
        "scrollHeight": {"ok": True},
        "contextmenu": {"ok": True},
        "items.slice": {"items": ["回复", "编辑"]},
        "reply_item_missing": {"ok": True, "label": "回复"},
        "replyBar": {"ok": True, "bars": 1, "text": "Dana: hi", "replyTo": "222"},
        "Escape": 1,
        "key: 'Enter'": {"ok": True, "prevented": True},
        "execCommand('selectAll')": {"ok": True, "focused": True},
        "len: (el.innerText": {"len": len("answer")},
        "lastText": {"mine": 3, "lastId": "9001", "lastText": "answer",
                     "author": "Roy", "replyTo": "222"},
        "mine: mine.length, lastId": {"mine": 2, "lastId": "111"},
    }
    values.update(overrides)
    return list(values.items())


class ReadbackFallbackTest(unittest.TestCase):
    def test_no_new_row_but_text_present_is_committed(self):
        out, _ev, _call = _reply(_handlers(**{
            "mine: mine.length, lastId": (lambda: {"mine": 2, "lastId": "111",
                         "lastText": "answer", "author": "Tongroy", "replyTo": "222"}),
            "present": {"present": True, "lastId": None, "lastText": "answer",
                        "author": "Tongroy"}}))
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["confirmed"])
        self.assertIsNone(out["serverId"])
        self.assertTrue(out["readback"]["text_present"])

    def test_no_new_row_and_no_text_stays_indeterminate(self):
        out, _ev, _call = _reply(_handlers(**{
            "mine: mine.length, lastId": (lambda: {"mine": 2, "lastId": "111",
                         "lastText": "answer", "author": "Tongroy", "replyTo": "222"}),
            "present": {"present": False}}))
        self.assertFalse(out["ok"])
        self.assertEqual(out["err"], "no new message row after Enter")

    def test_readback_scrolls_to_bottom(self):
        out, ev_mock, _call = _reply(_handlers(**{
            "mine: mine.length, lastId": (lambda: {"mine": 2, "lastId": "111",
                         "lastText": "answer", "author": "Tongroy", "replyTo": "222"}),
            "present": {"present": True, "lastId": None, "lastText": "answer",
                        "author": "Tongroy"}}))
        self.assertTrue(out["ok"])
        self.assertTrue(any("scrollHeight" in c.args[1] for c in ev_mock.call_args_list))


class GroupedReadbackTest(unittest.TestCase):
    def test_readback_walks_back_to_group_author(self):
        for script in (discord_engine.SENT, discord_engine.TEXT_PRESENT):
            self.assertIn("for (let i = mine.length - 1", script)
            self.assertIn("candidate", script)
            self.assertIn('class*="username"', script)

    def test_grouped_author_is_accepted(self):
        self.assertTrue(discord_engine._valid_readback(
            {"mine": 2, "lastId": "111"},
            {"mine": 3, "lastId": "9001", "lastText": "answer", "author": "Tongroy"},
            "answer", author="Tongroy"))


class ReplyTest(unittest.TestCase):
    """原生回复：找不到目标行/菜单项/回复条时都必须停手，不输入正文。"""

    def test_unmounted_target_does_not_type(self):
        out, ev_mock, call_mock = _reply(_handlers(**{
            "mounted: rows.length": {"ok": False, "mounted": 2}}))
        self.assertFalse(out["ok"])
        self.assertEqual(out["err"], "target_message_not_mounted")
        call_mock.assert_not_called()
        self.assertTrue(any("scrollHeight" in c.args[1] for c in ev_mock.call_args_list))

    def test_missing_reply_menu_item_does_not_type(self):
        out, _ev, call_mock = _reply(_handlers(**{
            "items.slice": {"items": ["编辑", "删除"]},
            "reply_item_missing": {"ok": False, "err": "reply_item_missing"}}))
        self.assertFalse(out["ok"])
        self.assertEqual(out["err"], "reply_item_missing")
        call_mock.assert_not_called()

    def test_missing_reply_bar_escapes_without_typing(self):
        out, ev_mock, call_mock = _reply(_handlers(**{
            "replyBar": {"ok": False, "bars": 0}}))
        self.assertFalse(out["ok"])
        self.assertEqual(out["err"], "reply_context_missing")
        call_mock.assert_not_called()
        self.assertTrue(any("Escape" in c.args[1] for c in ev_mock.call_args_list))

    def test_stale_reply_bar_for_another_target_does_not_type(self):
        out, _ev, call_mock = _reply(_handlers(**{
            "replyBar": {"ok": True, "bars": 1, "text": "Other", "replyTo": "999"}}))
        self.assertFalse(out["ok"])
        self.assertEqual(out["err"], "reply_context_mismatch")
        call_mock.assert_not_called()

    def test_new_row_with_someone_elses_content_is_not_committed(self):
        out, _ev, _call = _reply(_handlers(**{
            "lastText": {"mine": 3, "lastId": "9001", "lastText": "other text",
                         "author": "Roy", "replyTo": "222"}}))
        self.assertFalse(out["ok"])
        self.assertFalse(out.get("confirmed", False))

    def test_happy_path_types_and_binds_reply_to(self):
        out, _ev, call_mock = _reply(_handlers())
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["confirmed"])
        self.assertEqual(out["replyTo"], "222")
        self.assertEqual(out["message_id"], "222")
        self.assertEqual(out["serverId"], "9001")
        self.assertEqual(out["pick"]["label"], "回复")
        call_mock.assert_called_once()
        self.assertEqual(call_mock.call_args.kwargs["method"], "Input.insertText")
        self.assertEqual(call_mock.call_args.kwargs["params"]["text"], "answer")



class _Clock:
    """假时钟：sleep 推进时间，避免 3s/2s 轮询在测试里真的等待。"""

    def __init__(self, start=1_000_000.0):
        self.now = float(start)

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, float(seconds))


def _reply(handlers, *, mid="222", text="answer"):
    clock = _Clock()

    def ev(_tab, script):
        for marker, value in handlers:
            if marker in script:
                return {"value": value() if callable(value) else value}
        raise AssertionError("unexpected script: %s" % script[:120])

    ev_mock = mock.Mock(side_effect=ev)
    call_mock = mock.Mock()
    with mock.patch.object(discord_engine.wb, "ev", ev_mock), \
            mock.patch.object(discord_engine.wb, "call", call_mock), \
            mock.patch.object(discord_engine, "time", clock):
        out = discord_engine.reply(11, "111", mid, text)
    return out, ev_mock, call_mock


def _handlers(**overrides):
    values = {
        "guildsnav___": {"ok": True, "already": True},
        "emptyHint": {"ready": True},
        "mounted: rows.length": {"ok": True, "id": "chat-messages-111-222"},
        "scrollHeight": {"ok": True},
        "contextmenu": {"ok": True},
        "items.slice": {"items": ["回复", "编辑"]},
        "reply_item_missing": {"ok": True, "label": "回复"},
        "replyBar": {"ok": True, "bars": 1, "text": "Dana: hi", "replyTo": "222"},
        "Escape": 1,
        "key: 'Enter'": {"ok": True, "prevented": True},
        "execCommand('selectAll')": {"ok": True, "focused": True},
        "len: (el.innerText": {"len": len("answer")},
        "lastHead": {"mine": 3, "lastId": "9001", "lastHead": "answer"},
        "mine: mine.length, lastId": (lambda states=iter([
            {"mine": 2, "lastId": "111"},
            {"mine": 3, "lastId": "9001", "lastText": "answer", "author": "Tongroy", "replyTo": "222"},
            {"mine": 3, "lastId": "9001", "lastText": "answer", "author": "Tongroy", "replyTo": "222"},
        ]): lambda: next(states, {"mine": 3, "lastId": "9001", "lastText": "answer",
                                      "author": "Tongroy", "replyTo": "222"}))(),
    }
    values.update(overrides)
    return list(values.items())


class ReadbackFallbackTest(unittest.TestCase):
    def test_no_new_row_but_text_present_is_committed(self):
        out, _ev, _call = _reply(_handlers(**{
            "mine: mine.length, lastId": (lambda: {"mine": 2, "lastId": "111",
                         "lastText": "answer", "author": "Tongroy", "replyTo": "222"}),
            "present": {"present": True, "lastId": None, "lastText": "answer",
                        "author": "Tongroy"}}))
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["confirmed"])
        self.assertIsNone(out["serverId"])
        self.assertTrue(out["readback"]["text_present"])

    def test_no_new_row_and_no_text_stays_indeterminate(self):
        out, _ev, _call = _reply(_handlers(**{
            "mine: mine.length, lastId": (lambda: {"mine": 2, "lastId": "111",
                         "lastText": "answer", "author": "Tongroy", "replyTo": "222"}),
            "present": {"present": False}}))
        self.assertFalse(out["ok"])
        self.assertEqual(out["err"], "no new message row after Enter")

    def test_readback_scrolls_to_bottom(self):
        out, ev_mock, _call = _reply(_handlers(**{
            "mine: mine.length, lastId": (lambda: {"mine": 2, "lastId": "111",
                         "lastText": "answer", "author": "Tongroy", "replyTo": "222"}),
            "present": {"present": True, "lastId": None, "lastText": "answer",
                        "author": "Tongroy"}}))
        self.assertTrue(out["ok"])
        self.assertTrue(any("scrollHeight" in c.args[1] for c in ev_mock.call_args_list))


class GroupedReadbackTest(unittest.TestCase):
    def test_readback_walks_back_to_group_author(self):
        for script in (discord_engine.SENT, discord_engine.TEXT_PRESENT):
            self.assertIn("for (let i = mine.length - 1", script)
            self.assertIn("candidate", script)
            self.assertIn('class*="username"', script)

    def test_grouped_author_is_accepted(self):
        self.assertTrue(discord_engine._valid_readback(
            {"mine": 2, "lastId": "111"},
            {"mine": 3, "lastId": "9001", "lastText": "answer", "author": "Tongroy"},
            "answer", author="Tongroy"))


class ReplyTest(unittest.TestCase):
    """原生回复：找不到目标行/菜单项/回复条时都必须停手，不输入正文。"""

    def test_unmounted_target_does_not_type(self):
        out, ev_mock, call_mock = _reply(_handlers(**{
            "mounted: rows.length": {"ok": False, "mounted": 2}}))
        self.assertFalse(out["ok"])
        self.assertEqual(out["err"], "target_message_not_mounted")
        call_mock.assert_not_called()
        self.assertTrue(any("scrollHeight" in c.args[1] for c in ev_mock.call_args_list))

    def test_missing_reply_menu_item_does_not_type(self):
        out, _ev, call_mock = _reply(_handlers(**{
            "items.slice": {"items": ["编辑", "删除"]},
            "reply_item_missing": {"ok": False, "err": "reply_item_missing"}}))
        self.assertFalse(out["ok"])
        self.assertEqual(out["err"], "reply_item_missing")
        call_mock.assert_not_called()

    def test_missing_reply_bar_escapes_without_typing(self):
        out, ev_mock, call_mock = _reply(_handlers(**{
            "replyBar": {"ok": False, "bars": 0}}))
        self.assertFalse(out["ok"])
        self.assertEqual(out["err"], "reply_context_missing")
        call_mock.assert_not_called()
        self.assertTrue(any("Escape" in c.args[1] for c in ev_mock.call_args_list))

    def test_happy_path_types_and_binds_reply_to(self):
        out, _ev, call_mock = _reply(_handlers())
        self.assertTrue(out["ok"])
        self.assertTrue(out["confirmed"])
        self.assertEqual(out["replyTo"], "222")
        self.assertEqual(out["message_id"], "222")
        self.assertEqual(out["serverId"], "9001")
        self.assertEqual(out["pick"]["label"], "回复")
        call_mock.assert_called_once()
        self.assertEqual(call_mock.call_args.kwargs["method"], "Input.insertText")
        self.assertEqual(call_mock.call_args.kwargs["params"]["text"], "answer")



if __name__ == "__main__":
    unittest.main()
