import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import browser_write, cdp
from core.browser_attach import BridgeError, BrowserAttach
from core.managed_tabs import ManagedTabs
from channels.x.adapter import _legacy_cdp_vendor_env
from channels.x.vendor.session_page import automation_page


class ManagedTabsStoreTest(unittest.TestCase):
    def test_remember_get_and_forget_are_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tabs = ManagedTabs(tmp)
            tabs.remember("svc", "x", 11, "OVO service", "https://x.com/home")
            self.assertEqual(11, ManagedTabs(tmp).get("svc", "x")["tabId"])
            self.assertIsNone(ManagedTabs(tmp).get("other", "x"))
            tabs.forget("svc", "x", 11)
            self.assertIsNone(ManagedTabs(tmp).get("svc", "x"))


class EnsureManagedTabTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.attach = BrowserAttach("svc")

    def test_live_registered_tab_is_reused_without_rebinding(self):
        tabs = ManagedTabs(self.tmp.name)
        tabs.remember("svc", "x", 11, "OVO service", "https://x.com/home")
        with mock.patch.object(self.attach, "_tab_alive", return_value=True), \
                mock.patch.object(self.attach, "attach_site") as attach_site, \
                mock.patch.object(self.attach, "ensure_tab") as ensure_tab:
            got = self.attach.ensure_managed_tab(
                self.tmp.name, "x", "https://x.com/home", "OVO service")
        self.assertEqual(11, got)
        attach_site.assert_not_called()
        ensure_tab.assert_not_called()

    def test_stale_registration_is_replaced_by_existing_site_tab(self):
        ManagedTabs(self.tmp.name).remember("svc", "x", 11, "OVO service", "https://x.com/home")
        with mock.patch.object(self.attach, "_tab_alive", return_value=False), \
                mock.patch.object(self.attach, "attach_site", return_value=22), \
                mock.patch.object(self.attach, "ensure_tab") as ensure_tab:
            got = self.attach.ensure_managed_tab(
                self.tmp.name, "x", "https://x.com/home", "OVO service")
        self.assertEqual(22, got)
        self.assertEqual(22, ManagedTabs(self.tmp.name).get("svc", "x")["tabId"])
        ensure_tab.assert_not_called()

    def test_missing_site_tab_creates_one_managed_tab_and_registers_it(self):
        with mock.patch.object(
                self.attach, "attach_site",
                side_effect=BridgeError("tab_not_found", "missing")), \
                mock.patch.object(self.attach, "ensure_tab", return_value=33) as ensure_tab:
            got = self.attach.ensure_managed_tab(
                self.tmp.name, "x", "https://x.com/home", "OVO service")
        self.assertEqual(33, got)
        ensure_tab.assert_called_once_with(
            "x", "https://x.com/home", group_title="OVO service",
            prefer_visible=False, allow_new=True)
        self.assertEqual(33, ManagedTabs(self.tmp.name).get("svc", "x")["tabId"])


class NoPlaywrightNewPageTest(unittest.TestCase):
    def test_vendor_page_reuses_existing_or_fails_without_new_page(self):
        context = mock.Mock()
        context.pages = []
        with self.assertRaisesRegex(RuntimeError, "managed_tab_required"):
            automation_page(context)
        context.new_page.assert_not_called()

    def test_core_cdp_reuse_page_never_creates_page(self):
        context = mock.Mock()
        context.pages = []
        with self.assertRaisesRegex(RuntimeError, "managed_tab_required"):
            cdp._reuse_page(context)
        context.new_page.assert_not_called()

    def test_core_cdp_reuses_the_requested_site_not_an_x_page(self):
        x_page = mock.Mock()
        x_page.url = "https://x.com/home"
        reddit_page = mock.Mock()
        reddit_page.url = "https://old.reddit.com/r/agent/"
        context = mock.Mock()
        context.pages = [x_page, reddit_page]
        got = cdp._reuse_page(context, ("https://old.reddit.com/",))
        self.assertIs(reddit_page, got)
        context.new_page.assert_not_called()

    def test_releasing_a_managed_page_does_not_close_it(self):
        page = mock.Mock()
        cdp.release_page(page)
        page.close.assert_not_called()

    def test_connect_never_creates_a_webbridge_managed_tab(self):
        page = mock.Mock()
        page.url = "https://old.reddit.com/r/agent/comments/1"
        context = mock.Mock()
        context.pages = [page]
        browser = mock.Mock()
        browser.contexts = [context]
        with mock.patch("core.cdp._playwright") as playwright, \
                mock.patch("channels.x.vendor.session_page.connect_browser",
                           return_value=browser):
            got_browser, got_page = cdp.connect(
                site="reddit", data_dir="/tmp/ovo-managed-test",
                managed_url="https://old.reddit.com/r/agent/comments/1")
        self.assertIs(browser, got_browser)
        self.assertIs(page, got_page)

    def test_legacy_vendor_env_does_not_create_a_webbridge_tab(self):
        env = _legacy_cdp_vendor_env()
        self.assertEqual("https://x.com/home", env["MKT_MANAGED_TAB_URL"])
        self.assertNotIn("MKT_MANAGED_TAB_ID", env)


class BrowserWriteManagedTabTest(unittest.TestCase):
    def test_browser_write_uses_managed_tab(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.probe_visibility.return_value = "visible"
        fake.observe_identity.return_value = {"handle": "owner"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write, "require_owner_attested_browser_write",
                                  return_value={"route": "test"}), \
                mock.patch("core.throttle.Throttle.reserve", return_value=None), \
                mock.patch.object(browser_write.x_engine, "original",
                                  return_value={"ok": True, "status": {"id": "123"}}):
            got = browser_write.execute("x", "publish", tmp, text="hello", go=True)
        self.assertEqual("committed", got["ledger_result"])
        fake.ensure_managed_tab.assert_called_once_with(
            tmp, "x", "https://x.com/home", group_title="OVO engagement cycle",
            prefer_visible=True)
        fake.ensure_tab.assert_not_called()


if __name__ == "__main__":
    unittest.main()
