"""执行级审批指纹与计划结构。"""
import json
import tempfile
import unittest
from pathlib import Path

from core import approval, execute, planlint, strategy


class ExecutionApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.strategy = strategy.build({"name": "X"}, focus={"primary": "email"}, analysis={})
        self.result = execute.build(
            self.strategy,
            [{"target": "user@example.com", "body": "先给一个可复现的解决办法"}],
            campaign="c1", data_dir=self.data)

    def test_observation_criteria_is_allowed(self):
        plan = json.loads(Path(self.result["plan"]).read_text())
        self.assertEqual([], planlint.lint_plan(plan))
        self.assertNotIn("target", plan["success_criteria"])

    def test_queue_tamper_invalidates_approval(self):
        plan, _, _ = approval.approve_plan(self.data, "c1", by="roy", account="acct-a", workspace=self.tmp)
        queue_path = Path(self.result["queue"])
        queue = json.loads(queue_path.read_text())
        queue["items"][0]["body"] = "批准后被改写的正文"
        queue_path.write_text(json.dumps(queue))
        with self.assertRaises(approval.ApprovalError):
            approval.verify_approval(plan, data_dir=self.data, account="acct-a", workspace=self.tmp)

    def test_account_and_workspace_are_bound(self):
        plan, _, _ = approval.approve_plan(self.data, "c1", by="roy", account="acct-a", workspace=self.tmp)
        with self.assertRaises(approval.ApprovalError):
            approval.verify_approval(plan, data_dir=self.data, account="acct-b", workspace=self.tmp)
        with self.assertRaises(approval.ApprovalError):
            approval.verify_approval(plan, data_dir=self.data, account="acct-a", workspace="/tmp/other")

    def test_declared_but_missing_queue_cannot_be_approved(self):
        plan = json.loads(Path(self.result["plan"]).read_text())
        plan["waves"][0]["queue"] = "missing-queue.json"
        plan_path = Path(self.result["plan"])
        plan_path.write_text(json.dumps(plan))
        with self.assertRaises(approval.ApprovalError):
            approval.approve_plan(self.data, "c1", by="roy")

    def test_revoke_blocks_execution(self):
        plan, _, _ = approval.approve_plan(self.data, "c1", by="roy")
        plan, _ = approval.revoke_plan(self.data, "c1", by="roy")
        with self.assertRaises(approval.ApprovalError):
            approval.verify_approval(plan, data_dir=self.data)


if __name__ == "__main__":
    unittest.main()
