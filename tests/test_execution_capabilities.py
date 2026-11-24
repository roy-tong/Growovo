import copy
import tempfile
import unittest

from core import action_resolution, execution_capabilities
from core.growth_goals import compile_goal
from tests.test_growth_goals import goal


def capability(verified=None):
    verified = list(verified or [])
    return {
        "account": "default", "platform": "github", "action": "reply", "route": "github_app",
        "implemented": ["read", "write", "verify"], "verified": verified,
        "available": ["read", "write", "verify"], "required": ["read", "write", "verify"],
        "authorization": {"status": "active", "scope": ["repo:o/r"], "expires_at": "2027-01-01T00:00:00Z"},
        "last_verified_at": "2026-11-24T00:00:00Z" if verified else "",
        "verification_evidence": {operation: f"proof://{operation}" for operation in verified},
        "adapter_version": "github-app/1", "paused_reason": "",
    }


class ExecutionCapabilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name

    def test_read_write_and_verify_are_independent(self):
        execution_capabilities.upsert(
            self.data, capability(verified=["read", "write"]), by="tester")
        row = execution_capabilities.view(self.data, account="default")[0]
        self.assertTrue(row["operations"]["read"]["verified"])
        self.assertTrue(row["operations"]["write"]["verified"])
        self.assertFalse(row["operations"]["verify"]["verified"])
        self.assertFalse(row["executable"])
        self.assertIn("verify_not_verified", row["missing"])

    def test_verification_requires_evidence_and_rejects_credentials(self):
        row = capability(verified=["read", "write", "verify"])
        row["verification_evidence"]["write"] = ""
        with self.assertRaisesRegex(ValueError, "evidence"):
            execution_capabilities.upsert(self.data, row, by="tester")
        for key in ("access_token", "client_secret", "api_key", "password", "private_key_path"):
            with self.subTest(key=key):
                row = capability()
                row[key] = "must-not-be-stored"
                with self.assertRaisesRegex(ValueError, "凭据"):
                    execution_capabilities.upsert(self.data, row, by="tester")

    def test_expiry_pause_and_account_isolation_are_derived(self):
        expired = capability(verified=["read", "write", "verify"])
        expired["authorization"]["expires_at"] = "2020-01-01T00:00:00Z"
        execution_capabilities.upsert(self.data, expired, by="tester")
        row = execution_capabilities.view(self.data, account="default")[0]
        self.assertFalse(row["authorized_now"])
        self.assertIn("authorization_expired", row["missing"])

        paused = capability(verified=["read", "write", "verify"])
        paused["paused_reason"] = "platform_warning"
        execution_capabilities.upsert(self.data, paused, by="tester")
        healthy = capability(verified=["read", "write", "verify"])
        healthy["account"] = "second"
        execution_capabilities.upsert(self.data, healthy, by="tester")
        self.assertFalse(execution_capabilities.view(self.data, account="default")[0]["executable"])
        self.assertTrue(execution_capabilities.view(self.data, account="second")[0]["executable"])

    def test_resolution_uses_structured_capabilities_and_intake_exposes_gaps(self):
        execution_capabilities.upsert(
            self.data, capability(verified=["read", "write"]), by="tester")
        rows = execution_capabilities.view(self.data)
        action = next(row for row in compile_goal(goal())["actions"] if row["id"] == "github-engage")
        resolution = action_resolution.resolve(action, {"capabilities": rows, "account": "default"})
        self.assertEqual("manual_handoff", resolution["execution_mode"])
        self.assertIn("verify_not_verified", resolution["missing"])
        readiness = execution_capabilities.plan_readiness(self.data, compile_goal(goal()))
        github = next(row for row in readiness["actions"] if row["id"] == "github-engage")
        self.assertFalse(github["ready"])
        self.assertIn("verify_not_verified", github["missing"])

        execution_capabilities.upsert(
            self.data, capability(verified=["read", "write", "verify"]), by="tester")
        rows = execution_capabilities.view(self.data)
        resolution = action_resolution.resolve(action, {"capabilities": rows, "account": "default"})
        self.assertEqual("github_app", resolution["execution_mode"])

    def test_explicit_capability_set_fails_closed_for_missing_route(self):
        action = next(row for row in compile_goal(goal())["actions"] if row["id"] == "github-engage")
        resolution = action_resolution.resolve(action, {"capabilities": [], "account": "default"})
        self.assertEqual("manual_handoff", resolution["execution_mode"])
        self.assertIn("capability_record_missing", resolution["missing"])


if __name__ == "__main__":
    unittest.main()
