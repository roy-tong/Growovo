# Gmail Community Operator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Deliver recoverable Gmail two-way conversation operations with incremental sync, watch renewal, Pub/Sub pull, verified replies and indeterminate Sent reconciliation.

**Architecture:** Gmail API owns mailbox facts; the existing SQLite runtime owns cursors, source events, conversations and attempts. Lite mode uses periodic sync; Pro mode adds watch and Pub/Sub pull, with acknowledgement only after successful synchronization. Replies use the existing write pipeline.

**Tech Stack:** Python 3.9 stdlib, Gmail REST API, Cloud Pub/Sub REST pull, `EmailMessage`, SQLite, `unittest`.

## Global Constraints

- OAuth access/refresh tokens, client secrets and message credentials never enter records or logs.
- A notification is only a change signal; `history.list` is the incremental fact source. HTTP 404 forces full resynchronization.
- Pub/Sub acknowledgement follows successful sync, never precedes it.
- Reply completion requires an independent message GET matching thread, recipient and body.
- Unknown sends query Sent/thread state; no blind retry.
- Tests use fake API transports and no real mailbox operations.

---

### Task 1: Gmail OAuth, full/incremental sync and durable cursors

**Files:**
- Create: `core/gmail_api.py`, `core/gmail_events.py`
- Modify: `core/growth_runtime.py`
- Test: `tests/test_gmail_api.py`

**Interfaces:**
- Produces: `GmailClient.from_env()`, `.full_sync()`, `.history_sync(start_history_id)`, `.watch(topic)`, `gmail_events.sync(...)`, `.renew_watch(...)`, `.pull_notifications(...)`.
- Cursor identity: `(connector, account, service_id)` in SQLite.

- [x] **Step 1: Write full sync, history 404 and acknowledgement tests**

```python
first = gmail_events.sync(data, sid, client=client)
self.assertEqual("10", first["history_id"])
second = gmail_events.sync(data, sid, client=client)
self.assertEqual("11", second["history_id"])
self.assertEqual(2, len(growth_engagement.list_conversations(data, sid)))
```

Test history 404 rebuilding from full sync, duplicate messages not duplicating conversations, watch expiry storage, and Pub/Sub acknowledgement only after successful sync.

- [x] **Step 2: Run tests before modules exist**

Run: `python3 -m unittest tests.test_gmail_api -v`
Expected: import failure or missing cursor behavior.

- [x] **Step 3: Implement API, cursor and source-event transactions**

```text
messages.list/messages.get → historyId → history.list(startHistoryId)
                                   ↘ HTTP 404 → full sync
watch + Pub/Sub pull → sync → acknowledge
```

Store OAuth tokens only in memory. Persist raw messages before projection and advance the cursor only after all projections succeed.

- [x] **Step 4: Run sync and replay tests**

Run: `python3 -m unittest tests.test_gmail_api -v`
Expected: PASS, including full recovery and no premature acknowledgement.

### Task 2: Verified Gmail reply and Sent reconciliation

**Files:**
- Create: `channels/gmail.py`
- Modify: `core/gmail_api.py`, `core/action_runner.py`, `core/growth_execution.py`, `core/growth_goals.py`
- Test: `tests/test_gmail_api.py`, `tests/test_growth_execution.py`

**Interfaces:**
- Produces: `gmail.reply(thread_id, recipient, text, ...)`, `.find_sent_replies(...)`, `gmail_api` execution route.

- [x] **Step 1: Write verified reply and unknown-send tests**

```python
record = gmail.reply("thread-1", "user@example.com", "answer",
                     go=True, adhoc=True, data_dir=data, client=client)
self.assertEqual("committed", record["result"])
self.assertEqual("gmail://threads/thread-1/messages/message-1", record["evidence"])
```

Test `action_runner` forwarding, unique Sent reconciliation, and zero/multiple matches remaining non-runnable.

- [x] **Step 2: Run tests before wiring**

Run: `python3 -m unittest tests.test_gmail_api tests.test_growth_execution -v`
Expected: missing adapter and unsupported transport.

- [x] **Step 3: Implement MIME send and independent readback**

```python
message = EmailMessage()
message["To"] = recipient
message.set_content(text)
raw = base64.urlsafe_b64encode(message.as_bytes()).rstrip(b"=").decode("ascii")
```

Keep `threadId` in the send request, then GET the returned message ID and verify headers/body. Continue through `action_runner` and `standard_pipeline`; SMTP remains available.

- [x] **Step 4: Run Gmail, execution and golden-chain regressions**

Run: `python3 -m unittest tests.test_gmail_api tests.test_growth_execution tests.test_execution_integrity_integration -v`
Expected: PASS; reconciliation performs no send.
