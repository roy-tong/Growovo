import tempfile
import unittest
from pathlib import Path

from core import growth_cycle, growth_service, risk_policy
from core.storage import AtomicJSONStore
from tests.test_growth_goals import goal


PROPOSAL = "growovo.growth-cycle-proposal/1"


class GrowthCycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.service = growth_service.start(self.data, goal())
        self.sid = self.service["id"]

    def test_request_is_observable(self):
        cycle = growth_cycle.request(self.data, self.sid, question="what next?",
                                     actor="tester", cycle_id="cycle-test")
        self.assertEqual("open", cycle["status"])
        self.assertIn("diagnose-product", [row["action_id"] for row in cycle["request"]["observations"]])
        self.assertTrue(Path(self.data, "state", "growth-cycles", self.sid,
                             "cycle-test.json").is_file())

    def test_proposal_without_grant_is_refused(self):
        growth_cycle.request(self.data, self.sid, actor="tester", cycle_id="cycle-test")
        result = growth_cycle.propose(self.data, self.sid, "cycle-test", {
            "schema": PROPOSAL, "producer_id": "host-agent",
            "proposals": [{"proposal_id": "p1", "action_class": "new_outreach",
                           "action_id": "x-publish", "rationale": "reach users"}]})
        self.assertEqual(1, len(result["refused"]))
        self.assertEqual("no_active_grant", result["refused"][0]["reason"])

    def test_granted_proposal_is_accepted_and_executable(self):
        risk_policy.grant(self.data, self.sid, action_class="new_outreach",
                          granted_by="owner", quota=1)
        growth_cycle.request(self.data, self.sid, actor="tester", cycle_id="cycle-test")
        result = growth_cycle.propose(self.data, self.sid, "cycle-test", {
            "schema": PROPOSAL, "producer_id": "host-agent",
            "proposals": [{"proposal_id": "p1", "action_class": "new_outreach",
                           "action_id": "x-publish", "rationale": "reach users"}]})
        self.assertEqual(1, len(result["accepted"]))

    def test_auto_action_can_execute(self):
        growth_cycle.request(self.data, self.sid, actor="tester", cycle_id="cycle-test")
        result = growth_cycle.propose(self.data, self.sid, "cycle-test", {
            "schema": PROPOSAL, "producer_id": "host-agent",
            "proposals": [{"proposal_id": "p1", "action_class": "observe",
                           "action_id": "diagnose-product", "rationale": "diagnose first"}]})
        self.assertEqual(1, len(result["accepted"]))
        executed = growth_cycle.execute(self.data, self.sid, "cycle-test", by="tester")
        self.assertEqual("executed", executed["status"])
        self.assertEqual("completed", executed["executions"][0]["result"]["status"])

    def test_duplicate_proposal_is_rejected(self):
        growth_cycle.request(self.data, self.sid, actor="tester", cycle_id="cycle-test")
        payload = {"schema": PROPOSAL, "producer_id": "host-agent",
                   "proposals": [{"proposal_id": "p1", "action_class": "observe",
                                  "action_id": "diagnose-product", "rationale": "diagnose"}]}
        growth_cycle.propose(self.data, self.sid, "cycle-test", payload)
        with self.assertRaises(ValueError):
            growth_cycle.propose(self.data, self.sid, "cycle-test", payload)

    def test_expired_cycle_refuses_late_proposal(self):
        growth_cycle.request(self.data, self.sid, actor="tester", cycle_id="cycle-test")
        path = growth_cycle.path(self.data, self.sid, "cycle-test")
        cycle = AtomicJSONStore(path).load()
        cycle["deadline"] = "2020-01-01T00:00:00+08:00"
        AtomicJSONStore(path).save(cycle)
        with self.assertRaises(ValueError) as ctx:
            growth_cycle.propose(self.data, self.sid, "cycle-test", {
                "schema": PROPOSAL, "producer_id": "host-agent",
                "proposals": [{"proposal_id": "p1", "action_class": "observe",
                               "action_id": "diagnose-product", "rationale": "late"}]})
        self.assertIn("过期", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
