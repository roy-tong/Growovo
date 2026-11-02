#!/usr/bin/env python3
"""GitHub expert task benchmark: fixture schema, split, metrics and CLI."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import github_benchmark as gb  # noqa: E402

CASES_PATH = ROOT / "tests" / "fixtures" / "github_benchmark" / "cases.jsonl"


def synthetic(case_id, title, body, expected):
    return {
        "id": case_id,
        "title": title,
        "body": body,
        "repo_context": {"repo": "acme/synthetic", "kind": "issue", "labels": [],
                         "age_days": 1, "supported_platforms": ["linux-x86_64"]},
        "expected": expected,
        "split": "dev",
        "rationale": "Synthetic case used only to verify a benchmark metric.",
    }


class BenchmarkFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = gb.load_cases(CASES_PATH)

    def test_size_splits_and_ids(self):
        self.assertGreaterEqual(len(self.cases), 24)
        self.assertEqual({"dev", "holdout"}, {case["split"] for case in self.cases})
        holdout = [case for case in self.cases if case["split"] == "holdout"]
        self.assertGreaterEqual(len(holdout), 6)
        self.assertGreaterEqual(len(self.cases) - len(holdout), 6)
        ids = [case["id"] for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_case_matches_the_schema(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertEqual([], gb.validate_case(case))
                for field in ("id", "title", "body", "repo_context", "expected", "split", "rationale"):
                    self.assertIn(field, case)
                self.assertTrue(case["rationale"].strip())
                self.assertIn(case["expected"]["category"], gb.CATEGORIES)
                self.assertIn(case["expected"]["priority"], gb.PRIORITIES)
                self.assertIn(case["expected"]["action"], gb.ACTIONS)
                self.assertIn(case["expected"]["risk_class"], gb.RISK_CLASSES)
                if case["expected"]["action"] in ("escalate", "hold_unknown"):
                    self.assertTrue(case["expected"].get("escalation_reason"))
                if case["expected"]["category"] == "security":
                    self.assertEqual("escalate", case["expected"]["action"])
                if case["expected"]["category"] == "unknown_result":
                    self.assertEqual("hold_unknown", case["expected"]["action"])

    def test_required_scenario_coverage(self):
        categories = {case["expected"]["category"] for case in self.cases}
        self.assertEqual(set(gb.CATEGORIES), categories)
        risks = {case["expected"]["risk_class"] for case in self.cases}
        self.assertIn("high", risks)
        self.assertIn("irreversible", risks)
        unknown = [case for case in self.cases
                   if case["expected"]["category"] == "unknown_result"]
        self.assertGreaterEqual(len(unknown), 2)
        # Normal, ambiguous and high-risk cases must all be present.
        self.assertGreaterEqual(sum(1 for case in self.cases
                                    if case["expected"]["risk_class"] == "low"), 10)
        self.assertGreaterEqual(sum(1 for case in self.cases
                                    if case["expected"]["category"] in ("security", "abuse")), 6)

    def test_invalid_fixture_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.jsonl"
            path.write_text('{"id": "gh-bad"}\n', encoding="utf-8")
            with self.assertRaises(gb.BenchmarkError):
                gb.load_cases(path)

    def test_invalid_split_is_rejected(self):
        with self.assertRaises(gb.BenchmarkError):
            gb.evaluate(self.cases, split="test")


class BenchmarkMetricTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = gb.load_cases(CASES_PATH)
        cls.report = gb.evaluate(cls.cases, split="all")

    def test_baseline_is_deterministic_and_ignores_expected(self):
        copy = json.loads(json.dumps(self.cases))
        changed = copy[0]
        changed["expected"] = {
            "category": "feature", "priority": "P2", "action": "reply", "risk_class": "low",
        }
        self.assertEqual(gb.baseline_classify(self.cases[0]),
                         gb.baseline_classify(changed))
        self.assertEqual(gb.render_json(self.report),
                         gb.render_json(gb.evaluate(self.cases, split="all")))

    def test_report_shape_and_ranges(self):
        report = self.report
        self.assertEqual("all", report["split"])
        self.assertEqual(len(self.cases), report["n_cases"])
        self.assertEqual(len(self.cases), len(report["per_case"]))
        self.assertEqual({"dev", "holdout"}, set(report["split_counts"]))
        for field in gb.FIELDS:
            self.assertGreaterEqual(report["field_accuracy"][field], 0.0)
            self.assertLessEqual(report["field_accuracy"][field], 1.0)
            self.assertEqual(report["field_correct"][field],
                             round(report["field_accuracy"][field] * report["n_cases"]))
        self.assertIn("keyword-rule baseline", report["baseline"]["name"])
        self.assertIn("not an expert", report["baseline"]["kind"])
        self.assertIn("not a capability claim", report["boundary"])

    def test_confusion_matrices_cover_every_case(self):
        for name, vocab in (("category", gb.CATEGORIES), ("action", gb.ACTIONS)):
            block = self.report["confusion"][name]
            self.assertEqual(list(vocab), block["labels"])
            matrix = block["matrix"]
            self.assertEqual(set(vocab), set(matrix))
            total = sum(sum(row.values()) for row in matrix.values())
            self.assertEqual(self.report["n_cases"], total)

    def test_harmful_action_metric_counts_public_actions_on_sensitive_cases(self):
        report = self.report
        self.assertEqual(report["harmful_count"], len(report["harmful_cases"]))
        self.assertGreater(report["sensitive_cases"], 0)
        self.assertEqual(report["harmful_action_rate"],
                         round(report["harmful_count"] / report["sensitive_cases"], 4))
        self.assertEqual(0, report["harmful_count"],
                         "baseline must not take a public action on any sensitive fixture case")
        for item in report["harmful_cases"]:
            self.assertIn(item["expected_category"], ("security", "abuse"))
            self.assertIn(item["predicted_action"], gb.HARMFUL_ACTIONS)
        # A forced public reply on a security case must be counted.
        unsafe = synthetic(
            "gh-998", "Question about the API", "How do I install this?",
            {"category": "security", "priority": "P0", "action": "escalate",
             "risk_class": "high", "escalation_reason": "private channel"})
        forced = gb.evaluate([unsafe], split="dev")
        self.assertEqual(1, forced["sensitive_cases"])
        self.assertEqual(1, forced["harmful_count"])
        self.assertEqual(1.0, forced["harmful_action_rate"])
        self.assertEqual("reply", forced["harmful_cases"][0]["predicted_action"])

    def test_safe_escalation_is_not_harmful(self):
        safe = synthetic(
            "gh-997", "Security vulnerability in auth", "A security vulnerability allows access.",
            {"category": "security", "priority": "P0", "action": "escalate",
             "risk_class": "high", "escalation_reason": "private channel"})
        report = gb.evaluate([safe], split="dev")
        self.assertEqual(1, report["sensitive_cases"])
        self.assertEqual(0, report["harmful_count"])
        self.assertEqual(0.0, report["harmful_action_rate"])

    def test_unknown_result_cases_are_held(self):
        unknown = [case for case in self.cases
                   if case["expected"]["category"] == "unknown_result"]
        report = gb.evaluate(unknown, split="all")
        self.assertEqual(len(unknown), report["unknown_result"]["cases"])
        self.assertEqual(len(unknown), report["unknown_result"]["handled"])
        self.assertEqual(1.0, report["unknown_result"]["rate"])

    def test_dev_and_holdout_are_disjoint_and_complete(self):
        dev = gb.evaluate(self.cases, split="dev")
        holdout = gb.evaluate(self.cases, split="holdout")
        dev_ids = {case["id"] for case in dev["per_case"]}
        holdout_ids = {case["id"] for case in holdout["per_case"]}
        self.assertFalse(dev_ids & holdout_ids)
        self.assertEqual({case["id"] for case in self.cases}, dev_ids | holdout_ids)
        self.assertGreaterEqual(len(holdout_ids), 6)
        self.assertEqual(len(holdout_ids), holdout["n_cases"])
        for case in holdout["per_case"]:
            self.assertEqual("holdout", case["split"])


class BenchmarkCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = gb.load_cases(CASES_PATH)

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "github_benchmark.py"), *args],
            cwd=str(ROOT), capture_output=True, text=True, check=False)

    def test_json_output_is_parseable_and_deterministic(self):
        first = self._run("--split", "holdout", "--format", "json")
        second = self._run("--split", "holdout", "--format", "json")
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        payload = json.loads(first.stdout)
        self.assertEqual("holdout", payload["split"])
        expected = sum(1 for case in self.cases if case["split"] == "holdout")
        self.assertEqual(expected, payload["n_cases"])

    def test_both_format_contains_markdown_and_json(self):
        proc = self._run("--split", "dev", "--format", "both")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("# GitHub expert task benchmark", proc.stdout)
        self.assertIn("```json", proc.stdout)
        start = proc.stdout.index("```json") + len("```json")
        end = proc.stdout.rindex("```")
        payload = json.loads(proc.stdout[start:end])
        self.assertEqual("dev", payload["split"])

    def test_out_dir_writes_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run("--split", "dev", "--format", "json", "--out-dir", tmp)
            self.assertEqual(0, proc.returncode, proc.stderr)
            json_path = Path(tmp) / "github-benchmark.json"
            md_path = Path(tmp) / "github-benchmark.md"
            self.assertTrue(json_path.is_file())
            self.assertTrue(md_path.is_file())
            self.assertEqual("dev", json.loads(json_path.read_text(encoding="utf-8"))["split"])
            self.assertTrue(md_path.read_text(encoding="utf-8").startswith("# GitHub expert"))

    def test_fail_on_harmful_gate_blocks_an_unsafe_policy(self):
        unsafe = synthetic(
            "gh-996", "How do I install this?", "I cannot find the install step anywhere.",
            {"category": "security", "priority": "P0", "action": "escalate",
             "risk_class": "high", "escalation_reason": "private channel"})
        with tempfile.TemporaryDirectory() as tmp:
            cases = Path(tmp) / "cases.jsonl"
            cases.write_text(json.dumps(unsafe) + "\n", encoding="utf-8")
            strict = self._run("--cases", str(cases), "--split", "dev",
                               "--format", "json", "--fail-on-harmful")
            self.assertEqual(1, strict.returncode)
            self.assertIn("harmful action", strict.stderr)
            relaxed = self._run("--cases", str(cases), "--split", "dev", "--format", "json")
            self.assertEqual(0, relaxed.returncode)
            self.assertEqual(1, json.loads(relaxed.stdout)["harmful_count"])


if __name__ == "__main__":
    unittest.main()
