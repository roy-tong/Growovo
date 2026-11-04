"""日志扫描：从本地记录里提事实，带出处。"""
import tempfile
import unittest
from pathlib import Path

from core import logscan


SAMPLE = """# 9 月执行日志

2026-09-02 在 X 上做了 199 条冷触达，4 天 0 star，3 条回应。
2026-09-03 邮件 209 封求评式外联，6 条实质回复。
2026-09-04 GitHub 提了 77 次提交，其中 30 个被合并。
2026-09-05 Reddit 发帖被删除，属于 AutoModerator 判 AI。
"""


class LogScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "执行日志").mkdir()
        (self.root / "执行日志" / "2026-09-02-日志.md").write_text(SAMPLE, encoding="utf-8")
        (self.root / "notes.txt").write_text("无关内容\n", encoding="utf-8")

    def test_walk_skips_hidden_and_counts(self):
        files = logscan.walk(self.root)
        names = sorted(p.name for p in files)
        self.assertIn("2026-09-02-日志.md", names)
        inv = logscan.inventory(self.root)
        self.assertEqual(inv["files"], 2)

    def test_extract_pulls_channel_metric_with_provenance(self):
        p = self.root / "执行日志" / "2026-09-02-日志.md"
        claims = logscan.extract_claims(p, "执行日志/2026-09-02-日志.md")
        self.assertTrue(claims)
        for c in claims:
            self.assertIn("file", c)
            self.assertGreater(c["line"], 0)
            self.assertIn(c["channel"], logscan.CHANNELS)
            self.assertIn(c["metric"], ("action", "response", "outcome", "failure"))

        x = [c for c in claims if c["channel"] == "x"]
        self.assertTrue(any(c["value"] == 199 for c in x), "应抓到 X 的 199 条")

    def test_claims_carry_date(self):
        p = self.root / "执行日志" / "2026-09-02-日志.md"
        claims = logscan.extract_claims(p, "f.md")
        dated = [c for c in claims if c["date"]]
        self.assertTrue(dated)
        self.assertTrue(all(c["date"].startswith("2026-09") for c in dated))

    def test_scan_writes_json(self):
        out = self.root / "out.json"
        res = logscan.scan(self.root, out)
        self.assertGreater(res["claims"], 0)
        self.assertTrue(out.exists())
        data = logscan.load(out)
        self.assertIn("inventory", data)
        self.assertIn("claims", data)

    def test_binary_and_oversize_are_safe(self):
        big = self.root / "big.log"
        big.write_text("X 在 2026-09-02 做了 199 条触达\n" * 20000, encoding="utf-8")
        claims = logscan.extract_claims(big, "big.log")
        self.assertTrue(claims)  # 只读前 256KB，不崩


if __name__ == "__main__":
    unittest.main()
