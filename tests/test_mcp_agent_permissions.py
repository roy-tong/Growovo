#!/usr/bin/env python3
"""MCP 不得暴露或接受代理自行提升权限的参数。

冻结失败样例（review 2026-11-04，G-1）：MCP 工具面曾把 allow_unverified
作为普通布尔参数暴露，在工作区已存在有效 routine policy 时，代理可以
自行解除 UNVERIFIED 渠道的人工首跑门禁并真实公开发帖。该标志只能保留
为 CLI 人工通道（core 层测试继续直测 precheck/run_action）。
All send/vendor boundaries are fakes; no network operations.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mcp_server

ESCALATION_TOOLS = ("mkt_action", "mkt_run_queue")


class AgentPermissionSurface(unittest.TestCase):
    def test_tool_schemas_do_not_expose_allow_unverified(self):
        for name in ESCALATION_TOOLS:
            tool = next(t for t in mcp_server.TOOLS if t["name"] == name)
            self.assertNotIn("allow_unverified",
                             tool["inputSchema"]["properties"], name)
            self.assertNotIn("allow_unverified", tool["description"], name)

    def test_mkt_action_rejects_agent_supplied_allow_unverified(self):
        with patch.object(mcp_server, "DATA", Path(tempfile.mkdtemp())):
            response = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "mkt_action", "arguments": {
                    "channel": "hn", "action": "reply", "target": "item:1",
                    "text": "hi", "go": True, "allow_unverified": True}}})
        self.assertTrue(response["result"].get("isError"), response)
        self.assertIn("CLI", response["result"]["content"][0]["text"])

    def test_mkt_run_queue_rejects_agent_supplied_allow_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "queues").mkdir()
            path = data / "queues/c1.json"
            path.write_text(json.dumps({
                "campaign": "c1", "channel": "email", "action": "message",
                "items": [{"id": "1", "target": "a@example.com", "body": "fixture"}],
            }), encoding="utf-8")
            with patch.object(mcp_server, "DATA", data):
                response = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "mkt_run_queue", "arguments": {
                        "queue_path": str(path), "go": True, "allow_unverified": True}}})
        self.assertTrue(response["result"].get("isError"), response)
        self.assertIn("CLI", response["result"]["content"][0]["text"])

    def test_unverified_channel_still_requires_human_first_run_without_flag(self):
        """没有该参数时，UNVERIFIED 渠道实跑仍被人工首跑门禁拦下（fail closed 不变）。"""
        with patch.object(mcp_server, "DATA", Path(tempfile.mkdtemp())):
            response = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "mkt_action", "arguments": {
                    "channel": "hn", "action": "reply", "target": "item:1",
                    "text": "hi", "go": True}}})
        self.assertTrue(response["result"].get("isError"), response)
        self.assertIn("UNVERIFIED", response["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
