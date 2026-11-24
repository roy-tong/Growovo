# Growovo: exactly-once execution is the wrong goal

*Approval fingerprints, capability derivation, attempt leases, durable ingress, and independent readback for authorized execution.*

> Implementation boundary: this article covers the released Growovo core through 0.20.0 and an internal 24 November 2026 development tree. The SQLite growth runtime, GitHub App operator, Gmail two-way sync, and structured capability model described below are implemented and offline-tested in that development tree; they are not in a numbered release or on public `main` at the time of writing, and real GitHub App and Gmail write canaries have not been completed.

A POST times out after the server has already created a comment. What should the worker do next?

Retrying may create a duplicate. Marking the action as failed may record something that is no longer true. Reporting success would be optimistic in a different direction. The network cannot tell the worker which side of the timeout won the race, and no amount of local transaction logging changes that.

This is why “exactly-once execution” is the wrong objective for an agent that writes to external systems. The useful objectives are more concrete:

- do not execute work whose approved intent has changed;
- do not issue a second attempt while the first outcome is unknown;
- make inbound processing idempotent even when delivery is repeated;
- verify external state through an independent read;
- preserve an unresolved outcome until a person or a read-only reconciliation can decide it.

Growovo's recent work is organized around those objectives. The design is less glamorous than an autonomous growth daemon. It is also much more interesting from a reliability-engineering perspective.

## Approval covers bytes, identity, and location

A `human_approved=true` flag says nothing about what was approved. Growovo replaces that ambiguity with an execution fingerprint over four inputs:

```python
payload = {
    "plan": {k: v for k, v in plan.items() if k != "review"},
    "queues": resolved_queues_with_sorted_items,
    "workspace": resolved_workspace,
    "account": resolved_account,
}
fingerprint = sha256(canonical_json(payload))
```

The canonical form is Python JSON serialization with sorted keys and compact separators. Queue items are sorted before hashing, and resolved queue paths are included. Targets, bodies, actions, platforms, account, and workspace therefore all affect the fingerprint.

Approval is not just a signature over this payload. Before writing it, `approve_plan` runs:

```text
lint_plan
→ load every declared queue (a missing queue is an error)
→ lint_queue for each bound queue
→ campaign.preflight for content and target gates
→ compute execution fingerprint
→ write plan.review
→ save an approval snapshot
→ append an event to approvals.jsonl
```

The resulting record includes `approved_by`, `approved_at`, optional expiry, `fingerprint_scope=plan+queue+account+workspace`, and a snapshot path. Execution recomputes the fingerprint. Editing the body, replacing a recipient, changing accounts, or moving the workspace invalidates the old approval.

There is an explicit compatibility exception: legacy plans with `human_approved=true` but no fingerprint can still run and are marked `legacy`. That exception matters for a security review. It preserves old workspaces, but it is not equivalent to the stronger fingerprint guarantee.

## Capability is a derived view, not a boolean

The development tree adds another layer between intent and execution. A capability record is keyed by:

```text
(account, platform, action, route)
```

For each route, `read`, `write`, and `verify` are independent operations. The record stores separate `implemented`, `available`, and `verified` arrays, with two subset invariants:

```text
verified ⊆ implemented
available ⊆ implemented
```

Every verified operation needs a verification evidence reference and `last_verified_at`. Authorization must be `active`, have a non-empty scope, and be unexpired. Credentials are rejected recursively from capability records.

Readiness is derived on every view:

```python
missing = []
for operation in required:
    if not implemented(operation): missing.append(f"{operation}_not_implemented")
    elif not available(operation): missing.append(f"{operation}_unavailable")
    if not verified(operation):    missing.append(f"{operation}_not_verified")

executable = authorized_now and not paused_reason and not missing
```

This is intentionally more verbose than `available=true`. A GitHub App may be implemented, its token may work, and its write path may have passed a canary, while independent readback remains unverified. That route is not executable when `verify` is required. A paused account can block its own routes without disabling an unrelated healthy account.

The action resolver walks `preferred_transports` in order. With a structured capability set, it returns the first matching route whose derived `executable` is true. Otherwise it returns the accumulated missing reasons, then either a manual work package or `LIVE_BLOCKED`, depending on the action's fallback policy. It does not silently turn a requested live action into a dry run. Preview is an explicit execution intent.

MCP can read these capability records but cannot upsert its own verification claims. Otherwise an agent could improve its permission merely by describing it more confidently.

## The durable unit is an attempt, not a queue row

The current runtime uses SQLite as its authoritative store. Its central tables reveal the intended failure model:

```sql
attempts(
  attempt_id TEXT PRIMARY KEY,
  service_id TEXT,
  action_id TEXT,
  worker TEXT,
  intent_sha256 TEXT,
  status TEXT,
  lease_expires_at INTEGER,
  external INTEGER,
  evidence_json TEXT,
  ...
)

source_events(
  source TEXT,
  source_event_id TEXT,
  service_id TEXT,
  event_type TEXT,
  payload_json TEXT,
  processed INTEGER,
  ...
  PRIMARY KEY(source, source_event_id, service_id)
)

scheduled_jobs(
  job_id TEXT,
  intent_sha256 TEXT,
  status TEXT,
  result_json TEXT,
  external INTEGER,
  ...
  PRIMARY KEY(job_id, intent_sha256)
)
```

Services have a version number. Updates run under `BEGIN IMMEDIATE`, and the update succeeds only if the version read at the start still matches:

```sql
UPDATE services
SET service_json = ?, version = version + 1, updated_at = ?
WHERE service_id = ? AND version = ?;
```

A zero-row update is a version conflict, not a last-writer-wins merge.

Existing JSON and JSONL files remain compatibility projections. SQLite and the filesystem are not magically one atomic storage system; the design instead makes SQLite authoritative and the projections rebuildable. A scheduled job records its final result before writing the legacy state file, so a projection write failure does not cause a successful external job to execute again.

## Expiry has different semantics for local and external work

Claiming an action creates an attempt with a UUID, a worker identity, a payload hash, and a lease. A second claimant sees the active attempt rather than creating another one; if its payload hashes differ, the result carries `intent_mismatch`.

The action state machine is approximately:

```text
pending ──claim──> in_progress ──accepted evidence──> completed
                     │
                     ├──────────────────────────────> waiting_manual
                     │
                     ├─local attempt expired────────> pending
                     │
                     └─external attempt expired─────> waiting_verification
                                                        │
                                                        ├─one verified match──> completed
                                                        ├─zero matches────────> unresolved
                                                        └─multiple matches────> manual review
```

The attempt table distinguishes `expired_retryable` from `expired_unknown`. Local side-effect-free work can return to `pending`. External work becomes `waiting_verification` and stays non-runnable. A completed, manually waiting, or verification-waiting action is not claimed again by the scheduler.

This is the practical replacement for exactly-once fantasies: **one active claim, explicit uncertainty, and read-only recovery.**

The same rule applies to scheduled jobs with external effects. If a previous claim is still unresolved, the next scheduler tick returns `needs_reconciliation` instead of executing a duplicate.

## Inbound events can be at-least-once; projection must be idempotent

GitHub App ingress starts with HMAC-SHA256 signature verification using `hmac.compare_digest`. The HTTP server accepts at most 1 MiB and binds to loopback by default. Supported events are restricted to opened Issues, created Issue comments, created Discussions, and created Discussion comments.

Processing is a two-step durability protocol:

1. `INSERT OR IGNORE` the raw payload into `source_events` using `(source, source_event_id, service_id)` as the key.
2. In a transaction, project the stored payload into the conversation model and mark that source event processed.

A duplicate delivery is therefore a database duplicate, not a new conversation or action. If projection fails, the raw event remains pending and can be replayed. Conversation keys preserve the distinction between an Issue and a Discussion:

```text
owner/repo#7                  # Issue and all of its comments
owner/repo/discussion#12      # Discussion and all of its comments
```

Every interaction ID is retained. External text is tagged `untrusted_external_content`; receiving a webhook does not make its body an instruction to the agent.

Gmail uses a different cursor mechanism with the same idempotency goal. Initial sync enumerates INBOX messages and records the maximum `historyId`. Incremental sync follows `history.list`, deduplicating message IDs within the run. A 404 for an expired history cursor triggers a full resync rather than an empty “success.” The cursor advances only after messages have been ingested and pending events replayed. Pub/Sub notifications are acknowledged only after the corresponding sync succeeds; a notification is treated as a hint to synchronize, not as the message itself.

These mechanisms accept duplicate inputs so the system does not lose inputs. Durable identity and transactional projection prevent that choice from becoming duplicate business work.

## Outbound success requires an independent read

The return value from a write API is an executor-observed receipt. It is useful, but it is not independent final-state evidence.

The GitHub operator therefore follows a reply with an independent GET. The verification checks the comment ID or URL, body, author, and target Issue. Discussion replies follow the same pattern through GraphQL: `addDiscussionComment` must return a complete comment receipt, then `node(id: ...)` is queried independently. Gmail replies are checked through a separate thread/message read for thread, recipient, and body.

Reconciliation never resends. It searches the external system:

- one matching object completes the original attempt;
- zero matches leave the outcome unresolved;
- multiple matches require human review.

This rule also prevents an ambiguous read from becoming an excuse for another write.

## Proof export keeps execution, final state, and business events apart

Growovo's read-only Proof exporter maps those evidence types to different levels.

For every operation, a P0 check records the executor-observed result:

- `committed` → `PASS`;
- `failed` → `FAIL`;
- `indeterminate` → `INDETERMINATE`;
- dry-run, rejected, and other non-executed states → `UNCHECKED`.

A P2 final-state check is much stricter. The current `readback_ok` predicate requires a verified readback bound to the same operation, channel, action, target, full content hash, receipt identity, account, completion time, and readback source. Missing or mismatched evidence leaves P2 `UNCHECKED`; the executor's committed row cannot promote itself.

External product events become P4 checks. A verified event can pass; an unverified one is `INDETERMINATE`. No events produces a P4 `UNCHECKED` coverage check. The bundle explicitly sets `causality=not_claimed`.

The offline demo makes this concrete. I ran it in a temporary workspace and inspected `checks.json`:

```text
summary-readable                 P1  PASS
execution:<committed operation>  P0  PASS
final-state:<same operation>     P2  UNCHECKED
execution:<dry-run operation>    P0  UNCHECKED
execution:<rejected operation>   P0  UNCHECKED
execution:<failed operation>     P0  FAIL
external-business-events         P4  UNCHECKED
replay.status                        UNAVAILABLE
replay.missing                     operations.jsonl
```

The committed sandbox operation does not receive P2 because an in-memory demo receipt is not independent external final state. One intentional failed fixture makes the overall proof `FAIL`. That is a more useful test than a uniformly green demonstration: it exercises aggregation, negative states, and the difference between readable evidence and a business result.

You can reproduce the demo with:

```bash
pipx install growovo
mkt demo
```

Or inspect the [controlled-run page](https://roy-tong.github.io/Growovo/examples/controlled-run.en.html).

## Business observations need matching pairs

Execution evidence is only one layer. Growovo 0.20.0 distinguishes `owner_declared`, `observed`, and `independently_verified` business observations. Stronger observations require hash-bound evidence, subject identity, and metric-definition versions. Independent verification additionally needs an independent control domain, authority, verified result, and review receipt.

Before-and-after comparisons require the same observation pair, subject, and definitions, with baseline time before final time. A later baseline invalidates the old pair rather than retroactively improving the story. A valid delta still carries no causal claim.

This distinction came from an operational lesson. One internal handoff recorded 503 committed actions across X, GitHub, and Discord in twelve waves, alongside zero star growth in the three OVO repositories and no readable external user count. That was not a controlled experiment, but it made one measurement error untenable: treating executor-produced volume as user value.

The second batch is therefore a separate evidence event. It should test whether suppression, identity, unresolved receipts, capability state, and metric definitions survive a new authorized input set. Repeating the same fixture proves determinism, not continued usefulness.

## What the implementation proves so far

The released 0.18.1 boundary rejects all scripted non-API X browser writes before vendor execution, quota reservation, or committed-ledger append. Local operator authorization and a logged-in browser are not treated as platform permission. Read-only collection, explicit previews, drafts, and human operation remain separate.

For this article I also ran the development-tree capability, concurrent-domain, signed GitHub golden-chain, and Gmail tests: 13 passed. They cover independent read/write/verify requirements, expiry and account isolation, SQLite authority without compatibility JSON, duplicate signed webhook submission, independent Gmail readback, history-404 recovery, and Pub/Sub acknowledgement after successful sync.

Those tests use temporary directories, simulated platform transports, and local HTTP/SQLite flows. They do not establish a real GitHub App or Gmail write canary, user activation, or revenue. The next engineering milestone is correspondingly narrow: run those canaries against authorized targets and compare their external readbacks with the attempts already stored. Reliability claims should grow at the same speed as the evidence.

### Source and verification links

- [`approval.py`: execution fingerprints and approval snapshots](https://github.com/roy-tong/Growovo/blob/main/core/approval.py)
- [`proof_export.py`: P0/P2/P4 mapping and readback predicate](https://github.com/roy-tong/Growovo/blob/main/core/proof_export.py)
- [`business_observations.py`: observation grades and comparison pairs](https://github.com/roy-tong/Growovo/blob/main/core/business_observations.py)
- [Growovo implementation status, including development-tree boundaries](https://github.com/roy-tong/Growovo/blob/main/docs/feature-status.md)
- [Controlled-run example](https://roy-tong.github.io/Growovo/examples/controlled-run.en.html)
- [Growovo changelog](https://github.com/roy-tong/Growovo/blob/main/CHANGELOG.md)
