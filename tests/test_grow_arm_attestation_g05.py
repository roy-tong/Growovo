"""D4 验收冻结：`grow arm` 必须是人署名、有授权、无阻塞告警的显式动作。

C-01 与 PRD G-05 要求 `grow_arm` 走独立人工授权通道。本文件冻结：

- 匿名 arm 一律拒绝（AUTH_REQUIRED），不改 go；
- policy 缺失/过期/撤销时拒绝（POLICY_DENIED）；
- 有 blocking 告警（未裁决 indeterminate 等）时拒绝，并给出事件 ID；
- 正常路径落到 active/go=true，并留 `armed_by` / `armed_at` /
  `armed_policy_sha256` 与 history；
- 重复 arm 幂等，但不丢第一次的署名。

零网络：合成 facts、台账与注入探测器。
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import grow_runbook as rb
from core.execution_policy import authorize, policy_path
from core.grow_v2 import GrowError, GrowRunV2
from core.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"
RUN = "g5"


def facts(tmp: Path) -> Path:
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
    path = tmp / "facts.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def logged_in(channel):
    return {"channel": channel, "status": "logged_in",
            "checked_at": "2026-11-15T09:00:00"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.facts = facts(self.dir)
        self.run_path = self.dir / "grow" / f"{RUN}.json"

    def ready(self):
        """用 G-05 向导建一个 ready_to_arm 的 run（不 arm）。"""
        authorize(self.dir, by="tester", actions=["x:reply"], days=90)
        plan = rb.build_start_plan(
            self.dir, facts_path=str(self.facts),
            goal_id="awareness.followers", run_id=RUN, channels=("x",),
            actions=("reply",), handle="verovo", login_probe=logged_in)
        result = rb.apply_start_plan(plan, self.dir)
        self.assertEqual("ready_to_arm", result["status"])
        return GrowRunV2(Path(result["run_path"]))

    def _mkt(self, *args, check=True):
        env = dict(os.environ, MKT_HOME=str(self.dir))
        proc = subprocess.run([sys.executable, str(MKT), *args],
                              cwd=str(ROOT), env=env, text=True,
                              capture_output=True)
        if check and proc.returncode:
            self.fail(f"mkt {' '.join(args)} failed ({proc.returncode})"
                      f"\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
        return proc


class SignatureTests(Base):
    def test_anonymous_arm_is_refused(self):
        run = self.ready()
        with self.assertRaises(GrowError) as ctx:
            run.arm()
        self.assertIn("AUTH_REQUIRED", str(ctx.exception))
        self.assertFalse(run.load()["go"])
        self.assertEqual("ready_to_arm", run.load()["status"])

    def test_blank_signature_is_refused(self):
        run = self.ready()
        with self.assertRaises(GrowError):
            run.arm(by="   ")
        self.assertFalse(run.load()["go"])

    def test_cli_anonymous_arm_is_refused(self):
        self.ready()
        proc = self._mkt("grow", "arm", RUN, check=False)
        self.assertEqual(2, proc.returncode)
        self.assertIn("AUTH_REQUIRED", proc.stdout + proc.stderr)
        self.assertFalse(GrowRunV2(self.run_path).load()["go"])


class PolicyTests(Base):
    def test_arm_refuses_when_policy_is_missing(self):
        run = self.ready()
        policy_path(self.dir).unlink()
        with self.assertRaises(GrowError) as ctx:
            run.arm(by="roy")
        self.assertIn("POLICY_DENIED", str(ctx.exception))
        self.assertFalse(run.load()["go"])

    def test_arm_refuses_expired_policy(self):
        run = self.ready()
        path = policy_path(self.dir)
        policy = json.loads(path.read_text(encoding="utf-8"))
        policy["expires_at"] = "2020-01-01T00:00:00"
        path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(GrowError) as ctx:
            run.arm(by="roy")
        self.assertIn("POLICY_DENIED", str(ctx.exception))
        self.assertFalse(run.load()["go"])

    def test_arm_refuses_revoked_policy(self):
        run = self.ready()
        from core import execution_policy
        execution_policy.revoke(self.dir, by="roy")
        with self.assertRaises(GrowError):
            run.arm(by="roy")
        self.assertFalse(run.load()["go"])


class BlockingIncidentTests(Base):
    def test_arm_refuses_unresolved_indeterminate(self):
        run = self.ready()
        Ledger(self.dir / "ledger.jsonl").append(
            channel="x", action="reply", target="tweet:https://x.com/a/9",
            result="indeterminate", campaign=f"{RUN}-20261103",
            ts="2026-11-03T09:00:00", ref="ref-ind")
        with self.assertRaises(rb.RunbookError) as ctx:
            rb.arm_run(self.dir, self.run_path, by="roy")
        self.assertEqual("INDETERMINATE_EXTERNAL_ACTION", ctx.exception.code)
        self.assertIn("INC-", str(ctx.exception))
        self.assertFalse(run.load()["go"])

    def test_cli_arm_refuses_blocking_incident(self):
        self.ready()
        Ledger(self.dir / "ledger.jsonl").append(
            channel="x", action="reply", target="tweet:https://x.com/a/9",
            result="indeterminate", campaign=f"{RUN}-20261103",
            ts="2026-11-03T09:00:00", ref="ref-ind")
        proc = self._mkt("grow", "arm", RUN, "--by", "roy", check=False)
        self.assertEqual(2, proc.returncode)
        self.assertIn("INC-", proc.stdout)
        self.assertFalse(GrowRunV2(self.run_path).load()["go"])


class AttestationTests(Base):
    def test_arm_records_operator_and_policy_hash(self):
        run = self.ready()
        expected = hashlib.sha256(
            policy_path(self.dir).read_bytes()).hexdigest()
        data = rb.arm_run(self.dir, self.run_path, by="roy")
        self.assertEqual("active", data["status"])
        self.assertTrue(data["go"])
        self.assertEqual("roy", data["armed_by"])
        self.assertTrue(data["armed_at"])
        self.assertEqual(expected, data["armed_policy_sha256"])
        history = [h for h in data["history"] if h.get("kind") == "arm"]
        self.assertEqual("roy", history[-1]["by"])
        self.assertEqual(expected, history[-1]["policy_sha256"])

    def test_second_arm_is_idempotent_and_keeps_first_signature(self):
        self.ready()
        first = rb.arm_run(self.dir, self.run_path, by="roy")
        second = rb.arm_run(self.dir, self.run_path, by="ann")
        self.assertTrue(second["go"])
        self.assertEqual(first["armed_by"], second["armed_by"])
        self.assertEqual(first["armed_at"], second["armed_at"])

    def test_arm_does_not_run_queues_or_send(self):
        self.ready()
        rb.arm_run(self.dir, self.run_path, by="roy")
        self.assertFalse((self.dir / "queues").exists())
        self.assertFalse((self.dir / "ledger.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
