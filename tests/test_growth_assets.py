import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import growth_assets


def spec(command=None):
    return {
        "id": "demo",
        "title": "Run a receipt-aware GTM demo",
        "scenario": "Agent returns a confident completion message",
        "audience": "Agent developers",
        "value": "Separate action receipts from user outcomes",
        "cta": {"kind": "run_demo", "url": "https://example.invalid/demo"},
        "steps": ["Clone the repository", "Run the demo", "Read the result card"],
        "expected_output": "A receipt and a result card",
        "limitations": ["Offline synthetic example"],
        "reproduction": {
            "command": command or [sys.executable, "-c", "print('demo-ok')"],
            "authorized": True,
            "by": "tester",
            "timeout_sec": 10,
        },
    }


class GrowthAssetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name

    def test_reproduced_asset_can_be_repurposed_by_channel(self):
        asset = growth_assets.create(self.data, "svc-test", spec())
        self.assertTrue(asset["reproduced"])
        self.assertEqual(0, asset["reproduction"]["exit_code"])
        self.assertIn("demo-ok", asset["reproduction"]["stdout"])
        variants = growth_assets.repurpose(self.data, "svc-test", "demo", ["github", "x", "email"])
        self.assertEqual(3, len(variants))
        x_text = Path(variants["x"]["path"]).read_text()
        self.assertLessEqual(len(x_text), 280)
        self.assertIn("https://example.invalid/demo", x_text)
        github = Path(variants["github"]["path"]).read_text()
        self.assertIn("Run the demo", github)
        self.assertIn("Offline synthetic example", github)

    def test_failed_reproduction_cannot_be_repurposed(self):
        request = spec([sys.executable, "-c", "raise SystemExit(2)"])
        asset = growth_assets.create(self.data, "svc-test", request)
        self.assertFalse(asset["reproduced"])
        with self.assertRaisesRegex(ValueError, "reproduced"):
            growth_assets.repurpose(self.data, "svc-test", "demo", ["x"])

    def test_unauthorized_command_does_not_execute(self):
        request = spec()
        request["reproduction"]["authorized"] = False
        with mock.patch("core.growth_assets.subprocess.run") as runner:
            with self.assertRaisesRegex(ValueError, "authorized"):
                growth_assets.create(self.data, "svc-test", request)
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
