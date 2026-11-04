"""单一数据源：所有环节必须看到同一批平台，否则就会各说各话。"""
import unittest

from core import execute, gtm, logscan, platforms, strategy


class PlatformSourceTests(unittest.TestCase):
    def test_required_fields_present(self):
        required = ("id", "name", "kind", "icp_tags", "product_types", "motions",
                    "account", "residency", "stages", "scale", "risks", "tier", "write")
        for p in platforms.PLATFORMS:
            for f in required:
                self.assertIn(f, p, f"{p.get('id')} 缺 {f}")

    def test_ids_unique(self):
        ids = [p["id"] for p in platforms.PLATFORMS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_five_cold_start_stages_everywhere(self):
        for p in platforms.PLATFORMS:
            self.assertEqual(len(p["stages"]), 5, p["id"])
            self.assertEqual([s[0] for s in p["stages"]][2], "被问到", p["id"])

    def test_every_stage_sees_every_platform(self):
        ids = {p["id"] for p in platforms.PLATFORMS}
        self.assertEqual({p["id"] for p in gtm.PLATFORMS}, ids, "诊断少了平台")
        self.assertEqual(set(strategy.PLAYBOOK), ids, "策略少了平台")
        self.assertEqual(set(logscan.CHANNELS), ids, "日志扫描少了平台")

    def test_writable_channels_are_real_subsets(self):
        ids = {p["id"] for p in platforms.PLATFORMS}
        self.assertTrue(set(execute.CHANNEL_ACTION) <= ids)
        # 没有写通道的平台不能出现在可自动执行里
        for pid in ids - set(execute.CHANNEL_ACTION):
            self.assertIsNone(platforms.BY_ID[pid]["write"], pid)

    def test_every_platform_has_motions_with_labels(self):
        for p in platforms.PLATFORMS:
            self.assertTrue(p["motions"], p["id"])
            for m in p["motions"]:
                self.assertIn(m["label"], gtm.LABELS, f"{p['id']} 动作标签非法")

    def test_coverage_is_wide(self):
        # 20 个以上才算"主流平台都覆盖了"
        self.assertGreaterEqual(len(platforms.PLATFORMS), 30)


if __name__ == "__main__":
    unittest.main()
