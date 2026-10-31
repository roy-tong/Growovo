#!/usr/bin/env python3
"""mkt demo 离线单测（W1 B3）：零网络零账号四步闭环，且可重复跑。"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.demo import CAMPAIGN, run_demo

EXPECTED = {"d-001": "dry-run", "d-002": "rejected", "d-003": "failed"}


class TestDemo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def _run(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run_demo(self.base)
        return rc, buf.getvalue()

    def test_four_acts_all_pass(self):
        rc, out = self._run()
        self.assertEqual(rc, 0)
        for kw in ("计划校验", "人审闸门", "写路径事务", "账本"):
            self.assertIn(kw, out)
        ledger = self.base / "data" / "demo" / "ledger.jsonl"
        self.assertTrue(ledger.exists())
        results = {}
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                results[rec["ref"]] = rec["result"]
        self.assertEqual(results, EXPECTED)

    def test_rerun_deterministic(self):
        """沙盒可反复跑：终态不因账本累积而改变（demo 只有 dry-run/rejected/failed，无 committed）。"""
        self.assertEqual(self._run()[0], 0)
        rc, _ = self._run()
        self.assertEqual(rc, 0)

    def test_human_gate_act_is_real(self):
        """第二步必须真的调 enforce_approval：跑完后沙盒 plan 已翻为 human_approved=true。"""
        self._run()
        plan = json.loads((self.base / "data" / "demo" / "plans" / "demo-p0.json")
                          .read_text(encoding="utf-8"))
        self.assertTrue(plan["review"]["human_approved"])
        self.assertEqual(plan["waves"][0]["queue"], f"data/queues/{CAMPAIGN}.json")


if __name__ == "__main__":
    unittest.main()
