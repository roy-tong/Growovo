"""周结果单 v2：业务事件、护栏与供给、下周动作——只增不改的扩展面。

守住三件事：
  1. 新增键有数据就带上，没数据给契约缺省（0/None/{}），不造数；
  2. render 只追加三节，原三件套表格与边界声明一字不改；
  3. 业务漏斗按 run id 精确归集、比值口径不证明因果。
"""
import tempfile
import unittest
from pathlib import Path

from core import result_card
from core.attribution import OutcomeLedger
from core.ledger import Ledger

WEEK_START = "2026-11-01"
WEEK_END = "2026-11-07"


def make_run(**overrides):
    """一份最小 run（data/grow/<id>.json 的形状）。"""
    run = {
        "id": "g1",
        "started": "2026-11-01",
        "goal": {"metric": "followers", "name": "社媒关注者增长",
                 "baseline": 120, "target": 320},
        "plan": {"actions_per_week": 20},
        "adjustments": [],
    }
    run.update(overrides)
    return run


class TmpDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def build(self, run):
        return result_card.build(self.dir, run, WEEK_START, WEEK_END)


class BusinessEventsTests(TmpDir):
    def _seed_events(self):
        ol = OutcomeLedger(self.dir)
        for subject in ("u1", "u2"):
            ol.add_event(subject=subject, event_type="signal", channel="x",
                         campaign="g1", ts="2026-11-02T10:00:00")
        ol.add_event(subject="u1", event_type="signup", channel="x",
                     approximated=True, campaign="g1",
                     ts="2026-11-03T10:00:00")
        ol.add_event(subject="u1", event_type="activation", channel="x",
                     source_ref="app:activation:u1",
                     verification={"method": "app_db", "ref": "row:42"},
                     campaign="g1", ts="2026-11-04T10:00:00")
        ol.add_event(subject="u2", event_type="paid", channel="x",
                     source_ref="stripe:pi_1",
                     verification={"method": "stripe_api", "ref": "pi_1"},
                     campaign="g1", ts="2026-11-05T10:00:00")

    def test_funnel_counts_this_runs_events(self):
        self._seed_events()
        card = self.build(make_run())
        business = card["business"]
        self.assertTrue(business["available"])
        self.assertEqual({"signal": 2, "signup": 1, "activation": 1, "paid": 1},
                         business["funnel"])
        self.assertIn("因果", business["note"])
        text = result_card.render(card)
        self.assertIn("## 业务事件", text)
        self.assertIn("| signal（信号/访问） | 2 |", text)
        self.assertIn("| paid（付费） | 1 |", text)
        self.assertIn("比值口径，不证明因果", text)

    def test_other_campaign_events_do_not_leak_in(self):
        # funnel(campaign=run id) 是精确归集：g1-w1 / 无 campaign 都不算 g1 的。
        self._seed_events()
        ol = OutcomeLedger(self.dir)
        ol.add_event(subject="noise", event_type="signal", campaign="g1-w1")
        ol.add_event(subject="anon", event_type="paid", source_ref="x:y",
                     verification={"method": "m", "ref": "r"})
        card = self.build(make_run())
        self.assertEqual(2, card["business"]["funnel"]["signal"])
        self.assertEqual(1, card["business"]["funnel"]["paid"])

    def test_no_events_is_unavailable_not_zero(self):
        card = self.build(make_run())
        business = card["business"]
        self.assertFalse(business["available"])
        self.assertEqual({"signal": 0, "signup": 0, "activation": 0, "paid": 0},
                         business["funnel"])
        self.assertIn("取不到", business["note"])
        text = result_card.render(card)
        self.assertIn("## 业务事件", text)
        self.assertIn("取不到（未接入数据源）", text)
        self.assertNotIn("比值口径，不证明因果", text)


class GuardrailsTests(TmpDir):
    def test_week_row_is_read(self):
        telemetry = {"weeks": {WEEK_START: {
            "failed": 1, "indeterminate": 2, "duplicates": 3,
            "suppressed": 4, "readings_days": 6, "account_health": 92}}}
        card = self.build(make_run(telemetry=telemetry))
        self.assertEqual({"failed": 1, "indeterminate": 2, "duplicates": 3,
                          "suppressed": 4, "readings_days": 6,
                          "account_health": 92}, card["guardrails"])
        text = result_card.render(card)
        self.assertIn("## 护栏与供给", text)
        self.assertIn(
            "- 失败 1｜未知 2｜重复拦截 3｜抑制跳过 4｜读数 6/7 天｜账号健康 92",
            text)

    def test_missing_telemetry_defaults_to_zero_and_none(self):
        card = self.build(make_run())
        self.assertEqual({"failed": 0, "indeterminate": 0, "duplicates": 0,
                          "suppressed": 0, "readings_days": 0,
                          "account_health": None}, card["guardrails"])
        self.assertEqual({}, card["supply"])
        # 全缺省等于「没读数」，不渲染一节假的 0。
        self.assertNotIn("## 护栏与供给", result_card.render(card))

    def test_other_weeks_are_not_borrowed(self):
        telemetry = {"weeks": {"2026-11-08": {"failed": 5, "duplicates": 5}}}
        card = self.build(make_run(telemetry=telemetry))
        self.assertEqual(0, card["guardrails"]["failed"])
        self.assertEqual(0, card["guardrails"]["duplicates"])

    def test_unknown_account_health_reads_unavailable(self):
        telemetry = {"weeks": {WEEK_START: {"duplicates": 4}}}
        card = self.build(make_run(telemetry=telemetry))
        self.assertIsNone(card["guardrails"]["account_health"])
        text = result_card.render(card)
        self.assertIn("重复拦截 4", text)
        self.assertIn("账号健康 取不到", text)


class SupplyTests(TmpDir):
    def test_supply_snapshot_and_gap_line(self):
        supply = {"staged": 14, "upcoming": 14, "gap": 11,
                  "coverage_days": 2, "daily_quota": 3, "horizon_days": 3,
                  "note": "未来 3 天缺 11 条（日配额 3）"}
        card = self.build(make_run(supply_state=supply))
        self.assertEqual(supply, card["supply"])
        text = result_card.render(card)
        self.assertIn("## 护栏与供给", text)
        self.assertIn("- 下周已暂存 14/25（缺口 11）", text)

    def test_supply_explicit_target_wins(self):
        supply = {"staged": 3, "target": 20, "gap": 17}
        card = self.build(make_run(supply_state=supply))
        self.assertIn("- 下周已暂存 3/20（缺口 17）", result_card.render(card))

    def test_gap_missing_reads_unavailable_not_zero(self):
        card = self.build(make_run(supply_state={"staged": 5}))
        text = result_card.render(card)
        self.assertIn("## 护栏与供给", text)
        self.assertIn("下周已暂存 5（缺口取不到）", text)
        self.assertNotIn("缺口 0", text)

    def test_empty_supply_does_not_render_line(self):
        card = self.build(make_run(supply_state={}))
        self.assertEqual({}, card["supply"])
        self.assertNotIn("下周已暂存", result_card.render(card))


class NextWeekTests(TmpDir):
    def test_changes_come_from_this_weeks_adjustments(self):
        adjustments = [
            {"on": "2026-11-06", "kind": "auto", "param": "actions_per_week",
             "from": 20, "to": 25,
             "detail": "第 1 周完成 18/20（≥90%），下周计划量 20 → 25"},
            {"on": "2026-11-10", "kind": "auto", "detail": "下周的事不算本周"},
        ]
        card = self.build(make_run(adjustments=adjustments))
        self.assertEqual(20, card["next_week"]["actions_per_week"])
        self.assertEqual([{"detail": "第 1 周完成 18/20（≥90%），"
                                    "下周计划量 20 → 25"}],
                         card["next_week"]["changes"])
        text = result_card.render(card)
        self.assertIn("## 下周动作", text)
        self.assertIn("- 第 1 周完成 18/20（≥90%），下周计划量 20 → 25", text)
        self.assertNotIn("下周的事不算本周", text)

    def test_no_adjustments_means_no_next_week_section(self):
        card = self.build(make_run())
        self.assertEqual([], card["next_week"]["changes"])
        self.assertEqual(20, card["next_week"]["actions_per_week"])
        self.assertNotIn("## 下周动作", result_card.render(card))

    def test_missing_plan_reads_none(self):
        run = make_run()
        run.pop("plan")
        card = self.build(run)
        self.assertIsNone(card["next_week"]["actions_per_week"])
        self.assertEqual(0, card["planned_actions"])  # 现有字段口径不变


class CompatibilityTests(TmpDir):
    """v2 只加键、只追加节：旧三件套与边界声明必须逐字不变。"""

    def _seed(self):
        ledger = Ledger(self.dir / "ledger.jsonl")
        for i in range(12):
            ledger.append(channel="x", action="reply",
                          target=f"tweet:https://x.com/a/{i}",
                          result="committed", campaign="g1-w1",
                          ts="2026-11-03T10:00:00")
        ledger.append(channel="email", action="message",
                      target="b@example.com", result="bounced",
                      campaign="g1-w1", ts="2026-11-05T10:00:00")
        ol = OutcomeLedger(self.dir)
        ol.add_count(metric="x_followers", value=120, window="2026-10-31",
                     source_ref="seed", approximated=True)
        ol.add_count(metric="x_followers", value=153, window="2026-11-05",
                     source_ref="seed", approximated=True)
        for day, value in (("2026-11-02", 100), ("2026-11-03", 200),
                           ("2026-11-04", 300)):
            ol.add_count(metric="x_impressions", value=value, window=day,
                         source_ref="seed", approximated=True)

    def test_existing_contract_stays_verbatim(self):
        self._seed()
        card = self.build(make_run())
        self.assertEqual(12, card["published"]["total"])
        self.assertEqual(12, card["published"]["by_channel"]["x"]["reply"])
        self.assertEqual(1, card["email"]["bounced"])
        self.assertEqual(200, card["impressions"]["avg_daily"])
        self.assertEqual(3, card["impressions"]["days_read"])
        self.assertEqual(33, card["followers"]["new"])
        text = result_card.render(card)
        self.assertTrue(text.startswith("# 结果单：g1（2026-11-01 ~ 2026-11-07）"))
        for anchor in (
                "## 这一周交付了什么",
                "| 发布量（committed） | 12（计划 20） | 账本已确认发出，可逐条回查 |",
                "| 日均曝光 | 200（3/7 天有读数） | 平台后台自报数，approximated |",
                "| 新增粉丝 | 33（120 → 153） | 粉丝数日快照差值，approximated |",
                "| 退信 | 1 | 账本 bounced 终态 |",
                "## 边界声明",
                "- 发布量是「已发出且确认」，不等于业务转化；",
                "- 曝光与粉丝是平台自报数（首周人工对数，UNVERIFIED 起步）；",
                "- 缺数写「取不到」，不写 0；三层口径互不推导。"):
            self.assertIn(anchor, text)

    def test_new_keys_are_present(self):
        card = self.build(make_run())
        for key in ("business", "guardrails", "supply", "next_week"):
            self.assertIn(key, card)
        self.assertNotIn("business", card["published"])  # 旧结构不夹带

    def test_new_sections_precede_boundary_declaration(self):
        text = result_card.render(self.build(make_run()))
        self.assertLess(text.index("## 这一周交付了什么"),
                        text.index("## 业务事件"))
        self.assertLess(text.index("## 业务事件"), text.index("## 边界声明"))
        self.assertTrue(text.rstrip().endswith("三层口径互不推导。"))


if __name__ == "__main__":
    unittest.main()
