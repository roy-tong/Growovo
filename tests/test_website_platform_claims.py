from html.parser import HTMLParser
from pathlib import Path
import unittest

from core import platform_caps as caps

ROOT = Path(__file__).resolve().parents[1]


class AutomationMetadataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.auto_channels = None

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        values = dict(attrs)
        if values.get("name") == "growovo:auto-channels":
            self.auto_channels = values.get("content", "")


class WebsitePlatformClaimsTest(unittest.TestCase):
    def page(self, name):
        return (ROOT / "website" / name).read_text(encoding="utf-8")

    def test_execution_registry_includes_verified_business_routes(self):
        self.assertEqual(caps.auto_channels(), ["x", "email", "discord"])
        self.assertTrue(caps.is_auto("x"))
        self.assertTrue(caps.is_auto("email"))

    def test_machine_readable_claims_match_execution_registry(self):
        expected = sorted(caps.auto_channels())
        for name in ("index.html", "en.html"):
            with self.subTest(page=name):
                parser = AutomationMetadataParser()
                parser.feed(self.page(name))
                self.assertIsNotNone(parser.auto_channels)
                actual = sorted(
                    channel.strip().lower()
                    for channel in parser.auto_channels.split(",")
                    if channel.strip()
                )
                self.assertEqual(actual, expected)

    def test_visible_copy_leads_with_growth_value_and_next_action(self):
        requirements = {
            "index.html": (
                "你定目标，Growovo 把业务执行到底。",
                "端到端交付的业务执行",
                "给我目标，开跑第一轮",
                "docs/platform-automation-matrix.md",
            ),
            "en.html": (
                "You set the goal. Growovo carries out the work.",
                "delivers the whole business workflow",
                "Start your first run",
                "docs/platform-automation-matrix.md",
            ),
        }
        for name, sentences in requirements.items():
            text = self.page(name)
            for sentence in sentences:
                with self.subTest(page=name, sentence=sentence):
                    self.assertIn(sentence, text)

    def test_readmes_do_not_label_forbidden_x_writes_as_proven(self):
        for name in ("README.md", "README.en.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(readme=name):
                self.assertNotIn("X 全动作", text)
                self.assertNotIn("All X actions", text)
                self.assertIn("DISABLED", text)

    def test_stale_x_automation_promises_are_rejected(self):
        stale_claims = {
            "index.html": (
                "第一期只有 X 和 Email",
                "第一期只承诺 X + Email",
            ),
            "en.html": (
                "Phase one commits to X and Email only",
                "Phase one commits to X + Email",
                "Phase one: X and Email only",
            ),
        }
        for name, claims in stale_claims.items():
            text = self.page(name)
            for claim in claims:
                with self.subTest(page=name, claim=claim):
                    self.assertNotIn(claim, text)


if __name__ == "__main__":
    unittest.main()
