import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mcp_server
from core import growth_cycle, growth_cycle_resolution, growth_service
from core.storage import AtomicJSONStore
from tests.test_growth_goals import goal

ROOT = Path(__file__).resolve().parents[1]
PROPOSAL = "growovo.growth-cycle-proposal/1"


class GrowthCycleResolutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.service = growth_service.start(self.data, goal())
        self.sid = self.service["id"]

    def propose_without_grant(self):
        growth_cycle.request(self.data, self.sid, actor="tester", cycle_id="cycle-permission")
        growth_cycle.propose(self.data, self.sid, "cycle-permission", {
            "schema": PROPOSAL, "producer_id": "host",
            "proposals": [{"proposal_id": "p1", "action_class": "new_outreach",
                           "action_id": "x-publish", "rationale": "needs grant"}]})
        return growth_cycle.status(self.data, self.sid, "cycle-permission")

    def test_missing_grant_becomes_incremental_permission_request(self):
        cycle = self.propose_without_grant()
        got = growth_cycle_resolution.from_cycle(cycle)
        self.assertEqual("ovo.problem-resolution/1", got["schema"])
        self.assertEqual("permission_needed", got["requested_resolution"])
        self.assertEqual(["growth-action-class:new_outreach"], got["permission"]["scopes"])
        self.assertTrue(got["permission"]["incremental"])
        self.assertTrue(got["permission"]["missing_only"])
        self.assertFalse(got.get("execution_applied", False))

    def test_unknown_external_result_requires_reconciliation_contract(self):
        growth_cycle.request(self.data, self.sid, actor="tester", cycle_id="cycle-unknown")
        path = growth_cycle.path(self.data, self.sid, "cycle-unknown")
        cycle = AtomicJSONStore(path).load()
        cycle.update(status="blocked", executed_at="2026-11-25T00:00:00Z", executions=[{
            "proposal_id": "p1", "action_id": "x-publish", "execution": "executed",
            "result": {"status": "waiting_verification", "action_id": "x-publish"},
        }])
        got = growth_cycle_resolution.from_cycle(cycle)
        self.assertEqual("auto_fix", got["requested_resolution"])
        self.assertEqual("unknown", got["observation"]["external_effect_status"])
        self.assertEqual("reconcile-external-receipt", got["fix"]["action_id"])
        self.assertTrue(got["fix"]["side_effect_free"])

    def test_open_cycle_has_no_invented_problem(self):
        cycle = growth_cycle.request(
            self.data, self.sid, actor="tester", cycle_id="cycle-open")
        with self.assertRaisesRegex(ValueError, "no resolution problem"):
            growth_cycle_resolution.from_cycle(cycle)

    def test_mcp_exports_same_read_only_contract(self):
        self.propose_without_grant()
        with mock.patch.object(mcp_server, "DATA", self.data):
            response = mcp_server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "mkt_growth", "arguments": {
                    "action": "cycle", "cycle_action": "resolution", "service_id": self.sid,
                    "cycle": "cycle-permission",
                }},
            })
        self.assertFalse(response["result"].get("isError"), response)
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual("permission_needed", payload["requested_resolution"])

    def test_cli_exports_shared_resolution_contract(self):
        self.propose_without_grant()
        result = subprocess.run([
            sys.executable, str(ROOT / "mkt.py"), "--workspace", self.data,
            "growth", "cycle", "resolution", self.sid, "--cycle", "cycle-permission",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("ovo.problem-resolution/1", payload["schema"])
        self.assertEqual("permission_needed", payload["requested_resolution"])


if __name__ == "__main__":
    unittest.main()
