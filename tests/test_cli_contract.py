"""CLI 操作契约：路径解析、错误语义、SOP 覆盖率展示。

这些测试只调用真实 CLI 子进程，不发送任何消息、不触碰真实 data/。
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"


class CliContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "data"
        self.home.mkdir(parents=True)
        self.env = dict(os.environ, MKT_HOME=str(self.home),
                        PYTHONPATH=str(ROOT))

    def run_mkt(self, *args, extra_env=None):
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        return subprocess.run([sys.executable, str(MKT), *args], cwd=ROOT, env=env,
                              text=True, capture_output=True)

    def test_missing_queue_path_is_needs_user_input(self):
        for action in ("lint", "run"):
            proc = self.run_mkt("queue", action)
            self.assertEqual(2, proc.returncode)
            payload = json.loads(proc.stdout)
            self.assertEqual("NEEDS_USER_INPUT", payload["reason_code"])
            for key in ("missing", "impact", "next_action", "example"):
                self.assertTrue(payload.get(key), f"{action} 缺少 {key}")
            self.assertEqual("growovo.error/1", payload["semantics"])

    def test_error_json_env_keeps_semantics(self):
        proc = self.run_mkt("queue", "build", extra_env={"GROWOVO_ERROR_JSON": "1"})
        self.assertEqual(2, proc.returncode)
        payload = json.loads(proc.stdout)
        self.assertEqual("NEEDS_USER_INPUT", payload["reason_code"])
        self.assertIn("--campaign", " ".join(payload["missing"]))

    def test_queue_build_relative_out_resolves_under_data(self):
        template = Path(self.tmp.name) / "outreach.md"
        template.write_text("你好 {name}，看到你在 {org} 做 {topic}。"
                            "先把失败命令和完整报错发我，我按步骤复现后再回复你。\n",
                            encoding="utf-8")
        targets = Path(self.tmp.name) / "targets.csv"
        targets.write_text("target,name,org,topic\n"
                           "user@example.com,小林,示例组织,开发者工具\n", encoding="utf-8")
        proc = self.run_mkt("queue", "build", "--campaign", "rel",
                            "--template", str(template), "--targets", str(targets),
                            "--out", "rel.json")
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        expected = (self.home / "rel.json").resolve()
        self.assertTrue(expected.exists())
        self.assertIn(str(expected), proc.stdout)
        self.assertIn("路径规则", proc.stdout)

    def test_sop_coverage_summary_is_explicit(self):
        proc = self.run_mkt("sop", "coverage")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertRegex(proc.stdout, r"SOP 覆盖：\d+/18 个能力域有可执行 SOP")
        self.assertIn("不表示已在真实业务中运行过", proc.stdout)

    def test_capability_lint_reports_coverage(self):
        proc = self.run_mkt("capability", "lint")
        self.assertEqual(0, proc.returncode, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIn("/18", payload["coverage_summary"])

    def test_human_error_lines_on_stderr(self):
        outputs = Path(self.tmp.name) / "outputs.json"
        outputs.write_text("[]", encoding="utf-8")
        proc = self.run_mkt("sop", "complete", "--run-id", "r1",
                            "--step-id", "s1", "--outputs", str(outputs))
        self.assertEqual(2, proc.returncode)
        self.assertEqual("", proc.stdout)
        self.assertIn("NEEDS_USER_INPUT", proc.stderr)
        self.assertIn("影响：", proc.stderr)
        self.assertIn("下一步：", proc.stderr)


if __name__ == "__main__":
    unittest.main()
