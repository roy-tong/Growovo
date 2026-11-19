#!/usr/bin/env python3
"""目标退出与个体旅程（G-NEXT-2 / GN2-01～14）验收。

全部在临时目录构造 fixture；零发送、零网络、零真实账号。
覆盖：目标达成、UNKNOWN 不补 0、退出优先、窗口/去重/身份、计划与执行分区、
未决事故不重试、迟到事件版本化、归因不写因果、过期不等于失败、确定性重放、
契约冲突不覆盖、暂停与存储损坏的显式降级。
"""
import contextlib
import hashlib
import io
import json
import smtplib
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from core import goal_exit

NOW = "2026-11-19T10:00:00"
TARGET = "reader@example.com"
RUN = "R1"

ACCOUNTS = {
    "version": 1,
    "accounts": [{
        "id": "acc-1",
        "platform": "email",
        "handle": "owner@example.com",
        "identity_type": "pseudonymous",
        "status": "eligible",
    }],
    "audit": [],
}

RUN_RECORD = {"schema": "growovo.grow-run/2", "id": RUN,
              "status": "ready_to_arm"}


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _event(event_id, **overrides):
    rec = {
        "event_id": event_id,
        "event_type": "activation",
        "subject_id": TARGET,
        "occurred_at": "2026-11-18T10:00:00",
        "source_system": "product_export",
        "source_ref": "export-%s" % event_id,
        "campaign_id": RUN,
        "evidence_hash": "sha256:ev-%s" % event_id,
        "verification_state": "verified",
        "imported_at": "2026-11-19T09:00:00",
        "imported_by": "operator",
    }
    rec.update(overrides)
    return rec


def _reply(case_id, **overrides):
    rec = {
        "reply_case_id": case_id,
        "version": 1,
        "target": TARGET,
        "classification_candidate": "neutral",
        "human_label": "",
        "dnc_state": "none",
        "link_state": "linked",
        "received_at": "2026-11-18T12:00:00",
        "imported_at": "2026-11-19T09:00:00",
    }
    rec.update(overrides)
    return rec


def _ledger_row(ref, **overrides):
    rec = {
        "ts": "2026-11-17T09:00:00",
        "channel": "x",
        "action": "post",
        "target": TARGET,
        "result": "committed",
        "campaign": RUN,
        "ref": ref,
    }
    rec.update(overrides)
    return rec


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class GoalExitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        (self.data / "accounts.json").write_text(
            json.dumps(ACCOUNTS), encoding="utf-8")
        _write_jsonl(self.data / "runs.jsonl", [RUN_RECORD])
        (self.data / "suppress.txt").touch()

    def tearDown(self):
        self.tmp.cleanup()

    # -- helpers -----------------------------------------------------------

    def contract(self, **overrides):
        opts = {
            "run_id": RUN,
            "goal_event": {"event_type": "activation", "target": 1},
            "observation_window": {"start": "2026-11-01",
                                   "end": "2026-11-30"},
            "created_by": "tester",
        }
        opts.update(overrides)
        return goal_exit.build_contract(**opts)

    def add_events(self, *rows):
        _write_jsonl(self.data / "outcomes" / "product_events.jsonl", rows)

    def add_ledger(self, *rows):
        _write_jsonl(self.data / "ledger.jsonl", rows)

    def add_replies(self, *rows):
        _write_jsonl(self.data / "replies" / "reply-cases.jsonl", rows)

    def add_qualifications(self, *rows):
        _write_jsonl(self.data / "outcomes" / "qualifications.jsonl", rows)

    def add_queue(self, name="q1.json", **overrides):
        rec = {"campaign": RUN, "channel": "x", "action": "post",
               "items": [{"id": "item-1", "target": TARGET,
                          "scheduled_for": "2026-11-20T09:00:00"}]}
        rec.update(overrides)
        path = self.data / "queues" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")

    def evaluate(self, contract=None, subject=TARGET, now=NOW, **kwargs):
        contract = contract or self.contract()
        return goal_exit.evaluate(self.data, contract, subject_ref=subject,
                                  now=now, **kwargs)

    # -- GN2-01 目标达成与停止记录 -----------------------------------------

    def test_gn2_01_goal_met_and_stop_dry_run_then_apply(self):
        self.add_events(_event("e1"))
        projection = self.evaluate()
        self.assertEqual(projection["state"], "goal_met")
        self.assertEqual(projection["goal_result"]["observed"], 1)
        self.assertTrue(projection["goal_result"]["met"])
        self.assertEqual(projection["attribution"]["causal_credit"],
                         "not_claimed")

        stops = self.data / "outcomes" / "journey" / "stop-commands.jsonl"
        dry = goal_exit.write_stop(self.data, projection)
        self.assertEqual(dry["status"], "would_write")
        self.assertFalse(stops.exists())

        written = goal_exit.write_stop(self.data, projection, dry_run=False)
        self.assertEqual(written["status"], "written")
        self.assertTrue(stops.exists())
        again = goal_exit.write_stop(self.data, projection, dry_run=False)
        self.assertEqual(again["status"], "unchanged")
        self.assertEqual(len(stops.read_text(encoding="utf-8").splitlines()), 1)

    # -- GN2-02 没有授权导入时是 UNKNOWN，不是 0 ---------------------------

    def test_gn2_02_unknown_without_authorized_imports(self):
        projection = self.evaluate()
        self.assertEqual(projection["state"], "unknown")
        self.assertFalse(projection["goal_result"]["met"])
        self.assertEqual(projection["goal_result"]["observed"], 0)
        self.assertIn("不是 0", projection["state_reason"])
        self.assertTrue(projection["next_allowed_action"].startswith(
            "repair_source"))
        text = goal_exit.render_text(projection)
        self.assertIn("unknown 表示没有观察到，不等于 0 或失败", text)

    # -- GN2-03 退出优先于目标达成 -----------------------------------------

    def test_gn2_03_dnc_outranks_goal_met(self):
        self.add_events(_event("e1"))
        self.add_replies(_reply("rc1", dnc_state="active"))
        projection = self.evaluate()
        self.assertEqual(projection["state"], "exited")
        self.assertEqual(projection["exits"][0]["kind"], "dnc")
        self.assertIn("不再发新动作", projection["state_reason"])
        self.assertEqual(projection["next_allowed_action"],
                         "none: DNC/退出是终态，本模块不提供解除")

    # -- GN2-04 窗口外事件不计入 ------------------------------------------

    def test_gn2_04_outside_window_not_counted(self):
        self.add_events(
            _event("old", occurred_at="2026-10-01T10:00:00"),
            _event("future", occurred_at="2026-12-01T10:00:00"),
        )
        projection = self.evaluate()
        self.assertEqual(projection["goal_result"]["observed"], 0)
        kinds = [e["event_id"] for e in projection["events_outside_window"]]
        self.assertEqual(sorted(kinds), ["future", "old"])
        self.assertEqual(projection["state"], "active")

    # -- GN2-05 重复事件只计一次 ------------------------------------------

    def test_gn2_05_duplicate_ids_and_content_count_once(self):
        self.add_events(
            _event("e1"),
            _event("e1", evidence_hash="sha256:dup-id"),
            _event("e2", source_ref="export-e1",
                   occurred_at="2026-11-18T10:00:00"),
        )
        projection = self.evaluate()
        self.assertEqual(projection["goal_result"]["observed"], 1)
        self.assertEqual(len(projection["goal_result"]["events"]), 1)

    # -- GN2-06 身份只做显式归一 ------------------------------------------

    def test_gn2_06_identity_normalization_and_blank_rejected(self):
        self.add_events(
            _event("case", subject_id="Reader@Example.COM"),
            _event("other", subject_id="reader2@example.com"),
        )
        projection = self.evaluate()
        self.assertEqual(projection["goal_result"]["observed"], 1)
        self.assertEqual(projection["subject"]["identity_basis"], "email_exact")

        with self.assertRaises(goal_exit.GoalExitError):
            self.evaluate(subject="   ")

    def test_gn2_06b_identity_map_merges_alias_explicitly(self):
        self.add_events(_event("e1", subject_id="alias@example.com"))
        projection = self.evaluate(
            subject="alias@example.com",
            identity_map={"alias@example.com": TARGET})
        self.assertEqual(projection["goal_result"]["observed"], 1)
        self.assertEqual(projection["subject"]["normalized"], TARGET)
        self.assertEqual(projection["subject"]["identity_basis"], "explicit_map")

    # -- GN2-07 计划动作与已执行动作分区 ----------------------------------

    def test_gn2_07_planned_and_executed_partition(self):
        self.add_events(_event("e1"))
        self.add_ledger(_ledger_row("L-1", result="committed"))
        self.add_queue()
        self.add_queue(name="q2.json", action="reply",
                       items=[{"id": "item-2", "target": TARGET}])
        projection = self.evaluate()
        counts = projection["actions"]["counts"]
        self.assertEqual(counts["executed"], 1)
        self.assertEqual(counts["planned"], 1)
        self.assertEqual(projection["actions"]["planned"][0]["action"],
                         "reply")

    # -- GN2-08 未决外部动作不自动重试 ------------------------------------

    def test_gn2_08_indeterminate_blocks_and_offers_manual_resolve(self):
        self.add_ledger(_ledger_row("L-9", result="indeterminate"))
        projection = self.evaluate()
        self.assertEqual(projection["state"], "blocked")
        incident = projection["blocked"]["incidents"][0]
        self.assertEqual(incident["kind"], "indeterminate_external_action")
        self.assertIn("mkt ledger resolve L-9", incident["next_command"])
        self.assertIn("禁止自动重试", projection["next_allowed_action"])
        text = goal_exit.render_text(projection)
        self.assertIn("未决事故", text)
        self.assertIn("mkt ledger resolve", text)

    # -- GN2-09 迟到事件产生新版本并保留旧行 ------------------------------

    def test_gn2_09_late_event_appends_revision_and_keeps_old_row(self):
        self.add_events(_event("seed", event_type="signup",
                               occurred_at="2026-10-01T10:00:00"))
        first = self.evaluate()
        self.assertEqual(first["state"], "active")
        goal_exit.write_projection(self.data, first, dry_run=False)
        path = self.data / "outcomes" / "journey" / "journey-projections.jsonl"
        first_line = path.read_text(encoding="utf-8").splitlines()[0]

        self.add_events(_event("late"))
        second = self.evaluate(now="2026-11-19T11:00:00")
        self.assertEqual(second["state"], "goal_met")
        result = goal_exit.write_projection(self.data, second, dry_run=False)
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["supersedes"], first["projection_id"])

        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], first_line)
        self.assertEqual(len(goal_exit.load_projections(self.data)), 2)

        again = goal_exit.write_projection(self.data, second, dry_run=False)
        self.assertEqual(again["status"], "unchanged")
        self.assertEqual(
            len(path.read_text(encoding="utf-8").splitlines()), 2)

    # -- GN2-10 多触点归因不写因果 ----------------------------------------

    def test_gn2_10_multi_touch_attribution_not_claimed(self):
        self.add_events(_event("e1"))
        self.add_ledger(
            _ledger_row("L-1", ts="2026-11-15T09:00:00"),
            _ledger_row("L-2", ts="2026-11-17T09:00:00", action="reply"),
        )
        projection = self.evaluate()
        attribution = projection["attribution"]
        self.assertEqual(attribution["touch_count"], 2)
        self.assertEqual(attribution["causal_credit"], "not_claimed")
        self.assertIn("不证明", attribution["note"])
        self.assertEqual(attribution["state"], "direct_receipt")

    # -- GN2-11 过期不等于失败 --------------------------------------------

    def test_gn2_11_expired_is_not_failure(self):
        contract = self.contract(
            observation_window={"start": "2026-09-01", "end": "2026-09-30"})
        self.add_events(
            _event("signup-1", event_type="signup",
                   occurred_at="2026-09-10T10:00:00"))
        projection = self.evaluate(contract=contract)
        self.assertEqual(projection["state"], "expired")
        self.assertTrue(projection["next_allowed_action"].startswith(
            "close_or_renew"))
        self.assertIn("不等同失败", projection["state_reason"])
        text = goal_exit.render_text(projection)
        self.assertIn("expired 表示没有观察到，不等于 0 或失败", text)

    # -- GN2-12 达成后出现 DNC → 退出 -------------------------------------

    def test_gn2_12_goal_met_then_dnc_exits(self):
        self.add_events(_event("e1"))
        self.assertEqual(self.evaluate()["state"], "goal_met")
        self.add_replies(_reply("rc1", dnc_state="pending"))
        projection = self.evaluate(now="2026-11-19T12:00:00")
        self.assertEqual(projection["state"], "exited")
        self.assertEqual(projection["exits"][0]["kind"], "dnc")

    # -- GN2-13 同输入同 now 完全重放 -------------------------------------

    def test_gn2_13_deterministic_replay(self):
        self.add_events(_event("e1"))
        self.add_ledger(_ledger_row("L-1"))
        first = self.evaluate()
        second = self.evaluate()
        self.assertEqual(first, second)
        self.assertEqual(first["projection_id"], second["projection_id"])
        self.assertEqual(first["evaluated_at"], NOW)

    # -- GN2-14 零发送／零网络 --------------------------------------------

    def test_gn2_14_no_send_or_network(self):
        self.add_events(_event("e1"))
        before = _sha256(self.data / "outcomes" / "product_events.jsonl")
        ledger_before = (self.data / "ledger.jsonl").read_text(
            encoding="utf-8") if (self.data / "ledger.jsonl").exists() else ""

        def boom(*args, **kwargs):
            raise AssertionError("evaluate 不得发送或联网")

        with mock.patch.object(smtplib, "SMTP", boom), \
                mock.patch.object(urllib.request, "urlopen", boom):
            projection = self.evaluate()
            goal_exit.write_projection(self.data, projection, dry_run=False)
            goal_exit.write_stop(self.data, projection, dry_run=False)
            goal_exit.render_text(projection)
            goal_exit.run_projection(self.data, RUN)

        self.assertEqual(before, _sha256(
            self.data / "outcomes" / "product_events.jsonl"))
        after_ledger = (self.data / "ledger.jsonl").read_text(
            encoding="utf-8") if (self.data / "ledger.jsonl").exists() else ""
        self.assertEqual(ledger_before, after_ledger)

    # -- 额外：契约校验与冲突不覆盖 --------------------------------------

    def test_contract_rejects_unbounded_window_and_bad_exit(self):
        with self.assertRaises(goal_exit.GoalExitError):
            goal_exit.build_contract(
                run_id=RUN,
                goal_event={"event_type": "activation"},
                observation_window={"start": "", "end": ""})
        with self.assertRaises(goal_exit.GoalExitError):
            goal_exit.build_contract(
                run_id=RUN,
                goal_event={"event_type": "download"},
                observation_window={"start": "2026-11-01",
                                    "end": "2026-11-30"})
        with self.assertRaises(goal_exit.GoalExitError):
            goal_exit.build_contract(
                run_id=RUN,
                goal_event={"event_type": "activation"},
                observation_window={"start": "2026-11-01",
                                    "end": "2026-11-30"},
                exit_events=[{"kind": "dnc"}])

    def test_contract_conflict_does_not_overwrite(self):
        contract = self.contract()
        goal_exit.write_contract(self.data, contract, dry_run=False)
        path = (self.data / "outcomes" / "journey"
                / "goal-exit-contracts.jsonl")
        original = path.read_text(encoding="utf-8")

        forged = json.loads(json.dumps(contract))
        forged["goal"]["label"] = "another meaning"
        forged["fingerprint"] = goal_exit.contract_fingerprint(forged)
        with self.assertRaises(goal_exit.GoalExitError):
            goal_exit.write_contract(self.data, forged, dry_run=False)

        self.assertEqual(original, path.read_text(encoding="utf-8"))
        conflicts = self.data / "outcomes" / "journey" / "journey-conflicts.jsonl"
        self.assertEqual(
            len(conflicts.read_text(encoding="utf-8").splitlines()), 1)

    def test_contract_write_is_idempotent(self):
        contract = self.contract()
        first = goal_exit.write_contract(self.data, contract, dry_run=False)
        second = goal_exit.write_contract(self.data, contract, dry_run=False)
        self.assertEqual(first["status"], "written")
        self.assertEqual(second["status"], "unchanged")
        path = (self.data / "outcomes" / "journey"
                / "goal-exit-contracts.jsonl")
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)

    def test_contract_and_projection_do_not_touch_canonical(self):
        self.add_events(_event("e1"))
        self.add_ledger(_ledger_row("L-1"))
        events_hash = _sha256(self.data / "outcomes" / "product_events.jsonl")
        ledger_hash = _sha256(self.data / "ledger.jsonl")
        contract = self.contract()
        goal_exit.write_contract(self.data, contract, dry_run=False)
        projection = self.evaluate(contract=contract)
        goal_exit.write_projection(self.data, projection, dry_run=False)
        goal_exit.write_stop(self.data, projection, dry_run=False)
        self.assertEqual(events_hash,
                         _sha256(self.data / "outcomes" / "product_events.jsonl"))
        self.assertEqual(ledger_hash, _sha256(self.data / "ledger.jsonl"))

    # -- 额外：DNC 写入失败、暂停、契约退出、资格、坏存储 -----------------

    def test_dnc_write_failure_blocks(self):
        self.add_replies(_reply("rc1", dnc_state="failed"))
        projection = self.evaluate()
        self.assertEqual(projection["state"], "blocked")
        kinds = [b["kind"] for b in projection["blocked"]["reasons"]]
        self.assertIn("dnc_write_failed", kinds)

    def test_paused_workspace_blocks_new_actions(self):
        self.add_events(
            _event("old", occurred_at="2026-10-01T10:00:00"))
        (self.data / "state").mkdir(parents=True, exist_ok=True)
        (self.data / "state" / "PAUSED").write_text("paused\n",
                                                    encoding="utf-8")
        projection = self.evaluate()
        self.assertEqual(projection["state"], "paused")
        self.assertTrue(projection["next_allowed_action"].startswith(
            "wait_unpause"))

    def test_declared_exit_is_only_linked_to_its_subject(self):
        contract = self.contract(exit_events=[{
            "kind": "manual_stop", "source_ref": "ticket-9",
            "subject_ref": TARGET}])
        self.add_events(_event("e1"))
        projection = self.evaluate(contract=contract)
        self.assertEqual(projection["state"], "exited")
        self.assertEqual(projection["exits"][0]["kind"], "manual_stop")

        other = self.contract(exit_events=[{
            "kind": "manual_stop", "source_ref": "ticket-9",
            "subject_ref": "someone-else@example.com"}])
        projection = self.evaluate(contract=other)
        self.assertEqual(projection["state"], "goal_met")
        self.assertFalse(projection["exit_declarations"][0]["subject_linked"])

    def test_unqualified_events_are_counted_but_not_merged(self):
        self.add_events(_event("e1"), _event("e2"))
        self.add_qualifications({"event_id": "e2", "qualifies": False})
        projection = self.evaluate()
        self.assertEqual(projection["goal_result"]["observed"], 1)
        self.assertEqual(len(projection["events_unqualified"]), 1)

    def test_interested_reply_is_unresolved_not_goal(self):
        self.add_replies(_reply("rc1", human_label="interested"))
        projection = self.evaluate()
        self.assertEqual(projection["state"], "unknown")
        kinds = [u["kind"] for u in projection["unresolved_evidence"]]
        self.assertIn("reply_interested_not_goal", kinds)

    def test_suppression_file_exits_and_never_clears(self):
        (self.data / "suppress.txt").write_text(TARGET + "\n",
                                                encoding="utf-8")
        projection = self.evaluate()
        self.assertEqual(projection["state"], "exited")
        self.assertEqual(projection["exits"][0]["kind"], "suppressed")
        self.assertEqual(goal_exit.STOP_STATES,
                         ("goal_met", "exited", "expired", "blocked"))

    def test_broken_store_reports_unreadable_without_crash(self):
        path = self.data / "outcomes" / "product_events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json\n", encoding="utf-8")
        projection = self.evaluate()
        self.assertEqual(projection["state"], "blocked")
        kinds = [b["kind"] for b in projection["blocked"]["reasons"]]
        self.assertIn("product_events_unreadable", kinds)

        block = goal_exit.run_projection(self.data, RUN)
        self.assertIn(block["state"], ("unreadable", "empty"))

    # -- 运行详情页只读块 ------------------------------------------------

    def test_run_projection_empty_ok_and_stale(self):
        empty = goal_exit.run_projection(self.data, RUN)
        self.assertEqual(empty["state"], "empty")
        self.assertIn("mkt grow goal-exit contract", empty["note"])

        contract = self.contract(
            observation_window={"start": "2026-09-01",
                                "end": "2026-09-30"})
        goal_exit.write_contract(self.data, contract, dry_run=False)
        stale = goal_exit.run_projection(self.data, RUN)
        self.assertEqual(stale["state"], "empty")
        self.assertEqual(len(stale["stale"]), 1)

        self.add_events(_event("e1"))
        projection = self.evaluate(contract=contract)
        goal_exit.write_projection(self.data, projection, dry_run=False)
        goal_exit.write_stop(self.data, projection, dry_run=False)
        block = goal_exit.run_projection(self.data, RUN)
        self.assertEqual(block["state"], "ok")
        self.assertEqual(block["stop_count"], 1)
        self.assertEqual(block["projections"][0]["state"], "expired")
        self.assertEqual(block["stale"], [])

    def test_render_text_plain_and_honest(self):
        self.add_events(_event("e1"))
        projection = self.evaluate()
        text = goal_exit.render_text(projection)
        self.assertIn("只读投影", text)
        self.assertIn("causal_credit=not_claimed", text)
        self.assertIn("下一步：stop_new_actions", text)
        self.assertNotIn("<script", text)

    def test_cli_contract_evaluate_view(self):
        import argparse
        import mkt

        old_data = mkt.DATA
        mkt.DATA = self.data
        self.addCleanup(setattr, mkt, "DATA", old_data)

        def capture(func, args):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                code = func(args)
            return code, out.getvalue(), err.getvalue()

        contract_args = argparse.Namespace(
            goal_exit_action="contract", run=RUN, goal_type="activation",
            target=1, metric="", label="首次激活",
            verify_state="verified",
            window_start="2026-11-01", window_end="2026-11-30",
            attr_days=7, exit=[], by="owner", note="", apply=True, json=True)
        code, out, err = capture(mkt.cmd_grow_goal_exit, contract_args)
        self.assertEqual(0, code, err)
        payload = json.loads(out)
        contract_id = payload["contract"]["contract_id"]
        self.assertEqual("written", payload["write"]["status"])
        self.assertIn("不把先后发生写成因果", err)

        self.add_events(_event("e1"))
        evaluate_args = argparse.Namespace(
            goal_exit_action="evaluate", contract=contract_id,
            subject=TARGET, now=NOW, apply=True, json=True)
        code, out, err = capture(mkt.cmd_grow_goal_exit, evaluate_args)
        self.assertEqual(0, code, err)
        payload = json.loads(out)
        self.assertEqual("goal_met", payload["projection"]["state"])
        self.assertEqual("written", payload["write"]["status"])
        self.assertEqual("written", payload["stop"]["status"])

        view_args = argparse.Namespace(
            goal_exit_action="view", run=RUN, limit=20, json=True)
        code, out, err = capture(mkt.cmd_grow_goal_exit, view_args)
        self.assertEqual(0, code, err)
        block = json.loads(out)
        self.assertEqual(goal_exit.VIEW_SCHEMA, block["schema"])
        self.assertEqual("ok", block["state"])
        self.assertEqual(1, block["stop_count"])

    def test_cli_dry_run_writes_nothing(self):
        import argparse
        import mkt

        old_data = mkt.DATA
        mkt.DATA = self.data
        self.addCleanup(setattr, mkt, "DATA", old_data)
        args = argparse.Namespace(
            goal_exit_action="contract", run=RUN, goal_type="activation",
            target=1, metric="", label="", verify_state="",
            window_start="2026-11-01", window_end="2026-11-30",
            attr_days=7, exit=[], by="", note="", apply=False, json=True)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = mkt.cmd_grow_goal_exit(args)
        self.assertEqual(0, code, err)
        payload = json.loads(out.getvalue())
        self.assertEqual("would_write", payload["write"]["status"])
        self.assertFalse(
            (self.data / "outcomes" / "journey"
             / "goal-exit-contracts.jsonl").exists())

    def test_cli_help_after_registration(self):
        import mkt

        parser = mkt.build_parser()
        with contextlib.redirect_stdout(io.StringIO()):
            args = parser.parse_args(
                ["grow", "goal-exit", "view", "--run", RUN])
        self.assertEqual("goal-exit", args.action)
        self.assertEqual("view", args.goal_exit_action)
        self.assertEqual(RUN, args.run)


if __name__ == "__main__":
    unittest.main()
