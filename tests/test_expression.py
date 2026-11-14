"""表达规则：生造词检查（find/fix/check/render）+ CLI + MCP。

规则：面向用户的输出不许出现需要先翻译才能懂的生造比喻词。
这里同时验证「不误报」——检查器一旦开始误报，人就会关掉它。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import expression


ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"


class FindTests(unittest.TestCase):
    def test_detects_coined_term_with_replacement(self):
        hits = expression.find("先做发射开关，再发内容。")
        self.assertEqual([h["term"] for h in hits], ["发射开关"])
        self.assertEqual(hits[0]["replace_with"], "发布前准备")
        self.assertEqual(hits[0]["line"], 1)

    def test_longest_term_wins(self):
        # 「发射开关」被整个替换成「发布前准备」，不再单独命中「发射」
        hits = expression.find("发射开关")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["term"], "发射开关")

    def test_reports_line_numbers(self):
        hits = expression.find("第一行没问题\n第二行有发射点")
        self.assertEqual(hits[0]["line"], 2)

    def test_clean_text_has_no_hits(self):
        self.assertEqual(expression.find("先做完发布前的准备，再发一次。"), [])

    def test_empty_text(self):
        self.assertEqual(expression.find(""), [])
        self.assertTrue(expression.check("")["ok"])

    def test_allowlist_does_not_fire(self):
        # 「发射器」是正常词，不应被判为生造词
        self.assertEqual(expression.find("这个发射器的功率"), [])

    def test_multiple_terms_ordered_by_position(self):
        hits = expression.find("先定发射窗口，再谈打法")
        self.assertEqual([h["term"] for h in hits], ["发射窗口", "打法"])


class FixTests(unittest.TestCase):
    def test_fix_replaces_and_reports(self):
        fixed, hits = expression.fix("先做发射开关，再谈打法。")
        self.assertIn("发布前准备", fixed)
        self.assertIn("做法", fixed)
        self.assertNotIn("发射", fixed)
        self.assertEqual(len(hits), 2)

    def test_fix_on_clean_text_is_identity(self):
        text = "先做完发布前的准备。"
        fixed, hits = expression.fix(text)
        self.assertEqual(fixed, text)
        self.assertEqual(hits, [])


class CheckAndRenderTests(unittest.TestCase):
    def test_check_shape(self):
        res = expression.check("用发射点做一次集中曝光")
        self.assertFalse(res["ok"])
        self.assertEqual(res["count"], 1)
        self.assertIn("rule", res)
        self.assertIn("hint", res)

    def test_render_mentions_rule_even_when_ok(self):
        self.assertIn("规则", expression.render(expression.check("干净的一段话")))

    def test_render_lists_findings(self):
        out = expression.render(expression.check("先做发射开关"))
        self.assertIn("发射开关", out)
        self.assertIn("发布前准备", out)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        self.env["MKT_HOME"] = tempfile.mkdtemp(prefix="mkt-plain-")

    def _run(self, *args):
        return subprocess.run([sys.executable, str(MKT), "voice", "plain", *args],
                              cwd=ROOT, env=self.env, text=True, capture_output=True)

    def test_exits_2_on_findings(self):
        proc = self._run("--text", "先做发射开关")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("生造词", proc.stdout)

    def test_exits_0_when_clean(self):
        proc = self._run("--text", "先把发布前的准备做完")
        self.assertEqual(proc.returncode, 0)

    def test_json_output(self):
        proc = self._run("--text", "发射点", "--json")
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["findings"][0]["replace_with"], "集中发布渠道")

    def test_fix_prints_corrected_text(self):
        proc = self._run("--text", "先做发射开关", "--fix")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "先做发布前准备")

    def test_file_input(self):
        path = Path(tempfile.mkdtemp()) / "draft.md"
        path.write_text("这篇讲打法。", encoding="utf-8")
        proc = self._run("--file", str(path))
        self.assertEqual(proc.returncode, 2)


class McpTests(unittest.TestCase):
    def test_mcp_tool(self):
        from mcp_server import _call_tool
        res = _call_tool("mkt_plain_language", {"text": "先做发射开关"})
        payload = json.loads(res["content"][0]["text"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["count"], 1)


class RuleTests(unittest.TestCase):
    def test_rule_is_single_readable_sentence(self):
        self.assertIn("日常说法", expression.RULE)
        self.assertLess(len(expression.RULE), 200)

    def test_replacements_do_not_contain_banned_terms(self):
        # 替换词自己不能再生造一个词
        for plain in expression.COINED_TERMS.values():
            self.assertEqual(expression.find(plain), [], msg=f"替换词仍含生造词：{plain}")


if __name__ == "__main__":
    unittest.main()


class RenderGuardTests(unittest.TestCase):
    """规则要钉在渲染出口上：以后谁往里加生造词，输出也会被换掉。"""

    def test_decorator_rewrites_return_value(self):
        from core.expression import plain_output

        @plain_output
        def render():
            return "先做发射开关，再谈打法"
        self.assertEqual(render(), "先做发布前准备，再谈做法")

    def test_every_renderer_is_guarded(self):
        from core import cadence, channels, goals, gtm_design, sprint, strategy
        targets = [
            (strategy, "render"), (cadence, "render"), (sprint, "render"),
            (goals, "render_plan"), (goals, "render_list"),
            (channels, "render"), (gtm_design, "render"), (gtm_design, "render_handoff"),
        ]
        for mod, name in targets:
            fn = getattr(mod, name)
            self.assertTrue(hasattr(fn, "__wrapped__"),
                            msg=f"{mod.__name__}.{name} 没接表达关口")

    def test_injected_term_is_removed_end_to_end(self):
        from core import strategy
        facts = {
            "product": {"name": "测试产品", "what": "证据分级研究工具",
                        "url": "https://example.com", "stage": "launched",
                        "product_type": "devtool"},
            "icp": [{"hypothesis": "开发者", "tags": ["technical-founders"],
                     "evidence_label": "hypothesis", "evidence": "待验证"}],
            "value_events": [{"name": "跑通", "source": "回执", "is_approximation": False}],
            "constraints": {"red_lines": ["不群发"], "human_minutes_budget": 600},
            "owned_assets": {"platforms": ["github", "hackernews"]},
        }
        payload = strategy.build(facts)
        payload["scale_gate"] = "先做发射开关再说"
        # 没接关口时这句会原样出现在报告里
        self.assertIn("发射", strategy.render.__wrapped__(payload))
        self.assertNotIn("发射", strategy.render(payload))
