#!/usr/bin/env python3
"""mkt doctor 离线单测（W1 B4）：FAIL/WARN 分级、退出码、环境无关性。"""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import doctor
from core.safety import set_pause

BASE = Path(__file__).resolve().parents[1]


class TestDoctor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def _run(self, data=None, base=BASE):
        env = {k: v for k, v in os.environ.items() if not k.startswith("MKT_")}
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True):
            with contextlib.redirect_stdout(buf):
                rc = doctor.run_doctor(base, data or self.data)
        return rc, buf.getvalue()

    def test_fresh_env_no_fail(self):
        """全新环境（无凭据无 CDP）也必须 0 FAIL：可选项缺省只降级为 WARN。"""
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("内核导入", out)
        self.assertIn("WARN", out)  # SMTP 凭据未配置等

    def test_kernel_import_failure_exits_1(self):
        with mock.patch.object(doctor, "KERNEL_MODULES", ["no.such.module"]):
            rc, out = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("[FAIL] 内核导入", out)

    def test_pause_is_warn_not_fail(self):
        set_pause(self.data, True, "test")
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("[WARN] 全局暂停", out)
        set_pause(self.data, False)

    def test_missing_schemas_doc_warns(self):
        with tempfile.TemporaryDirectory() as empty:
            rc, out = self._run(base=Path(empty))
        self.assertEqual(rc, 0)
        self.assertIn("[WARN] 数据契约", out)


if __name__ == "__main__":
    unittest.main()
