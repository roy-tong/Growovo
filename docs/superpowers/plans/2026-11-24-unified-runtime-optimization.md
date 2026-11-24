# Unified Runtime Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Finish the approved unified-runtime refactor without a big-bang rewrite, preserving existing connectors and evidence while eliminating duplicate scheduling, JSON write amplification, browser idle work and account-level execution races.

**Architecture:** Normalize SQLite state, make compatibility JSON read-through, unify scheduling behind one ready path, serialize account execution lanes, and route writes through one gateway. Migrate in small TDD slices with real canaries after the full offline suite.

**Tech Stack:** Python 3.9 stdlib, SQLite, existing `action_runner`, existing browser/API connectors, `unittest`.

## Global Constraints

- Preserve every uncommitted file and all historical records; no reset, commit or push.
- No new package dependencies, no distributed workflow engine and no second business state machine.
- External writes go through `action_runner`; unknown results are reconciled read-only, never blindly retried.
- No production worker or real account writes in unit tests. Live canary only on `roy-tong/Growovo` and the authorized Gmail test account.
- Legacy files remain readable, but runtime state is authoritative. Compatibility JSON is not written synchronously inside online transactions.

---

### Task 1: Normalize conversation storage and lazy projections

**Files:**
- Modify: `core/growth_runtime.py`, `core/growth_engagement.py`
- Test: `tests/test_growth_domain_state.py`, `tests/test_growth_engagement.py`

**Interfaces:**
- Produces: `import_engagement(...)`, `load_engagement(...)`, `update_engagement(...)`.
- Keeps public `growth_engagement` behavior, conversation IDs, message history and shared source-event connections.

- [x] **Step 1: Add failing tests for per-message persistence and lazy JSON**

```python
import_interactions(data, sid, [first, second])
self.assertEqual(2, message_count(data, sid))
path.unlink()
self.assertEqual(2, len(list_conversations(data, sid)[0]["interaction_ids"]))
```

Verify old JSON migration, per-message IDs, no synchronous snapshot during import, and projection rebuilt on read.

- [x] **Step 2: Run tests and confirm old whole-document behavior**

Run: `python3 -m unittest tests.test_growth_domain_state tests.test_growth_engagement -v`
Expected: missing normalized functions and synchronous JSON writes.

- [x] **Step 3: Implement normalized conversations/messages and read-through projection**

Create `conversation_states` and `conversation_messages` with unique `(service_id, conversation_id)` and `(service_id, conversation_id, external_id)` keys. Migrate old JSON/domain state once. Preserve shared `connection` for source-event atomicity; do not write compatibility JSON in `update_domain_state`—rebuild it on read.

- [x] **Step 4: Run all engagement/source-event tests**

Run: `python3 -m unittest tests.test_growth_domain_state tests.test_growth_engagement tests.test_github_events tests.test_gmail_api -v`
Expected: PASS with no nested transactions and identical public behavior.

### Task 2: Schema initialization and bounded connector reads

**Files:**
- Modify: `core/growth_runtime.py`, `core/gmail_api.py`, `core/github_app.py`
- Test: `tests/test_runtime_storage.py`, `tests/test_gmail_api.py`, `tests/test_github_app.py`, `tests/test_github_discussions.py`

**Interfaces:**
- Produces: one-time schema migration and WAL setup, token-refresh lock, bounded `map` for message/comment reads.

- [x] **Step 1: Add failing initialization and concurrent-read tests**

Assert schema initialization executes once per data directory, concurrent first connections do not fail, and Gmail/GitHub results keep deterministic IDs/order with a small worker bound.

- [x] **Step 2: Run tests and identify current repeated initialization/sequential reads**

Run: `python3 -m unittest tests.test_runtime_storage tests.test_gmail_api tests.test_github_app -v`
Expected: repeated initialization or no bounded parallel implementation.

- [x] **Step 3: Implement one-time startup migration and bounded readers**

Use a process lock plus filesystem initialization lock, then enable WAL once. Use `ThreadPoolExecutor(max_workers=4)` for independent reads and a lock for installation-token refresh. Preserve source-event transactions and per-source failure handling.

- [x] **Step 4: Run concurrency and connector regressions**

Run: `python3 -m unittest tests.test_runtime_storage tests.test_gmail_api tests.test_github_app tests.test_github_discussions -v`
Expected: PASS with identical records and no token-refresh race.

### Task 3: Local readiness before browser work and unified scheduler

**Files:**
- Modify: `core/engagement_cycle.py`, `core/growth_worker.py`, `core/cycle_scheduler.py`
- Modify: `/Users/tongxiarui/Library/Application Support/growovo/night-wave-scheduler.py`
- Test: `tests/test_night_stability_matrix.py`, `tests/test_growth_scheduler.py`

**Interfaces:**
- Produces: one ready path for legacy waves and connector jobs, cheap `planned=0` diagnosis before browser collection.

- [x] **Step 1: Add failing no-browser-on-empty tests**

```python
with mock.patch("core.engagement_cycle.collect_inbox_report") as collect:
    got = cycle.run(empty_data, go=True, wave_id="04-0105")
self.assertEqual(0, got["planned"])
collect.assert_not_called()
```

Verify `growth_worker.run_once` can execute a legacy wave through the same runtime and old scheduler only wakes it.

- [x] **Step 2: Run tests and confirm current browser-first order**

Run: `python3 -m unittest tests.test_night_stability_matrix tests.test_growth_scheduler -v`
Expected: inbox is collected before zero-eligible diagnosis.

- [x] **Step 3: Implement local readiness and unified ready adapter**

Load queue/stop state and compute local eligibility before `collect_inbox_report`. Return the normal zero-action wave summary when nothing is executable. `growth_worker.run_once` accepts `wave_id`, and `operator-tick` uses that single path; the external night script only invokes it.

- [x] **Step 4: Run scheduler and historical replay tests**

Run: `python3 -m unittest tests.test_night_stability_matrix tests.test_growth_scheduler tests.test_growth_worker -v`
Expected: zero eligible work does not touch the browser; all other jobs keep their behavior.

### Task 4: Account execution lanes and single Action Gateway

**Files:**
- Create: `core/action_gateway.py`
- Modify: `core/growth_runtime.py`, `core/growth_execution.py`, `core/engagement_cycle.py`, `core/queue_runner.py`, `mkt.py`
- Test: `tests/test_night_stability_matrix.py`, `tests/test_growth_execution.py`, `tests/test_execution_integrity_integration.py`

**Interfaces:**
- Produces: `action_gateway.execute(...)`, account/route leases shared by growth actions, legacy waves and queue writes.

- [x] **Step 1: Add failing cross-entry account-race tests**

Run growth execution and a legacy wave concurrently for the same account; assert the second waits or returns `account_busy`, and independent read-only work proceeds.

- [x] **Step 2: Run tests and confirm independent lock files cannot coordinate**

Run: `python3 -m unittest tests.test_night_stability_matrix tests.test_growth_execution -v`
Expected: account execution is not leased across entry points.

- [x] **Step 3: Implement shared account/route leases**

Add `execution_leases` to SQLite. `action_gateway.execute` acquires the lane outside the network call and releases it in `finally`. `growth_execution`, legacy `engagement_cycle` and `queue_runner` call the gateway; mkt single-action entry uses it too.

- [x] **Step 4: Run all execution integrity tests**

Run: `python3 -m unittest tests.test_night_stability_matrix tests.test_growth_execution tests.test_execution_integrity_integration tests.test_browser_attach_runner -v`
Expected: no cross-entry account write race; existing approval/Gate/quota/receipt behavior is preserved.

### Task 5: Full acceptance and real canary

**Files:**
- Modify: `docs/growth-service.md`, `docs/feature-status.md`, `docs/platform-automation-matrix.md`
- Test: `tests/test_night_stability_matrix.py`, full suite

- [x] **Step 1: Run the full offline suite and demo**

Run: `python3 -m unittest discover -s tests && python3 mkt.py demo && git diff --check`
Expected: all tests and demo PASS.

- [x] **Step 2: Replay the real night scenario twice**

Run the fourth wave twice with `cycle run --now` and compare ledger/OperationsLog hashes. Expect `planned=0`, `committed=0`, no duplicate external write and no source-version error.

- [x] **Step 3: Run real GitHub and Gmail write canaries**

Post one uniquely identified comment to `roy-tong/Growovo` through the App, read it independently, and send one uniquely identified email to the authorized Gmail test account, then independently read thread, recipient and body. Expect one external object per channel and no duplicate on repeat verification.

- [x] **Step 4: Update evidence and outstanding boundaries**

Record tests, canary URLs/receipt IDs, live readback and any remaining X/legacy anomaly. Do not mark unexecuted channels or production worker live-proven.
