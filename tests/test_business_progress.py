import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import mkt
from core import business_progress


class BusinessProgressTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def event(self, **changes):
        row = {
            "event_type": "scope_check", "title": "配额核准",
            "fact": "W08 有 28 条素材，本波只允许 27 条。",
            "impact": "多出的 1 条不能发送，也不计入合格动作。",
            "next_action": "移除超配素材后重新审批。",
            "wave_id": "w08", "job_id": "ovo-300-w08",
            "counts": {"planned": 27, "surplus": 1},
            "technical": {"reason": "supply_scope_invalid"},
        }
        row.update(changes)
        return row

    def test_record_is_persistent_filtered_and_consecutively_deduplicated(self):
        first = business_progress.record(self.tmp.name, self.event(), now="2026-11-27T08:00:00+08:00")
        second = business_progress.record(self.tmp.name, self.event(), now="2026-11-27T08:01:00+08:00")
        self.assertTrue(first["recorded"])
        self.assertFalse(second["recorded"])
        rows = business_progress.events(self.tmp.name, wave_id="w08", event_type="scope_check")
        self.assertEqual(1, len(rows))
        self.assertEqual("growovo.business-progress/1", rows[0]["schema"])
        self.assertEqual("supply_scope_invalid", rows[0]["technical"]["reason"])

    def test_human_output_leads_with_business_meaning_and_hides_codes(self):
        text = business_progress.render(self.event())
        self.assertIn("[配额核准]", text)
        self.assertIn("多出的 1 条不能发送", text)
        self.assertIn("下一步：移除超配素材", text)
        self.assertNotIn("supply_scope_invalid", text)
        technical = business_progress.render(self.event(), include_technical=True)
        self.assertIn("supply_scope_invalid", technical)

    def test_preparation_surplus_becomes_scope_check_not_business_success(self):
        event = business_progress.from_preparation({
            "wave_id": "w08", "wave_status": "supply_scope_invalid", "executed": 0,
            "external_writes": 0, "surplus": {"verovo/github/reply": 1},
            "shortfall": {}, "scope_errors": [],
        })
        self.assertEqual("scope_check", event["event_type"])
        self.assertIn("Verovo GitHub 回帖多 1 条", event["fact"])
        self.assertIn("不计入合格动作", event["impact"])
        self.assertIn("尚未外发", event["fact"])
        self.assertEqual("w08", event["job_id"])
        event = business_progress.from_preparation({
            "wave_id": "w08", "job_id": "ovo-300-w08", "wave_status": "preparing"})
        self.assertEqual("ovo-300-w08", event["job_id"])

    def test_committed_receipt_does_not_become_growth_evidence(self):
        event = business_progress.from_job(
            {"id": "w01", "kind": "wave"},
            {"headline": "X 原创：执行 1，发送成功 1。已提交 1 条。",
             "action_summary": "X 原创：执行 1，发送成功 1",
             "execution": {"target": 1, "committed": 1, "failed": 0, "indeterminate": 0, "skipped": 0},
             "business_outcome": "not_measured", "next_action": "继续观察外部首用"},
            {"action": "inspect_results", "reason": "succeeded"},
        )
        self.assertEqual("evidence_confirmation", event["event_type"])
        self.assertIn("不证明首用、Star、粉丝或业务增长", event["impact"])

    def test_recovery_is_explained_without_raw_reason_in_human_output(self):
        event = business_progress.from_job(
            {"id": "w01", "kind": "wave"},
            {"headline": "业务任务未执行", "execution": {"committed": 0},
             "business_outcome": "not_measured", "next_action": "检查构建"},
            {"action": "diagnose_build", "reason": "source_version_changed"},
        )
        self.assertEqual("recovery_decision", event["event_type"])
        self.assertIn("沿用原作业检查构建路径和版本", event["next_action"])
        self.assertNotIn("source_version_changed", business_progress.render(event))

    def test_blockers_and_probe_delays_are_translated_into_business_facts(self):
        card = {
            "headline": "已提交 11 条，仍有 4 条目标缺口。",
            "execution": {"target": 15, "committed": 11, "failed": 0, "indeterminate": 0,
                          "skipped": 1, "deferred": 0},
            "actions": {"github/reply": {"target": 3, "attempted": 0, "gap": 3},
                        "x/message": {"target": 1, "attempted": 0, "gap": 1}},
            "blockers": {"github:页面出现 '403'": 1, "unsupported": 1},
            "business_outcome": "not_included", "next_action": "核对缺口",
        }
        event = business_progress.from_job(
            {"id": "w01", "kind": "wave"}, card, {"action": "inspect_results", "reason": "partial"})
        self.assertIn("GitHub 预检返回 403，3 条回帖未执行", event["fact"])
        self.assertIn("1 条私信因路线不支持未发送", event["fact"])
        card["blockers"] = {"stop_probe_pending": 2, "unsupported": 1}
        card["execution"]["deferred"] = 2
        event = business_progress.from_job(
            {"id": "w02", "kind": "wave"}, card, {"action": "inspect_results", "reason": "partial"})
        self.assertIn("2 条 GitHub 回帖在半开探测后延后", event["fact"])
        self.assertIn("延后者不计成功，也不自动补发", event["next_action"])

    def test_cycle_progress_command_supports_human_and_json_views(self):
        business_progress.record(self.tmp.name, self.event(), now="2026-11-27T08:00:00+08:00")
        args = SimpleNamespace(action="progress", limit=20, job_id="", wave="w08",
                               event_type="", json=False, technical=False)
        output = io.StringIO()
        with mock.patch.object(mkt, "DATA", Path(self.tmp.name)), contextlib.redirect_stdout(output):
            code = mkt.cmd_cycle(args)
        self.assertEqual(0, code)
        self.assertIn("[配额核准]", output.getvalue())
        args.json = True
        output = io.StringIO()
        with mock.patch.object(mkt, "DATA", Path(self.tmp.name)), contextlib.redirect_stdout(output):
            mkt.cmd_cycle(args)
        payload = json.loads(output.getvalue())
        self.assertEqual(1, payload["count"])
        self.assertEqual("w08", payload["events"][0]["wave_id"])


if __name__ == "__main__":
    unittest.main()
