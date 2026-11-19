"""G-12: content positioning follows product facts; schedules start today.

The review found the module hardcoded one operator's audience ("硬件产品经理、
电商运营") and a fixed 2026-11-07 start. Three unrelated products must produce
three different ICPs, themes, CTAs and metrics, and a default plan must not
begin in the past.
"""
import unittest
from datetime import date, timedelta

from core import content_ops


def facts(kind, name, what, reader, event):
    return {
        "product": {"name": name, "what": what, "product_type": kind,
                    "stage": "launched"},
        "icp": [{"hypothesis": reader, "tags": [], "evidence_label": "hypothesis"}],
        "value_events": [{"name": event, "source": "产品事件"}],
    }


DEVTOOL = facts("open_source", "RepoLens", "review a repository's dependency drift",
                "维护开源仓库的独立开发者", "首个成功任务")
HARDWARE = facts("consumer_hardware", "CampKettle",
                 "a kettle that keeps water at temperature off-grid",
                 "周末露营的家庭用户", "真实下单")
SAAS = facts("b2b_saas", "LedgerLoop",
             "close the monthly books with an approval trail",
             "成长型公司的财务与客户成功团队", "团队激活")

ALL = {"devtool": DEVTOOL, "hardware": HARDWARE, "saas": SAAS}


class PositioningTests(unittest.TestCase):
    def brief(self, payload):
        return content_ops.brief("xiaohongshu", "选题", facts=payload)

    def test_each_product_keeps_its_own_reader_and_cta(self):
        readers = {}
        ctas = {}
        for key, payload in ALL.items():
            positioning = self.brief(payload)["positioning"]
            readers[key] = positioning["reader"]
            ctas[key] = positioning["call_to_action"]
        self.assertIn("开源仓库", readers["devtool"])
        self.assertIn("露营", readers["hardware"])
        self.assertIn("财务", readers["saas"])
        for key, reader in readers.items():
            for other, payload in ALL.items():
                if other == key:
                    continue
                self.assertNotEqual(reader, payload["icp"][0]["hypothesis"])
        self.assertEqual(3, len(set(ctas.values())), "三个产品不能共用同一行动号召")

    def test_themes_and_metrics_do_not_leak_across_products(self):
        devtool = self.brief(DEVTOOL)["positioning"]
        hardware = self.brief(HARDWARE)["positioning"]
        saas = self.brief(SAAS)["positioning"]
        self.assertIn("接入与自动化", devtool["themes"])
        self.assertIn("购买与售后", hardware["themes"])
        self.assertIn("团队流程改造", saas["themes"])
        self.assertNotIn("购买与售后", devtool["themes"])
        self.assertNotIn("硬件主线", devtool["themes"])
        self.assertEqual(["首个成功任务"], devtool["metrics"])
        self.assertEqual(["真实下单"], hardware["metrics"])
        self.assertEqual(["团队激活"], saas["metrics"])

    def test_unknown_product_gets_neutral_positioning(self):
        neutral = content_ops.positioning_for()
        blob = repr(neutral)
        for banned in ("硬件产品经理", "电商运营", "硬件主线", "视频工具"):
            self.assertNotIn(banned, blob)
        self.assertIn("不预设行业", neutral["reader"])

    def test_explicit_without_facts_still_uses_the_given_audience(self):
        positioning = content_ops.brief(
            "xiaohongshu", "选题", audience="独立播客制作人",
            product="剪一版")["positioning"]
        self.assertEqual("独立播客制作人", positioning["reader"])
        self.assertIn("剪一版", positioning["statement"])


class ScheduleStartTests(unittest.TestCase):
    def test_default_plan_starts_today(self):
        plan = content_ops.plan(days=7)
        self.assertEqual(date.today().isoformat(), plan["start"])
        self.assertFalse(plan["replay"])

    def test_explicit_past_start_is_marked_as_replay(self):
        past = (date.today() - timedelta(days=11)).isoformat()
        plan = content_ops.plan(start=past, days=3)
        self.assertEqual(past, plan["start"])
        self.assertTrue(plan["replay"])
        self.assertIn("历史回放", plan["note"])

    def test_plan_rows_only_use_the_products_own_themes(self):
        plan = content_ops.plan(days=14, facts=HARDWARE)
        pool = set(plan["positioning"]["themes"])
        devtool_pool = set(content_ops.positioning_for(facts=DEVTOOL)["themes"])
        for row in plan["rows"]:
            self.assertIn(row["theme"], pool)
            self.assertNotIn(row["theme"], devtool_pool - pool)

    def test_rendered_plan_states_the_reader(self):
        text = content_ops.render_plan(content_ops.plan(days=2, facts=SAAS))
        self.assertIn("读者：", text)
        self.assertIn("财务", text)
        self.assertIn(date.today().isoformat(), text)


if __name__ == "__main__":
    unittest.main()
