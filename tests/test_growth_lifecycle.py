import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import gates, growth_engagement, growth_experiments, growth_lifecycle, growth_opportunities

ROOT = Path(__file__).resolve().parents[1]


class GrowthLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.service = "svc-test"

    def add_opportunity(self, author="alice"):
        growth_opportunities.import_opportunities(self.data, self.service, [{
            "source": "x", "external_id": "opp-1", "author": author,
            "problem": "Needs a reproducible first run", "trigger": "Asked for a working demo",
            "intent": "solution_seeking", "fit": "high", "evidence": ["fixture"],
            "asset_reproduced": True, "human_minutes": 5, "risk": "low",
            "observed_at": "2026-11-24T09:00:00Z",
        }])

    def add_conversation(self, author="alice", existing_user=False):
        growth_engagement.import_interactions(self.data, self.service, [{
            "source": "x", "external_id": "comment-1", "author": author,
            "body": "I tried the demo and have a question", "url": "https://example.test/c1",
            "received_at": "2026-11-24T09:00:00Z", "existing_user": existing_user,
            "conversation_key": "thread-1",
        }])

    def add_experiment_event(self, event):
        if not growth_experiments.list_experiments(self.data, self.service):
            growth_experiments.create(self.data, self.service, {
                "id": "lifecycle-events", "hypothesis": "fixture", "primary_metric": "activation",
                "target": 1, "window": {"start": "2026-11-20T00:00:00Z",
                                       "end": "2026-11-30T23:59:59Z"},
                "stop_rule": "stop on missing evidence",
            })
        growth_experiments.observe(
            self.data, self.service, "lifecycle-events", [event])

    def test_opportunity_starts_discovery_and_enters_followup_queue(self):
        self.add_opportunity()
        got = growth_lifecycle.project(
            self.data, self.service, now="2026-11-24T10:00:00Z")
        contact = next(row for row in got["contacts"] if row["subject_id"] == "x:alice")
        self.assertEqual("discovery", contact["stage"])
        self.assertEqual("opportunity", got["followup_queue"][0]["kind"])

    def test_open_existing_user_conversation_is_stale_followup(self):
        self.add_conversation(existing_user=True)
        got = growth_lifecycle.project(
            self.data, self.service, now="2026-11-24T11:00:00Z", stale_minutes=60)
        contact = next(row for row in got["contacts"] if row["subject_id"] == "x:alice")
        self.assertEqual("engagement", contact["stage"])
        item = next(row for row in got["followup_queue"] if row["kind"] == "conversation")
        self.assertTrue(item["stale"])
        self.assertEqual("existing_user", item["priority_class"])

    def test_unverified_activation_does_not_advance_lifecycle(self):
        self.add_experiment_event({
            "id": "activation-1", "type": "activation", "subject": "u1",
            "source": "product", "at": "2026-11-24T09:30:00Z",
        })
        got = growth_lifecycle.project(
            self.data, self.service, now="2026-11-24T10:00:00Z")
        contact = next(row for row in got["contacts"] if row["subject_id"] == "product:u1")
        self.assertEqual("unknown", contact["stage"])
        self.assertEqual(1, len(contact["unqualified_events"]))

    def test_verified_activation_advances_and_identity_map_merges_explicit_aliases(self):
        self.add_conversation()
        self.add_experiment_event({
            "id": "activation-1", "type": "activation", "subject": "u1",
            "source": "product", "at": "2026-11-24T09:30:00Z",
            "source_ref": "product://events/activation-1",
            "verification": {"status": "verified", "method": "product_database_export",
                             "ref": "buyer-controlled-export-1"},
        })
        unmerged = growth_lifecycle.project(
            self.data, self.service, now="2026-11-24T10:00:00Z")
        self.assertIn("product:u1", {row["subject_id"] for row in unmerged["contacts"]})
        merged = growth_lifecycle.project(
            self.data, self.service, now="2026-11-24T10:00:00Z",
            identity_map={"product:u1": "x:alice"})
        contact = next(row for row in merged["contacts"] if row["subject_id"] == "x:alice")
        self.assertEqual("activation", contact["stage"])
        self.assertIn("product:u1", contact["aliases"])

    def test_retention_needs_verified_event_and_earlier_verified_activation(self):
        self.add_experiment_event({
            "id": "retention-unverified", "type": "retention", "subject": "u1",
            "source": "product", "at": "2026-11-25T09:30:00Z",
            "source_ref": "product://events/retention-unverified",
        })
        got = growth_lifecycle.project(
            self.data, self.service, now="2026-11-25T10:00:00Z")
        contact = next(row for row in got["contacts"] if row["subject_id"] == "product:u1")
        self.assertEqual("unknown", contact["stage"])
        self.assertIn("verified", contact["unqualified_events"][0]["reason"])

    def test_verified_retention_without_prior_activation_does_not_advance(self):
        self.add_experiment_event({
            "id": "retention-1", "type": "retention", "subject": "u1",
            "source": "product", "at": "2026-11-25T09:30:00Z",
            "source_ref": "product://events/retention-1",
            "verification": {"status": "verified", "method": "product_database_export",
                             "ref": "buyer-controlled-export-2"},
        })
        got = growth_lifecycle.project(
            self.data, self.service, now="2026-11-25T10:00:00Z")
        contact = next(row for row in got["contacts"] if row["subject_id"] == "product:u1")
        self.assertEqual("unknown", contact["stage"])
        self.assertIn("earlier verified activation", contact["unqualified_events"][0]["reason"])

    def test_verified_retention_after_activation_advances(self):
        self.add_experiment_event({
            "id": "activation-1", "type": "activation", "subject": "u1",
            "source": "product", "at": "2026-11-24T09:30:00Z",
            "source_ref": "product://events/activation-1",
            "verification": {"status": "verified", "method": "product_database_export",
                             "ref": "buyer-controlled-export-1"},
        })
        self.add_experiment_event({
            "id": "retention-1", "type": "retention", "subject": "u1",
            "source": "product", "at": "2026-11-25T09:30:00Z",
            "source_ref": "product://events/retention-1",
            "verification": {"status": "verified", "method": "product_database_export",
                             "ref": "buyer-controlled-export-2"},
        })
        got = growth_lifecycle.project(
            self.data, self.service, now="2026-11-25T10:00:00Z")
        contact = next(row for row in got["contacts"] if row["subject_id"] == "product:u1")
        self.assertEqual("retention", contact["stage"])
        self.assertEqual([], contact["unqualified_events"])

    def test_suppressed_identities_leave_actionable_queue(self):
        Path(self.data, "suppress.txt").write_text("@alice\n", encoding="utf-8")
        self.add_opportunity()
        self.add_conversation()
        got = growth_lifecycle.project(
            self.data, self.service, now="2026-11-24T10:00:00Z")
        self.assertEqual([], got["followup_queue"])
        self.assertEqual(2, len(got["suppressed_followups"]))
        self.assertTrue(all(row["next_step"] == "suppressed"
                            for row in got["suppressed_followups"]))

    def test_contact_dnc_uses_the_same_suppression_source(self):
        Path(self.data, "contacts.jsonl").write_text(json.dumps({
            "id": "contact-1", "platforms": {"x": "@alice"},
            "state": {"dnc": True, "bounced": False},
        }) + "\n", encoding="utf-8")
        self.add_conversation()
        got = growth_lifecycle.project(
            self.data, self.service, now="2026-11-24T10:00:00Z")
        self.assertEqual([], got["followup_queue"])
        self.assertEqual(1, got["counts"]["suppressed_followups"])

    def test_damaged_suppression_source_fails_closed(self):
        Path(self.data, "contacts.jsonl").write_text("not-json\n", encoding="utf-8")
        self.add_conversation()
        with self.assertRaises(gates.SuppressionIntegrityError):
            growth_lifecycle.project(
                self.data, self.service, now="2026-11-24T10:00:00Z")

    def test_cli_returns_business_lifecycle_projection(self):
        self.add_conversation()
        result = subprocess.run([
            sys.executable, str(ROOT / "mkt.py"), "--workspace", self.data,
            "growth", "lifecycle", self.service, "--now", "2026-11-24T10:00:00Z",
        ], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual("growovo.lifecycle/1", payload["schema"])
        self.assertEqual(1, payload["counts"]["engagement"])


if __name__ == "__main__":
    unittest.main()
