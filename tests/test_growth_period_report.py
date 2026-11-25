import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import growth_cycle, growth_engagement, growth_experiments, growth_period_report, growth_service
from core.storage import AtomicJSONStore, StorageError
from tests.test_growth_goals import goal

ROOT = Path(__file__).resolve().parents[1]
PROPOSAL = "growovo.growth-cycle-proposal/1"


class GrowthPeriodReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.service = growth_service.start(self.data, goal())
        self.sid = self.service["id"]

    def make_cycle(self, cycle_id, created_at):
        cycle = growth_cycle.request(
            self.data, self.sid, actor="tester", cycle_id=cycle_id, question="What next?")
        cycle["created_at"] = created_at
        AtomicJSONStore(growth_cycle.path(self.data, self.sid, cycle_id)).save(cycle)
        return cycle

    def test_report_separates_execution_business_state_and_user_decisions(self):
        self.make_cycle("cycle-executed", "2026-11-24T10:00:00+00:00")
        growth_cycle.propose(self.data, self.sid, "cycle-executed", {
            "schema": PROPOSAL, "producer_id": "host",
            "proposals": [{"proposal_id": "p1", "action_class": "observe",
                           "action_id": "diagnose-product", "rationale": "diagnose"}]})
        growth_cycle.execute(self.data, self.sid, "cycle-executed", by="tester")
        executed = growth_cycle.status(self.data, self.sid, "cycle-executed")
        executed["executed_at"] = "2026-11-24T10:05:00+00:00"
        AtomicJSONStore(growth_cycle.path(self.data, self.sid, "cycle-executed")).save(executed)
        self.make_cycle("cycle-review", "2026-11-24T11:00:00+00:00")
        growth_cycle.propose(self.data, self.sid, "cycle-review", {
            "schema": PROPOSAL, "producer_id": "host",
            "proposals": [{"proposal_id": "p2", "action_class": "new_outreach",
                           "action_id": "x-publish", "rationale": "needs authorization"}]})
        self.make_cycle("cycle-open", "2026-11-24T12:00:00+00:00")
        self.make_cycle("cycle-old", "2026-11-01T12:00:00+00:00")
        growth_engagement.import_interactions(self.data, self.sid, [{
            "source": "x", "external_id": "comment-1", "author": "alice",
            "body": "I tried the demo", "url": "https://example.test/c1",
            "received_at": "2026-11-24T09:00:00Z", "conversation_key": "thread-1",
        }])
        growth_experiments.create(self.data, self.sid, {
            "id": "lifecycle-events", "hypothesis": "fixture", "primary_metric": "activation",
            "target": 1, "window": {"start": "2026-11-20T00:00:00Z", "end": "2026-11-30T23:59:59Z"},
            "stop_rule": "stop on missing evidence"})
        growth_experiments.observe(self.data, self.sid, "lifecycle-events", [{
            "id": "activation-1", "type": "activation", "subject": "u1", "source": "product",
            "at": "2026-11-24T09:30:00Z", "source_ref": "product://events/activation-1",
            "verification": {"status": "verified", "method": "product_database_export",
                             "ref": "buyer-controlled-export-1"}}])
        got = growth_period_report.build(
            self.data, self.sid, start="2026-11-24T00:00:00Z", end="2026-11-25T00:00:00Z",
            identity_map={"product:u1": "x:alice"})
        self.assertEqual("growovo.growth-period-report/1", got["schema"])
        self.assertEqual(3, got["cycles"]["total"])
        self.assertEqual(1, got["cycles"]["executed"])
        self.assertEqual(1, got["execution"]["completed_action_results"])
        self.assertEqual(1, got["business_observations"]["engagement_contacts"])
        self.assertEqual(1, got["business_observations"]["activation_contacts"])
        self.assertEqual("descriptive_not_causal", got["business_observations"]["interpretation"])
        decisions = {(row["kind"], row["cycle_id"]) for row in got["user_decisions"]}
        self.assertIn(("proposal_review", "cycle-review"), decisions)
        self.assertIn(("open_cycle", "cycle-open"), decisions)
        self.assertNotIn("cycle-old", json.dumps(got["cycles"]))

    def test_cross_midnight_execution_belongs_to_execution_day_not_creation_day(self):
        self.make_cycle("cycle-cross-day", "2026-11-24T23:55:00+00:00")
        growth_cycle.propose(self.data, self.sid, "cycle-cross-day", {
            "schema": PROPOSAL, "producer_id": "host",
            "proposals": [{"proposal_id": "p1", "action_class": "observe",
                           "action_id": "diagnose-product", "rationale": "diagnose"}]})
        growth_cycle.execute(self.data, self.sid, "cycle-cross-day", by="tester")
        yesterday = growth_period_report.build(
            self.data, self.sid, start="2026-11-24T00:00:00Z", end="2026-11-25T00:00:00Z")
        today = growth_period_report.build(
            self.data, self.sid, start="2026-11-25T00:00:00Z", end="2026-11-26T00:00:00Z")
        self.assertEqual(1, yesterday["cycles"]["total"])
        self.assertEqual(0, yesterday["execution"]["completed_action_results"])
        self.assertEqual(0, today["cycles"]["total"])
        self.assertEqual(1, today["execution"]["completed_action_results"])
        self.assertEqual(1, today["execution"]["executed"])

    def test_damaged_cycle_is_not_silently_omitted(self):
        self.make_cycle("cycle-good", "2026-11-24T10:00:00+00:00")
        Path(self.data, "state", "growth-cycles", self.sid, "broken.json").write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(StorageError, "JSON"):
            growth_period_report.build(
                self.data, self.sid, start="2026-11-24T00:00:00Z", end="2026-11-25T00:00:00Z")

    def test_cli_returns_json_and_business_markdown(self):
        self.make_cycle("cycle-open", "2026-11-24T12:00:00+00:00")
        base = [sys.executable, str(ROOT / "mkt.py"), "--workspace", self.data,
                "growth", "report", self.sid,
                "--start", "2026-11-24T00:00:00Z", "--end", "2026-11-25T00:00:00Z"]
        result = subprocess.run(base, cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(1, json.loads(result.stdout)["cycles"]["open"])
        rendered = subprocess.run(base + ["--format", "md"], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, rendered.returncode, rendered.stdout + rendered.stderr)
        self.assertIn("执行", rendered.stdout)
        self.assertIn("激活", rendered.stdout)
        self.assertIn("需要你决定", rendered.stdout)


if __name__ == "__main__":
    unittest.main()
