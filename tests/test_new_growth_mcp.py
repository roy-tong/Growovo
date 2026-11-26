import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mcp_server
from core import growth_cycle, growth_engagement, growth_service
from tests.test_growth_goals import goal


class NewGrowthMcpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.root = Path(self.data)
        self.service = growth_service.start(self.data, goal())
        self.sid = self.service["id"]

    def call(self, name, arguments):
        return mcp_server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })

    def test_mcp_lifecycle_uses_existing_projection(self):
        with mock.patch.object(mcp_server, "DATA", self.data):
            response = self.call("mkt_growth", {
                "action": "lifecycle", "service_id": self.sid,
                "now": "2026-11-25T00:00:00Z",
            })
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual("growovo.lifecycle/1", payload["schema"])

    def test_mcp_feedback_export_uses_same_source_bound_projection(self):
        growth_engagement.import_interactions(self.data, self.sid, [{
            "source": "x", "external_id": "comment-1", "author": "alice",
            "body": "I could not replay the result", "url": "https://example.test/c1",
            "received_at": "2026-11-24T09:00:00Z", "conversation_key": "thread-1",
        }])
        review = self.root / "feedback-review.json"
        review.write_text(json.dumps([{
            "conversation_id": "conv-x-thread-1", "category": "usability",
            "summary": "Needs replay help", "evidence_quote": "could not replay",
        }]), encoding="utf-8")
        out = self.root / "feedback.json"
        with mock.patch.object(mcp_server, "DATA", self.data):
            response = self.call("mkt_growth", {
                "action": "feedback-export", "service_id": self.sid,
                "file": str(review), "by": "product-owner", "out": str(out),
            })
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(1, payload["feedback"])
        self.assertEqual("growovo.product-feedback/1", json.loads(out.read_text())["schema"])

    def test_mcp_single_cycle_wake_does_not_start_worker(self):
        growth_cycle.request(self.data, self.sid, actor="tester", cycle_id="cycle-mcp")
        script = self.root / "host.py"
        script.write_text(
            "import json,sys\n"
            "json.dump({'schema':'growovo.growth-cycle-proposal/1','producer_id':'mcp-host@1','proposals':[{'proposal_id':'p1','action_class':'observe','action_id':'diagnose-product','rationale':'diagnose'}]},open(sys.argv[1]+'.proposal','w'))\n"
            "print(json.dumps(json.load(open(sys.argv[1]+'.proposal'))))\n",
            encoding="utf-8")
        adapter = self.root / "adapter.json"
        adapter.write_text(json.dumps({
            "schema": "growovo.host-adapter/1", "adapter_id": "mcp-host", "version": "1",
            "producer_id": "mcp-host@1", "argv": [sys.executable, str(script), "{request}"],
            "authorization": {"authorized_by": "owner"},
        }), encoding="utf-8")
        with mock.patch.object(mcp_server, "DATA", self.data):
            response = self.call("mkt_growth", {
                "action": "cycle", "cycle_action": "wake", "service_id": self.sid,
                "cycle": "cycle-mcp", "adapter": str(adapter),
            })
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual("proposed", payload["status"])

    def adoption_suite(self, external=False):
        script = self.root / "agent.py"
        script.write_text("print('OK')\n", encoding="utf-8")
        return {
            "schema": "growovo.agent-adoption-suite/1", "suite_id": "mcp-adoption",
            "input_class": "synthetic", "product": {"name": "Demo"},
            "host": {"name": "fixture", "model": "fixture",
                     "argv": [sys.executable, str(script), "{intent}"]},
            "authorization": {"authorized_by": "owner", "external_writes_allowed": external},
            "steps": [
                {"id": "discover", "intent": "discover", "success": {"stdout_contains": ["OK"]}},
                {"id": "install", "intent": "install", "success": {"stdout_contains": ["OK"]}},
                {"id": "first_success", "intent": "first success", "success": {"stdout_contains": ["OK"]}},
            ],
        }

    def test_mcp_agent_adoption_runs_local_suite(self):
        suite = self.root / "suite.json"
        suite.write_text(json.dumps(self.adoption_suite()), encoding="utf-8")
        response = self.call("mkt_agent_adoption", {
            "suite": str(suite), "workdir": str(self.root / "work"),
            "out": str(self.root / "evidence"),
        })
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual("PROCESS_PASS", payload["outcome"])

    def test_mcp_agent_adoption_rejects_external_write_suite(self):
        suite = self.root / "suite.json"
        suite.write_text(json.dumps(self.adoption_suite(external=True)), encoding="utf-8")
        response = self.call("mkt_agent_adoption", {
            "suite": str(suite), "workdir": str(self.root / "work"),
            "out": str(self.root / "evidence"),
        })
        self.assertTrue(response.get("isError") or response.get("result", {}).get("isError"))

    def test_skill_contains_new_operating_paths(self):
        text = (Path(__file__).resolve().parents[1] /
                "skills/growovo-gtm/SKILL.md").read_text(encoding="utf-8")
        for phrase in ("growth lifecycle", "feedback-export", "cycle wake", "agent-adoption"):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
