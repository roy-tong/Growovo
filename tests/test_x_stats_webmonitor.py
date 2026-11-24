import tempfile
import unittest
from unittest import mock

from channels.x import read_stats


class XStatsWebMonitorTest(unittest.TestCase):
    def test_read_uses_webmonitor_managed_tab_without_debug_chrome(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.observe_identity.return_value = {"handle": "elliwoodtong"}
        fake.read_page.side_effect = [
            "Profile\n42 Followers\n",
            "Analytics\nImpressions 1,234\n",
        ]
        fake.screenshot.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("channels.x.read_stats.BrowserAttach", return_value=fake), \
                mock.patch("channels.x.read_stats.wb.nav"), \
                mock.patch("channels.x.read_stats.wb.time.sleep"):
            got = read_stats.read("elliwoodtong", tmp, data_dir=tmp, log=lambda *_: None)
        self.assertEqual(42, got["followers"])
        self.assertEqual(1234, got["impressions_yesterday"])
        self.assertEqual("webmonitor_observed", got["login_status"])
        fake.ensure_managed_tab.assert_called_once()


class HydrationWaitTest(unittest.TestCase):
    def test_read_waits_for_profile_content_instead_of_fixed_sleep(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.observe_identity.return_value = {"handle": "elliwoodtong"}
        fake.read_page.side_effect = [
            "要查看键盘快捷键\n主页\n",
            "Profile\n42 Followers\n" + "x" * 600,
            "Analytics\nImpressions 1,234\n" + "y" * 600,
        ]
        fake.screenshot.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("channels.x.read_stats.BrowserAttach", return_value=fake), \
                mock.patch("channels.x.read_stats.wb.nav"), \
                mock.patch("channels.x.read_stats.wb.time.sleep"):
            got = read_stats.read("elliwoodtong", tmp, data_dir=tmp, log=lambda *_: None)
        self.assertEqual(42, got["followers"])
        self.assertEqual(1234, got["impressions_yesterday"])
        self.assertEqual(3, fake.read_page.call_count)


class BeforeUnloadTest(unittest.TestCase):
    def test_standard_beforeunload_is_accepted_once(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.observe_identity.return_value = {"handle": "elliwoodtong"}
        fake.read_page.side_effect = ["42 Followers", "Impressions 1,234"]
        fake.screenshot.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("channels.x.read_stats.BrowserAttach", return_value=fake), \
                mock.patch("channels.x.read_stats.wb.nav",
                           side_effect=[RuntimeError("beforeunload"), None, None]), \
                mock.patch("channels.x.read_stats.wb.accept_dialog") as accept, \
                mock.patch("channels.x.read_stats.wb.time.sleep"):
            got = read_stats.read("elliwoodtong", tmp, data_dir=tmp, log=lambda *_: None)
        self.assertEqual(42, got["followers"])
        accept.assert_called_once_with(11)


if __name__ == "__main__":
    unittest.main()
