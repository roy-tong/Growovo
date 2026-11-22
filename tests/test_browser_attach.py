#!/usr/bin/env python3
"""browser-attach 传输：复用操作者已登录标签页、失败即停、身份必读。

全部用例替换 WebBridge 调用，不连接浏览器、不读登录态。
"""
import unittest
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import browser_attach
from core.bridge import wb


def tab(tab_id, url):
    return {"tabId": tab_id, "url": url, "title": "t"}


class SessionTest(unittest.TestCase):
    def tearDown(self):
        wb.set_session("growovo")

    def test_explicit_session_switches_bridge_session(self):
        attach = browser_attach.BrowserAttach("ovo-test")
        self.assertEqual(attach.session, "ovo-test")
        self.assertEqual(wb.SESSION, "ovo-test")

    def test_default_keeps_current_session(self):
        wb.set_session("keep-me")
        attach = browser_attach.BrowserAttach()
        self.assertEqual(attach.session, "keep-me")


class DaemonTest(unittest.TestCase):
    def test_transport_error_becomes_bridge_unavailable(self):
        attach = browser_attach.BrowserAttach()
        with mock.patch.object(wb, "call", side_effect=OSError("connection refused")):
            with self.assertRaises(browser_attach.BridgeError) as ctx:
                attach.require_daemon()
        self.assertEqual(ctx.exception.code, "bridge_unavailable")

    def test_daemon_error_payload_becomes_bridge_unavailable(self):
        attach = browser_attach.BrowserAttach()
        with mock.patch.object(wb, "call", return_value={"ok": False, "error": {"code": "x"}}):
            with self.assertRaises(browser_attach.BridgeError) as ctx:
                attach.require_daemon()
        self.assertEqual(ctx.exception.code, "bridge_unavailable")


class TabTest(unittest.TestCase):
    def setUp(self):
        self.attach = browser_attach.BrowserAttach()
        self.tabs = [
            tab(11, "https://x.com/home"),
            tab(12, "https://twitter.com/notifications/mentions"),
            tab(13, "https://github.com/notifications"),
            tab(14, "https://discord.com/channels/1/2"),
            tab(15, "https://example.com/"),
        ]

    def fake_call(self, tool, _timeout=180, **kwargs):
        if tool == "evaluate":
            tid = kwargs.get("tabId")
            url = next((t["url"] for t in self.tabs if t["tabId"] == tid), "")
            return {"ok": True, "data": {"value": url}}
        return {"ok": True, "data": {"tabs": list(self.tabs)}}

    def test_list_tabs_labels_sites(self):
        with mock.patch.object(wb, "call", side_effect=self.fake_call):
            rows = self.attach.list_tabs()
        labels = {row["tabId"]: row["site"] for row in rows}
        self.assertEqual(labels[11], "x")
        self.assertEqual(labels[12], "x")
        self.assertEqual(labels[13], "github")
        self.assertEqual(labels[14], "discord")
        self.assertEqual(labels[15], "")

    def test_attach_site_reuses_existing_tab_and_never_opens_new(self):
        with mock.patch.object(wb, "call", side_effect=self.fake_call), \
                mock.patch.object(wb, "nav") as nav:
            self.assertEqual(self.attach.attach_site("x"), 11)
            self.assertEqual(self.attach.attach_site("github"), 13)
            self.assertEqual(self.attach.attach_site("discord"), 14)
        nav.assert_not_called()

    def test_attach_site_without_tab_raises_tab_not_found(self):
        self.tabs = [tab(15, "https://example.com/")]
        with mock.patch.object(wb, "call", side_effect=self.fake_call), \
                mock.patch.object(wb, "nav") as nav:
            with self.assertRaises(browser_attach.BridgeError) as ctx:
                self.attach.attach_site("x")
        self.assertEqual(ctx.exception.code, "tab_not_found")
        nav.assert_not_called()

    def test_ensure_tab_reuses_existing_and_refuses_new_by_default(self):
        with mock.patch.object(wb, "call", side_effect=self.fake_call), \
                mock.patch.object(wb, "nav") as nav:
            self.assertEqual(self.attach.ensure_tab("github", "https://github.com/"), 13)
        nav.assert_not_called()
        self.tabs = [tab(15, "https://example.com/")]
        with mock.patch.object(wb, "call", side_effect=self.fake_call), \
                mock.patch.object(wb, "nav") as nav:
            with self.assertRaises(browser_attach.BridgeError) as ctx:
                self.attach.ensure_tab("github", "https://github.com/")
        self.assertEqual(ctx.exception.code, "tab_not_found")
        nav.assert_not_called()

    def test_ensure_tab_opens_only_with_explicit_allow_new(self):
        self.tabs = [tab(15, "https://example.com/")]

        def open_tab(*_args, **_kwargs):
            self.tabs.append(tab(21, "https://github.com/notifications"))

        with mock.patch.object(wb, "call", side_effect=self.fake_call), \
                mock.patch.object(wb, "nav", side_effect=open_tab) as nav:
            self.assertEqual(self.attach.ensure_tab(
                "github", "https://github.com/", allow_new=True), 21)
            nav.assert_called_once()
            _, kwargs = nav.call_args
            self.assertEqual(kwargs.get("new_tab"), True)

    def test_ensure_tab_times_out_when_allowed_new_tab_never_appears(self):
        self.tabs = [tab(15, "https://example.com/")]
        with mock.patch.object(wb, "call", side_effect=self.fake_call), \
                mock.patch.object(wb, "nav"), \
                mock.patch.object(wb.time, "sleep"):
            with self.assertRaises(browser_attach.BridgeError) as ctx:
                self.attach.ensure_tab("github", "https://github.com/", allow_new=True)
        self.assertEqual(ctx.exception.code, "tab_open_timeout")

    def test_attach_site_skips_dead_tab_with_stale_url(self):
        # 扩展列表可能仍按旧 URL 报告已崩溃的标签；探活读到的真实地址是错误页。
        self.tabs = [tab(61, "https://github.com/notifications"),
                     tab(62, "https://github.com/notifications")]
        real = {61: "chrome-error://chromewebdata/",
                62: "https://github.com/notifications"}

        def fake_call(tool, _timeout=180, **kwargs):
            if tool == "evaluate":
                return {"ok": True, "data": {"value": real.get(kwargs.get("tabId"), "")}}
            if tool == "list_browser_tabs":
                return {"ok": False, "error": {"code": "unknown_tool"}}
            return {"ok": True, "data": {"tabs": list(self.tabs)}}

        with mock.patch.object(wb, "call", side_effect=fake_call), \
                mock.patch.object(wb, "nav") as nav:
            self.assertEqual(self.attach.attach_site("github"), 62)
        nav.assert_not_called()

    def test_attach_site_prefers_visible_tab(self):
        states = {11: "hidden", 12: "visible"}

        def fake_js(tab_id, _code):
            return states.get(tab_id, "")

        with mock.patch.object(wb, "call", side_effect=self.fake_call), \
                mock.patch.object(wb, "js", side_effect=fake_js):
            self.assertEqual(self.attach.attach_site("x"), 11)
            self.assertEqual(self.attach.attach_site("x", prefer_visible=True), 12)


class BorrowTabTest(unittest.TestCase):
    """会话标签页为空时，借用用户已打开的同站页；不新建、不切前台。"""

    def setUp(self):
        self.attach = browser_attach.BrowserAttach()
        self.session_tabs = []
        self.browser_tabs = []
        self.reject = set()
        self.calls = []

    def fake_call(self, _tool, _timeout=180, **kwargs):
        self.calls.append((_tool, kwargs))
        if _tool == "list_tabs":
            return {"ok": True, "data": {"tabs": list(self.session_tabs)}}
        if _tool == "list_browser_tabs":
            return {"ok": True, "data": {"tabs": list(self.browser_tabs)}}
        if _tool == "attach_tab":
            tab_id = kwargs.get("tabId")
            if tab_id in self.reject:
                return {"ok": False, "error": {"code": "tab_not_found"}}
            for row in self.browser_tabs:
                if row["tabId"] == tab_id:
                    self.session_tabs.append(dict(row))
                    return {"ok": True}
            return {"ok": False, "error": {"code": "tab_not_found"}}
        return {"ok": True, "data": {}}

    def test_attach_site_borrows_user_tab_when_session_empty(self):
        self.browser_tabs = [tab(31, "https://x.com/notifications")]
        with mock.patch.object(wb, "call", side_effect=self.fake_call), \
                mock.patch.object(wb, "nav") as nav:
            self.assertEqual(self.attach.attach_site("x"), 31)
        nav.assert_not_called()
        tools = [name for name, _ in self.calls]
        self.assertIn("list_browser_tabs", tools)
        self.assertIn("attach_tab", tools)

    def test_borrow_prefers_non_active_user_tab(self):
        self.browser_tabs = [
            dict(tab(41, "https://x.com/home"), active=True),
            dict(tab(42, "https://x.com/notifications"), active=False),
        ]
        with mock.patch.object(wb, "call", side_effect=self.fake_call), \
                mock.patch.object(wb, "nav"):
            self.assertEqual(self.attach.attach_site("x"), 42)
        tried = [kw.get("tabId") for name, kw in self.calls if name == "attach_tab"]
        self.assertEqual(tried[:1], [42])

    def test_borrow_skips_tab_that_refuses_attach(self):
        self.browser_tabs = [
            tab(51, "https://x.com/home"),
            tab(52, "https://x.com/notifications"),
        ]
        self.reject = {51}
        with mock.patch.object(wb, "call", side_effect=self.fake_call), \
                mock.patch.object(wb, "nav"):
            self.assertEqual(self.attach.attach_site("x"), 52)
        tried = [kw.get("tabId") for name, kw in self.calls if name == "attach_tab"]
        self.assertEqual(tried, [51, 52])

    def test_borrow_ignores_unsupported_daemon(self):
        def fake_call(_tool, _timeout=180, **kwargs):
            if _tool == "list_tabs":
                return {"ok": True, "data": {"tabs": []}}
            return {"ok": False, "error": {"code": "unknown_tool"}}

        with mock.patch.object(wb, "call", side_effect=fake_call), \
                mock.patch.object(wb, "nav") as nav:
            with self.assertRaises(browser_attach.BridgeError) as ctx:
                self.attach.attach_site("discord")
        self.assertEqual(ctx.exception.code, "tab_not_found")
        nav.assert_not_called()

    def test_borrow_survives_transport_error_on_browser_scan(self):
        def fake_call(_tool, _timeout=180, **kwargs):
            if _tool == "list_tabs":
                return {"ok": True, "data": {"tabs": []}}
            raise OSError("connection refused")

        with mock.patch.object(wb, "call", side_effect=fake_call), \
                mock.patch.object(wb, "nav") as nav:
            with self.assertRaises(browser_attach.BridgeError) as ctx:
                self.attach.attach_site("github")
        self.assertEqual(ctx.exception.code, "tab_not_found")
        nav.assert_not_called()


class IdentityTest(unittest.TestCase):
    def setUp(self):
        self.attach = browser_attach.BrowserAttach()

    def test_x_identity_requires_handle(self):
        with mock.patch.object(wb, "js", return_value={"ok": True, "screen_name": "someone"}):
            got = self.attach.observe_identity("x", 11)
        self.assertTrue(got["enforced"])
        self.assertEqual(got["mode"], "cdp_observed")
        self.assertEqual(got["observed_platform_identity"], "x:someone")
        self.assertEqual(got["handle"], "someone")
        for key in ("identity_proof_ref", "browser_context", "observed_at"):
            self.assertTrue(got[key])
        self.assertFalse(got.get("platform_permission", False))

    def test_x_missing_screen_name_fails_closed(self):
        with mock.patch.object(wb, "js", return_value={"ok": True, "screen_name": ""}):
            with self.assertRaises(browser_attach.BridgeError) as ctx:
                self.attach.observe_identity("x", 11)
        self.assertEqual(ctx.exception.code, "identity_unavailable")

    def test_x_logged_out_fails_closed(self):
        with mock.patch.object(wb, "js", return_value={"ok": False, "screen_name": ""}):
            with self.assertRaises(browser_attach.BridgeError) as ctx:
                self.attach.observe_identity("x", 11)
        self.assertEqual(ctx.exception.code, "identity_unavailable")

    def test_github_identity(self):
        with mock.patch.object(wb, "js", return_value={"ok": True, "login": "roy-tong"}):
            got = self.attach.observe_identity("github", 13)
        self.assertEqual(got["observed_platform_identity"], "github:roy-tong")
        self.assertEqual(got["handle"], "roy-tong")

    def test_discord_identity(self):
        with mock.patch.object(wb, "js", return_value={"ok": True, "username": "roy"}):
            got = self.attach.observe_identity("discord", 14)
        self.assertEqual(got["observed_platform_identity"], "discord:roy")
        self.assertEqual(got["handle"], "roy")

    def test_unknown_site_is_unsupported(self):
        with self.assertRaises(browser_attach.BridgeError) as ctx:
            self.attach.observe_identity("linkedin", 1)
        self.assertEqual(ctx.exception.code, "identity_unsupported")


if __name__ == "__main__":
    unittest.main()
