# GitHub Discussions Community Operator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add GitHub Discussion and Discussion Comment ingestion, synchronized context, authorized replies, independent readback and indeterminate reconciliation to the existing Community Operator.

**Architecture:** Use GitHub's official GraphQL API for Discussions while retaining the existing runtime, capability records and `standard_pipeline`. Conversation identity uses `repository/discussion#number`; each comment keeps a distinct global node ID.

**Tech Stack:** Python 3.9 stdlib, existing GitHub App client, GraphQL over HTTPS, SQLite, `unittest`.

## Global Constraints

- Issue and Discussion targets/events remain distinct; no Issue REST rewrite.
- `discussion` and `discussion_comment` webhooks are durable before projection and replayable.
- Reply completion requires an independent comment query matching ID, URL and body.
- Unknown results are read-only reconciled; zero/multiple matches never auto-retry.
- Tests use fake GraphQL transports and no real account writes.

---

### Task 1: Durable Discussion ingestion and synchronization

**Files:**
- Modify: `core/github_events.py`, `core/github_app.py`, `core/growth_cli.py`, `core/cycle_scheduler.py`
- Test: `tests/test_github_discussions.py`

**Interfaces:**
- Produces: `GitHubAppClient.sync_discussion_interactions(slug)`, `.sync_community_interactions(slug)`, Discussion normalizers.
- Consumes: existing source-event transactions and `growth_engagement.import_interactions`.

- [x] **Step 1: Write webhook and GraphQL mapping tests**

```python
ingest(data, sid, "discussion", discussion_payload(), "delivery-d")
ingest(data, sid, "discussion_comment", comment_payload(), "delivery-c")
rows = growth_engagement.list_conversations(data, sid)
self.assertEqual(1, len(rows))
self.assertEqual("o/r/discussion#8", rows[0]["conversation_key"])
```

Test open Discussion pagination, comment mapping and `sync_community_interactions` returning both Issue and Discussion rows.

- [x] **Step 2: Run tests and confirm current unsupported behavior**

Run: `python3 -m unittest tests.test_github_discussions -v`
Expected: ignored events and missing GraphQL methods.

- [x] **Step 3: Implement GraphQL queries and normalization**

```graphql
repository(owner: $owner, name: $name) {
  discussion(number: $number) {
    id number title body url createdAt closed
    author { login }
    comments(first: 100, after: $after) {
      nodes { id body url createdAt author { login } }
      pageInfo { hasNextPage endCursor }
    }
  }
}
```

Persist before projection, map stable interaction IDs, and call both Issue and Discussion synchronization from CLI/scheduler.

- [x] **Step 4: Run source tests**

Run: `python3 -m unittest tests.test_github_discussions tests.test_github_events tests.test_growth_scheduler -v`
Expected: PASS with one Discussion conversation and idempotent replay.

### Task 2: Verified Discussion reply and reconciliation

**Files:**
- Modify: `core/github_app.py`, `channels/github.py`, `core/action_runner.py`, `core/growth_execution.py`
- Test: `tests/test_github_discussions.py`, `tests/test_growth_execution.py`

**Interfaces:**
- Produces: `github.discussion_reply(..., token=None, client=None)`, `github.find_discussion_replies(...)`; `discussion:owner/repo#N` targets.

- [x] **Step 1: Write failing reply and recovery tests**

```python
record = github.discussion_reply(
    "o/r", 8, "answer", go=True, adhoc=True, data_dir=data, client=client)
self.assertEqual("committed", record["result"])
self.assertEqual(comment_url, record["evidence"])
client.discussion_comment.assert_called_once()
```

Test target parsing, token forwarding, unique-match reconciliation, and zero/multiple matches remaining non-runnable.

- [x] **Step 2: Run tests before implementation**

Run: `python3 -m unittest tests.test_github_discussions tests.test_growth_execution -v`
Expected: missing adapter and target errors.

- [x] **Step 3: Implement addDiscussionComment and independent readback**

```graphql
mutation AddComment($input: AddDiscussionCommentInput!) {
  addDiscussionComment(input: $input) {
    comment { id url body author { login } }
  }
}
```

Use `standard_pipeline` for approval, Gate, quota, claim and receipt. Reconciliation only queries existing comments and closes the original attempt on one exact match.

- [x] **Step 4: Run all GitHub and execution regressions**

Run: `python3 -m unittest tests.test_github_discussions tests.test_github_app tests.test_github_events tests.test_github_golden_chain tests.test_growth_execution -v`
Expected: PASS; no Discussion recovery path issues a mutation.
