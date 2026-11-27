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

    def test_quality_allocation_and_funnel_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            allocation = Path(tmp) / "allocation.json"
            allocation.write_text(json.dumps({"total_actions": 10, "candidates": [
                {"id": "new", "product": "verovo", "quality_score": 80, "evidence": {"actions": 0}}
            ]}))
            got = self.run_cli("growth", "quality-allocate", "--file", str(allocation))
            self.assertEqual(0, got.returncode, got.stderr)
            payload = json.loads(got.stdout)
            self.assertEqual(10, payload["candidates"][0]["allocation"])
            events = Path(tmp) / "events.json"
            events.write_text(json.dumps([{"event_id": "a", "event_type": "committed",
                                           "campaign_id": "c", "subject_id": "s",
                                           "occurred_at": "2026-11-27T09:00:00+08:00"}]))
            got = self.run_cli("growth", "quality-funnel", "--file", str(events), "--complete")
            self.assertEqual(0, got.returncode, got.stderr)
            self.assertEqual("observed_zero", json.loads(got.stdout)["groups"][0]["stage_status"]["first_run"])
            allocation.write_text(json.dumps({"total_actions": 10, "candidates": [
                {"id": "c", "product": "verovo", "quality_score": 80, "evidence": {}}
            ]}))
            events.write_text(json.dumps([
                {"event_id": "a", "event_type": "committed", "campaign_id": "c", "subject_id": "s", "occurred_at": "2026-11-27T09:00:00+08:00"},
                {"event_id": "b", "event_type": "accepted_result", "campaign_id": "c", "subject_id": "s", "occurred_at": "2026-11-27T09:01:00+08:00"},
            ]))
            got = self.run_cli("growth", "quality-allocate", "--file", str(allocation),
                               "--events", str(events), "--complete")
            self.assertEqual(0, got.returncode, got.stderr)
            candidate = json.loads(got.stdout)["candidates"][0]
            self.assertEqual(1, candidate["evidence"]["accepted_results"])
            self.assertEqual(10, candidate["allocation"])

    def test_ready_lint_rejects_unmaterialized_blueprint(self):
        got = self.run_cli("growth", "output-lint", "--file",
                           "examples/execution-strategy-300.json", "--require-ready")
        self.assertEqual(2, got.returncode)
        self.assertIn("item_count_mismatch", json.loads(got.stdout)["errors"])


if __name__ == "__main__":
    unittest.main()
