#!/usr/bin/env python3
"""safety（暂停闸/报告）+ contacts（线索库）+ 限额校准 离线单测（06/07 文档对应）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.contacts import Contacts
from core.evidence import Evidence
from core.gates import Gate
from core.ledger import Ledger
from core.pipeline import WritePipeline
from core.safety import is_paused, pause_file, report, set_pause
from core.throttle import Throttle


class TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)


class TestPause(TmpDir):
    def test_pause_lifecycle(self):
        set_pause(self.dir, True, reason="事故演练")
        self.assertTrue(is_paused(self.dir))
        set_pause(self.dir, False)
        self.assertFalse(is_paused(self.dir))
        self.assertFalse(pause_file(self.dir).exists())  # off = 删除，无内容态歧义

    def test_pipeline_refuses_when_paused(self):
        led = Ledger(self.dir / "ledger.jsonl")
        pf = self.dir / "state" / "PAUSED"
        pf.parent.mkdir(parents=True)
        pf.write_text("演练\n", encoding="utf-8")
        pipe = WritePipeline(gate=Gate(led), ledger=led,
                             throttle=Throttle(self.dir / "s"), pause_file=pf)
        rec, calls = None, []

        def cb(name):
            def f(item, *a, **k):
                calls.append(name)
                return "hello"
        # 暂停时任何回调都不该被碰
        rec = pipe.run_one(
            {"id": "x"}, channel="email", action="message", campaign="c",
            transport="smtp", expect="hello", target="a@b.c",
            compose=cb("compose"), fill=cb("fill"), readback=cb("readback"),
            send=cb("send"), confirm=cb("confirm"), dry_run=False)
        self.assertEqual(rec["result"], "rejected")
        self.assertIn("暂停", rec["detail"])
        self.assertEqual(calls, [])
        self.assertEqual(len(led.all()), 1)  # 拒绝也留痕


class TestReport(TmpDir):
    def test_report_window_and_attention(self):
        led = Ledger(self.dir / "ledger.jsonl")
        led.append(channel="email", action="message", target="a@b.c",
                   result="indeterminate", campaign="c1", detail="未确认")
        led.append(channel="x", action="publish", target="tweet:1",
                   result="committed", campaign="c1")
        rep = report(self.dir, led)
        self.assertEqual(rep["total_records"], 2)
        self.assertEqual(len(rep["needs_attention"]), 1)
        self.assertIn("c1", rep["campaigns"])
        self.assertFalse(rep["paused"])


class TestContacts(TmpDir):
    def test_add_find_dedupe(self):
        c = Contacts(self.dir / "contacts.jsonl")
        c.add(name="Sam", org="Acme", platforms={"email": "s@acme.io", "x": "@sam"},
              source="test", tier=1)
        with self.assertRaises(ValueError):  # email 撞车 → 拒绝
            c.add(name="Sam2", platforms={"email": "s@acme.io"})
        rows = c.find(platform="x", value="@sam")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "contact-0001")
        self.assertEqual(c.find(text="acme"), rows)

    def test_dnc_hidden_and_mark(self):
        c = Contacts(self.dir / "contacts.jsonl")
        c.add(name="A", platforms={"email": "a@x.io"})
        cid = c.all()[0]["id"]
        c.mark(cid, dnc=True)
        self.assertEqual(c.find(text="a@x.io"), [])          # 默认隐藏 DNC
        self.assertEqual(len(c.find(text="a@x.io", include_dnc=True)), 1)
        c.mark(cid, replies="+1")
        self.assertEqual(c.all()[0]["state"]["replies"], 1)

    def test_import_er_batch(self):
        c = Contacts(self.dir / "contacts.jsonl")
        src = self.dir / "er.json"
        src.write_text(json.dumps([
            {"email": "p@q.io", "name": "P", "org": "Q", "email_status": "verified",
             "verification_source": "github-public-commit"},
            {"email": "p@q.io", "name": "P dup"},   # 重复应跳过
        ]), encoding="utf-8")
        out = c.import_legacy(src)
        self.assertEqual(out, {"added": 1, "skipped_duplicate": 1})
        self.assertEqual(c.all()[0]["tier"], 1)     # verified → tier1


class TestBudgetCalibration(TmpDir):
    """06 文档口径：X 未验证账号官方 50 原创/日 → 我们的预算 40（留余量）。"""

    def test_x_publish_budget_below_official_ceiling(self):
        th = Throttle(self.dir)
        lo, hi, run_cap, daily = th.policy("x", "publish")
        self.assertEqual(daily, 40)
        self.assertLess(daily, 50)  # 官方上限（未验证档）
        self.assertEqual(th.policy("x", "reply")[3], 40)

    def test_email_budget_within_smtp_consensus(self):
        th = Throttle(self.dir)
        self.assertLessEqual(th.policy("email", "message")[3], 100)  # Gmail SMTP 社区共识线


if __name__ == "__main__":
    unittest.main()
