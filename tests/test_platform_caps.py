"""平台自动执行白名单的单元测试。

判定口径以 docs/grow-v2-实现契约.md 第 1 节为准：
AUTO 必须三件事同时成立（注册表已验证 + 有写原语 + 队列支持该动作），
第一期 AUTO 集合恰为 {x, email}；GATED 是有写通道但未实测的渠道；
其余平台一律 MANUAL，未知渠道也不假装支持。
"""
import unittest

from channels import REGISTRY
from core import platform_caps as caps
from core.platforms import BY_ID, WRITE
from core.queue_runner import QUEUE_ACTIONS

REQUIRED_KEYS = {"channel", "status", "tier", "write", "actions", "reason"}


class AutoChannelTests(unittest.TestCase):
    """第一期能全自动执行的渠道。"""

    def test_auto_channels_exactly_x_and_email(self):
        # 顺序也要固定：x 在前、email 在后（注册表顺序）
        self.assertEqual(caps.auto_channels(), ["x", "email"])

    def test_auto_set_is_exactly_x_email(self):
        self.assertEqual(set(caps.auto_channels()), {"x", "email"})

    def test_is_auto(self):
        self.assertTrue(caps.is_auto("x"))
        self.assertTrue(caps.is_auto("email"))

    def test_auto_requires_supported_write_action(self):
        for channel in caps.auto_channels():
            row = caps.classify(channel)
            self.assertEqual(row["tier"], caps.AUTO)
            self.assertIn(row["status"], {"PROVEN", "PORTED"})
            self.assertIsNotNone(row["write"])
            self.assertIn(row["write"][1], QUEUE_ACTIONS)

    def test_auto_constants(self):
        self.assertEqual((caps.AUTO, caps.GATED, caps.MANUAL), ("auto", "gated", "manual"))


class GatedChannelTests(unittest.TestCase):
    """有写通道、但选择器/流程未实测的渠道，必须人工放行。"""

    def test_gated_contains_expected_channels(self):
        gated = set(caps.gated_channels())
        self.assertTrue({"github", "reddit", "hn", "linkedin"} <= gated,
                        f"gated 集合缺少渠道: {gated}")

    def test_gated_channels_have_write_but_unverified(self):
        for channel in caps.gated_channels():
            self.assertFalse(caps.is_auto(channel))
        # 判定以平台行为准（hn 的平台 id 是 hackernews）
        for row in caps.matrix():
            if row["tier"] != caps.GATED:
                continue
            with self.subTest(platform=row["platform"]):
                self.assertEqual(row["status"], "UNVERIFIED")
                self.assertIsNotNone(row["write"])
                self.assertIn(row["write"][1], QUEUE_ACTIONS)
                self.assertIn(row["platform"], WRITE)

    def test_hackernews_platform_is_gated(self):
        # 平台 id 是 hackernews，内核渠道名是 hn
        row = caps.classify("hackernews")
        self.assertEqual(row["tier"], caps.GATED)
        self.assertEqual(row["channel"], "hn")
        self.assertEqual(row["platform"], "hackernews")
        self.assertEqual(row["status"], REGISTRY["hn"]["status"])

    def test_gated_never_auto(self):
        for channel in caps.gated_channels():
            self.assertNotEqual(caps.classify(channel)["tier"], caps.AUTO)


class ManualChannelTests(unittest.TestCase):
    """没有自动写原语的平台，只能出人工作业单。"""

    def test_zhihu_is_manual(self):
        row = caps.classify("zhihu")
        self.assertEqual(row["tier"], caps.MANUAL)
        self.assertIsNone(row["write"])
        self.assertFalse(caps.is_auto("zhihu"))

    def test_unknown_channel_is_manual(self):
        for name in ("myspace", "not-a-real-channel", ""):
            with self.subTest(name=name):
                self.assertEqual(caps.classify(name)["tier"], caps.MANUAL)
                self.assertFalse(caps.is_auto(name))

    def test_platform_without_write_is_manual(self):
        for pid in BY_ID:
            if pid not in WRITE:
                with self.subTest(pid=pid):
                    self.assertEqual(caps.classify(pid)["tier"], caps.MANUAL)


class ClassifyShapeTests(unittest.TestCase):
    """classify 的返回字段与证据口径。"""

    def test_required_keys_present(self):
        for channel in ("x", "email", "github", "reddit", "hn", "linkedin",
                        "hackernews", "zhihu", "myspace"):
            with self.subTest(channel=channel):
                row = caps.classify(channel)
                self.assertTrue(REQUIRED_KEYS <= set(row), f"缺少字段: {REQUIRED_KEYS - set(row)}")
                self.assertIn(row["tier"], {caps.AUTO, caps.GATED, caps.MANUAL})
                self.assertIsInstance(row["actions"], list)
                self.assertIsInstance(row["reason"], str)
                self.assertTrue(row["reason"].strip(), "reason 不能为空")

    def test_write_shape(self):
        row = caps.classify("x")
        self.assertEqual(tuple(row["write"]), WRITE["x"])
        self.assertEqual(row["write"][1], "reply")   # x 的 primary action
        self.assertEqual(caps.classify("email")["write"][1], "message")

    def test_reason_plain_language_no_placeholder(self):
        for channel in ("x", "email", "github", "zhihu", "myspace"):
            reason = caps.classify(channel)["reason"]
            self.assertNotIn("{", reason)
            self.assertNotIn("TODO", reason)


class AssertAutoTests(unittest.TestCase):
    """assert_auto 是自动执行入口的准入检查。"""

    def test_accepts_auto_channels(self):
        caps.assert_auto(["x", "email"])  # 不抛错即通过
        caps.assert_auto([])

    def test_rejects_zhihu(self):
        with self.assertRaises(caps.PlatformError):
            caps.assert_auto(["zhihu"])

    def test_rejects_mixed_list(self):
        with self.assertRaises(caps.PlatformError):
            caps.assert_auto(["x", "github", "email"])

    def test_error_is_module_error(self):
        self.assertTrue(issubclass(caps.PlatformError, Exception))


class MatrixTests(unittest.TestCase):
    """matrix 覆盖全部平台，且 auto → gated → manual 排序稳定。"""

    def test_covers_every_platform(self):
        rows = caps.matrix()
        self.assertEqual(len(rows), len(BY_ID))
        self.assertEqual({row["platform"] for row in rows}, set(BY_ID))

    def test_tier_order(self):
        rank = {caps.AUTO: 0, caps.GATED: 1, caps.MANUAL: 2}
        ranks = [rank[row["tier"]] for row in caps.matrix()]
        self.assertEqual(ranks, sorted(ranks), "matrix 未按 auto→gated→manual 排序")

    def test_stable(self):
        first = caps.matrix()
        second = caps.matrix()
        self.assertEqual(
            [(r["platform"], r["tier"], r["channel"]) for r in first],
            [(r["platform"], r["tier"], r["channel"]) for r in second],
        )

    def test_auto_rows_match_auto_channels(self):
        auto_rows = [row["channel"] for row in caps.matrix() if row["tier"] == caps.AUTO]
        self.assertEqual(sorted(auto_rows), sorted(caps.auto_channels()))


if __name__ == "__main__":
    unittest.main()
