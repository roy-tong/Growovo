"""真实 CLI 端到端：策略→执行→审批→dry-run→结果→优化。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"


class CliE2ETests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ, MKT_HOME=str(self.home))
        self.facts = self.home / "facts.json"
        self.facts.write_text(json.dumps({
            "product": {"name": "X", "what": "test", "url": "https://example.invalid",
                        "stage": "launched", "product_type": "devtool"},
            "icp": [{"hypothesis": "开发者", "tags": ["developers"],
                     "evidence_label": "hypothesis", "evidence": "待验证"}],
            "value_events": [{"name": "激活", "source": "产品事件", "is_approximation": False}],
            "constraints": {"red_lines": ["不做群发"]},
            "owned_assets": {"platforms": ["email"]},
        }))

    def run_mkt(self, *args):
        proc = subprocess.run([sys.executable, str(MKT), *args], cwd=ROOT, env=self.env,
                              text=True, capture_output=True)
        if proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed ({proc.returncode})\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return proc.stdout

    def test_full_email_chain_and_campaign_isolation(self):
        self.run_mkt("gtm", "diagnose", "--facts", str(self.facts), "--json")
        strategy_text = self.run_mkt("strategy", "build", "--facts", str(self.facts), "--json")
        strategy = json.loads(strategy_text)
        strategy_path = self.home / "strategy.json"
        strategy_path.write_text(strategy_text)
        cadence = self.run_mkt("strategy", "cadence", "--facts", str(self.facts), "--start", "2026-11-04")
        self.assertIn("四周动作表", cadence)
        targets = self.home / "targets.csv"
        targets.write_text("target,body\nuser@example.com,先给一个可复现的解决办法\n")
        self.run_mkt("execute", "--strategy-file", str(strategy_path), "--targets", str(targets),
                     "--campaign", "c1", "--account", "acct-a")
        queue = self.home / "queues" / "c1.json"
        self.assertTrue(queue.exists())
        self.run_mkt("plan", "approve", "--id", "c1", "--by", "roy",
                     "--account", "acct-a", "--workspace", str(self.home))
        dry = self.run_mkt("queue", "run", str(queue))
        self.assertIn("dry-run", dry)
        result = json.loads(self.run_mkt("result", "--campaign", "c1", "--json"))
        self.assertEqual("c1", result["scope"]["campaign"])
        empty = json.loads(self.run_mkt("result", "--campaign", "other", "--json"))
        self.assertEqual(0, empty["platform"]["total"])
        revised = self.run_mkt("revise", "--campaign", "c1", "--params", "{\"actions_per_week\":20}")
        self.assertIn("下一轮改什么", revised)

    def test_x_reply_and_manual_channel_cli_contract(self):
        targets = self.home / "x-targets.csv"
        targets.write_text("target,body\n@example,具体问题的最小复现\n")
        strategy = self.home / "x-strategy.json"
        strategy.write_text(json.dumps({"primary": {"id": "x", "name": "X"}}))
        self.run_mkt("execute", "--strategy-file", str(strategy), "--targets", str(targets),
                     "--campaign", "x-c1", "--account", "acct-x")
        queue_path = self.home / "queues" / "x-c1.json"
        queue = json.loads(queue_path.read_text())
        self.assertEqual("reply", queue["action"])
        self.run_mkt("plan", "approve", "--id", "x-c1", "--by", "roy", "--account", "acct-x")
        lint = self.run_mkt("queue", "lint", str(queue_path))
        self.assertIn("PASS", lint)
        from core.queue_runner import QUEUE_ACTIONS
        self.assertIn("reply", QUEUE_ACTIONS)

        manual_strategy = self.home / "manual-strategy.json"
        manual_strategy.write_text(json.dumps({"primary": {"id": "xiaohongshu", "name": "小红书"}}))
        self.run_mkt("execute", "--strategy-file", str(manual_strategy), "--targets", str(targets),
                     "--campaign", "manual-c1")
        self.assertTrue((self.home / "manual" / "manual-c1.md").exists())


if __name__ == "__main__":
    unittest.main()
