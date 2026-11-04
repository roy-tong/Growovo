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

    def test_dry_run_without_records_stays_silent_not_indeterminate(self):
        """干跑不留结果文件时不记 indeterminate（dry-run 永不算已发送）。"""
        _fake_vendor(self.vendor, "reply_x.py")
        self._run("reply", ["https://x.com/c/status/3"], go=False)
        self.assertEqual(0, len(self.ledger.results(channel="x", action="reply")))


if __name__ == "__main__":
    unittest.main()
