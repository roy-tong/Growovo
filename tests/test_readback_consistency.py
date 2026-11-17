#!/usr/bin/env python3
"""回读一致性核验：动作后回读 ID/URL/内容哈希与预期比对（评审 §4.2/§7 P2）。

- 内容哈希不一致 → indeterminate 停机（复用 unknown 回执机制），禁止自动重发；
- 一致 → committed 保持，回执带 readback_sha256；
- 只有 URL/ID、无内容哈希 → 不升级也不降级（内容维度缺省，不算核验通过）。
"""
import json
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

from channels.x import adapter
from core import operations_log
from core.ledger import Ledger, NO_RETRY_CODES


def _fake_vendor(dirpath: Path, script: str, *, result: str = ""):
    stamp = int(time.time())
    body = (
        "import sys, pathlib\n"
        "pathlib.Path(__file__).parent.joinpath('called').write_text('1')\n"
    )
    if result:
        body += (
            "logs = pathlib.Path(__file__).parent / 'logs'\n"
            "logs.mkdir(exist_ok=True)\n"
            "(logs / ('reply-%d.json' %% %d)).write_text(%r, encoding='utf-8')\n"
            % (stamp, stamp, result)
        )
    body += "sys.exit(0)\n"
    (dirpath / script).write_text(body, encoding="utf-8")


class ReadbackFinalizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.log = operations_log.OperationsLog(self.dir)

    def _entry(self, **over):
        text = "回读验收内容"
        base = dict(
            operation_id="op-rb-1", run_id="", queue_item_id="",
            channel="x", action="reply", target="https://x.com/a/status/1",
            content_sha256=operations_log.content_sha256(text),
            browser_context_ref="cdp:http://127.0.0.1:9223",
            account={"platform_user_id": "123", "handle": "acc",
                     "observed_at": "2026-11-17T09:00:00+00:00",
                     "proof_ref": "x:123"},
            operator="test", approval_ref="routine-policy",
            policy_sha256="a" * 64,
            started_at="2026-11-17T09:00:01+00:00",
            finished_at="2026-11-17T09:00:02+00:00", result="committed",
            receipt={"id": "1", "url": "https://x.com/a/status/2",
                     "readback_sha256": operations_log.content_sha256(text)},
            artifacts=[], error=None)
        base.update(over)
        return base

    def test_matching_readback_keeps_committed(self):
        entry = self.log.append(self._entry())
        self.assertEqual("committed", entry["result"])
        self.assertIsNone(entry["error"])

    def test_mismatched_readback_becomes_indeterminate_and_halt(self):
        entry = self.log.append(self._entry(
            receipt={"id": "1", "url": "https://x.com/a/status/2",
                     "readback_sha256":
                         operations_log.content_sha256("被平台改写的内容")}))
        self.assertEqual("indeterminate", entry["result"],
                         "回读内容哈希不一致必须降级为 indeterminate")
        self.assertEqual("readback_mismatch", entry["error"]["code"])
        self.assertFalse(entry["error"]["retryable"],
                         "回读不一致禁止自动重发")
        self.assertTrue(entry["error"]["recovery_ref"],
                        "必须给出人工恢复入口")
        self.assertTrue(entry.get("halted"))

    def test_status_mismatched_without_hash_also_halts(self):
        entry = self.log.append(self._entry(
            readback={"status": "mismatched", "source": "cdp", "note": ""},
            receipt={"id": "1", "url": "", "readback_sha256": ""}))
        self.assertEqual("indeterminate", entry["result"])
        self.assertEqual("readback_mismatch", entry["error"]["code"])

    def test_indeterminate_is_no_retry(self):
        """操作日志判定的 indeterminate 与账本 no_retry 语义一致。"""
        entry = self.log.append(self._entry(
            receipt={"id": "1", "url": "", "readback_sha256":
                     operations_log.content_sha256("别的")}))
        self.assertIn(entry["result"], NO_RETRY_CODES)


class AdapterReadbackTests(unittest.TestCase):
    """X 适配器：vendor 结果带 readback 时映射回执并核对内容哈希。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vendor = self.root / "vendor"
        self.vendor.mkdir()
        self.data = self.root / "data"
        (self.data / "plans").mkdir(parents=True)
        self.ledger = Ledger(self.data / "ledger.jsonl")

    def _run(self, text, readback):
        rec = {"url": "https://x.com/a/status/1", "posted": True,
               "submitted": True}
        if readback is not None:
            rec["readback"] = readback
        _fake_vendor(self.vendor, "reply_x.py",
                     result=json.dumps({"record": rec}))
        with unittest.mock.patch.object(adapter, "VENDOR", self.vendor):
            rc = adapter.run(
                "reply", ["--tweet-url", "https://x.com/a/status/1",
                          "--text", text],
                go=True, adhoc=True, campaign="rb1", ledger=self.ledger,
                data_dir=self.data, log=lambda *a, **k: None)
        self.assertEqual(0, rc)
        return self.ledger.results(channel="x", campaign="rb1")[-1]

    def test_matching_readback_records_receipt(self):
        text = "适配器回读验收"
        row = self._run(text, {"url": "https://x.com/a/status/9",
                               "id": "1790",
                               "sha256": operations_log.content_sha256(text)})
        self.assertEqual("committed", row["result"])
        receipt = row.get("receipt") or {}
        self.assertEqual("1790", receipt.get("id"))
        self.assertEqual("https://x.com/a/status/9", receipt.get("url"))
        self.assertEqual(operations_log.content_sha256(text),
                         receipt.get("readback_sha256"))

    def test_mismatched_readback_is_indeterminate(self):
        text = "适配器回读验收"
        row = self._run(text, {"url": "https://x.com/a/status/9", "id": "1790",
                               "sha256": operations_log.content_sha256(
                                   "平台上出现的却是这段")})
        self.assertEqual("indeterminate", row["result"],
                         "回读哈希不一致必须走 unknown 回执机制")
        self.assertIn("readback", str(row.get("detail", "")))
        # no_retry 生效：同目标后续实跑被拒
        with unittest.mock.patch.object(adapter, "VENDOR", self.vendor), \
                self.assertRaises(SystemExit):
            adapter.run("reply",
                        ["--tweet-url", "https://x.com/a/status/1",
                         "--text", text],
                        go=True, adhoc=True, campaign="rb1",
                        ledger=self.ledger, data_dir=self.data,
                        log=lambda *a, **k: None)


if __name__ == "__main__":
    unittest.main()
