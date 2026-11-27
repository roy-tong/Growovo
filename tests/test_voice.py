"""语气引擎：每个平台都要有画像，广告腔必须能被拦。"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import platforms, voice

ROOT = Path(__file__).resolve().parents[1]


class VoiceTests(unittest.TestCase):
    def test_every_platform_has_a_profile(self):
        for p in platforms.PLATFORMS:
            v = voice.profile(p["id"])
            for field in ("tone", "sentences", "openers", "taboos", "memes", "length"):
                self.assertTrue(v.get(field), f"{p['id']} 缺 {field}")
            self.assertGreaterEqual(len(v["openers"]), 3, p["id"])

    def test_bespoke_profiles_cover_the_main_platforms(self):
        cov = voice.coverage()
        for pid in ("hackernews", "github", "xiaohongshu", "zhihu", "v2ex", "reddit", "email"):
            self.assertIn(pid, cov["bespoke"])

    def test_platform_voice_differs_not_copies(self):
        hn = voice.profile("hackernews")
        xhs = voice.profile("xiaohongshu")
        self.assertNotEqual(hn["openers"], xhs["openers"])
        self.assertNotEqual(hn["tone"], xhs["tone"])

    def test_lint_blocks_marketing_speak(self):
        problems = voice.lint("github", "这是一个颠覆性的极致方案，全面赋能开发者")
        self.assertTrue(problems)
        self.assertTrue(any("广告词" in p for p in problems))

    def test_lint_blocks_contact_on_content_platforms(self):
        problems = voice.lint("xiaohongshu", "想要的话加我微信，或者看公众号：xxx")
        self.assertTrue(any("联系方式" in p for p in problems))
        self.assertEqual(voice.lint("email", "下面是复现步骤"), [])

    def test_lint_blocks_emoji_on_hn_and_github(self):
        self.assertTrue(voice.lint("hackernews", "I built this 🚀"))
        self.assertEqual(voice.lint("reddit", "I ran into this too"), [])

    def test_lint_blocks_taboo_phrases(self):
        problems = voice.lint("hackernews", "We're excited to announce our new tool")
        self.assertTrue(any("禁忌" in p for p in problems))

    def test_draft_returns_three_openers_with_slots_filled(self):
        d = voice.draft("github", hook="v2.4.1 上 token 计数翻倍")
        self.assertEqual(len(d["drafts"]), 3)
        self.assertTrue(any("v2.4.1" in x["opener"] for x in d["drafts"]))

    def test_x_draft_uses_real_hook_product_and_invitation(self):
        result = voice.draft(
            "x", hook="第一版刚发，30 star", what="本地优先笔记同步，不绑云笔记厂商",
            ask="你愿意先在自己两台设备上试一次吗")
        self.assertEqual(3, len(result["drafts"]))
        for row in result["drafts"]:
            self.assertIn("30 star", row["opener"])
            self.assertIn("本地优先笔记同步", row["opener"])
            self.assertIn("两台设备", row["opener"])
            self.assertNotIn("double-count", row["opener"])

    def test_x_draft_does_not_return_unrelated_canned_text_without_inputs(self):
        with self.assertRaisesRegex(ValueError, "hook|what|ask"):
            voice.draft("x")

    def test_draft_cli_prints_platform_length(self):
        d = voice.draft("github")
        self.assertTrue(d["length"])
        self.assertEqual(d["length"], d["drafts"][0]["length"])
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(ROOT / "mkt.py"), "--workspace", tmp, "voice", "draft", "github"],
                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode,
                         msg=f"stdout={result.stdout}\nstderr={result.stderr}")
        self.assertIn("长度：", result.stdout)
        self.assertIn(d["length"], result.stdout)

    def test_render_contains_tone_and_taboos(self):
        md = voice.render("xiaohongshu")
        self.assertIn("语气", md)
        self.assertIn("别这么写", md)


class EnglishMarketingTests(unittest.TestCase):
    """中文词表管不到英文帖；英文促销/导流表达必须同样被拦。"""

    def test_lint_blocks_english_promotion(self):
        problems = voice.lint(
            "x", "Buy now! Limited time 50% off, click the link in bio")
        self.assertTrue(problems)
        self.assertTrue(any("英文促销" in p for p in problems))

    def test_lint_blocks_each_english_pattern_family(self):
        cases = [
            "Order now and get yours today",
            "Don't miss out on this drop",
            "Half price for the first 100 users",
            "Swipe up to see the offer",
            "DM me for the discount code",
            "Sign up today, risk-free",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertTrue(voice.lint("linkedin", text), text)

    def test_lint_keeps_plain_english_engineering_copy(self):
        texts = [
            "Agent said done. Nobody could replay it. We exported a proof bundle with the run trace.",
            "We ran the same prompt through five providers; three double-count on retry.",
            "The average reply latency dropped from 900 ms to 240 ms after the change.",
        ]
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual([], voice.lint("x", text), text)


class EmptyTextTests(unittest.TestCase):
    """空正文曾返回「这条可以用」；调用方漏传正文时会拿到假通过。"""

    def test_lint_rejects_empty_text(self):
        for text in ("", "   ", "\n\t"):
            with self.subTest(text=repr(text)):
                problems = voice.lint("x", text)
                self.assertTrue(problems)
                self.assertIn("正文为空", problems[0])

    def test_lint_cli_exits_2_without_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "mkt.py"), "--workspace", tmp,
                 "voice", "lint", "x"],
                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(2, proc.returncode)
        self.assertIn("需要正文", proc.stderr)

    def test_lint_cli_accepts_positional_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "mkt.py"), "--workspace", tmp,
                 "voice", "lint", "x", "Buy now! Limited time offer"],
                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(2, proc.returncode, msg=proc.stderr)
        self.assertIn("英文促销", proc.stdout)


if __name__ == "__main__":
    unittest.main()
