# Changelog

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
