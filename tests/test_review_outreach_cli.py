from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_review_outreach import NOW, valid_candidate

ROOT = Path(__file__).resolve().parents[1]


class ReviewOutreachCliTest(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "mkt.py"), *map(str, args)],
            cwd=ROOT, capture_output=True, text=True)

    def test_check_writes_unapproved_queue_without_external_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.json"
            candidates.write_text(json.dumps([valid_candidate()]), encoding="utf-8")
            legacy = root / "legacy"
            legacy.mkdir()
            out = root / "out"
            run = self.run_cli(
                "--workspace", root / "data", "review-ops", "check", candidates,
                "--legacy-root", legacy, "--out", out,
                "--campaign", "expert_review_v2", "--now", NOW.isoformat())
            self.assertEqual(0, run.returncode, run.stdout + run.stderr)
            summary = json.loads(run.stdout)
            self.assertEqual(1, summary["accepted"])
            self.assertEqual("none", summary["external_action"])
            queue = json.loads((out / "expert_review_v2.json").read_text(encoding="utf-8"))
            self.assertEqual(1, len(queue["items"]))
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["human_approval_required"])
            self.assertEqual({"review_material": 1}, report["business_goals"])
            self.assertEqual(3, report["capacity_per_channel_per_wave"])
            self.assertEqual("eligible_only_no_padding", report["quantity_policy"])
            self.assertNotIn("ada@example.com", run.stdout)

    def test_check_keeps_github_reviews_out_of_the_email_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = valid_candidate()
            candidate["person"].update({
                "name": "Gunjan Jaswal", "channel": "github",
                "target": "issue:roy-tong/Verovo#11",
            })
            candidate["artifact"] = {
                "ref": "healthcheck/verovo_healthcheck/proof_export.py",
                "url": "https://github.com/roy-tong/Verovo/blob/main/healthcheck/verovo_healthcheck/proof_export.py",
            }
            candidate["body"] = (
                "Gunjan, your spec-version drift guard in PR 38 kept a prose specification "
                "and a conformance runner from silently diverging. "
                "The read-only proof exporter now separates observed state, professional review, "
                "and external outcomes in a similar checkable form. "
                "Would you review the P4 check at "
                "https://github.com/roy-tong/Verovo/blob/main/healthcheck/verovo_healthcheck/proof_export.py "
                "and say whether its external-result wording preserves your availability versus "
                "influence boundary? "
                "A missing unknown state or a misleading level label would be useful. "
                "A small counterexample fixture would also help."
            )
            candidates = root / "candidates.json"
            candidates.write_text(json.dumps([candidate]), encoding="utf-8")
            out = root / "out"
            run = self.run_cli(
                "--workspace", root / "data", "review-ops", "check", candidates,
                "--legacy-root", root / "legacy", "--out", out,
                "--campaign", "expert_review_v2", "--now", NOW.isoformat())
            self.assertEqual(0, run.returncode, run.stdout + run.stderr)
            self.assertEqual(1, json.loads(run.stdout)["accepted"])
            queue = json.loads((out / "expert_review_v2_github.json").read_text(encoding="utf-8"))
            self.assertEqual("github", queue["channel"])
            self.assertFalse((out / "expert_review_v2.json").exists())

    def test_run_creates_private_timestamped_outputs_and_zero_eligible_is_not_a_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = valid_candidate()
            stale["hook"]["checked_at"] = "2026-11-22T12:00:00+08:00"
            candidates = root / "candidates.json"
            candidates.write_text(json.dumps([stale]), encoding="utf-8")
            config = root / "review.json"
            config.write_text(json.dumps({
                "workspace": str(root / "data"), "candidates": str(candidates),
                "legacy_root": str(root / "legacy"), "out_root": str(root / "runs"),
                "campaign": "expert_review_v2",
            }), encoding="utf-8")
            run = self.run_cli("review-ops", "run", config, "--now", NOW.isoformat())
            self.assertEqual(0, run.returncode, run.stdout + run.stderr)
            summary = json.loads(run.stdout)
            self.assertEqual(0, summary["accepted"])
            self.assertEqual(1, summary["rejected"])
            self.assertEqual("none", summary["external_action"])
            runs = list((root / "runs").glob("run-*"))
            self.assertEqual(1, len(runs))
            self.assertFalse(list(runs[0].glob("*.json")) == [])
            self.assertFalse((runs[0] / "expert_review_v2.json").exists())
            self.assertFalse((root / "data" / "ledger.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
