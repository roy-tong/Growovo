#!/usr/bin/env python3
"""Eligible supply, KOC qualification, content gates and independent safety caps."""
import copy
import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import engagement_cycle as cycle
from core import koc_qualification
from core import supply_pool
from core import supply_readiness
from core.ledger import Ledger
import mkt

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 11, 23, 22, 5, tzinfo=TZ)


def policy():
    got = copy.deepcopy(cycle.DEFAULT_POLICY)
    got["window"] = {"start_hour": 22, "end_hour": 7, "waves_per_night": 3}
    got["platforms"] = {
        "x": {"reply": {"min_gap_sec": 0, "per_hour": 20, "per_night": 100},
              "publish": {"min_gap_sec": 0, "per_hour": 20, "per_night": 100}},
    }
    got["interleave"] = {"priority": ["x"], "min_gap_sec": 0, "max_actions_per_wave": 20}
    got["supply"]["wave_targets"] = {"x/reply": 2, "x/publish": 2}
    got["content_rules"] = {
        "reply": {"artifact_min_per_wave": 0, "artifact_min_ratio": 0},
        "x_publish": {"artifact_min_per_wave": 0, "links_in_body": False},
        "focus": {"min_share": 0, "targets_file": ""},
    }
    return got


def reply(target, text="answer", actor="a"):
    return {"channel": "x", "action": "reply", "target": target,
            "text": text, "actor": actor, "source": "queue", "context": "peer context", "queued_at": NOW.isoformat()}


class SupplyReadinessTests(unittest.TestCase):
    def test_candidate_draft_and_eligible_depth_are_different(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            queue = [reply("https://x.com/a/status/1"),
                     reply("https://x.com/b/status/2", actor="b"),
                     reply("https://x.com/c/status/3", text="", actor="c")]
            cycle.save_queue(data / "state" / "engagement-queue.json", queue)
            pool = {"version": 1, "built_at": "", "last_build": [], "items": {
                "candidate-%d" % i: {"key": "candidate-%d" % i, "channel": "x", "action": "reply",
                                     "target": "https://x.com/p/status/%d" % i,
                                     "status": "new", "score": 1}
                for i in range(10)
            }}
            supply_pool.save(data, pool)
            got = supply_readiness.assess(data, policy(), now=NOW, waves=3)
            row = got["by_key"]["x/reply"]
            self.assertEqual(10, row["candidate_available"])
            self.assertEqual(2, row["drafted"])
            self.assertEqual(1, row["blank"])
            self.assertEqual(2, row["eligible_by_wave"][0])
            self.assertEqual(1, row["ready_depth_waves"])
            self.assertEqual(4, row["shortfall"])
            self.assertEqual(1, row["blockers"].get("text_empty"))
            self.assertTrue(got["projection_only"])

    def test_cli_readiness_and_koc_audit_use_product_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            pol = policy()
            path = data / "policies" / "engagement-cycle.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(pol), encoding="utf-8")
            cycle.save_queue(data / "state" / "engagement-queue.json",
                             [reply("https://x.com/a/status/1")])
            output = io.StringIO()
            with mock.patch.object(mkt, "DATA", data), contextlib.redirect_stdout(output):
                code = mkt.cmd_supply(SimpleNamespace(action="readiness", waves=1, now=NOW.isoformat()))
            self.assertEqual(0, code)
            self.assertIn("x/reply", json.loads(output.getvalue())["by_key"])

            supply_pool.koc_save(data, {"version": 1, "updated_at": "", "accounts": {
                "empty": {"handle": "empty", "koc_tier": "unknown"},
            }})
            output = io.StringIO()
            with mock.patch.object(mkt, "DATA", data), contextlib.redirect_stdout(output):
                code = mkt.cmd_supply(SimpleNamespace(action="koc-audit"))
            self.assertEqual(0, code)
            self.assertEqual(1, json.loads(output.getvalue())["counts"]["registered"])

    def test_blockers_do_not_leak_across_action_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            stale = {"channel": "x", "action": "publish", "target": "", "text": "old",
                     "source": "queue", "queued_at": "2026-11-20T01:00:00+08:00"}
            cycle.save_queue(data / "state" / "engagement-queue.json",
                             [reply("https://x.com/a/status/1"), stale])
            got = supply_readiness.assess(data, policy(), now=NOW, waves=1)
            self.assertNotIn("stale_row", got["by_key"]["x/reply"]["blockers"])
            self.assertEqual(1, got["by_key"]["x/publish"]["blockers"]["stale_row"])

    def test_outside_window_uses_next_window_without_changing_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            path = data / "state" / "engagement-queue.json"
            cycle.save_queue(path, [reply("https://x.com/a/status/1")])
            before = path.read_bytes()
            daytime = NOW.replace(hour=12)
            got = supply_readiness.assess(data, policy(), now=daytime, waves=1)
            self.assertTrue(got["window_assumed"])
            self.assertEqual(22, dt.datetime.fromisoformat(got["starts_at"]).hour)
            self.assertEqual(before, path.read_bytes())


class KOCQualificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def seed(self):
        supply_pool.koc_save(self.data, {"version": 1, "updated_at": "", "accounts": {
            "person": {"handle": "person", "followers": 1200, "bio": "AI evaluator",
                       "koc_tier": "unknown", "touches": 1, "reply_receipts": [{"id": "r1"}]},
            "stale": {"handle": "stale", "followers": 50, "bio": "builder", "koc_tier": "koc"},
            "empty": {"handle": "empty", "koc_tier": "unknown"},
        }})

    def test_report_separates_registered_enriched_qualified_engaged_and_replied(self):
        self.seed()
        registry = supply_pool.koc_load(self.data)
        registry["accounts"]["person"]["qualification"] = {
            "status": "qualified", "by": "roy", "at": NOW.isoformat(),
            "evidence": ["recent post"], "account_type": "person",
        }
        supply_pool.koc_save(self.data, registry)
        got = koc_qualification.report(self.data)
        self.assertEqual(3, got["counts"]["registered"])
        self.assertEqual(2, got["counts"]["enriched"])
        self.assertEqual(1, got["counts"]["qualified"])
        self.assertEqual(1, got["counts"]["engaged"])
        self.assertEqual(1, got["counts"]["replied"])
        self.assertEqual(2, got["counts"]["tier_stale"])

    def test_qualification_requires_evidence_and_company_contact(self):
        self.seed()
        with self.assertRaisesRegex(ValueError, "证据"):
            koc_qualification.qualify(self.data, "person", status="qualified", by="roy",
                                      evidence=[], account_type="person")
        with self.assertRaisesRegex(ValueError, "联系人"):
            koc_qualification.qualify(self.data, "person", status="qualified", by="roy",
                                      evidence=["post"], account_type="company")
        got = koc_qualification.qualify(
            self.data, "person", status="qualified", by="roy", evidence=["recent post"],
            account_type="person", now=NOW)
        self.assertEqual("qualified", got["qualification"]["status"])
        stored = supply_pool.koc_load(self.data)["accounts"]["person"]
        self.assertEqual("roy", stored["qualification"]["by"])
        self.assertEqual(1, len(stored["qualification_history"]))

    def test_recompute_is_dry_run_by_default_and_audited_on_apply(self):
        self.seed()
        before = supply_pool.koc_path(self.data).read_bytes()
        preview = koc_qualification.recompute(self.data)
        self.assertEqual(2, preview["changed"])
        self.assertEqual(before, supply_pool.koc_path(self.data).read_bytes())
        applied = koc_qualification.recompute(self.data, apply=True, by="roy", now=NOW)
        self.assertEqual(2, applied["changed"])
        stored = supply_pool.koc_load(self.data)["accounts"]["stale"]
        self.assertNotEqual("koc", stored["koc_tier"])
        self.assertEqual("roy", stored["tier_updated_by"])


class ContentAndSafetyGateTests(unittest.TestCase):
    def test_content_hard_gate_blocks_send_and_preserves_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            pol = policy()
            pol["supply"]["wave_targets"] = {"x/publish": 1}
            pol["content_rules"]["enforce"] = True
            pol["content_rules"]["x_publish"]["artifact_min_per_wave"] = 1
            path = data / "policies" / "engagement-cycle.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(pol), encoding="utf-8")
            queue = data / "state" / "engagement-queue.json"
            cycle.save_queue(queue, [{"channel": "x", "action": "publish", "target": "",
                                      "text": "plain thought", "source": "queue"}])
            before = queue.read_bytes()
            with mock.patch.object(cycle, "_now", return_value=NOW), \
                    mock.patch("core.action_runner.run_action") as dispatch:
                got = cycle.run(data, go=True, queue_path=queue, wave_id="01-2205",
                                log=lambda *_: None)
            self.assertFalse(got["ok"])
            self.assertEqual("content_gate_blocked", got["reason"])
            self.assertIn("x_publish_artifact", got["content_gate"]["failures"])
            dispatch.assert_not_called()
            self.assertEqual(before, queue.read_bytes())

    def test_content_soft_mode_keeps_wave_and_reports_miss(self):
        pol = policy()
        pol["supply"]["wave_targets"] = {"x/publish": 1}
        pol["content_rules"]["x_publish"]["artifact_min_per_wave"] = 1
        got = cycle.plan([{"channel": "x", "action": "publish", "target": "",
                           "text": "plain thought", "source": "queue"}],
                         pol, ledger=Ledger(Path(tempfile.mkdtemp()) / "ledger.jsonl"), now=NOW)
        self.assertEqual(1, len(got["scheduled"]))
        self.assertFalse(got["content"]["x_publish_artifact_floor_met"])
        self.assertFalse(got["content_gate"]["blocked"])

    def test_safety_cap_applies_even_when_business_quota_is_disabled(self):
        pol = policy()
        pol["quota"] = {"enforce": False,
                        "safety": {"enforced": True, "per_night": {"x/publish": 1}}}
        pol["supply"]["wave_targets"] = {"x/publish": 2}
        rows = [{"channel": "x", "action": "publish", "target": "", "text": str(i)}
                for i in range(2)]
        got = cycle.plan(rows, pol, ledger=Ledger(Path(tempfile.mkdtemp()) / "ledger.jsonl"), now=NOW)
        self.assertEqual(1, len(got["scheduled"]))
        self.assertIn("safety_cap_exhausted", [r["reason"] for r in got["skipped"]])
        self.assertEqual(0, got["safety"]["x/publish"]["left"])

    def test_enabled_safety_cap_missing_fails_closed(self):
        pol = policy()
        pol["quota"] = {"enforce": False, "safety": {"enforced": True, "per_night": {}}}
        got = cycle.plan([{"channel": "x", "action": "reply", **reply("https://x.com/a/status/1")}],
                         pol, ledger=Ledger(Path(tempfile.mkdtemp()) / "ledger.jsonl"), now=NOW)
        self.assertEqual([], got["scheduled"])
        self.assertEqual("safety_cap_missing", got["skipped"][0]["reason"])

    def test_disabled_safety_layer_is_explicitly_visible(self):
        pol = policy()
        pol["quota"] = {"enforce": False, "safety": {"enforced": False, "per_night": {}}}
        got = cycle.plan([reply("https://x.com/a/status/1")], pol,
                         ledger=Ledger(Path(tempfile.mkdtemp()) / "ledger.jsonl"), now=NOW)
        self.assertEqual(1, len(got["scheduled"]))
        self.assertEqual("disabled", got["safety"]["x/reply"]["status"])


if __name__ == "__main__":
    unittest.main()
