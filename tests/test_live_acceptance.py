import unittest
from unittest import mock

from core import live_acceptance


class LiveAcceptanceTest(unittest.TestCase):
    def test_oauth_file_configuration_reaches_the_real_client_boundary(self):
        gmail = mock.Mock()
        gmail.get_profile.return_value = {"emailAddress": "owner@example.com"}
        env = {"MKT_GMAIL_OAUTH_TOKEN_PATH": "/secure/token.json",
               "MKT_GMAIL_OAUTH_CLIENT_PATH": "/secure/client.json"}
        with mock.patch.dict("os.environ", env, clear=True), \
                mock.patch("core.gmail_api.GmailClient.from_env", return_value=gmail):
            got = live_acceptance.check_gmail()
        self.assertTrue(got["ok"], got)
        self.assertEqual([], got["missing"])
        self.assertTrue(got["profile_read"])

    def test_missing_configuration_is_explicit_and_does_not_call_network(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch("core.github_app.GitHubAppClient.from_env") as github, \
                mock.patch("core.gmail_api.GmailClient.from_env") as gmail:
            got = live_acceptance.preflight(repo="o/r", gmail=True)
        self.assertFalse(got["ok"])
        self.assertIn("MKT_GITHUB_APP_ID", got["components"]["github_app"]["missing"])
        self.assertIn("MKT_GMAIL_REFRESH_TOKEN", got["components"]["gmail"]["missing"])
        github.assert_not_called()
        gmail.assert_not_called()
        self.assertEqual(0, got["writes"])

    def test_x_preflight_uses_managed_browser_and_returns_agent_route(self):
        identity = {
            "enforced": True, "mode": "cdp_observed",
            "observed_platform_identity": "x:owner", "handle": "owner",
            "identity_proof_ref": "browser-attach:11@now",
            "browser_context": "webbridge-session:test", "observed_at": "now",
        }
        attach = mock.Mock(session="test")
        attach.ensure_managed_tab.return_value = 11
        attach.observe_identity.return_value = identity
        with mock.patch("core.browser_attach.BrowserAttach", return_value=attach), \
                mock.patch("core.platform_compliance.require_owner_attested_browser_write",
                           return_value={"route": "owner_attested_session", "window_end": "tomorrow"}), \
                mock.patch("core.cdp_identity.probe") as legacy:
            got = live_acceptance.preflight(x=True, x_action="publish", data_dir="data")
        self.assertTrue(got["ok"], got)
        self.assertEqual("browser-attach", got["components"]["x"]["transport"])
        self.assertEqual("owner", got["components"]["x"]["handle"])
        self.assertEqual("ensure_managed_tab", got["components"]["x"]["solution"]["steps"][0]["operation"])
        legacy.assert_not_called()
        self.assertEqual(0, got["writes"])

    def test_x_preflight_failure_returns_recovery_instead_of_login_guess(self):
        from core.browser_attach import BridgeError
        with mock.patch("core.browser_attach.BrowserAttach.ensure_managed_tab",
                        side_effect=BridgeError("tab_not_found", "missing")):
            got = live_acceptance.preflight(x=True, data_dir="data")
        self.assertFalse(got["ok"])
        self.assertEqual("browser-attach", got["components"]["x"]["recovery"]["route"])
        self.assertEqual("ensure_managed_tab",
                         got["components"]["x"]["recovery"]["steps"][0]["operation"])
        self.assertNotIn("verify_credentials", str(got))

    def test_configured_read_only_preflight_uses_real_client_boundaries(self):
        env = {name: "configured" for name in live_acceptance.GITHUB_APP_VARS}
        env["MKT_GMAIL_ACCESS_TOKEN"] = "configured"
        github = mock.Mock()
        github.get_repository.return_value = {"full_name": "o/r"}
        gmail = mock.Mock()
        gmail.get_profile.return_value = {"emailAddress": "owner@example.com"}
        with mock.patch.dict("os.environ", env, clear=True), \
                mock.patch("core.github_app.GitHubAppClient.from_env", return_value=github), \
                mock.patch("core.gmail_api.GmailClient.from_env", return_value=gmail):
            got = live_acceptance.preflight(repo="o/r", gmail=True)
        self.assertTrue(got["ok"])
        self.assertTrue(got["components"]["github_app"]["repository_access"])
        self.assertTrue(got["components"]["gmail"]["profile_read"])


if __name__ == "__main__":
    unittest.main()
