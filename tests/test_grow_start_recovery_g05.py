try:  # discover -s tests puts tests/ on sys.path
    from support_compliance import bypass_x_compliance
except ImportError:  # python -m unittest tests.<module>
    from tests.support_compliance import bypass_x_compliance
"""G-05 验收冻结：统一启动、监控与安全恢复（P1，新手第一次跑）。

先于实现编写。冻结四组语义：

1. `mkt grow start` 九步向导：自动体检 → 凭据/登录清单 → 账号证明 → 目标与
   指标来源 → 首批 3 条候选预览 → 风险与配额 → 显式 Arm → 前台/计划任务 →
   日报订阅。向导绝不代签政策、绝不制造身份凭据、绝不自动 Arm；缺登录/授权
   就停在 ready_to_arm 之前并给准确下一步。
2. `status --explain` / `incident --list` / `resume --after-reconciliation`：
   C-01 的 next_actor / next_required_action / blocked_reason_code /
   last_updated_at / has_pending_external_effect / artifact_refs 必须齐全；
   每个报警有唯一事件 ID 和人工恢复建议。
3. 八行故障矩阵逐行可判定：浏览器崩溃、账号/授权过期、未知回执、账本/抑制
   表损坏、读数失败、重复启动、供给不足、时区切换；indeterminate 未裁决
   禁止自动重试，读数失败不许伪造 0，窗口按 RunSpec IANA 时区算。
4. AC G05 七条：干净机器不联网不发消息、未登录有准确下一步、pause 立即
   生效、重启不重发 indeterminate、调度器绑定 workspace、日卡与邮件分离。

全部离线：合成数据、注入探测器，零网络、零真实发送（工单 G-05，P1）。
"""
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core import grow_runbook as rb
from core import safety
from core import session_gate
from core.execution_policy import authorize
from core.grow_v2 import GrowError, GrowRunV2
from core.ledger import Ledger
from core.queue_runner import run_queue

ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"
RUN = "g5"
STEP_NAMES = ["doctor", "credentials", "identity", "goal", "preview",
              "risk", "arm", "schedule", "delivery"]


def facts(tmp: Path, **override) -> Path:
    payload = {
        "product": {"name": "Verovo", "what": "AI 账期分摊",
                    "url": "https://example.invalid", "stage": "launched",
                    "product_type": "devtool"},
        "icp": [{"hypothesis": "开发者", "tags": ["developers"],
                 "evidence_label": "hypothesis", "evidence": "待验证"}],
        "baseline": {"followers": 120},
        "value_events": [{"name": "激活", "source": "产品事件",
                          "is_approximation": False}],
        "constraints": {"red_lines": ["不做群发"]},
        "owned_assets": {"platforms": ["x", "email"]},
    }
    payload.update(override)
    path = tmp / "facts.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def probe(status: str = session_gate.LOGGED_IN):
    calls = []

    def fn(channel):
        calls.append(channel)
        return {"channel": channel, "status": status,
                "checked_at": "2026-11-15T09:00:00"}

    fn.calls = calls
    return fn


def plan_kwargs(facts_path, **override):
    kwargs = {
        "facts_path": str(facts_path),
        "goal_id": "commercial.conversations",
        "run_id": RUN,
        "channels": ("email",),
        "actions": ("message",),
        "actions_per_week": 7,
        "handle": "verovo",
        "audience": "us-west",
        "login_probe": probe(),
    }
    kwargs.update(override)
    return kwargs


def _bound_proof(channel: str, user_id: str):
    from core.account_identity import AccountIdentityProof
    return AccountIdentityProof(channel=channel, user_id=user_id, handle="acct-a",
                                browser_context="test:bound", source_ref="test:reader")


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.facts = facts(self.dir)
        self.run_path = self.dir / "grow" / f"{RUN}.json"

    def authorize(self, **override):
        kwargs = dict(by="tester", actions=["x:reply", "email:message"],
                      days=90)
        kwargs.update(override)
        return authorize(self.dir, **kwargs)

    def plan(self, **override):
        return rb.build_start_plan(self.dir, **plan_kwargs(self.facts,
                                                          **override))

    def apply(self, plan=None):
        return rb.apply_start_plan(plan or self.plan(), self.dir)

    def launched(self, name=RUN):
        """直接建一个可执行的 run（等价已走完向导 + arm）。"""
        self.authorize()
        path = self.dir / "grow" / f"{name}.json"
        GrowRunV2.launch(path, run_id=name, facts_path=str(self.facts),
                         goal_id="awareness.followers", channels=["x"],
                         target=20, actions_per_week=7,
                         started="2026-11-01", handle="verovo",
                         data_dir=self.dir, skip_probe=True)
        run = GrowRunV2(path)
        run.arm(by="roy")
        return run, path

    def ledger(self):
        return Ledger(self.dir / "ledger.jsonl")

    def seed(self, *, result="committed", target="tweet:https://x.com/a/1",
             ref="ref-1", ts="2026-11-03T09:00:00", campaign=None):
        return self.ledger().append(
            channel="x", action="reply", target=target, result=result,
            campaign=campaign or f"{RUN}-20261103", ts=ts, ref=ref)


class StartPlanTests(Workspace):
    """九步向导：不停在命令堆里，也不替人签字。"""

    def test_nine_steps_in_prd_order(self):
        plan = self.plan()
        self.assertEqual(STEP_NAMES, [step["name"] for step in plan["steps"]])
        self.assertEqual(9, len(plan["steps"]))
        for step in plan["steps"]:
            self.assertIn("state", step)
            self.assertIn("next_action", step)

    def test_missing_facts_blocks_before_any_write(self):
        plan = rb.build_start_plan(
            self.dir, **plan_kwargs(self.dir / "missing.json"))
        self.assertIn(plan["ux_state"], ("BLOCKED", "NEEDS_INPUT"))
        self.assertIn("product_facts", plan["missing"])
        self.assertIn("--facts", plan["next_command"])
        with self.assertRaises(rb.RunbookError) as ctx:
            rb.apply_start_plan(plan, self.dir)
        self.assertEqual("INPUT_REQUIRED", ctx.exception.code)
        self.assertFalse(self.run_path.exists())

    def test_missing_policy_blocks_with_exact_command(self):
        plan = self.plan()
        self.assertEqual("BLOCKED", plan["ux_state"])
        self.assertIn("risk_policy", plan["missing"])
        self.assertIn("mkt policy enable", plan["next_command"])
        with self.assertRaises(rb.RunbookError):
            rb.apply_start_plan(plan, self.dir)
        self.assertFalse(self.run_path.exists())

    def test_not_logged_in_stops_before_ready_to_arm(self):
        self.authorize()
        plan = self.plan(channels=("x",), actions=("reply",),
                         login_probe=probe(session_gate.LOGGED_OUT))
        self.assertEqual("NEEDS_INPUT", plan["ux_state"])
        self.assertEqual("AUTH_REQUIRED", plan["blocked_reason_code"])
        self.assertIn("登录", plan["next_required_action"])
        with self.assertRaises(rb.RunbookError):
            rb.apply_start_plan(plan, self.dir)
        self.assertFalse(self.run_path.exists())

    def test_cdp_unavailable_also_blocks_plan(self):
        self.authorize()
        plan = self.plan(channels=("x",), actions=("reply",),
                         login_probe=probe(session_gate.CDP_UNAVAILABLE))
        self.assertEqual("NEEDS_INPUT", plan["ux_state"])
        with self.assertRaises(rb.RunbookError):
            rb.apply_start_plan(plan, self.dir)
        self.assertFalse(self.run_path.exists())

    def test_ready_to_arm_never_arms_itself(self):
        self.authorize()
        plan = self.plan()
        self.assertEqual("READY", plan["ux_state"])
        arm_step = next(s for s in plan["steps"] if s["name"] == "arm")
        self.assertEqual("pending_human", arm_step["state"])
        result = rb.apply_start_plan(plan, self.dir)
        self.assertEqual("ready_to_arm", result["status"])
        run = GrowRunV2(Path(result["run_path"]))
        data = run.load()
        self.assertEqual("ready_to_arm", data["status"])
        self.assertFalse(data["go"])
        self.assertIn(f"mkt grow arm {RUN}", plan["next_command"])
        # 只有人的 arm 才把它推进到 active
        run.arm(by="roy")
        self.assertEqual("active", run.load()["status"])
        self.assertTrue(run.load()["go"])

    def test_preview_is_three_offline_candidates(self):
        self.authorize()
        calls = probe()
        plan = self.plan(login_probe=calls)
        self.assertEqual(3, len(plan["preview"]))
        for index, row in enumerate(plan["preview"], 1):
            self.assertEqual(index, row["index"])
            self.assertTrue(row["their_problem"])
            self.assertTrue(row["why_we_help"])
            self.assertTrue(row["expected_action"])
            self.assertTrue(row["needs_review"])
            self.assertFalse(row.get("is_sendable", False))
        self.assertEqual([], calls.calls)  # email 没有浏览器登录态探测，不搜网

    def test_delivery_defaults_local_and_email_needs_separate_authorization(self):
        self.authorize()
        local = self.plan()
        self.assertEqual("local", local["delivery"]["mode"])
        self.assertFalse(local["delivery"]["wizard_sends"])
        email = self.plan(delivery="email", email_owner="owner@example.com")
        self.assertEqual("needs_review", email["steps"][-1]["state"])
        self.assertFalse(email["delivery"]["email_authorized"])
        self.assertFalse(email["delivery"]["wizard_sends"])
        self.assertIn("--authorize-email", email["delivery"]["next_action"])
        allowed = self.plan(delivery="email", email_owner="owner@example.com",
                            email_authorized=True)
        self.assertTrue(allowed["delivery"]["email_authorized"])
        self.assertFalse(allowed["delivery"]["wizard_sends"])

    def test_missing_activation_definition_is_review_not_blocking(self):
        self.authorize()
        bare = facts(self.dir, value_events=[])
        plan = rb.build_start_plan(self.dir, **plan_kwargs(bare))
        self.assertEqual("READY", plan["ux_state"])
        self.assertIn("activation_definition", plan["missing"])
        goal_step = next(s for s in plan["steps"] if s["name"] == "goal")
        self.assertEqual("needs_review", goal_step["state"])

    def test_identity_bound_without_records_stops_at_review(self):
        self.authorize(actions=["email:message"], accounts=["acct-a"],
                       require_identity=True)
        plan = self.plan(account="acct-a")
        identity = next(s for s in plan["steps"] if s["name"] == "identity")
        self.assertEqual("needs_review", identity["state"])
        self.assertEqual("REVIEW_REQUIRED", plan["blocked_reason_code"])
        result = rb.apply_start_plan(plan, self.dir)
        self.assertEqual("draft", result["status"])

    def test_build_never_writes_until_apply(self):
        self.authorize()
        self.plan()
        self.assertFalse((self.dir / "grow").exists())
        self.assertFalse((self.dir / "queues").exists())
        self.assertFalse((self.dir / "ledger.jsonl").exists())

    def test_schedule_choice_writes_files_only_on_apply(self):
        self.authorize()
        plan = self.plan(schedule="foreground")
        self.assertIn("run-loop.command", json.dumps(plan["schedule"]))
        result = rb.apply_start_plan(plan, self.dir)
        self.assertTrue(any("run-loop.command" in a for a in result["artifacts"]))
        scheduled = self.plan(run_id="g5b", schedule="scheduled")
        result = rb.apply_start_plan(scheduled, self.dir)
        plist = self.dir / "grow" / "g5b" / "com.growovo.g5b.plist"
        self.assertTrue(plist.is_file())
        text = plist.read_text(encoding="utf-8")
        self.assertIn("MKT_HOME", text)
        self.assertIn(str(self.dir.resolve()), text)


class StatusExplainTests(Workspace):
    def test_explain_has_c01_required_fields(self):
        run, path = self.launched()
        explain = rb.explain_status(self.dir, path, on="2026-11-03")
        for field in ("next_actor", "next_required_action",
                      "blocked_reason_code", "last_updated_at",
                      "has_pending_external_effect", "artifact_refs",
                      "ux_state", "goal_status", "quota_remaining",
                      "observation_window", "data_coverage", "next_command"):
            self.assertIn(field, explain)
        self.assertEqual("system", explain["next_actor"])
        self.assertFalse(explain["has_pending_external_effect"])
        self.assertTrue(explain["artifact_refs"])

    def test_ready_to_arm_next_action_is_arm(self):
        self.authorize()
        result = rb.apply_start_plan(self.plan(), self.dir)
        explain = rb.explain_status(self.dir, Path(result["run_path"]))
        self.assertEqual("NEEDS_INPUT", explain["ux_state"])
        self.assertEqual("human", explain["next_actor"])
        self.assertIn(f"mkt grow arm {RUN}", explain["next_command"])

    def test_indeterminate_blocks_and_names_incident(self):
        run, path = self.launched()
        self.seed(result="indeterminate", ref="ref-ind")
        explain = rb.explain_status(self.dir, path, on="2026-11-03")
        self.assertTrue(explain["has_pending_external_effect"])
        self.assertEqual("INDETERMINATE_EXTERNAL_ACTION",
                         explain["blocked_reason_code"])
        self.assertEqual("human", explain["next_actor"])
        self.assertIn("INC-", explain["next_required_action"])
        self.assertIn("mkt grow resume", explain["next_command"])

    def test_quota_remaining_and_observation_window(self):
        run, path = self.launched()
        self.seed()
        explain = rb.explain_status(self.dir, path, on="2026-11-03")
        quota = explain["quota_remaining"]
        self.assertIn("remaining", quota)
        self.assertGreaterEqual(quota["remaining"], 0)
        window = explain["observation_window"]
        self.assertEqual("2026-11-01", window["start"])
        self.assertIn("days_observed", window)
        coverage = explain["data_coverage"]
        self.assertIn("reading_days", coverage)

    def test_paused_maps_to_blocked_with_resume_next(self):
        run, path = self.launched()
        run.pause(reason="人工接管", by="roy")
        explain = rb.explain_status(self.dir, path, on="2026-11-03")
        self.assertEqual("BLOCKED", explain["ux_state"])
        self.assertIn("mkt grow resume", explain["next_command"])


class IncidentTests(Workspace):
    def test_each_incident_has_unique_stable_id_and_recovery(self):
        run, path = self.launched()
        self.seed(result="indeterminate", ref="ref-a",
                  target="tweet:https://x.com/a/1")
        self.seed(result="indeterminate", ref="ref-b",
                  target="tweet:https://x.com/a/2")
        report = rb.list_incidents(self.dir, path, now=None)
        ids = [i["event_id"] for i in report["incidents"]]
        self.assertGreaterEqual(len(ids), 2)
        self.assertEqual(len(ids), len(set(ids)))
        for incident in report["incidents"]:
            self.assertTrue(incident["event_id"].startswith("INC-"))
            self.assertTrue(incident["recovery"])
            self.assertTrue(incident["next_command"])
            self.assertIn(incident["error_code"], rb.C01_ERROR_CODES)
            self.assertTrue(incident["detected_at"])
        again = rb.list_incidents(self.dir, path)
        self.assertEqual(sorted(ids),
                         sorted(i["event_id"] for i in again["incidents"]))

    def test_corrupt_ledger_is_incident_not_crash(self):
        run, path = self.launched()
        (self.dir / "ledger.jsonl").write_text("{broken line\n",
                                               encoding="utf-8")
        report = rb.list_incidents(self.dir, path)
        kinds = [i["kind"] for i in report["incidents"]]
        self.assertIn("ledger_or_suppression_corrupt", kinds)
        corrupt = [i for i in report["incidents"]
                   if i["kind"] == "ledger_or_suppression_corrupt"][0]
        self.assertEqual("blocking", corrupt["severity"])
        self.assertIn("修复", corrupt["recovery"])

    def test_corrupt_suppression_is_incident(self):
        run, path = self.launched()
        (self.dir / "contacts.jsonl").write_text("{broken\n", encoding="utf-8")
        report = rb.list_incidents(self.dir, path)
        kinds = [i["kind"] for i in report["incidents"]]
        self.assertIn("ledger_or_suppression_corrupt", kinds)

    def test_policy_expired_incident(self):
        run, path = self.launched()
        authorize(self.dir, by="tester", actions=["x:reply"], days=1,
                  now="2020-01-01T00:00:00+00:00")
        report = rb.list_incidents(self.dir, path)
        kinds = [i["kind"] for i in report["incidents"]]
        self.assertIn("account_or_policy", kinds)
        incident = [i for i in report["incidents"]
                    if i["kind"] == "account_or_policy"][0]
        self.assertEqual("blocking", incident["severity"])
        self.assertIn("AUTH_REQUIRED", incident["error_code"])
        self.assertIn("mkt policy enable", incident["next_command"])

    def test_readings_failure_incident_never_says_zero(self):
        run, path = self.launched()
        data = run.load()
        data.setdefault("telemetry", {}).setdefault("days", {})["2026-11-03"] = {
            "readings": {"error": "CDP 掉线", "attempts": 3}}
        run.save(data)
        report = rb.list_incidents(self.dir, path)
        kinds = [i["kind"] for i in report["incidents"]]
        self.assertIn("readings_degraded", kinds)
        incident = [i for i in report["incidents"]
                    if i["kind"] == "readings_degraded"][0]
        self.assertEqual("warning", incident["severity"])
        self.assertIn("不伪造 0", json.dumps(incident, ensure_ascii=False))

    def test_supply_gap_incident(self):
        run, path = self.launched()
        report = rb.list_incidents(self.dir, path)
        kinds = [i["kind"] for i in report["incidents"]]
        self.assertIn("supply_shortage", kinds)
        incident = [i for i in report["incidents"]
                    if i["kind"] == "supply_shortage"][0]
        self.assertIn("mkt grow", incident["next_command"])

    def test_duplicate_instance_incident(self):
        run, path = self.launched()
        lock = Path(str(path) + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        handle = lock.open("a")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(lambda: (fcntl.flock(handle, fcntl.LOCK_UN),
                                 handle.close()))
        report = rb.list_incidents(self.dir, path)
        kinds = [i["kind"] for i in report["incidents"]]
        self.assertIn("duplicate_instance", kinds)
        incident = [i for i in report["incidents"]
                    if i["kind"] == "duplicate_instance"][0]
        self.assertIn("另", incident["recovery"])
        self.assertIn("pause", incident["next_command"])

class ReconciliationTests(Workspace):
    def setUp(self):
        super().setUp()
        bypass_x_compliance(self)

    def _needs_attention(self):
        run, path = self.launched()
        self.seed(result="indeterminate", ref="ref-ind",
                  target="tweet:https://x.com/a/9")
        data = run.load()
        data["status"] = "needs_attention"
        run.save(data)
        report = rb.list_incidents(self.dir, path)
        incident = [i for i in report["incidents"]
                    if i["kind"] == "indeterminate_external_action"][0]
        return run, path, incident

    def test_resume_blocked_until_indeterminate_resolved(self):
        run, path, incident = self._needs_attention()
        with self.assertRaises(rb.RunbookError) as ctx:
            rb.resume_after_reconciliation(self.dir, path, incident["event_id"],
                                           by="roy")
        self.assertEqual("INDETERMINATE_EXTERNAL_ACTION",
                         ctx.exception.code)
        self.assertIn("mkt ledger resolve", str(ctx.exception))
        self.assertEqual("needs_attention", run.load()["status"])

    def test_resume_requires_operator_signature(self):
        run, path, incident = self._needs_attention()
        with self.assertRaises(rb.RunbookError) as ctx:
            rb.resume_after_reconciliation(self.dir, path, incident["event_id"],
                                           by="")
        self.assertEqual("INPUT_REQUIRED", ctx.exception.code)

    def test_resume_after_resolution_activates_and_records(self):
        run, path, incident = self._needs_attention()
        self.ledger().resolve("ref-ind", result="committed", by="roy",
                              note="平台后台已看到这条")
        result = rb.resume_after_reconciliation(
            self.dir, path, incident["event_id"], by="roy",
            note="已核对平台后台")
        self.assertTrue(result["resumed"])
        self.assertEqual("active", result["status"])
        data = run.load()
        self.assertEqual("active", data["status"])
        history = [h for h in data.get("history") or []
                   if h.get("after_reconciliation") == incident["event_id"]]
        self.assertEqual(1, len(history))
        self.assertEqual("roy", history[0]["by"])

    def test_resume_wrong_incident_id_rejected(self):
        run, path, incident = self._needs_attention()
        with self.assertRaises(rb.RunbookError) as ctx:
            rb.resume_after_reconciliation(self.dir, path, "INC-deadbeef",
                                           by="roy")
        self.assertEqual("INPUT_REQUIRED", ctx.exception.code)

    def test_resume_refuses_while_other_blocking_incident_remains(self):
        run, path, incident = self._needs_attention()
        self.ledger().resolve("ref-ind", result="committed", by="roy",
                              note="已核对")
        authorize(self.dir, by="tester", actions=["x:reply"], days=1,
                  now="2020-01-01T00:00:00+00:00")
        with self.assertRaises(rb.RunbookError) as ctx:
            rb.resume_after_reconciliation(self.dir, path, incident["event_id"],
                                           by="roy")
        self.assertIn("还有", str(ctx.exception))
        self.assertEqual("needs_attention", run.load()["status"])

    def test_restart_does_not_resend_indeterminate(self):
        """调度路径（tick→run_queue→adapter）也不能碰 vendor。"""
        from unittest.mock import patch
        from channels.x import adapter
        from core import session_gate
        run, path = self.launched()
        vendor = self.dir / "vendor"
        vendor.mkdir(parents=True, exist_ok=True)
        (vendor / "reply_x.py").write_text(
            "import pathlib\n"
            "pathlib.Path(__file__).parent.joinpath('called').write_text('1')\n",
            encoding="utf-8")
        target = "tweet:https://x.com/a/status/7"
        self.seed(result="indeterminate", ref="ref-ind", target=target)
        queue_path = self.dir / "queues" / f"{RUN}-20261103.json"
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text(json.dumps({
            "campaign": f"{RUN}-20261103", "channel": "x", "action": "reply",
            "account": "acct-a",
            "items": [{"id": "i1", "target": target,
                       "body": "先说结论，再给一个可复现的细节。",
                       "scheduled_for": "2026-11-03"}]}, ensure_ascii=False),
            encoding="utf-8")
        # G-01b 把“旧授权未绑定身份”的真实发送也拦下了（AC-H）。本用例要测的是
        # indeterminate 不重发，所以先把授权绑到具体身份并注入匹配的观测，
        # 让队列真正走到不重试判定，而不是停在身份门禁。
        from core import account_identity
        # tick 用 guards.account_id 作为账号范围；先把 run 的账号补上，
        # 再按该账号绑定身份，队列才会走到不重试判定而不是停在身份门禁。
        data = run.load()
        data.setdefault("guards", {})["account_id"] = "acct-a"
        run.save(data)
        self.authorize(accounts=["acct-a"], identities=["acct-a=x:uid-a"],
                       require_identity=True)
        account_identity.set_reader(lambda channel, **kw: _bound_proof("x", "uid-a"))
        before = len(self.ledger().all())
        logged_in = lambda channel, **kw: {"channel": channel,
                                          "status": "logged_in"}
        with patch.object(adapter, "VENDOR", vendor), \
                patch.object(session_gate, "check_login", logged_in):
            first = run.tick(self.dir, on="2026-11-03",
                             now=_now("2026-11-03"))
            second = run.tick(self.dir, on="2026-11-03",
                              now=_now("2026-11-03"))
        self.assertTrue(first["ran"])
        self.assertTrue(any("不重试名单" in str(entry.get("error") or "")
                            for entry in first["ran"]),
                        f"tick 应报告不重试拦截：{first}")
        self.assertTrue(second["ran"] or second.get("skipped"))
        self.assertFalse((vendor / "called").exists(),
                         "未裁决的 indeterminate 目标不得触碰 vendor")
        self.assertEqual(before, len(self.ledger().all()))


class PauseTests(Workspace):
    def test_pause_is_immediate_and_reversible(self):
        run, path = self.launched()
        data = run.pause(reason="人工接管", by="roy")
        self.assertEqual("paused", data["status"])
        summary = run.tick(self.dir, on="2026-11-03",
                           runner=lambda *a, **k: [], now=_now("2026-11-03"))
        self.assertEqual([], summary.get("ran"))
        self.assertEqual("paused", summary.get("skipped"))
        self.assertFalse((self.dir / "ledger.jsonl").exists())
        resumed = run.resume(note="接管结束", by="roy")
        self.assertEqual("active", resumed["status"])

    def test_global_pause_blocks_tick(self):
        run, path = self.launched()
        safety.set_pause(self.dir, True, "事故")
        summary = run.tick(self.dir, on="2026-11-03",
                           runner=lambda *a, **k: [], now=_now("2026-11-03"))
        self.assertEqual("paused", summary.get("skipped"))
        self.assertEqual([], summary.get("ran"))
        safety.set_pause(self.dir, False)


class FaultMatrixTests(Workspace):
    def test_matrix_has_eight_rows_with_handling_and_recovery(self):
        self.assertEqual(8, len(rb.FAULT_MATRIX))
        codes = [row["code"] for row in rb.FAULT_MATRIX]
        self.assertEqual(len(codes), len(set(codes)))
        for row in rb.FAULT_MATRIX:
            self.assertTrue(row["handling"])
            self.assertTrue(row["recovery"])
            self.assertIn(row["error_code"], rb.C01_ERROR_CODES)

    def test_diagnose_has_all_eight_rows(self):
        run, path = self.launched()
        report = rb.diagnose(self.dir, path, on="2026-11-03")
        codes = [row["code"] for row in report["rows"]]
        self.assertEqual(8, len(codes))
        self.assertEqual(set(codes),
                         {row["code"] for row in rb.FAULT_MATRIX})

    def test_diagnose_forced_scenarios(self):
        run, path = self.launched()
        self.seed(result="indeterminate", ref="ref-ind")
        data = run.load()
        data.setdefault("telemetry", {}).setdefault("days", {})["2026-11-03"] = {
            "readings": {"error": "CDP 掉线"}}
        data.setdefault("windows", {})["audience"] = "mars"
        run.save(data)
        report = rb.diagnose(self.dir, path, on="2026-11-03",
                             login_probe=probe(session_gate.LOGGED_OUT))
        rows = {row["code"]: row for row in report["rows"]}
        self.assertEqual("blocked", rows["indeterminate_external_action"]["state"])
        self.assertEqual("blocked", rows["browser_unavailable"]["state"])
        self.assertEqual("warn", rows["readings_degraded"]["state"])
        self.assertEqual("blocked", rows["timezone_switch"]["state"])
        self.assertEqual("warn", rows["supply_shortage"]["state"])

    def test_timezone_uses_run_iana_not_host(self):
        from core import grow_schedule as gs
        summer = datetime(2026, 7, 15, 3, 0, tzinfo=timezone.utc)
        self.assertTrue(gs.window_allows("20:00-23:00@us-west", summer,
                                         iana="America/Los_Angeles"))
        self.assertFalse(gs.window_allows("20:00-23:00@us-west", summer))

    def test_diagnose_timezone_ok_with_iana(self):
        run, path = self.launched()
        report = rb.diagnose(self.dir, path, on="2026-11-03")
        row = {r["code"]: r for r in report["rows"]}["timezone_switch"]
        self.assertEqual("ok", row["state"])
        self.assertIn("America/Los_Angeles", json.dumps(row, ensure_ascii=False))

    def test_reading_failure_does_not_fabricate_zero(self):
        run, path = self.launched()

        def broken(_on):
            raise RuntimeError("CDP 掉线")

        summary = run.tick(self.dir, on="2026-11-03",
                           runner=lambda *a, **k: [], reader=broken,
                           now=_now("2026-11-03"))
        card = summary["daily"]
        self.assertIsNone((card.get("readings") or {}).get("followers"))
        text = GrowRunV2.render_daily(card)
        self.assertIn("取不到", text)
        self.assertIn("CDP 掉线", text)


class DeliveryAndSchedulerTests(Workspace):
    def test_daily_card_written_without_email_send(self):
        run, path = self.launched()
        run.tick(self.dir, on="2026-11-03", runner=lambda *a, **k: [],
                 now=_now("2026-11-03"))
        cards = list((self.dir / "grow" / RUN / "cards").glob("daily-*.md"))
        self.assertTrue(cards)
        outbox = self.dir / "grow" / RUN / "outbox"
        self.assertEqual([], list(outbox.glob("*.eml")) if outbox.exists() else [])

    def test_deliver_dry_run_writes_eml_but_sends_nothing(self):
        from core import deliver
        result = deliver.deliver_card(
            self.dir, RUN, "daily", "2026-11-03", "# 日结果",
            to="owner@example.com", live=False)
        self.assertTrue(Path(result["markdown"]).is_file())
        self.assertFalse(result["sent"])
        self.assertTrue(result["dry_run"])
        self.assertTrue(Path(result["path"]).is_file())
        self.assertTrue(str(result["path"]).endswith(".eml"))

    def test_install_binds_workspace_not_current_directory(self):
        run, path = self.launched()
        result = run.install(self.dir, load=False)
        plist = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn("MKT_HOME", plist)
        self.assertIn(str(self.dir.resolve()), plist)
        self.assertIn("MKT_HOME", result["command"])
        self.assertIn(str(self.dir.resolve()), result["command"])
        loop = Path(result["fallback"]).read_text(encoding="utf-8")
        self.assertIn(f"MKT_HOME={self.dir.resolve()}", loop)


class CleanMachineTests(Workspace):
    def test_start_plan_makes_no_network_calls_and_no_writes(self):
        self.authorize()
        calls = probe()
        plan = rb.build_start_plan(self.dir, **plan_kwargs(self.facts,
                                                          login_probe=calls))
        self.assertEqual([], calls.calls)
        self.assertEqual("READY", plan["ux_state"])
        self.assertFalse((self.dir / "ledger.jsonl").exists())
        self.assertFalse((self.dir / "queues").exists())
        self.assertFalse((self.dir / "grow").exists())
        outbox = self.dir / "grow" / RUN / "outbox"
        self.assertFalse(outbox.exists())

    def test_wizard_never_sends_even_with_email_authorized(self):
        self.authorize()
        plan = self.plan(delivery="email", email_owner="owner@example.com",
                         email_authorized=True)
        result = rb.apply_start_plan(plan, self.dir)
        data = GrowRunV2(Path(result["run_path"])).load()
        self.assertFalse(data["go"])
        self.assertEqual("ready_to_arm", data["status"])
        self.assertFalse((self.dir / "grow" / RUN / "outbox").exists())


class CliTests(Workspace):
    def run_mkt(self, *args, check=True):
        env = dict(os.environ, MKT_HOME=str(self.dir))
        proc = subprocess.run([sys.executable, str(MKT), *args],
                              cwd=str(ROOT), env=env, text=True,
                              capture_output=True)
        if check and proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed ({proc.returncode})"
                      f"\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return proc

    def test_start_wizard_dry_run_shows_steps_and_writes_nothing(self):
        self.authorize()
        proc = self.run_mkt(
            "grow", "start", "--run-id", RUN, "--facts", str(self.facts),
            "--goal", "commercial.conversations", "--handle", "verovo",
            "--dry-run")
        for title in ("环境与凭据体检", "首批 3 条候选预览", "显式开闸"):
            self.assertIn(title, proc.stdout)
        self.assertIn("未写入", proc.stdout)
        self.assertFalse(self.run_path.exists())

    def test_start_wizard_apply_stops_before_arm(self):
        self.authorize()
        proc = self.run_mkt(
            "grow", "start", "--run-id", RUN, "--facts", str(self.facts),
            "--goal", "commercial.conversations", "--handle", "verovo",
            "--apply")
        self.assertIn(f"mkt grow arm {RUN}", proc.stdout)
        self.assertTrue(self.run_path.is_file())
        data = GrowRunV2(self.run_path).load()
        self.assertIn(data["status"], ("draft", "ready_to_arm"))
        self.assertFalse(data["go"])

    def test_status_explain_json(self):
        run, path = self.launched()
        self.seed(result="indeterminate", ref="ref-ind")
        proc = self.run_mkt("grow", "status", RUN, "--explain", "--json")
        payload = json.loads(proc.stdout)
        self.assertIn("next_actor", payload)
        self.assertIn("next_required_action", payload)
        self.assertEqual("INDETERMINATE_EXTERNAL_ACTION",
                         payload["blocked_reason_code"])

    def test_incident_list_cli(self):
        run, path = self.launched()
        self.seed(result="indeterminate", ref="ref-ind")
        proc = self.run_mkt("grow", "incident", RUN, "--list", "--json")
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["incidents"])
        self.assertTrue(payload["incidents"][0]["event_id"].startswith("INC-"))
        text = self.run_mkt("grow", "incident", RUN, "--list").stdout
        self.assertIn("INC-", text)
        self.assertIn("恢复", text)

    def test_resume_cli_requires_reconciliation(self):
        run, path = self.launched()
        self.seed(result="indeterminate", ref="ref-ind")
        data = run.load()
        data["status"] = "needs_attention"
        run.save(data)
        incident = [i for i in rb.list_incidents(self.dir, path)["incidents"]
                    if i["kind"] == "indeterminate_external_action"][0]
        proc = self.run_mkt("grow", "resume", RUN, "--after-reconciliation",
                            incident["event_id"], "--by", "roy", check=False)
        self.assertEqual(2, proc.returncode)
        self.assertIn("mkt ledger resolve", proc.stdout)
        self.run_mkt("ledger", "resolve", "ref-ind", "--result", "committed",
                     "--by", "roy", "--note", "平台后台已看到")
        proc = self.run_mkt("grow", "resume", RUN, "--after-reconciliation",
                            incident["event_id"], "--by", "roy")
        self.assertIn("已恢复", proc.stdout)
        self.assertEqual("active", GrowRunV2(path).load()["status"])

    def test_resume_cli_default_path_requires_reconciliation(self):
        run, path = self.launched()
        self.seed(result="indeterminate", ref="ref-ind")
        data = run.load()
        data["status"] = "needs_attention"
        run.save(data)
        proc = self.run_mkt("grow", "resume", RUN, "--by", "roy",
                            "--note", "处理好了", check=False)
        self.assertEqual(2, proc.returncode)
        self.assertIn("mkt ledger resolve", proc.stdout)
        self.assertIn("--after-reconciliation", proc.stdout)
        self.assertEqual("needs_attention", GrowRunV2(path).load()["status"])
        incident = [i for i in rb.list_incidents(self.dir, path)["incidents"]
                    if i["kind"] == "indeterminate_external_action"][0]
        self.run_mkt("ledger", "resolve", "ref-ind", "--result", "committed",
                     "--by", "roy", "--note", "平台后台已看到")
        proc = self.run_mkt("grow", "resume", RUN, "--after-reconciliation",
                            incident["event_id"], "--by", "roy",
                            "--note", "平台后台已看到")
        self.assertIn("已恢复", proc.stdout)
        self.assertEqual("active", GrowRunV2(path).load()["status"])

    def test_resume_cli_default_path_works_without_blocking(self):
        run, path = self.launched()
        run.pause(reason="人工接管", by="roy")
        proc = self.run_mkt("grow", "resume", RUN, "--by", "roy",
                            "--note", "接管结束")
        self.assertIn("已恢复", proc.stdout)
        self.assertEqual("active", GrowRunV2(path).load()["status"])

    def test_pause_cli_and_skip(self):
        run, path = self.launched()
        proc = self.run_mkt("grow", "pause", RUN, "--reason", "人工接管",
                            "--by", "roy")
        self.assertIn("paused", proc.stdout)
        self.assertEqual("paused", GrowRunV2(path).load()["status"])
        tick = self.run_mkt("grow", "tick", RUN, "--on", "2026-11-03")
        self.assertIn("paused", tick.stdout)


def _now(day: str, hour: int = 16) -> datetime:
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00").replace(
        tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
