"""G-05 实现侧补充测试：向导落盘、状态投影、时区、锁与投递边界。

冻结契约在 ``test_grow_start_recovery_g05.py``；这里补它没覆盖的实现面：

1. 向导落盘幂等、默认 run id、预览只落文件不入队；
2. 状态投影：draft/completed/paused 的下一动作与 artifact refs；
3. 时区：RunSpec 带 IANA，选择条目时按夏令时算窗口；
4. 锁：陈旧锁文件不算重复实例；真正持锁才算；
5. 投递：Email 授权但缺 SMTP 环境时 live_send=False（不虚报可发送）；
6. CLI：向导 --json、draft 不可 arm、incident/pause 链路。

全部离线：合成数据与注入探测器，零网络、零真实发送。
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
from core import session_gate
from core.execution_policy import authorize
from core.grow_v2 import GrowError, GrowRunV2
from core.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"
RUN = "g5"


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
    def fn(channel):
        return {"channel": channel, "status": status,
                "checked_at": "2026-11-15T09:00:00"}

    fn.calls = []
    return fn


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
        kwargs = dict(
            facts_path=str(self.facts), goal_id="commercial.conversations",
            run_id=RUN, channels=("email",), actions=("message",),
            actions_per_week=7, handle="verovo", audience="us-west",
            login_probe=probe())
        kwargs.update(override)
        return rb.build_start_plan(self.dir, **kwargs)

    def launched(self):
        self.authorize()
        GrowRunV2.launch(self.run_path, run_id=RUN, facts_path=str(self.facts),
                         goal_id="awareness.followers", channels=["x"],
                         target=20, actions_per_week=7,
                         started="2026-11-01", handle="verovo",
                         data_dir=self.dir, skip_probe=True)
        run = GrowRunV2(self.run_path)
        run.arm(by="roy")
        return run


class ApplyPlanTests(Workspace):
    def test_apply_is_idempotent_and_refuses_overwrite(self):
        self.authorize()
        plan = self.plan()
        first = rb.apply_start_plan(plan, self.dir)
        self.assertEqual("ready_to_arm", first["status"])
        with self.assertRaises(rb.RunbookError) as ctx:
            rb.apply_start_plan(plan, self.dir)
        self.assertEqual("INPUT_REQUIRED", ctx.exception.code)
        self.assertEqual(1, len(list((self.dir / "grow").glob("g5.json"))))

    def test_preview_is_file_not_queue(self):
        self.authorize()
        rb.apply_start_plan(self.plan(), self.dir)
        preview = self.dir / "grow" / RUN / "start-preview.json"
        self.assertTrue(preview.is_file())
        self.assertEqual(3, len(json.loads(
            preview.read_text(encoding="utf-8"))["preview"]))
        self.assertFalse((self.dir / "queues").exists())

    def test_default_run_id_comes_from_product_and_date(self):
        self.authorize()
        plan = rb.build_start_plan(
            self.dir, facts_path=str(self.facts),
            goal_id="commercial.conversations", channels=("email",),
            actions=("message",), handle="verovo", login_probe=probe())
        self.assertTrue(plan["run_id"].startswith("verovo-"))
        result = rb.apply_start_plan(plan, self.dir)
        self.assertTrue(Path(result["run_path"]).is_file())
        self.assertIn(plan["run_id"], result["run_path"])

    def test_arm_refuses_draft_via_cli(self):
        self.authorize(accounts=["acct-a"], require_identity=True)
        plan = self.plan(account="acct-a")
        result = rb.apply_start_plan(plan, self.dir)
        self.assertEqual("draft", result["status"])
        proc = self._mkt("grow", "arm", RUN, "--by", "roy", check=False)
        self.assertEqual(2, proc.returncode)
        self.assertIn("draft", proc.stdout + proc.stderr)
        self.assertFalse(GrowRunV2(self.run_path).load()["go"])

    def _mkt(self, *args, check=True):
        env = dict(os.environ, MKT_HOME=str(self.dir))
        proc = subprocess.run([sys.executable, str(MKT), *args],
                              cwd=str(ROOT), env=env, text=True,
                              capture_output=True)
        if check and proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed ({proc.returncode})"
                      f"\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return proc


class WizardGuardTests(Workspace):
    def test_unsupported_action_is_rejected_before_compile(self):
        self.authorize()
        plan = self.plan(actions=("nonsense",))
        self.assertFalse(plan["can_apply"])
        self.assertEqual("UNSUPPORTED_SCOPE", plan["blocked_reason_code"])
        with self.assertRaises(rb.RunbookError):
            rb.apply_start_plan(plan, self.dir)
        self.assertFalse(self.run_path.exists())

    def test_empty_channels_is_rejected(self):
        self.authorize()
        plan = self.plan(channels=())
        self.assertFalse(plan["can_apply"])
        self.assertIn("channels", plan["next_command"])

    def test_pause_draft_is_refused(self):
        self.authorize(accounts=["acct-a"], require_identity=True)
        result = rb.apply_start_plan(self.plan(account="acct-a"), self.dir)
        run = GrowRunV2(Path(result["run_path"]))
        with self.assertRaises(GrowError):
            run.pause(reason="想停一下", by="roy")

    def test_arm_paused_is_refused_until_resume(self):
        run = self.launched()
        run.pause(reason="事故", by="roy")
        with self.assertRaises(GrowError):
            run.arm(by="roy")
        run.resume(note="处理完", by="roy")
        armed = run.arm(by="roy")
        self.assertTrue(armed["go"])


class StatusProjectionTests(Workspace):
    def test_draft_next_is_doctor(self):
        self.authorize(accounts=["acct-a"], require_identity=True)
        result = rb.apply_start_plan(self.plan(account="acct-a"), self.dir)
        explain = rb.explain_status(self.dir, Path(result["run_path"]))
        self.assertEqual("NEEDS_INPUT", explain["ux_state"])
        self.assertEqual("INPUT_REQUIRED", explain["blocked_reason_code"])
        self.assertIn("doctor", explain["next_command"])

    def test_completed_maps_to_delivered(self):
        run = self.launched()
        data = run.load()
        data["status"] = "completed"
        run.save(data)
        explain = rb.explain_status(self.dir, self.run_path)
        self.assertEqual("DELIVERED", explain["ux_state"])
        self.assertEqual("none", explain["next_actor"])

    def test_artifact_refs_include_daily_card(self):
        run = self.launched()
        run.tick(self.dir, on="2026-11-03", runner=lambda *a, **k: [],
                 now=datetime(2026, 11, 3, 16, tzinfo=timezone.utc))
        explain = rb.explain_status(self.dir, self.run_path, on="2026-11-03")
        joined = json.dumps(explain["artifact_refs"])
        self.assertIn("cards", joined)
        self.assertIn("daily-2026-11-03", joined)

    def test_explain_paused_resume_command(self):
        run = self.launched()
        run.pause(reason="事故", by="roy")
        explain = rb.explain_status(self.dir, self.run_path)
        self.assertEqual("BLOCKED", explain["ux_state"])
        self.assertEqual("REVIEW_REQUIRED", explain["blocked_reason_code"])
        self.assertIn("mkt grow resume", explain["next_command"])

    def test_resume_on_active_run_is_noop(self):
        run = self.launched()
        result = rb.resume_after_reconciliation(
            self.dir, self.run_path, "INC-whatever", by="roy")
        self.assertFalse(result["resumed"])
        self.assertEqual("active", result["status"])


class TimezoneTests(Workspace):
    def test_compiled_run_stamps_iana_zone(self):
        run = self.launched()
        data = run.load()
        self.assertEqual("America/Los_Angeles",
                         (data.get("windows") or {}).get("iana"))

    def test_selection_uses_iana_in_summer(self):
        run = self.launched()
        queue = {"campaign": f"{RUN}-20260715", "channel": "x",
                 "action": "reply",
                 "items": [{"id": "i1", "target": "tweet:https://x.com/a/1",
                            "body": "先说结论，再给一个可复现的细节。",
                            "scheduled_for": "2026-07-15",
                            "window": "20:00-23:00@us-west"}]}
        summer = datetime(2026, 7, 15, 3, 0, tzinfo=timezone.utc)
        with_tz = GrowRunV2._select_items(
            queue, on="2026-07-15", now=summer, default_window="",
            committed_targets=set(), limit=5,
            audience_tz="America/Los_Angeles")
        self.assertEqual(1, len(with_tz))
        without_tz = GrowRunV2._select_items(
            queue, on="2026-07-15", now=summer, default_window="",
            committed_targets=set(), limit=5)
        self.assertEqual([], without_tz)  # 旧固定偏移：19:00，不在黄金窗


class LockTests(Workspace):
    def test_stale_lock_file_is_not_duplicate(self):
        self.launched()
        (self.dir / "grow" / f"{RUN}.json.lock").write_text("", encoding="utf-8")
        report = rb.diagnose(self.dir, self.run_path)
        row = {r["code"]: r for r in report["rows"]}["duplicate_instance"]
        self.assertEqual("ok", row["state"])

    def test_held_lock_is_reported(self):
        self.launched()
        lock = self.dir / "grow" / f"{RUN}.json.lock"
        handle = lock.open("a")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(lambda: (fcntl.flock(handle, fcntl.LOCK_UN),
                                 handle.close()))
        report = rb.list_incidents(self.dir, self.run_path)
        self.assertIn("duplicate_instance",
                      [i["kind"] for i in report["incidents"]])

    def test_corrupt_ledger_marks_diagnosis_not_crash(self):
        self.launched()
        (self.dir / "ledger.jsonl").write_text("{bad\n", encoding="utf-8")
        report = rb.diagnose(self.dir, self.run_path)
        row = {r["code"]: r for r in report["rows"]}[
            "ledger_or_suppression_corrupt"]
        self.assertEqual("blocked", row["state"])


class DeliveryTests(Workspace):
    def test_email_authorized_but_env_missing_is_not_live(self):
        plan = rb.delivery_plan(RUN, mode="email", owner="owner@example.com",
                                email_authorized=True, env={})
        self.assertTrue(plan["email_authorized"])
        self.assertFalse(plan["live_send"])
        self.assertEqual(2, len(plan["missing_env"]))
        self.assertFalse(plan["wizard_sends"])

    def test_email_ready_with_env_is_still_human_sent(self):
        env = {"MKT_EMAIL_USER": "a@b.c", "MKT_EMAIL_APP_PASSWORD": "x"}
        plan = rb.delivery_plan(RUN, mode="email", owner="owner@example.com",
                                email_authorized=True, env=env)
        self.assertTrue(plan["live_send"])
        self.assertFalse(plan["wizard_sends"])
        self.assertIn("mkt grow tick", plan["next_action"])


class CliJsonTests(Workspace):
    def _mkt(self, *args, check=True):
        env = dict(os.environ, MKT_HOME=str(self.dir))
        proc = subprocess.run([sys.executable, str(MKT), *args],
                              cwd=str(ROOT), env=env, text=True,
                              capture_output=True)
        if check and proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed ({proc.returncode})"
                      f"\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return proc

    def test_start_plan_json_has_nine_steps_and_no_write(self):
        self.authorize()
        proc = self._mkt("grow", "start", "--run-id", RUN,
                         "--facts", str(self.facts), "--goal",
                         "commercial.conversations", "--handle", "verovo",
                         "--dry-run", "--json")
        plan = json.loads(proc.stdout)
        self.assertEqual(9, len(plan["steps"]))
        self.assertFalse(plan["applied"])
        self.assertFalse(self.run_path.exists())

    def test_identity_bound_wizard_drafts_and_arm_fails(self):
        self.authorize(actions=["email:message"], accounts=["acct-a"],
                       require_identity=True)
        plan = self.plan(account="acct-a")
        self.assertEqual("NEEDS_REVIEW", plan["ux_state"])
        result = rb.apply_start_plan(plan, self.dir)
        self.assertEqual("draft", result["status"])
        with self.assertRaises(GrowError):
            GrowRunV2(Path(result["run_path"])).arm(by="roy")

    def test_incident_json_and_pause_cli(self):
        run = self.launched()
        Ledger(self.dir / "ledger.jsonl").append(
            channel="x", action="reply", target="tweet:https://x.com/a/9",
            result="indeterminate", campaign=f"{RUN}-20261103",
            ts="2026-11-03T09:00:00", ref="ref-ind")
        payload = json.loads(self._mkt("grow", "incident", RUN,
                                       "--list", "--json").stdout)
        incident = [i for i in payload["incidents"]
                    if i["kind"] == "indeterminate_external_action"][0]
        self.assertTrue(incident["event_id"].startswith("INC-"))
        self.assertTrue(incident["recovery"])
        self.assertEqual("INDETERMINATE_EXTERNAL_ACTION",
                         incident["error_code"])
        paused = self._mkt("grow", "pause", RUN, "--by", "roy")
        self.assertIn("已暂停", paused.stdout)
        self.assertEqual("paused", GrowRunV2(self.run_path).load()["status"])


if __name__ == "__main__":
    unittest.main()
