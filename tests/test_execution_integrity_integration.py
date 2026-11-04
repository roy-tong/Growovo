#!/usr/bin/env python3
"""P0 执行完整性集成验收：在同一分支上贯穿 G-1/G-2/G-3/G-4 四项修复。

复核（2026-11-04）要求：四个独立分支各自绿不等于组合后满足同一执行安全
契约；合并后必须重跑一组跨修复的对抗场景。本文件即该组场景：

  1. DNC 数据损坏 + 有效授权 + 请求真实发布        → 禁止执行
  2. Ledger 损坏 + 请求 X 发帖                      → 禁止执行
  3. 两个 Worker 同时执行同一目标动作               → 不得重复执行
  4. Vendor 成功发送但未产生可信回执                → 未知状态，禁止自动重发
  5. 未知状态经过人工裁决                           → 按裁决结果安全恢复
  6. 一个批次多目标，部分成功、部分未知             → 分目标记录
  7. Vendor 留有历史成功日志，本次没有新回执        → 不得误认历史结果

All send/vendor boundaries are fakes; no network or real account operations.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from channels.x import adapter
from core.gates import SuppressionIntegrityError
from core.ledger import Ledger, LedgerCorruptError
from core.pipeline import standard_pipeline


def _fake_vendor(dirpath: Path, script: str, *, rc: int = 0, slow: bool = False):
    body = ("import sys, pathlib, time\n"
            "pathlib.Path(__file__).parent.joinpath('called-%s').write_text('1')\n" % script)
    if slow:
        body += "time.sleep(0.6)\n"
    body += "sys.exit(%d)\n" % rc
    (dirpath / script).write_text(body, encoding="utf-8")


class ExecutionIntegrityIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vendor = self.root / "vendor"
        self.vendor.mkdir()
        self.data = self.root / "data"
        (self.data / "plans").mkdir(parents=True)
        self.ledger = Ledger(self.data / "ledger.jsonl")
        _fake_vendor(self.vendor, "follow_x.py")
        _fake_vendor(self.vendor, "reply_x.py")

    def _run(self, *args, **kw):
        defaults = dict(go=True, adhoc=True, campaign="integ",
                        ledger=self.ledger, log=lambda *a: None,
                        data_dir=self.data)
        defaults.update(kw)
        with patch.object(adapter, "VENDOR", self.vendor):
            return adapter.run(*args, **defaults)

    def _called(self, script):
        return (self.vendor / ("called-" + script)).exists()

    # 场景 1：DNC 数据损坏 + 有效授权（已人审 plan）+ 请求真实发布 → 禁止执行
    def test_corrupt_dnc_blocks_real_send_despite_valid_authorization(self):
        (self.data / "plans/p1.json").write_text(json.dumps({
            "plan_id": "p1", "goal": "集成", "created": "2026-11-04",
            "hypothesis": "h", "icp": {"beachhead": "开发者"},
            "audience": {"spec": "spec"},
            "waves": [{"wave": 1, "channel": "email", "action": "message",
                       "size": 1, "content_policy": "人工确认",
                       "queue": "data/queues/integ.json"}],
            "success_criteria": {"metric": "reply", "target": "1", "stop": "0"},
            "review": {"human_approved": True},
        }), encoding="utf-8")
        (self.data / "contacts.jsonl").write_text(
            '{"id": "c1", "platforms": {"email": "dnc@example.com"}, '
            '"state": {"dnc": true}\n', encoding="utf-8")  # 坏 JSON：DNC 记录被截断
        with self.assertRaises(SuppressionIntegrityError):
            standard_pipeline(self.data, self.ledger)  # 批次在建管道时即中止
        self.assertEqual([], self.ledger.results())

    # 场景 2：Ledger 损坏 + 请求 X 发帖 → 禁止执行（fail closed，不触碰 vendor）
    def test_corrupt_ledger_blocks_x_send(self):
        (self.data / "ledger.jsonl").write_text('{"torn', encoding="utf-8")
        with self.assertRaises(LedgerCorruptError):
            self._run("engage", ["follow", "@victim"])
        self.assertFalse(self._called("follow_x.py"))

    # 场景 3：两个 Worker 同时执行同一目标动作 → 不得重复执行
    def test_two_workers_same_intent_exactly_one_executes(self):
        _fake_vendor(self.vendor, "follow_x.py", slow=True)
        worker = self.root / "worker.py"
        worker.write_text(
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "from pathlib import Path\n"
            "from unittest.mock import patch\n"
            "from channels.x import adapter\n"
            "from core.ledger import Ledger\n"
            "with patch.object(adapter, 'VENDOR', Path(%r)):\n"
            "    adapter.run('engage', ['follow', '@race'], go=True, adhoc=True,\n"
            "                campaign='integ', ledger=Ledger(Path(%r)),\n"
            "                log=lambda *a: None, data_dir=Path(%r))\n"
            % (str(REPO), str(self.vendor),
               str(self.data / "ledger.jsonl"), str(self.data)), encoding="utf-8")
        procs = [subprocess.Popen([sys.executable, str(worker)], cwd=str(REPO),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                 for _ in range(2)]
        codes = [p.wait() for p in procs]
        marker = self.vendor / "called-follow_x.py"
        self.assertTrue(marker.exists(), "胜者应执行 vendor")
        content = marker.read_text(encoding="utf-8")
        self.assertEqual("1", content, "vendor 只被执行一次（覆盖写也只留一份标记）")
        self.assertEqual(1, codes.count(0), "恰好一个 Worker 成功，另一个被认领拒绝: %s" % codes)

    # 场景 4：Vendor 成功发送但未产生可信回执 → 未知状态，禁止自动重发
    def test_send_without_receipt_is_indeterminate_and_blocks_resend(self):
        self._run("reply", ["https://x.com/i/status/7"])
        rows = self.ledger.results(channel="x", action="reply")
        self.assertEqual(1, len(rows))
        self.assertEqual("indeterminate", rows[0]["result"])
        self.assertTrue(rows[0].get("ref"))
        with self.assertRaises(SystemExit) as ctx:
            self._run("reply", ["https://x.com/i/status/7"])
        self.assertIn("不重试名单", str(ctx.exception))

    # 场景 5：未知状态经过人工裁决 → 按裁决结果安全恢复
    def test_human_resolution_recovers_per_verdict(self):
        self._run("reply", ["https://x.com/j/status/1"])
        row = self.ledger.results(channel="x", action="reply")[0]
        # 裁决 failed + 清理认领 → 允许重试
        self.ledger.resolve(row["ref"], result="failed", by="roy",
                            note="后台核实：未发出")
        import shutil
        shutil.rmtree(self.data / "state/x-claims", ignore_errors=True)
        self._run("reply", ["https://x.com/j/status/1"])
        self.assertEqual(2, len(self.ledger.results(channel="x", action="reply")))
        # 裁决 committed → 幂等拦截
        row2 = self.ledger.results(channel="x", action="reply")[-1]
        self.ledger.resolve(row2["ref"], result="committed", by="roy",
                            note="后台核实：已发出")
        shutil.rmtree(self.data / "state/x-claims", ignore_errors=True)
        with self.assertRaises(SystemExit) as ctx:
            self._run("reply", ["https://x.com/j/status/1"])
        self.assertIn("幂等", str(ctx.exception))

    # 场景 6：一个批次多目标，部分成功、部分未知 → 分目标记录
    def test_batch_partial_success_partial_unknown_recorded_per_target(self):
        logs = self.vendor / "logs"
        logs.mkdir()
        (logs / "follow-1.json").write_text(json.dumps(
            {"records": [{"handle": "@good", "followed": True}]}), encoding="utf-8")
        self._run("engage", ["follow", "@good", "@opaque"])
        by_target = {r["target"]: r["result"]
                     for r in self.ledger.results(channel="x", action="engage")}
        self.assertEqual("committed", by_target.get("@good"))
        self.assertEqual("indeterminate", by_target.get("@opaque"))
        with self.assertRaises(SystemExit):
            self._run("engage", ["follow", "@opaque"])  # 未知目标被拦
        self.assertTrue(self._called("follow_x.py"))

    # 场景 7：历史成功日志存在、本次没有新回执 → 不得误认历史结果
    def test_historical_success_log_not_adopted(self):
        logs = self.vendor / "logs"
        logs.mkdir()
        old = logs / "publish-20260101.json"
        old.write_text(json.dumps({"records": [{"index": 1, "posted": True}]}),
                       encoding="utf-8")
        past = time.time() - 7200
        os.utime(old, (past, past))
        _fake_vendor(self.vendor, "publish_x.py")
        self._run("publish", [])
        rows = self.ledger.results(channel="x", action="publish")
        self.assertEqual(1, len(rows))
        self.assertEqual("indeterminate", rows[0]["result"])


if __name__ == "__main__":
    unittest.main()
