import unittest

from core import schedule_recovery


class ScheduleRecoveryTest(unittest.TestCase):
    def test_pending_job_waits_without_creating_another_job(self):
        got = schedule_recovery.decide({"id": "w01"}, {"status": "pending"})
        self.assertEqual("wait", got["action"])
        self.assertFalse(got["new_job_allowed"])

    def test_expiry_requires_original_scope_and_window(self):
        job = {"id": "w01-r2", "retry_of": "w01"}
        got = schedule_recovery.decide(job, {"status": "expired", "reason": "job_expired"},
                                       original_scope_valid=True)
        self.assertEqual("new_recovery_job", got["action"])
        self.assertTrue(got["new_job_allowed"])
        blocked = schedule_recovery.decide(job, {"status": "expired", "reason": "job_expired"})
        self.assertEqual("manual_review", blocked["action"])
        self.assertFalse(blocked["new_job_allowed"])

    def test_build_change_is_diagnosed_without_a_new_campaign(self):
        got = schedule_recovery.decide(
            {"id": "w01"}, {"status": "failed", "reason": "source_version_changed"})
        self.assertEqual("diagnose_build", got["action"])
        self.assertFalse(got["new_job_allowed"])

    def test_approval_problem_reuses_the_same_job(self):
        missing = schedule_recovery.decide(
            {"id": "w01"}, {"status": "blocked", "reason": "wave_approval_required"})
        self.assertEqual("approve_same_job", missing["action"])
        approved = schedule_recovery.decide(
            {"id": "w01"}, {"status": "blocked", "reason": "wave_approval_required"},
            approval_valid=True)
        self.assertEqual("retry_same_job", approved["action"])

    def test_changed_schedule_is_reloaded_not_cloned(self):
        got = schedule_recovery.decide(
            {"id": "w01"}, {"status": "failed", "reason": "scheduled_job_changed"})
        self.assertEqual("reload_job", got["action"])
        self.assertFalse(got["new_job_allowed"])

    def test_unknown_dispatch_requires_reconciliation(self):
        got = schedule_recovery.decide(
            {"id": "w01"}, {"status": "needs_reconciliation", "reason": "dispatch_state_unknown"})
        self.assertEqual("reconcile", got["action"])
        self.assertFalse(got["new_job_allowed"])


if __name__ == "__main__":
    unittest.main()
