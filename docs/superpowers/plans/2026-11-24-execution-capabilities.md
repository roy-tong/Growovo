# Account Action Capabilities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make intake and execution distinguish implemented, verified, authorized and currently available read/write/readback capabilities for every account, platform, action and route.

**Architecture:** Persist operator-supplied implementation and verification evidence in the existing SQLite runtime. Derive readiness rather than storing one mutable `ready` flag. `action_resolution` consumes structured capabilities when present and retains legacy explicit availability only for compatibility.

**Tech Stack:** Python 3.9 stdlib, SQLite, existing growth runtime and `unittest`.

## Global Constraints

- Verification evidence is required for every operation marked verified; implementation does not imply verification.
- Read, write and readback are evaluated independently.
- Expired/revoked authorization, pause, unavailable readback or missing evidence blocks execution without blocking unrelated accounts/routes.
- No credentials in capability records. No real account writes in tests.
- Existing callers without capability records retain their current explicit-availability behavior.

---

### Task 1: Persistent capability records

**Files:**
- Create: `core/execution_capabilities.py`
- Modify: `core/growth_runtime.py`
- Test: `tests/test_execution_capabilities.py`

**Interfaces:**
- Produces: `upsert(data_dir, record, by)`, `view(data_dir, account=None, platform=None)`, `plan_readiness(data_dir, plan, account="default")`.
- Record identity: `(account, platform, action, route)`; operations: `read`, `write`, `verify`.

- [x] **Step 1: Write independent operation and expiry tests**

```python
record = capability("github", "reply", "github_app",
                    implemented=["read", "write", "verify"], verified=["read", "write"])
upsert(self.data, record, by="tester")
row = view(self.data, account="default")[0]
self.assertTrue(row["operations"]["write"]["verified"])
self.assertFalse(row["operations"]["verify"]["verified"])
self.assertFalse(row["executable"])
```

Add cases requiring evidence for verified operations, rejecting expired/revoked authorization, isolating a paused account, and allowing a second healthy account.

- [x] **Step 2: Run tests and confirm failure before the module exists**

Run: `python3 -m unittest tests.test_execution_capabilities -v`
Expected: import failure or missing readiness fields.

- [x] **Step 3: Implement the SQLite table and derived view**

```python
PRIMARY KEY(account, platform, action, route)
```

Store implementation/verified operation arrays, operation availability, authorization status/scope/expiry, verification timestamp/evidence, adapter version and pause reason. Compute `authorized_now`, per-operation readiness and `executable` on every view; never persist a standalone executable flag.

- [x] **Step 4: Run persistent capability tests**

Run: `python3 -m unittest tests.test_execution_capabilities -v`
Expected: PASS, including restart, expiry and account isolation.

### Task 2: Intake and action-resolution integration

**Files:**
- Modify: `core/action_resolution.py`, `core/growth_service.py`, `core/growth_execution.py`, `core/growth_cli.py`, `mcp_server.py`
- Test: `tests/test_execution_capabilities.py`, `tests/test_growth_service.py`, `tests/test_growth_execution.py`, `tests/test_growth_cli.py`

**Interfaces:**
- Produces: `growth capabilities`, `growth capability-upsert --file RECORD --by OPERATOR`.
- Consumes: `view(...)` and `plan_readiness(...)` from Task 1.

- [x] **Step 1: Write failing resolution and intake tests**

```python
resolution = action_resolution.resolve(
    github_action(), {"capabilities": rows, "account": "default"})
self.assertEqual("manual_handoff", resolution["execution_mode"])
self.assertIn("verify_not_verified", resolution["missing"])
```

Test that a fully verified route selects `github_app`, an explicit capability set fails closed for missing records, service intake stores per-action readiness, and unrelated healthy work remains selectable.

- [x] **Step 2: Run integration tests before wiring**

Run: `python3 -m unittest tests.test_execution_capabilities tests.test_growth_execution tests.test_growth_service -v`
Expected: FAIL because structured capabilities are ignored.

- [x] **Step 3: Implement structured selection and intake readiness**

```python
if "capabilities" in context:
    for transport in preferred:
        row = match(account, channel, action, transport)
        if row and row["executable"]:
            return ready(transport)
    return blocked_or_manual(missing=missing)
```

Only an explicitly supplied capability set disables legacy boolean availability. Service intake records readiness without deleting existing actions; unavailable work explains missing evidence/authorization and follows the existing manual/blocked policy.

- [x] **Step 4: Wire CLI/MCP and run regression**

Run: `python3 -m unittest tests.test_execution_capabilities tests.test_growth_service tests.test_growth_execution tests.test_growth_cli tests.test_growth_mcp -v`
Expected: PASS. `growth capabilities` returns identical persisted rows through CLI and MCP; no token/private-key field is accepted.
