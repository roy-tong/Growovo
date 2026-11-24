import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mcp_server
from tests.test_growth_goals import goal


class GrowthMcpTest(unittest.TestCase):
    def test_start_and_next_share_the_persistent_growth_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "goal.json"
            path.write_text(json.dumps(goal()), encoding="utf-8")
            with mock.patch.object(mcp_server, "DATA", tmp):
                started = mcp_server.handle({
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "mkt_growth",
                               "arguments": {"action": "start", "file": str(path)}}})
                service = json.loads(started["result"]["content"][0]["text"])
                following = mcp_server.handle({
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "mkt_growth",
                               "arguments": {"action": "next", "service_id": service["id"]}}})
                action = json.loads(following["result"]["content"][0]["text"])
                mcp_server.handle({
                    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "mkt_growth",
                               "arguments": {"action": "run", "service_id": service["id"],
                                             "action_id": "diagnose-product", "by": "tester"}}})
                attempts = mcp_server.handle({
                    "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "mkt_growth",
                               "arguments": {"action": "attempts", "service_id": service["id"]}}})
                saved = json.loads(attempts["result"]["content"][0]["text"])
        self.assertEqual("diagnose-product", action["id"])
        self.assertEqual("completed", saved["attempts"][0]["status"])

    def test_mcp_cannot_start_continuous_worker_or_attest_capabilities(self):
        for action in ("operator-serve", "capability-upsert"):
            response = mcp_server.handle({
                "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "mkt_growth", "arguments": {"action": action}}})
            self.assertTrue(response.get("isError") or response.get("result", {}).get("isError"))


if __name__ == "__main__":
    unittest.main()
