"""商业形状：按动作 / 按结果计价，月费在代码层被拒绝。"""
import unittest

from core import billing


class BillingTests(unittest.TestCase):
    def test_quote_breakdown(self):
        q = billing.quote(actions=20, outcomes=2)
        self.assertEqual(q["action_amount"], 800.0)      # 20 × 40
        self.assertEqual(q["outcome_amount"], 1600.0)    # 2 × 800
        self.assertEqual(q["total"], 3000 + 800 + 1600)
        self.assertFalse(q["outcome_capped"])

    def test_outcome_fee_is_capped(self):
        q = billing.quote(actions=10, outcomes=100)
        base = 3000 + 400
        self.assertEqual(q["outcome_cap"], float(base))
        self.assertEqual(q["outcome_amount"], float(base))
        self.assertTrue(q["outcome_capped"])
        self.assertLess(q["total"], 3000 + 400 + 100 * 800)

    def test_monthly_pricing_is_refused_in_code(self):
        with self.assertRaises(ValueError) as ctx:
            billing.quote(1, 1, {"monthly_fee": 999})
        self.assertIn("月", str(ctx.exception))

        with self.assertRaises(ValueError):
            billing.merge_pricing({"subscription": 199})

    def test_unknown_field_rejected(self):
        with self.assertRaises(ValueError):
            billing.merge_pricing({"随便加的": 1})

    def test_negative_inputs_rejected(self):
        with self.assertRaises(ValueError):
            billing.quote(-1, 0)
        with self.assertRaises(ValueError):
            billing.merge_pricing({"per_action": -1})

    def test_formula_states_no_monthly_fee(self):
        q = billing.quote(0, 0)
        self.assertIn("不收月费", q["formula"])
        self.assertIn("不收月费", billing.describe(q))

    def test_count_actions_ignores_dry_run(self):
        entries = [
            {"result": "committed"}, {"status": "sent"},
            {"result": "dry_run"}, {"result": "failed"}, {"result": "unknown"},
        ]
        self.assertEqual(billing.count_actions(entries), 2)

    def test_count_outcomes_ignores_approximation(self):
        events = [
            {"type": "signup", "verified": True},
            {"type": "activation", "is_approximation": True},
            {"type": "pageview"},
            {"type": "paid", "verified": True},
        ]
        self.assertEqual(billing.count_outcomes(events), 2)


if __name__ == "__main__":
    unittest.main()
