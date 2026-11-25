import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mcp_server
from core import growth_cycle, growth_service
from tests.test_growth_goals import goal


class GrowthPeriodReportMcpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.service = growth_service.start(self.data, goal())
        self.sid = self.service["id"]
        growth_cycle.request(self.data, self.sid, actor="tester", cycle_id="cycle-report-mcp")

    def test_mcp_report_uses_read_only_period_projection(self):
        cycle_path = growth_cycle.path(self.data, self.sid, "cycle-report-mcp")
        before = cycle_path.read_bytes()
        with mock.patch.object(mcp_server, "DATA", self.data):
            response = mcp_server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "mkt_growth", "arguments": {
                    "action": "report", "service_id": self.sid,
                    "start": "2020-01-01T00:00:00Z", "end": "2030-01-01T00:00:00Z",
                }},
            })
        self.assertFalse(response.get("result", {}).get("isError"), response)
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual("growovo.growth-period-report/1", payload["schema"])
        self.assertEqual(1, payload["cycles"]["open"])
        self.assertEqual(before, cycle_path.read_bytes())

    def test_manifest_and_skill_explain_period_report_boundary(self):
        tool = next(row for row in mcp_server.TOOLS if row["name"] == "mkt_growth")
        for field in ("start", "end", "identity_map"):
            self.assertIn(field, tool["inputSchema"]["properties"])
        self.assertIn("周期汇报", tool["description"])
        text = (Path(__file__).resolve().parents[1] /
                "skills/growovo-gtm/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("growth report", text)
        self.assertIn("current_lifecycle_records_not_period_delta", text)


if __name__ == "__main__":
    unittest.main()
