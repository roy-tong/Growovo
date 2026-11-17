#!/usr/bin/env python3
"""CDP-observed account mode: any logged-in browser account is allowed, but
the observation is mandatory and recorded; no pre-bound identity required."""
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import account_identity, cdp_identity
from core import action_runner
from unittest import mock
from core.account_identity import AccountIdentityProof, IdentityBlocked
from core.execution_policy import authorize


class ObservedAccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.policy = authorize(
            self.dir, by="owner", actions=["x:reply"], days=30,
            observe_identity=True)

    def test_policy_marks_observed_mode(self):
        self.assertEqual("cdp_observed", self.policy.get("identity_mode"))
        self.assertEqual("observed",
                         account_identity.policy_state(self.policy)["mode"])

    def test_observed_mode_has_no_legacy_identity_blocker(self):
        self.assertEqual([], account_identity.identity_blockers(
            self.dir, policy=self.policy, policy_loaded=True))

    def test_any_logged_in_account_is_allowed_and_recorded(self):
        proof = AccountIdentityProof(channel="x", user_id="uid-b",
                                     handle="another_account")
        result = account_identity.enforce_identity(
            self.dir, "x", "", proof, policy=self.policy)
        self.assertTrue(result["enforced"])
        self.assertEqual("cdp_observed", result["mode"])
        self.assertEqual("uid-b", result["observed_platform_identity"])
        self.assertEqual("another_account", result["handle"])

    def test_action_gate_forces_probe_and_returns_evidence(self):
        proof = AccountIdentityProof(channel="x", user_id="uid-c", handle="browser_user")
        with mock.patch.object(account_identity, "observe", return_value=proof) as observe, \
                mock.patch.object(cdp_identity, "assert_identity_endpoint_matches"):
            evidence = action_runner.gate_account_identity("x", self.dir, "")
        self.assertEqual("cdp_observed", evidence["mode"])
        self.assertEqual("browser_user", evidence["handle"])
        self.assertEqual("1", observe.call_args.kwargs["env"]["MKT_CDP_IDENTITY"])

    def test_observation_is_mandatory(self):
        with self.assertRaises(IdentityBlocked) as ctx:
            account_identity.enforce_identity(self.dir, "x", "", None,
                                              policy=self.policy)
        self.assertEqual("identity_unknown", ctx.exception.reason_code)


if __name__ == "__main__":
    unittest.main()
