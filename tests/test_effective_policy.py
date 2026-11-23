#!/usr/bin/env python3
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path

from core import engagement_cycle as cycle
from core.effective_policy import EffectivePolicy
from core.ledger import Ledger

NOW = dt.datetime(2026, 11, 23, 22, 5, tzinfo=dt.timezone.utc)


class EffectivePolicyTests(unittest.TestCase):
    def policy(self):
        return {"content_rules": {"x_publish": {"links_in_body": False}},
                "quota": {"enforce": False,
                          "safety": {"enforced": True, "per_night": {"x/reply": 1}}}}

    def test_canonical_hash_and_explanation_are_deterministic(self):
        one = EffectivePolicy.compile(self.policy(), NOW)
        two = EffectivePolicy.compile(copy.deepcopy(self.policy()), NOW)
        self.assertEqual(one.sha256, two.sha256)
        self.assertEqual(64, len(one.sha256))
        self.assertEqual(one.explain(), two.explain())
        self.assertIn("suppress", one.explain()["precedence"][0])

    def test_decision_precedence_never_lets_content_or_quota_override_safety(self):
        policy = EffectivePolicy.compile(self.policy(), NOW)
        got = policy.decide({"channel": "x", "action": "reply", "text": "x"},
                            signals={"suppressed": True, "cooldown_active": True,
                                     "quota_exceeded": True})
        self.assertFalse(got["allowed"])
        self.assertEqual("suppress", got["stage"])
        got = policy.decide({"channel": "x", "action": "reply", "text": "x"},
                            signals={"safety_cap_missing": True, "quota_exceeded": True})
        self.assertEqual("safety", got["stage"])

    def test_legacy_reason_mapping_keeps_existing_skip_codes(self):
        policy = EffectivePolicy.compile(self.policy(), NOW)
        self.assertEqual("safety", policy.from_legacy_reason("safety_cap_exhausted")["stage"])
        self.assertEqual("content", policy.from_legacy_reason("x_link_in_body")["stage"])
        self.assertEqual("quota", policy.from_legacy_reason("daily_quota_exhausted")["stage"])

    def test_cycle_plan_exposes_hash_and_ordered_decision_records(self):
        pol = copy.deepcopy(cycle.DEFAULT_POLICY)
        pol["window"] = {"start_hour": 22, "end_hour": 7, "waves_per_night": 1}
        pol["platforms"] = {"x": {"publish": {"min_gap_sec": 0, "per_hour": 2, "per_night": 2}}}
        pol["supply"]["wave_targets"] = {"x/publish": 1}
        pol["content_rules"] = {"x_publish": {"links_in_body": False, "artifact_min_per_wave": 0},
                                "reply": {"artifact_min_per_wave": 0, "artifact_min_ratio": 0},
                                "focus": {"min_share": 0, "targets_file": ""}}
        with tempfile.TemporaryDirectory() as tmp:
            got = cycle.plan([
                {"channel": "x", "action": "publish", "target": "", "text": "ok"},
                {"channel": "x", "action": "publish", "target": "", "text": "https://github.com/a/b"},
            ], pol, ledger=Ledger(Path(tmp) / "ledger.jsonl"), now=NOW)
        self.assertEqual(64, len(got["effective_policy"]["sha256"]))
        reasons = [row["reason"] for row in got["effective_policy"]["decisions"]]
        self.assertIn("allowed", reasons)
        self.assertIn("x_link_in_body", reasons)


if __name__ == "__main__":
    unittest.main()
