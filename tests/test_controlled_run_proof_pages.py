from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ControlledRunProofPagesTest(unittest.TestCase):
    def test_controlled_run_pages_expose_offline_gates(self):
        for relative in ("examples/controlled-run.html", "examples/controlled-run.en.html"):
            page = ROOT / "website" / relative
            self.assertTrue(page.is_file(), relative)
            text = page.read_text(encoding="utf-8")
            for token in ("mkt demo", "human_approved=false", "dry-run", "demo-memory",
                          "UNKNOWN", "Synthetic", "no real"):
                self.assertIn(token, text)
            self.assertNotIn("<script>", text)
            self.assertNotIn("javascript:", text)
            self.assertEqual(1, text.count('<script type="application/ld+json">'))

    def test_homepages_link_family_story_and_controlled_run(self):
        for relative in ("index.html", "en.html"):
            text = (ROOT / "website" / relative).read_text(encoding="utf-8")
            self.assertIn("Generate Anything. Prove What Matters.", text)
            self.assertIn("examples/controlled-run", text)


if __name__ == "__main__":
    unittest.main()
