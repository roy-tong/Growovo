import hashlib
import tempfile
import unittest
from pathlib import Path

from core import geo_entity, geo_questions, geo_runs
from tests.geo_fixtures import entity_document, question_document


SURFACE = {"name": "chatgpt_search", "tier": "T1",
           "capture_mode": "ui_observed", "session": "fresh"}


class GeoRunsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        geo_entity.init(self.data, entity_document())
        geo_questions.import_set(self.data, question_document())

    def freeze(self):
        geo_questions.freeze(self.data, "growovo-test", 1)

    def start(self, **overrides):
        values = {"set_id": "growovo-test", "surfaces": [SURFACE],
                  "samples_per_question": 2, "operator": "tester",
                  "run_id": "geo-test"}
        values.update(overrides)
        return geo_runs.start(self.data, **values)

    def test_start_requires_frozen_set(self):
        with self.assertRaises(ValueError) as ctx:
            self.start()
        self.assertIn("冻结", str(ctx.exception))

    def test_start_status_and_not_run(self):
        self.freeze()
        run = self.start()
        self.assertEqual(2, run["samples_per_question"])
        status = geo_runs.status(self.data, "geo-test")
        self.assertEqual(4, status["expected"])
        geo_runs.add_not_run(self.data, "geo-test", "q01", "chatgpt_search", "auth_required")
        status = geo_runs.status(self.data, "geo-test")
        self.assertEqual(1, status["not_run"])

    def test_duplicate_run_id_is_rejected(self):
        self.freeze()
        self.start()
        with self.assertRaises(ValueError):
            self.start()

    def test_invalid_surface_tier_is_rejected(self):
        self.freeze()
        with self.assertRaises(ValueError) as ctx:
            self.start(surfaces=[dict(SURFACE, tier="T9")])
        self.assertIn("tier", str(ctx.exception))

    def test_import_is_idempotent_and_close_blocks_more(self):
        self.freeze()
        self.start()
        answer = Path(self.data) / "answer.md"
        answer.write_text("Growovo is an open-source growth operator.", encoding="utf-8")
        row = {
            "schema": geo_runs.OBSERVATION_SCHEMA,
            "observation_id": "geo-test-q01-chatgpt_search-1",
            "question_id": "q01",
            "surface": "chatgpt_search",
            "tier": "T1",
            "capture_mode": "ui_observed",
            "session": "fresh",
            "sampled_at": "2026-11-24T22:30:00+08:00",
            "operator": "tester",
            "answer": {"path": str(answer), "chars": answer.stat().st_size,
                       "sha256": hashlib.sha256(answer.read_bytes()).hexdigest()},
            "mentions": [{"entity": "Growovo", "is_target": True, "position": 1,
                          "verbatim": "Growovo is an open-source growth operator"}],
            "cited_urls": ["https://github.com/example/growovo"],
            "judgement": {"recommended": True, "accurate": True, "basis": "rubric"},
            "fact_conflicts": [],
            "notes": "",
        }
        first = geo_runs.import_observations(self.data, "geo-test", [row])
        self.assertEqual(1, first["added"])
        second = geo_runs.import_observations(self.data, "geo-test", [row])
        self.assertEqual(1, second["duplicates"])
        geo_runs.close(self.data, "geo-test")
        with self.assertRaises(ValueError) as ctx:
            geo_runs.import_observations(self.data, "geo-test", [row])
        self.assertIn("已关闭", str(ctx.exception))

    def test_surface_mismatch_is_rejected(self):
        self.freeze()
        self.start()
        answer = Path(self.data) / "answer.md"
        answer.write_text("text", encoding="utf-8")
        row = {
            "schema": geo_runs.OBSERVATION_SCHEMA,
            "observation_id": "geo-test-q01-chatgpt_search-1",
            "question_id": "q01",
            "surface": "chatgpt_search",
            "tier": "T2",
            "capture_mode": "ui_observed",
            "session": "fresh",
            "sampled_at": "2026-11-24T22:30:00+08:00",
            "operator": "tester",
            "answer": {"path": str(answer), "chars": 4,
                       "sha256": hashlib.sha256(answer.read_bytes()).hexdigest()},
            "mentions": [],
            "cited_urls": [],
            "judgement": {"recommended": False, "accurate": None, "basis": ""},
            "fact_conflicts": [],
        }
        with self.assertRaises(ValueError) as ctx:
            geo_runs.import_observations(self.data, "geo-test", [row])
        self.assertIn("不一致", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
