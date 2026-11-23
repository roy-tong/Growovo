# Growovo Progressive Architecture Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Consolidate Growovo's recently added capabilities behind one runtime, policy, storage, planning, scheduling, content, result-projection and CLI architecture without breaking existing commands or external-write safety.

**Architecture:** Keep a standard-library modular monolith and the existing `action_runner`/`pipeline` final safety path. Add focused application contracts and ports, then make legacy modules delegate incrementally; retain compatibility facades instead of deleting old paths.

**Tech Stack:** Python 3.9+, stdlib dataclasses/JSON/JSONL/fcntl/hashlib, existing unittest suite, existing Playwright/browser and SMTP adapters.

## Global Constraints

- No new runtime dependency and no real account sends in tests.
- Preserve pause, DNC/suppression, approval, identity, quota/safety caps, readback, ledger, unknown-result and no-retry behavior.
- Do not merge grow-v2 goal lifecycle with nightly wave policy; both consume the same runtime contracts.
- Do not merge ledger and operations-log semantics; correlate them with run/attempt/action ids.
- All timestamps are ISO 8601; all clocks are injectable; all new files use focused responsibilities.
- Content adapters produce drafts only and never choose destinations, approve, send, commit or push.
- No automatic migration of live runtime state; migration commands default to dry-run.
- No `git commit` or `git push`; each task checkpoint is its failing/passing tests plus `git diff --check`.

---

### Task 1: Runtime Context and Storage Primitives

**Files:**
- Create: `core/runtime.py`
- Create: `core/storage.py`
- Test: `tests/test_runtime_storage.py`
- Modify: `core/engagement_cycle.py`

**Interfaces:**
- Produces: `RunContext.create(data_dir, campaign, wave, now=None)`, `RunContext.child(...)`, `AtomicJSONStore`, `AppendOnlyJSONL`, `FileLock`.
- Consumes: existing `engagement_cycle.stable_id`, manifest attempt ids and data directories.

- [x] Write tests proving unique attempt ids, stable correlation fields, atomic writes, lock exclusion, and fail-loud malformed JSON/JSONL.
- [x] Run `python3 -m unittest tests.test_runtime_storage` and verify failure because the modules do not exist.
- [x] Implement immutable `RunContext`, atomic JSON snapshots with schema/version validation, append-only JSONL with lock, and nonblocking `FileLock`.
- [x] Make engagement-queue loading fail loud on malformed existing JSON while preserving a missing-file empty queue; add a regression test proving malformed input cannot be overwritten.
- [x] Run runtime/storage tests plus `tests.test_engagement_cycle` and verify all pass.

### Task 2: Effective Policy and Unified Decision Explanation

**Files:**
- Create: `core/effective_policy.py`
- Test: `tests/test_effective_policy.py`
- Modify: `core/engagement_cycle.py`
- Modify: `core/content_rules.py`

**Interfaces:**
- Produces: `EffectivePolicy.compile(policy, now, data_dir=None)`, `.decide(row, stage)`, `.explain()`, `.sha256`.
- Consumes: existing content gates, quota safety rows, channel holds and future contact/cooldown contracts.

- [x] Write tests for canonical hashes, precedence (`suppress/pause > identity/approval > cooldown > safety > content > quota > ranking`), missing caps, and stable explanations.
- [x] Verify tests fail before implementation.
- [x] Implement pure policy compilation and ordered decision records without performing I/O or authorization itself.
- [x] Add the compiled policy hash and decision records to cycle plans/manifests while preserving legacy `skipped` reasons.
- [x] Run policy, content-rule and engagement-cycle tests.

### Task 3: Indexed Planning and Durable Queue Events

**Files:**
- Create: `core/ledger_index.py`
- Create: `core/queue_store.py`
- Test: `tests/test_ledger_queue_performance.py`
- Modify: `core/engagement_cycle.py`
- Modify: `core/inbox_tracking.py`

**Interfaces:**
- Produces: `LedgerIndex.build(records)`, `.already(channel,target,results)`, `.blocked(channel,target)`, `QueueStore.load/save/consume/recover`.
- Consumes: existing ledger rows and queue stable ids.

- [x] Add parity tests against current `Ledger.already/no_retry` and queue recovery fixtures; add a 500-row/5000-ledger benchmark guard.
- [x] Verify the new modules are absent and the recovery test fails.
- [x] Implement one-pass ledger indexes and a queue journal recording consume events before the last snapshot, with deterministic recovery and no external-action replay.
- [x] Make engagement planning use the index while preserving output order and skip reasons; preload inbox terminal identities once per merge.
- [x] Run parity/performance tests and compare the 500x5000 benchmark with the recorded 0.397s baseline.

### Task 4: One Job Kernel with Legacy Scheduler Adapters

**Files:**
- Create: `core/job_kernel.py`
- Test: `tests/test_job_kernel.py`
- Modify: `core/cycle_scheduler.py`
- Modify: `core/scheduler.py`

**Interfaces:**
- Produces: `JobStateMachine.transition(job,result)`, `JobStore`, `is_dependency_satisfied`.
- Consumes: current cycle-job states and legacy schedule `active/running/needs_review/completed` states.

- [x] Test duplicate ticks, interrupted running jobs, dependency failure, indeterminate no-retry, lock deferral and expiry.
- [x] Implement one state machine and persistent history contract used by both scheduler surfaces.
- [x] Keep `mkt schedule` and `cycle jobs-*` schemas working through adapters; do not auto-migrate existing files.
- [x] Run `tests.test_scheduler`, `tests.test_cycle_scheduler` and new fault-injection tests.

### Task 5: Outreach Contracts and Content Pipeline

**Files:**
- Create: `core/outreach.py`
- Create: `core/content_pipeline.py`
- Create: `core/contribution.py`
- Test: `tests/test_outreach_pipeline.py`
- Modify: `core/koc_qualification.py`
- Modify: `mkt.py`

**Interfaces:**
- Produces: `AccountMap`, `HookEvidence`, `DraftBundle`, `Contribution`, `prepare_draft(...)`, `record_contribution(...)`.
- Consumes: versioned JSON policy exports of B2B/warm/expert-review/my-voice, existing voice fact-preservation and human approval.

- [x] Write tests for company-to-person requirement, persona/replacement limits, same-day hook freshness, warm/expert hard gates, my-voice scene routing and fact/disclosure preservation.
- [x] Test contribution states `received→triaged→patch_proposed→reviewed→merged→credited→followed_up`; merged requires a real change reference and reviewer, never auto-commits.
- [x] Implement stdlib dataclasses and append-only contribution events; policy exports carry source version/hash, not absolute skill dependencies.
- [x] Add dry-run CLI preparation and feedback commands; existing approval and execution remain unchanged.
- [x] Run outreach, voice, reply-case and deliverable tests.

### Task 6: Rebuildable Result Projections

**Files:**
- Create: `core/projections.py`
- Test: `tests/test_projections.py`
- Modify: `core/cycle_report.py`
- Modify: `core/business_observations.py`

**Interfaces:**
- Produces: `ProjectionRegistry.build(name,data_dir,...)`, `correlation_report(context)`, deterministic source hashes.
- Consumes: existing wave, ledger, receipt, inbox, business and contribution records.

- [x] Test that repeated builds are byte-equivalent and missing sources remain explicit unknowns.
- [x] Implement named projections for wave, inbox, receipts, readiness, KOC and business outcomes without changing their source-of-truth semantics.
- [x] Add run/attempt/action correlation output where records contain ids; never fabricate missing legacy ids.
- [x] Run projection and all existing report/receipt/business tests against real wave fixtures.

### Task 7: Thin Modern CLI Boundary

**Files:**
- Create: `core/command_registry.py`
- Create: `core/cli_runtime.py`
- Test: `tests/test_command_registry.py`
- Modify: `mkt.py`

**Interfaces:**
- Produces: lazy `CommandSpec(name,parser,handler)` registration and a runtime command group.
- Consumes: existing `cmd_cycle`/`cmd_supply` compatibility handlers and new runtime/projection/outreach handlers.

- [x] Test lazy imports, duplicate-name refusal, help output and legacy command parity.
- [x] Move new runtime/outreach/projection parser and dispatch logic out of `mkt.py`; keep old parser branches working.
- [x] Do not mechanically rewrite all 7427 lines; the modern path becomes thin and legacy commands remain explicit compatibility surfaces.
- [x] Run CLI-contract and help tests.

### Task 8: End-to-End Verification and Documentation

**Files:**
- Modify: `docs/feature-status.md`
- Modify: `Growovo/ai输出/Growovo-会话复盘与产品优化-20261123.md`
- Modify: `Growovo/ai输出/README.md`

- [x] Run all focused tests and `python3 -m unittest discover -s tests`.
- [x] Run `python3 mkt.py demo`, `python3 mkt.py cycle report` and structural receipt audits against real data.
- [x] Re-run the plan benchmark and record actual results, not estimates.
- [x] Run `git diff --check`, syntax checks and plain-language checks; inspect `git status` for generated artifacts.
- [x] Document completed architecture, compatibility facades, migration boundaries and remaining live-only canaries.
