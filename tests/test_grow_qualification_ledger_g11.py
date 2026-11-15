"""G-11 冻结：资格账本必须严格读取，损坏就不能再给出「已验证」计数。

先于修复编写。修复前 ``core.attribution`` 自己重读 qualifications.jsonl：
坏行直接 continue（等于把撤销记录当成不存在），``bool("false")`` 为真
（等于撤销失效），并且撤销集合缓存在实例上、外部追加后不失效。

冻结的契约：

1. 资格审计任一行不是合法 JSON → 业务漏斗拒绝给出已验证计数（抛错，不跳过）。
2. ``qualifies`` 不是真正的布尔（如字符串 "false"）→ 同样拒绝，不按真值猜。
3. 同一进程内先读一次再撤销，第二次读取必须反映撤销（缓存按文件状态失效）。
4. 正常数据下行为不变：撤销生效、恢复生效。
"""
import json
import tempfile
import unittest
from pathlib import Path

from core import outcome_layers
from core.attribution import LedgerReadError, OutcomeLedger
from core.execution_policy import authorize

ACTIONS = ["x:reply", "email:message"]


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        authorize(self.dir, by="tester", actions=ACTIONS, days=90)
        # 一条已核验付费 + 一条注册，直接写进导入与台账（不依赖 grow run）
        (self.dir / "outcomes").mkdir(parents=True, exist_ok=True)
        events = [{
            "event_id": "p-1", "event_type": "paid", "subject_id": "buyer-1",
            "occurred_at": "2026-11-03T10:00:00", "source_system": "product_export",
            "source_ref": "app:1", "campaign_id": "g1", "amount": "49.00",
            "currency": "USD", "payment_ref": "pi_1", "paid_at": "2026-11-03T09:00:00",
            "verified_by": "stripe",
            "evidence_hash": outcome_layers.evidence_hash_for({
                "event_id": "p-1", "event_type": "paid", "subject_id": "buyer-1",
                "source_system": "product_export", "source_ref": "app:1",
                "amount": "49.00", "currency": "USD", "payment_ref": "pi_1",
                "paid_at": "2026-11-03T09:00:00"}),
        }]
        payload = self.dir / "events.json"
        payload.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
        outcome_layers.import_events(self.dir, payload,
                                     allowed_sources={"product_export"},
                                     imported_by="tester")
        self.qual = self.dir / "outcomes" / "qualifications.jsonl"

    def _write_qual(self, rows):
        with self.qual.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _paid(self):
        return OutcomeLedger(self.dir).funnel("g1")


class StrictReadTests(LedgerCase):
    def test_corrupt_line_refuses_verified_counts(self):
        self._write_qual([{"event_id": "p-1", "qualifies": False, "by": "roy",
                           "note": "内部测试"}])
        with self.qual.open("a", encoding="utf-8") as fh:
            fh.write("{ broken\n")
        with self.assertRaises(LedgerReadError):
            self._paid()

    def test_non_boolean_qualifies_is_refused(self):
        self._write_qual([{"event_id": "p-1", "qualifies": "false", "by": "roy",
                           "note": "字符串 false 不能当真值"}])
        with self.assertRaises(LedgerReadError):
            self._paid()

    def test_valid_data_still_works(self):
        self._write_qual([{"event_id": "p-1", "qualifies": False, "by": "roy",
                           "note": "内部测试"}])
        payload = self._paid()
        paid = [r for r in payload["funnel"] if r["stage"] == "paid"][0]
        self.assertEqual(0, paid["subjects"])
        self.assertEqual(1, len(payload["disqualified"]))
        self._write_qual([{"event_id": "p-1", "qualifies": True, "by": "roy",
                           "note": "复核后算回"}])
        payload = self._paid()
        paid = [r for r in payload["funnel"] if r["stage"] == "paid"][0]
        self.assertEqual(1, paid["subjects"])


class CacheFreshnessTests(LedgerCase):
    def test_same_process_revocation_is_visible(self):
        ledger = OutcomeLedger(self.dir)
        before = ledger.funnel("g1")
        self.assertEqual(1, [r for r in before["funnel"] if r["stage"] == "paid"][0]["subjects"])
        outcome_layers.set_event_qualification(self.dir, "p-1", qualifies=False,
                                               by="roy", note="内部测试交易")
        after = ledger.funnel("g1")
        self.assertEqual(0, [r for r in after["funnel"] if r["stage"] == "paid"][0]["subjects"],
                         "长生命周期实例必须看到外部追加的撤销")


if __name__ == "__main__":
    unittest.main()
