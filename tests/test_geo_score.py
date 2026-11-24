import tempfile
import unittest
from pathlib import Path

from core import geo_capture, geo_entity, geo_questions, geo_runs, geo_score
from tests.geo_fixtures import entity_document, observation, question_document, write_rows


SURFACE = {"name": "chatgpt_search", "tier": "T1",
           "capture_mode": "ui_observed", "session": "fresh"}
TARGET_MENTION = [{"entity": "Growovo", "is_target": True, "position": 1,
                   "verbatim": "Growovo is an open-source growth operator"}]


class GeoScoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        geo_entity.init(self.data, entity_document())
        geo_questions.import_set(self.data, question_document())
        geo_questions.freeze(self.data, "growovo-test", 1)
        geo_runs.start(self.data, "growovo-test", surfaces=[SURFACE],
                       samples_per_question=2, operator="tester", run_id="geo-test")
        rows = geo_capture.template(self.data, "geo-test")
        by_id = {row["observation_id"]: row for row in rows}
        q01 = [row for row in rows if row["question_id"] == "q01"]
        q02 = [row for row in rows if row["question_id"] == "q02"]
        answers = [
            observation("q01", q01[0]["observation_id"], mentions=TARGET_MENTION),
            observation("q01", q01[1]["observation_id"],
                        recommended=False, cited=[], mentions=[]),
            observation("q02", q02[0]["observation_id"],
                        mentions=[{"entity": "Growovo", "is_target": True, "position": 2,
                                   "verbatim": "Growovo"}],
                        conflicts=[{"fact_id": "cap-01", "expected": "authorized",
                                    "observed": "fully autonomous", "severity": "major"}]),
            observation("q02", q02[1]["observation_id"],
                        recommended=False,
                        mentions=[{"entity": "Growovo", "is_target": True, "position": 4,
                                   "verbatim": "Growovo"}]),
        ]
        write_rows(Path(self.data) / "answers.jsonl", answers)
        geo_capture.import_file(self.data, "geo-test", Path(self.data) / "answers.jsonl")
        geo_runs.add_not_run(self.data, "geo-test", "q01", "chatgpt_search", "not_attempted")

    def test_rates_and_grouping(self):
        result = geo_score.score(self.data, "geo-test")
        groups = {(row["intent"], row["brand_scope"], row["tier"]): row
                  for row in result["groups"]}
        q01 = groups[("category_discovery", "nonbrand", "T1")]
        q02 = groups[("recommendation", "nonbrand", "T1")]
        self.assertEqual(2, q01["n"])
        self.assertEqual(0.5, q01["mention_rate"]["value"])
        self.assertEqual(0.5, q01["citation_rate"]["value"])
        self.assertEqual(0.5, q01["recommendation_rate"]["value"])
        self.assertEqual(1, q01["not_run"])
        self.assertEqual({"first": 1, "top3": 0, "listed": 0, "absent": 1},
                         q01["position_mix"])
        self.assertEqual(1.0, q02["mention_rate"]["value"])
        self.assertEqual(0.5, q02["recommendation_rate"]["value"])
        self.assertEqual(0.5, q02["accuracy_rate"]["value"])
        self.assertEqual({"first": 0, "top3": 1, "listed": 1, "absent": 0},
                         q02["position_mix"])

    def test_small_groups_are_marked_descriptive(self):
        result = geo_score.score(self.data, "geo-test")
        self.assertTrue(all(row["sample_status"] == "descriptive"
                            for row in result["groups"]))

    def test_score_is_reproducible(self):
        first = geo_score.score(self.data, "geo-test")
        second = geo_score.score(self.data, "geo-test")
        self.assertEqual(first["inputs_sha256"], second["inputs_sha256"])
        self.assertEqual(first["groups"], second["groups"])

    def test_wilson_interval(self):
        low, high = geo_score.wilson(1, 1)
        self.assertAlmostEqual(0.206543, low, places=5)
        self.assertEqual(1.0, high)
        self.assertEqual([None, None], geo_score.wilson(0, 0))

    def test_accuracy_is_unknown_without_target_mentions(self):
        rows = geo_capture.template(self.data, "geo-test")
        # A separate run keeps this assertion independent of the fixture above.
        geo_runs.start(self.data, "growovo-test", surfaces=[SURFACE],
                       samples_per_question=1, operator="tester", run_id="geo-empty")
        empty = [observation("q01", rows[0]["observation_id"], mentions=[],
                             recommended=False, cited=[])]
        empty[0]["observation_id"] = "geo-empty-q01-chatgpt_search-1"
        write_rows(Path(self.data) / "empty.jsonl", empty)
        geo_capture.import_file(self.data, "geo-empty", Path(self.data) / "empty.jsonl")
        result = geo_score.score(self.data, "geo-empty")
        self.assertIsNone(result["groups"][0]["accuracy_rate"]["value"])


if __name__ == "__main__":
    unittest.main()
