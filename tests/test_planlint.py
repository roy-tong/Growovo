#!/usr/bin/env python3
"""planlint + transports 注册表离线单测（05 产品方案 §2.4/§4）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.planlint import PlanNotApproved, enforce_approval, lint_plan
from core.throttle import INTERVALS
from core import transports


def valid_plan(**over):
    p = {
        "plan_id": "am-trial20",
        "goal": "拿到 20 个企业试用",
        "created": "2026-10-04",
        "hypothesis": "MCP 工具作者有计量痛点",
        "icp": {"org": "agent 产品团队", "persona": "平台负责人", "beachhead": "MCP server 作者"},
        "audience": {"spec": "github:MCP servers 目录", "target_count": 40, "evidence_grade": "B"},
        "waves": [
            {"wave": 1, "channel": "email", "action": "message", "size": 30,
             "content_policy": "求评式/千人千面", "queue": "data/queues/am-trial20-w1.json",
             "caps": {"daily": 30}},
        ],
        "success_criteria": {"metric": "reply_rate", "target": "≥5%", "stop": "退信率>5% 即停"},
        "review": {"human_approved": False},
    }
    p.update(over)
    return p


class TestLint(unittest.TestCase):
    def test_valid_plan_passes(self):
        self.assertEqual(lint_plan(valid_plan()), [])

    def test_missing_fields(self):
        p = valid_plan()
        del p["icp"], p["success_criteria"]
        errs = lint_plan(p)
        self.assertTrue(any("icp" in e for e in errs))
        self.assertTrue(any("success_criteria" in e for e in errs))

    def test_beachhead_required(self):
        p = valid_plan(icp={"org": "x", "persona": "y"})
        errs = lint_plan(p)
        self.assertTrue(any("beachhead" in e for e in errs))

    def test_unknown_channel_rejected(self):
        p = valid_plan(waves=[{"wave": 1, "channel": "sms", "action": "message",
                               "size": 5, "content_policy": "x"}])
        errs = lint_plan(p)
        self.assertTrue(any("未知渠道" in e for e in errs))

    def test_unsupported_action_rejected(self):
        p = valid_plan(waves=[{"wave": 1, "channel": "email", "action": "engage",
                               "size": 5, "content_policy": "x"}])
        errs = lint_plan(p)
        self.assertTrue(any("不支持动作" in e for e in errs))

    def test_size_over_kernel_budget_rejected(self):
        """上限是内核属性：计划写 999 封/波 直接拒（email/message 日预算 100）。"""
        p = valid_plan(waves=[{"wave": 1, "channel": "email", "action": "message",
                               "size": 999, "content_policy": "x"}])
        errs = lint_plan(p)
        self.assertTrue(any("内核日预算" in e for e in errs))
        # 边界：恰好等于日预算可过
        cap = INTERVALS[("email", "message")][3]
        p2 = valid_plan(waves=[{"wave": 1, "channel": "email", "action": "message",
                                "size": cap, "content_policy": "x"}])
        self.assertEqual(lint_plan(p2), [])

    def test_content_policy_and_criteria_required(self):
        p = valid_plan(waves=[{"wave": 1, "channel": "email", "action": "message", "size": 5}])
        errs = lint_plan(p)
        self.assertTrue(any("content_policy" in e for e in errs))
        p2 = valid_plan(success_criteria={"metric": "reply_rate"})
        errs2 = lint_plan(p2)
        self.assertTrue(any("success_criteria.target" in e for e in errs2))
        self.assertTrue(any("success_criteria.stop" in e for e in errs2))


class TestEnforceApproval(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.plans = self.dir / "plans"
        self.plans.mkdir(parents=True)

    def _write(self, approved):
        (self.plans / "am-trial20.json").write_text(json.dumps(
            valid_plan(review={"human_approved": approved})), encoding="utf-8")

    def test_unapproved_blocks(self):
        self._write(False)
        with self.assertRaises(PlanNotApproved):
            enforce_approval(self.dir, "am-trial20-w1")   # 命中 wave queue 文件名

    def test_approved_passes(self):
        self._write(True)
        enforce_approval(self.dir, "am-trial20-w1")       # 不抛

    def test_adhoc_campaign_without_plan_passes(self):
        self._write(False)
        enforce_approval(self.dir, "random-adhoc")        # 无 plan 管辖，放行


class TestTransports(unittest.TestCase):
    def test_matrix(self):
        # 降级链含 RESERVE 备件；primary 取首选
        self.assertEqual(transports.primary("email", "message"), "protocol")
        self.assertEqual(transports.chain("email", "message"),
                         ["protocol", "esp-api", "browser-cdp", "semi-auto-ui"])
        # ACTIVE 链只保留当前可执行的通道（esp-api/semi-auto-ui 为 RESERVE）
        self.assertEqual(transports.active_chain("email", "message"),
                         ["protocol", "browser-cdp"])
        self.assertEqual(transports.primary("email", "inbox"), "browser-cdp")
        self.assertEqual(transports.primary("x", "publish"), "browser-cdp")
        self.assertEqual(transports.primary("desktop", "publish"), "os-input")
        self.assertEqual(transports.primary("linkedin", "message"), "browser-cdp")
        self.assertEqual(transports.chain("nosuch", "publish"), [])

    def test_registry_entries_exist(self):
        for t in ("api", "browser-cdp", "os-input", "protocol",          # ACTIVE
                  "webdriver", "rss-public", "esp-api", "mobile-appium",
                  "integration", "semi-auto-ui"):                         # RESERVE
            self.assertTrue(transports.known(t))


if __name__ == "__main__":
    unittest.main()
