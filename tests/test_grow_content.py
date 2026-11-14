"""内容公式、配比与结构闸门；纯离线。"""
import unittest

from core import grow_content as gc


class FormulaTests(unittest.TestCase):
    def test_formulas_for_layers(self):
        replies = gc.formulas_for("reply")
        posts = gc.formulas_for("post")
        self.assertTrue(all(f["id"].startswith("reply.") for f in replies))
        self.assertTrue(all(f["id"].startswith("post.") for f in posts))
        self.assertEqual(len(gc.formulas_for()), len(replies) + len(posts))

    def test_mix_by_family(self):
        self.assertGreater(gc.mix_for("awareness")["reply"],
                           gc.mix_for("awareness")["post"])
        self.assertEqual({"reply": 0.8, "post": 0.2}, gc.mix_for("unknown"))


class EnrichTests(unittest.TestCase):
    def test_brief_gets_formulas_summing_to_count(self):
        brief = {"id": "b-x-reply", "channel": "x", "action": "reply",
                 "count_per_week": 7}
        enriched = gc.enrich_brief(brief, goal_family="awareness")
        self.assertEqual(7, sum(row["count"] for row in enriched["formulas"]))
        self.assertEqual("reply", enriched["content_layer"])
        self.assertIn("mix", enriched)

    def test_allocate_and_weights(self):
        allocation = gc.allocate(7, ["a", "b", "c"], {"a": 0.5, "b": 0.3, "c": 0.2})
        self.assertEqual(7, sum(allocation.values()))
        self.assertGreater(allocation["a"], allocation["c"])

    def test_apply_weights_normalizes_and_clamps(self):
        weights = gc.apply_weights(None, boost="reply.agree_extend_ask",
                                   reduce="reply.contrast_boundary")
        self.assertAlmostEqual(1.0, sum(weights.values()), places=3)
        self.assertGreater(weights["reply.agree_extend_ask"],
                           weights["reply.contrast_boundary"])


class SuggestTests(unittest.TestCase):
    def test_suggest_formula_by_target(self):
        self.assertEqual("reply.koc_peer",
                         gc.suggest_formula("invoice pain", koc=True))
        self.assertEqual("reply.agree_extend_ask",
                         gc.suggest_formula("How do you reconcile invoice?"))
        self.assertEqual("reply.contrast_boundary",
                         gc.suggest_formula("Invoice matching is simple, but..."))
        self.assertEqual("post.teardown", gc.suggest_formula("x", kind="post"))

    def test_unknown_formula_is_flagged(self):
        result = gc.score_content("A concrete point with 30% and a question?",
                                  kind="reply", formula="reply.not_exists")
        self.assertTrue(result["unknown_formula"])
        self.assertTrue(any("未知公式" in issue for issue in result["issues"]))


class ScoreTests(unittest.TestCase):
    TARGET = ("Our AI invoice doesn't match usage. How do you reconcile token "
              "cost per team before the close?")

    def test_good_reply_scores_high(self):
        text = ("Same trap here. We reconcile invoice total against the usage "
                "export and the contract price, then park anything that does not "
                "tie in a gap line. Which source do you treat as canonical?")
        result = gc.score_content(text, kind="reply", target_text=self.TARGET,
                                  formula="reply.agree_extend_ask")
        self.assertGreaterEqual(result["score"], 55)
        self.assertTrue(result["ok"])
        self.assertTrue(result["beats"]["acknowledge"])
        self.assertTrue(result["beats"]["invite"])

    def test_generic_praise_is_penalized(self):
        result = gc.score_content("Great post, totally agree!", kind="reply",
                                  target_text=self.TARGET)
        self.assertLess(result["score"], 55)
        self.assertTrue(any("空泛夸" in issue for issue in result["issues"]))
        self.assertFalse(result["beats"]["invite"])

    def test_reply_link_and_product_are_penalized(self):
        text = ("We use Verovo for invoice reconciliation. Check https://example.com "
                "if you want details. What do you think?")
        result = gc.score_content(text, kind="reply", target_text=self.TARGET,
                                  product_name="Verovo")
        self.assertTrue(any("带链接" in issue for issue in result["issues"]))
        self.assertTrue(any("产品名" in issue for issue in result["issues"]))
        self.assertFalse(result["ok"])

    def test_post_formula_checks_evidence(self):
        weak = gc.score_content("Short thought about billing.", kind="post")
        strong = gc.score_content(
            "How we closed a messy AI bill in 3 steps. First, tie invoice total to "
            "usage export. Then map seats to contract price. Finally, park the 13% "
            "gap instead of allocating it. This fails when seats change mid-cycle.",
            kind="post", formula="post.teardown")
        self.assertGreater(strong["score"], weak["score"])
        self.assertTrue(strong["beats"]["boundary"])

    def test_stage_lint_returns_issues(self):
        result = gc.lint_stage_item({"body": "Great post!", "kind": "reply",
                                     "target_text": self.TARGET})
        self.assertFalse(result["ok"])
        self.assertTrue(result["issues"])

    def test_render_formulas(self):
        text = gc.render_formulas()
        self.assertIn("reply.agree_extend_ask", text)
        self.assertIn("配比", text)


if __name__ == "__main__":
    unittest.main()
