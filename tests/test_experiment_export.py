import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import experiment_export, growth_experiments

ROOT = Path(__file__).resolve().parents[1]


class GrowthExperimentExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.experiment = growth_experiments.create(self.data, "svc-test", {
            "id": "first-demo",
            "hypothesis": "A reproducible demo earns more first successful runs than a feature post",
            "primary_metric": "activation",
            "target": 1,
            "window": {"start": "2026-11-23T00:00:00Z",
                       "end": "2026-11-24T23:59:59Z"},
            "stop_rule": "Stop on platform warning or missing evidence",
        })
        growth_experiments.observe(self.data, "svc-test", "first-demo", [
            {"id": "control-event", "type": "activation", "subject": "u1",
             "source": "product", "at": "2026-11-24T01:00:00Z"},
            {"id": "treatment-event", "type": "activation", "subject": "u2",
             "source": "product", "at": "2026-11-24T02:00:00Z"},
        ])

    def design(self):
        return {
            "falsification_condition": "The demo does not improve verified first successful runs.",
            "design": {
                "experiment_type": "controlled_trial",
                "unit_of_analysis": "qualified_visitor",
                "population": "Synthetic open-source evaluators",
                "assignment": "fixed_groups",
                "arms": [
                    {"arm_id": "control", "role": "control",
                     "description": "Feature post", "planned_sample_size": 10},
                    {"arm_id": "treatment", "role": "treatment",
                     "description": "Reproducible demo", "planned_sample_size": 10},
                ],
                "primary_metrics": [{
                    "metric_id": "activation",
                    "definition": "Evaluator completes the documented first successful run.",
                    "unit": "event",
                    "direction": "increase",
                }],
                "comparability": {
                    "same_task_set": True,
                    "same_acceptance_rubric": True,
                    "same_budget": True,
                    "controlled_variables": ["Repository version", "Activation rubric"],
                    "known_differences": ["Distribution asset"],
                },
            },
            "external_actions": False,
            "authorization_refs": [],
            "registered_by": "growth-owner",
            "registered_at": "2026-11-22T00:00:00Z",
            "observation_bindings": {
                "control-event": {
                    "arm_id": "control", "metric_id": "activation",
                    "eligible": True, "evidence_grade": "observed",
                },
                "treatment-event": {
                    "arm_id": "treatment", "metric_id": "activation",
                    "eligible": True, "evidence_grade": "observed",
                },
            },
        }

    def test_export_preserves_growth_hypothesis_and_event_lineage(self):
        got = experiment_export.from_growth_experiment(
            self.data, "svc-test", "first-demo", self.design(), "0.22.0")
        self.assertEqual("ovo.experiment-contract/1", got["schema"])
        self.assertEqual("growovo", got["origin_product"])
        self.assertEqual(self.experiment["hypothesis"], got["hypothesis"]["statement"])
        self.assertEqual(2, len(got["observations"]))
        self.assertEqual("descriptive_association", got["outcome"]["causality"])
        self.assertEqual("inconclusive", got["outcome"]["status"])
        self.assertEqual(got["design_sha256"], experiment_export.design_hash(got))

    def test_missing_falsification_condition_is_refused(self):
        spec = self.design()
        spec.pop("falsification_condition")
        with self.assertRaisesRegex(ValueError, "falsification_condition"):
            experiment_export.from_growth_experiment(
                self.data, "svc-test", "first-demo", spec, "0.22.0")

    def test_external_actions_need_authorization_refs(self):
        spec = self.design()
        spec["external_actions"] = True
        with self.assertRaisesRegex(ValueError, "authorization_refs"):
            experiment_export.from_growth_experiment(
                self.data, "svc-test", "first-demo", spec, "0.22.0")

    def test_every_event_needs_an_explicit_binding(self):
        spec = self.design()
        spec["observation_bindings"].pop("control-event")
        with self.assertRaisesRegex(ValueError, "control-event"):
            experiment_export.from_growth_experiment(
                self.data, "svc-test", "first-demo", spec, "0.22.0")

    def test_cli_exports_new_file_without_overwrite(self):
        spec_path = Path(self.tmp.name) / "design.json"
        spec_path.write_text(json.dumps(self.design()), encoding="utf-8")
        out = Path(self.tmp.name) / "experiment.json"
        argv = [
            sys.executable, str(ROOT / "mkt.py"), "--workspace", self.data,
            "growth", "experiment-export", "svc-test", "--experiment", "first-demo",
            "--design", str(spec_path), "--out", str(out),
        ]
        result = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual("ovo.experiment-contract/1", payload["schema"])
        second = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True)
        self.assertNotEqual(0, second.returncode)


if __name__ == "__main__":
    unittest.main()
