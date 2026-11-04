"""Authorized account lifecycle and credential-free proxy references."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import accounts, network

T0 = "2026-11-04T08:00:00+00:00"


def make_registry():
    registry = accounts.AccountRegistry()
    account = registry.register(
        "reddit", "authorized-owner", accounts.REAL_AUTHORIZED,
        account_id="acc-1", owner="Example Team",
        ownership_evidence=[{"url": "https://example.com/ownership", "checked_by": "reviewer"}],
        authorization_evidence=["authorization-record-1"], at=T0,
    )
    return registry, account


class AccountTests(unittest.TestCase):
    def test_identity_types_require_ownership_and_real_authorization(self):
        with self.assertRaises(ValueError):
            accounts.AccountRegistry().register(
                "reddit", "owner", accounts.REAL_AUTHORIZED,
                owner="Team", authorization_evidence=["auth"], ownership_evidence=[])
        with self.assertRaises(ValueError):
            accounts.AccountRegistry().register(
                "reddit", "owner", accounts.REAL_AUTHORIZED,
                owner="Team", ownership_evidence=["owner-proof"])
        registry = accounts.AccountRegistry()
        pseudonym = registry.register(
            "reddit", "transparent-alias", accounts.PSEUDONYMOUS,
            account_id="pseudo-1", ownership_evidence=["operator-approval"],
            disclosure="由 Example Team 运营", at=T0)
        self.assertEqual(pseudonym["identity_type"], accounts.PSEUDONYMOUS)
        brand = accounts.AccountRegistry().register(
            "reddit", "example-brand", accounts.BRAND, owner="Example Ltd",
            ownership_evidence=["domain-control"], authorization_evidence=["brand-authorization"],
            at=T0)
        self.assertEqual(brand["identity_type"], accounts.BRAND)

    def test_additional_account_needs_platform_multi_account_permission(self):
        registry, _ = make_registry()
        base = dict(platform="reddit", handle="second", identity_type=accounts.PSEUDONYMOUS,
                    ownership_evidence=["operator-approval"], at=T0)
        with self.assertRaises(ValueError):
            registry.register(**base)
        with self.assertRaises(ValueError):
            registry.register(**base, multi_account_allowed=True)
        second = registry.register(**base, account_id="acc-2", multi_account_allowed=True,
                                   multi_account_evidence=["https://reddit.com/rules/multi-account"])
        self.assertTrue(second["multi_account_allowed"])
        self.assertTrue(second["multi_account_evidence"])

    def test_lifecycle_cannot_skip_warming_or_eligibility_review(self):
        registry, account = make_registry()
        with self.assertRaises(ValueError):
            registry.transition(account["id"], "eligible", evidence=["review"], reviewer="human", at=T0)
        registry.transition(account["id"], "warming", at=T0)
        with self.assertRaises(ValueError):
            registry.transition(account["id"], "eligible", at=T0)
        eligible = registry.transition(account["id"], "eligible", evidence=["warming-review-1"],
                                       reviewer="human-reviewer", at=T0)
        self.assertEqual(eligible["status"], "eligible")
        self.assertTrue(registry.can_operate(account["id"], now=T0))
        with self.assertRaises(ValueError):
            registry.transition(account["id"], "paused", at=T0)
        registry.transition(account["id"], "retired", reason="项目结束", at=T0)
        self.assertFalse(registry.can_operate(account["id"], now=T0))
        with self.assertRaises(ValueError):
            registry.transition(account["id"], "warming", at=T0)

    def test_health_limit_does_not_silently_recover(self):
        registry, account = make_registry()
        registry.transition(account["id"], "warming", at=T0)
        registry.transition(account["id"], "eligible", evidence=["review"], reviewer="human", at=T0)
        limited = registry.record_health(account["id"], 35, evidence=["platform-warning"], at=T0)
        self.assertEqual(limited["status"], "limited")
        self.assertFalse(registry.can_operate(account["id"], now=T0))
        recovered = registry.record_health(account["id"], 85, evidence=["healthy-observation"], at=T0)
        self.assertEqual(recovered["status"], "limited")
        registry.transition(account["id"], "eligible", evidence=["human-reinstatement"],
                            reviewer="human", at=T0)
        self.assertTrue(registry.can_operate(account["id"], now=T0))

    def test_cooldown_is_time_bounded_and_blocks_eligibility(self):
        registry, account = make_registry()
        registry.transition(account["id"], "warming", at=T0)
        registry.start_cooldown(account["id"], seconds=60, at=T0)
        with self.assertRaises(ValueError):
            registry.transition(account["id"], "eligible", evidence=["review"], reviewer="human",
                                at="2026-11-04T08:00:30+00:00")
        registry.transition(account["id"], "eligible", evidence=["review"], reviewer="human",
                            at="2026-11-04T08:01:01+00:00")
        self.assertFalse(registry.can_operate(account["id"], now="2026-11-04T08:00:30+00:00"))
        self.assertTrue(registry.can_operate(account["id"], now="2026-11-04T08:01:01+00:00"))
        with self.assertRaises(ValueError):
            registry.start_cooldown(account["id"], seconds=0, at=T0)

    def test_human_takeover_pauses_and_resolution_does_not_auto_restore(self):
        registry, account = make_registry()
        registry.transition(account["id"], "warming", at=T0)
        registry.transition(account["id"], "eligible", evidence=["review"], reviewer="human", at=T0)
        paused = registry.request_human_takeover(account["id"], "平台警告，需要人看", at=T0)
        self.assertEqual(paused["status"], "paused")
        self.assertTrue(paused["human_takeover"]["required"])
        resolved = registry.resolve_human_takeover(account["id"], reviewer="owner",
                                                    note="已人工阅读警告并处理", at=T0)
        self.assertEqual(resolved["status"], "paused")
        self.assertFalse(registry.can_operate(account["id"], now=T0))
        registry.transition(account["id"], "eligible", evidence=["post-incident-review"],
                            reviewer="owner", at=T0)
        self.assertTrue(registry.can_operate(account["id"], now=T0))

    def test_takeover_from_candidate_cannot_skip_warming(self):
        registry, account = make_registry()
        registry.request_human_takeover(account["id"], "登记信息需人工核对", at=T0)
        registry.resolve_human_takeover(account["id"], reviewer="owner", note="归属已确认", at=T0)
        with self.assertRaises(ValueError):
            registry.transition(account["id"], "eligible", evidence=["review"], reviewer="owner", at=T0)
        registry.transition(account["id"], "warming", at=T0)
        registry.transition(account["id"], "eligible", evidence=["warming-review"],
                            reviewer="owner", at=T0)
        self.assertTrue(registry.can_operate(account["id"], now=T0))

    def test_audit_persists_and_credentials_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accounts.json"
            registry = accounts.AccountRegistry(path)
            registry.register("reddit", "owner", accounts.PSEUDONYMOUS, account_id="acc-1",
                              ownership_evidence=["owner-proof"], at=T0)
            registry.transition("acc-1", "warming", at=T0)
            loaded = accounts.AccountRegistry(path)
            self.assertEqual(loaded.get("acc-1")["status"], "warming")
            self.assertEqual([x["event"] for x in loaded.audit("acc-1")],
                             ["account_registered", "account_transitioned"])
            with self.assertRaises(ValueError):
                loaded.register("github", "bad", accounts.PSEUDONYMOUS,
                                ownership_evidence={"token": "do-not-store"}, at=T0)
            self.assertNotIn("do-not-store", path.read_text(encoding="utf-8"))


class NetworkTests(unittest.TestCase):
    def test_binding_stores_only_env_reference_and_fixed_effects(self):
        registry, account = make_registry()
        proxies = network.ProxyRegistry()
        with mock.patch.dict(os.environ, {"GROWOVO_HTTPS_PROXY": "http://user:secret@proxy.internal:8080"}):
            binding = proxies.bind(account["id"], "GROWOVO_HTTPS_PROXY", platform="reddit",
                                   account_registry=registry, at=T0)
            exported = json.dumps(proxies.export(), ensure_ascii=False)
            self.assertNotIn("secret", exported)
            self.assertNotIn("proxy.internal", exported)
        self.assertEqual(binding["env_var"], "GROWOVO_HTTPS_PROXY")
        self.assertEqual(binding["proxy"], {"type": "environment_variable", "name": "GROWOVO_HTTPS_PROXY"})
        self.assertEqual(binding["effects"]["quota_multiplier"], 1)
        self.assertFalse(binding["effects"]["ban_evasion"])
        self.assertEqual(binding["effects"]["account_permissions"], "unchanged")
        self.assertEqual(network.assert_permitted_use(binding)["quota_multiplier"], 1)

    def test_proxy_values_credentials_and_prohibited_purposes_are_rejected(self):
        for env in ("http://user:password@proxy", "${HTTPS_PROXY}", "PROXY_PASSWORD", ""):
            with self.assertRaises(ValueError, msg=env):
                network.ProxyRegistry().bind("acc-1", env)
        with self.assertRaises(ValueError):
            network.bind_proxy("acc-1", "HTTPS_PROXY", proxy_url="http://secret-proxy")
        for purpose in ("increase_quota", "bypass_ban", "绕过封禁", "伪造授权"):
            with self.assertRaises(ValueError, msg=purpose):
                network.validate_use(purpose)
        with self.assertRaises(ValueError):
            network.validate_use("authorized_access", increase_quota=True)
        self.assertFalse(network.use_allowed("quota_increase"))
        self.assertTrue(network.use_allowed("authorized_access"))

    def test_mismatched_account_platform_and_tampered_binding_fail(self):
        registry, account = make_registry()
        with self.assertRaises(ValueError):
            network.ProxyRegistry().bind(account["id"], "HTTPS_PROXY", platform="github",
                                         account_registry=registry)
        binding = network.bind_proxy(account["id"], "HTTPS_PROXY")
        tampered = dict(binding, proxy={"type": "static", "url": "http://user:secret@proxy"})
        with self.assertRaises(ValueError):
            network.assert_permitted_use(tampered)
        with self.assertRaises(ValueError):
            network.assert_permitted_use(binding, bypass_ban=True)

    def test_rebind_unbind_and_audit_are_safe_and_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "network.json"
            registry = network.ProxyRegistry(path)
            registry.bind("acc-1", "HTTPS_PROXY", at=T0)
            registry.bind("acc-1", "GROWOVO_PROXY", at=T0)
            loaded = network.ProxyRegistry(path)
            self.assertEqual(loaded.get("acc-1")["env_var"], "GROWOVO_PROXY")
            loaded.unbind("acc-1", reason="人工撤销", at=T0)
            with self.assertRaises(KeyError):
                loaded.get("acc-1")
            self.assertEqual([x["event"] for x in loaded.audit("acc-1")],
                             ["proxy_bound", "proxy_rebound", "proxy_unbound"])
            self.assertNotIn("http://", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
