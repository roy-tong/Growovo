# GitHub App Community Operator and Recoverable Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build one recoverable GitHub Issue/Comment operations path that survives process restart, duplicate entry points, manual handoff and post-submit disconnection without losing progress or repeating external writes.

**Architecture:** SQLite is authoritative for growth-service state, attempts, events and durable source events; existing JSON/JSONL files remain compatibility projections. GitHub App source ingestion is separate from decision and execution, while replies continue through `action_runner` and `standard_pipeline`.

**Tech Stack:** Python 3.9 stdlib, `sqlite3`, `urllib`, `hmac`, `hashlib`, `subprocess` + system OpenSSL, `unittest`; no new package dependency.

## Global Constraints

- Preserve all existing uncommitted files; do not reset, commit or push.
- Writes remain live by default and use `action_runner` / `standard_pipeline`; explicit preview does not satisfy live acceptance.
- Never log App private keys, installation tokens or webhook secrets.
- Platform payloads are untrusted data. No real account writes in tests.
- `waiting_manual` and `waiting_verification` are non-runnable states; only ready work is selected.
- A post-submit exception becomes `waiting_verification`, never an automatic retry.
- This slice covers GitHub Issues and Issue Comments. Other event types remain explicitly unsupported.

---

### Task 1: Transactional growth-service state

**Files:**
- Create: `core/growth_runtime.py`
- Modify: `core/growth_service.py`
- Test: `tests/test_growth_runtime.py`, `tests/test_growth_service.py`

**Interfaces:**
- Produces: `load_service(data_dir, service_id)`, `create_service(data_dir, service)`, `update_service(data_dir, service_id, mutator, event=None)`, `events(data_dir, service_id)`.
- Consumes: existing `AtomicJSONStore` and `AppendOnlyJSONL` for compatibility projections.

- [x] **Step 1: Write concurrent-update and restart tests**

```python
def test_two_writers_keep_both_action_updates(self):
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda action_id: growth_service.record_action(
            self.data, self.sid, action_id, "completed", by="tester"),
            ("diagnose-product", "baseline-observe")))
    loaded = growth_service.load(self.data, self.sid)
    statuses = {row["id"]: row["status"] for row in loaded["actions"]}
    self.assertEqual("completed", statuses["diagnose-product"])
    self.assertEqual("completed", statuses["baseline-observe"])
    self.assertEqual(2, len([e for e in growth_runtime.events(self.data, self.sid)
                             if e["kind"] == "action_recorded"]))
```

- [x] **Step 2: Run the test and confirm the old implementation loses one update or raises lock contention**

Run: `python3 -m unittest tests.test_growth_runtime.GrowthRuntimeTest.test_two_writers_keep_both_action_updates -v`
Expected: FAIL before the runtime exists or before `growth_service` uses it.

- [x] **Step 3: Implement SQLite transactions and JSON compatibility projections**

```python
connection.execute("BEGIN IMMEDIATE")
row = connection.execute(
    "SELECT service_json, version FROM services WHERE service_id = ?",
    (service_id,)).fetchone()
service = json.loads(row[0])
result = mutator(service)
connection.execute(
    "UPDATE services SET service_json = ?, version = version + 1, updated_at = ? "
    "WHERE service_id = ? AND version = ?",
    (json.dumps(service, ensure_ascii=False), now, service_id, row[1]))
connection.execute(
    "INSERT INTO events(event_id, service_id, kind, payload_json, created_at) "
    "VALUES (?, ?, ?, ?, ?)", (event_id, service_id, event["kind"], payload, now))
```

Create indexes for service events and attempts. Configure `PRAGMA busy_timeout=5000` and foreign keys; writes use `BEGIN IMMEDIATE`, without changing journal mode on every connection. Load existing JSON once when a service has no SQLite row; subsequent reads use SQLite.

- [x] **Step 4: Run runtime and existing growth-service tests**

Run: `python3 -m unittest tests.test_growth_runtime tests.test_growth_service -v`
Expected: PASS, including two persistent `action_recorded` events and both completed statuses.

### Task 2: Atomic claims, independent waiting states and recovery

**Files:**
- Modify: `core/growth_runtime.py`, `core/growth_service.py`, `core/growth_execution.py`, `core/growth_cli.py`
- Test: `tests/test_growth_runtime.py`, `tests/test_growth_execution.py`

**Interfaces:**
- Produces: `claim_action(data_dir, service_id, action_id, worker, payload, lease_seconds=300)`, `recover_expired(data_dir, now=None)`, `resolve_action(data_dir, service_id, action_id, status, by, evidence)`.
- Consumes: existing `growth_execution.run(...)` and `growth_service.record_action(...)`.

- [x] **Step 1: Write failing selection, duplicate-submit and crash-recovery tests**

```python
self.assertEqual("waiting_manual", self.action("x-publish")["status"])
self.assertNotEqual("x-publish", growth_service.next_action(self.data, self.sid)["id"])

with mock.patch("core.action_runner.run_action",
                return_value={"result": "indeterminate", "evidence": ""}) as send:
    first = growth_execution.run(self.data, self.sid, "github-engage", by="worker-a",
                                 payload=self.github_payload())
    second = growth_execution.run(self.data, self.sid, "github-engage", by="worker-b",
                                  payload=self.github_payload())
self.assertEqual("waiting_verification", first["status"])
self.assertEqual("waiting_verification", second["status"])
self.assertEqual(1, send.call_count)
```

Add a two-thread barrier test proving only one caller reaches `run_action`. Add a lease-expiry test proving a channel attempt becomes `waiting_verification`, while a local attempt becomes `pending` within its retry budget.

- [x] **Step 2: Run the new tests and confirm current behavior fails**

Run: `python3 -m unittest tests.test_growth_execution tests.test_growth_runtime -v`
Expected: FAIL because manual handoff is `in_progress`, duplicate calls have no runtime attempt, and indeterminate becomes `blocked`.

- [x] **Step 3: Implement attempts and waiting states**

```python
attempt_id = "attempt-" + uuid.uuid4().hex
connection.execute(
    "INSERT INTO attempts(attempt_id, service_id, action_id, worker, intent_sha256, "
    "status, lease_expires_at, external, evidence_json, created_at, updated_at) "
    "VALUES (?, ?, ?, ?, ?, 'claimed', ?, ?, '{}', ?, ?)",
    (attempt_id, service_id, action_id, worker, intent_hash, lease_until,
     int(action["kind"] == "channel"), now, now))
action["status"] = "in_progress"
action["active_attempt_id"] = attempt_id
```

Extend service statuses with `waiting_manual` and `waiting_verification`. `next_action` selects only dependency-ready `pending` rows. Manual handoff records `waiting_manual`; indeterminate records `waiting_verification`. Finishing any action also closes its active attempt in the same transaction.

- [x] **Step 4: Run focused recovery and legacy execution tests**

Run: `python3 -m unittest tests.test_growth_runtime tests.test_growth_execution tests.test_growth_service tests.test_growth_scheduler -v`
Expected: PASS; duplicate external sends, lost progress and repeated manual selection are zero.

### Task 3: GitHub App authentication and durable source ingestion

**Files:**
- Create: `core/github_app.py`, `core/github_events.py`
- Modify: `core/growth_runtime.py`, `core/growth_engagement.py`
- Test: `tests/test_github_app.py`, `tests/test_github_events.py`

**Interfaces:**
- Produces: `GitHubAppClient.from_env()`, `.installation_token()`, `.sync_issue_interactions(slug)`, `.find_issue_comment(slug, number, comment_id)`, `verify_signature(secret, body, header)`, `ingest(data_dir, service_id, event, payload, delivery_id, secret)`.
- Consumes: runtime source-event table and `growth_engagement.import_interactions(...)`.

- [x] **Step 1: Write authentication, signature, normalization and duplicate-delivery tests**

```python
with self.assertRaises(ValueError):
    github_events.verify_signature(b"secret", b"{}", "sha256=bad")

first = github_events.ingest(self.data, "svc-test", "issue_comment",
                             self.payload(), "delivery-1", b"secret")
second = github_events.ingest(self.data, "svc-test", "issue_comment",
                             self.payload(), "delivery-1", b"secret")
self.assertTrue(first["stored"])
self.assertTrue(second["duplicate"])
self.assertEqual(1, len(growth_engagement.list_conversations(self.data, "svc-test")))
```

Test JWT creation with mocked OpenSSL and installation-token exchange with a fake transport. Test paginated Issue/Comment synchronization without network.

- [x] **Step 2: Run the new source tests**

Run: `python3 -m unittest tests.test_github_app tests.test_github_events -v`
Expected: FAIL because the modules do not exist.

- [x] **Step 3: Implement App auth and durable ingestion**

```python
expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
if not hmac.compare_digest(expected, str(signature_header or "")):
    raise ValueError("invalid GitHub webhook signature")

connection.execute(
    "INSERT OR IGNORE INTO source_events(source, source_event_id, service_id, event_type, "
    "payload_json, processed, received_at) VALUES ('github_app', ?, ?, ?, ?, 0, ?)",
    (delivery_id, service_id, event_type, json.dumps(payload), received_at))
```

Use `openssl dgst -sha256 -sign PRIVATE_KEY_PATH` with argv, never a shell. Require `MKT_GITHUB_APP_ID`, `MKT_GITHUB_APP_INSTALLATION_ID`, `MKT_GITHUB_APP_PRIVATE_KEY_PATH`; allow an explicitly supplied short-lived `MKT_GITHUB_APP_INSTALLATION_TOKEN`. Persist before processing and replay unprocessed rows idempotently.

- [x] **Step 4: Run source and runtime tests**

Run: `python3 -m unittest tests.test_github_app tests.test_github_events tests.test_growth_runtime -v`
Expected: PASS; duplicate delivery creates no duplicate conversation.

### Task 4: Verified GitHub reply and indeterminate reconciliation

**Files:**
- Modify: `channels/github.py`, `core/action_runner.py`, `core/growth_execution.py`, `core/github_app.py`
- Test: `tests/test_github_app.py`, `tests/test_growth_execution.py`

**Interfaces:**
- Produces: `github.reply(..., token=None)`, `github.find_replies(slug, number, text, token=None)`, `growth_execution.reconcile(data_dir, service_id, action_id, by, payload=None)`.
- Consumes: existing `standard_pipeline`, App installation token, runtime waiting states.

- [x] **Step 1: Write failing readback and reconciliation tests**

```python
self.assertEqual("committed", record["result"])
self.assertEqual(comment_url, record["evidence"])
self.assertEqual(expected_comment_id, readback["id"])
self.assertEqual(text, readback["body"])

with mock.patch("channels.github.find_replies", return_value=[self.comment()]):
    got = growth_execution.reconcile(self.data, self.sid, "github-engage", by="tester")
self.assertEqual("completed", got["status"])
self.assertEqual(self.comment()["html_url"], got["evidence"]["receipt"])
```

Also test zero and multiple matches: both remain non-runnable and no POST occurs.

- [x] **Step 2: Run the tests against current reply behavior**

Run: `python3 -m unittest tests.test_github_app tests.test_growth_execution -v`
Expected: FAIL because current confirmation trusts only POST response shape and no reconcile entry exists.

- [x] **Step 3: Implement second-GET verification and read-only recovery**

```python
def _confirm(item, timeout=0):
    observed = _api("/repos/%s/issues/comments/%s" % (slug, resp["id"]), token=token)
    return (observed.get("html_url") == resp.get("html_url")
            and observed.get("body") == text
            and (observed.get("user") or {}).get("id")
                == (resp.get("user") or {}).get("id"))
```

Pass the App token only through keyword arguments. Store sanitized evidence. Reconciliation performs GET requests only; unique matching comment completes the original action, while zero/multiple matches preserve `waiting_verification` or escalate to `waiting_manual`.

- [x] **Step 4: Run GitHub and execution regression tests**

Run: `python3 -m unittest tests.test_github_app tests.test_github_events tests.test_growth_execution tests.test_execution_integrity_integration -v`
Expected: PASS; no reconciliation path issues a POST.

### Task 5: CLI, MCP, scheduled synchronization and operator documentation

**Files:**
- Modify: `core/growth_cli.py`, `core/cycle_scheduler.py`, `mcp_server.py`, `docs/growth-service.md`, `docs/feature-status.md`, `AGENTS.md` if documented behavior changes
- Test: `tests/test_growth_cli.py`, `tests/test_growth_mcp.py`, `tests/test_growth_scheduler.py`

**Interfaces:**
- Produces: `growth github-sync`, `growth github-webhook-serve`, `growth reconcile`, `growth recover`, `growth attempts`, and `github-sync` scheduler job.
- Consumes: all prior public functions through the same CLI/MCP namespace.

- [x] **Step 1: Write cross-entry persistence and scheduler tests**

```python
started = self.invoke("start", file=self.goal_path)
first = self.invoke("run", service_id=self.sid, action="diagnose-product", by="tester")
resumed = self.invoke("attempts", service_id=self.sid)
self.assertEqual("completed", resumed["attempts"][0]["status"])
self.assertEqual(first["attempt_id"], resumed["attempts"][0]["id"])
```

Test MCP reads the same attempts, and `github-sync` imports normalized rows without requiring a webhook process.

- [x] **Step 2: Run entry-point tests before wiring**

Run: `python3 -m unittest tests.test_growth_cli tests.test_growth_mcp tests.test_growth_scheduler -v`
Expected: FAIL for unknown actions/job kind.

- [x] **Step 3: Wire the shared handlers and document operation**

```python
elif args.growth_action == "reconcile":
    _emit(growth_execution.reconcile(
        data_dir, args.service_id, args.action, by=args.by,
        payload={"target": args.target, "text": args.text}))
elif args.growth_action == "recover":
    _emit(growth_runtime.recover_expired(data_dir))
```

Document required App environment variables, least-privilege setup, webhook signature handling, local-only default bind `127.0.0.1`, scheduled reconciliation, and the fact that real write canary remains separately gated.

- [x] **Step 4: Run complete verification**

Run: `python3 -m unittest discover -s tests`
Expected: all tests PASS.

Run: `python3 mkt.py demo`
Expected: exit 0.

Run: `python3 mkt.py voice plain --file docs/growth-service.md && git diff --check`
Expected: no coined metaphor nouns and no whitespace errors.

Run a local end-to-end fixture through signed Webhook → persisted source event → conversation → claimed reply with fake transport → independent GET → receipt → duplicate submission. Confirm one external POST, one conversation, one completed action and resumable attempts after reopening the SQLite database. Real GitHub App installation/write canary remains explicitly unverified unless separately authorized and executed.
