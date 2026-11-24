# Growth Worker and Domain State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Move remaining growth-domain state into the transactional runtime and provide a supervised one-shot/continuous worker for GitHub, Gmail, recovery and heartbeat.

**Architecture:** A generic SQLite domain-state store preserves JSON compatibility while supporting atomic mutations. Source-event projection and conversation updates share one connection/transaction. A worker isolates connector failures and records one heartbeat per cycle.

**Tech Stack:** Python 3.9 stdlib, SQLite, `threading.Event`, existing GitHub/Gmail adapters, `unittest`.

## Global Constraints

- Existing JSON files are compatibility projections, not a second authority after migration.
- No nested SQLite transactions during source projection; pass the active connection explicitly.
- One connector failure must not prevent unrelated recovery/sync work.
- Long-running services start only through explicit CLI; MCP cannot start them.
- Tests use temporary state, one worker cycle and fake connectors.

---

### Task 1: Transactional domain state

**Files:**
- Modify: `core/growth_runtime.py`, `core/growth_engagement.py`, `core/growth_opportunities.py`, `core/growth_assets.py`, `core/growth_experiments.py`, `core/github_events.py`, `core/gmail_events.py`
- Test: `tests/test_growth_domain_state.py`

**Interfaces:**
- Produces: `load_domain_state(...)`, `update_domain_state(..., connection=None)`.
- Domain IDs: service ID for engagement/opportunities; `service/asset` and `service/experiment` for assets/experiments.

- [x] **Step 1: Write concurrent mutation and migration tests**

```python
with ThreadPoolExecutor(max_workers=2) as pool:
    list(pool.map(lambda row: growth_engagement.import_interactions(data, sid, [row]), rows))
self.assertEqual(2, len(growth_engagement.list_conversations(data, sid)[0]["interaction_ids"]))
```

Test concurrent opportunity imports and experiment observations, JSON migration on first read, and SQLite remaining authoritative when a compatibility JSON file is removed.

- [x] **Step 2: Run tests before migration**

Run: `python3 -m unittest tests.test_growth_domain_state -v`
Expected: lost updates or missing domain-state functions.

- [x] **Step 3: Implement shared-connection domain transactions**

```python
with transaction(connection):
    state = load_or_migrate_json()
    result = mutator(state)
    save_sqlite_and_json_projection()
```

Pass the source-event connection into engagement updates so raw event, projection and processed marker commit together.

- [x] **Step 4: Run all growth-domain regressions**

Run: `python3 -m unittest tests.test_growth_domain_state tests.test_growth_engagement tests.test_growth_opportunities tests.test_growth_assets tests.test_growth_experiments tests.test_github_events tests.test_gmail_api -v`
Expected: PASS without nested transaction errors.

### Task 2: Supervised worker and operating commands

**Files:**
- Create: `core/growth_worker.py`
- Modify: `core/growth_cli.py`, `core/cycle_scheduler.py`, `mcp_server.py`
- Test: `tests/test_growth_worker.py`, `tests/test_growth_scheduler.py`

**Interfaces:**
- Produces: `run_once(...)`, `serve(...)`, `growth gmail-sync`, `gmail-watch-renew`, `gmail-pubsub-pull`, `operator-serve [--once]`, `worker-status`, and `operator-tick` cycle job.

- [x] **Step 1: Write failure-isolation and heartbeat tests**

```python
result = growth_worker.run_once(
    data, sid, github_repo="o/r", gmail_sync=True, account="default")
self.assertTrue(result["components"]["github"]["ok"])
self.assertFalse(result["components"]["gmail"]["ok"])
self.assertIn("last_run_at", growth_worker.status(data, sid))
```

Test watch due/renewal, acknowledgement after sync, one-cycle CLI operation, scheduler reuse and MCP refusal to start a continuous worker.

- [x] **Step 2: Run tests before worker exists**

Run: `python3 -m unittest tests.test_growth_worker -v`
Expected: import failure.

- [x] **Step 3: Implement isolated components and heartbeat**

```python
for component in enabled_components:
    try:
        results[component.name] = {"ok": True, "result": component.run()}
    except Exception as exc:
        results[component.name] = {"ok": False, "error_type": type(exc).__name__}
save_heartbeat(results)
```

Renew watch only when expiry is within 24 hours; run Gmail sync before Pub/Sub pull; optional GitHub webhook server has a controllable lifecycle.

- [x] **Step 4: Run worker, CLI, scheduler and full regressions**

Run: `python3 -m unittest tests.test_growth_worker tests.test_growth_cli tests.test_growth_scheduler tests.test_growth_mcp -v`
Expected: PASS; one failing connector does not block the others.
