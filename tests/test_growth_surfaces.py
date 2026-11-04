"""增长智能的 CLI 与只读 MCP 表面。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mcp_server import _call_tool


ROOT = Path(__file__).resolve().parents[1]


def artifact():
    return {
        "kind": "tutorial", "title": "十分钟恢复服务",
        "scenario": {"user": "开发者", "trigger": "部署失败", "task": "恢复服务"},
        "direct_user_value": "按步骤恢复服务并验证进程",
        "evidence": [{"summary": "真实复现记录", "source_ref": "log:1"}],
        "reproduction_steps": ["复现错误", "执行修复", "检查进程"],
        "limitations": ["仅适用于示例服务"],
        "observation_events": [{"event_type": "reproduction_passed", "source": "local-test",
                                "approximated": False, "observed": True,
                                "subject": "u1", "source_ref": "run-1", "ts": "2026-11-04"}],
        "platform": "github", "motion": "community_answer", "vertical": "devtool",
    }


class GrowthSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir()
        self.env = dict(os.environ, MKT_HOME=str(self.home))

    def run_mkt(self, *args):
        proc = subprocess.run([sys.executable, str(ROOT / "mkt.py"), *args], cwd=ROOT,
                              env=self.env, text=True, capture_output=True)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        return proc.stdout

    def test_cli_opportunity_artifact_journey(self):
        facts = self.home / "facts.json"
        facts.write_text(json.dumps({"value": [{"id": "billing", "text": "账单复算"}],
                                     "target_audience": [{"id": "finops", "text": "FinOps"}],
                                     "strengths": [{"id": "evidence", "text": "独立证据"}]}))
        signals = self.home / "signals.json"
        signals.write_text(json.dumps({"help_requests": [{
            "id": "s1", "text": "账单对不上", "value_ids": ["billing"],
            "audience_ids": ["finops"], "strength_ids": ["evidence"],
            "source_ref": "post-1"}]}))
        out = self.run_mkt("opportunity", "--facts", str(facts), "--signals", str(signals), "--json")
        self.assertIn("candidates", out)

        path = self.home / "artifact.json"
        path.write_text(json.dumps(artifact()))
        self.assertIn("校验通过", self.run_mkt("artifact", "lint", "--file", str(path)))
        self.assertIn("十分钟恢复服务", self.run_mkt("artifact", "render", "--file", str(path)))

        events = self.home / "events.json"
        events.write_text(json.dumps([{"event_type": "trial", "campaign": "c1", "subject": "u1",
                                       "ts": "2026-11-04", "source_ref": "event-1", "approximated": False}]))
        out = self.run_mkt("journey", "--events", str(events), "--campaign", "c1",
                           "--since", "2026-11-01", "--until", "2026-11-05", "--json")
        self.assertIn("funnel", out)

    def test_mcp_readonly_growth_tools(self):
        out = _call_tool("mkt_opportunity", {"facts": {"value": [{"id": "billing", "text": "账单"}]},
                                             "signals": {"help_requests": [{"text": "对不上", "value_ids": ["billing"]}]}})
        self.assertIn("candidates", out["content"][0]["text"])
        out = _call_tool("mkt_value_artifact", {"action": "lint", "artifact": artifact()})
        self.assertIn('"ok": true', out["content"][0]["text"])
        out = _call_tool("mkt_journey", {
            "events": [{"event_type": "trial", "campaign": "c1", "subject": "u1",
                        "ts": "2026-11-04", "source_ref": "e1", "approximated": False}],
            "campaign": "c1", "time_window": {"start": "2026-11-01", "end": "2026-11-05"}})
        self.assertIn("funnel", out["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
