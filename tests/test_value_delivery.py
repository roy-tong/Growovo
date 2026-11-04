"""G2：真实本地复现、显式状态机、授权交付与用户完成读回。"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import value_delivery


ROOT = Path(__file__).resolve().parents[1]


def artifact():
    return {
        "id": "asset-1", "kind": "tutorial", "title": "复现并修复示例服务",
        "scenario": {"user": "开发者", "trigger": "服务启动失败", "task": "按步骤恢复并验证服务"},
        "direct_user_value": "独立完成同一个恢复任务",
        "evidence": [{"summary": "原工单记录了同一失败", "source_ref": "tickets/42"}],
        "reproduction_steps": [{"action": "运行本地 fixture", "expected_result": "输出 repro-ok"}],
        "limitations": ["只覆盖本地 fixture，不代表生产环境"],
        "observation_events": [{"event_type": "task_completed", "source": "product-events",
                                "approximated": False}],
        "platform": "github", "motion": "community_answer", "vertical": "devtool",
    }


def reproduction(code="print('repro-ok')", source_ref="tests/reproduction/run-1"):
    command = [sys.executable, "-c", code]
    return {
        "command": command, "source_ref": source_ref, "expected_output": "repro-ok" if "print" in code else "",
        "authorization": {
            "authorized": True, "authorized_by": "local-tester",
            "authorized_at": "2026-11-04T08:00:00Z",
            "command_sha256": value_delivery.command_fingerprint(command),
        },
    }


def spec():
    return {
        "id": "chain-1",
        "research": {"source_ref": "tickets/42", "subject": "user-1", "problem": "服务启动失败"},
        "solution": {"summary": "用最小 fixture 复现并给出恢复步骤", "steps": ["复现", "修复", "验证"]},
        "reproduction": reproduction(),
        "asset": artifact(),
        "delivery": {
            "authorization": {
                "authorized": True, "authorized_by": "operator", "authorized_at": "2026-11-04T09:00:00Z",
                "asset_id": "asset-1", "subject": "user-1",
            },
            "receipt": {
                "status": "delivered", "source_ref": "receipts/delivery-1",
                "delivered_at": "2026-11-04T10:00:00Z", "subject": "user-1",
            },
        },
        "readback_events": [{
            "event_type": "task_completed", "observed": True, "approximated": False,
            "subject": "user-1", "observed_at": "2026-11-04T11:00:00Z",
            "source_ref": "product-events/completion-1",
            "verification": {"method": "product-db", "ref": "verify-completion-1"},
        }],
    }


class LocalReproductionTests(unittest.TestCase):
    def test_real_exit_code_output_and_source_ref_are_recorded(self):
        record = value_delivery.run_local_reproduction(reproduction())
        self.assertTrue(record["executed"])
        self.assertTrue(record["verified"])
        self.assertEqual(0, record["exit_code"])
        self.assertIn("repro-ok", record["output"])
        self.assertEqual("tests/reproduction/run-1", record["source_ref"])
        self.assertTrue(record["started_at"] and record["finished_at"])

    def test_unauthorized_command_is_never_executed(self):
        request = reproduction()
        request["authorization"]["authorized"] = False
        with mock.patch("core.value_delivery.subprocess.run") as runner:
            with self.assertRaises(value_delivery.ReproductionAuthorizationError):
                value_delivery.run_local_reproduction(request)
        runner.assert_not_called()

    def test_command_or_cwd_change_breaks_authorization_fingerprint(self):
        request = reproduction()
        request["command"] = [sys.executable, "-c", "print('other')"]
        with mock.patch("core.value_delivery.subprocess.run") as runner:
            with self.assertRaises(value_delivery.ReproductionAuthorizationError):
                value_delivery.run_local_reproduction(request)
        runner.assert_not_called()

    def test_nonzero_exit_is_real_but_not_verified(self):
        record = value_delivery.run_local_reproduction(reproduction("import sys; sys.exit(7)"))
        self.assertEqual(7, record["exit_code"])
        self.assertFalse(record["verified"])


class ValueDeliveryStateTests(unittest.TestCase):
    def test_complete_chain_has_ordered_transitions_and_verified_user_completion(self):
        request = spec()
        snapshot = copy.deepcopy(request)
        result = value_delivery.run_chain(request)
        self.assertEqual(snapshot, request)
        self.assertTrue(result["complete"])
        self.assertEqual("completed", result["state"])
        self.assertEqual([
            ("research", "solution"), ("solution", "reproduction"),
            ("reproduction", "asset"), ("asset", "authorized_delivery"),
            ("authorized_delivery", "user_readback"), ("user_readback", "completed"),
        ], [(row["from"], row["to"]) for row in result["transitions"]])
        self.assertEqual("reproduction_verified", result["asset"]["verification"]["status"])
        self.assertFalse(result["delivery"]["user_value_verified"])
        self.assertTrue(result["user_completion"]["verified"])

    def test_missing_or_failed_reproduction_cannot_build_verified_asset(self):
        chain = value_delivery.ValueDeliveryChain("manual")
        chain.record_research(spec()["research"])
        chain.define_solution(spec()["solution"])
        with self.assertRaises(value_delivery.ChainError):
            chain.build_asset(artifact())
        self.assertIsNone(chain.snapshot()["asset"])

        request = spec()
        request["reproduction"] = reproduction("import sys; sys.exit(7)")
        result = value_delivery.run_chain(request)
        self.assertEqual("reproduction_failed", result["state"])
        self.assertEqual(7, result["reproduction"]["exit_code"])
        self.assertIsNone(result["asset"])
        self.assertIsNone(result["delivery"])

    def test_unauthorized_chain_stops_before_subprocess(self):
        request = spec()
        request["reproduction"]["authorization"] = {"authorized": True}
        with mock.patch("core.value_delivery.subprocess.run") as runner:
            result = value_delivery.run_chain(request)
        runner.assert_not_called()
        self.assertEqual("rejected", result["state"])
        self.assertIsNone(result["reproduction"])

    def test_delivery_is_not_use_and_sent_readback_cannot_complete(self):
        request = spec()
        request["readback_events"] = [{
            "event_type": "sent", "observed": True, "subject": "user-1",
            "observed_at": "2026-11-04T11:00:00Z", "source_ref": "receipts/delivery-1",
        }]
        result = value_delivery.run_chain(request)
        self.assertFalse(result["complete"])
        self.assertEqual("user_readback", result["state"])
        self.assertFalse(result["user_completion"]["verified"])
        self.assertTrue(any("不是用户完成" in row["reason"] for row in result["rejected_readbacks"]))

    def test_completion_before_delivery_or_wrong_subject_is_rejected(self):
        request = spec()
        request["readback_events"][0]["observed_at"] = "2026-11-04T09:30:00Z"
        result = value_delivery.run_chain(request)
        self.assertEqual("user_readback", result["state"])
        self.assertTrue(any("早于交付" in row["reason"] for row in result["rejected_readbacks"]))
        request = spec()
        request["readback_events"][0]["subject"] = "someone-else"
        result = value_delivery.run_chain(request)
        self.assertEqual("user_readback", result["state"])
        self.assertTrue(any("主体" in row["reason"] for row in result["rejected_readbacks"]))


class ValueDeliveryCliTests(unittest.TestCase):
    def test_cli_runs_real_local_chain_and_rejects_unauthorized_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chain.json"
            path.write_text(json.dumps(spec()), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "mkt.py"), "value-chain", "run", "--file", str(path), "--json"],
                cwd=ROOT, env=dict(os.environ, MKT_HOME=tmp), text=True, capture_output=True,
            )
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["complete"])
            self.assertEqual(0, payload["reproduction"]["exit_code"])

            unauthorized = spec()
            unauthorized["reproduction"]["authorization"]["command_sha256"] = "bad"
            path.write_text(json.dumps(unauthorized), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "mkt.py"), "value-chain", "run", "--file", str(path), "--json"],
                cwd=ROOT, env=dict(os.environ, MKT_HOME=tmp), text=True, capture_output=True,
            )
            self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
            self.assertEqual("rejected", json.loads(proc.stdout)["state"])


if __name__ == "__main__":
    unittest.main()
