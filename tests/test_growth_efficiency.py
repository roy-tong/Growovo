import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mcp_server
from core import growth_efficiency, growth_engagement, growth_experiments, growth_service
from tests.test_growth_goals import goal

ROOT = Path(__file__).resolve().parents[1]


class GrowthEfficiencyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.service = growth_service.start(self.data, goal())
        self.sid = self.service["id"]
        growth_engagement.import_interactions(self.data, self.sid, [
            {"source": "x", "external_id": "x1", "author": "a", "body": "question",
             "received_at": "2026-11-24T09:00:00Z"},
            {"source": "x", "external_id": "x2", "author": "b", "body": "question",
             "received_at": "2026-11-24T10:00:00Z"},
            {"source": "github", "external_id": "g1", "author": "c", "body": "issue",
             "received_at": "2026-11-24T11:00:00Z"},
            {"source": "x", "external_id": "old", "author": "d", "body": "old",
             "received_at": "2026-11-01T11:00:00Z"},
        ])
        growth_experiments.create(self.data, self.sid, {
            "id": "efficiency-events", "hypothesis": "fixture", "primary_metric": "activation",
            "target": 1, "window": {"start": "2026-11-20T00:00:00Z", "end": "2026-11-30T23:59:59Z"},
            "stop_rule": "stop on missing evidence"})
        verification = {"status": "verified", "method": "product_database_export", "ref": "export-1"}
        growth_experiments.observe(self.data, self.sid, "efficiency-events", [
            {"id": "a1", "type": "activation", "subject": "u1", "source": "product",
             "channel": "x", "at": "2026-11-24T12:00:00Z", "source_ref": "product://a1",
             "verification": verification},
            {"id": "a2", "type": "activation", "subject": "u2", "source": "product",
             "channel": "x", "at": "2026-11-24T12:01:00Z"},
            {"id": "a3", "type": "activation", "subject": "u3", "source": "product",
             "at": "2026-11-24T12:02:00Z", "source_ref": "product://a3",
             "verification": verification},
        ])
        self.spec = {
            "schema": "growovo.channel-investment/1", "investment_id": "investment-1",
            "service_id": self.sid, "window": {"start": "2026-11-24T00:00:00Z",
                                               "end": "2026-11-25T00:00:00Z"},
            "channels": [
                {"channel": "x", "human_minutes": 120, "time_source_ref": "time://x",
                 "cost": {"amount": "40", "currency": "USD", "source_ref": "ledger://x"}},
                {"channel": "github", "human_minutes": 60, "time_source_ref": "time://github",
                 "cost": {"amount": "20", "currency": "USD", "source_ref": "ledger://github"}},
            ],
        }

    def test_channel_efficiency_uses_observed_interactions_and_qualified_activation(self):
        result = growth_efficiency.build(self.data, self.sid, self.spec)
        self.assertEqual("growovo.channel-efficiency/1", result["schema"])
        x = result["channels"][0]
        self.assertEqual(2, x["observed_interactions"])
        self.assertEqual(1, x["verified_activations"])
        self.assertEqual("60.00", x["minutes_per_interaction"])
        self.assertEqual("20.00", x["cost_per_interaction"])
        self.assertEqual("40.00", x["cost_per_verified_activation"])
        github = result["channels"][1]
        self.assertIsNone(github["cost_per_verified_activation"])
        self.assertEqual("explicit_channel_event_only_not_causal", result["attribution"])

    def test_unqualified_and_unassigned_activations_stay_visible(self):
        result = growth_efficiency.build(self.data, self.sid, self.spec)
        self.assertEqual(1, result["coverage"]["unqualified_activation_events"])
        self.assertEqual(1, result["coverage"]["unassigned_verified_activations"])

    def write_spec(self):
        path = Path(self.data) / "investment.json"
        path.write_text(json.dumps(self.spec), encoding="utf-8")
        return path

    def test_cli_projects_channel_efficiency(self):
        result = subprocess.run([
            sys.executable, str(ROOT / "mkt.py"), "--workspace", self.data,
            "growth", "efficiency", self.sid, "--file", str(self.write_spec()),
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(2, json.loads(result.stdout)["channels"][0]["observed_interactions"])

    def test_mcp_projects_same_efficiency(self):
        with mock.patch.object(mcp_server, "DATA", self.data):
            response = mcp_server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "mkt_growth", "arguments": {
                    "action": "efficiency", "service_id": self.sid, "file": str(self.write_spec()),
                }},
            })
        self.assertFalse(response["result"].get("isError"), response)
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual("40.00", payload["channels"][0]["cost_per_verified_activation"])


if __name__ == "__main__":
    unittest.main()
