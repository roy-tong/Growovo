"""语气引擎：每个平台都要有画像，广告腔必须能被拦。"""
import unittest

from core import platforms, voice


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

    def test_render_contains_tone_and_taboos(self):
        md = voice.render("xiaohongshu")
        self.assertIn("语气", md)
        self.assertIn("别这么写", md)


if __name__ == "__main__":
    unittest.main()
