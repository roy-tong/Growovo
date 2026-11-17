#!/usr/bin/env python3
"""CLI-level regression tests for day-1 grow/plan flows."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CliGrowFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def run_cli(self, *args):
        env = os.environ.copy()
        env["MKT_HOME"] = str(self.data)
        return subprocess.run(
            [sys.executable, str(ROOT / "mkt.py"), *args],
            cwd=str(ROOT), env=env, text=True, capture_output=True, timeout=60)

    def test_plan_approve_without_queue_is_actionable(self):
        initialized = self.run_cli("init")
        self.assertEqual(0, initialized.returncode, initialized.stderr)
        created = self.run_cli(
            "plan", "new", "--id", "p1", "--goal", "30 天 +200 关注者",
            "--hypothesis", "独立开发者有日志分诊痛点", "--beachhead", "MCP 工具作者",
            "--channel", "email", "--wave-action", "message", "--size", "3")
        self.assertEqual(0, created.returncode, created.stderr)
        approved = self.run_cli("plan", "approve", "--id", "p1", "--by", "tester")
        self.assertEqual(2, approved.returncode)
        self.assertIn("先构建队列", approved.stdout)
        self.assertNotIn("Traceback", approved.stdout + approved.stderr)


if __name__ == "__main__":
    unittest.main()


class ProduceExitCodeTests(unittest.TestCase):
    def test_zero_added_with_rejection_returns_nonzero(self):
        import json
        import os
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MKT_HOME"] = tmp
            facts = Path(tmp) / "facts.json"
            facts.write_text(json.dumps({
                "product": {"name": "TraceLens", "what": "日志分诊 CLI",
                            "url": "https://example.com", "stage": "launched",
                            "product_type": "devtool"},
                "icp": [{"hypothesis": "独立开发者", "tags": ["developers"],
                         "evidence_label": "hypothesis", "evidence": "待验证"}],
                "baseline": {"followers": 40},
                "value_events": [{"name": "首次生成证据包", "source": "本地事件",
                                  "is_approximation": False}],
                "constraints": {"red_lines": ["不群发"]},
                "owned_assets": {"platforms": ["x"]},
            }, ensure_ascii=False), encoding="utf-8")
            sys.path.insert(0, str(ROOT))
            import mkt
            old_data = getattr(mkt, "DATA", None)
            old_home = os.environ.get("MKT_HOME")

            def restore_globals():
                if old_data is not None:
                    mkt.DATA = old_data
                if old_home is None:
                    os.environ.pop("MKT_HOME", None)
                else:
                    os.environ["MKT_HOME"] = old_home
            self.addCleanup(restore_globals)
            from core.execution_policy import authorize
            from core.grow_v2 import GrowRunV2
            ws = Path(tmp)
            authorize(ws, by="tester", actions=["x:reply"], days=30)
            GrowRunV2.launch(ws / "grow/g1.json", run_id="g1",
                             facts_path=str(facts), goal_id="awareness.followers",
                             channels=["x"], target=20, actions_per_week=7,
                             handle="tracelens_demo", data_dir=ws, skip_probe=True)
            with patch.object(GrowRunV2, "produce",
                              return_value={"added": 0, "rejected": ["Codex 调用失败"]}):
                rc = mkt.main(["grow", "produce", "g1"])
            self.assertEqual(1, rc)


if __name__ == "__main__":
    unittest.main()
