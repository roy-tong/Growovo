import datetime as dt
import tempfile
import unittest

from core import risk_policy


class RiskPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = self.tmp.name
        self.sid = "svc-test"

    def test_defaults(self):
        self.assertTrue(risk_policy.explain(
            self.data, self.sid, action_class="observe")["allowed"])
        self.assertFalse(risk_policy.explain(
            self.data, self.sid, action_class="routine_reply")["allowed"])
        self.assertEqual("named_review_required", risk_policy.explain(
            self.data, self.sid, action_class="public_statement")["reason"])

    def test_grant_quota_and_revoke(self):
        grant = risk_policy.grant(
            self.data, self.sid, action_class="routine_reply",
            granted_by="owner", quota=1)
        first = risk_policy.explain(self.data, self.sid, action_class="routine_reply")
        self.assertTrue(first["allowed"])
        risk_policy.consume(self.data, self.sid, grant["grant_id"])
        second = risk_policy.explain(self.data, self.sid, action_class="routine_reply")
        self.assertFalse(second["allowed"])
        self.assertEqual("no_active_grant", second["reason"])

    def test_scope_must_match(self):
        risk_policy.grant(self.data, self.sid, action_class="new_outreach",
                          granted_by="owner", scope={"channel": "x"})
        self.assertTrue(risk_policy.explain(
            self.data, self.sid, action_class="new_outreach",
            scope={"channel": "x"})["allowed"])
        self.assertFalse(risk_policy.explain(
            self.data, self.sid, action_class="new_outreach",
            scope={"channel": "email"})["allowed"])

    def test_expired_grant_is_ignored(self):
        past = dt.datetime.now().astimezone() - dt.timedelta(days=2)
        risk_policy.grant(self.data, self.sid, action_class="new_outreach",
                          granted_by="owner", days=1, now=past)
        self.assertFalse(risk_policy.explain(
            self.data, self.sid, action_class="new_outreach")["allowed"])

    def test_revoke_is_immediate(self):
        grant = risk_policy.grant(self.data, self.sid, action_class="new_outreach",
                                  granted_by="owner")
        risk_policy.revoke(self.data, self.sid, grant["grant_id"], by="owner")
        self.assertFalse(risk_policy.explain(
            self.data, self.sid, action_class="new_outreach")["allowed"])

    def test_high_risk_classes_cannot_be_standing_grants(self):
        for action_class in ("public_statement", "money_contract"):
            with self.assertRaises(ValueError):
                risk_policy.grant(self.data, self.sid, action_class=action_class,
                                  granted_by="owner")


if __name__ == "__main__":
    unittest.main()
