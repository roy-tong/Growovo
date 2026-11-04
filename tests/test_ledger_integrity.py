#!/usr/bin/env python3
"""账本完整性：坏行 fail-loud、并发追加不交错、读失败=发送门禁 fail closed。

冻结失败样例（review 2026-11-04，G-3）：ledger.jsonl 是全部防重发逻辑的
根基，但写入无锁、坏行静默跳过——一条已 committed 的记录因撕裂写丢失后，
already() 返回 False，重跑队列即重复发送且无任何告警。修复后：
- 坏行必须显式报错（LedgerCorruptError），禁止以"少一行"伪装正常；
- 追加用 O_APPEND + flock + 单次 write，与 event_store 同一纪律；
- 账本不可读时发送门禁拒绝动作（fail closed），不静默放行。
All boundaries are local files; no network operations.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from core.gates import Gate
from core.ledger import Ledger, LedgerCorruptError

REPO = Path(__file__).resolve().parents[1]


class LedgerIntegrity(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "ledger.jsonl"

    def test_torn_line_fails_loud_instead_of_silent_skip(self):
        """坏行不得被静默丢弃：丢一条 committed 就会重复发送。"""
        self.path.write_text(json.dumps({
            "channel": "email", "action": "message", "target": "a@example.com",
            "result": "committed"}) + "\n" + '{"ts": "2026-11-04T00:00:00", "chan',
            encoding="utf-8")
        with self.assertRaises(LedgerCorruptError):
            Ledger(self.path).all()

    def test_corrupt_ledger_fails_closed_in_send_gate(self):
        """账本不可读时，防重发门禁必须拒绝而不是当作'没发过'。"""
        self.path.write_text('{"torn": "x"\n', encoding="utf-8")
        gate = Gate(Ledger(self.path), suppress=set())
        with self.assertRaises(Exception):
            gate.check(channel="x", action="publish", target="tweet:1",
                       campaign="c1")

    def test_resolutions_corrupt_line_fails_loud(self):
        self.path.write_text(json.dumps({
            "channel": "x", "action": "publish", "target": "t:1",
            "result": "indeterminate", "ref": "r1"}) + "\n", encoding="utf-8")
        (self.dir / "ledger_resolutions.jsonl").write_text(
            '{"ref": "r1", "result": "comm', encoding="utf-8")
        with self.assertRaises(LedgerCorruptError):
            Ledger(self.path).all()

    def test_concurrent_append_never_interleaves(self):
        """两个进程并发追加，最终每行都必须是完整 JSON（无撕裂/交错）。"""
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from core.ledger import Ledger\n"
            "led = Ledger(%r)\n"
            "for i in range(200):\n"
            "    led.append(channel='email', action='message',\n"
            "                target='p%%d@x.test' %% i, result='committed',\n"
            "                campaign='stress')\n"
        ) % (str(REPO), str(self.path))
        procs = [subprocess.Popen([sys.executable, "-c", script],
                                  cwd=str(REPO)) for _ in range(2)]
        for p in procs:
            self.assertEqual(0, p.wait())
        rows = Ledger(self.path).all()
        self.assertEqual(400, len(rows))
        self.assertTrue(all(r.get("result") == "committed" for r in rows))


if __name__ == "__main__":
    unittest.main()
