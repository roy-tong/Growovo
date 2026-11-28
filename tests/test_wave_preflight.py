#!/usr/bin/env python3
"""波次前预检：只读判断各平台是否可用，不新建标签页、不发送。"""
import unittest

from core import wave_preflight
from core.browser_attach import BridgeError

POLICY = {"platforms": {"x": {"publish": {}}, "github": {"reply": {}},
                        "discord": {"publish": {}}}}


class FakeAttach:
    def __init__(self, *, missing=(), urls=None, identities=None, errors=None, tabs=None):
        self.missing = set(missing)
        self.urls = urls or {}
        self.identities = identities or {}
        self.errors = errors or {}
        self.tabs = tabs
        self.identity_calls = []

    def alive_site_tabs(self, site):
        if self.tabs is not None:
            return self.tabs.get(site, [])
        return [{"x": 1, "github": 2, "discord": 3}[site]]

    def attach_site(self, site):
        if site in self.missing:
            raise BridgeError("tab_not_found", "没有 %s 标签页" % site)
        return {"x": 1, "github": 2, "discord": 3}[site]

    def eval(self, tab, code):
        for site, url in self.urls.items():
            if {"x": 1, "github": 2, "discord": 3}[site] == tab:
                return url
        return "https://example.com/home"

    def observe_identity(self, site, tab):
        self.identity_calls.append(site)
        if (site, tab) in self.errors:
            raise self.errors[(site, tab)]
        if site in self.errors:
            raise self.errors[site]
        return {"handle": self.identities.get(site, "%s_user" % site)}


class PreflightTest(unittest.TestCase):
    def check(self, attach, policy=POLICY):
        return wave_preflight.check(policy, attach=attach, log=lambda *_: None)

    def test_all_logged_in(self):
        got = self.check(FakeAttach())
        self.assertTrue(got["ok"])
        self.assertEqual(set(got["channels"]), {"x", "github", "discord"})
        self.assertTrue(all(row["ok"] for row in got["channels"].values()))
        self.assertEqual(got["channels"]["x"]["handle"], "x_user")

    def test_login_redirect_is_login_required_without_identity_probe(self):
        attach = FakeAttach(urls={"discord": "https://discord.com/login?redirect_to=%2Fchannels%2F%40me"})
        got = self.check(attach)
        row = got["channels"]["discord"]
        self.assertFalse(row["ok"])
        self.assertEqual(row["reason"], "login_required")
        self.assertNotIn("discord", attach.identity_calls)
        self.assertTrue(got["channels"]["x"]["ok"])

    def test_missing_tab_is_tab_not_found(self):
        got = self.check(FakeAttach(missing={"github"}))
        self.assertEqual(got["channels"]["github"]["reason"], "tab_not_found")
        self.assertFalse(got["ok"])

    def test_unreadable_identity_is_login_required(self):
        attach = FakeAttach(errors={"x": BridgeError("identity_unavailable", "账号菜单不可读")})
        got = self.check(attach)
        self.assertEqual(got["channels"]["x"]["reason"], "login_required")

    def test_other_identity_error_keeps_code(self):
        attach = FakeAttach(errors={"github": BridgeError("identity_unsupported", "no js")})
        got = self.check(attach)
        self.assertEqual(got["channels"]["github"]["reason"], "identity_unsupported")

    def test_prefers_logged_in_tab_over_login_page(self):
        attach = FakeAttach(
            tabs={"discord": [3, 4]},
            urls={"discord": "https://discord.com/login?redirect_to=%2Fchannels%2F%40me",
                  4: "https://discord.com/channels/@me"})
        got = self.check(attach)
        self.assertTrue(got["channels"]["discord"]["ok"])
        self.assertEqual(got["channels"]["discord"]["tab"], 4)

    def test_login_page_only_returns_login_required(self):
        attach = FakeAttach(
            tabs={"discord": [3]},
            urls={"discord": "https://discord.com/login?redirect_to=%2Fchannels%2F%40me"})
        got = self.check(attach)
        self.assertFalse(got["channels"]["discord"]["ok"])
        self.assertEqual(got["channels"]["discord"]["reason"], "login_required")

    def test_login_page_before_missing_identity_tries_next_tab(self):
        attach = FakeAttach(
            tabs={"github": [2, 4]},
            urls={"github": "https://github.com/login",
                  4: "https://github.com/notifications"},
            errors={("github", 2): BridgeError("identity_unavailable", "账号菜单不可读")})
        got = self.check(attach)
        self.assertTrue(got["channels"]["github"]["ok"])
        self.assertEqual(got["channels"]["github"]["tab"], 4)


    def test_platform_absent_from_policy_is_not_probed(self):
        attach = FakeAttach()
        got = self.check(attach, policy={"platforms": {"x": {"publish": {}}}})
        self.assertEqual(set(got["channels"]), {"x"})


if __name__ == "__main__":
    unittest.main()
