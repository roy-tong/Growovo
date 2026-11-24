import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import action_gateway


class XTransportResolutionTest(unittest.TestCase):
    def test_x_publish_and_reply_default_to_browser_attach(self):
        for action in ("publish", "reply"):
            got = action_gateway.resolve_transport({"channel": "x", "action": action})
            self.assertEqual("browser-attach", got["transport"])

    def test_legacy_cdp_is_explicit(self):
        got = action_gateway.resolve_transport(
            {"channel": "x", "action": "reply", "legacy_cdp": True})
        self.assertEqual("", got["transport"])
        got = action_gateway.resolve_transport(
            {"channel": "x", "action": "reply", "transport": "cdp"})
        self.assertEqual("", got["transport"])

    def test_routine_also_routes_browser_attach(self):
        got = action_gateway.resolve_transport(
            {"channel": "x", "action": "publish", "routine": True})
        self.assertEqual("browser-attach", got["transport"])
        self.assertFalse(got["routine"])

    def test_message_requires_legacy_cdp(self):
        import mkt
        args = SimpleNamespace(
            action="message", extra=[], file=None, thread=False, go=None,
            dry_run=True, adhoc=True, campaign="route-test", account="",
            handle="", record=False, legacy_cdp=False, cdp_url="",
            text="dm", tweet_url=None, actor="")
        self.assertEqual(1, mkt.cmd_x(args))

    def test_x_cdp_connect_requires_explicit_legacy(self):
        from core import cdp
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MKT_X_LEGACY_CDP", None)
            with self.assertRaisesRegex(RuntimeError, "x_legacy_cdp_required"):
                cdp.connect(site="x")


    def test_other_channels_are_unchanged(self):
        got = action_gateway.resolve_transport({"channel": "github", "action": "reply"})
        self.assertEqual("", got["transport"])

    def test_execute_passes_browser_attach_to_action_runner(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("core.growth_runtime.try_acquire_execution_lane",
                           return_value=True), \
                mock.patch("core.growth_runtime.release_execution_lane"), \
                mock.patch("core.action_runner.run_action",
                           return_value={"result": "dry-run"}) as runner:
            action_gateway.execute(
                channel="x", action="reply", data_dir=tmp,
                target="https://x.com/a/status/1", text="synthetic", go=False)
        self.assertEqual("browser-attach", runner.call_args.kwargs["transport"])


class XReadReadyTest(unittest.TestCase):
    def test_execute_passes_browser_attach_to_action_runner(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("core.growth_runtime.try_acquire_execution_lane",
                           return_value=True), \
                mock.patch("core.growth_runtime.release_execution_lane"), \
                mock.patch("core.action_runner.run_action",
                           return_value={"result": "dry-run"}) as runner:
            action_gateway.execute(
                channel="x", action="reply", data_dir=tmp,
                target="https://x.com/a/status/1", text="synthetic", go=False)
        self.assertEqual("browser-attach", runner.call_args.kwargs["transport"])

    def test_login_page_fails_loudly(self):
        from core import cdp
        page = SimpleNamespace(url="https://x.com/i/flow/login")
        with self.assertRaisesRegex(RuntimeError, "x_login_required"):
            cdp.assert_x_ready(page)

    def test_execute_lease_uses_channel_route_and_default_account(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("core.growth_runtime.try_acquire_execution_lane",
                           return_value=True) as acquire, \
                mock.patch("core.growth_runtime.release_execution_lane"), \
                mock.patch("core.action_runner.run_action",
                           return_value={"result": "dry-run"}):
            action_gateway.execute(
                channel="x", action="reply", data_dir=tmp,
                target="https://x.com/a/status/1", text="synthetic", go=False)
        self.assertEqual("default", acquire.call_args.args[1])
        self.assertEqual("x", acquire.call_args.args[2])

    def test_lease_uses_the_same_default_lane(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("core.growth_runtime.try_acquire_execution_lane",
                           return_value=True) as acquire, \
                mock.patch("core.growth_runtime.release_execution_lane"):
            with action_gateway.lease(tmp, route="x"):
                pass
        self.assertEqual("default", acquire.call_args.args[1])
        self.assertEqual("x", acquire.call_args.args[2])


class XReadReadyTest(unittest.TestCase):
    def test_login_page_fails_loudly(self):
        from core import cdp
        page = SimpleNamespace(url="https://x.com/i/flow/login")
        with self.assertRaisesRegex(RuntimeError, "x_login_required"):
            cdp.assert_x_ready(page)

    def test_normal_x_page_passes(self):
        from core import cdp
        cdp.assert_x_ready(SimpleNamespace(url="https://x.com/home"))


class CmdXRouteTest(unittest.TestCase):
    def _args(self, **overrides):
        values = dict(
            action="reply", extra=[], file=None, thread=False, go=None,
            dry_run=True, adhoc=True, campaign="route-test", account="",
            handle="", record=False, legacy_cdp=False, cdp_url="",
            text="路由测试正文", tweet_url="https://x.com/a/status/1",
            actor="",
        )
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_reply_defaults_to_gateway_browser_attach(self):
        import mkt
        old_data = mkt.DATA
        with tempfile.TemporaryDirectory() as tmp:
            mkt.DATA = Path(tmp)
            try:
                with mock.patch("core.action_gateway.execute",
                                return_value={"result": "dry-run"}) as gateway:
                    rc = mkt.cmd_x(self._args())
            finally:
                mkt.DATA = old_data
        self.assertEqual(0, rc)
        self.assertEqual("browser-attach", gateway.call_args.kwargs["transport"])

    def test_cli_identity_failure_prints_structured_managed_recovery(self):
        import contextlib
        import io
        import json
        import mkt
        from core.browser_write import ActionBlocked
        old_data = mkt.DATA
        with tempfile.TemporaryDirectory() as tmp:
            mkt.DATA = Path(tmp)
            try:
                with mock.patch("core.action_gateway.execute",
                                side_effect=ActionBlocked("identity_unknown", "verify_credentials HTTP 403")):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        rc = mkt.cmd_x(self._args(dry_run=False, go=True))
            finally:
                mkt.DATA = old_data
        self.assertEqual(1, rc)
        lines = output.getvalue().strip().splitlines()
        recovery = json.loads(lines[-1])
        self.assertEqual("browser-attach", recovery["route"])
        self.assertEqual("ensure_managed_tab", recovery["steps"][0]["operation"])
        self.assertFalse(recovery["retry_write"])

    def test_legacy_cdp_keeps_vendor_path_and_aligns_endpoint(self):
        import mkt
        old_data = mkt.DATA
        old_env = {key: os.environ.get(key) for key in
                   ("MKT_CDP_URL", "X_CDP_URL", "MKT_CDP_IDENTITY_URL")}
        with tempfile.TemporaryDirectory() as tmp:
            mkt.DATA = Path(tmp)
            captured = {}
            try:
                with mock.patch("channels.x.adapter.run", return_value=0) as vendor:
                    rc = mkt.cmd_x(self._args(
                        legacy_cdp=True, cdp_url="http://127.0.0.1:9333"))
                captured = {key: os.environ.get(key) for key in
                            ("MKT_CDP_URL", "X_CDP_URL", "MKT_CDP_IDENTITY_URL")}
            finally:
                mkt.DATA = old_data
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        self.assertEqual(0, rc)
        vendor.assert_called_once()
        self.assertEqual("http://127.0.0.1:9333", captured["MKT_CDP_URL"])
        self.assertEqual("http://127.0.0.1:9333", captured["X_CDP_URL"])
        self.assertEqual("http://127.0.0.1:9333",
                         captured["MKT_CDP_IDENTITY_URL"])


if __name__ == "__main__":
    unittest.main()
