"""终验必修：库入口的 incident 门禁必须默认生效，不能靠调用方记得传函数。

`GrowRunV2.arm(by=...)` / `GrowRunV2.resume(...)` 是公共入口。CLI 已经走
runbook 门禁，但直接调库不传 `blocking_check` 时也必须拦住未裁决的
indeterminate / blocking 告警；否则“所有入口共用门禁”只是一句注释。

零网络：合成 facts、账本与注入探测器。
"""
import json
import tempfile
import unittest
from pathlib import Path

from core.execution_policy import authorize
from core.grow_v2 import GrowError, GrowRunV2
from core.ledger import Ledger

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


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "grow" / f"{RUN}.json"
        authorize(self.dir, by="tester", actions=["x:reply"], days=90)
        GrowRunV2.launch(self.path, run_id=RUN, facts_path=str(facts(self.dir)),
                         goal_id="awareness.followers", channels=["x"],
                         target=20, actions_per_week=7, started="2026-11-01",
                         handle="verovo", data_dir=self.dir, skip_probe=True)
        self.run = GrowRunV2(self.path)
        self.run.arm(by="roy")  # 先正常开闸，再制造需要恢复的状态

    def seed_indeterminate(self):
        Ledger(self.dir / "ledger.jsonl").append(
            channel="x", action="reply", target="tweet:https://x.com/a/9",
            result="indeterminate", campaign=f"{RUN}-20261103",
            ts="2026-11-03T09:00:00", ref="ref-ind")

    def resolve(self):
        Ledger(self.dir / "ledger.jsonl").resolve(
            "ref-ind", result="committed", by="roy", note="平台后台已看到")


class ArmDefaultGateTests(Base):
    def test_direct_arm_without_check_refuses_indeterminate(self):
        self.seed_indeterminate()
        with self.assertRaises(GrowError) as ctx:
            self.run.arm(by="roy")  # 不传 blocking_check
        self.assertIn("INDETERMINATE_EXTERNAL_ACTION", str(ctx.exception))
        self.assertIn("INC-", str(ctx.exception))
        self.assertTrue(self.run.load()["go"])  # 已经是开闸态，不因失败而关闸

    def test_direct_arm_after_resolution_succeeds(self):
        self.seed_indeterminate()
        self.resolve()
        data = self.run.arm(by="roy")
        self.assertTrue(data["go"])
        self.assertEqual("roy", data["armed_by"])


class ResumeDefaultGateTests(Base):
    def _needs_attention(self):
        data = self.run.load()
        data["status"] = "needs_attention"
        data["go"] = True
        self.run.save(data)

    def test_direct_resume_without_check_refuses_indeterminate(self):
        self.seed_indeterminate()
        self._needs_attention()
        with self.assertRaises(GrowError) as ctx:
            self.run.resume(note="处理好了", by="roy")  # 不传 blocking_check
        self.assertIn("INDETERMINATE_EXTERNAL_ACTION", str(ctx.exception))
        self.assertEqual("needs_attention", self.run.load()["status"])

    def test_direct_resume_after_resolution_succeeds(self):
        self.seed_indeterminate()
        self._needs_attention()
        self.resolve()
        data = self.run.resume(note="平台后台已看到", by="roy")
        self.assertEqual("active", data["status"])

    def test_direct_resume_with_warning_only_succeeds(self):
        self._needs_attention()
        data = self.run.resume(note="只有读数警告", by="roy")
        self.assertEqual("active", data["status"])


if __name__ == "__main__":
    unittest.main()
