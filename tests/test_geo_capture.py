import tempfile
import unittest
from pathlib import Path

from core import geo_capture, geo_entity, geo_questions, geo_runs
from tests.geo_fixtures import entity_document, observation, question_document, write_rows


SURFACE = {"name": "chatgpt_search", "tier": "T1",
           "capture_mode": "ui_observed", "session": "fresh"}


class GeoCaptureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        geo_entity.init(self.data, entity_document())
        geo_questions.import_set(self.data, question_document())
        geo_questions.freeze(self.data, "growovo-test", 1)
        geo_runs.start(self.data, "growovo-test", surfaces=[SURFACE],
                       samples_per_question=2, operator="tester", run_id="geo-test")
        self.file = Path(self.data) / "answers.jsonl"

    def test_template_has_every_question_surface_sample(self):
        rows = geo_capture.template(self.data, "geo-test")
        self.assertEqual(4, len(rows))
        self.assertEqual(2, len({row["question_id"] for row in rows}))
        self.assertTrue(all(row["observation_id"].startswith("geo-test-") for row in rows))

    def test_import_writes_answer_and_is_idempotent(self):
        rows = geo_capture.template(self.data, "geo-test")
        row = observation("q01", rows[0]["observation_id"],
                          answer_text="first answer")
        write_rows(self.file, [row])
        first = geo_capture.import_file(self.data, "geo-test", self.file)
        self.assertEqual(1, first["added"])
        stored = geo_runs.observations(self.data, "geo-test")[0]
        answer_path = Path(stored["answer"]["path"])
        self.assertEqual("first answer", answer_path.read_text(encoding="utf-8"))

        row["answer_text"] = "second answer"
        write_rows(self.file, [row])
        second = geo_capture.import_file(self.data, "geo-test", self.file)
        self.assertEqual(1, second["duplicates"])
        self.assertEqual("first answer", answer_path.read_text(encoding="utf-8"))

    def test_recommended_without_basis_is_rejected(self):
        rows = geo_capture.template(self.data, "geo-test")
        row = observation("q01", rows[0]["observation_id"])
        row["judgement"] = {"recommended": True, "accurate": True, "basis": ""}
        write_rows(self.file, [row])
        with self.assertRaises(ValueError) as ctx:
            geo_capture.import_file(self.data, "geo-test", self.file)
        self.assertIn("basis", str(ctx.exception))

    def test_not_run_row_is_recorded_without_observation(self):
        rows = geo_capture.template(self.data, "geo-test")
        write_rows(self.file, [{
            "observation_id": rows[0]["observation_id"],
            "question_id": "q01",
            "surface": "chatgpt_search",
            "not_run": True,
            "reason": "auth_required",
        }])
        result = geo_capture.import_file(self.data, "geo-test", self.file)
        self.assertEqual(1, result["not_run"])
        run = geo_runs.load(self.data, "geo-test")
        self.assertEqual("auth_required", run["not_run"][0]["reason"])
        self.assertEqual([], geo_runs.observations(self.data, "geo-test"))

    def test_answer_path_is_relative_to_capture_file(self):
        rows = geo_capture.template(self.data, "geo-test")
        answer = Path(self.data) / "manual.md"
        answer.write_text("from a file", encoding="utf-8")
        row = observation("q01", rows[0]["observation_id"])
        row.pop("answer_text")
        row["answer_path"] = answer.name
        write_rows(self.file, [row])
        geo_capture.import_file(self.data, "geo-test", self.file)
        stored = geo_runs.observations(self.data, "geo-test")[0]
        self.assertEqual("from a file", Path(stored["answer"]["path"]).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
