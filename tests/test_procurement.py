#!/usr/bin/env python3
"""requires_procurement 显式分支（评审 2026-11-17 §5）。

预算/采购不属于端到端范围：遇到付费数据源/广告/付费 API 时输出结构化
JSON（terminal_state=BLOCKED_USER_INPUT、transaction_executed=false），
不静默跳过、不代替用户购买。
"""
import json
import tempfile
import unittest
from pathlib import Path

from core import procurement
from core.grow_compile import CompileError, compile_run

FACTS = {
    "product": {"name": "TraceLens", "what": "日志分诊 CLI",
                "url": "https://example.com", "stage": "launched",
                "product_type": "devtool"},
    "icp": [{"hypothesis": "独立开发者", "tags": ["developers"],
             "evidence_label": "hypothesis", "evidence": "待验证"}],
    "baseline": {"followers": 40},
    "value_events": [{"name": "首次生成证据包", "source": "本地事件",
                      "is_approximation": False}],
    "constraints": {"red_lines": ["不群发"]},
    "owned_assets": {"platforms": ["x"]},
}


class ProcurementPayloadTests(unittest.TestCase):
    def test_payload_matches_review_schema(self):
        payload = procurement.blocked(
            item="X 广告投放", why="投放需要用户广告账号与预算",
            user_action="在 X Ads 后台创建广告账号并预充值")
        self.assertEqual("BLOCKED_USER_INPUT", payload["terminal_state"])
        self.assertEqual("requires_procurement", payload["reason"])
        self.assertTrue(payload["item"])
        self.assertTrue(payload["why"])
        self.assertTrue(payload["user_action"])
        self.assertIsInstance(payload["alternatives"], list)
        self.assertTrue(payload["alternatives"])
        self.assertIs(False, payload["transaction_executed"])
        # 可序列化（CLI/JSON 输出用）
        json.dumps(payload, ensure_ascii=False)

    def test_render_is_one_json_object(self):
        payload = procurement.blocked(item="i", why="w", user_action="a")
        text = procurement.render(payload)
        self.assertEqual(payload, json.loads(text))


class CompileTriggerTests(unittest.TestCase):
    def test_paid_channel_request_raises_structured_block(self):
        with tempfile.TemporaryDirectory() as tmp, \
                self.assertRaises(CompileError) as ctx:
            compile_run(run_id="r1", facts=FACTS, goal_id="awareness.followers",
                        channels=["x", "x_ads"], data_dir=Path(tmp))
        payload = getattr(ctx.exception, "payload", None)
        self.assertIsNotNone(payload, "付费渠道拒绝必须携带结构化 payload")
        self.assertEqual("requires_procurement", payload["reason"])
        self.assertIs(False, payload["transaction_executed"])
        self.assertIn("requires_procurement", str(ctx.exception))

    def test_alias_and_free_channels_unaffected(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = compile_run(run_id="r2", facts=FACTS,
                               goal_id="awareness.followers",
                               channels=["x", "email"], data_dir=Path(tmp))
        self.assertIn("x", str(spec.get("channels", spec)))


class GrowLaunchPropagationTests(unittest.TestCase):
    def test_launch_error_carries_procurement_payload(self):
        from core.grow_v2 import GrowError, GrowRunV2
        with tempfile.TemporaryDirectory() as tmp:
            facts = Path(tmp) / "facts.json"
            facts.write_text(json.dumps(FACTS, ensure_ascii=False),
                             encoding="utf-8")
            with self.assertRaises(GrowError) as ctx:
                GrowRunV2.launch(
                    Path(tmp) / "grow" / "r3.json", run_id="r3",
                    facts_path=str(facts), goal_id="awareness.followers",
                    channels=["x_ads"], data_dir=Path(tmp), skip_probe=True)
        payload = getattr(ctx.exception, "payload", None)
        self.assertIsNotNone(payload)
        self.assertEqual("BLOCKED_USER_INPUT", payload["terminal_state"])
        self.assertIs(False, payload["transaction_executed"])


if __name__ == "__main__":
    unittest.main()
