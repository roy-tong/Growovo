#!/usr/bin/env python3
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.work_demo import run_work_demo


class TestWorkDemo(unittest.TestCase):
    def test_complete_demo_never_executes_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # The demo loads its input contract from installed core package data.
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run_work_demo(base)
            self.assertEqual(0, rc, buf.getvalue())
            for keyword in ("WorkOrder/SOP", "交付物与交接", "SOP 证据门", "KPI", "无发送账本"):
                self.assertIn(keyword, buf.getvalue())
            roots = list((base / "data/demo/work-os").iterdir())
            self.assertEqual(1, len(roots))
            data = roots[0]
            plan = json.loads((data / "plans/work-demo-action.json").read_text(encoding="utf-8"))
            self.assertTrue(plan["review"]["human_approved"])
            self.assertIn("provenance", plan)
            self.assertFalse((data / "ledger.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
