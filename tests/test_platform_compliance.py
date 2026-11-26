"""平台规则只提示风险；账号授权与执行安全仍然是独立硬边界。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from channels.x import adapter
from core import platform_caps, platform_compliance, platform_strategy


class RiskNoticeTests(unittest.TestCase):
    def test_dry_run_and_reads_do_not_emit_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(platform_compliance.require_autonomous_write(
                "x", "reply", tmp, go=False, actual_transport="cdp"))
            self.assertIsNone(platform_compliance.require_autonomous_write(
                "x", "search", tmp, go=True, actual_transport="cdp"))

    def test_cdp_write_returns_risk_without_platform_authorization_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            for action in ("publish", "reply", "message", "engage"):
                with self.subTest(action=action):
                    got = platform_compliance.require_autonomous_write(
                        "x", action, tmp, go=True, actual_transport="cdp")
                    self.assertEqual("non_api_write_forbidden", got["risk_code"])
                    self.assertIn("不阻断", got["risk_warning"])
                    self.assertEqual("cdp", got["transport"])
                    self.assertIs(False, got["platform_permission"])
                    self.assertEqual("operator_risk", got["authorization_scope"])

    def test_user_visible_cdp_returns_the_same_non_blocking_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = platform_compliance.require_user_visible_cdp(
                "x", "publish", tmp, go=True, actual_transport="cdp", identity=None)
            self.assertEqual("non_api_write_forbidden", got["risk_code"])
            self.assertIn("不阻断", got["risk_warning"])

    def test_x_strategy_gate_is_warning_not_error(self):
        gates = [g for g in platform_strategy.gates("x", scope="execution")
                 if g["code"] == "non_api_write"]
        self.assertEqual(1, len(gates))
        self.assertEqual("warning", gates[0]["level"])


class AdapterRiskTests(unittest.TestCase):
    @staticmethod
    def observed_identity():
        return {
            "enforced": True, "mode": "cdp_observed",
            "observed_platform_identity": "uid-current-browser",
            "handle": "current_browser_account", "identity_proof_ref": "offline:identity-proof",
            "browser_context": "cdp:http://127.0.0.1:9223",
            "observed_at": "2026-11-18T12:00:00+00:00",
        }

    def test_user_visible_cdp_reaches_vendor_with_other_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = mock.Mock(returncode=0)
            with mock.patch.object(adapter.subprocess, "run", return_value=proc) as runner:
                adapter.run("reply", ["--tweet-url", "https://x.com/a/status/1",
                                      "--text", "synthetic only"],
                            go=True, adhoc=True, campaign="c", data_dir=tmp,
                            identity=self.observed_identity(),
                            execution_mode="user_visible_cdp",
                            log=lambda *_a, **_k: None)
        self.assertTrue(runner.called)

    def test_dry_run_still_reaches_vendor(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = mock.Mock(returncode=0)
            with mock.patch.object(adapter.subprocess, "run", return_value=proc) as runner:
                adapter.run("reply", ["--tweet-url", "https://x.com/a/status/1",
                                      "--text", "hi"],
                            go=False, campaign="c", data_dir=tmp,
                            log=lambda *_a, **_k: None)
        self.assertIn("--dry-run", runner.call_args.args[0])


class LinkedInBusinessExecutionTests(unittest.TestCase):
    def test_live_linkedin_send_does_not_require_env_unlock_or_terminal_input(self):
        from channels import linkedin
        page = mock.Mock()
        page.locator.return_value.first.click = mock.Mock()
        pipe = mock.Mock()
        pipe.run_one.side_effect = lambda _item, **kwargs: kwargs["send"]({}) or {"result": "committed"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("channels.linkedin.cdp.connect", return_value=(mock.Mock(), page)), \
                mock.patch("channels.linkedin.cdp.release_page"), \
                mock.patch("channels.linkedin.standard_pipeline", return_value=pipe), \
                mock.patch("channels.linkedin.Throttle.daily_allows", return_value=True), \
                mock.patch("channels.linkedin.Throttle.daily_used", return_value=0), \
                mock.patch("builtins.input", side_effect=AssertionError("不应再要求终端确认")):
            got = linkedin.message("https://www.linkedin.com/in/example", "hello",
                                   go=True, adhoc=True, data_dir=tmp, log=lambda *_: None)
        self.assertEqual("committed", got["result"])
        page.locator.return_value.first.click.assert_called_once_with()


class ActionRunnerLinkedInTests(unittest.TestCase):
    def test_action_runner_does_not_reintroduce_env_lock(self):
        from core import action_runner
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch("core.action_runner.precheck", return_value="plan"), \
                mock.patch("channels.linkedin.message", return_value={"result": "committed"}) as send:
            got = action_runner._dispatch_action_inner(
                channel="linkedin", action="message", data_dir=tmp, campaign="c",
                go=True, allow_unverified=True, target="https://www.linkedin.com/in/example",
                text="hello")
        self.assertEqual("committed", got["result"])
        self.assertTrue(send.call_args.kwargs["go"])


class PolicyMetadataTests(unittest.TestCase):
    def test_matrix_separates_business_capability_from_risk(self):
        row = platform_caps.classify("x")
        self.assertTrue(row["policy"]["autonomous_write"])
        self.assertEqual("business_capability_not_platform_permission",
                         row["policy"]["autonomous_write_meaning"])
        self.assertEqual("official_api", row["policy"]["preferred_route"])
        self.assertIs(False, row["policy"]["platform_permission"])
        self.assertTrue(row["policy"]["owner_risk_opt_in_required"])
        self.assertEqual("non_api_write_forbidden", row["policy"]["code"])
        self.assertTrue(row["policy"]["user_visible_cdp_write"])
        self.assertIn("不阻断", row["policy"]["risk_warning"])
        self.assertTrue(platform_caps.classify("email")["policy"]["autonomous_write"])

    def test_discord_reply_has_independent_unverified_maturity(self):
        from channels import REGISTRY
        self.assertEqual("PROVEN", REGISTRY["discord"]["status"])
        self.assertEqual("UNVERIFIED", REGISTRY["discord"]["action_status"]["reply"])


if __name__ == "__main__":
    unittest.main()
