"""Explicit subscription/hybrid pricing experiments and measured acceptance."""
import json
import tempfile
import unittest
from pathlib import Path

from core import billing


class PricingModelTests(unittest.TestCase):
    def test_action_and_outcome_remain_candidates(self):
        action = billing.quote(3, 9, {"model": "action"})
        self.assertEqual(action["action_amount"], 120.0)
        self.assertEqual(action["outcome_amount"], 0.0)
        self.assertEqual(action["total"], 3120.0)
        outcome = billing.quote(9, 2, {"model": "outcome"})
        self.assertEqual(outcome["action_amount"], 0.0)
        self.assertEqual(outcome["outcome_amount"], 1600.0)
        self.assertEqual(outcome["total"], 4600.0)

    def test_explicit_subscription_experiment_is_allowed(self):
        q = billing.quote(4, 2, {"model": "subscription", "monthly_fee": 199})
        self.assertEqual(q["model"], "subscription")
        self.assertEqual(q["subscription_amount"], 199.0)
        self.assertEqual(q["setup_fee"], 0.0)
        self.assertEqual(q["action_amount"], 0.0)
        self.assertEqual(q["outcome_amount"], 0.0)
        self.assertEqual(q["total"], 199.0)
        self.assertTrue(q["experimental"])
        self.assertEqual(q["acceptance"], "unrecorded")
        self.assertIn("实验", billing.describe(q))

    def test_hybrid_combines_subscription_action_and_capped_outcome(self):
        q = billing.quote(2, 1, {"model": "hybrid", "subscription_fee": 99})
        self.assertEqual(q["subscription_amount"], 99.0)
        self.assertEqual(q["action_amount"], 80.0)
        self.assertEqual(q["outcome_amount"], 800.0)
        self.assertEqual(q["outcome_cap"], 3080.0)  # 订阅费不提高结果费封顶
        self.assertEqual(q["total"], 3979.0)

    def test_legacy_monthly_key_alone_is_still_ambiguous_not_a_global_ban(self):
        with self.assertRaises(ValueError):
            billing.quote(1, 1, {"monthly_fee": 199})
        with self.assertRaises(ValueError):
            billing.merge_pricing({"subscription": 199})
        merged = billing.merge_pricing({"pricing_model": "subscription", "subscription": 199})
        self.assertEqual(merged["subscription_fee"], 199.0)
        self.assertEqual(merged["model"], "subscription")

    def test_experiment_entry_requires_id_and_explicit_experimental_model(self):
        with self.assertRaises(ValueError):
            billing.quote_experiment(0, 0, {"model": "subscription", "monthly_fee": 1},
                                     experiment_id="")
        with self.assertRaises(ValueError):
            billing.quote_experiment(0, 0, {"model": "action"}, experiment_id="a-1")
        q = billing.quote_experiment(0, 0, {"model": "subscription", "monthly_fee": 199},
                                     experiment_id="sub-1")
        self.assertEqual(q["experiment_id"], "sub-1")
        self.assertTrue(q["experimental"])

    def test_invalid_subscription_values_and_conflicts_are_rejected(self):
        with self.assertRaises(ValueError):
            billing.quote(0, 0, {"model": "subscription", "monthly_fee": -1})
        with self.assertRaises(ValueError):
            billing.quote(0, 0, {"model": "subscription", "monthly_fee": 199,
                                 "subscription_fee": 299})
        with self.assertRaises(ValueError):
            billing.quote(0, 0, {"model": "action", "subscription_fee": 199})


class AcceptanceTests(unittest.TestCase):
    def test_offer_does_not_imply_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pricing.jsonl"
            log = billing.PricingExperimentLog(path)
            log.offer("sub-1", actions=0, outcomes=0,
                      pricing={"model": "subscription", "monthly_fee": 199},
                      offered_at="2026-11-04T08:00:00Z")
            summary = log.summary()
            self.assertEqual(summary["offered"], 1)
            self.assertEqual(summary["pending"], 1)
            self.assertIsNone(summary["acceptance_rate"])
            self.assertIn("不等于已付款", summary["note"])

    def test_acceptance_is_recorded_once_and_computed_per_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pricing.jsonl"
            log = billing.PricingExperimentLog(path)
            log.offer("sub-accepted", pricing={"model": "subscription", "monthly_fee": 199},
                      offered_at="2026-11-04T08:00:00Z")
            log.offer("sub-pending", pricing={"model": "subscription", "monthly_fee": 299},
                      offered_at="2026-11-04T08:01:00Z")
            log.offer("hybrid-rejected", actions=2, outcomes=1,
                      pricing={"model": "hybrid", "subscription_fee": 99},
                      offered_at="2026-11-04T08:02:00Z")
            billing.record_acceptance(path, "sub-accepted", True, reason="预算通过",
                                      responded_at="2026-11-04T09:00:00Z")
            log.record("hybrid-rejected", "rejected", reason="只要按结果",
                       responded_at="2026-11-04T09:01:00Z")
            log.record("sub-pending", None, responded_at="2026-11-04T09:02:00Z")
            summary = billing.acceptance_summary(path)
            self.assertEqual(summary["offered"], 3)
            self.assertEqual(summary["accepted"], 1)
            self.assertEqual(summary["rejected"], 1)
            self.assertEqual(summary["pending"], 1)
            self.assertEqual(summary["acceptance_rate"], 0.5)
            self.assertEqual(summary["by_model"]["subscription"]["acceptance_rate"], 1.0)
            self.assertEqual(summary["by_model"]["hybrid"]["acceptance_rate"], 0.0)
            with self.assertRaises(ValueError):
                log.record("sub-accepted", False)
            with self.assertRaises(ValueError):
                log.offer("sub-accepted", pricing={"model": "subscription", "monthly_fee": 199})
            with self.assertRaises(ValueError):
                log.record("missing", True)
            rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len([x for x in rows if x["event"] == "response"]), 3)

    def test_quote_acceptance_is_not_counted_as_business_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pricing.jsonl"
            log = billing.PricingExperimentLog(path)
            log.offer("hybrid-1", actions=1, outcomes=0,
                      pricing={"model": "hybrid", "subscription_fee": 100})
            log.record("hybrid-1", True)
            self.assertEqual(log.summary()["accepted"], 1)
            self.assertEqual(billing.count_outcomes(load_jsonl(path)), 0)


def load_jsonl(path):
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]


if __name__ == "__main__":
    unittest.main()
