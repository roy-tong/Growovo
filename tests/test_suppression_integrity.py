#!/usr/bin/env python3
"""抑制源完整性：DNC 数据不可靠读取时 fail closed，不得当作"无人需要抑制"。

冻结失败样例（review 2026-11-04，G-4）：load_contact_suppressions 对坏行/
坏 schema 静默 continue——恰好损坏的那条 DNC 记录会被忽略，发送门禁看不
到该联系人应被禁止触达。修复后：contacts.jsonl 存在但不可可靠解析时抛
SuppressionIntegrityError，受影响的发送批次中止（文件缺失仍=空集，属正
常未配置）。
"""
import json
import tempfile
import unittest
from pathlib import Path

from core.gates import (Gate, GateError, SuppressionIntegrityError,
                        load_contact_suppressions, load_suppressions)


class SuppressionIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_corrupt_line_with_dnc_record_raises(self):
        """坏行旁边的 DNC 记录不得因坏行被静默丢弃。"""
        (self.dir / "contacts.jsonl").write_text(json.dumps({
            "id": "c1", "platforms": {"email": "dnc@example.com"},
            "state": {"dnc": True},
        }) + "\n" + '{"id": "c2", "platforms": {"em', encoding="utf-8")
        with self.assertRaises(SuppressionIntegrityError):
            load_contact_suppressions(self.dir)

    def test_non_dict_state_raises_not_silently_drops_dnc(self):
        (self.dir / "contacts.jsonl").write_text(json.dumps({
            "id": "c1", "platforms": {"email": "dnc@example.com"},
            "state": "dnc",
        }) + "\n", encoding="utf-8")
        with self.assertRaises(SuppressionIntegrityError):
            load_contact_suppressions(self.dir)

    def test_non_dict_platforms_raises(self):
        (self.dir / "contacts.jsonl").write_text(json.dumps({
            "id": "c1", "platforms": ["dnc@example.com"],
            "state": {"dnc": True},
        }) + "\n", encoding="utf-8")
        with self.assertRaises(SuppressionIntegrityError):
            load_contact_suppressions(self.dir)

    def test_missing_file_is_empty_and_valid_contacts_still_work(self):
        self.assertEqual(load_contact_suppressions(self.dir), set())
        (self.dir / "contacts.jsonl").write_text(json.dumps({
            "id": "c1", "platforms": {"email": "dnc@example.com"},
            "state": {"dnc": True},
        }) + "\n", encoding="utf-8")
        gate = Gate(suppress=load_suppressions(self.dir))
        with self.assertRaises(GateError):
            gate.check(channel="email", action="message", target="dnc@example.com",
                       campaign="c1", content="hello")

    def test_corrupt_contacts_block_send_path_fail_closed(self):
        """precheck 依赖抑制源：不可靠时发送前置检查必须失败而不是放行。"""
        (self.dir / "contacts.jsonl").write_text('{"id": "c1", "state": {"dn',
                                                  encoding="utf-8")
        from core.action_runner import ActionError, precheck
        with self.assertRaises((SuppressionIntegrityError, ActionError)):
            precheck("email", "message", self.dir, "c1", True, False,
                     target="anyone@example.com")


if __name__ == "__main__":
    unittest.main()
