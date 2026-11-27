#!/usr/bin/env python3
"""browser-attach 单条写的传输机制与平台拒绝边界。

后台标签页先尝试 CDP 焦点模拟（不激活标签页）；模拟不开才拒绝。
传输机制用例可替换授权门来隔离回读、回执与异常行为，不代表平台许可；
XPlatformPermissionTest 使用真实平台门禁，证明 X 不得触达写引擎或 committed 账本。
全部用例使用模拟浏览器，不连接真实账号、不发帖、不读登录态。
"""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import browser_write


class XPlatformPermissionTest(unittest.TestCase):
    def test_issue_prefixed_github_target_is_accepted(self):
        self.assertEqual(browser_write._parse_gh_target("issue:awiseguy88/AgentWiseAI#148"),
                         ("awiseguy88/AgentWiseAI", 148))

    def test_revoked_owner_attestation_cannot_reach_engine_or_committed_ledger(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.probe_visibility.return_value = "visible"
        fake.observe_identity.return_value = {
            "enforced": True, "mode": "cdp_observed",
            "observed_platform_identity": "x:1", "handle": "owner",
            "identity_proof_ref": "offline:identity", "browser_context": "offline:test",
            "observed_at": "2026-11-23T00:00:00+00:00",
        }
        now = dt.datetime.now().astimezone()
        auth = {
            "route": "owner_attested_session", "approved_by": "test",
            "approved_at": now.isoformat(), "window_start": now.isoformat(),
            "window_end": (now + dt.timedelta(hours=1)).isoformat(),
            "scopes": ["x:publish"], "stop_conditions": ["captcha"],
            "revoked": True,
        }
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch("core.platform_compliance.load_owner_attested", return_value=auth), \
                mock.patch.object(browser_write.x_engine, "original",
                                  return_value={"ok": True, "status": {"id": "1"}}) as engine, \
                mock.patch("core.throttle.Throttle.reserve") as quota:
            with self.assertRaises(browser_write.ActionBlocked) as ctx:
                browser_write.execute("x", "publish", Path(tmp), text="synthetic only",
                                      go=True, log=lambda *_: None)
            rows = browser_write._ledger(tmp).all()

        self.assertEqual(ctx.exception.code, "authorization_revoked")
        engine.assert_not_called()
        quota.assert_not_called()
        self.assertEqual(rows, [])


class HiddenXTabTest(unittest.TestCase):
    def test_hidden_x_tab_blocks_when_focus_emulation_unavailable(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.probe_visibility.return_value = "hidden"
        fake.focus_emulation.return_value = {"ok": False, "error": "cdp refused"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write,
                                  "require_owner_attested_browser_write") as auth:
            with self.assertRaises(browser_write.ActionBlocked) as ctx:
                browser_write.execute("x", "publish", Path(tmp), text="hello", go=True)

        self.assertEqual(ctx.exception.code, "tab_hidden")
        self.assertTrue(fake.ensure_managed_tab.call_args.kwargs.get("prefer_visible"))
        fake.observe_identity.assert_not_called()
        auth.assert_not_called()

    def test_hidden_tab_recovered_by_focus_emulation(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.probe_visibility.side_effect = ["hidden", "visible"]
        fake.focus_emulation.return_value = {"ok": True}
        fake.observe_identity.return_value = {"handle": "owner"}
        engine = {"ok": True, "status": {"id": "123", "source": "toast"}}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write, "require_owner_attested_browser_write",
                                  return_value={"route": "test"}), \
                mock.patch("core.throttle.Throttle.reserve", return_value=None), \
                mock.patch.object(browser_write.x_engine, "reply_native",
                                  return_value=engine):
            got = browser_write.execute("x", "reply", Path(tmp), text="hello",
                                        target="https://x.com/a/status/1", go=True,
                                        log=lambda *_: None)
            rows = browser_write._ledger(tmp).all()

        self.assertEqual(got["ledger_result"], "committed")
        self.assertEqual(rows[-1]["evidence"], "123")
        self.assertEqual(got["receipt"],
                         {"id": "123", "url": "https://x.com/owner/status/123"})
        self.assertEqual([c.args for c in fake.focus_emulation.call_args_list],
                         [(11, True), (11, False)])
        self.assertTrue(got["focus_emulation"]["used"])
        self.assertEqual(got["focus_emulation"]["before"], "hidden")

    def test_non_x_channels_do_not_probe_visibility(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 14
        fake.observe_identity.side_effect = RuntimeError("stop after gate order")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake):
            with self.assertRaises(RuntimeError):
                browser_write.execute("discord", "publish", Path(tmp),
                                      text="hello", target="1573250833871536161", go=True)

        fake.probe_visibility.assert_not_called()
        fake.focus_emulation.assert_not_called()


class AttemptedSendReadbackTest(unittest.TestCase):
    """发送控件已触发而回读失败时，必须进入未知结果而非可重试失败。"""

    def _execute(self, channel, engine):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.probe_visibility.return_value = "visible"
        fake.observe_identity.return_value = {"handle": "owner"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write, "require_owner_attested_browser_write",
                                  return_value={"route": "test"}), \
                mock.patch("core.throttle.Throttle.reserve", return_value=None):
            if channel == "x":
                with mock.patch.object(browser_write.x_engine, "reply_native",
                                       return_value=engine):
                    got = browser_write.execute(
                        "x", "reply", Path(tmp), text="hello",
                        target="https://x.com/a/status/1", go=True, log=lambda *_: None)
            else:
                with mock.patch.object(browser_write.discord_engine, "post",
                                       return_value=engine):
                    got = browser_write.execute(
                        "discord", "publish", Path(tmp), text="hello",
                        target="1573250833871536161", go=True, log=lambda *_: None)
            rows = browser_write._ledger(tmp).all()
        return got, rows

    def test_x_clicked_send_then_confirmation_failed_is_indeterminate(self):
        engine = {"ok": False, "err": "composer still holds text",
                  "send": {"ok": True, "clickedAt": 123}}
        got, rows = self._execute("x", engine)
        self.assertEqual(got["ledger_result"], "indeterminate")
        self.assertEqual(rows[-1]["result"], "indeterminate")

    def test_discord_entered_then_readback_failed_is_indeterminate(self):
        engine = {"ok": False, "err": "no new message row after Enter",
                  "enter": {"ok": True}}
        got, rows = self._execute("discord", engine)
        self.assertEqual(got["ledger_result"], "indeterminate")
        self.assertEqual(rows[-1]["result"], "indeterminate")

    def test_x_send_control_rejection_remains_failed(self):
        engine = {"ok": False, "err": "send button disabled",
                  "send": {"ok": False, "err": "send button disabled"}}
        got, rows = self._execute("x", engine)
        self.assertEqual(got["ledger_result"], "failed")
        self.assertEqual(rows[-1]["result"], "failed")


class AccountQuotaProfileTest(unittest.TestCase):
    def test_throttle_uses_the_observed_x_account_handle(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.probe_visibility.return_value = "visible"
        fake.observe_identity.return_value = {"handle": "owner"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write, "require_owner_attested_browser_write",
                                  return_value={"route": "test"}), \
                mock.patch("core.throttle.Throttle") as throttle_class, \
                mock.patch.object(browser_write.x_engine, "original",
                                  return_value={"ok": True, "status": {"id": "456"}}):
            browser_write.execute("x", "publish", Path(tmp), text="hello world",
                                  go=True, log=lambda *_: None)

        throttle_class.assert_called_once_with(Path(tmp) / "state", account="owner", strict=True)


class StatusBindingTest(unittest.TestCase):
    """X 发送成功即绑定 status id；只有页面给不出来时才扫 profile。"""

    def _run_publish(self, tmp, engine, readback=None):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.probe_visibility.return_value = "visible"
        fake.observe_identity.return_value = {"handle": "owner"}
        with mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write, "require_owner_attested_browser_write",
                                  return_value={"route": "test"}), \
                mock.patch("core.throttle.Throttle.reserve", return_value=None), \
                mock.patch.object(browser_write.x_engine, "original",
                                  return_value=engine), \
                mock.patch.object(browser_write.x_engine, "readback",
                                  side_effect=readback) as rb:
            got = browser_write.execute("x", "publish", Path(tmp), text="hello world",
                                        go=True, log=lambda *_: None)
            rows = browser_write._ledger(tmp).all()
        return got, rows, rb

    def test_publish_binds_status_id_without_profile_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            got, rows, rb = self._run_publish(
                tmp, {"ok": True, "status": {"id": "456", "source": "location"}})

        rb.assert_not_called()
        self.assertEqual(got["ledger_result"], "committed")
        self.assertEqual(got["receipt"],
                         {"id": "456", "url": "https://x.com/owner/status/456"})
        self.assertEqual(rows[-1]["evidence"], "456")
        self.assertTrue(rows[-1]["content_key"])
        self.assertEqual(got["status_binding"], "location")

    def test_publish_falls_back_to_readback_when_engine_has_no_status(self):
        def readback(_tab, _handle, manifest, **_kw):
            return {item["key"]: "789" for item in manifest}

        with tempfile.TemporaryDirectory() as tmp:
            got, rows, rb = self._run_publish(tmp, {"ok": True}, readback=readback)

        rb.assert_called_once()
        self.assertEqual(got["ledger_result"], "committed")
        self.assertEqual(got["receipt"],
                         {"id": "789", "url": "https://x.com/owner/status/789"})
        self.assertEqual(rows[-1]["evidence"], "789")
        self.assertEqual(got["status_binding"], "readback")


class ChannelTargetFailureTest(unittest.TestCase):
    """渠道内去重、422 目标隔离与 X 渲染失败的单目标隔离。"""

    def test_protected_account_is_skipped_and_persistent(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.probe_visibility.return_value = "visible"
        fake.observe_identity.return_value = {"handle": "owner"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write, "require_owner_attested_browser_write",
                                  return_value={"route": "test"}), \
                mock.patch("core.throttle.Throttle.reserve", return_value=None), \
                mock.patch.object(browser_write.x_engine, "reply_native",
                                  return_value={"ok": False, "err": "protected_account"}):
            got = browser_write.execute(
                "x", "reply", Path(tmp), text="hello",
                target="https://x.com/Toscane/status/123", go=True, log=lambda *_: None)
            self.assertTrue(got["ok"])
            self.assertTrue(got["skipped"])
            self.assertEqual("protected_account", got["reason"])
            from core import protected_content
            self.assertTrue(protected_content.is_target_suppressed(
                Path(tmp), "https://x.com/Toscane/status/456"))

    def test_discord_same_channel_three_texts_all_execute(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.observe_identity.return_value = {"handle": "owner"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write, "require_owner_attested_browser_write",
                                  return_value={"route": "test"}), \
                mock.patch("core.throttle.Throttle.reserve", return_value=None), \
                mock.patch.object(browser_write.discord_engine, "post",
                                  return_value={"ok": True, "serverId": "911", "author": "owner"}):
            results = [browser_write.execute(
                "discord", "publish", Path(tmp), text=text,
                target="1573250833871536161", go=True, log=lambda *_: None)
                for text in ("a", "b", "c")]
            rows = browser_write._ledger(tmp).all()

        self.assertEqual([r["ledger_result"] for r in results], ["committed"] * 3)
        self.assertEqual([r["result"] for r in rows], ["committed"] * 3)

    def test_discord_repeated_text_is_skipped_as_duplicate_content(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.observe_identity.return_value = {"handle": "owner"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write, "require_owner_attested_browser_write",
                                  return_value={"route": "test"}), \
                mock.patch("core.throttle.Throttle.reserve", return_value=None), \
                mock.patch.object(browser_write.discord_engine, "post",
                                  return_value={"ok": True, "serverId": "911", "author": "owner"}):
            first = browser_write.execute(
                "discord", "publish", Path(tmp), text="same",
                target="1573250833871536161", go=True, log=lambda *_: None)
            second = browser_write.execute(
                "discord", "publish", Path(tmp), text="same",
                target="1573250833871536161", go=True, log=lambda *_: None)
            rows = browser_write._ledger(tmp).all()

        self.assertEqual(first["ledger_result"], "committed")
        self.assertTrue(second["skipped"])
        self.assertEqual(second["reason"], "already_done_content")
        self.assertEqual(len(rows), 1)

    def test_github_403_on_locked_issue_isolates_target(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.observe_identity.return_value = {"handle": "owner"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write, "require_owner_attested_browser_write",
                                  return_value={"route": "test"}), \
                mock.patch("core.throttle.Throttle.reserve", return_value=None), \
                mock.patch.object(browser_write.gh_post, "post",
                                  return_value={"ok": False, "err": "403 Forbidden"}), \
                mock.patch.object(browser_write.gh_post, "issue",
                                  return_value={"ok": True, "locked": True, "state": "open"}):
            got = browser_write.execute(
                "github", "reply", Path(tmp), text="body", target="a/b#1",
                go=True, log=lambda *_: None)
            rows = browser_write._ledger(tmp).all()

        self.assertEqual(got["ledger_result"], "target_rejected")
        self.assertTrue(got["target_isolated"])
        self.assertEqual(rows[-1]["result"], "target_rejected")

    def test_github_403_on_open_issue_stops_channel(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.observe_identity.return_value = {"handle": "owner"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write, "require_owner_attested_browser_write",
                                  return_value={"route": "test"}), \
                mock.patch("core.throttle.Throttle.reserve", return_value=None), \
                mock.patch.object(browser_write.gh_post, "post",
                                  return_value={"ok": False, "err": "403 Forbidden"}), \
                mock.patch.object(browser_write.gh_post, "issue",
                                  return_value={"ok": True, "locked": False, "state": "open"}):
            with self.assertRaises(browser_write.StopChannel):
                browser_write.execute(
                    "github", "reply", Path(tmp), text="body", target="a/b#2",
                    go=True, log=lambda *_: None)

    def test_github_403_when_issue_state_unknown_stops_channel(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.observe_identity.return_value = {"handle": "owner"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write, "require_owner_attested_browser_write",
                                  return_value={"route": "test"}), \
                mock.patch("core.throttle.Throttle.reserve", return_value=None), \
                mock.patch.object(browser_write.gh_post, "post",
                                  return_value={"ok": False, "err": "403 Forbidden"}), \
                mock.patch.object(browser_write.gh_post, "issue",
                                  return_value={"ok": False, "err": "timeout"}):
            with self.assertRaises(browser_write.StopChannel):
                browser_write.execute(
                    "github", "reply", Path(tmp), text="body", target="a/b#3",
                    go=True, log=lambda *_: None)

    def test_single_github_422_isolates_target_without_stopping_channel(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.observe_identity.return_value = {"handle": "owner"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write, "require_owner_attested_browser_write",
                                  return_value={"route": "test"}), \
                mock.patch("core.throttle.Throttle.reserve", return_value=None), \
                mock.patch.object(browser_write.gh_post, "post",
                                  return_value={"ok": False,
                                                "err": "422 Unprocessable Entity"}):
            got = browser_write.execute(
                "github", "reply", Path(tmp), text="body", target="a/b#1",
                go=True, log=lambda *_: None)
            rows = browser_write._ledger(tmp).all()

        self.assertEqual(got["ledger_result"], "target_rejected")
        self.assertTrue(got["target_isolated"])
        self.assertEqual(rows[-1]["result"], "target_rejected")

    def test_three_distinct_github_targets_422_stop_the_channel(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.observe_identity.return_value = {"handle": "owner"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write, "require_owner_attested_browser_write",
                                  return_value={"route": "test"}), \
                mock.patch("core.throttle.Throttle.reserve", return_value=None), \
                mock.patch.object(browser_write.gh_post, "post",
                                  return_value={"ok": False,
                                                "err": "422 Unprocessable Entity"}):
            for target in ("a/b#1", "a/b#2"):
                got = browser_write.execute(
                    "github", "reply", Path(tmp), text="body", target=target,
                    go=True, log=lambda *_: None)
                self.assertEqual(got["ledger_result"], "target_rejected")
            with self.assertRaises(browser_write.StopChannel):
                browser_write.execute(
                    "github", "reply", Path(tmp), text="body", target="a/b#3",
                    go=True, log=lambda *_: None)

    def test_x_unrendered_target_is_isolated_not_retried(self):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.probe_visibility.return_value = "visible"
        fake.observe_identity.return_value = {"handle": "owner"}
        target = "https://x.com/a/status/1"
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write, "require_owner_attested_browser_write",
                                  return_value={"route": "test"}), \
                mock.patch("core.throttle.Throttle.reserve", return_value=None), \
                mock.patch.object(browser_write.x_engine, "reply_native",
                                  return_value={"ok": False,
                                                "err": "article not rendered"}):
            first = browser_write.execute("x", "reply", Path(tmp), text="hello",
                                          target=target, go=True, log=lambda *_: None)
            second = browser_write.execute("x", "reply", Path(tmp), text="hello again",
                                           target=target, go=True, log=lambda *_: None)
            rows = browser_write._ledger(tmp).all()

        self.assertEqual(first["ledger_result"], "unavailable_or_unrenderable")
        self.assertTrue(first["gone"])
        self.assertTrue(second["skipped"])
        self.assertEqual(second["reason"], "target_isolated")
        self.assertEqual(len(rows), 1)


class DiscordReplyRouteTest(unittest.TestCase):
    """Discord 原生回复：target 必须编码 channel_id:message_id，回执带 reply_to。"""

    def _run(self, target, engine=None):
        fake = mock.Mock()
        fake.ensure_managed_tab.return_value = 11
        fake.observe_identity.return_value = {"handle": "owner"}
        engine = engine or {"ok": True, "serverId": "9001", "author": "owner"}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(browser_write, "BrowserAttach", return_value=fake), \
                mock.patch.object(browser_write, "require_owner_attested_browser_write",
                                  return_value={"route": "test"}), \
                mock.patch("core.throttle.Throttle.reserve", return_value=None), \
                mock.patch.object(browser_write.discord_engine, "reply",
                                  return_value=engine) as reply:
            got = browser_write.execute("discord", "reply", Path(tmp), text="answer",
                                        target=target, go=True, log=lambda *_: None)
            rows = browser_write._ledger(tmp).all()
        return got, rows, reply

    def test_reply_splits_channel_and_message_and_records_receipt(self):
        got, rows, reply = self._run("111:222")
        reply.assert_called_once()
        self.assertEqual(reply.call_args.args, (11, "111", "222", "answer"))
        self.assertEqual(got["receipt"], {"id": "9001", "reply_to": "222"})
        self.assertEqual(got["ledger_result"], "committed")
        self.assertEqual(rows[-1]["target"], "111:222")

    def test_target_without_message_id_is_rejected_before_typing(self):
        for target in ("111", "a:b", ":222", "111:"):
            with self.assertRaises(browser_write.ActionBlocked) as ctx:
                self._run(target)
            self.assertEqual(ctx.exception.code, "target_invalid")

    def test_non_numeric_server_id_does_not_forge_a_receipt(self):
        got, _rows, _reply = self._run("111:222",
                                       engine={"ok": True, "serverId": "local-temp",
                                               "author": "owner"})
        self.assertTrue(got["ok"])
        self.assertNotIn("receipt", got)

    def test_readback_author_mismatch_is_indeterminate_not_committed(self):
        got, rows, _reply = self._run("111:222", engine={
            "ok": True, "serverId": "9001", "author": "someone-else",
            "enter": {"ok": True},
        })
        self.assertFalse(got["ok"])
        self.assertEqual(got["ledger_result"], "indeterminate")
        self.assertEqual(rows[-1]["result"], "indeterminate")
        self.assertNotIn("receipt", got)
        self.assertEqual(got["error"], "readback_identity_mismatch")


if __name__ == "__main__":
    unittest.main()
