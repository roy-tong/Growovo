"""v2 CLI 端到端：launch → briefs → stage → status → decide → doctor → install。

全部在临时工作区；tick 不实跑（真实 now 可能在窗口外，只验证不报错）。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core.execution_policy import authorize

ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"


class GrowCliV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ, MKT_HOME=str(self.home))
        self.facts = self.home / "facts.json"
        self.facts.write_text(json.dumps({
            "product": {"name": "Verovo", "what": "test",
                        "url": "https://example.invalid", "stage": "launched",
                        "product_type": "devtool"},
            "icp": [{"hypothesis": "开发者", "tags": ["developers"],
                     "evidence_label": "hypothesis", "evidence": "待验证"}],
            "baseline": {"followers": 120},
            "value_events": [{"name": "激活", "source": "产品事件",
                              "is_approximation": False}],
            "constraints": {"red_lines": ["不做群发"]},
            "owned_assets": {"platforms": ["x", "email"]},
        }, ensure_ascii=False), encoding="utf-8")
        self.drafts = self.home / "drafts.json"
        self.drafts.write_text(json.dumps([
            {"target": "tweet:https://x.com/a/1",
             "body": "第 1 条具体回复：先说结论，再给一个可复现的细节。"},
            {"target": "tweet:https://x.com/a/2",
             "body": "第 2 条具体回复：换一个场景，结论仍然成立。"},
        ], ensure_ascii=False), encoding="utf-8")

    def run_mkt(self, *args, check=True):
        proc = subprocess.run([sys.executable, str(MKT), *args],
                              cwd=ROOT, env=self.env, text=True,
                              capture_output=True)
        if check and proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed ({proc.returncode})"
                      f"\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return proc

    def _authorize(self):
        authorize(self.home, by="测试授权人",
                  actions=["x:reply", "email:message"], days=30)

    def _launch(self):
        self._authorize()
        return self.run_mkt(
            "grow", "launch", "g1", "--facts", str(self.facts),
            "--goal", "awareness.followers", "--target", "20",
            "--handle", "verovo", "--skip-probe")

    def test_platforms_lists_auto_and_gated(self):
        proc = self.run_mkt("grow", "platforms")
        self.assertIn("自动执行", proc.stdout)
        self.assertIn("x", proc.stdout)
        self.assertIn("email", proc.stdout)
        self.assertIn("github", proc.stdout)   # gated 也列出来，但标注需解锁

    def test_launch_requires_policy(self):
        proc = self.run_mkt("grow", "launch", "g1", "--facts", str(self.facts),
                            "--goal", "awareness.followers", "--skip-probe",
                            check=False)
        self.assertEqual(2, proc.returncode)
        self.assertIn("mkt policy enable", proc.stdout + proc.stderr)
        self.assertFalse((self.home / "grow" / "g1.json").exists())

    def test_full_command_surface(self):
        proc = self._launch()
        self.assertIn("闭环已启动", proc.stdout)
        self.assertIn("开闸", proc.stdout)

        briefs = self.run_mkt("grow", "briefs", "g1")
        self.assertIn("b-x-reply", briefs.stdout)
        self.assertIn("每周 17 条", briefs.stdout)  # 主渠道 85%
        self.assertIn("b-email-message", briefs.stdout)

        stage = self.run_mkt("grow", "stage", "g1", "--from", str(self.drafts))
        self.assertIn("已暂存 2 条", stage.stdout)
        self.assertTrue((self.home / "queues" / f"g1-{self._today().replace('-', '')}.json").is_file())

        status = self.run_mkt("grow", "status", "g1", "--on", self._today())
        self.assertIn("今日配额", status.stdout)
        self.assertIn("供给", status.stdout)

        decide = self.run_mkt("grow", "decide", "g1", "--action", "continue",
                              "--note", "先观察", "--by", "roy",
                              "--on", self._today())
        self.assertIn("已记录决定", decide.stdout)

        doctor = self.run_mkt("grow", "doctor", "g1", check=False)
        self.assertIn("体检", doctor.stdout)

        install = self.run_mkt("grow", "install", "g1")
        self.assertIn("定时任务已生成", install.stdout)
        self.assertIn("grow tick g1", install.stdout)

        daily = self.run_mkt("grow", "daily", "g1", "--on", self._today())
        self.assertIn("日结果", daily.stdout)
        self.assertIn("continue", daily.stdout)

    def test_tick_without_queue_is_clean(self):
        self._launch()
        proc = self.run_mkt("grow", "tick", "g1", "--on", self._today())
        self.assertIn("tick", proc.stdout)
        self.assertIn("状态 active", proc.stdout)

    @staticmethod
    def _today():
        from datetime import date
        return date.today().isoformat()


if __name__ == "__main__":
    unittest.main()
