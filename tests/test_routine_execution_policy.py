#!/usr/bin/env python3
"""Routine standing authorization: opt-in approval only, never weaker writes.

All send/vendor/SMTP boundaries are fakes.  No test performs network or real
account operations.
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import mcp_server
from channels import email_smtp
from core import execution_policy
from core.action_runner import ActionError, precheck, run_action
from core.execution_policy import allows, authorize, get_policy, revoke
from core.gates import Gate
from core.ledger import Ledger
from core.pipeline import WritePipeline, standard_pipeline
from core.planlint import PlanNotApproved, enforce_approval
from core.queue_runner import run_queue
from core.safety import set_pause
from core.throttle import Throttle


class RoutineTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()

    def make_pipeline(self, *, routine=True, account="", approval=None):
        ledger = Ledger(self.data / "ledger.jsonl")
        if approval is None:
            pipe = standard_pipeline(self.data, ledger, routine=routine, account=account)
        else:
            pipe = WritePipeline(
                gate=Gate(ledger, suppress=set()), ledger=ledger,
                throttle=Throttle(self.data / "state"), approval=approval,
                routine=routine, account=account)
        return pipe, ledger

    def run_fake(self, pipe, *, target="person@example.com", expect="hello",
                 readback="hello", dry_run=False, confirm=True):
        calls = []

        def callback(name):
            def run(item):
                calls.append(name)
                if name == "readback":
                    return readback
                if name == "confirm":
                    return confirm
            return run

        rec = pipe.run_one(
            {"id": "routine-1"}, channel="email", action="message",
            campaign="routine-campaign", transport="fake", expect=expect,
            target=target, compose=callback("compose"), fill=callback("fill"),
            readback=callback("readback"), send=callback("send"),
            confirm=callback("confirm"), shot=callback("shot"), dry_run=dry_run)
        return rec, calls

    def write_email_queue(self, *, account=None):
        queues = self.data / "queues"
        queues.mkdir(exist_ok=True)
        path = queues / "email-routine.json"
        queue = {
            "campaign": "email-routine", "channel": "email", "action": "message",
            "items": [{"id": "e1", "target": "person@example.com",
                       "subject": "Routine", "body": "A useful update"}],
        }
        if account is not None:
            queue["account"] = account
        path.write_text(json.dumps(queue), encoding="utf-8")
        return path


class RoutinePolicyTests(RoutineTestCase):
    def test_default_without_file_still_requires_plan(self):
        self.assertIsNone(get_policy(self.data))
        self.assertFalse(allows(self.data, "email", "message"))
        with self.assertRaises(PlanNotApproved):
            enforce_approval(self.data, "routine-campaign")
        with self.assertRaises(PlanNotApproved):
            enforce_approval(self.data, "routine-campaign", routine=True,
                             channel="email", action="message")

    def test_authorize_enables_only_explicit_routine_mode(self):
        policy = authorize(self.data, by="operator", actions=["email:message"])
        self.assertEqual(1, policy["version"])
        self.assertTrue(policy["enabled"])
        self.assertFalse(policy["revoked"])
        self.assertEqual(
            self.data / "policies" / "routine-marketing.json",
            execution_policy.policy_path(self.data))
        self.assertEqual(
            "routine-policy",
            enforce_approval(self.data, "routine-campaign", routine=True,
                             channel="email", action="message"))
        # Even adhoc cannot relabel a valid standing authorization.
        self.assertEqual(
            "routine-policy",
            enforce_approval(self.data, "routine-campaign", adhoc=True, routine=True,
                             channel="email", action="message"))
        with self.assertRaises(PlanNotApproved):
            enforce_approval(self.data, "routine-campaign")
        self.assertEqual("adhoc", enforce_approval(self.data, "routine-campaign", adhoc=True))

    def test_action_and_exact_account_scope(self):
        authorize(self.data, by="operator", actions=["email:message"], accounts=["acct-a"])
        self.assertTrue(allows(self.data, "email", "message", "acct-a"))
        self.assertFalse(allows(self.data, "email", "message", "acct-A"))
        self.assertFalse(allows(self.data, "email", "message", "acct-a-other"))
        self.assertFalse(allows(self.data, "email", "message", "default"))
        self.assertFalse(allows(self.data, "x", "publish", "acct-a"))
        with self.assertRaises(PlanNotApproved):
            enforce_approval(self.data, "routine-campaign", routine=True,
                             channel="email", action="message", account="acct-b")

    def test_empty_account_scope_is_default_only_not_wildcard(self):
        authorize(self.data, by="operator", actions=["x:publish"], accounts=[])
        self.assertTrue(allows(self.data, "x", "publish", ""))
        self.assertTrue(allows(self.data, "x", "publish", "default"))
        self.assertFalse(allows(self.data, "x", "publish", "named-account"))
        self.assertFalse(allows(self.data, "x", "publish", " default"))
        self.assertFalse(allows(self.data, "x", "publish", 0))

    def test_expiry_and_revoke_fail_closed(self):
        authorize(self.data, by="operator", actions=["github:reply"], days=1,
                  now=datetime.now(timezone.utc) - timedelta(days=2))
        self.assertFalse(allows(self.data, "github", "reply"))

        authorize(self.data, by="operator", actions=["github:reply"])
        self.assertTrue(allows(self.data, "github", "reply"))
        revoked = revoke(self.data, by="operator")
        self.assertTrue(revoked["revoked"])
        self.assertEqual("operator", revoked["revoked_by"])
        self.assertFalse(allows(self.data, "github", "reply"))
        with self.assertRaises(PlanNotApproved):
            enforce_approval(self.data, "routine-campaign", routine=True,
                             channel="github", action="reply")

    def test_payment_crisis_and_unknown_actions_are_not_routine(self):
        for operation in ("payment:charge", "contract:sign", "discount:grant",
                          "x:crisis", "unknown:publish"):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    authorize(self.data, by="operator", actions=[operation])
        authorize(self.data, by="operator", actions=["x:publish"])
        self.assertFalse(allows(self.data, "payment", "charge"))
        self.assertFalse(allows(self.data, "x", "crisis"))
        self.assertFalse(allows(self.data, "unknown", "publish"))
        self.assertNotIn("payment:charge", execution_policy.list_routine_actions())

    def test_malformed_or_broadened_policy_fails_closed(self):
        policy = authorize(self.data, by="operator", actions=["email:message"])
        policy["allow_actions"].append("payment:charge")
        execution_policy.policy_path(self.data).write_text(json.dumps(policy), encoding="utf-8")
        self.assertFalse(allows(self.data, "email", "message"))
        execution_policy.policy_path(self.data).write_text("not json", encoding="utf-8")
        self.assertFalse(allows(self.data, "email", "message"))


class RoutineExecutionTests(RoutineTestCase):
    def test_action_runner_nested_legacy_recheck_uses_routine_policy(self):
        queueless_target = "https://x.com/example/status/1"
        nested_modes = []

        def fake_xrun(action, args, *, go=False, campaign=None, ledger=None,
                      data_dir=None, log=print, **kwargs):
            if go:
                # Deliberately use the old adapter call shape: no routine args.
                nested_modes.append(enforce_approval(data_dir, campaign or ""))
            return 0

        with patch("channels.x.adapter.run", side_effect=fake_xrun) as vendor:
            dry = run_action(channel="x", action="reply", data_dir=self.data,
                             campaign="routine-campaign", go=False, routine=True,
                             target=queueless_target, text="helpful reply", log=lambda *_: None)
            self.assertEqual("dry-run", dry["result"])
            self.assertNotIn("approval", dry)
            with self.assertRaises(ActionError):
                run_action(channel="x", action="reply", data_dir=self.data,
                           campaign="routine-campaign", go=True, routine=True,
                           target=queueless_target, text="helpful reply", log=lambda *_: None)
            self.assertEqual(1, vendor.call_count)  # denied before vendor on real run

            authorize(self.data, by="operator", actions=["x:reply"], accounts=["acct-a"])
            rec = run_action(channel="x", action="reply", data_dir=self.data,
                             campaign="routine-campaign", go=True, routine=True,
                             account="acct-a", target=queueless_target,
                             text="helpful reply", log=lambda *_: None)
        self.assertEqual("committed", rec["result"])
        self.assertEqual("routine-policy", rec["approval"])
        self.assertEqual(["routine-policy"], nested_modes)

    def test_non_routine_action_still_requires_plan(self):
        authorize(self.data, by="operator", actions=["x:reply"])
        with self.assertRaises(ActionError):
            precheck("x", "reply", self.data, "routine-campaign", True, False,
                     routine=False)
        self.assertEqual(
            "routine-policy",
            precheck("x", "reply", self.data, "routine-campaign", True, False,
                     routine=True))

    def test_unverified_gate_is_not_downgraded(self):
        authorize(self.data, by="operator", actions=["github:reply"])
        with self.assertRaisesRegex(ActionError, "UNVERIFIED"):
            precheck("github", "reply", self.data, "routine-campaign", True, False,
                     routine=True)

    def test_pipeline_marks_routine_policy_in_result_and_ledger(self):
        authorize(self.data, by="operator", actions=["email:message"])
        pipe, ledger = self.make_pipeline()
        rec, calls = self.run_fake(pipe)
        self.assertEqual("committed", rec["result"])
        self.assertEqual("routine-policy", rec["approval"])
        self.assertIn("send", calls)
        self.assertEqual("routine-policy", ledger.all()[-1].get("approval"))

    def test_dry_run_does_not_require_policy_or_send(self):
        pipe, ledger = self.make_pipeline(routine=True)
        rec, calls = self.run_fake(pipe, dry_run=True)
        self.assertEqual("dry-run", rec["result"])
        self.assertNotIn("send", calls)
        self.assertNotIn("approval", rec)
        self.assertEqual("dry-run", ledger.all()[-1]["result"])

    def test_pause_and_dnc_are_not_downgraded(self):
        authorize(self.data, by="operator", actions=["email:message"])
        set_pause(self.data, True, reason="routine safety test")
        pipe, _ = self.make_pipeline()
        rec, calls = self.run_fake(pipe)
        self.assertEqual("rejected", rec["result"])
        self.assertIn("暂停", rec["detail"])
        self.assertEqual([], calls)

        set_pause(self.data, False)
        (self.data / "suppress.txt").write_text("person@example.com\n", encoding="utf-8")
        pipe, _ = self.make_pipeline()
        rec, calls = self.run_fake(pipe)
        self.assertEqual("rejected", rec["result"])
        self.assertIn("抑制", rec["detail"])
        self.assertNotIn("send", calls)

    def test_quota_and_readback_are_not_downgraded(self):
        authorize(self.data, by="operator", actions=["email:message"])
        throttle = Throttle(self.data / "state")
        throttle.daily_charge("email", "message", 100)
        pipe, _ = self.make_pipeline()
        rec, calls = self.run_fake(pipe)
        self.assertEqual("rejected", rec["result"])
        self.assertIn("日预算", rec["detail"])
        self.assertNotIn("send", calls)

        fresh = Path(self.tmp.name) / "fresh-data"
        fresh.mkdir()
        original_data = self.data
        self.data = fresh
        try:
            authorize(self.data, by="operator", actions=["email:message"])
            pipe, _ = self.make_pipeline()
            rec, calls = self.run_fake(pipe, readback="different")
            self.assertEqual("failed", rec["result"])
            self.assertIn("回读不一致", rec["detail"])
            self.assertNotIn("send", calls)
        finally:
            self.data = original_data

    def test_unknown_result_is_not_automatically_retried(self):
        authorize(self.data, by="operator", actions=["email:message"])
        pipe, _ = self.make_pipeline()
        first, calls = self.run_fake(pipe, confirm=False)
        self.assertEqual("indeterminate", first["result"])
        self.assertEqual(1, calls.count("send"))
        second, calls = self.run_fake(pipe, confirm=False)
        self.assertEqual("rejected", second["result"])
        self.assertIn("不重试", second["detail"])
        self.assertEqual(0, calls.count("send"))

    def test_old_approval_callback_and_real_typeerror_are_compatible(self):
        seen = []

        def old_callback(campaign):
            seen.append(campaign)
            return "plan"

        pipe, _ = self.make_pipeline(approval=old_callback)
        rec, _ = self.run_fake(pipe)
        self.assertEqual("committed", rec["result"])
        self.assertEqual("plan", rec["approval"])
        self.assertEqual(["routine-campaign"], seen)

        failures = []

        def broken_callback(campaign, **kwargs):
            failures.append((campaign, kwargs))
            raise TypeError("real approval failure")

        pipe, _ = self.make_pipeline(approval=broken_callback)
        rec, calls = self.run_fake(pipe)
        self.assertEqual("rejected", rec["result"])
        self.assertIn("real approval failure", rec["detail"])
        self.assertEqual(1, len(failures))  # never retried as a legacy callback
        self.assertEqual("email", failures[0][1]["channel"])
        self.assertEqual([], calls)

    def test_queue_passes_queue_account_to_precheck_and_action(self):
        queue = {
            "campaign": "q-routine", "channel": "github", "action": "reply",
            "account": "acct-a",
            "items": [{"id": "g1", "target": "issue:owner/repo#1", "body": "hello"}],
        }
        with patch("core.queue_runner.load_queue", return_value=queue), \
             patch("core.queue_runner.lint_queue", return_value=[]), \
             patch("core.queue_runner.precheck") as check, \
             patch("core.queue_runner.run_action", return_value={"result": "dry-run"}) as action:
            rows = run_queue(Path(self.tmp.name) / "q-routine.json", data_dir=self.data,
                             routine=True, log=lambda *_: None)
        self.assertEqual(1, len(rows))
        self.assertEqual("acct-a", check.call_args.kwargs["account"])
        self.assertTrue(check.call_args.kwargs["routine"])
        self.assertEqual("acct-a", action.call_args.kwargs["account"])
        self.assertTrue(action.call_args.kwargs["routine"])

    def test_email_queue_routine_and_dry_run_use_no_real_smtp(self):
        queue_path = self.write_email_queue(account="acct-a")
        with patch("channels.email_smtp.SMTPChannel") as smtp:
            dry = email_smtp.send_queue(queue_path, data_dir=self.data, go=False,
                                        routine=True, account="acct-a", log=lambda *_: None)
        self.assertEqual(["dry-run"], [r["result"] for r in dry])
        smtp.assert_not_called()

        authorize(self.data, by="operator", actions=["email:message"], accounts=["acct-a"])

        class FakeSMTP:
            sent = []

            def __init__(self):
                self.user = "sender@example.com"

            def _client(self):
                return self

            def send_message(self, message):
                self.sent.append(message)

            def close(self):
                pass

        with patch("channels.email_smtp.SMTPChannel", FakeSMTP):
            rows = run_queue(queue_path, data_dir=self.data, go=True,
                             routine=True, log=lambda *_: None)
        self.assertEqual(["committed"], [r["result"] for r in rows])
        self.assertEqual("routine-policy", rows[0]["approval"])
        self.assertEqual(1, len(FakeSMTP.sent))

    def test_mcp_has_no_routine_self_authorization_tool(self):
        names = {tool["name"] for tool in mcp_server.TOOLS}
        self.assertFalse(any("routine" in name.lower() for name in names))
        self.assertNotIn("mkt_authorize_routine", names)


if __name__ == "__main__":
    unittest.main()
