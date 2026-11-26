# Execution Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guide users from incomplete business input to a validated execution strategy, then prepare and gate wave-specific recipients and content through a resumable preparation scheduler.

**Architecture:** Add a pure strategy compiler, a pure preparation planner, and a persistent bounded preparation runtime. Reuse existing scheduler, queue approval, supply miners, host-adapter controls, and action gateway; strategy-bound jobs add a strategy hash without changing legacy jobs.

**Tech Stack:** Python 3 stdlib, `unittest`, existing Growovo JSON/SQLite state and CLI patterns.

## Global Constraints

- No real account sends in tests.
- Preserve `action_gateway → action_runner → pipeline`, account leases, quotas, suppression, identity checks, stop states, and unknown-receipt handling.
- `growovo.execution-strategy/1` is data-driven; do not hardcode OVO product names or the 300-action allocation into compiler rules.
- Every external item needs target, context, qualification, complete text, and measurement IDs. Candidate count is not eligible supply.
- Original content requires a current-hash `my-voice` review before it is ready.
- Preparation candidates and approved wave queues are separate snapshots.
- Legacy jobs without `strategy` retain their current behavior.
- User-facing text must be plain language. Update `docs/feature-status.md` and `docs/growth-service.md` to describe actual implemented behavior and remaining production acceptance boundaries.
- Work only in the isolated worktree. Do not commit, push, merge, or modify the dirty main checkout.

---

### Task 1: Guided input and draft advancement

**Files:**
- Create: `core/output_strategy.py`
- Test: `tests/test_output_strategy_guide.py`

**Interfaces:**
- Produces: `guide(payload: dict) -> dict`, `apply_answer(payload: dict, field: str, value: Any) -> dict`, `strategy_digest(document: dict) -> str`.
- `guide` returns `ready`, `status`, `answered_fields`, `remaining_fields`, and optional `next_question` with `field`, `question`, `why`, `example`, `suggested_default`, and `answer_command`.

- [ ] **Step 1: Write failing guide tests**

```python
import unittest
from core import output_strategy


class GuidedIntakeTest(unittest.TestCase):
    def test_minimal_draft_returns_one_useful_question(self):
        result = output_strategy.guide({"product": {"name": "Askovo"}})
        self.assertFalse(result["ready"])
        self.assertEqual("needs_input", result["status"])
        self.assertEqual("product.url", result["next_question"]["field"])
        self.assertIn("为什么", result["next_question"]["why"])
        self.assertIn("example", result["next_question"])
        self.assertIn("output-guide", result["next_question"]["answer_command"])
        self.assertGreater(len(result["remaining_fields"]), 1)
        self.assertNotIn(result["next_question"]["field"], result["answered_fields"])

    def test_answers_advance_without_losing_existing_input(self):
        draft = output_strategy.apply_answer({"product": {"name": "Askovo"}},
            "product.url", "https://github.com/roy-tong/Askovo")
        self.assertEqual("Askovo", draft["product"]["name"])
        result = output_strategy.guide(draft)
        self.assertEqual("outcomes.primary", result["next_question"]["field"])

    def test_answer_rejects_unknown_field(self):
        with self.assertRaisesRegex(ValueError, "未知引导字段"):
            output_strategy.apply_answer({}, "unknown.field", 1)
```

- [ ] **Step 2: Verify the tests fail for the missing module**

Run: `python3 -m unittest tests.test_output_strategy_guide -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.output_strategy'`.

- [ ] **Step 3: Implement ordered question specs and nested answers**

Implement `FIELD_SPECS` for product URL, primary outcome, audience, asset, window, accounts/platforms, and action budget. Each spec has a predicate and a question. `apply_answer` supports dotted paths, parses no strings implicitly, rejects unknown fields, and deep-copies the input. `guide` reports only the next unanswered question; `remaining_fields` means the ordered fields still needing answers, not a list of every nested schema property.

- [ ] **Step 4: Verify guide tests and existing growth-goal tests**

Run: `python3 -m unittest tests.test_output_strategy_guide tests.test_growth_goals -v`
Expected: all PASS.

---

### Task 2: Strategy compiler, materialization lint, and renderer

**Files:**
- Modify: `core/output_strategy.py`
- Create: `examples/execution-strategy-300.json`
- Test: `tests/test_output_strategy_compile.py`

**Interfaces:**
- Consumes: `guide`, `apply_answer`, and `strategy_digest` from Task 1.
- Produces: `compile_strategy(payload: dict) -> dict`, `lint_strategy(document: dict, require_ready: bool = False) -> list[str]`, `render_strategy(document: dict) -> str`.
- Produces `growovo.execution-strategy/1` with `campaign`, `outcomes`, `action_decomposition`, `waves`, `items`, `preparation`, and `readiness`.

- [ ] **Step 1: Write failing allocation and materialization tests**

```python
import copy
import json
import unittest
from pathlib import Path
from core import output_strategy


class StrategyCompileTest(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(Path(
            "examples/execution-strategy-300.json").read_text(encoding="utf-8"))

    def test_reference_campaign_closes_at_300(self):
        plan = output_strategy.compile_strategy(self.payload)
        self.assertEqual("growovo.execution-strategy/1", plan["schema"])
        self.assertEqual(300, plan["readiness"]["required"])
        self.assertEqual(10, len(plan["waves"]))
        for wave in plan["waves"]:
            self.assertEqual(30, wave["total"])
            for product in ("askovo", "growovo", "verovo"):
                self.assertEqual(1, wave["allocations"][product]["x/publish"])
        self.assertEqual({"x/publish": 30, "x/reply": 180,
                          "github/reply": 60, "x/message": 30},
                         plan["readiness"]["by_action"])

    def test_missing_target_and_text_are_not_ready(self):
        plan = output_strategy.compile_strategy(self.payload)
        plan["items"] = [{"id": "bad", "wave_id": "w01", "product": "askovo",
                          "platform": "x", "action": "reply", "goal_ids": ["demo"],
                          "target": {}, "content": {"text": ""}, "measurement": {}}]
        errors = output_strategy.lint_strategy(plan, require_ready=True)
        self.assertIn("target_missing", errors)
        self.assertIn("text_missing", errors)
        self.assertIn("measurement_missing", errors)

    def test_dm_recipients_are_unique_across_products(self):
        plan = output_strategy.compile_strategy(self.payload)
        item = {"wave_id": "w01", "platform": "x", "action": "message",
                "target": {"handle": "@same", "qualification": {"dm_open": True}},
                "content": {"text": "specific"}, "measurement": {"execution_event": "sent"}}
        plan["items"] = [dict(item, id="a", product="askovo", goal_ids=["demo"]),
                         dict(item, id="b", product="growovo", goal_ids=["demo"])]
        self.assertIn("duplicate_dm_handle", output_strategy.lint_strategy(plan))

    def test_renderer_exposes_goals_waves_todos_targets_and_measurement(self):
        text = output_strategy.render_strategy(output_strategy.compile_strategy(self.payload))
        for expected in ("目标拆解", "波次矩阵", "备料 TODO", "对象与正文", "验收事件"):
            self.assertIn(expected, text)
```

- [ ] **Step 2: Verify the compiler tests fail**

Run: `python3 -m unittest tests.test_output_strategy_compile -v`
Expected: FAIL because `compile_strategy` is absent and the example is absent.

- [ ] **Step 3: Add the data-driven reference input and compiler**

The example declares three products, per-wave mixes, ten hourly slots, goals, assets, and preparation adapters. Compiler arithmetic must derive totals from input. It must reject unknown platforms/actions, negative counts, non-integral product totals, windows without time zones, and campaigns where original quotas exceed the total. Blueprint items may be empty; `readiness` then reports `required=300`, `materialized=0`, and `eligible=0`.

- [ ] **Step 4: Add materialization and render implementation**

`lint_strategy(require_ready=True)` requires exact item coverage, real target/context/qualification/text/measurement, content hashes, and no duplicates. DM eligibility accepts only explicit `dm_open`, `prior_interaction`, or `requested_contact` evidence. `render_strategy` includes empty-supply gaps honestly.

- [ ] **Step 5: Verify compiler and guide tests**

Run: `python3 -m unittest tests.test_output_strategy_guide tests.test_output_strategy_compile -v`
Expected: all PASS.

---

### Task 3: Preparation TODO graph and review gates

**Files:**
- Create: `core/preparation_plan.py`
- Modify: `core/output_strategy.py`
- Test: `tests/test_preparation_plan.py`

**Interfaces:**
- Produces: `build_preparation_plan(strategy: dict) -> dict` with `schema=growovo.preparation-plan/1` and a `tasks` array.
- Each task has `id`, `kind`, `runner`, `depends_on`, `supply_keys`, `config`, and initial `status`.
- `output_strategy.compile_strategy` embeds this document under `preparation`.

- [ ] **Step 1: Write failing DAG and review tests**

```python
import json
import unittest
from pathlib import Path
from core import output_strategy
from core import preparation_plan


class PreparationPlanTest(unittest.TestCase):
    def setUp(self):
        payload = json.loads(Path("examples/execution-strategy-300.json").read_text())
        self.strategy = output_strategy.compile_strategy(payload)

    def test_all_independent_roots_start_ready(self):
        tasks = self.strategy["preparation"]["tasks"]
        by_id = {row["id"]: row for row in tasks}
        self.assertEqual("ready", by_id["generate-original-titles"]["status"])
        self.assertEqual("ready", by_id["mine-reply-topics"]["status"])
        self.assertEqual("ready", by_id["mine-koc"]["status"])
        self.assertEqual("ready", by_id["mine-dm-prospects"]["status"])
        self.assertEqual("pending", by_id["review-originals-my-voice"]["status"])

    def test_original_review_requires_my_voice_and_current_hash(self):
        tasks = self.strategy["preparation"]["tasks"]
        review = next(row for row in tasks if row["id"] == "review-originals-my-voice")
        self.assertEqual("my-voice", review["config"]["reviewer"])
        self.assertEqual(["generate-original-content"], review["depends_on"])
        self.assertTrue(review["config"]["requires_source_sha256"])

    def test_reply_and_dm_branches_have_explicit_stages(self):
        ids = {row["id"] for row in self.strategy["preparation"]["tasks"]}
        self.assertLess(ids.index("qualify-reply-targets"), ids.index("draft-replies"))
        self.assertLess(ids.index("qualify-dm-prospects"), ids.index("draft-dms"))
```

- [ ] **Step 2: Verify DAG tests fail**

Run: `python3 -m unittest tests.test_preparation_plan -v`
Expected: FAIL with `ModuleNotFoundError: core.preparation_plan`.

- [ ] **Step 3: Implement the declarative DAG**

Generate task IDs and dependencies exactly as in the approved design. `mine-reply-topics`, `mine-koc`, and content generation use runner/config from the strategy, with `manual` when no adapter is declared. Local qualification and freeze stages are deterministic. `build_preparation_plan` does not read network or write files.

- [ ] **Step 4: Verify DAG and compiler tests**

Run: `python3 -m unittest tests.test_preparation_plan tests.test_output_strategy_compile -v`
Expected: all PASS.

---

### Task 4: Persistent preparation loop and wave snapshots

**Files:**
- Create: `core/preparation_runtime.py`
- Test: `tests/test_preparation_runtime.py`

**Interfaces:**
- Consumes: strategy and preparation plan from Tasks 2–3.
- Produces: `load_state(data_dir, strategy) -> dict`, `advance(data_dir, strategy, now, runner=None, max_tasks=2) -> dict`, `status(data_dir, strategy, wave_id="") -> dict`, `mark_wave_reconciled(data_dir, strategy, wave_id) -> dict`.
- Runner signature: `runner(task: dict, strategy: dict, state: dict) -> dict`; result supports `ok`, `items`, `review`, `reason`, and `waiting_input`.

- [ ] **Step 1: Write failing readiness-loop tests**

```python
import json
import tempfile
import unittest
from pathlib import Path
from core import output_strategy
from core import preparation_runtime


class PreparationRuntimeTest(unittest.TestCase):
    def setUp(self):
        payload = json.loads(Path("examples/execution-strategy-300.json").read_text())
        self.strategy = output_strategy.compile_strategy(payload)
        self.tmp = tempfile.TemporaryDirectory()

    def test_shortage_runs_only_missing_branches_then_freezes(self):
        calls = []
        def runner(task, strategy, state):
            calls.append(task["id"])
            if task["kind"] == "mine":
                return {"ok": True, "items": []}
            return {"ok": True, "items": []}
        result = preparation_runtime.advance(self.tmp.name, self.strategy,
            "2026-11-26T20:00:00+08:00", runner=runner, max_tasks=2)
        self.assertEqual("preparing", result["wave_status"])
        self.assertLessEqual(len(calls), 2)
        self.assertTrue(result["shortfall"])
        self.assertTrue(result["state"]["tasks"])

    def test_ready_wave_uses_immutable_snapshot_and_resumes_after_receipt(self):
        state = preparation_runtime.load_state(self.tmp.name, self.strategy)
        state["supply"] = {"w01": {"eligible": 30, "reviewed": 30}}
        state["waves"] = {"w01": {"status": "sent"}}
        preparation_runtime.save_state(self.tmp.name, self.strategy, state)
        result = preparation_runtime.advance(self.tmp.name, self.strategy,
            "2026-11-26T20:00:00+08:00", runner=lambda *args: self.fail("must pause"),
            max_tasks=2)
        self.assertEqual("waiting_wave_approval", result["wave_status"])
        snapshot = result["snapshot"]
        before = snapshot["sha256"]
        snapshot["items"].append({"id": "unapproved"})
        stored = preparation_runtime.status(self.tmp.name, self.strategy, "w01")
        self.assertEqual(before, stored["snapshot"]["sha256"])
        self.assertEqual(0, len(stored["snapshot"]["items"]))
        done = preparation_runtime.mark_wave_reconciled(
            self.tmp.name, self.strategy, "w01")
        self.assertEqual("preparing", done["wave_status"])

    def test_unknown_receipt_stops_preparation_from_masking_execution_problem(self):
        result = preparation_runtime.mark_wave_reconciled(
            self.tmp.name, self.strategy, "missing-wave")
        self.assertEqual("error", result["status"])
```

- [ ] **Step 2: Verify runtime tests fail**

Run: `python3 -m unittest tests.test_preparation_runtime -v`
Expected: FAIL with `ModuleNotFoundError: core.preparation_runtime`.

- [ ] **Step 3: Implement persistent attempts, bounded advancement, and readiness**

Use atomic JSON writes. Stable task attempts prevent duplicate artifact application. `advance` never executes more than `max_tasks`. `waiting_input` and `blocked` remain visible. Readiness is based on reviewed eligible items for the exact wave, not pool size. A due wave with shortages does not freeze or claim approval.

- [ ] **Step 4: Implement host/miner runner and review validation**

Provide a default runner for deterministic local tasks, configured existing supply miners, and authorized host adapters. Host output must match `growovo.preparation-result/1` and declared producer ID. `my-voice` review requires reviewer, verdict, source SHA-256, and issues/revisions. Missing adapters return `waiting_input` rather than success.

- [ ] **Step 5: Verify runtime and planner tests**

Run: `python3 -m unittest tests.test_preparation_runtime tests.test_preparation_plan -v`
Expected: all PASS.

---

### Task 5: CLI, strategy-bound approvals, scheduler advancement, and docs

**Files:**
- Modify: `core/growth_cli.py`
- Modify: `core/scheduled_scope.py`
- Modify: `core/cycle_scheduler.py`
- Modify: `docs/growth-service.md`
- Modify: `docs/feature-status.md`
- Test: `tests/test_output_strategy_cli.py`
- Test: `tests/test_preparation_scheduler.py`
- Modify: `tests/test_scheduled_stability.py`

**Interfaces:**
- Consumes all public interfaces from Tasks 1–4.
- Produces CLI `output-guide`, `output-plan`, `output-lint`, and `preparation-status`.
- Wave job optional fields: `strategy` (path), `strategy_sha256` (64 hex chars), and `preparation` (`enabled`, `max_tasks_per_tick`). Approval scope adds `strategy_sha256`.
- Produces `scheduled_scope.strategy_binding(config_path, job) -> dict` with `path`, `sha256`, and `item_ids`.

- [ ] **Step 1: Write failing CLI tests**

```python
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class OutputStrategyCliTest(unittest.TestCase):
    def test_guide_outputs_structured_next_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.json"
            draft.write_text(json.dumps({"product": {"name": "Askovo"}}))
            got = subprocess.run([sys.executable, "mkt.py", "growth", "output-guide",
                "--file", str(draft)], text=True, capture_output=True)
            self.assertEqual(0, got.returncode, got.stderr)
            payload = json.loads(got.stdout)
            self.assertEqual("product.url", payload["next_question"]["field"])

    def test_plan_and_lint_expose_supply_gap(self):
        got = subprocess.run([sys.executable, "mkt.py", "growth", "output-plan",
            "--file", "examples/execution-strategy-300.json"], text=True, capture_output=True)
        self.assertEqual(0, got.returncode, got.stderr)
        self.assertEqual(300, json.loads(got.stdout)["readiness"]["required"])
```

- [ ] **Step 2: Verify CLI tests fail**

Run: `python3 -m unittest tests.test_output_strategy_cli -v`
Expected: FAIL with `growth error: 未知 growth 动作 output-guide`.

- [ ] **Step 3: Implement CLI commands**

`output-guide --set FIELD --value JSON` updates the draft atomically and prints the next question. `--interactive` prompts one question at a time only when explicitly requested. `output-plan` emits JSON or Markdown; `--out` refuses overwrite using existing CLI conventions. `output-lint --require-ready` exits 2 when errors exist. `preparation-status` is read-only.

- [ ] **Step 4: Write failing strategy-approval and scheduler tests**

```python
def test_strategy_binding_rejects_changed_document(self):
    import json
    import tempfile
    from pathlib import Path
    from core import scheduled_scope
    with tempfile.TemporaryDirectory() as tmp:
        strategy = Path(tmp) / "execution-plan.json"
        strategy.write_text(json.dumps({"schema": "growovo.execution-strategy/1",
                                        "waves": [], "items": []}))
        digest = scheduled_scope.digest(json.loads(strategy.read_text()))
        job = {"strategy": strategy.name, "strategy_sha256": digest}
        self.assertEqual(digest, scheduled_scope.strategy_binding(
            Path(tmp) / "jobs.json", job)["sha256"])
        strategy.write_text(json.dumps({"schema": "growovo.execution-strategy/1",
                                        "waves": [], "items": [{"id": "changed"}]}))
        with self.assertRaisesRegex(scheduled_scope.ScopeError, "strategy_changed"):
            scheduled_scope.strategy_binding(Path(tmp) / "jobs.json", job)


def test_tick_advances_preparation_before_first_wave_is_due(self):
    import json
    import tempfile
    from pathlib import Path
    from unittest import mock
    from core import cycle_scheduler
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "jobs.json"
        config.write_text(json.dumps({"strategy": "execution-plan.json",
            "preparation": {"enabled": True, "max_tasks_per_tick": 1},
            "jobs": [{"id": "w01", "kind": "wave", "at": "2030-01-01T20:00:00+08:00",
                      "queue": "w01.json", "accounts": {"x": "owner"}}]}))
        with mock.patch("core.cycle_scheduler.preparation_runtime.advance",
                        return_value={"executed": 1, "external_writes": 0}) as advance:
            got = cycle_scheduler.tick(tmp, config, now="2029-01-01T20:00:00+08:00", go=True)
        self.assertEqual(1, advance.call_count)
        self.assertEqual(0, got["executed"])
```

Expected pre-implementation failures are missing `strategy_binding` and no preparation call.

- [ ] **Step 5: Implement strategy binding and bounded scheduler calls**

`scheduled_scope.approve` loads the strategy, verifies digest and wave item IDs, and stores the hash. `activate` rechecks it. `cycle_scheduler.tick` performs bounded preparation advancement for strategy-enabled configs, including before the first wave is due. Preparation work does not increment external wave execution counts. Dry-run returns preparation status without executing adapters. Existing no-strategy jobs follow the old path.

- [ ] **Step 6: Verify focused and scheduler regression tests**

Run: `python3 -m unittest tests.test_output_strategy_cli tests.test_preparation_scheduler tests.test_scheduled_stability tests.test_cycle_scheduler -v`
Expected: all PASS.

- [ ] **Step 7: Update operator documentation and run the required full verification**

Document guided input, JSON/Markdown outputs, TODO states, two approval levels, exact supply gaps, and production adapter/DM acceptance boundaries in `docs/growth-service.md` and `docs/feature-status.md`.

Run: `python3 -m unittest discover -s tests`
Expected: all tests PASS.

Run: `python3 mkt.py demo`
Expected: all five demo checks PASS with zero real account sends.

Run: `python3 mkt.py growth output-plan --file examples/execution-strategy-300.json --format md`
Expected: readable goal decomposition, ten-wave matrix, preparation TODO graph, and `required=300` with unmaterialized supply shown as a gap.
