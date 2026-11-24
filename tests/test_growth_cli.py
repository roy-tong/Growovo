import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.test_growth_goals import goal


ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"


class GrowthCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.goal_path = self.data / "goal.json"
        self.goal_path.write_text(json.dumps(goal()), encoding="utf-8")
        self.env = dict(os.environ, MKT_HOME=str(self.data))

    def run_growth(self, *args):
        proc = subprocess.run(
            [sys.executable, str(MKT), "growth", *args], cwd=ROOT, env=self.env,
            text=True, capture_output=True)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def test_compile_start_next_transition_evaluate_and_status(self):
        plan = self.run_growth("compile", "--file", str(self.goal_path))
        self.assertEqual("growovo.business-action-plan/1", plan["schema"])
        service = self.run_growth("start", "--file", str(self.goal_path))
        sid = service["id"]
        next_action = self.run_growth("next", sid)
        self.assertEqual("diagnose-product", next_action["id"])
        updated = self.run_growth(
            "transition", sid, "--action", "diagnose-product", "--status", "completed",
            "--by", "tester", "--minutes", "5")
        self.assertEqual(5, updated["metrics"]["human_minutes_total"])
        next_action = self.run_growth("next", sid)
        self.assertEqual("baseline-observe", next_action["id"])
        metrics = self.data / "metrics.json"
        metrics.write_text(json.dumps({
            "baseline": {"github_stars": 221, "x_followers": 38},
            "final": {"activated_users": 1, "github_stars": 224, "x_followers": 41},
        }), encoding="utf-8")
        evaluation = self.run_growth("evaluate", sid, "--metrics", str(metrics), "--by", "tester")
        self.assertTrue(evaluation["primary_success"])
        status = self.run_growth("status", sid, "--json")
        self.assertEqual(sid, status["id"])
        self.assertEqual(1, len(status["evaluations"]))

    def test_resolve_returns_manual_work_package_instead_of_dry_run(self):
        service = self.run_growth("start", "--file", str(self.goal_path))
        got = self.run_growth("resolve", service["id"], "--action", "x-publish")
        self.assertEqual("manual_handoff", got["execution_mode"])
        self.assertNotEqual("dry-run", got["execution_mode"])

    def test_next_prefers_existing_user_inbox_over_regular_plan(self):
        service = self.run_growth("start", "--file", str(self.goal_path))
        interactions = self.data / "interactions.json"
        interactions.write_text(json.dumps([{
            "source": "github", "external_id": "real-question", "author": "external-user",
            "body": "Where is the demo output?", "received_at": "2026-11-23T22:00:00+08:00",
            "existing_user": True,
        }]), encoding="utf-8")
        self.run_growth("inbox-import", service["id"], "--file", str(interactions))
        got = self.run_growth("next", service["id"])
        self.assertEqual("conv-github-real-question", got["conversation_id"])
        self.assertEqual("draft", got["next_step"])

    def test_plan_and_brief_expose_human_budget_and_next_action(self):
        service = self.run_growth("start", "--file", str(self.goal_path))
        for action_id in ("diagnose-product", "baseline-observe", "asset-prepare"):
            self.run_growth("transition", service["id"], "--action", action_id,
                            "--status", "completed", "--by", "tester")
        plan = self.run_growth("plan", service["id"], "--minutes", "0")
        self.assertTrue(any(row["id"] == "x-publish" for row in plan["deferred"]))
        brief = self.run_growth("brief", service["id"])
        self.assertEqual(service["id"], brief["service_id"])
        self.assertIn("human_interruption_cost", brief)

    def test_run_command_executes_local_diagnosis(self):
        service = self.run_growth("start", "--file", str(self.goal_path))
        got = self.run_growth(
            "run", service["id"], "--action", "diagnose-product", "--by", "tester")
        self.assertEqual("completed", got["status"])
        status = self.run_growth("status", service["id"])
        action = next(row for row in status["actions"] if row["id"] == "diagnose-product")
        self.assertEqual("completed", action["status"])

    def test_attempts_and_recover_share_the_same_persistent_runtime(self):
        service = self.run_growth("start", "--file", str(self.goal_path))
        ran = self.run_growth("run", service["id"], "--action", "diagnose-product", "--by", "tester")
        attempts = self.run_growth("attempts", service["id"])
        self.assertEqual(ran["attempt_id"], attempts["attempts"][0]["id"])
        self.assertEqual("completed", attempts["attempts"][0]["status"])
        recovered = self.run_growth("recover")
        self.assertEqual(0, recovered["recovered"])

    def test_capability_registry_is_shared_by_cli_calls(self):
        from tests.test_execution_capabilities import capability
        path = self.data / "capability.json"
        path.write_text(json.dumps(capability(verified=["read", "write"])), encoding="utf-8")
        saved = self.run_growth("capability-upsert", "--file", str(path), "--by", "tester")
        self.assertFalse(saved["executable"])
        rows = self.run_growth("capabilities", "--account", "default", "--platform", "github")
        self.assertEqual(1, len(rows["capabilities"]))
        self.assertIn("verify_not_verified", rows["capabilities"][0]["missing"])

    def test_live_preflight_exposes_x_to_agents(self):
        from core import growth_cli
        args = SimpleNamespace(growth_action="live-preflight", repo="", gmail=False,
                               x=True, x_action="publish")
        with mock.patch("core.growth_cli.live_acceptance.preflight",
                        return_value={"ok": True, "components": {}, "writes": 0}) as preflight:
            rc = growth_cli.handle(args, self.data)
        self.assertEqual(0, rc)
        self.assertTrue(preflight.call_args.kwargs["x"])
        self.assertEqual("publish", preflight.call_args.kwargs["x_action"])

    def test_operator_once_and_worker_status_share_heartbeat(self):
        ran = self.run_growth("operator-serve", "svc-test", "--once")
        self.assertTrue(ran["components"]["recovery"]["ok"])
        status = self.run_growth("worker-status", "svc-test")
        self.assertIn("last_run_at", status)
        self.assertTrue(status["components"]["recovery"]["ok"])

    def test_bounded_continuous_worker_emits_and_persists_every_cycle(self):
        proc = subprocess.run([
            sys.executable, str(MKT), "growth", "operator-serve", "svc-test",
            "--max-cycles", "3", "--interval-seconds", "1",
        ], cwd=ROOT, env=self.env, text=True, capture_output=True)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        decoder = json.JSONDecoder()
        text = proc.stdout
        rows = []
        while text.strip():
            text = text.lstrip()
            row, offset = decoder.raw_decode(text)
            rows.append(row)
            text = text[offset:]
        self.assertEqual([1, 2, 3], [row["cycle"] for row in rows])
        history = self.run_growth("worker-history", "svc-test", "--limit", "10")
        self.assertEqual(3, history["count"])
        self.assertEqual([row["cycle_id"] for row in rows], [row["cycle_id"] for row in history["cycles"]])

    def test_asset_create_and_repurpose_commands(self):
        service = self.run_growth("start", "--file", str(self.goal_path))
        request = self.data / "asset.json"
        request.write_text(json.dumps({
            "id": "demo", "title": "Replay the result", "scenario": "Agent says done",
            "audience": "developers", "value": "Inspect a receipt",
            "cta": {"kind": "run_demo", "url": "https://example.invalid/demo"},
            "steps": ["Run the command"], "expected_output": "A result card",
            "limitations": ["Synthetic example"],
            "reproduction": {"command": [sys.executable, "-c", "print('ok')"],
                             "authorized": True, "by": "tester"},
        }), encoding="utf-8")
        asset = self.run_growth("asset-create", service["id"], "--file", str(request))
        self.assertTrue(asset["reproduced"])
        variants = self.run_growth(
            "asset-repurpose", service["id"], "--asset", "demo", "--channels", "x,github")
        self.assertEqual({"x", "github"}, set(variants))


if __name__ == "__main__":
    unittest.main()
