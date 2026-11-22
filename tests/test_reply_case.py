#!/usr/bin/env python3
"""回复案例与 DNC（G-NEXT-1 / GN1-01～14）验收：导入、改判、抑制、只读投影。

全部在临时目录构造 fixture，不碰真实 data/，不发送、不连接邮箱、不自动回复。
"""
import argparse
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

from core import gates, reply_case, run_detail

NOW = "2026-11-19T10:00:00"
TARGET = "reader@example.com"

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

RUN = {"schema": "growovo.grow-run/2", "id": "R1",
       "status": "ready_to_arm"}


def _csv_text(rows):
    headers = list(rows[0].keys())
    lines = [",".join(headers)]
    for row in rows:
        cells = []
        for name in headers:
            value = str(row.get(name, ""))
            if any(ch in value for ch in ',"\n'):
                value = '"%s"' % value.replace('"', '""')
            cells.append(value)
        lines.append(",".join(cells))
    return "\n".join(lines) + "\n"


def _row(**overrides):
    row = {
        "ref": "msg-1",
        "text": "Sounds good, tell me more.",
        "ts": "2026-11-19T09:00:00",
        "from": TARGET,
        "run": "R1",
        "action": "action-1",
    }
    row.update(overrides)
    return row


class ReplyFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        (self.data / "accounts.json").write_text(
            json.dumps(ACCOUNTS, ensure_ascii=False), encoding="utf-8")

    def write_file(self, name, rows):
        path = self.data / name
        path.write_text(_csv_text(rows), encoding="utf-8")
        return path

    def import_rows(self, rows, name="replies.csv", **overrides):
        path = self.write_file(name, rows)
        kwargs = {
            "account_id": "acc-1",
            "channel": "email",
            "imported_by": "owner",
            "authorization_ref": "ticket-42",
            "now": NOW,
        }
        kwargs.update(overrides)
        return reply_case.import_replies(self.data, path, **kwargs)

    def only_case(self, run_id="R1"):
        cases = reply_case.list_cases(self.data, run_id=run_id)
        self.assertEqual(1, len(cases))
        return cases[0]

    def write_run(self, run_id="R1"):
        path = self.data / "grow" / ("%s.json" % run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(RUN)
        payload["id"] = run_id
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def snapshot_replies(self):
        base = self.data / "replies"
        if not base.exists():
            return {}
        return {
            str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(base.rglob("*")) if p.is_file()
        }


class ImportTests(ReplyFixture):
    def test_gn1_01_import_links_run_action_and_queue(self):
        result = self.import_rows([_row()])
        self.assertEqual(1, result["created"])
        self.assertEqual(0, result["conflicts"])
        self.assertEqual([], result["errors"])
        case = self.only_case()
        self.assertEqual("acc-1", case["account_id"])
        self.assertEqual("email", case["channel"])
        self.assertEqual(TARGET, case["target"])
        self.assertEqual("R1", case["run_id"])
        self.assertEqual("action-1", case["action_ref"])
        self.assertEqual("linked", case["link_state"])
        self.assertEqual("interested", case["classification_candidate"])
        self.assertEqual("ticket-42", case["authorization_ref"])
        self.assertEqual("owner", case["imported_by"])
        queue = self.data / "replies" / "reply-review-queue.csv"
        self.assertTrue(queue.is_file())
        rows = [line.split(",") for line in
                queue.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(list(reply_case.QUEUE_HEADERS), rows[0])
        self.assertIn(case["reply_case_id"], queue.read_text(encoding="utf-8"))

    def test_gn1_02_reimport_same_file_is_duplicate(self):
        self.import_rows([_row()])
        again = self.import_rows([_row()], name="replies.csv")
        self.assertEqual(0, again["created"])
        self.assertEqual(1, again["skipped_duplicate"])
        self.assertEqual(0, again["conflicts"])
        cases = reply_case.list_cases(self.data)
        self.assertEqual(1, len(cases))
        history = reply_case.case_history(self.data, cases[0]["reply_case_id"])
        self.assertEqual(1, len(history))

    def test_gn1_03_same_source_ref_changed_content_conflicts(self):
        first = self.import_rows([_row(text="Sounds good, tell me more.")])
        second = self.import_rows(
            [_row(text="Totally different message.")], name="replies-2.csv")
        self.assertEqual(1, first["created"])
        self.assertEqual(0, second["created"])
        self.assertEqual(1, second["conflicts"])
        case = self.only_case()
        self.assertIn("Sounds good", case["raw_text"])
        conflicts = (self.data / "replies" /
                     "reply-import-conflicts.jsonl")
        self.assertTrue(conflicts.is_file())
        record = json.loads(conflicts.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(case["reply_case_id"], record["reply_case_id"])
        self.assertNotEqual(record["existing_source_hash"],
                            record["incoming_source_hash"])
        block = reply_case.run_projection(self.data, "R1")
        self.assertEqual(1, block["conflict_count"])

    def test_gn1_04_reply_without_run_stays_unresolved(self):
        rows = [{k: v for k, v in _row().items() if k not in ("run", "action")}]
        result = self.import_rows(rows)
        self.assertEqual(1, result["created"])
        case = reply_case.list_cases(self.data)[0]
        self.assertEqual("unresolved", case["link_state"])
        self.assertIsNone(case["run_id"])
        self.assertIn("未关联运行", case["next_required_action"])
        block = reply_case.run_projection(self.data, "R1")
        self.assertEqual("empty", block["state"])
        self.assertEqual(0, block["count"])
        self.assertEqual(1, block["unresolved_count"])
        self.assertIn("未关联回复", block["note"])

    def test_reimport_with_run_relinks_unresolved_case(self):
        rows = [{k: v for k, v in _row().items() if k not in ("run", "action")}]
        first = self.import_rows(rows)
        cid = first["case_ids"][0]
        before = reply_case.get_case(self.data, cid)
        again = self.import_rows(rows, name="replies-2.csv", run_id="R1")
        self.assertEqual(0, again["created"])
        self.assertEqual(1, again["relinked"])
        self.assertEqual(1, again["would_relink"])
        self.assertEqual(0, again["skipped_duplicate"])
        self.assertEqual(0, again["conflicts"])
        self.assertEqual([], again["errors"])
        current = reply_case.get_case(self.data, cid)
        self.assertEqual(2, current["version"])
        self.assertEqual("R1", current["run_id"])
        self.assertEqual("linked", current["link_state"])
        self.assertEqual(before["source_hash"], current["source_hash"])
        self.assertEqual(before["raw_text_hash"], current["raw_text_hash"])
        self.assertNotIn("未关联运行", current["next_required_action"])
        block = reply_case.run_projection(self.data, "R1")
        self.assertEqual("ok", block["state"])
        self.assertEqual(1, block["linked_count"])
        events = (self.data / "replies" / "reply-events.jsonl").read_text(
            encoding="utf-8")
        self.assertIn('"event": "link"', events)

    def test_reimport_without_run_keeps_case_duplicate(self):
        rows = [{k: v for k, v in _row().items() if k not in ("run", "action")}]
        first = self.import_rows(rows)
        again = self.import_rows(rows, name="replies-2.csv")
        self.assertEqual(0, again["created"])
        self.assertEqual(0, again["relinked"])
        self.assertEqual(1, again["skipped_duplicate"])
        case = reply_case.get_case(self.data, first["case_ids"][0])
        self.assertEqual(1, case["version"])
        self.assertEqual("unresolved", case["link_state"])

    def test_reimport_relink_respects_dry_run(self):
        rows = [{k: v for k, v in _row().items() if k not in ("run", "action")}]
        first = self.import_rows(rows)
        again = self.import_rows(rows, name="replies-2.csv", run_id="R1",
                                 dry_run=True)
        self.assertEqual(1, again["would_relink"])
        self.assertEqual(0, again["relinked"])
        case = reply_case.get_case(self.data, first["case_ids"][0])
        self.assertEqual(1, case["version"])
        self.assertEqual("unresolved", case["link_state"])

    def test_link_command_links_unresolved_case(self):
        rows = [{k: v for k, v in _row().items() if k not in ("run", "action")}]
        first = self.import_rows(rows)
        cid = first["case_ids"][0]
        case = reply_case.link_reply(
            self.data, cid, run_id="R1", action_ref="action-9",
            operator="owner", authorization_ref="ticket-42", now=NOW)
        self.assertEqual(2, case["version"])
        self.assertEqual("R1", case["run_id"])
        self.assertEqual("action-9", case["action_ref"])
        self.assertEqual("linked", case["link_state"])
        self.assertNotIn("未关联运行", case["next_required_action"])
        history = reply_case.case_history(self.data, cid)
        self.assertEqual(2, len(history))
        self.assertEqual(history[0]["raw_text"], history[1]["raw_text"])
        queue = (self.data / "replies" / "reply-review-queue.csv").read_text(
            encoding="utf-8")
        self.assertIn("linked", queue)

    def test_link_requires_run_signature_and_authorization(self):
        rows = [{k: v for k, v in _row().items() if k not in ("run", "action")}]
        cid = self.import_rows(rows)["case_ids"][0]
        base = {"run_id": "R1", "operator": "owner",
                "authorization_ref": "ticket-42"}
        for missing in ("run_id", "operator", "authorization_ref"):
            kwargs = dict(base)
            kwargs[missing] = ""
            with self.assertRaises(reply_case.ReplyError):
                reply_case.link_reply(self.data, cid, **kwargs)
        with self.assertRaises(reply_case.ReplyError):
            reply_case.link_reply(self.data, "rc-missing", **base)

    def test_link_does_not_silently_rebind_linked_case(self):
        cid = self.import_rows([_row()])["case_ids"][0]
        same = reply_case.link_reply(
            self.data, cid, run_id="R1", operator="owner",
            authorization_ref="ticket-42", now=NOW)
        self.assertTrue(same.pop("already_linked"))
        self.assertEqual(1, same["version"])
        with self.assertRaises(reply_case.ReplyError):
            reply_case.link_reply(
                self.data, cid, run_id="R2", operator="owner",
                authorization_ref="ticket-42", now=NOW)

    def test_import_requires_signature_and_authorization(self):
        path = self.write_file("replies.csv", [_row()])
        with self.assertRaises(reply_case.ReplyError):
            reply_case.import_replies(
                self.data, path, account_id="acc-1", imported_by="",
                authorization_ref="ticket-42", now=NOW)
        with self.assertRaises(reply_case.ReplyError):
            reply_case.import_replies(
                self.data, path, account_id="acc-1", imported_by="owner",
                authorization_ref="", now=NOW)

    def test_import_rejects_unknown_account_and_channel_mismatch(self):
        path = self.write_file("replies.csv", [_row()])
        with self.assertRaises(reply_case.ReplyError):
            reply_case.import_replies(
                self.data, path, account_id="acc-missing", imported_by="owner",
                authorization_ref="ticket-42", now=NOW)
        with self.assertRaises(reply_case.ReplyError):
            reply_case.import_replies(
                self.data, path, account_id="acc-1", channel="x",
                imported_by="owner", authorization_ref="ticket-42", now=NOW)

    def test_discord_and_hn_replies_import_with_channel_alias(self):
        accounts = json.loads(json.dumps(ACCOUNTS))
        accounts["accounts"].extend([
            {"id": "acc-dc", "platform": "discord", "handle": "owner#0001",
             "identity_type": "pseudonymous", "status": "eligible"},
            {"id": "acc-hn", "platform": "hn", "handle": "owner",
             "identity_type": "pseudonymous", "status": "eligible"},
        ])
        (self.data / "accounts.json").write_text(
            json.dumps(accounts, ensure_ascii=False), encoding="utf-8")
        for account_id, channel, ref, name, expected in (
                ("acc-dc", "discord", "msg-dc", "dc.csv", "discord"),
                ("acc-hn", "hackernews", "msg-hn", "hn.csv", "hn")):
            path = self.write_file(name, [_row(ref=ref)])
            result = reply_case.import_replies(
                self.data, path, account_id=account_id, channel=channel,
                imported_by="owner", authorization_ref="ticket-42", now=NOW)
            self.assertEqual(1, result["created"])
            self.assertEqual(expected, result["channel"])
            case = reply_case.get_case(self.data, result["case_ids"][0])
            self.assertEqual(expected, case["channel"])

    def test_unknown_columns_rejected_unless_explicitly_ignored(self):
        rows = [_row(weird_column="value")]
        path = self.write_file("replies.csv", rows)
        with self.assertRaises(reply_case.ReplyError):
            reply_case.import_replies(
                self.data, path, account_id="acc-1", imported_by="owner",
                authorization_ref="ticket-42", now=NOW)
        result = reply_case.import_replies(
            self.data, path, account_id="acc-1", imported_by="owner",
            authorization_ref="ticket-42", ignore_unmapped=True, now=NOW)
        self.assertEqual(1, result["created"])
        self.assertEqual(["weird_column"], result["unknown_columns"])
        self.assertTrue(any("未映射" in w for w in result["warnings"]))

    def test_invalid_rows_are_errors_not_silent_drops(self):
        rows = [_row(ref="ok-1"),
                _row(ref="no-time", ts="not-a-time"),
                _row(ref="no-text", text="")]
        result = self.import_rows(rows)
        self.assertEqual(1, result["created"])
        self.assertEqual(2, len(result["errors"]))
        self.assertTrue(result["partial"])
        self.assertEqual(1, len(reply_case.list_cases(self.data)))

    def test_dry_run_writes_nothing(self):
        result = self.import_rows([_row()], dry_run=True)
        self.assertEqual(0, result["created"])
        self.assertEqual(1, result["would_create"])
        self.assertTrue(result["dry_run"])
        self.assertFalse((self.data / "replies").exists())


class ReviewTests(ReplyFixture):
    def test_gn1_09_interested_stays_candidate_without_business_event(self):
        self.import_rows([_row(text="Yes, I am interested. Tell me more.")])
        case = self.only_case()
        self.assertEqual("interested", case["classification_candidate"])
        self.assertEqual("proposed", case["review_state"])
        self.assertEqual("", case["human_label"])
        confirmed = reply_case.review_reply(
            self.data, case["reply_case_id"], reviewer="owner",
            label="interested", note="先人工回复", now=NOW)
        self.assertEqual("confirmed", confirmed["review_state"])
        self.assertEqual("interested", confirmed["human_label"])
        self.assertFalse((self.data / "ledger.jsonl").exists())
        self.assertFalse((self.data / "state").exists())
        self.assertIn("不自动升级", confirmed["next_required_action"])

    def test_gn1_10_overrule_keeps_candidate_and_history(self):
        self.import_rows([_row(text="Sounds good, tell me more.")])
        case = self.only_case()
        updated = reply_case.review_reply(
            self.data, case["reply_case_id"], reviewer="owner",
            label="not_interested", note="预算不足", now=NOW)
        self.assertEqual("overruled", updated["review_state"])
        self.assertEqual("not_interested", updated["human_label"])
        self.assertEqual("interested", updated["classification_candidate"])
        self.assertEqual(2, updated["version"])
        history = reply_case.case_history(self.data, case["reply_case_id"])
        self.assertEqual([1, 2], [row["version"] for row in history])
        self.assertEqual("interested", history[0]["classification_candidate"])
        events = (self.data / "replies" /
                  "reply-events.jsonl").read_text(encoding="utf-8")
        self.assertIn('"event": "review"', events)
        self.assertIn('"to_label": "not_interested"', events)

    def test_review_requires_signature_and_known_label(self):
        self.import_rows([_row()])
        case = self.only_case()
        with self.assertRaises(reply_case.ReplyError):
            reply_case.review_reply(
                self.data, case["reply_case_id"], reviewer="", label="OOO",
                now=NOW)
        with self.assertRaises(reply_case.ReplyError):
            reply_case.review_reply(
                self.data, case["reply_case_id"], reviewer="owner",
                label="maybe", now=NOW)


class DncTests(ReplyFixture):
    def test_gn1_05_free_text_unsubscribe_needs_confirmation(self):
        self.import_rows([_row(text="Please unsubscribe me from this list.")])
        case = self.only_case()
        self.assertEqual("unsubscribe", case["classification_candidate"])
        self.assertEqual("pending_review", case["review_state"])
        self.assertEqual("pending", case["dnc_state"])
        self.assertEqual(set(), gates.load_suppressions(self.data))
        self.assertFalse((self.data / "contacts.jsonl").exists())
        block = reply_case.run_projection(self.data, "R1")
        self.assertEqual(1, block["dnc"]["pending"])
        self.assertEqual(0, block["dnc"]["active"])

    def test_gn1_05_structured_unsubscribe_can_confirm(self):
        rows = [_row(text="remove me", unsubscribe="yes")]
        self.import_rows(rows)
        case = self.only_case()
        self.assertTrue(case["structured_unsubscribe"])
        self.assertEqual("unsubscribe", case["classification_candidate"])
        self.assertEqual("proposed", case["review_state"])
        self.assertEqual("pending", case["dnc_state"])

    def test_gn1_06_confirmed_dnc_suppresses_next_queue(self):
        rows = [_row(text="Please unsubscribe me.")]
        self.import_rows(rows)
        case = reply_case.review_reply(
            self.data, self.only_case()["reply_case_id"], reviewer="owner",
            label="unsubscribe", now=NOW)
        self.assertEqual("confirmed", case["review_state"])
        result = reply_case.confirm_dnc(
            self.data, case["reply_case_id"], operator="owner",
            reason="用户明确退订", now=NOW)
        self.assertTrue(result["ok"], result)
        self.assertEqual("active", result["dnc_state"])
        self.assertTrue(result["dnc_ref"].startswith("contacts.jsonl#"))
        contacts = (self.data / "contacts.jsonl").read_text(encoding="utf-8")
        record = json.loads(contacts.splitlines()[0])
        self.assertTrue(record["state"]["dnc"])
        self.assertEqual(case["reply_case_id"], record["state"]["dnc_case"])
        self.assertIn(TARGET, gates.load_suppressions(self.data))
        with self.assertRaises(gates.GateError) as caught:
            gates.Gate(suppress=gates.load_suppressions(self.data)).check(
                channel="email", action="message", target=TARGET)
        self.assertIn("抑制名单", str(caught.exception))
        block = reply_case.run_projection(self.data, "R1")
        self.assertEqual(1, block["dnc"]["active"])
        self.assertEqual([], block["dnc"]["unverified"])

    def test_gn1_07_dnc_double_failure_pauses_and_reports(self):
        self.import_rows([_row(text="Please unsubscribe me.")])
        case = self.only_case()
        (self.data / "contacts.jsonl").mkdir()
        (self.data / "suppress.txt").mkdir()
        result = reply_case.confirm_dnc(
            self.data, case["reply_case_id"], operator="owner", now=NOW)
        self.assertFalse(result["ok"])
        self.assertEqual("failed", result["dnc_state"])
        self.assertIn("双通道写入失败", result["error"])
        self.assertTrue(Path(result["paused_path"]).is_file())
        self.assertTrue((self.data / "state" / "PAUSED").is_file())
        latest = reply_case.get_case(self.data, case["reply_case_id"])
        self.assertEqual("failed", latest["dnc_state"])
        self.assertEqual("operator", latest["next_actor"])
        self.assertIn("保持暂停", latest["next_required_action"])
        block = reply_case.run_projection(self.data, "R1")
        self.assertEqual(1, block["dnc"]["failed"])
        detail = run_detail.build_run_detail(
            self.data, "R1", on="2026-11-19", now=NOW)
        codes = {state["code"] for state in detail["states"]}
        self.assertIn("PARTIAL", codes)

    def test_gn1_12_auto_dnc_failure_never_retries(self):
        rows = [_row(text="remove me", unsubscribe="yes")]
        path = self.write_file("replies.csv", rows)
        # 两个抑制通道都不可写，强制自动 DNC 失败；失败后不得因重导入再试。
        (self.data / "contacts.jsonl").mkdir()
        (self.data / "suppress.txt").mkdir()
        first = reply_case.import_replies(
            self.data, path, account_id="acc-1", imported_by="owner",
            authorization_ref="ticket-42", auto_dnc_structured=True, now=NOW)
        self.assertEqual(1, first["created"])
        self.assertEqual(0, first["auto_dnc"])
        self.assertEqual(1, first["auto_dnc_failed"])
        self.assertTrue((self.data / "state" / "PAUSED").is_file())
        again = reply_case.import_replies(
            self.data, path, account_id="acc-1", imported_by="owner",
            authorization_ref="ticket-42", auto_dnc_structured=True, now=NOW)
        self.assertEqual(0, again["created"])
        self.assertEqual(1, again["skipped_duplicate"])
        self.assertEqual(0, again["auto_dnc_failed"])
        events = (self.data / "replies" /
                  "reply-events.jsonl").read_text(encoding="utf-8")
        self.assertEqual(1, events.count('"event": "dnc_failed"'))
        self.assertNotIn('"event": "dnc_active"', events)
        case = reply_case.list_cases(self.data)[0]
        self.assertEqual("failed", case["dnc_state"])

    def test_gn1_08_ooo_only_records_return_candidate(self):
        self.import_rows([_row(text="I am out of office until 2026-11-25.")])
        case = self.only_case()
        self.assertEqual("OOO", case["classification_candidate"])
        self.assertTrue(case["ooo_return_candidate"])
        self.assertEqual("not_requested", case["dnc_state"])
        self.assertIn("人工排期", case["next_required_action"])
        self.assertFalse((self.data / "state").exists())
        self.assertEqual(set(), gates.load_suppressions(self.data))


class ProjectionTests(ReplyFixture):
    def test_gn1_11_injection_text_is_escaped_in_page(self):
        payload = '<script>alert(1)</script> please unsubscribe me'
        self.import_rows([_row(text=payload)])
        self.write_run()
        case = self.only_case()
        self.assertEqual("unsubscribe", case["classification_candidate"])
        block = reply_case.run_projection(self.data, "R1")
        self.assertIn("<script>alert(1)</script>",
                      block["cases"][0]["raw_excerpt"])
        detail = run_detail.build_run_detail(
            self.data, "R1", on="2026-11-19", now=NOW)
        html = run_detail.render_html(detail)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("回复与 DNC", html)
        self.assertIn(case["reply_case_id"], html)

    def test_gn1_13_projection_is_read_only_and_stable(self):
        self.import_rows([_row()])
        case = self.only_case()
        reply_case.review_reply(
            self.data, case["reply_case_id"], reviewer="owner",
            label="interested", now=NOW)
        reply_case.confirm_dnc(
            self.data, case["reply_case_id"], operator="owner", now=NOW)
        before = self.snapshot_replies()
        first = reply_case.run_projection(self.data, "R1")
        second = reply_case.run_projection(self.data, "R1")
        self.assertEqual(before, self.snapshot_replies())
        self.assertEqual(first, second)
        self.assertEqual("ok", first["state"])
        self.assertEqual(1, first["count"])
        self.assertEqual(1, first["linked_count"])
        self.assertEqual(1, len(first["batches"]))
        self.assertEqual(1, first["dnc"]["active"])
        self.assertTrue(first["queue_path"].endswith(
            "replies/reply-review-queue.csv"))

    def test_unreadable_store_marks_projection_unreadable(self):
        path = self.data / "replies" / "reply-cases.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("{not json}\n", encoding="utf-8")
        block = reply_case.run_projection(self.data, "R1")
        self.assertEqual("unreadable", block["state"])
        self.assertIn("ReplyStoreError", block["error"])
        self.assertEqual([], block["cases"])

    def test_missing_replies_dir_is_empty_not_error(self):
        block = reply_case.run_projection(self.data, "R1")
        self.assertEqual("empty", block["state"])
        self.assertEqual(0, block["count"])
        self.assertIn("mkt grow reply import", block["note"])
        self.write_run()
        detail = run_detail.build_run_detail(
            self.data, "R1", on="2026-11-19", now=NOW)
        html = run_detail.render_html(detail)
        self.assertIn("回复与 DNC", html)
        self.assertIn("还没有导入回复", html)


class SafetyTests(ReplyFixture):
    def test_gn1_14_no_send_no_account_write(self):
        accounts_before = (self.data / "accounts.json").read_bytes()
        self.import_rows([_row(text="Please unsubscribe me.")])
        case = self.only_case()
        reply_case.review_reply(
            self.data, case["reply_case_id"], reviewer="owner",
            label="unsubscribe", now=NOW)
        reply_case.confirm_dnc(
            self.data, case["reply_case_id"], operator="owner", now=NOW)
        reply_case.run_projection(self.data, "R1")
        self.assertFalse((self.data / "ledger.jsonl").exists())
        self.assertFalse((self.data / "outbox").exists())
        self.assertEqual(accounts_before,
                         (self.data / "accounts.json").read_bytes())
        events = (self.data / "replies" /
                  "reply-events.jsonl").read_text(encoding="utf-8")
        self.assertIn('"actor": "owner"', events)
        self.assertIn('"event": "dnc_active"', events)

    def test_flow_never_touches_network_stacks(self):
        with mock.patch.object(
                smtplib, "SMTP",
                side_effect=AssertionError("测试中不允许真实 SMTP")), \
             mock.patch.object(
                urllib.request, "urlopen",
                side_effect=AssertionError("测试中不允许真实网络")):
            self.import_rows([_row(text="Please unsubscribe me.")])
            case = self.only_case()
            reply_case.confirm_dnc(
                self.data, case["reply_case_id"], operator="owner", now=NOW)
            reply_case.run_projection(self.data, "R1")


class CliTests(ReplyFixture):
    def _capture(self, func, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = func(args)
        return code, out.getvalue(), err.getvalue()

    def test_cli_import_list_and_boundary(self):
        import mkt

        path = self.write_file("replies.csv", [_row()])
        old_data = mkt.DATA
        mkt.DATA = self.data
        self.addCleanup(setattr, mkt, "DATA", old_data)
        import_args = argparse.Namespace(
            reply_action="import", file=str(path), account="acc-1",
            channel="email", by="owner", authorization="ticket-42",
            run="R1", ignore_unmapped=False, auto_dnc_structured=False,
            dry_run=False, json=True)
        code, out, err = self._capture(mkt.cmd_grow_reply, import_args)
        self.assertEqual(0, code, err)
        payload = json.loads(out)
        self.assertEqual(1, payload["created"])
        self.assertIn("边界：只登记本地回复与抑制", err)
        list_args = argparse.Namespace(
            reply_action="list", run="R1", state="", limit=0, json=True)
        code, out, err = self._capture(mkt.cmd_grow_reply, list_args)
        self.assertEqual(0, code, err)
        payload = json.loads(out)
        self.assertEqual(reply_case.QUEUE_SCHEMA, payload["schema"])
        self.assertEqual(1, payload["count"])

    def test_cli_dnc_failure_returns_one(self):
        import mkt

        self.import_rows([_row(text="Please unsubscribe me.")])
        case = self.only_case()
        (self.data / "contacts.jsonl").mkdir()
        (self.data / "suppress.txt").mkdir()
        old_data = mkt.DATA
        mkt.DATA = self.data
        self.addCleanup(setattr, mkt, "DATA", old_data)
        args = argparse.Namespace(
            reply_action="dnc", reply_case_id=case["reply_case_id"],
            by="owner", reason="", json=True)
        code, out, err = self._capture(mkt.cmd_grow_reply, args)
        self.assertEqual(1, code)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertIn("发送已暂停", err)

    def test_cli_unknown_case_returns_two(self):
        import mkt

        old_data = mkt.DATA
        mkt.DATA = self.data
        self.addCleanup(setattr, mkt, "DATA", old_data)
        args = argparse.Namespace(
            reply_action="show", reply_case_id="rc-missing", json=True)
        code, out, err = self._capture(mkt.cmd_grow_reply, args)
        self.assertEqual(2, code)
        self.assertIn("没有这个回复案例", err)


if __name__ == "__main__":
    unittest.main()
