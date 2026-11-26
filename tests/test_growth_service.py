import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
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
        evidence = Path(self.data) / "metric-evidence.json"
        evidence.write_text('{"observed": true}\n', encoding="utf-8")
        self.metric_evidence = {"path": str(evidence),
                                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}

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
            self.data, self.sid, "x-publish", "completed", by="tester", minutes=12,
            completion={"kind": "attested_manual", "reason": "人工在平台侧确认已发布"})
        self.assertEqual(12, loaded["metrics"]["human_minutes_total"])
        self.assertEqual(4, loaded["metrics"]["accepted_tasks"])
        self.assertEqual(1, loaded["metrics"]["completed_attested"])
        self.assertEqual(0, loaded["metrics"]["completed_verified"])
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
        def observed(metric, value, window):
            operation = "count_unique" if metric == "activated_users" else (
                "sum" if metric == "github_stars" else "last")
            field = "subject_id" if metric == "activated_users" else "value"
            observations = []
            if metric != "activated_users" or value:
                observations = [{field: value if metric != "activated_users" else "user-1",
                                 "observed_at": window}]
            document = {
                "schema": "growovo.metric-evidence/1", "metric": metric,
                "account": "default", "window": window,
                "collector": {"id": "mkt metrics", "version": "1"},
                "raw_observations": observations,
                "calculation": {"definition_hash": "metric-def-1",
                                "operation": operation, "field": field},
                "value": value,
            }
            path = Path(self.data) / ("%s-%s.json" % (metric, window.replace("/", "-")))
            path.write_text(json.dumps(document), encoding="utf-8")
            ref = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            return {"value": value, "source": "observed", "window": window,
                    "definition_hash": "metric-def-1", "collected_by": "mkt metrics",
                    "evidence_refs": [ref]}

        baseline_window = "2026-11-17/2026-11-23"
        final_window = "2026-11-24/2026-11-30"
        got = growth_service.evaluate(
            self.data, self.sid,
            {"baseline": {"activated_users": observed("activated_users", 0, baseline_window),
                          "github_stars": observed("github_stars", 221, baseline_window),
                          "x_followers": observed("x_followers", 38, baseline_window)},
             "final": {"activated_users": observed("activated_users", 1, final_window),
                       "github_stars": observed("github_stars", 221, final_window),
                       "x_followers": observed("x_followers", 41, final_window)}},
            by="tester")
        self.assertTrue(got["primary_success"])
        self.assertTrue(got["primary_verified"])
        statuses = {row["id"]: row["status"] for row in got["outcomes"]}
        self.assertEqual("not_achieved", statuses["stars"])
        self.assertEqual("achieved", statuses["followers"])
        self.assertEqual("review_secondary_assets_not_volume", got["recommendation"]["kind"])

    def test_self_reported_numbers_are_target_met_but_not_verified(self):
        got = growth_service.evaluate(
            self.data, self.sid,
            {"baseline": {"activated_users": 0},
             "final": {"activated_users": 100}},
            by="tester")
        self.assertTrue(got["primary_success"])
        self.assertFalse(got["primary_verified"])
        self.assertEqual("owner_declared", got["verification"]["grade"])
        self.assertFalse(got["verification"]["same_basis"])
        self.assertEqual("verify_measurement", got["recommendation"]["kind"])

    def test_observed_metric_without_verifiable_evidence_is_not_verified(self):
        def observed(value, window):
            return {"value": value, "source": "observed", "window": window,
                    "definition_hash": "metric-def-1", "collected_by": "tester"}
        got = growth_service.evaluate(
            self.data, self.sid,
            {"baseline": {"activated_users": observed(0, "2026-11-17/2026-11-23")},
             "final": {"activated_users": observed(1, "2026-11-24/2026-11-30")}},
            by="tester")
        self.assertTrue(got["primary_success"])
        self.assertFalse(got["primary_verified"])
        self.assertFalse(got["verification"]["same_basis"])

    def test_raw_observations_and_calculation_can_verify_a_business_metric(self):
        def observed(value, window):
            document = {
                "schema": "growovo.metric-evidence/1", "metric": "activated_users",
                "account": "default", "window": window,
                "collector": {"id": "mkt metrics", "version": "1"},
                "raw_observations": ([{"subject_id": "user-1", "observed_at": window}]
                                     if value else []),
                "calculation": {"definition_hash": "metric-def-1",
                                "operation": "count_unique", "field": "subject_id"},
                "value": value,
            }
            path = Path(self.data) / ("evidence-%s.json" % window.replace("/", "-"))
            path.write_text(json.dumps(document), encoding="utf-8")
            ref = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            return {"value": value, "source": "observed", "window": window,
                    "definition_hash": "metric-def-1", "collected_by": "mkt metrics",
                    "evidence_refs": [ref]}
        got = growth_service.evaluate(
            self.data, self.sid,
            {"baseline": {"activated_users": observed(0, "2026-11-17/2026-11-23")},
             "final": {"activated_users": observed(1, "2026-11-24/2026-11-30")}},
            by="tester")
        self.assertTrue(got["primary_verified"])

    def test_unrelated_evidence_file_cannot_verify_a_business_metric(self):
        def observed(value, window):
            return {"value": value, "source": "observed", "window": window,
                    "definition_hash": "metric-def-1", "collected_by": "mkt metrics",
                    "evidence_refs": [self.metric_evidence]}
        got = growth_service.evaluate(
            self.data, self.sid,
            {"baseline": {"activated_users": observed(0, "2026-11-17/2026-11-23")},
             "final": {"activated_users": observed(1, "2026-11-24/2026-11-30")}},
            by="tester")
        self.assertTrue(got["primary_success"])
        self.assertFalse(got["primary_verified"])
        self.assertEqual("owner_declared", got["verification"]["grade"])

    def test_mixed_source_baseline_and_final_is_not_verified(self):
        got = growth_service.evaluate(
            self.data, self.sid,
            {"baseline": {"activated_users": {"value": 0, "source": "observed",
                                              "window": "2026-11-17/2026-11-23",
                                              "definition_hash": "metric-def-1"}},
             "final": {"activated_users": 100}},
            by="tester")
        self.assertTrue(got["primary_success"])
        self.assertFalse(got["primary_verified"])
        self.assertFalse(got["verification"]["same_basis"])


class GrowthServiceCompletionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.service = growth_service.start(self.data, goal())
        self.sid = self.service["id"]
        for action_id in ("diagnose-product", "baseline-observe", "asset-prepare"):
            growth_service.record_action(self.data, self.sid, action_id, "completed", by="tester")

    def receipt(self, action_id="x-publish", channel="x"):
        return {"action_id": action_id, "channel": channel,
                "attempt_id": "attempt-1", "account": "default", "target": "target",
                "sent_at": "2026-11-24T10:00:00+08:00",
                "idempotency_key": "claim-1", "readback": "https://example.test/1"}

    def complete_both_channels(self, completion):
        from core.ledger import Ledger
        for action_id, channel in (("x-publish", "x"), ("github-engage", "github")):
            block = completion(action_id, channel)
            if block.get("kind") == "verified":
                receipt = block["receipt"]
                Ledger(Path(self.data) / "ledger.jsonl").append(
                    channel=channel, action="publish", target="target", result="committed",
                    campaign=self.sid, evidence=receipt["readback"],
                    ref="ledger-" + action_id, claim_key=receipt["idempotency_key"],
                    growth_action_id=action_id, attempt_id=receipt["attempt_id"],
                    account=receipt["account"])
            growth_service.record_action(
                self.data, self.sid, action_id, "completed", by="tester", completion=block)

    def test_channel_completion_requires_explicit_kind(self):
        with self.assertRaisesRegex(ValueError, "completion.kind"):
            growth_service.record_action(self.data, self.sid, "x-publish", "completed", by="tester")

    def test_verified_completion_requires_every_receipt_field(self):
        with self.assertRaisesRegex(ValueError, "缺少字段"):
            growth_service.record_action(
                self.data, self.sid, "x-publish", "completed", by="tester",
                completion={"kind": "verified", "receipt": {"action_id": "x-publish"}})

    def test_receipt_must_match_the_action_it_completes(self):
        receipt = dict(self.receipt(), action_id="github-engage")
        with self.assertRaisesRegex(ValueError, "action_id"):
            growth_service.record_action(
                self.data, self.sid, "x-publish", "completed", by="tester",
                completion={"kind": "verified", "receipt": receipt})

    def test_committed_receipt_cannot_complete_another_action(self):
        from core.ledger import Ledger
        Ledger(Path(self.data) / "ledger.jsonl").append(
            channel="x", action="publish", target="target-A", result="committed",
            campaign=self.sid, evidence="receipt-A", ref="ledger-A", claim_key="claim-A",
            growth_action_id="x-publish", attempt_id="attempt-A", account="default")
        action = {"id": "x-publish-b", "kind": "channel", "channel": "x"}
        completion = {"kind": "verified", "receipt": {
            "action_id": "x-publish-b", "channel": "x", "attempt_id": "attempt-B",
            "account": "default", "target": "target-B", "sent_at": "2026-11-24T10:00:00+08:00",
            "idempotency_key": "claim-A", "readback": "receipt-A"}}
        with self.assertRaisesRegex(ValueError, "同一动作"):
            growth_service._completion_block(
                action, completion, by="tester", note="", data_dir=self.data,
                service_id=self.sid, service_account="default")

    def test_attested_completion_needs_a_reason(self):
        with self.assertRaisesRegex(ValueError, "原因"):
            growth_service.record_action(
                self.data, self.sid, "x-publish", "completed", by="tester",
                completion={"kind": "attested_manual"})

    def test_verified_completion_unlocks_downstream(self):
        self.complete_both_channels(
            lambda action_id, channel: {"kind": "verified",
                                        "receipt": self.receipt(action_id, channel)})
        loaded = growth_service.load(self.data, self.sid)
        action = next(row for row in loaded["actions"] if row["id"] == "x-publish")
        self.assertEqual("verified", action["completion"]["kind"])
        self.assertEqual(2, loaded["metrics"]["completed_verified"])
        nxt = growth_service.next_action(self.data, self.sid)
        self.assertEqual("final-observe", nxt["id"])
        self.assertNotIn("dependency_quality", nxt)

    def test_verified_completion_rejects_receipt_absent_from_ledger(self):
        with self.assertRaisesRegex(ValueError, "账本"):
            growth_service.record_action(
                self.data, self.sid, "x-publish", "completed", by="tester",
                completion={"kind": "verified", "receipt": self.receipt()})

    def test_legacy_completion_without_kind_is_not_trusted(self):
        from core import growth_runtime

        def mutate(current):
            for action in current["actions"]:
                if action["id"] == "x-publish":
                    action["status"] = "completed"
                    action.pop("completion", None)
                    action["evidence"] = None
            return current

        growth_runtime.update_service(self.data, self.sid, mutate)
        loaded = growth_service.load(self.data, self.sid)
        action = next(row for row in loaded["actions"] if row["id"] == "x-publish")
        self.assertEqual("declared", action["completion"]["kind"])
        self.assertTrue(action["completion"]["legacy"])
        # declared 不满足渠道依赖，下游不能继续。
        self.assertEqual("github-engage", growth_service.next_action(self.data, self.sid)["id"])

    def test_attested_completion_marks_downstream_degraded(self):
        self.complete_both_channels(
            lambda action_id, channel: {"kind": "attested_manual",
                                        "reason": "人工确认已发送"})
        nxt = growth_service.next_action(self.data, self.sid)
        self.assertEqual("final-observe", nxt["id"])
        self.assertEqual("attested", nxt["dependency_quality"])


if __name__ == "__main__":
    unittest.main()
