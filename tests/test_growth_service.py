import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from core import growth_service
from tests.test_growth_goals import goal


class GrowthServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.service = growth_service.start(self.data, goal())
        self.sid = self.service["id"]

    def complete(self, action_id, minutes=0):
        return growth_service.record_action(
            self.data, self.sid, action_id, "completed", by="tester", minutes=minutes)

    def test_start_is_idempotent_and_next_action_follows_dependencies(self):
        again = growth_service.start(self.data, goal())
        self.assertEqual(self.sid, again["id"])
        self.assertEqual("diagnose-product", growth_service.next_action(self.data, self.sid)["id"])
        self.complete("diagnose-product")
        self.assertEqual("baseline-observe", growth_service.next_action(self.data, self.sid)["id"])
        self.complete("baseline-observe")
        self.complete("asset-prepare")
        self.assertEqual("x-publish", growth_service.next_action(self.data, self.sid)["id"])

    def test_blocked_channel_does_not_stop_other_channels(self):
        for action_id in ("diagnose-product", "baseline-observe", "asset-prepare"):
            self.complete(action_id)
        growth_service.record_action(
            self.data, self.sid, "x-publish", "blocked", by="tester", note="cdp unavailable")
        self.assertEqual("github-engage", growth_service.next_action(self.data, self.sid)["id"])
        loaded = growth_service.load(self.data, self.sid)
        self.assertEqual("active", loaded["status"])
        self.assertEqual(["x-publish"], loaded["metrics"]["blocked_actions"])

    def test_human_interruption_and_autonomous_delivery_metrics(self):
        for action_id in ("diagnose-product", "baseline-observe", "asset-prepare"):
            self.complete(action_id)
        loaded = growth_service.record_action(
            self.data, self.sid, "x-publish", "completed", by="tester", minutes=12)
        self.assertEqual(12, loaded["metrics"]["human_minutes_total"])
        self.assertEqual(4, loaded["metrics"]["accepted_tasks"])
        self.assertEqual(3, loaded["metrics"]["human_minutes_per_accepted"])
        self.assertEqual(4 / 7, loaded["metrics"]["autonomous_delivery_rate"])

    def test_two_writers_keep_both_action_updates_and_events(self):
        original_load = growth_service.load
        barrier = threading.Barrier(2)
        calls = [0]

        def synchronized_load(*args):
            payload = original_load(*args)
            if calls[0] < 2:
                calls[0] += 1
                barrier.wait(timeout=5)
            return payload

        with mock.patch.object(growth_service, "load", side_effect=synchronized_load):
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(
                    lambda action_id: growth_service.record_action(
                        self.data, self.sid, action_id, "completed", by="tester"),
                    ("diagnose-product", "baseline-observe")))
        loaded = growth_service.load(self.data, self.sid)
        statuses = {row["id"]: row["status"] for row in loaded["actions"]}
        self.assertEqual("completed", statuses["diagnose-product"])
        self.assertEqual("completed", statuses["baseline-observe"])
        events = growth_service._events(self.data).all()
        self.assertEqual(2, len([row for row in events if row["kind"] == "action_recorded"]))

    def test_start_exposes_structured_capability_readiness(self):
        from core import execution_capabilities
        from tests.test_execution_capabilities import capability
        execution_capabilities.upsert(
            self.data, capability(verified=["read", "write"]), by="tester")
        service = growth_service.start(self.data, goal())
        self.assertEqual("structured", service["capability_readiness"]["mode"])
        github = next(row for row in service["capability_readiness"]["actions"]
                      if row["id"] == "github-engage")
        self.assertFalse(github["ready"])
        self.assertIn("verify_not_verified", github["missing"])

    def test_evaluation_distinguishes_unknown_primary_from_zero(self):
        got = growth_service.evaluate(
            self.data, self.sid,
            {"baseline": {"github_stars": 221, "x_followers": 38},
             "final": {"activated_users": None, "github_stars": 224, "x_followers": 41}},
            by="tester")
        outcomes = {row["id"]: row for row in got["outcomes"]}
        self.assertEqual("unknown", outcomes["activation"]["status"])
        self.assertEqual("achieved", outcomes["stars"]["status"])
        self.assertEqual("achieved", outcomes["followers"]["status"])
        self.assertFalse(got["primary_success"])
        self.assertEqual("fix_measurement", got["recommendation"]["kind"])

    def test_evaluation_uses_delta_and_does_not_hide_secondary_miss(self):
        got = growth_service.evaluate(
            self.data, self.sid,
            {"baseline": {"github_stars": 221, "x_followers": 38},
             "final": {"activated_users": 1, "github_stars": 221, "x_followers": 41}},
            by="tester")
        self.assertTrue(got["primary_success"])
        statuses = {row["id"]: row["status"] for row in got["outcomes"]}
        self.assertEqual("not_achieved", statuses["stars"])
        self.assertEqual("achieved", statuses["followers"])
        self.assertEqual("review_secondary_assets_not_volume", got["recommendation"]["kind"])


if __name__ == "__main__":
    unittest.main()
