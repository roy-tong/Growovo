"""G-08 冻结：七日校准成功后，第八天读数冲突必须降低「当前读数」可信度。

先于修复编写。修复前 ``calibrate`` 只在账号/解析器口径变化时降级 PROVEN：
第八天人工 34 / 自动 33 会把 streak 归零，但 ``verification`` 仍是 PROVEN，
界面继续把这份读数当已证明准确的数据展示。

冻结的契约：

1. 七个连续日历日一致 → ``verification == "PROVEN"``。
2. 第八天明确冲突 → ``verification`` 变为 ``CONFLICTED``，
   ``requalify_required`` 为真，``due`` 非空，``streak_days == 0``；
   历史 ``promoted_at`` 保留（曾经通过过这件事不能被抹掉）。
3. 状态必须出现在 ``grow status`` 的投影里，且不能再对外显示为 PROVEN。
4. 再补七个连续有效日 → 回到 ``PROVEN``，``requalify_required`` 归位。
"""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.execution_policy import authorize
from core.grow_v2 import GrowRunV2

ACTIONS = ["x:reply", "email:message"]


def _due_now(day: str = "2026-11-03"):
    return datetime.fromisoformat(f"{day}T16:00:00").replace(tzinfo=timezone.utc)


def _facts(tmp: Path) -> Path:
    facts = {
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
    path.write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
    return path


class RequalificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        authorize(self.dir, by="tester", actions=ACTIONS, days=90)
        self.path = self.dir / "grow" / "g1.json"
        GrowRunV2.launch(self.path, run_id="g1",
                         facts_path=str(_facts(self.dir)),
                         goal_id="awareness.followers",
                         channels=["x", "email"], target=20,
                         actions_per_week=7, started="2026-11-01",
                         handle="acct-a", data_dir=self.dir, skip_probe=True)
        self.run = GrowRunV2(self.path)

    def _set_auto(self, day, followers):
        data = self.run.load()
        rec = (data.setdefault("telemetry", {}).setdefault("days", {})
               .setdefault(day, {}))
        rec.setdefault("readings", {})["followers"] = followers
        self.run.save(data)

    def _calibrate(self, day, followers, match=True):
        self._set_auto(day, followers)
        return self.run.calibrate(on=day, followers=followers, match=match,
                                  by="roy", note="人工对数")

    def _measurement(self):
        return self.run.load()["measurement"]

    def _seven_good_days(self, start=1, followers=120):
        for offset in range(7):
            day = f"2026-11-{start + offset:02d}"
            self._calibrate(day, followers)

    def test_conflict_after_proven_lowers_current_readings(self):
        self._seven_good_days()
        m = self._measurement()
        self.assertEqual("PROVEN", m["verification"])
        promoted_at = m["calibration"].get("promoted_at")
        self.assertTrue(promoted_at)

        self._calibrate("2026-11-08", 34, match=False)  # 第八天明确冲突

        m = self._measurement()
        self.assertEqual("CONFLICTED", m["verification"])
        self.assertTrue(m["calibration"].get("requalify_required"))
        self.assertTrue(m["calibration"].get("due"))
        self.assertEqual(0, m["calibration"].get("streak_days"))
        self.assertEqual(promoted_at, m["calibration"].get("promoted_at"),
                         "曾经通过七日校准的事实不能被抹掉")

    def test_status_projection_does_not_show_proven(self):
        self._seven_good_days()
        self._calibrate("2026-11-08", 34, match=False)
        status = self.run.status(self.dir)
        blob = json.dumps(status, ensure_ascii=False)
        self.assertNotIn('"verification": "PROVEN"', blob)
        self.assertIn("CONFLICTED", blob)
        self.assertIn("重新校准", blob)

    def test_seven_more_good_days_restore_proven(self):
        self._seven_good_days()
        self._calibrate("2026-11-08", 34, match=False)
        self._seven_good_days(start=9)
        m = self._measurement()
        self.assertEqual("PROVEN", m["verification"])
        self.assertFalse(m["calibration"].get("requalify_required"))

    def test_requalification_is_not_a_dead_end(self):
        self._seven_good_days()
        self._calibrate("2026-11-08", 34, match=False)
        for offset in range(6):
            self._calibrate(f"2026-11-{9 + offset:02d}", 120)
        self.assertEqual("CONFLICTED", self._measurement()["verification"])
        self._calibrate("2026-11-15", 120)
        self.assertEqual("PROVEN", self._measurement()["verification"])


if __name__ == "__main__":
    unittest.main()
