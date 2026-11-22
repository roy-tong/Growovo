#!/usr/bin/env python3
"""增长假设卡离线单测：卡校验、建实验、卡指纹、下一轮建议、CLI。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import experiment_hypothesis as hyp
from core import experiments

ROOT = Path(__file__).resolve().parents[1]
MKT = ROOT / "mkt.py"


def base_card(**overrides):
    card = {
        "exp_id": "hyp-1",
        "hypothesis": "给维护者发求评邮件能换来仓库评审回复",
        "support_metric": "review_reply_rate",
        "falsify_metric": "bounce_or_no_reply_rate",
        "decision_rule": "14 天内评审回复率 ≥10% 为 met；<2% 为 not_met",
        "alternative_explanations": ["名单里没有活跃维护者", "邮件被当成群发"],
        "next_change_on_fail": "audience",
        "stop_conditions": ["两轮更换人群后回复率仍 <2%"],
        "owner": "Roy",
        "window": {"start": "2026-11-22", "end": "2026-12-06"},
        "variant": "第一轮：20 名近 90 天有合并记录的中文维护者",
    }
    card.update(overrides)
    return card


class CardValidationTests(unittest.TestCase):
    def test_valid_card_passes(self):
        self.assertEqual([], hyp.validate_card(base_card()))

    def test_missing_falsify_metric_rejected(self):
        errors = hyp.validate_card(base_card(falsify_metric=""))
        self.assertTrue(any("falsify_metric" in e for e in errors))

    def test_same_metric_cannot_support_and_falsify(self):
        errors = hyp.validate_card(base_card(falsify_metric="review_reply_rate"))
        self.assertTrue(any("同一指标" in e for e in errors))

    def test_needs_two_alternative_explanations(self):
        errors = hyp.validate_card(base_card(alternative_explanations=["只有一个原因"]))
        self.assertTrue(any("alternative_explanations" in e for e in errors))

    def test_needs_stop_conditions(self):
        errors = hyp.validate_card(base_card(stop_conditions=[]))
        self.assertTrue(any("stop_conditions" in e for e in errors))

    def test_next_change_target_is_enumerated(self):
        errors = hyp.validate_card(base_card(next_change_on_fail="再改文案"))
        self.assertTrue(any("next_change_on_fail" in e for e in errors))

    def test_window_order_checked(self):
        errors = hyp.validate_card(base_card(window={"start": "2026-12-06",
                                                     "end": "2026-11-22"}))
        self.assertTrue(any("window.start" in e for e in errors))


class CardExperimentTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name)

    def test_create_writes_card_and_metric_spec(self):
        exp = hyp.create_from_card(self.data, base_card())
        self.assertEqual("planned", exp["status"])
        self.assertEqual("review_reply_rate", exp["metric"])
        self.assertEqual("bounce_or_no_reply_rate",
                         exp["metric_spec"]["falsify_metric"])
        self.assertEqual("audience", exp["metric_spec"]["next_change_on_fail"])
        stored = hyp.load_card(self.data, "hyp-1")
        self.assertEqual("audience", stored["next_change_on_fail"])
        self.assertTrue(hyp.check_card(self.data, "hyp-1")["ok"])

    def test_incomplete_card_creates_no_experiment(self):
        with self.assertRaises(hyp.HypothesisCardError):
            hyp.create_from_card(self.data, base_card(falsify_metric=""))
        self.assertFalse((self.data / "experiments" / "hyp-1.json").exists())

    def test_hand_edited_card_is_detected(self):
        hyp.create_from_card(self.data, base_card())
        path = self.data / "experiments" / "hyp-1.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["hypothesis_card"]["next_change_on_fail"] = "content"
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        out = hyp.check_card(self.data, "hyp-1")
        self.assertFalse(out["ok"])
        self.assertTrue(any("指纹" in e for e in out["errors"]))

    def test_next_change_hint_is_advisory(self):
        card = hyp.normalize_card(base_card())
        hint = hyp.next_change_hint(card, "not_met")
        self.assertEqual("audience", hint["action"])
        self.assertIn("换人群", hint["note"])
        self.assertEqual("review_power", hyp.next_change_hint(card, "inconclusive")["action"])
        # 建议不改实验：文件仍为 planned
        hyp.create_from_card(self.data, base_card())
        hyp.next_change_hint(hyp.load_card(self.data, "hyp-1"), "not_met")
        self.assertEqual("planned", experiments.load_experiment(self.data, "hyp-1")["status"])


class CardCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "data"
        self.home.mkdir(parents=True)
        self.card_path = Path(self._tmp.name) / "card.json"
        self.card_path.write_text(json.dumps(base_card(), ensure_ascii=False),
                                  encoding="utf-8")
        self.env = dict(os.environ, MKT_HOME=str(self.home), PYTHONPATH=str(ROOT))

    def run_mkt(self, *args):
        return subprocess.run([sys.executable, str(MKT), *args], cwd=ROOT,
                              env=self.env, text=True, capture_output=True)

    def test_card_new_and_check(self):
        proc = self.run_mkt("experiment", "card-new", "--card", str(self.card_path))
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("hyp-1", json.loads(proc.stdout)["exp_id"])
        proc = self.run_mkt("experiment", "card-check", "--id", "hyp-1")
        self.assertEqual(0, proc.returncode, proc.stdout)
        self.assertTrue(json.loads(proc.stdout)["ok"])

    def test_card_new_rejects_incomplete_card(self):
        self.card_path.write_text(json.dumps(base_card(stop_conditions=[]),
                                             ensure_ascii=False), encoding="utf-8")
        proc = self.run_mkt("experiment", "card-new", "--card", str(self.card_path))
        self.assertEqual(2, proc.returncode)
        self.assertEqual("INPUT_INVALID", json.loads(proc.stdout)["reason_code"])

    def test_card_show_returns_hint_for_result(self):
        self.run_mkt("experiment", "card-new", "--card", str(self.card_path))
        proc = self.run_mkt("experiment", "card-show", "--id", "hyp-1",
                            "--result", "not_met")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("audience", json.loads(proc.stdout)["action"])


if __name__ == "__main__":
    unittest.main()
