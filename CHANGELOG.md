# Changelog

## 0.5.0 - 2026-11-04

This release deepens the growth loop from structured operations to evidence-bound
demand interpretation, real local value delivery, temporal journey accounting,
traceable experience review and release hardening. Synthetic adversarial tests
are not external business validation.

### Added

- **Signal Interpretation** (`core/signal_interpretation.py`,
  `mkt signal-interpret`, `mkt_signal_interpretation`): validate host-Agent
  semantic candidates for source, subject, problem vs technical discussion,
  tool seeking, alternative dissatisfaction, contact eligibility and commercial
  evidence, with deterministic deduplication and risk disclosure.
  `opportunity.discover` accepts `semantic_candidates` and consumes only
  qualified rows; legacy raw signals remain `unverified_signal`.
- **Value delivery chain** (`core/value_delivery.py`, `mkt value-chain run`):
  research → solution → real local reproduction → asset → authorized delivery →
  user readback → completed. Reproduction uses argv with `shell=False` and a
  command/cwd fingerprint authorization; real exit code, output and source refs
  are recorded. Unauthorized commands never execute; failed reproduction cannot
  produce a verified asset; sent/exposure is not user completion.
- **Journey temporal and identity depth**: parsed event time ordering,
  out-of-order disclosure without backfill, email normalization plus explicit
  identity mapping, first vs repeated activation, and separate ordered
  multi-touch relationships. No last-touch causal attribution.
- **Traceable experience review** (`mkt exp assess`): formal strategy requires a
  rights basis, confirmer, confirmation time and record, plus reviewer, review
  time, review record and source domains. Boolean-only `rights_cleared` /
  `human_reviewed` no longer qualifies; internal self-report is experiment-only.
- **Read-only MCP surfaces** for Signal Interpretation and the new claim
  boundary.
- **Release guard and CODEOWNERS**: tags must descend from `main` and match the
  package version before the release workflow can publish.

### Changed

- Opportunity Discovery now distinguishes host-interpreted candidates from
  legacy signals; self-declared purchase intent and tool seeking never upgrade
  to observed commercial intent.
- Journey and experience status expose explicit review grade, timing and source
  provenance instead of treating declarations as proof.

### Verification

- `python3 -m unittest discover -s tests`: **431 tests pass** on Python 3.9 and
  3.11.
- `python3 mkt.py demo`: pass.
- `git diff --check`: pass.
- No real account sends or external business validation.

## 0.4.0 - 2026-11-04

This release turns the execution kernel into a complete GTM/User Growth loop:
demand discovery, value artifacts, authorized execution, same-scope results and
typed learning. Local implementation and tests are not production business
validation.

### Added

- **Demand and value loop**: `mkt opportunity` discovers candidate needs from
  product facts and explicit signals; `mkt artifact` validates and renders
  tutorials, examples, reproducible tests, templates, comparisons and real
  cases; `mkt journey` diagnoses discovery→engagement→trial→activation→retention→payment
  inside one campaign/subject/time window.
- **Platform × Motion × Vertical playbooks** (`core/playbook_model.py`) compose
  platform rules, reusable growth motions and vertical value constraints
  without duplicating a workflow per platform and industry.
- **Capability, competition and evolution evaluation** (`core/evaluation.py`,
  `mkt evaluate`): same-condition metric comparisons, external unseen-case
  contract and regression/evolution history, without a composite winner score.
- **Typed experience eligibility** (`core/experience.py`): official rules,
  reproducible methods, single-expert experience, short-term tactics and own
  tests have separate source, rights, relevance, independence, review and
  freshness gates. Strategy, MCP and prompt generation use eligible records only.
- **Authorized account and network assets**: account ownership/authorization,
  lifecycle, health, cooldown and human takeover (`mkt account`); proxy
  environment-variable references bound to accounts (`mkt network`) with no
  credential reading, quota increase or ban evasion.
- **Pricing experiments** (`mkt billing`): action/outcome candidates plus
  explicit subscription/hybrid experiments and acceptance logs. Accepted quote
  is commercial feedback, not payment.
- **Growth modules across CLI and MCP**: opportunity, value artifact and journey
  are available as read-only MCP tools as well as CLI commands.
- **Real CLI end-to-end coverage** for email plan→approval→dry-run→result,
  X reply queues, manual work sheets, campaign isolation, account lifecycle and
  pricing experiments.

### Changed

- **Result projection is scope-first**: ledger, outcomes and follow-ups share
  campaign/subject/time-window filters with no fallback to full history.
  Verified value events are dynamic per real `OutcomeLedger` data.
- **Approval binds the execution, not just the plan**: fingerprint covers the
  normalized queue, targets, bodies, action, platform, account and workspace.
  Queue tampering, wrong account/workspace, expiry, revoke and missing queues
  fail closed.
- **Intent campaigns are configurable learning experiments**, not fixed
  four-week pass/dead verdicts. Sample insufficiency and safety anomalies remain
  visible; channel death is not automatic.
- **Strategy and cadence no longer require product-level numeric gates**.
  Success criteria are optional learning objects; the fourth week is
  retrospective and next iteration.
- README, English README, Growovo GTM Skill and feature status now describe the
  actual Demand/Execution/Learning product rather than only the GitHub Inbox
  playbook.

### Fixed

- Cross-campaign and historical-record leakage in result, revision and journey
  projections.
- Duplicate platform registry definitions; X queue generation now emits the
  supported `reply` action instead of an unsupported `message` queue.
- Case-specific Verovo/default ICP content and Star-threshold gates removed from
  generic Growovo strategy and execution defaults.
- Stale or unreviewed experience can no longer be presented as current platform
  guidance.

### Verification

- `python3 -m unittest discover -s tests`: **402 tests pass**.
- `python3 mkt.py demo`: plan lint, approval, dry-run/write path and ledger pass.
- Real CLI E2E covers email, X reply contract and manual channels without live
  sends.
- `git diff --check` passes.

## 0.3.0 - 2026-11-02

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
