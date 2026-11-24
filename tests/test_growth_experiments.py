import tempfile
import unittest

from core import growth_experiments


class GrowthExperimentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.experiment = growth_experiments.create(self.data, "svc-test", {
            "id": "first-demo",
            "hypothesis": "A reproducible demo earns first successful runs",
            "primary_metric": "activation",
            "target": 1,
            "window": {"start": "2026-11-23T22:00:00+08:00",
                       "end": "2026-11-24T07:00:00+08:00"},
            "stop_rule": "Stop on platform warning or missing evidence",
        })
        self.eid = self.experiment["id"]

    def test_click_without_activation_diagnoses_first_value_path(self):
        growth_experiments.observe(self.data, "svc-test", self.eid, [
            {"id": "e1", "type": "signal", "subject": "u1", "source": "x",
             "at": "2026-11-23T23:00:00+08:00"},
            {"id": "e2", "type": "click", "subject": "u1", "source": "github",
             "at": "2026-11-23T23:01:00+08:00"},
        ])
        got = growth_experiments.diagnose(self.data, "svc-test", self.eid)
        self.assertEqual("first_value_path", got["kind"])
        self.assertEqual(1, got["funnel"]["click"])
        self.assertEqual(0, got["funnel"]["activation"])

    def test_duplicate_events_do_not_inflate_funnel(self):
        event = {"id": "same", "type": "activation", "subject": "u1", "source": "product",
                 "at": "2026-11-23T23:00:00+08:00"}
        growth_experiments.observe(self.data, "svc-test", self.eid, [event, dict(event)])
        got = growth_experiments.diagnose(self.data, "svc-test", self.eid)
        self.assertEqual(1, got["funnel"]["activation"])

    def test_primary_success_with_secondary_gap_does_not_recommend_more_volume(self):
        growth_experiments.observe(self.data, "svc-test", self.eid, [
            {"id": "e1", "type": "activation", "subject": "u1", "source": "product",
             "at": "2026-11-23T23:00:00+08:00"},
        ])
        got = growth_experiments.diagnose(self.data, "svc-test", self.eid,
                                          secondary_targets={"star": 1})
        self.assertEqual("secondary_assets", got["kind"])
        self.assertEqual("not_claimed", got["causal_credit"])


if __name__ == "__main__":
    unittest.main()
