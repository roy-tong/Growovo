# Execution strategy, guided intake, and preparation scheduling

## Context

Growovo currently compiles a business goal into one action per channel. It does not expand that goal into a wave-by-wave execution plan with concrete recipients and content. The scheduler can execute approved queues, but users must already know how to supply strategy inputs, prepare originals, mine reply and DM targets, review content, and keep enough material ready for each wave.

This change adds one connected path:

```text
partial user input
→ guided questions
→ growovo.execution-strategy/1
→ goal and action allocation
→ preparation TODO graph
→ materialized recipients and content
→ reviewed wave snapshot and approval
→ existing action gateway execution
→ execution, interaction, and business observations
```

The first reference campaign is ten waves and 300 actions: 30 X originals, 180 X replies, 60 GitHub replies, and 30 X direct messages. Each product has exactly one original per wave. The reference campaign is data, not a hardcoded product rule.

## Approved choices

- Add a versioned execution-strategy contract and compiler instead of extending static GTM documents or hardcoding the OVO campaign into `growth_goals.py`.
- Guide users one question at a time. Every question explains why the input is needed, gives an example, and offers a usable default where one exists. Non-interactive callers receive the same question as structured JSON.
- Start all independent preparation work as early as possible. Preparation and sending use separate candidate and approved-wave snapshots.
- A wave pauses preparation while its readiness is checked. If material is insufficient, only the missing preparation branches continue. Once enough reviewed material exists, preparation stops for that wave and the frozen wave can be approved and sent. Preparation resumes for later waves after sending and reconciliation.
- Keep two approval levels. The campaign approves goals, accounts, actions, budgets, preparation adapters, and expiry. A wave approval binds actual recipient IDs, content hashes, queue hash, strategy hash, and execution window. An explicitly authorized campaign may auto-approve materialized waves only when all required reviews and gates pass; otherwise the wave waits for a named operator.
- Reuse `action_gateway → action_runner → pipeline`, account leases, quotas, suppression, identity checks, stop states, receipt reconciliation, and the existing scheduled-job store. No new write path is introduced.

## Components

### 1. `core.output_strategy`

A pure compiler with four public operations:

- `guide(payload)` returns the current completion state and the next question, or `ready=true`.
- `apply_answer(payload, field, value)` merges one guided answer without discarding existing fields.
- `compile_strategy(payload)` validates a complete campaign and returns `growovo.execution-strategy/1`.
- `lint_strategy(document, require_ready=False)` checks allocation, target, content, qualification, and approval invariants.
- `render_strategy(document)` produces a human-readable goal decomposition, wave matrix, preparation TODO list, recipient appendix, and measurement contract.

The guided fields are ordered by dependency:

1. Product name and product URL.
2. Primary business outcome and its observable event.
3. Secondary outcomes such as repository stars or qualified followers.
4. Audience role and the problem that makes the product relevant.
5. Reproducible asset and CTA.
6. Execution window, accounts, and allowed platforms.
7. Total action budget, wave count, and per-product/per-wave action mix.
8. Existing candidates, or permission to prepare candidates through configured miners/adapters.

A minimal draft can contain only a product name. `guide` must not reject it with a field-list error. It returns one question such as:

```json
{
  "status": "needs_input",
  "field": "outcomes.primary",
  "question": "今晚最希望外部用户完成哪一个可观察动作？",
  "why": "它决定原创、回帖和私信分别把用户带到哪里，也决定最后怎样验收。",
  "example": {"metric": "external_demo_runs", "target": 1},
  "suggested_default": {"metric": "external_demo_runs", "target": 1},
  "answer_command": "mkt growth output-guide --file draft.json --set outcomes.primary --value '{\"metric\":\"external_demo_runs\",\"target\":1}'"
}
```

Interactive mode asks the same question and saves the draft after each answer. Non-interactive mode returns the question and never prompts on stdin.

### 2. Strategy document

The compiled document contains:

- `campaign`: ID, products, window, total actions, wave count, accounts, and approval policy.
- `outcomes`: baseline/target, mechanism, leading indicators, observable event, and `causality=not_claimed`.
- `action_decomposition`: how originals, replies, and direct messages contribute to each outcome. It explicitly states that sends do not prove adoption and that replies/DMs do not ask for stars or follows before value is delivered.
- `waves`: exact platform/action/product allocations and due times.
- `items`: zero or more materialized actions. Every materialized external action has a real target, context, persona/trigger qualification, complete text, asset/CTA, and execution/interaction/business measurement IDs.
- `preparation`: the TODO graph and supply requirements used when items are incomplete.
- `readiness`: counts for required, materialized, eligible, blocked, and missing items. A blueprint may be incomplete; a live wave may not.

Compilation verifies exact arithmetic. For the reference campaign, every wave has 30 items and every product has one original, seven or four X replies, one or four GitHub replies, and one DM according to the configured product mix.

Materialization rejects duplicate queue IDs and duplicate targets. DM handles are unique across the whole campaign, not merely within one product. Missing target, context, text, qualification, or measurement remains `blocked_supply`; no unrelated candidate is substituted to make the count look complete.

### 3. Preparation TODO graph

`build_preparation_plan(strategy)` creates persistent task specifications with stable IDs and dependencies.

Originals:

```text
generate-original-titles
→ generate-original-content
→ review-originals-my-voice
→ freeze-original-supply
```

Replies:

```text
mine-reply-topics ─┐
mine-koc ──────────┴→ qualify-reply-targets
→ draft-replies
→ review-reply-strategy
→ freeze-reply-supply
```

Direct messages:

```text
mine-dm-prospects
→ qualify-dm-prospects
→ draft-dms
→ review-dm-content
→ freeze-dm-supply
```

Independent roots start immediately. `review-originals-my-voice` requires a review record with reviewer `my-voice`, the current content hash, an explicit verdict, and concrete issues/revisions. It is not marked complete by calling a text linter or by writing `approved` without evidence. Other review tasks follow the same source-hash rule.

Task runners are explicit:

- `local`: deterministic local compilation, validation, freezing, and reporting.
- `miner`: an existing Growovo read-only supply miner.
- `host_adapter`: an authorized local process using controlled argv, minimal environment passthrough, a timeout, and a declared output schema. This covers content generation and agent-assisted review when configured.
- `manual`: a named person must supply the input or review.

A missing adapter is a visible `waiting_input`/`blocked` state, not a silently completed task.

### 4. `core.preparation_runtime`

The runtime persists task state and wave snapshots under the workspace state directory. It exposes a bounded `advance` operation for scheduler ticks and a read-only status projection.

States include `pending`, `ready`, `running`, `waiting_input`, `blocked`, `completed`, `paused_for_wave`, and `failed`. Retry uses the same task attempt ID and cannot duplicate an already-produced artifact.

`advance` follows this loop:

1. Read the strategy and current supply/readiness snapshot.
2. If no wave is due, run ready preparation tasks, prioritizing branches with the largest earliest-wave shortfall.
3. At a wave boundary, pause preparation mutations and calculate eligible, reviewed material for that wave.
4. If short, resume only the missing branches. Report the exact missing product/platform/action counts. Repeat on subsequent ticks until ready, the wave expires, or a stop condition occurs.
5. When ready, freeze a wave queue and strategy hash. Create the wave approval request, or approve it under the campaign's explicit auto-approval policy. Preparation cannot mutate this snapshot.
6. Execute through the existing scheduled-wave path.
7. After sending and receipt reconciliation, resume preparation for later waves.

The runtime never treats candidate count as reviewed supply. It never continues mining after the wave requirement is met merely to increase volume. Safety stops, DNC/suppression, identity mismatch, quota changes, CAPTCHA, 429, and unknown receipts take precedence over the preparation loop.

### 5. CLI and scheduler integration

New commands:

```bash
mkt growth output-guide --file draft.json [--set FIELD --value JSON] [--interactive]
mkt growth output-plan --file campaign.json [--out execution-plan.json]
mkt growth output-plan --file campaign.json --format md --out execution-plan.md
mkt growth output-lint --file execution-plan.json [--require-ready]
mkt growth preparation-status --file execution-plan.json
```

`cycle` wave jobs may include `strategy` and `preparation` references. `jobs-preview` shows the preparation TODO graph and current shortfall. `jobs-approve` binds the strategy document hash in addition to the existing job/queue/item hashes. `jobs-serve` calls bounded preparation advancement between scheduler ticks and before wave gating.

Existing jobs without a strategy reference keep their current behavior. Strategy-bound waves cannot fall back to a default queue or bypass wave snapshot approval.

### 6. Scheduling and concurrency

- One scheduler lock covers preparation advancement and job execution selection.
- Long miners/adapters use the existing stop-event or subprocess timeout pattern and persist attempts before side effects.
- Candidate workspaces and frozen wave queues are different files. A content change creates a new hash and invalidates review/approval.
- X searches and browser reads do not compete with an active X write route. GitHub/local miners may run when their independent routes are healthy.
- Preparation failures are scoped to the affected branch. An unknown external write stops the affected execution path and is never resolved by generating more content.

## Error handling

Guidance handles missing user input. Validation handles contradictory input. Preparation handles missing supply. Execution handles platform outcomes. These states are not collapsed:

- `needs_input`: the user must answer the next guided question.
- `invalid_strategy`: totals, windows, accounts, or action mixes are contradictory.
- `blocked_supply`: candidates, content, review, or qualification are insufficient.
- `waiting_wave_approval`: the wave is ready but lacks its exact-scope approval.
- `waiting_verification`: an external effect is unknown and must be reconciled read-only.
- `stopped`: a platform or campaign stop prevents further writes.

A 300-action blueprint can exist before all 300 items are ready. Reports must show `required=300`, materialized/eligible counts, and the exact gap rather than claiming 300 executable actions.

## Testing

Tests are written before implementation and use no real accounts or external sends.

1. A one-field draft returns one useful next question with why/example/default; answers advance until ready.
2. Interactive and JSON guidance share the same question sequence and preserve answers.
3. The reference campaign closes at 300 actions, ten waves, product totals, and one original per product per wave.
4. Missing targets, context, text, measurements, and DM qualification fail materialization.
5. DM handles are globally unique; duplicate targets and quota overflow are rejected.
6. Candidate counts never substitute for reviewed supply or trigger automatic backfill.
7. Original preparation enforces title → content → my-voice review → freeze and stale-review invalidation.
8. Independent reply/DM roots start early; dependent tasks wait for their inputs.
9. A wave with insufficient supply continues only missing branches, freezes when ready, and resumes later preparation after reconciliation.
10. Queue/content/strategy changes invalidate approval. Unknown receipts and safety stops prevent further writes.
11. CLI JSON/Markdown outputs contain goal decomposition, wave allocations, TODOs, recipients, content, and measurements.
12. Existing scheduler and growth-service tests remain green, followed by the full suite and `python3 mkt.py demo`.

## Scope boundary

This change implements guided intake, the versioned strategy contract, preparation TODO/state management, wave snapshot gating, CLI projections, and scheduler integration with injected/local/miner/host adapters. It does not claim that a particular external miner, X DM browser route, host content generator, or my-voice reviewer is available in production. Those capabilities must be configured and independently accepted before a live campaign can become ready.
