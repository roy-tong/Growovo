#!/usr/bin/env python3
"""X 渠道与统一账本门禁：幂等/不重试前置 + go 实跑零记账必须落 indeterminate。

冻结失败样例（review 2026-11-04，G-2）：X 适配器直接 subprocess 调 vendor，
发送前不查 already/no_retry；go 实跑后若 vendor 结果不可判读，账本一条记录
都不留——外部动作已发生而系统无可靠记录，重跑同一目标即重复发送。
完整的 Reserve/Verify 执行事务属第二层；本层先钉住：
- 已 committed 的目标拒绝再次实跑（跨 campaign 幂等）
- indeterminate/no-dm 目标拒绝自动重试
- go 实跑零回写 → 记 indeterminate → 之后同目标被 no_retry 拦截
All vendor boundaries are fakes; no network or browser operations.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from channels.x import adapter
from core.ledger import Ledger


def _fake_vendor(dirpath: Path, script: str, *, rc: int = 0, touch: str = ""):
    """写一个假 vendor 脚本：记录被调用、可选写结果文件、返回 rc。"""
    (dirpath / script).write_text(
        "import sys, pathlib\n"
        "pathlib.Path(__file__).parent.joinpath('called-%s').write_text('1')\n" % script
        + ("pathlib.Path(%r).write_text('{}')\n" % touch if touch else "")
        + "sys.exit(%d)\n" % rc, encoding="utf-8")


class XLedgerGates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.vendor = root / "vendor"
        self.vendor.mkdir()
        self.data = root / "data"
        (self.data / "plans").mkdir(parents=True)
        self.ledger = Ledger(self.data / "ledger.jsonl")
        _fake_vendor(self.vendor, "follow_x.py")

    def _run(self, *args, **kw):
        defaults = dict(go=True, adhoc=True, campaign="c1",
                        ledger=self.ledger, log=lambda *a: None,
                        data_dir=self.data)
        defaults.update(kw)
        with patch.object(adapter, "VENDOR", self.vendor):
            return adapter.run(*args, **defaults)

    def _vendor_called(self, script):
        return (self.vendor / ("called-" + script)).exists()

    def test_already_committed_target_blocks_resend(self):
        self.ledger.append(channel="x", action="engage", target="@foo",
                           result="committed", campaign="older-campaign")
        with self.assertRaises(SystemExit) as ctx:
            self._run("engage", ["follow", "@foo"])
        self.assertIn("幂等", str(ctx.exception))
        self.assertFalse(self._vendor_called("follow_x.py"),
                         "已 committed 的目标不得再触碰 vendor")

    def test_indeterminate_target_blocks_auto_retry(self):
        self.ledger.append(channel="x", action="reply",
                           target="https://x.com/a/status/1",
                           result="indeterminate", campaign="c1")
        with self.assertRaises(SystemExit) as ctx:
            self._run("reply", ["https://x.com/a/status/1"])
        self.assertIn("不重试名单", str(ctx.exception))
        self.assertFalse(self._vendor_called("reply_x.py"))

    def test_go_run_with_zero_ledger_rows_records_indeterminate_and_blocks_rerun(self):
        """vendor rc=0 但无可判读结果：必须留 indeterminate，且重跑被拦。"""
        _fake_vendor(self.vendor, "reply_x.py")
        self._run("reply", ["https://x.com/b/status/2"])
        rows = self.ledger.results(channel="x", action="reply")
        self.assertEqual(1, len(rows))
        self.assertEqual("indeterminate", rows[0]["result"])
        self.assertIn("禁止自动重发", rows[0].get("detail", ""))
        with self.assertRaises(SystemExit) as ctx:
            self._run("reply", ["https://x.com/b/status/2"])
        self.assertIn("不重试名单", str(ctx.exception))

    def _claims_dir(self):
        return self.data / "state" / "x-claims"

    def _remove_claims(self):
        import shutil as _shutil
        if self._claims_dir().exists():
            _shutil.rmtree(self._claims_dir())

    def test_indeterminate_rows_carry_resolvable_refs_resolve_failed_recovers(self):
        """G-2.1：indeterminate 必须带非空 ref，可被 ledger.resolve 裁决；
        裁决为 failed 且人工清理 claim 后，同一意图允许再次执行。"""
        _fake_vendor(self.vendor, "reply_x.py")
        self._run("reply", ["https://x.com/c/status/9"])
        rows = self.ledger.results(channel="x", action="reply")
        self.assertEqual(1, len(rows))
        ref = rows[0].get("ref")
        self.assertTrue(ref, "indeterminate 记录必须带非空 ref，否则无法人工裁决")
        self.ledger.resolve(ref, result="failed", by="roy",
                            note="平台后台核实：发送前失败，未发出")
        self._remove_claims()  # 人工确认无外部动作后清理认领
        self._run("reply", ["https://x.com/c/status/9"])  # 裁决 failed + 认领清理 → 允许重试
        self.assertTrue(self._vendor_called("reply_x.py"))

    def test_resolve_committed_keeps_idempotent_block(self):
        _fake_vendor(self.vendor, "reply_x.py")
        self._run("reply", ["https://x.com/d/status/1"])
        row = self.ledger.results(channel="x", action="reply")[0]
        self.ledger.resolve(row["ref"], result="committed", by="roy",
                             note="平台后台核实：已发出")
        self._remove_claims()
        with self.assertRaises(SystemExit) as ctx:
            self._run("reply", ["https://x.com/d/status/1"])
        self.assertIn("幂等", str(ctx.exception))

    def test_multi_target_batch_records_per_target_not_joined(self):
        """G-2.2：批量动作逐目标记账；字符串拼接目标会让逐目标防重失效。"""
        self._run("engage", ["follow", "@user_a", "@user_b"])
        rows = self.ledger.results(channel="x", action="engage")
        self.assertEqual(2, len(rows))
        self.assertEqual({"@user_a", "@user_b"}, {r["target"] for r in rows})
        self.assertFalse(any("," in r["target"] for r in rows),
                         "不得用逗号拼接的聚合目标冒充逐目标记录")
        self.assertTrue(all(r.get("ref") for r in rows))
        with self.assertRaises(SystemExit) as ctx:
            self._run("engage", ["follow", "@user_a"])
        self.assertIn("不重试名单", str(ctx.exception))

    def test_atomic_claim_blocks_second_worker_on_same_intent(self):
        """G-2.3：check-then-send 竞态由原子认领关闭；账本干净也拦。"""
        _fake_vendor(self.vendor, "follow_x.py")
        self._run("engage", ["follow", "@race"])   # 产生未知状态 → 认领保留
        self.assertTrue(any(self._claims_dir().iterdir()))
        # 模拟另一 Worker：账本被清空（仍见"未发送"），但认领存在
        (self.data / "ledger.jsonl").unlink()
        self.ledger = Ledger(self.data / "ledger.jsonl")
        with self.assertRaises(SystemExit) as ctx:
            self._run("engage", ["follow", "@race"])
        self.assertIn("执行认领失败", str(ctx.exception))

    def test_claim_released_after_fully_decided_run(self):
        """全部可判定并落账 → 认领自动释放；后续拦截由账本幂等承担。"""
        logs = self.vendor / "logs"
        logs.mkdir()
        (logs / "follow-20261104.json").write_text(json.dumps(
            {"records": [{"handle": "@ok1", "followed": True}]}), encoding="utf-8")
        _fake_vendor(self.vendor, "follow_x.py")
        self._run("engage", ["follow", "@ok1"])
        rows = self.ledger.results(channel="x", action="engage")
        self.assertEqual(1, len(rows))
        self.assertEqual("committed", rows[0]["result"])
        self.assertFalse(any(self._claims_dir().iterdir()) if self._claims_dir().exists() else False,
                         "全部可判定后认领应释放")
        with self.assertRaises(SystemExit) as ctx:
            self._run("engage", ["follow", "@ok1"])
        self.assertIn("幂等", str(ctx.exception))

    def test_history_log_not_adopted_as_this_run_result(self):
        """vendor 目录留有历史成功日志、本次无新回执 → 不得误判为 committed。"""
        import os as _os
        logs = self.vendor / "logs"
        logs.mkdir()
        old = logs / "publish-20261001.json"
        old.write_text(json.dumps({"records": [{"index": 1, "posted": True}]}),
                       encoding="utf-8")
        past = time.time() - 3600
        _os.utime(old, (past, past))
        _fake_vendor(self.vendor, "publish_x.py")
        self._run("publish", [])
        rows = self.ledger.results(channel="x", action="publish")
        self.assertEqual(1, len(rows))
        self.assertEqual("indeterminate", rows[0]["result"],
                         "历史成功日志不得被当作本次结果")

    def test_partial_unknown_per_target_engage(self):
        """一批多目标部分成功部分未知：分目标记录，一条成功不得掩盖其余未知。"""
        logs = self.vendor / "logs"
        logs.mkdir()
        (logs / "follow-20261104b.json").write_text(json.dumps(
            {"records": [{"handle": "@good", "followed": True}]}), encoding="utf-8")
        _fake_vendor(self.vendor, "follow_x.py")
        self._run("engage", ["follow", "@good", "@opaque"])
        by_target = {r["target"]: r["result"] for r in
                     self.ledger.results(channel="x", action="engage")}
        self.assertEqual("committed", by_target.get("@good"))
        self.assertEqual("indeterminate", by_target.get("@opaque"))

    def test_dry_run_without_records_stays_silent_not_indeterminate(self):
        """干跑不留结果文件时不记 indeterminate（dry-run 永不算已发送）。"""
        _fake_vendor(self.vendor, "reply_x.py")
        self._run("reply", ["https://x.com/c/status/3"], go=False)
        self.assertEqual(0, len(self.ledger.results(channel="x", action="reply")))


if __name__ == "__main__":
    unittest.main()
