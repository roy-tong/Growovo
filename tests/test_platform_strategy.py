"""平台策略层：证据追溯、检测器契约、门禁与排期节奏。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import content_ops, experience, platform_strategy, platforms


ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"


class PlatformStrategyTests(unittest.TestCase):
    def test_every_card_is_a_real_platform(self):
        for pid in platform_strategy.platform_ids():
            self.assertIn(pid, platforms.BY_ID)

    def test_validate_passes(self):
        self.assertEqual(platform_strategy.validate(), {})

    def test_detector_contract_matches_content_ops(self):
        self.assertEqual(set(platform_strategy.DETECTORS), set(content_ops.DETECTOR_IMPL))

    def test_craft_rules_have_two_independent_sources(self):
        for pid in platform_strategy.platform_ids():
            for rule in platform_strategy.rules(pid, grade="craft"):
                assessment = experience.assess_eligibility(
                    next(row for row in experience.load()
                         if row["platform"] == pid and row["claim"] == rule["claim"]))
                self.assertGreaterEqual(assessment["support"], 2, rule["claim"])

    def test_lead_rules_never_become_gates(self):
        for pid in platform_strategy.platform_ids():
            for gate in platform_strategy.gates(pid, scope="all"):
                self.assertIn(gate["grade"], ("official", "craft"))
                self.assertTrue(gate.get("source"))

    def test_limits_match_content_channels(self):
        for pid in ("xiaohongshu", "wechat", "zhihu", "douyin"):
            channel = content_ops.CHANNELS[pid]
            limits = platform_strategy.limits(pid)
            self.assertEqual(channel["length"]["min"], limits["body_min"])
            self.assertEqual(channel["length"]["max"], limits["body_max"])
            self.assertEqual(channel["title_max"], limits["title_max"])
            self.assertEqual(channel["gates"], platform_strategy.gates(pid, scope="content"))

    def test_stale_window(self):
        self.assertEqual(platform_strategy.stale("2026-11-17"), [])
        stale = platform_strategy.stale("2027-02-01")
        self.assertEqual({row["platform"] for row in stale},
                         set(platform_strategy.platform_ids()))

    def test_card_renders_evidence_and_scope(self):
        text = platform_strategy.render_card("wechat")
        self.assertIn("公众号", text)
        self.assertIn("流量主", text)
        self.assertIn("不证明平台分发", text)
        self.assertIn("库里", text)

    def test_broken_claim_is_rejected(self):
        original = platform_strategy.STRATEGY["xiaohongshu"]["rules"]
        platform_strategy.STRATEGY["xiaohongshu"]["rules"] = original + [
            {"claim": "这条规则不存在于经验库", "action": "x", "grade": "craft"}]
        try:
            problems = platform_strategy.validate(["xiaohongshu"])
        finally:
            platform_strategy.STRATEGY["xiaohongshu"]["rules"] = original
        self.assertIn("xiaohongshu", problems)
        self.assertTrue(any("找不到这条 claim" in item for item in problems["xiaohongshu"]))


class ContentGateTests(unittest.TestCase):
    def _codes(self, channel, text, title=""):
        result = content_ops.audit_text(channel, text, title=title)
        return ([item["code"] for item in result["errors"]],
                [item["code"] for item in result["warnings"]])

    def test_xiaohongshu_induce_follow_warns(self):
        _, warnings = self._codes(
            "xiaohongshu",
            "这里讲一个具体场景。\n\n关注后回复「资料」领取完整清单。\n\n你今天就可以先试一下。")
        self.assertIn("induce_follow", warnings)

    def test_xiaohongshu_anxiety_is_flagged_once(self):
        errors, warnings = self._codes(
            "xiaohongshu",
            "再不学就晚了，同辈已经把你甩开。\n\n先做一张表。\n\n你今天就可以先试一下。")
        self.assertNotIn("anxiety_sell", errors)
        self.assertEqual(warnings.count("anxiety_sell"), 1)

    def test_wechat_share_inducement_is_error(self):
        errors, _ = self._codes("wechat", "很多团队做AI硬件。\n\n集赞满30个送模板。\n\n评论区聊聊。")
        self.assertIn("induce_share", errors)

    def test_wechat_ad_begging_is_error(self):
        errors, _ = self._codes("wechat", "很多团队做AI硬件。\n\n点文末广告支持我。\n\n评论区聊聊。")
        self.assertIn("ad_begging", errors)

    def test_zhihu_contact_is_error(self):
        errors, _ = self._codes("zhihu", "先说结论。\n\n加我微信聊。\n\n" + "过程细节。" * 300)
        self.assertIn("private_contact", errors)

    def test_douyin_external_link_is_error(self):
        errors, _ = self._codes("douyin", "3 秒演示功能。\n\n完整步骤见 https://example.com/x\n\n关注看系列。")
        self.assertIn("external_link", errors)

    def test_douyin_has_no_body_length_warning(self):
        _, warnings = self._codes("douyin", "3 秒演示功能。\n\n关注看系列。")
        self.assertNotIn("length", warnings)


class StrategyCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = dict(os.environ, MKT_HOME=str(Path(self.tmp.name) / "data"))

    def _run(self, *args):
        return subprocess.run([sys.executable, str(MKT), *args], cwd=ROOT,
                              env=self.env, capture_output=True, text=True)

    def test_card_and_validate(self):
        card = self._run("strategy", "card", "xiaohongshu")
        self.assertEqual(card.returncode, 0, card.stderr)
        self.assertIn("小红书 · 平台策略卡", card.stdout)
        validate = self._run("strategy", "validate")
        self.assertEqual(validate.returncode, 0, validate.stderr)

    def test_content_plan_cadence_flag(self):
        evidence = self._run("content", "plan", "--days", "14", "--json")
        self.assertEqual(evidence.returncode, 0, evidence.stderr)
        payload = json.loads(evidence.stdout)
        self.assertEqual(payload["cadence"], "evidence")
        self.assertEqual(payload["counts"]["xiaohongshu"], 6)
        tactic = self._run("content", "plan", "--days", "14", "--cadence", "daily-two", "--json")
        self.assertEqual(tactic.returncode, 0, tactic.stderr)
        self.assertEqual(json.loads(tactic.stdout)["counts"]["xiaohongshu"], 28)


if __name__ == "__main__":
    unittest.main()
