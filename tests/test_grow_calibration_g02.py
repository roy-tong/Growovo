"""G-02 反例冻结：读数校准必须按「连续 7 个日历日、每日一条有效对数」计。

有效对数 = 当日自动与人工读数均为非负整数且严格相等、署名非空、同一账号（handle）。
同日重复提交只追加审计记录，不重复计日；缺数日或不一致日中断连续；账号变化降级 UNVERIFIED。
本文件先于修复编写，针对「按条数累计」的旧实现应当失败（工单 G-02，P0）。
"""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from core.execution_policy import authorize
from core.grow_v2 import GrowError, GrowRunV2

ACTIONS = ["x:reply", "email:message"]


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


class CalibrationG02Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.facts = _facts(self.dir)
        self.path = self.dir / "grow" / "g1.json"
        authorize(self.dir, by="tester", actions=ACTIONS, days=90)
        GrowRunV2.launch(self.path, run_id="g1", facts_path=str(self.facts),
                         goal_id="awareness.followers", channels=["x"],
                         target=20, actions_per_week=7, started="2026-11-01",
                         handle="acct-a", data_dir=self.dir, skip_probe=True)
        self.run = GrowRunV2(self.path)
        self.run.arm(by="roy")

    # ---- 测试辅助 -------------------------------------------------

    def _set_auto(self, day: str, followers):
        data = self.run.load()
        rec = (data.setdefault("telemetry", {}).setdefault("days", {})
               .setdefault(day, {}))
        rec.setdefault("readings", {})["followers"] = followers
        self.run.save(data)

    def _cal(self, day: str, followers, by: str = "tester", **kw):
        return self.run.calibrate(followers=followers, by=by, on=day, **kw)

    # ---- 反例（修复前必须失败） ------------------------------------

    def test_same_day_seven_submissions_count_one_day(self):
        """同日重复提交七次只算一个有效日，不得升 PROVEN。"""
        self._set_auto("2026-11-01", 121)
        for _ in range(7):
            result = self._cal("2026-11-01", 121)
        self.assertEqual(1, result["streak_days"])
        self.assertFalse(result["promoted"])
        self.assertNotEqual("PROVEN", result["verification"])

    def test_missing_human_reading_is_invalid(self):
        """缺人工读数（--followers 空）不得计为有效对数。"""
        self._set_auto("2026-11-01", 121)
        result = self.run.calibrate(by="tester", on="2026-11-01")
        self.assertFalse(result["valid_day"])
        self.assertIn("missing_human_reading", result["invalid_reasons"])
        self.assertEqual(0, result["streak_days"])

    def test_missing_auto_reading_is_invalid(self):
        """当日没有自动读数（没 tick/没 handle）不得计为有效对数。"""
        result = self._cal("2026-11-02", 121)
        self.assertFalse(result["valid_day"])
        self.assertIn("missing_auto_reading", result["invalid_reasons"])

    def test_missing_reviewer_is_invalid(self):
        """署名缺失（--by 空）不得计为有效对数。"""
        self._set_auto("2026-11-01", 121)
        result = self.run.calibrate(followers=121, by="", on="2026-11-01")
        self.assertIn("missing_reviewer", result["invalid_reasons"])
        self.assertEqual(0, result["streak_days"])

    def test_value_mismatch_breaks_streak(self):
        """人工 34 对自动 33 不得算一致；不一致日中断连续。"""
        self._set_auto("2026-11-01", 121)
        self._set_auto("2026-11-02", 122)
        self._cal("2026-11-01", 121)
        result = self._cal("2026-11-02", 121)  # 自动读数 122
        self.assertFalse(result["match"])
        self.assertFalse(result["valid_day"])
        # 不一致日中断连续：连续段锚定本次提交日，当日无效即归零
        self.assertEqual(0, result["streak_days"])

    def test_gap_day_resets_streak(self):
        """缺一个日历日即中断：1,2,[3缺],4,5,6,7,8 → 只剩 5 个连续日。"""
        result = None
        for d, v in [(1, 121), (2, 122), (4, 124), (5, 125), (6, 126),
                     (7, 127), (8, 128)]:
            day = f"2026-11-{d:02d}"
            self._set_auto(day, v)
            result = self._cal(day, v)
        self.assertEqual(5, result["streak_days"])
        self.assertFalse(result["promoted"])

    def test_future_date_rejected(self):
        """--on 不能伪造未来日期。"""
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        with self.assertRaises(GrowError):
            self.run.calibrate(followers=121, by="tester", on=tomorrow)

    def test_account_change_downgrades_to_unverified(self):
        """handle 变化（账号切换）后旧 PROVEN 不延续，重新累计。"""
        for d in range(1, 8):
            day = f"2026-11-{d:02d}"
            v = 120 + d
            self._set_auto(day, v)
            self._cal(day, v)
        self.assertEqual("PROVEN",
                         self.run.load()["measurement"]["verification"])
        data = self.run.load()
        data["measurement"]["readers"][0]["handle"] = "acct-b"
        self.run.save(data)
        self._set_auto("2026-11-08", 128)
        result = self._cal("2026-11-08", 128)
        self.assertEqual("UNVERIFIED", result["verification"])
        self.assertEqual(1, result["streak_days"])

    def test_same_day_conflict_invalidates_day(self):
        """同日先一致后 --mismatch：冲突不能被最后一次提交抹掉。"""
        self._set_auto("2026-11-01", 121)
        self._cal("2026-11-01", 121)
        result = self.run.calibrate(followers=121, match=False,
                                    by="tester", on="2026-11-01")
        self.assertFalse(result["valid_day"])
        self.assertEqual(0, result["streak_days"])

    # ---- 正例（新契约下的合法升级路径） ----------------------------

    def test_seven_consecutive_valid_days_promote_with_scope(self):
        """连续 7 个日历日、每日一条有效对数 → PROVEN，且资格限定在该指标/账号。"""
        result = None
        for d in range(1, 8):
            day = f"2026-11-{d:02d}"
            v = 120 + d
            self._set_auto(day, v)
            result = self._cal(day, v)
        self.assertTrue(result["promoted"])
        self.assertEqual("PROVEN", result["verification"])
        self.assertEqual(7, result["streak_days"])
        self.assertEqual("followers", result["scope"]["metric"])
        self.assertEqual("acct-a", result["scope"]["handle"])
        self.assertEqual("2026-11-07", result["last_valid_date"])

    # ---- 复审反例（第二评审者复现，2026-11-15 追加冻结） -----------

    def test_invalid_day_after_long_gap_reports_zero_streak(self):
        """复审 S2：搁置多月后一条无效对数，不得沿用旧连续段报 7。"""
        for d in range(1, 8):
            day = f"2026-10-{d:02d}"
            v = 110 + d
            self._set_auto(day, v)
            self._cal(day, v)
        self.assertEqual(
            "PROVEN", self.run.load()["measurement"]["verification"])
        self._set_auto("2026-11-12", 999)
        result = self._cal("2026-11-12", 111)  # 读数不等 → 当日无效
        self.assertEqual(0, result["streak_days"])
        self.assertFalse(result["promoted"])

    def test_downgrade_without_scope_evidence_restarts_counting(self):
        """复审 S1：PROVEN 被外部重置且 scope 凭据缺失（迁移场景），
        下一次有效对数必须从 1 重新累计，不得凭旧连续段直接复活。"""
        for d in range(1, 8):
            day = f"2026-11-{d:02d}"
            v = 120 + d
            self._set_auto(day, v)
            self._cal(day, v)
        data = self.run.load()
        data["measurement"]["verification"] = "UNVERIFIED"
        del data["measurement"]["calibration"]["scope"]
        self.run.save(data)
        self._set_auto("2026-11-08", 128)
        result = self._cal("2026-11-08", 128)
        self.assertEqual("UNVERIFIED", result["verification"])
        self.assertEqual(1, result["streak_days"])
        self.assertFalse(result["promoted"])


if __name__ == "__main__":
    unittest.main()
