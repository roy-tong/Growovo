import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"


class GrowthIntakeCliTest(unittest.TestCase):
    def test_intake_start_and_live_diagnosis_use_repository_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            repo = Path(tmp) / "repo"
            repo.mkdir()
            data.mkdir()
            (repo / "README.md").write_text(
                "# Demo\n\nInspect real GTM outcomes.\n\n```bash\npython3 mkt.py demo\n```\n",
                encoding="utf-8")
            (repo / "AGENTS.md").write_text("rules", encoding="utf-8")
            (repo / "llms.txt").write_text("docs", encoding="utf-8")
            goal_path = data / "goal.json"
            env = dict(os.environ, MKT_HOME=str(data))
            intake = subprocess.run(
                [sys.executable, str(MKT), "growth", "intake", "--repo", str(repo),
                 "--slug", "owner/demo", "--out", str(goal_path)],
                cwd=ROOT, env=env, text=True, capture_output=True)
            self.assertEqual(0, intake.returncode, intake.stdout + intake.stderr)
            payload = json.loads(intake.stdout)
            self.assertEqual("UNKNOWN", payload["goal"]["intake"]["metrics_status"])
            started = subprocess.run(
                [sys.executable, str(MKT), "growth", "start", "--file", str(goal_path)],
                cwd=ROOT, env=env, text=True, capture_output=True)
            self.assertEqual(0, started.returncode, started.stdout + started.stderr)
            service = json.loads(started.stdout)
            diagnosed = subprocess.run(
                [sys.executable, str(MKT), "growth", "run", service["id"],
                 "--action", "diagnose-product", "--by", "tester"],
                cwd=ROOT, env=env, text=True, capture_output=True)
            self.assertEqual(0, diagnosed.returncode, diagnosed.stdout + diagnosed.stderr)
            evidence = json.loads(diagnosed.stdout)["evidence"]
            self.assertEqual("instrument_activation_events",
                             evidence["diagnosis"]["recommended_first_action"])


if __name__ == "__main__":
    unittest.main()
