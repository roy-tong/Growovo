#!/usr/bin/env python3
"""G-6（review 2026-11-09）：账本读侧 stat 键控缓存的不变量。

冻结放大样例：此前一次 Gate.check = no_retry+already 各自 results()→all()，
即每条队列项 4 次全文件解析（账本+裁决文件 ×2），lint 与 run 两阶段、X
preflight 再放大。修复后 all() 以 (mtime_ns, size) 为键复用解析结果，同时保
证：自己进程刚 append 的 committed 行对下一条防重检查立即可见。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import ledger as ledger_mod  # noqa: E402
from core.gates import Gate  # noqa: E402
from core.ledger import Ledger  # noqa: E402


def _ledger_with_rows(tmp: Path) -> Ledger:
    led = Ledger(tmp / "ledger.jsonl")
    led.append(channel="email", action="message", target="a@b.c",
               result="committed", transport="smtp", campaign="c1")
    led.append(channel="email", action="message", target="d@e.f",
               result="failed", transport="smtp", campaign="c1")
    return led


class LedgerReadCache(unittest.TestCase):
    def test_all_reuses_parse_until_append(self):
        with tempfile.TemporaryDirectory() as d:
            led = _ledger_with_rows(Path(d))
            with mock.patch.object(
                    ledger_mod, "_read_jsonl_fail_loud",
                    side_effect=ledger_mod._read_jsonl_fail_loud) as reader:
                for _ in range(5):
                    led.all()
                first = reader.call_count
                self.assertLessEqual(first, 2, first)  # 账本+裁决各一次，无重复
                led.append(channel="email", action="message", target="g@h.i",
                           result="committed", transport="smtp", campaign="c1")
                rows = led.all()
                self.assertGreater(reader.call_count, first)  # 追加后失效重读
                self.assertEqual(3, len(rows))

    def test_gate_checks_do_not_reparse_per_item(self):
        with tempfile.TemporaryDirectory() as d:
            led = _ledger_with_rows(Path(d))
            gate = Gate(ledger=led, suppress=set())
            gate.check(channel="email", action="message", target="x@y.z",
                       campaign="c2")  # 首次：建立视图
            with mock.patch.object(
                    ledger_mod, "_read_jsonl_fail_loud",
                    side_effect=ledger_mod._read_jsonl_fail_loud) as reader:
                for i in range(5):
                    gate.check(channel="email", action="message",
                               target=f"t{i}@y.z", campaign="c2")
                self.assertEqual(0, reader.call_count, reader.call_count)

    def test_dedupe_sees_own_process_append(self):
        with tempfile.TemporaryDirectory() as d:
            led = _ledger_with_rows(Path(d))
            gate = Gate(ledger=led, suppress=set())
            # a@b.c 已 committed：防重立即拒绝（缓存不得挡住新事实）
            with self.assertRaises(Exception):
                gate.check(channel="email", action="message",
                           target="a@b.c", campaign="c1")
            # 新写入同样立即可见
            led.append(channel="email", action="message", target="new@x.y",
                       result="committed", transport="smtp", campaign="c9")
            with self.assertRaises(Exception):
                gate.check(channel="email", action="message",
                           target="new@x.y", campaign="c9")


if __name__ == "__main__":
    unittest.main()
