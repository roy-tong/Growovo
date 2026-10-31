#!/usr/bin/env python3
"""闭环改造的离线单测：审批/队列/链接/导入/裁决/Campaign 视图/自动观察。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import approval, links, outcome_io, queue, workspace
from core import campaign as camp
from core.attribution import OutcomeLedger
from core.experiments import new_experiment, observe_from_outcomes, start_experiment
from core.ledger import Ledger
from core.planlint import PlanNotApproved, enforce_approval


def make_plan(plan_id="p1", **over):
    plan = {
        "plan_id": plan_id,
        "goal": "拿到 10 个激活",
        "created": "2026-10-31",
        "hypothesis": "研究者需要证据分级",
        "icp": {"org": "一人公司", "persona": "研究员", "beachhead": "UX 研究者"},
        "audience": {"spec": "r/UXResearch", "target_count": 10, "evidence_grade": "B"},
        "waves": [{"wave": 1, "channel": "email", "action": "message", "size": 3,
                   "content_policy": "千人千面", "queue": f"data/queues/{plan_id}-w1.json"}],
        "success_criteria": {"metric": "reply_rate", "target": "≥5%", "stop": "退信>5%"},
        "review": {"human_approved": False, "approved_at": None},
    }
    plan.update(over)
    return plan


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "data"
        self.dir.mkdir()
        self.addCleanup(self.tmp.cleanup)

    def write_plan(self, plan=None, name=None):
        plan = plan or make_plan()
        p = self.dir / "plans" / f"{name or plan['plan_id']}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        return p


class TestWorkspace(unittest.TestCase):
    def test_priority_and_env(self):
        default = workspace.resolve(env={})
        self.assertEqual(default.name, "data")
        self.assertEqual(workspace.resolve(env={"MKT_HOME": "/tmp/x"}),
                         Path("/tmp/x").resolve())
        self.assertEqual(workspace.resolve("/tmp/y", env={"MKT_HOME": "/tmp/x"}),
                         Path("/tmp/y").resolve())


class TestApproval(Tmp):
    def test_requires_signature_and_lint(self):
        self.write_plan()
        with self.assertRaises(approval.ApprovalError):
            approval.approve_plan(self.dir, "p1", by="")
        bad = make_plan("p2", icp={"org": "x"})
        self.write_plan(bad)
        with self.assertRaises(approval.ApprovalError):
            approval.approve_plan(self.dir, "p2", by="roy")

    def test_approve_fingerprints_and_tamper_blocks(self):
        path = self.write_plan()
        plan, _, event = approval.approve_plan(self.dir, "p1", by="roy", days=7)
        self.assertTrue(event["plan_sha256"])
        self.assertTrue(Path(event["snapshot"]).is_file())
        self.assertEqual("plan", enforce_approval(self.dir, "p1-w1"))
        self.assertEqual(0, len(approval.approval_events(self.dir, "p1")) - 1)

        plan = json.loads(path.read_text(encoding="utf-8"))
        plan["waves"][0]["size"] = 99
        path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(PlanNotApproved):
            enforce_approval(self.dir, "p1-w1")
        self.assertFalse(approval.status(plan)["valid"])

    def test_legacy_without_fingerprint_passes(self):
        self.write_plan(make_plan(review={"human_approved": True}))
        self.assertEqual("plan", enforce_approval(self.dir, "p1-w1"))
        self.assertTrue(approval.status(make_plan(review={"human_approved": True}))["legacy"])

    def test_expiry_and_revoke(self):
        path = self.write_plan()
        plan, _, _ = approval.approve_plan(self.dir, "p1", by="roy")
        plan["review"]["expires_at"] = "2000-01-01T00:00:00"
        path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(PlanNotApproved):
            enforce_approval(self.dir, "p1-w1")
        approval.revoke_plan(self.dir, "p1", by="roy", note="改稿")
        with self.assertRaises(PlanNotApproved):
            enforce_approval(self.dir, "p1-w1")


class TestQueue(Tmp):
    def test_lint_catches_authoring_errors(self):
        q = {"campaign": "c1", "channel": "email", "action": "message", "items": [
            {"id": "1", "target": "a@example.com", "body": "你好 {{NAME}}"},
            {"id": "1", "target": "a@example.com", "body": "重复"},
        ]}
        errs = queue.lint_queue(q, path=Path("c1.json"))
        self.assertTrue(any("占位符" in e for e in errs))
        self.assertTrue(any("id 重复" in e for e in errs))
        self.assertTrue(any("目标重复" in e for e in errs))

    def test_filename_must_match_campaign(self):
        q = {"campaign": "c1", "channel": "email", "action": "message",
             "items": [{"id": "1", "target": "a@example.com", "body": "正文"}]}
        errs = queue.lint_queue(q, path=Path("other.json"))
        self.assertTrue(any("文件名" in e for e in errs))

    def test_build_renders_and_rejects_leftover_placeholders(self):
        tpl = self.dir / "t.md"
        tpl.write_text("你好 {name}，看到你在 {org} 做研究。", encoding="utf-8")
        csv_path = self.dir / "t.csv"
        csv_path.write_text("target,name,org\na@example.com,Anna,Acme\n", encoding="utf-8")
        subj = self.dir / "s.md"
        subj.write_text("给 {name} 的一个问题", encoding="utf-8")
        out = self.dir / "queues" / "c1.json"
        q, errs = queue.build_queue(template_path=tpl, targets_csv=csv_path,
                                    campaign="c1", channel="email", action="message",
                                    out_path=out, subject_template=subj)
        self.assertEqual([], errs)
        self.assertEqual("你好 Anna，看到你在 Acme 做研究。", q["items"][0]["body"])
        self.assertEqual("给 Anna 的一个问题", q["items"][0]["subject"])
        self.assertTrue(out.is_file())

        tpl.write_text("你好 {missing}，", encoding="utf-8")
        q2, errs2 = queue.build_queue(template_path=tpl, targets_csv=csv_path,
                                      campaign="c2", channel="email", action="message",
                                      out_path=self.dir / "queues" / "c2.json", force=True)
        self.assertIsNone(q2)
        self.assertTrue(any("missing" in e for e in errs2))


class TestLinks(Tmp):
    def test_utm_preserved_and_idempotent(self):
        url = "https://example.com/p?ref=hn"
        tagged = links.build_url(url, campaign="c1", channel="hn", content="show-hn")
        self.assertIn("ref=hn", tagged)
        self.assertIn("utm_campaign=c1", tagged)
        self.assertIn("utm_source=hn", tagged)
        rec = links.make_link(self.dir, url=url, campaign="c1", channel="hn",
                              content="show-hn")
        again = links.make_link(self.dir, url=url, campaign="c1", channel="hn",
                               content="show-hn")
        self.assertFalse(rec["duplicate"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(1, len(links.list_links(self.dir, "c1")))
        with self.assertRaises(ValueError):
            links.build_url("not-a-url", campaign="c1", channel="hn")


class TestOutcomeIO(Tmp):
    def _csv(self, rows):
        p = self.dir / "e.csv"
        p.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return p

    def test_import_validation_and_idempotency(self):
        p = self._csv([
            "subject,event_type,channel,ts,campaign,source_ref,verify_method,verify_ref",
            "u1,signal,hn,2026-10-31,c1,,,",
            "u1,signup,hn,2026-10-31,c1,,,",
            "u2,activation,hn,2026-10-31,c1,order-0,,",
            "u2,paid,hn,2026-10-31,c1,order-1,后台,o-1",
        ])
        res = outcome_io.import_csv(self.dir, p, campaign="c1")
        self.assertEqual(3, res["imported"])
        self.assertEqual(1, len(res["errors"]))
        self.assertTrue(any("verification" in e["error"] for e in res["errors"]))
        res2 = outcome_io.import_csv(self.dir, p, campaign="c1")
        self.assertEqual(0, res2["imported"])
        self.assertEqual(3, res2["skipped"])
        events = [r for r in OutcomeLedger(self.dir).records("c1") if r["kind"] == "event"]
        self.assertEqual(3, len(events))

    def test_dry_run_writes_nothing(self):
        p = self._csv(["subject,event_type", "u1,signup"])
        outcome_io.import_csv(self.dir, p, campaign="c1", dry_run=True)
        self.assertEqual([], OutcomeLedger(self.dir).records())

    def test_sync_counts_is_idempotent(self):
        led = Ledger(self.dir / "ledger.jsonl")
        led.append(channel="email", action="message", target="a@x.com",
                   result="committed", campaign="c1", ref="1")
        led.append(channel="email", action="message", target="b@x.com",
                   result="dry-run", campaign="c1", ref="2")
        res = outcome_io.sync_ledger(self.dir, led, "c1")
        self.assertEqual(2, len(res["created"]))
        res2 = outcome_io.sync_ledger(self.dir, led, "c1")
        self.assertEqual([], res2["created"])
        self.assertEqual(2, len(res2["skipped"]))


class TestLedgerResolve(Tmp):
    def test_resolution_overlays_without_rewriting(self):
        led = Ledger(self.dir / "ledger.jsonl")
        led.append(channel="email", action="message", target="a@x.com",
                   result="indeterminate", campaign="c1", ref="r1")
        self.assertEqual(1, len(led.indeterminate()))
        with self.assertRaises(ValueError):
            led.resolve("r1", result="committed", by="", note="x")
        led.resolve("r1", result="committed", by="roy", note="后台确认")
        self.assertEqual([], led.indeterminate())
        self.assertEqual("committed", led.results(campaign="c1")[0]["result"])
        raw = json.loads((self.dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual("indeterminate", raw["result"])  # 原行不改


class TestCampaign(Tmp):
    def _setup_plan(self, approved=True):
        plan = make_plan()
        self.write_plan(plan)
        if approved:
            approval.approve_plan(self.dir, "p1", by="roy")

    def test_next_state_machine(self):
        led = Ledger(self.dir / "ledger.jsonl")
        self.assertEqual("plan-new", camp.next_step(self.dir, led)["code"])

        self._setup_plan(approved=False)
        self.assertEqual("approve", camp.next_step(self.dir, led, "p1-w1")["code"])

        approval.approve_plan(self.dir, "p1", by="roy")
        self.assertEqual("queue-missing", camp.next_step(self.dir, led, "p1-w1")["code"])

        q = {"campaign": "p1-w1", "channel": "email", "action": "message",
             "items": [{"id": "1", "target": "a@example.com", "body": "正文"}]}
        (self.dir / "queues").mkdir(parents=True, exist_ok=True)
        (self.dir / "queues" / "p1-w1.json").write_text(json.dumps(q), encoding="utf-8")
        self.assertEqual("dry-run", camp.next_step(self.dir, led, "p1-w1")["code"])

        led.append(channel="email", action="message", target="a@example.com",
                   result="dry-run", campaign="p1-w1", ref="1")
        self.assertEqual("go", camp.next_step(self.dir, led, "p1-w1")["code"])

        led.append(channel="email", action="message", target="a@example.com",
                   result="committed", campaign="p1-w1", ref="1")
        self.assertEqual("capture", camp.next_step(self.dir, led, "p1-w1")["code"])

        OutcomeLedger(self.dir).add_event(subject="u1", event_type="signup", campaign="p1-w1")
        self.assertEqual("experiment-new", camp.next_step(self.dir, led, "p1-w1")["code"])

    def test_collect_and_report(self):
        self._setup_plan()
        q = {"campaign": "p1-w1", "channel": "email", "action": "message",
             "items": [{"id": "1", "target": "a@example.com", "body": "正文"}]}
        (self.dir / "queues").mkdir(parents=True, exist_ok=True)
        (self.dir / "queues" / "p1-w1.json").write_text(json.dumps(q), encoding="utf-8")
        led = Ledger(self.dir / "ledger.jsonl")
        led.append(channel="email", action="message", target="a@example.com",
                   result="dry-run", campaign="p1-w1", ref="1")
        rep = camp.collect(self.dir, led, "p1-w1")
        self.assertTrue(rep["queue"]["exists"])
        self.assertEqual(1, rep["queue"]["items"])
        self.assertEqual(1, len(rep["queue"]["preflight"]))
        self.assertEqual(1, rep["ledger"]["dry_run"])
        md = camp.render_markdown(dict(rep, next=camp.next_step(self.dir, led, "p1-w1")))
        self.assertIn("# Campaign 报告", md)
        self.assertIn("口径说明", md)


class TestAutoObservation(Tmp):
    def test_rate_from_outcomes(self):
        ol = OutcomeLedger(self.dir)
        ol.add_event(subject="u1", event_type="signal", campaign="c1")
        ol.add_event(subject="u2", event_type="signal", campaign="c1")
        ol.add_event(subject="u1", event_type="signup", campaign="c1")
        new_experiment(self.dir, exp_id="e1", hypothesis="h", metric="signup_rate",
                       decision_rule="≥0.1", start="2026-10-31", end="2026-11-01",
                       field="signup_rate", agg="latest", campaign="c1",
                       source="outcomes")
        start_experiment(self.dir, "e1")
        exp = observe_from_outcomes(self.dir, "e1")
        self.assertEqual(0.5, exp["observations"][-1]["data"]["signup_rate"])
        self.assertEqual("outcomes/events.jsonl", exp["observations"][-1]["evidence_ref"])


if __name__ == "__main__":
    unittest.main()
