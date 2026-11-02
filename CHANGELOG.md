# Changelog

## Unreleased

### Added

- **Task-outcome record** (`core/task_outcome.py`): a canonical JSONL record that
  keeps the platform action result (`sent`/`acked`/`failed`/`unknown`), task outcome
  (`resolved`/`partial`/`unresolved`/`unknown`/`needs_human`) and business outcome
  (`observed`/`none`/`inconclusive`) as three independent layers. A record carries the
  action taken, decision basis, alternatives considered, risk class, verification
  method plus evidence refs, and an escalation reason where a human must take over.
  Validation rejects contradictions such as a successful action plus a resolved task
  without verification evidence, an unknown result without escalation, or an observed
  business outcome without its own evidence. `append_outcome` writes to
  `<data>/outcomes/records.jsonl`, the existing `outcomes/` workspace subdirectory.
- **GitHub expert task benchmark** (`tests/fixtures/github_benchmark/cases.jsonl`,
  `scripts/github_benchmark.py`): 33 realistic issue/PR cases across ten categories,
  split into 21 dev and 12 holdout cases (fixture v2). The four cases that exposed
  the unsafe baseline were moved to dev and the holdout was rebuilt; the
  deterministic baseline now recognises supply-chain impersonation, SSRF,
  cross-user data exposure, signature bypass, stalking and retaliation language.
  `--fail-on-harmful` exits 1 when a sensitive case would still receive a public
  or closing action, so the benchmark can gate an automated write path instead of
  only producing a report.
- **Ledger-to-outcome recording** (`core/action_runner.record_ledger_outcome`):
  every real `--go` action now appends a `task_outcome` record derived from its
  ledger result. `committed` becomes `sent` + `unknown`, `indeterminate` becomes
  `unknown` + escalation reason, `failed` becomes `failed` + `unresolved`; a
  dry-run writes nothing. Recording is best-effort and never rewrites the send
  result.

### Validation

- New tests cover record validation, append-only JSONL, fixture schema and split
  coverage, deterministic baseline output, the harmful-action gate, and the
  ledger-to-outcome mapping. 186 tests pass and `python3 mkt.py demo` still runs
  offline.

## 0.2.0 — 2026-11-01

### Added

- **Workflows** (`mkt workflow learn|approve|replay|runs|reconcile`): a versioned
  recipe for one repeated operation. Approval binds a content fingerprint with an
  expiry; editing the workflow invalidates it. Dry-run resolves locators only;
  `--go` replays. Every step is journalled before and after it runs, an unknown
  external result stops the run as `indeterminate`, and further `--go` replay is
  refused until `reconcile` records a human decision.
- **Locator ladder** (`core/locators.py`): `css → placeholder → label → role →
  text → visual`. Semantic recovery runs when a selector changes; visual
  recovery is an operator-supplied provider and stops as `visual_unavailable`
  when none is configured instead of guessing a target.
- **Commit safety**: a `click` marked `commit: true` checks the global pause and
  the daily budget, writes the ledger (`transport=workflow-ui`) and records an
  indeterminate result if the click raises.
- **Scorecard** (`mkt scorecard`): committed/unknown/failed/evidence rates from
  the ledger plus business-outcome counts from the separate event ledger;
  unknown results, failures, dry-run-only history and missing outcomes are
  explicit warnings. Rates are descriptive, not channel effectiveness.
- **Persisted schedule** (`mkt schedule add|list|tick|status|resume`): one due
  wave per tick, pause-aware, stopping on unknown/failed results and turning an
  interrupted wave into `needs_review` instead of retrying it.
- **Optimization proposals** (`mkt optimize`): reconcile unknowns, repair
  failures, test a higher-committed-rate channel, instrument outcomes, resolve
  memory blockers. Every proposal is `requires_human` and
  `automatic_apply: false`.
- Read-only MCP tools: `mkt_workflow`, `mkt_scorecard`, `mkt_optimize`.

### Changed

- MCP `serverInfo.version` is now `0.2.0`.

### Validation

- 144 tests pass, including locator recovery, visual-provider refusal,
  fingerprint invalidation, commit pause/budget/ledger, reconciliation gating,
  scorecard rates, scheduler pause/interruption and proposal human gates;
  `python3 mkt.py demo` still runs offline.
