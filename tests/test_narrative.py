"""业务叙述：四件套强制、拒绝正确的废话。"""
import unittest

from core.narrative import Claim, ClaimError, audit_claims, compare, render_claims


def _ok():
    return Claim(
        headline="X 上的批量触达等于零",
        evidence="约 900 次 X 动作只换回 5 次回应，有效率 0.55%（2026-10-31 复盘 §三）",
        mechanism="因为 X 流通的货币是注意力，批量回复消耗注意力却不提供价值，所以对方不回应",
        action="停掉模板化回复，只保留针对具体技术问题的深对话",
        sources=["复盘-GTM全周期-20261031.md:1"],
    )


class NarrativeTests(unittest.TestCase):
    def test_good_claim_renders_four_parts(self):
        text = _ok().render()
        for part in ("X 上的批量触达等于零", "0.55%", "为什么：", "所以："):
            self.assertIn(part, text)

    def test_evidence_without_numbers_rejected(self):
        c = _ok()
        c.evidence = "做了很多触达但效果不好"
        with self.assertRaises(ClaimError) as ctx:
            c.validate()
        self.assertIn("没有数字", str(ctx.exception))

    def test_missing_source_rejected(self):
        c = _ok()
        c.sources = []
        c.evidence = "900 次动作换回 5 次回应"
        with self.assertRaises(ClaimError):
            c.validate()

    def test_banned_phrase_rejected(self):
        c = _ok()
        c.action = "要更用心地经营渠道"
        with self.assertRaises(ClaimError) as ctx:
            c.validate()
        self.assertIn("正确但没有信息", str(ctx.exception))

    def test_mechanism_without_causation_rejected(self):
        c = _ok()
        c.mechanism = "平台规则比较严格，大家都这样"
        with self.assertRaises(ClaimError) as ctx:
            c.validate()
        self.assertIn("因果", str(ctx.exception))

    def test_short_action_rejected(self):
        c = _ok()
        c.action = "注意"
        with self.assertRaises(ClaimError):
            c.validate()

    def test_audit_collects_all_problems(self):
        bad = _ok()
        bad.evidence = "没有数字"
        problems = audit_claims([_ok(), bad])
        self.assertEqual(len(problems), 1)

    def test_render_claims_numbers_them(self):
        text = render_claims([_ok(), _ok()], start=1)
        self.assertIn("1. ", text)
        self.assertIn("2. ", text)

    def test_compare_states_the_multiple(self):
        self.assertIn("倍", compare("GitHub", 39, "X", 0.55, "%"))
        self.assertIn("为 0", compare("A", 3, "B", 0))


if __name__ == "__main__":
    unittest.main()
