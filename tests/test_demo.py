#!/usr/bin/env python3
"""mkt demo 离线单测：一条成功 committed + 三种拦截，且可重复跑。

只写 base/data/demo；不联网、不碰真实账号。
"""
import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.demo import CAMPAIGN, EXPECTED_RESULTS, run_demo


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

    def _ledger(self):
        path = self.base / "data" / "demo" / "ledger.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def _receipts(self):
        path = self.base / "data" / "demo" / "outcomes" / "receipts.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def test_success_and_three_blocked_paths_are_both_shown(self):
        rc, out = self._run()
        self.assertEqual(rc, 0)
        for kw in ("计划校验", "人审闸门", "成功投递", "三道拦截", "账本"):
            self.assertIn(kw, out)
        # 一条成功路径与三种拦截/拒绝必须同时出现在输出里
        for marker in ("d-000 → committed",
                       "d-001 → dry-run",
                       "d-002 → rejected",
                       "d-003 → failed"):
            self.assertIn(marker, out)
        results = {r["ref"]: r["result"] for r in self._ledger()}
        self.assertEqual(results, EXPECTED_RESULTS)
        committed = [r for r in self._ledger() if r["result"] == "committed"]
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0]["transport"], "demo-memory")

    def test_daily_card_has_committed_and_honest_three_layers(self):
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("# 日结果", out)
        self.assertIn("发布量（committed） | 1（计划 1）", out)
        self.assertIn("L1 执行（OBSERVED）", out)
        self.assertIn("L2 互动（UNKNOWN）", out)
        self.assertIn("L3 业务（UNKNOWN）", out)
        self.assertIn("护栏：失败 1", out)
        # L1 凭证来自 committed 账本行，不是手写的
        receipts = self._receipts()
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["layer"], "L1")
        self.assertEqual(receipts[0]["source_ref"], "ledger:d-000")
        self.assertEqual(receipts[0]["campaign_id"], CAMPAIGN)

    def test_rerun_clean_rebuild_keeps_same_terminal_states(self):
        """第二次仍正常：干净重建，账本不累积，也不会被首次 committed 挡住。"""
        first_rc, first_out = self._run()
        second_rc, second_out = self._run()
        self.assertEqual(first_rc, 0)
        self.assertEqual(second_rc, 0)
        # 日卡抬头带当天日期；跨午夜跑两次时只归一化日期，其余输出必须一致
        normalize = lambda text: re.sub(r"\d{4}-\d{2}-\d{2}", "<date>", text)
        self.assertEqual(normalize(first_out), normalize(second_out))
        self.assertEqual(len(self._ledger()), len(EXPECTED_RESULTS))

    def test_stale_sandbox_is_reset(self):
        """旧版本留下的 d-000 不能污染当前五步终态。"""
        data = self.base / "data" / "demo"
        data.mkdir(parents=True)
        (data / "ledger.jsonl").write_text(
            json.dumps({"ref": "d-000", "result": "committed"}) + "\n",
            encoding="utf-8")
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("4 条终态与预期一致", out)

    def test_human_gate_act_is_real(self):
        """第二步必须真的调 enforce_approval：跑完后沙盒 plan 已翻为 human_approved=true。"""
        self._run()
        plan = json.loads((self.base / "data" / "demo" / "plans" / "demo-p0.json")
                          .read_text(encoding="utf-8"))
        self.assertTrue(plan["review"]["human_approved"])
        self.assertEqual(plan["waves"][0]["queue"], f"data/queues/{CAMPAIGN}.json")

    def test_demo_only_writes_its_sandbox(self):
        """demo 只碰 data/demo；真实 data 账本/计划目录不应出现。"""
        self._run()
        self.assertTrue((self.base / "data" / "demo" / "ledger.jsonl").exists())
        self.assertFalse((self.base / "data" / "ledger.jsonl").exists())
        self.assertFalse((self.base / "data" / "plans").exists())


if __name__ == "__main__":
    unittest.main()
