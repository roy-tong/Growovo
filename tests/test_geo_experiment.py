import tempfile
import unittest
from pathlib import Path

from core import geo_capture, geo_entity, geo_experiment, geo_questions, geo_runs
from tests.geo_fixtures import entity_document, observation, question_document, write_rows


SURFACE = {"name": "chatgpt_search", "tier": "T1",
           "capture_mode": "ui_observed", "session": "fresh"}


class GeoExperimentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        geo_entity.init(self.data, entity_document())
        geo_questions.import_set(self.data, question_document())
        geo_questions.freeze(self.data, "growovo-test", 1)

    def make_run(self, run_id, recommended=True):
        geo_runs.start(self.data, "growovo-test", surfaces=[SURFACE],
                       samples_per_question=1, operator="tester", run_id=run_id)
        rows = [
            observation("q01", run_id + "-q01", recommended=recommended),
            observation("q02", run_id + "-q02", recommended=recommended),
        ]
        path = Path(self.data) / (run_id + ".jsonl")
        write_rows(path, rows)
        geo_capture.import_file(self.data, run_id, path)
        geo_runs.close(self.data, run_id)

    def spec(self, **overrides):
        payload = {
            "id": "geo-exp",
            "hypothesis": "fixing the page raises recommendation rate",
            "primary_metric": "recommendation_rate",
            "target": 0.2,
            "window": {"start": "2026-11-01T00:00:00+08:00",
                       "end": "2026-12-31T00:00:00+08:00"},
            "stop_rule": "stop if fact conflicts increase",
            "design": {
                "entity_id": "growovo",
                "set_id": "growovo-test",
                "baseline_runs": ["base-1", "base-2"],
                "intervention_runs": ["int-1", "int-2"],
                "intervention_question_ids": ["q01"],
                "control_question_ids": ["q02"],
                "surfaces": ["chatgpt_search"],
                "samples_per_question": 1,
                "primary_metric": "recommendation_rate",
                "min_detectable_effect": 0.2,
                "preregistered_at": "2026-11-01T00:00:00+08:00",
                "analysis_plan": "per-question recommendation rate; descriptive only",
            },
        }
        payload.update(overrides)
        return payload

    def test_create_and_report_are_explicitly_inconclusive(self):
        for run_id in ("base-1", "base-2"):
            self.make_run(run_id, recommended=False)
        for run_id in ("int-1", "int-2"):
            self.make_run(run_id, recommended=True)
        experiment = geo_experiment.create(self.data, "svc-test", self.spec())
        self.assertEqual("geo", experiment["kind"])
        self.assertEqual("growovo", experiment["design"]["entity_id"])
        report = geo_experiment.report(self.data, "svc-test", "geo-exp")
        self.assertEqual("inconclusive", report["conclusion"])
        self.assertEqual("not_claimed", report["causality"])
        self.assertEqual(2, report["intervention"]["intervention"]["observations"])
        self.assertIn("观测不足", report["reason"])

    def test_overlapping_questions_are_rejected(self):
        self.make_run("base-1")
        spec = self.spec()
        spec["design"]["control_question_ids"] = ["q01"]
        with self.assertRaises(ValueError) as ctx:
            geo_experiment.create(self.data, "svc-test", spec)
        self.assertIn("共享", str(ctx.exception))

    def test_missing_baseline_run_is_rejected(self):
        spec = self.spec()
        with self.assertRaises(ValueError) as ctx:
            geo_experiment.create(self.data, "svc-test", spec)
        self.assertIn("base-1", str(ctx.exception))

    def test_future_preregistration_is_rejected(self):
        self.make_run("base-1")
        self.make_run("base-2")
        self.make_run("int-1")
        self.make_run("int-2")
        spec = self.spec()
        spec["design"]["preregistered_at"] = "2099-01-01T00:00:00+08:00"
        with self.assertRaises(ValueError) as ctx:
            geo_experiment.create(self.data, "svc-test", spec)
        self.assertIn("preregistered_at", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
