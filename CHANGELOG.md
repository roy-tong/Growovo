# Changelog

## 0.10.0 - 2026-11-04

This release adds a GTM design layer above role orchestration: a task is
decomposed into object × objective × motion × channel, checked against real
constraints, and returned with an outcome contract and an explicit verification
level. It designs; it does not execute, send or predict.

### Added

- **GTM design** (`core/gtm_design.py`, `mkt gtm design/lint/taxonomy`):
  six growth objects, six objectives, six composable motions and the existing
  37-platform registry. Text signals infer object/objective, explicit flags
  override, and low confidence returns `needs_clarification` instead of a
  fabricated plan.
- **Agent proposals with rule checks**: `--proposal` accepts candidate motions,
  reasons and channels. Incompatible motions, unknown channels and unknown
  data sources are rejected with visible reasons; accepted candidates keep the
  agent's rationale.
- **Outcome contracts per objective**: primary and leading signals, guardrails,
  explicit "not success" lists (plays, stars, GMV, pre-orders, sign-ups),
  observation window, stop rule and `observable` / `partially_observable` /
  `unverified` verification levels.
- **Constraint checks**: authorized channels, content/technical/sales/supply
  assets, budget, region and data sources; unauthorized channels stay visible
  with `executable=false` rather than being silently dropped.
- **Design handoff** (`mkt gtm handoff`, `gtm_design.handoff`): maps the design
  objective to the existing awareness/adoption/commercial goal families and,
  when product facts are supplied, produces a linted candidate Workstream via
  `core/orchestration.py`. The handoff remains a design-to-plan bridge: no
  execution and no claim that the selected motion was run.
- **Narrower routine authorization by default** (`mkt policy enable`): standing
  authorization now defaults to 30 days; permanent authorization requires an
  explicit `--days 0` and produces a warning. The API keeps its explicit
  `days=None` semantics for tests and administrative tooling.
- **Docs**: `docs/gtm-os/07-object-motion-design.md`; README/README.en and the
  GTM OS index now point to the design layer.

### Boundaries

- A design is not execution, not channel effectiveness and not proof of growth.
- No real account sends; platform authorization is not verified by this layer.
- With no data source the contract stays `unverified`; platform receipts cannot
  be reported as business results.

## 0.9.0 - 2026-11-04

This release makes GTM orchestration selective rather than org-chart-driven and
allows explicitly authorized routine marketing execution without repeated plan
approval. Payments and other high-risk commitments remain human decisions.

### Added

- **Minimum-role orchestration** (`core/orchestration.py`, `mkt orchestrate`,
  `mkt_orchestrate`): product/goal/task selects only required roles and optional
  triggers, then emits a WorkOrder DAG, receiver handoffs and validation.
- **Per-deliverable expression**: decision memos, operator runbooks, technical
  tutorials, sales handoffs, customer/partner plans, channel overlays and
  artifact-specific required content. Public-channel style does not leak into
  internal POC, revenue or support artifacts.
- **Standing routine authorization** (`core/execution_policy.py`, `mkt policy`):
  workspace-scoped closed routine actions, exact account scope, expiry, revoke
  and fail-closed validation. MCP cannot manage authorization.
- **Routine execution path**: `planlint`, action/queue runners, pipeline and
  email SMTP accept explicit `routine` and return an independent
  `routine-policy` receipt. Non-routine calls retain the original plan gate.
- **Docs**: `docs/gtm-os/06-orchestration-and-routine.md` with orchestration,
  policy setup, direct-execution examples and remaining boundaries.

### Safety and verification

- Routine authorization replaces only per-run plan approval. Dry-run, pause,
  DNC/suppression, quotas, read-back, channel verification and indeterminate
  reconciliation remain enforced. Payments, contracts, discounts, crisis
  response and unknown actions cannot be added to the policy.
- Full suite: **599 tests pass**. Policy tests use fake vendors/SMTP; no live
  accounts, network sends or business outcomes are claimed.
- The current workspace routine policy was enabled by the user's direct
  instruction for default-account routine actions. No real message was sent.

## 0.8.0 (unreleased) - 2026-11-04

This release turns the GTM OS v5 design into a local R0/R1 business work
kernel. Campaigns and queues are now execution tools beneath versioned work,
professional SOPs, accepted deliverables and receiver handoffs.

### Added

- **Capability/template registries**: 18 capability domains, 40 role families
  (07–46) and 32 deliverable templates with complete cross-reference lint,
  deep-copy read APIs and packaged JSON data.
- **Work OS**: immutable business context snapshots, versioned objectives,
  WorkOrders, SOP versions and evidence-gated runs, deliverable QA reviews,
  receiver-accepted handoffs and deterministic count/ratio KPI measurement.
- **CLI/MCP surfaces**: `mkt capability`, `template`, `context`, `objective`,
  `work`, `sop`, `deliverable`, `handoff`, `metric` and `work-action`; read-only
  MCP tools for capabilities, templates, work orders and KPI measurement.
- **Execution bridge**: an accepted deliverable at a declared action-request
  step materialises the existing plan/queue with WorkOrder/SOP/deliverable
  provenance included in the approval fingerprint. It never sends directly.
- **`mkt work-demo`**: zero-network context → work → SOP → accepted artifacts →
  handoff → approved plan/queue → independent KPI demo; no send ledger.

### Fixed

- Deal funnels use cohort entry history rather than dividing current stage
  stock. Deal writes are append-only events with legacy reads; all stage
  advances require evidence, qualification and quote preconditions are enforced,
  and ordinary deals start at `sourced`.
- `mkt execute` no longer invents evidence grade `B`; it records `unverified`.
- CLI and MCP result projections read canonical TaskOutcome records. Platform
  actions, task outcomes and business observations remain separate.

### Verification and boundary

- Full suite: **566 tests pass**; `mkt demo` and `mkt work-demo` pass without
  network, real accounts or sends.
- All 40 role families are registered, but only the developer-first-success SOP
  is currently a complete executable Step Contract. Full B2B/PLG packs,
  enterprise identity/RBAC, live source verification and business effectiveness
  remain separate work.

## 0.7.0 (unreleased) - 2026-11-04

This release makes channel and conversion first-class product surfaces rather
than advice inside a strategy document.

### Added

- **Channel model** (`core/channels.py`, `mkt channel venues|plan|score`,
  `mkt_channel_venues` / `mkt_channel_plan`): the unit is a venue (a specific
  subreddit, Slack, newsletter, conference or repo), not a platform. Each venue
  carries audience, access, promotion policy, signal density, competition,
  speed, risk and evidence. The planner assigns 1 primary + 1 launch + up to 2
  support + 2 listen-only venues, and returns capacity, a bounded economics
  range (assumptions, not forecasts), pre-registered experiments and rules.
- **Conversion pipeline** (`core/conversion.py`, `mkt deal ...`,
  `mkt_deals` / `mkt_deal_next`): a 14-stage deal machine with explicit
  transitions, evidence requirements for quote/SOW/payment, five-criterion
  qualification, one next action per stage (including "wait"), nine objections
  with walk-away conditions, four offers, a one-page SOW renderer, funnel and
  stuck-deal detection. `deals.jsonl` is append-only per stage advance.
- **Business narrative by default**: `mkt channel plan` now renders, for each venue,
  who is there, what they do today, why this venue rather than another, what to say,
  what proof we have, what signal counts and what would make us leave; plus a
  "why not elsewhere" table with reconsider conditions and ●/◐/○ evidence levels.
  `mkt deal next|funnel` renders stage meaning, what we know/need, why this action,
  risk/walk-away and why the price is the price. JSON keeps the technical fields.
- **Docs**: `docs/channels.md`, `docs/conversion.md` and `docs/gtm-loop.md` state the research
  logic, field definitions, economics assumptions, failure modes and boundaries.
- **Tests**: 27 dedicated channel/conversion tests; full suite **513 pass**.

### Changed

- README command matrix and intro now put goal → channel → conversion in that
  order; MCP `serverInfo.version` is `0.7.0`.

### Boundary

A channel plan is a scheduling hypothesis, not channel effectiveness. A deal
stage is a record, not a forecast. Exposure is not reach, a reply is not
qualification, a sample is not payment, and a quote is not revenue.

## 0.6.0 (unreleased) - 2026-11-04

This release adds goal selection before platform selection. The same product can
chase awareness, real usage or a first payment; those are different actions,
platforms and pass lines. A goal is now an explicit contract, not an implicit
assumption inside a channel recommendation.

### Added

- **Goal catalog** (`core/goals.py`): 9 goals across `awareness` (GitHub stars,
  social followers), `adoption` (forks, external issues, real data submissions,
  activation) and `commercial` (qualified conversations, first paid deal,
  repeat purchase). Each goal carries a metric definition, baseline, target
  shape, window, leading and counter indicators, what it proves / does not
  prove, stop/switch rules and a human-minute estimate.
- **Applicability and platform roles**: goals are filtered by product type,
  stage and owned platforms. Platform ranking combines goal affinity, ICP-tag
  match, owned accounts and product-type match; roles are capped at 1 primary,
  1 launch and 2 support, and a launch platform cannot be the only channel.
- **Primary + optional secondary goal**: `--secondary` lets a round pair\n  awareness with adoption (or another family); the plan returns two metrics,\n  shared platform roles and a combined action list, and success requires\n  both targets. A primary that does not serve the primary goal is rejected.\n- **`mkt goal list|plan|select`**: list selectable goals, render a full plan
  (roles, first two weeks, milestones, stop rules, trade-offs), and write a
  `growovo.goal-selection/1` contract. `mkt strategy build --selection` and
  `mkt strategy cadence --selection` consume the contract; the old retro
  numbers no longer override the chosen primary, and cadence uses the goal
  plan's actions instead of the generic 20/week template.
- **Sprint planning** (`core/sprint.py`, `mkt sprint plan`, `mkt_sprint_plan`):
  compress a goal plan into N days of parallel workstreams, hourly blocks,
  front-loaded daily targets (secondary metric lags one day), six acceleration
  levers, three checkpoints, platform daily-capacity checks and red lines.
  A 3-day sprint is a signal test, not market validation.
- **Read-only MCP tools** `mkt_goals`, `mkt_goal_plan` and `mkt_sprint_plan`.
- **Docs**: `docs/goals.md`; README command matrix and intro updated.

### Changed

- `strategy.build` accepts `goal_plan`; the rendered strategy now opens with
  the goal, what it proves / does not prove, and a platform-role table.
- `cadence.build` accepts `goal_plan`; goal mode no longer claims a generic
  touch count and schedules support platforms as light weekly actions.
- MCP `serverInfo.version` is now `0.6.0`.

### Verification

- `python3 -m unittest discover -s tests`: **486 tests pass**.
- `python3 mkt.py demo`: pass.
- The goal layer is a planning aid: a reached goal does not prove demand,
  payment willingness or repeatability, and platform affinity is not channel
  effectiveness. No real account sends or external business validation.

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
