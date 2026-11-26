import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class OutputStrategyCliTest(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "mkt.py", *args], text=True, capture_output=True)

    def test_guide_outputs_structured_next_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.json"
            draft.write_text(json.dumps({"product": {"name": "Askovo"}}))
            got = self.run_cli("growth", "output-guide", "--file", str(draft))
            self.assertEqual(0, got.returncode, got.stderr)
            payload = json.loads(got.stdout)
            self.assertEqual("product.url", payload["next_question"]["field"])

    def test_guide_answer_is_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.json"
            draft.write_text(json.dumps({"product": {"name": "Askovo"}}))
            got = self.run_cli("growth", "output-guide", "--file", str(draft),
                               "--set", "product.url", "--value", json.dumps("https://github.com/roy-tong/Askovo"))
            self.assertEqual(0, got.returncode, got.stderr)
            saved = json.loads(draft.read_text())
            self.assertEqual("https://github.com/roy-tong/Askovo", saved["product"]["url"])
            self.assertEqual("outcomes.primary", json.loads(got.stdout)["next_question"]["field"])

    def test_plan_exposes_required_300(self):
        got = self.run_cli("growth", "output-plan", "--file", "examples/execution-strategy-300.json")
        self.assertEqual(0, got.returncode, got.stderr)
        self.assertEqual(300, json.loads(got.stdout)["readiness"]["required"])

    def test_markdown_plan_contains_wave_and_preparation_sections(self):
        got = self.run_cli("growth", "output-plan", "--file",
                           "examples/execution-strategy-300.json", "--format", "md")
        self.assertEqual(0, got.returncode, got.stderr)
        self.assertIn("## 波次矩阵", got.stdout)
        self.assertIn("## 备料 TODO", got.stdout)
        self.assertIn("review-originals-my-voice", got.stdout)

    def test_ready_lint_rejects_unmaterialized_blueprint(self):
        got = self.run_cli("growth", "output-lint", "--file",
                           "examples/execution-strategy-300.json", "--require-ready")
        self.assertEqual(2, got.returncode)
        self.assertIn("item_count_mismatch", json.loads(got.stdout)["errors"])


if __name__ == "__main__":
    unittest.main()
