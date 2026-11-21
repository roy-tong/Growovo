#!/usr/bin/env python3
"""X 回读与发送回执绑定；替换 WebBridge 和时钟，不连接真实账号。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.bridge import x_engine


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class ReadbackTimingTest(unittest.TestCase):
    def test_profile_post_appearing_after_twelve_seconds_is_found(self):
        clock = FakeClock()
        manifest = [{"key": "post", "prefix": "Growovo release canary"}]

        def evaluate(_tab, code):
            if "const PREFIXES" in code:
                found = {"post": "2123965028969894253"} if clock.now >= 13 else {}
                return {"value": {"found": found, "scanned": 1}}
            return {"value": "/owner"}

        with mock.patch.object(x_engine.time, "time", side_effect=clock.time), \
                mock.patch.object(x_engine.time, "sleep", side_effect=clock.sleep), \
                mock.patch.object(x_engine.wb, "ev", side_effect=evaluate), \
                mock.patch.object(x_engine, "_full_nav", return_value=True):
            got = x_engine.readback(11, "owner", manifest)

        self.assertEqual(got, {"post": "2123965028969894253"})
        self.assertGreaterEqual(clock.now, 13)


class StatusAfterSendTest(unittest.TestCase):
    def test_status_after_returns_first_page_report(self):
        clock = FakeClock()
        seen = []

        def evaluate(_tab, code):
            seen.append(code)
            if len(seen) == 1:
                return {"value": {"id": "", "url": "", "source": ""}}
            return {"value": {"id": "555", "url": "https://x.com/o/status/555",
                              "source": "toast"}}

        with mock.patch.object(x_engine.time, "time", side_effect=clock.time), \
                mock.patch.object(x_engine.time, "sleep", side_effect=clock.sleep), \
                mock.patch.object(x_engine.wb, "ev", side_effect=evaluate):
            got = x_engine._status_after(11, ["1", "2"], "hello")

        self.assertEqual(got, {"id": "555", "url": "https://x.com/o/status/555",
                               "source": "toast"})
        self.assertEqual(len(seen), 2)
        self.assertLess(clock.now, 1.0)

    def test_status_after_times_out_empty(self):
        clock = FakeClock()
        with mock.patch.object(x_engine.time, "time", side_effect=clock.time), \
                mock.patch.object(x_engine.time, "sleep", side_effect=clock.sleep), \
                mock.patch.object(x_engine.wb, "ev",
                                  return_value={"value": {"id": "", "url": "", "source": ""}}):
            got = x_engine._status_after(11, [], "hello", timeout=1.0)

        self.assertEqual(got.get("id"), "")
        self.assertGreaterEqual(clock.now, 1.0)

    def test_status_after_survives_evaluate_errors(self):
        clock = FakeClock()
        with mock.patch.object(x_engine.time, "time", side_effect=clock.time), \
                mock.patch.object(x_engine.time, "sleep", side_effect=clock.sleep), \
                mock.patch.object(x_engine.wb, "ev",
                                  side_effect=x_engine.wb.WBError({"code": "tab"})):
            got = x_engine._status_after(11, [], "hello", timeout=0.5)

        self.assertEqual(got.get("id"), "")

    def test_status_ids_reads_article_links(self):
        with mock.patch.object(x_engine.wb, "ev",
                               return_value={"value": ["111", "222"]}), \
                mock.patch.object(x_engine.wb, "js", return_value="visible"):
            got = x_engine._status_ids(11)

        self.assertEqual(got, ["111", "222"])


class SendReportsStatusTest(unittest.TestCase):
    def test_original_reports_readable_error_when_composer_does_not_clear(self):
        send_code = x_engine._send_script("hello")

        def evaluate(_tab, code):
            if code == x_engine.OPEN_COMPOSER:
                return {"value": {"ok": True}}
            if code == x_engine.STATUS_IDS:
                return {"value": []}
            if code == send_code:
                return {"value": {"ok": False, "cleared": False, "len": 5, "clickMs": 12}}
            return {"value": {"hits": [], "len": 0}}

        with mock.patch.object(x_engine.wb, "ev", side_effect=evaluate):
            got = x_engine.original(11, "hello")

        self.assertFalse(got["ok"])
        self.assertEqual(got["err"], "composer did not clear after send")

    def test_reply_reopens_composer_after_full_nav(self):
        url = "https://x.com/a/status/999"
        clock = FakeClock()
        state = {"reopened": False, "filled": False, "open_calls": 0}

        def evaluate(_tab, code):
            if "const PATH = " in code:
                state["open_calls"] += 1
                return {"value": {"ok": True, "log": {}, "pending": True, "len": 0}}
            if code == x_engine.BOX:
                return {"value": {"found": state["reopened"],
                                  "len": 5 if state["filled"] else 0}}
            if code == x_engine.CLEAR_BOX:
                return {"value": {"ok": True, "len": 0}}
            if code == x_engine.CLICK_SEND:
                return {"value": {"ok": True, "len": 5, "clickedAt": 1}}
            if "const EXCLUDE" in code:
                return {"value": {"id": "888", "url": "https://x.com/o/status/888",
                                  "source": "article"}}
            if "const head = flat(" in code:
                return {"value": {"box": True, "len": 0, "articles": 1, "hit": 0,
                                  "toast": ""}}
            return {"value": {"hits": [], "len": 0}}

        def fill(_tab, _selector, _text):
            state["filled"] = True
            return {"success": True}

        def nav(_tab, _url):
            state["reopened"] = True
            return True

        with mock.patch.object(x_engine.time, "time", side_effect=clock.time), \
                mock.patch.object(x_engine.time, "sleep", side_effect=clock.sleep), \
                mock.patch.object(x_engine.wb, "ev", side_effect=evaluate), \
                mock.patch.object(x_engine.wb, "fill", side_effect=fill), \
                mock.patch.object(x_engine, "_full_nav", side_effect=nav) as full_nav:
            got = x_engine.reply_native(11, url, "hello")

        self.assertTrue(got["ok"], got)
        self.assertEqual(state["open_calls"], 2)
        full_nav.assert_called_once()
        self.assertIn("reopen", got["steps"])
        self.assertEqual(got["status"]["id"], "888")

    def test_original_binds_status_without_readback(self):
        send_code = x_engine._send_script("hello")

        def evaluate(_tab, code):
            if code == x_engine.OPEN_COMPOSER:
                return {"value": {"ok": True}}
            if code == x_engine.STATUS_IDS:
                return {"value": ["111"]}
            if "const EXCLUDE" in code:
                return {"value": {"id": "222", "url": "https://x.com/o/status/222",
                                  "source": "toast"}}
            if code == send_code:
                return {"value": {"ok": True, "cleared": True, "len": 5, "clickMs": 12}}
            return {"value": {"hits": [], "len": 0}}

        with mock.patch.object(x_engine.wb, "ev", side_effect=evaluate):
            got = x_engine.original(11, "hello")

        self.assertTrue(got["ok"])
        self.assertEqual(got["status"], {"id": "222", "url": "https://x.com/o/status/222",
                                         "source": "toast"})

    def test_reply_reports_status_after_send(self):
        url = "https://x.com/a/status/999"

        def evaluate(_tab, code):
            if "const PATH = " in code:
                return {"value": {"ok": True, "log": {}, "pending": False, "len": 0}}
            if code == x_engine.CLEAR_BOX:
                return {"value": {"ok": True, "len": 0}}
            if code == x_engine.BOX:
                return {"value": {"found": True, "len": 5}}
            if code == x_engine.CLICK_SEND:
                return {"value": {"ok": True, "len": 5, "clickedAt": 1}}
            if "const EXCLUDE" in code:
                return {"value": {"id": "777", "url": "https://x.com/o/status/777",
                                  "source": "article"}}
            if "const head = flat(" in code:
                return {"value": {"box": True, "len": 0, "articles": 1, "hit": 0,
                                  "toast": ""}}
            return {"value": {"hits": [], "len": 0}}

        with mock.patch.object(x_engine.wb, "ev", side_effect=evaluate), \
                mock.patch.object(x_engine.wb, "fill", return_value={"success": True}):
            got = x_engine.reply_native(11, url, "hello")

        self.assertTrue(got["ok"])
        self.assertEqual(got["status"]["id"], "777")
        self.assertEqual(got["status"]["source"], "article")


if __name__ == "__main__":
    unittest.main()
