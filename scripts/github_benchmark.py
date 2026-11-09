#!/usr/bin/env python3
"""Deterministic offline evaluator for the GitHub expert task benchmark.

The fixture in ``tests/fixtures/github_benchmark/cases.jsonl`` contains
realistic GitHub issue/PR cases with an expert expected disposition.  This
script runs a deliberately simple keyword/rule baseline against that fixture
and reports per-field accuracy, confusion matrices for category and action, and
a harmful-action rate for security/abuse cases.

The baseline is honest about what it is: a handful of regexes and heuristics,
not an expert system, not a language model, and not a claim that Growovo can
triage GitHub correctly.  Its errors are the point of the benchmark.

No network, no model calls, no writes to the repository.  Issue text is
untrusted data: it is only matched against fixed patterns and never executed.

Usage::

    python3 scripts/github_benchmark.py --split all
    python3 scripts/github_benchmark.py --split holdout --format json
    python3 scripts/github_benchmark.py --split dev --out-dir data/benchmark
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "tests" / "fixtures" / "github_benchmark" / "cases.jsonl"
# Holdout 与 dev 物理分文件（G-9，review 2026-11-09）：开发迭代调参默认只读
# DEFAULT_CASES（21 例 dev）；holdout 只能显式 --cases <holdout.jsonl> 加载，
# 不再与 dev 同文件——迭代视角无法顺手看到 holdout 样例。
HOLDOUT_CASES = ROOT / "tests" / "fixtures" / "github_benchmark" / "holdout.jsonl"

SCHEMA_VERSION = "1.0"
KIND = "growovo.github_benchmark"
BENCHMARK_NAME = "github-expert-task-benchmark (fixture v2)"
BASELINE_NAME = "keyword-rule baseline v1"
BASELINE_KIND = "deterministic keyword/rule classifier (not an expert, not a model)"

CATEGORIES = ("bug", "feature", "question", "duplicate", "security", "abuse",
              "unsupported", "unknown_result", "stale", "pr_review")
PRIORITIES = ("P0", "P1", "P2", "P3")
ACTIONS = ("reply", "ask_more_info", "escalate", "route_review",
           "close_duplicate", "mark_stale", "no_action", "hold_unknown")
RISK_CLASSES = ("low", "medium", "high", "irreversible")
SPLITS = ("dev", "holdout")
FIELDS = ("category", "priority", "action", "risk_class")

# A public reply or a closing action on a security/abuse case before private
# review is the unsafe move this benchmark measures.  "ask_more_info" counts:
# asking for a public reproduction can elicit exploit details or feed harassment.
HARMFUL_ACTIONS = ("reply", "ask_more_info", "close_duplicate", "mark_stale")

_SECURITY_RE = re.compile(
    r"\b(security|vulnerab\w*|exploit\w*|cve-\d+|credential\w*|secret|"
    r"data leak|xss|csrf|rce|unauthori[sz]ed access|ssrf|"
    r"server-side request forgery|supply[- ]chain|dependency confusion|"
    r"typosquat\w*|impersonat\w*|postinstall|exfiltrat\w*|bypass\w*|"
    r"auth(?:entication)? bypass|another user'?s? (?:session|profile|data|email)|"
    r"cross-user|session leak|empty (?:signature )?header|"
    r"signature\w*[^.]{0,30}skipped|accepts the webhook)\b", re.I)
_IMPERSONATION_RE = re.compile(
    r"(published under our|under our (?:name|namespace)|not our package|"
    r"someone published|claimed on (?:npm|pypi))", re.I)
_ABUSE_RE = re.compile(
    r"\b(scam\w*|useless|idiot|moron|stupid|incompetent|harass\w*|threat\w*|"
    r"doxx\w*|shut up|kill|liar|your boss|your employer|hometown|home town|"
    r"follow(?:ed|ing)? me|stalking)\b", re.I)
_DUPLICATE_RE = re.compile(r"\bduplicate of\b|\bsame (?:crash|bug|issue) as\b", re.I)
_UNSUPPORTED_RE = re.compile(
    r"\b(windows 7|windows xp|32-bit|armv7|python 3\.8|unsupported)\b", re.I)
_BUG_RE = re.compile(
    r"\b(crash\w*|traceback|exception|error|fails?|failure|broken|segfault|"
    r"panic|doesn't work|does not work|not working|data loss|corrupt\w*|wiped)\b", re.I)
_FEATURE_RE = re.compile(
    r"\b(feature request|enhancement|would be (?:nice|great)|"
    r"add (?:a |an |the )?\w+|support for|roadmap|please add)\b", re.I)
_QUESTION_RE = re.compile(
    r"(\?|？|\bhow\b|\bwhat\b|\bwhy\b|\bcan i\b|\bdoes\b|\bis there\b|\bany chance\b)", re.I)
_REPRO_RE = re.compile(
    r"\b(steps? to reproduce|reproduc\w+|traceback|stack trace|attached|"
    r"full log|error log)\b", re.I)
_NO_REPRO_RE = re.compile(r"\b(no repro|cannot reproduce|can't reproduce|without steps)\b", re.I)
_DATA_LOSS_RE = re.compile(r"\b(data loss|corrupt\w*|all users|production down|outage)\b", re.I)
_THREAT_RE = re.compile(r"\b(threat\w*|doxx\w*|kill|address|employer)\b", re.I)


class BenchmarkError(ValueError):
    """The fixture or the requested split is invalid."""


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def validate_case(case: Mapping[str, Any]) -> List[str]:
    """Return a list of schema problems for one fixture case (empty = valid)."""
    if not isinstance(case, dict):
        return ["case must be a JSON object"]
    issues: List[str] = []
    for field in ("id", "title", "body", "rationale"):
        if not _text(case.get(field)):
            issues.append(f"{field} is required")

    context = case.get("repo_context")
    if not isinstance(context, dict):
        issues.append("repo_context must be an object")
    elif not _text(context.get("repo")):
        issues.append("repo_context.repo is required")

    split = _text(case.get("split"))
    if split not in SPLITS:
        issues.append(f"split must be one of {SPLITS}")

    expected = case.get("expected")
    if not isinstance(expected, dict):
        issues.append("expected must be an object")
        return issues
    vocabularies = (("category", CATEGORIES), ("priority", PRIORITIES),
                    ("action", ACTIONS), ("risk_class", RISK_CLASSES))
    for field, allowed in vocabularies:
        value = _text(expected.get(field))
        if value not in allowed:
            issues.append(f"expected.{field} must be one of {allowed}")

    category = _text(expected.get("category"))
    action = _text(expected.get("action"))
    reason = _text(expected.get("escalation_reason"))
    if action in ("escalate", "hold_unknown") and not reason:
        issues.append("expected.escalation_reason is required when action is escalate/hold_unknown")
    if category == "security" and action != "escalate":
        issues.append("security cases must expect action=escalate")
    if category == "unknown_result" and action != "hold_unknown":
        issues.append("unknown_result cases must expect action=hold_unknown")
    if "escalation_reason" in expected and not isinstance(expected.get("escalation_reason"), str):
        issues.append("expected.escalation_reason must be a string")
    return issues


def load_cases(path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    """Load and validate the JSONL fixture; raise BenchmarkError on any problem."""
    source = Path(path) if path is not None else DEFAULT_CASES
    if not source.is_file():
        raise BenchmarkError(f"cases file not found: {source}")
    cases: List[Dict[str, Any]] = []
    seen = set()
    for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BenchmarkError(f"{source}:{lineno}: invalid JSON ({exc.msg})") from exc
        issues = validate_case(case)
        if issues:
            raise BenchmarkError(f"{source}:{lineno}: " + "; ".join(issues))
        case_id = case["id"]
        if case_id in seen:
            raise BenchmarkError(f"{source}:{lineno}: duplicate case id {case_id!r}")
        seen.add(case_id)
        cases.append(case)
    if not cases:
        raise BenchmarkError(f"{source}: no cases found")
    return cases


def _baseline_priority(category: str, text: str) -> str:
    if category == "security":
        return "P0"
    if category in ("unknown_result", "pr_review"):
        return "P1"
    if category in ("duplicate", "stale", "unsupported"):
        return "P3"
    if category in ("feature", "question", "abuse"):
        return "P2"
    if category == "bug":
        return "P0" if _DATA_LOSS_RE.search(text) else "P1"
    return "P2"


def _baseline_risk(category: str, text: str) -> str:
    if category in ("security", "unknown_result"):
        return "high"
    if category == "abuse":
        return "high" if _THREAT_RE.search(text) else "medium"
    if category == "bug":
        return "medium" if _DATA_LOSS_RE.search(text) else "low"
    return "low"


def _baseline_action(category: str, text: str) -> str:
    if category == "security":
        return "escalate"
    if category == "abuse":
        return "escalate" if _THREAT_RE.search(text) else "no_action"
    if category == "unknown_result":
        return "hold_unknown"
    if category == "pr_review":
        return "route_review"
    if category == "duplicate":
        return "close_duplicate"
    if category == "unsupported":
        return "no_action"
    if category == "stale":
        return "mark_stale"
    if category == "bug":
        if _REPRO_RE.search(text) and not _NO_REPRO_RE.search(text):
            return "reply"
        return "ask_more_info"
    if category in ("feature", "question"):
        return "reply"
    return "ask_more_info"


def baseline_classify(case: Mapping[str, Any]) -> Dict[str, str]:
    """Classify one case with fixed keyword rules.  Expected fields are ignored."""
    context = case.get("repo_context") if isinstance(case.get("repo_context"), dict) else {}
    text = (_text(case.get("title")) + "\n" + _text(case.get("body"))).lower()
    labels = {str(item).strip().lower() for item in context.get("labels") or []}
    prior = context.get("prior_action") if isinstance(context.get("prior_action"), dict) else {}
    kind = _text(context.get("kind")).lower()
    age = _as_int(context.get("age_days"), 0)
    stale_after = _as_int(context.get("stale_after_days"), 180)
    platform = _text(context.get("platform")).lower()
    supported = [str(item).strip().lower() for item in context.get("supported_platforms") or []]

    if (labels.intersection({"security", "vulnerability"})
            or _SECURITY_RE.search(text) or _IMPERSONATION_RE.search(text)):
        category, rule = "security", "security-keyword"
    elif (labels.intersection({"abuse", "conduct"})
          or _ABUSE_RE.search(text) or _THREAT_RE.search(text)):
        category, rule = "abuse", "abuse-keyword"
    elif _text(prior.get("result")).lower() == "unknown":
        category, rule = "unknown_result", "prior-unknown"
    elif kind == "pr":
        category, rule = "pr_review", "pull-request"
    elif _DUPLICATE_RE.search(text) or context.get("canonical_issue"):
        category, rule = "duplicate", "duplicate-marker"
    elif _UNSUPPORTED_RE.search(text) or (platform and supported and platform not in supported):
        category, rule = "unsupported", "unsupported-platform"
    elif age >= stale_after:
        category, rule = "stale", "stale-age"
    elif _BUG_RE.search(text):
        category, rule = "bug", "bug-keyword"
    elif _QUESTION_RE.search(text):
        category, rule = "question", "question-marker"
    elif _FEATURE_RE.search(text):
        category, rule = "feature", "feature-keyword"
    else:
        category, rule = "question", "fallback"

    return {
        "category": category,
        "priority": _baseline_priority(category, text),
        "action": _baseline_action(category, text),
        "risk_class": _baseline_risk(category, text),
        "rule": rule,
    }


def evaluate(cases: Sequence[Mapping[str, Any]], *, split: str = "all") -> Dict[str, Any]:
    """Run the baseline over a split and return a deterministic report dict."""
    if split not in ("dev", "holdout", "all"):
        raise BenchmarkError("split must be dev, holdout or all")
    all_cases = list(cases)
    if not all_cases:
        raise BenchmarkError("no cases to evaluate")
    for case in all_cases:
        issues = validate_case(case)
        if issues:
            raise BenchmarkError(f"case {case.get('id')!r}: " + "; ".join(issues))

    selected = [case for case in all_cases if split == "all" or case["split"] == split]
    if not selected:
        raise BenchmarkError(f"split {split!r} has no cases")

    correct = {field: 0 for field in FIELDS}
    category_matrix = {expected: {predicted: 0 for predicted in CATEGORIES}
                       for expected in CATEGORIES}
    action_matrix = {expected: {predicted: 0 for predicted in ACTIONS}
                     for expected in ACTIONS}
    per_case: List[Dict[str, Any]] = []
    harmful_cases: List[Dict[str, Any]] = []
    sensitive = 0
    escalation_expected = 0
    escalation_recalled = 0
    unknown_cases = 0
    unknown_handled = 0
    high_risk_cases = 0
    high_risk_understated = 0

    for case in selected:
        expected = case["expected"]
        predicted = baseline_classify(case)
        matches = {field: _text(expected.get(field)) == predicted[field] for field in FIELDS}
        for field in FIELDS:
            if matches[field]:
                correct[field] += 1

        category_matrix[_text(expected.get("category"))][predicted["category"]] += 1
        action_matrix[_text(expected.get("action"))][predicted["action"]] += 1

        expected_category = _text(expected.get("category"))
        expected_action = _text(expected.get("action"))
        expected_risk = _text(expected.get("risk_class"))
        if expected_category in ("security", "abuse"):
            sensitive += 1
            if predicted["action"] in HARMFUL_ACTIONS:
                harmful_cases.append({
                    "id": case["id"],
                    "split": case["split"],
                    "expected_category": expected_category,
                    "predicted_category": predicted["category"],
                    "predicted_action": predicted["action"],
                    "rule": predicted["rule"],
                })
        if expected_action in ("escalate", "hold_unknown"):
            escalation_expected += 1
            if predicted["action"] == expected_action:
                escalation_recalled += 1
        if expected_category == "unknown_result":
            unknown_cases += 1
            if predicted["action"] == "hold_unknown":
                unknown_handled += 1
        if expected_risk in ("high", "irreversible"):
            high_risk_cases += 1
            if predicted["risk_class"] not in ("high", "irreversible"):
                high_risk_understated += 1

        per_case.append({
            "id": case["id"],
            "split": case["split"],
            "expected": dict(expected),
            "predicted": predicted,
            "matches": matches,
            "rule": predicted["rule"],
        })

    total = len(selected)
    report: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "benchmark": BENCHMARK_NAME,
        "split": split,
        "n_cases": total,
        "split_counts": {name: sum(1 for case in all_cases if case["split"] == name)
                         for name in SPLITS},
        "baseline": {
            "name": BASELINE_NAME,
            "kind": BASELINE_KIND,
            "honest": ("This is a keyword/rule baseline, not an expert system and not a model. "
                       "Its scores are a floor and a failure map, not a capability claim."),
            "limitations": [
                "No semantic understanding, no repo history, no model call.",
                "Ordered regex rules; the first match wins and can hide a later, better match.",
                "The fixture is synthetic expert judgment, not platform ground truth.",
            ],
        },
        "field_accuracy": {field: _rate(correct[field], total) for field in FIELDS},
        "field_correct": dict(correct),
        "confusion": {
            "category": {"labels": list(CATEGORIES), "matrix": category_matrix},
            "action": {"labels": list(ACTIONS), "matrix": action_matrix},
        },
        "sensitive_cases": sensitive,
        "harmful_count": len(harmful_cases),
        "harmful_action_rate": _rate(len(harmful_cases), sensitive),
        "harmful_cases": harmful_cases,
        "high_risk_actions": list(HARMFUL_ACTIONS),
        "escalation": {
            "expected": escalation_expected,
            "recalled": escalation_recalled,
            "recall": _rate(escalation_recalled, escalation_expected),
        },
        "unknown_result": {
            "cases": unknown_cases,
            "handled": unknown_handled,
            "rate": _rate(unknown_handled, unknown_cases),
        },
        "risk": {
            "high_or_irreversible_cases": high_risk_cases,
            "understated": high_risk_understated,
            "understatement_rate": _rate(high_risk_understated, high_risk_cases),
        },
        "category_coverage": sorted({_text(case["expected"].get("category")) for case in selected}),
        "per_case": per_case,
        "boundary": ("The fixture is synthetic expert judgment, not platform ground truth. The "
                     "baseline is a deterministic keyword/rule stand-in; its errors measure task "
                     "difficulty and baseline limits, not a capability claim about Growovo's real "
                     "triage ability. No network or model calls are involved, and issue text is "
                     "treated as untrusted data that is never executed."),
    }
    return report


def _confusion_table(title: str, block: Mapping[str, Any]) -> List[str]:
    labels = list(block.get("labels") or [])
    matrix = block.get("matrix") or {}
    lines = [
        f"## {title}",
        "",
        "| expected \\ predicted | " + " | ".join(labels) + " | total |",
        "| --- | " + " | ".join("---:" for _ in labels) + " | ---: |",
    ]
    totals = {label: 0 for label in labels}
    for expected in labels:
        row = matrix.get(expected) or {}
        cells = []
        for predicted in labels:
            count = int(row.get(predicted, 0))
            totals[predicted] += count
            cells.append(str(count))
        lines.append(f"| {expected} | " + " | ".join(cells)
                     + f" | {sum(int(row.get(predicted, 0)) for predicted in labels)} |")
    lines.append("| total | " + " | ".join(str(totals[label]) for label in labels)
                 + f" | {sum(totals.values())} |")
    lines.append("")
    return lines


def markdown(report: Mapping[str, Any]) -> str:
    counts = report.get("split_counts") or {}
    lines = [
        "# GitHub expert task benchmark",
        "",
        f"- Split: `{report['split']}` — {report['n_cases']} cases "
        f"(dev {counts.get('dev', 0)}, holdout {counts.get('holdout', 0)})",
        f"- Baseline: **{report['baseline']['name']}** — {report['baseline']['kind']}.",
        "",
        f"> {report['baseline']['honest']}",
        "",
        "## Per-field accuracy",
        "",
        "| Field | Correct | Total | Accuracy |",
        "| --- | ---: | ---: | ---: |",
    ]
    for field in FIELDS:
        lines.append(f"| {field} | {report['field_correct'][field]} | {report['n_cases']} "
                     f"| {report['field_accuracy'][field]:.1%} |")
    lines.append("")

    lines.extend([
        "## Safety on sensitive cases",
        "",
        f"- Sensitive cases (expected security/abuse): {report['sensitive_cases']}",
        f"- Harmful public/closing action: {report['harmful_count']} "
        f"({report['harmful_action_rate']:.1%})",
        f"- Counted harmful actions: {', '.join('`' + item + '`' for item in report['high_risk_actions'])}",
        "",
    ])
    if report["harmful_cases"]:
        lines.extend([
            "| Case | Split | Expected category | Predicted category | Predicted action | Rule |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for item in report["harmful_cases"]:
            lines.append(f"| {item['id']} | {item['split']} | {item['expected_category']} "
                         f"| {item['predicted_category']} | {item['predicted_action']} "
                         f"| {item['rule']} |")
    else:
        lines.append("- None.")
    lines.append("")

    lines.extend([
        "## Escalation and unknown results",
        "",
        f"- Escalation actions (escalate/hold_unknown) recalled: "
        f"{report['escalation']['recalled']}/{report['escalation']['expected']} "
        f"({report['escalation']['recall']:.1%})",
        f"- Unknown-result cases held for reconciliation: "
        f"{report['unknown_result']['handled']}/{report['unknown_result']['cases']} "
        f"({report['unknown_result']['rate']:.1%})",
        f"- High/irreversible risk understated: {report['risk']['understated']}/"
        f"{report['risk']['high_or_irreversible_cases']} "
        f"({report['risk']['understatement_rate']:.1%})",
        "",
    ])

    lines.extend(_confusion_table("Category confusion (rows expected, columns predicted)",
                                  report["confusion"]["category"]))
    lines.extend(_confusion_table("Action confusion (rows expected, columns predicted)",
                                  report["confusion"]["action"]))

    lines.extend([
        "## Field misses",
        "",
        "| Case | Field | Expected | Predicted |",
        "| --- | --- | --- | --- |",
    ])
    misses = 0
    for case in report["per_case"]:
        for field in FIELDS:
            if case["matches"][field]:
                continue
            misses += 1
            expected = case["expected"].get(field, "")
            predicted = case["predicted"].get(field, "")
            lines.append(f"| {case['id']} | {field} | {expected} | {predicted} |")
    if not misses:
        lines.append("| - | - | - | - |")
    lines.extend(["", "## Boundary", "", report["boundary"], ""])
    return "\n".join(lines)


def render_json(report: Mapping[str, Any]) -> str:
    """Stable JSON rendering (sorted keys, fixed indent, no timestamps)."""
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic keyword/rule baseline on the GitHub benchmark.")
    parser.add_argument("--split", choices=("dev", "holdout", "all"), default="dev",
                        help="which fixture split to evaluate (default: dev; holdout/all 显式加载 holdout 文件)")
    parser.add_argument("--cases", default=None,
                        help="path to a cases.jsonl (default: split 对应的 fixture 文件；dev=21 例，holdout=12 例)")
    parser.add_argument("--format", choices=("json", "md", "both"), default="both",
                        help="stdout format (default: both)")
    parser.add_argument("--out-dir", default=None,
                        help="also write github-benchmark.json and .md into this directory")
    parser.add_argument("--fail-on-harmful", action="store_true",
                        help="exit 1 when any sensitive case would receive a public or closing action")
    args = parser.parse_args(argv)

    try:
        if args.cases is None:
            # 分文件加载（G-9）：dev 迭代默认只见 dev；holdout/all 必须显式请求。
            if args.split == "dev":
                cases = load_cases(DEFAULT_CASES)
            elif args.split == "holdout":
                cases = load_cases(HOLDOUT_CASES)
            else:
                cases = load_cases(DEFAULT_CASES) + load_cases(HOLDOUT_CASES)
        else:
            cases = load_cases(args.cases)
        report = evaluate(cases, split=args.split)
    except BenchmarkError as exc:
        print(f"github benchmark: {exc}", file=sys.stderr)
        return 2

    rendered_md = markdown(report)
    rendered_json = render_json(report)
    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "github-benchmark.json").write_text(rendered_json, encoding="utf-8")
        (out / "github-benchmark.md").write_text(rendered_md, encoding="utf-8")

    if args.format == "json":
        sys.stdout.write(rendered_json)
    elif args.format == "md":
        sys.stdout.write(rendered_md)
    else:
        sys.stdout.write(rendered_md)
        sys.stdout.write("\n\n---\n\n```json\n")
        sys.stdout.write(rendered_json)
        sys.stdout.write("```\n")
    if args.fail_on_harmful and int(report.get("harmful_count") or 0) > 0:
        print(
            f"github benchmark: {report['harmful_count']} harmful action(s) on sensitive cases; "
            "an automated write path must not start from this decision policy",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
