# Changelog

## Unreleased

### Fixed

- **Approval consumption and rollback semantics (D2, G-04)**: a human approval
  is now single-use. When it is used to land a privilege escalation it is
  stamped `consumed_at` / `consumed_by_change` / `consumed_by`, and the same
  record can never approve another escalation (the stale-hash check already
  blocked some replays, but a rollback restored the old hash and made the old
  approval valid again). Rolling back also revokes unused approvals bound to
  the restored strategy hash (`revoked_by_rollback`), so no pre-rollback
  approval can come back to life; approvals created after the rollback still
  work. A change that has already been reversed cannot be rolled back again,
  including via an explicit `change_id`; rolling back a rollback record remains
  refused. The failure reason distinguishes consumed, revoked, stale and
  never-approved.

- **Signed, policy-bound arm (D4, G-05)**: `mkt grow arm` is an explicit
  human action, not something an agent can do after creating a plan.
  `--by <operator>` is required; anonymous or blank arm returns
  `AUTH_REQUIRED` and leaves `go=false`. The run must be `ready_to_arm` (or a
  legacy `active`/not-armed run), every action in the run must still be covered
  by a valid routine policy (`allows()` checks expiry, revocation and account
  scope), account health must pass, and the shared incident scan must show no
  blocking alert; any failure returns `POLICY_DENIED`/`REVIEW_REQUIRED` (with
  the blocking INC ids). A successful arm records `armed_by`, `armed_at` and
  `armed_policy_sha256` (sha256 of the policy file bytes) plus an `arm` history
  entry; a second signed arm is idempotent and keeps the first attestation.
  It never runs queues, writes ledger rows or sends. The legacy v1 path keeps
  working through `mkt grow arm --by` but does not record a signature; it
  prints a migration note. Signing is a declaration of who pressed the button,
  not cryptographic proof of identity (the same honesty as Verovo's
  `attested_by_declaration`); real security still comes from policy, quota,
  pause and the ledger.

- **Buyer-controlled payment verification (D1, G-03)**: `paid`/`activation`
  no longer count as verified just because `verified_by` and `evidence_hash`
  are non-empty. The verifier must be in a payment/buyer-controlled domain
  (payment providers, billing/product DB exports, `buyer:`/`customer:`
  records, or an operator-extended whitelist); vendor/supplier/seller/sales
  identities are rejected even with every field filled. The evidence hash must
  recompute exactly from the event identity and structured payment fields
  (`event_id`, `event_type`, `subject_id`, `source_system`, `source_ref`,
  `amount`, `currency`, `payment_ref`, `paid_at`, canonical compact JSON,
  sha256). A paid event additionally needs amount > 0, a three-letter currency
  and a non-empty payment ref; missing or tampered fields stay unverified and
  never project into the paid funnel or write `payment_verified` receipts.
  `import_events(..., trusted_verifiers=...)` lets an operator add a
  buyer-controlled verifier explicitly. Event records keep the new payment
  fields so the evidence can be recomputed later.

- **Competitor-watch URL and event truthfulness (G-06)**: `core/watch.py` now
  asks `core/watch_safety.py` before every snapshot. Only `http`/`https` are
  accepted in production; `file://` works only in an explicit local mode with
  an injected local fetcher (tests/offline demos) and is rejected on the default
  path. Host admission is two-stage: static hostname rules reject `localhost`
  (and subdomains), `*.local`, `.internal`/`.home.arpa`/`.lan`, single-label
  names, and literal/legacy IPv4 forms (decimal, octal, hex, short form, `0`);
  then DNS results are checked and **every** address must be public — loopback,
  RFC1918, link-local, CGNAT, documentation/benchmark, multicast, reserved,
  `0.0.0.0`, IPv6 unique-local (fc00::/7), and IPv4-mapped IPv6
  (`::ffff:127.0.0.1`) all fail closed. Resolution failure or an empty answer
  is a refusal, never a pass. The address classifier is a pure function so the
  whole IPv4/IPv6 matrix is testable without network. Redirects are followed
  manually, never by the HTTP library: each hop is re-resolved and re-checked,
  the hop limit is 5, loops and non-http(s) targets are refused, and
  `https→http` downgrade is refused (after checking the target is not private,
  so a redirect to an intranet is reported as such). Response bounds: 2 MB,
  15 s, HTML/plain-text content types only, 4xx/5xx and challenge/login-wall/
  empty-shell pages keep the previous baseline (403 never moves it). Event
  truthfulness: script/template-only changes, timestamps, UUIDs, long hex
  tokens, long numbers and long nonces are noise and produce no event; a real
  text diff only records `page_content_changed` with `verification:
  needs_review`, `claim: content_changed`, `claim_status: unverified` and
  `tactics_status: candidate_after_review`. Price changes are never inferred
  from the whole-page diff: structured currency/number tokens are included as
  a hint only. Human confirmation is a separate append-only
  `event_verifications.jsonl` (`mkt watch confirm <id> <event_id> --claim
  price_change|status_incident|changelog_update|content_only|rejected --by
  <who> --note <why>`); it never rewrites the original event, and the latest
  verdict wins in the read view. `mkt watch add/check --allow-local-file` is
  the explicit offline local-page mode; production commands reject `file://`
  without it. All tests use injected fake resolvers/transports; zero network.

- **Unified start, monitoring and safe recovery (G-05)**: a new
  `core/grow_runbook.py` turns the first safe run into a nine-step wizard
  (`mkt grow start` without a run id; with a run id the old v1 path is kept):
  automatic doctor → credential/login/permission gap list → account identity →
  goal and metric source → three offline candidate previews → risk and quota →
  **explicit arm** → foreground or scheduled mode → daily-report delivery. The
  wizard never signs policy, never fabricates identity proof and never arms:
  it writes a plan with status `draft` or `ready_to_arm` (`go=False`) and the
  exact next command; `mkt grow arm` refuses a draft, so nothing can start
  before login/authorization are real. It reports the C-01 start checklist
  (`product_facts`, `verified_account`, `risk_policy`,
  `activation_definition`, `source_access`, `required_human_review`), defaults
  to one channel / one action / a minimal quota / no auto-scale, and treats the
  daily report as local files by default — email delivery requires a separate
  `--authorize-email` and still only happens when a human runs
  `mkt grow tick --deliver` (the wizard never sends). Run status gains the
  `draft`/`ready_to_arm` states from §4.3 on the existing run file; there is no
  second state machine. `mkt grow status <run> --explain` returns the C-01
  contract (`next_actor`, `next_required_action`, `blocked_reason_code`,
  `last_updated_at`, `has_pending_external_effect`, `artifact_refs`) plus
  quota remaining, observation window, data coverage and per-row fault status.
  `mkt grow incident <run> --list` reports every alert with a stable unique
  `INC-xxxxxxxxxxxx` event id and a human recovery command; corrupt ledgers or
  suppression files become `ledger_or_suppression_corrupt` incidents instead of
  crashing (fail closed, never treat a bad row as absent). The eight-row fault
  matrix is a decidable object (`FAULT_MATRIX` / `diagnose`): browser crash,
  account/authorization expiry, unknown receipt, ledger/suppression corruption,
  readings failure (no fake 0), duplicate instance (single-instance lock probe
  that ignores harmless stale lock files), supply shortage/staleness, and
  timezone switches. `mkt grow resume <run> --after-reconciliation <INC-id>
  --by <operator>` refuses to resume while an `indeterminate` external action
  is unreconciled — a real `mkt ledger resolve` record is required — and also
  refuses while other blocking incidents remain; it only changes the run status
  and never resends or undoes an external action. `mkt grow pause <run>` sets
  `paused` so the next tick selects nothing (the global `mkt pause` file stays
  the hard stop), and `resume` understands `paused` too. Run windows now carry
  the RunSpec IANA zone (`windows.iana`) and scheduling uses it when present,
  so daylight-saving changes no longer shift the audience's local golden window
  (runs without an IANA zone keep the legacy fixed-offset behaviour). CLI,
  offline and default-safe: `mkt grow start` (wizard, `--dry-run` by default,
  `--apply` writes the plan), `status --explain`, `incident --list`,
  `resume --after-reconciliation`, and `pause`. Synthetic fixtures and injected
  login probes only; zero network, zero real sends.

- **Controlled learning and strategy experiments (G-04)**: a new
  `core/controlled_learning.py` turned the learning loop into a permission
  system, not a statistics contest. `GrowthExperiment` now carries every PRD
  field (`hypothesis`, `segment`, `mechanism`, `control_or_baseline`,
  `primary_metric`, `guardrail`, `window`, `min_sample_declared`,
  `analysis_limitations`, `stop_conditions`, `approval`) on the existing
  `data/experiments/<id>.json` store; design requires all of them, approval
  binds the full plan fingerprint (`plan_sha256`), and a changed plan or a
  missing approval blocks start with `REVIEW_REQUIRED`. Observations are
  evidence-idempotent: re-submitting the same evidence hash is a no-op and
  never adds a second sample. Analysis is honest by construction:
  `no_data` without samples, `exploration` for descriptive before/after or
  samples below the declared minimum (three samples can only suggest a
  direction and explicitly cannot claim a winner), and `causal_allowed` only
  when the experiment is randomized with an `assignment_ref` and reached
  `min_sample_declared`; the conclusion is still signed by a human.
  Strategy changes are classified into three tiers: agent-suggestible
  (audience, queries, formula/mix, quota decrease), human re-approval
  (single-day/weekly cap increase, adding a channel, changing the account,
  lifting suppression, changing the auto-send scope, unknown params fail
  closed), and auto-safe (reduce or stop). An Agent actor never mutates
  strategy — it returns suggestions; in extreme-failure mode it may still only
  reduce quota or stop, never increase. Every applied change records
  `before_hash`/`after_hash` of the canonical strategy view, the evidence hash,
  tier and approver; an approval is bound to both the change fingerprint and
  the strategy hash, so it expires once the strategy moves. Re-submitting the
  same evidence is a no-op (no second privilege bump). Rollback restores the
  previous strategy snapshot, appends a rollback record instead of deleting
  history, and leaves the ledger / external actions untouched. Learning never
  lifts DNC: even an approved suppression change is recorded as a manual action
  for a human to perform. Viral spikes (one post at ≥5× the median) and
  zero-interaction samples force a review before any weight change, and strong
  negative feedback can only suggest stopping a strategy (never lifting DNC or
  raising quota). §12.5 is covered by a frozen three-arm comparison
  (manual / generic agent / Growovo) plus the seven result metrics, which stay
  `unknown` when no traceable source exists (not 0), and the report states that
  billing is not based on followers or send counts. `mkt grow decide --action
  optimize` now applies only low-risk changes and holds the rest as
  `needs_review` with the old strategy still running (never silently stops, never
  escalates without approval); weekly account-health reductions are recorded as
  `auto_safety` changes; `grow_learning` marks its payload `non_causal` and
  carries review triggers; daily/weekly cards say when samples are too thin to
  adjust weights. CLI, offline and default-safe: `mkt grow experiment
  design/approve/start/observe/analyze/compare/metrics/list` and `mkt grow
  change list/approve/rollback` (design/approve/start/observe/compare and the
  change writes need `--apply`; analyze/metrics/list are read-only). Synthetic
  data only, zero network, zero real sends.

- **Three-layer outcomes and real activation feedback (G-03)**: a new
  `core/outcome_layers.py` imports **authorized product events** (JSON / JSONL /
  CSV) and keeps execution, interaction and business evidence in separate
  layers. Import validates the source system against an explicit allow-list and
  requires an importer signature, deduplicates on `event_id`, enforces an
  import time window and ISO timestamps, stamps `growovo.product-event/1`
  (schema version + import batch + `imported_by`), and records
  `verification_state`: activation/paid need both `verified_by` and
  `evidence_hash`, otherwise the claim stays `unverified` and never counts as
  paid. Every accepted event appends a receipt that points at its source
  (`product_events:<event_id>`) — L1 `action_receipt` from the send ledger,
  L2 `engagement_receipt`, L3 `activation_receipt` — and each layer has an
  assertion allow-list, so an L1 receipt can no longer claim business growth.
  Receipts and qualification changes are append-only. Verified business events
  are projected into the existing `OutcomeLedger`
  (`import_key=product_events:<event_id>`), so the funnel, `grow
  metrics/learn/review` and the result card keep one ledger instead of a
  parallel one. First-activation semantics deduplicate by subject (a second
  activation is listed as a repeat, a second signup does not become a second
  first activation), campaign-less events count toward the product total but
  stay `unattributed`, and attribution only reports evidence-chain reach:
  `direct_receipt` / `assisted_observation` / `unattributed` with
  `causal_credit=not_claimed` — multi-touch is reported, never converted into
  "X brought 10 users". Manual qualification changes require `by` + `note`,
  append an audit record and leave the original event untouched. A read-only
  `ProspectJourney` projection merges ledger touches, imported events, contact
  identity namespaces and suppression state (G07–G10); same display name with
  different platform IDs is not merged and DNC/bounced prospects keep their
  suppression. Result cards gain main-goal status (met/short/no_data),
  three-layer columns, evidence coverage and blind spots, human minutes and a
  next recommendation; without a product event source L3 renders `UNKNOWN`
  with "激活事件未接入", never `0` and never `activation 0`. Queue staging now
  evaluates the three-element outreach admission (`their_problem`,
  `why_we_help`, `expected_action`): incomplete items are excluded from the
  auto-reach set, legacy items that never declared the elements stay eligible
  (`outreach_ready=None`). CLI, all offline and default-safe:
  `mkt grow events import --from <file> --allowed-source <system> --by <who>
  [--apply]`, `mkt grow events list|sync`, `mkt grow layers [id]`,
  `mkt grow journey <ref>`, `mkt grow qualify <event_id> --yes|--no --by <who>
  --note <why> [--apply]`. Import and qualification are dry-run unless
  `--apply` is passed; everything else is read-only. Synthetic fixtures only,
  zero network, zero real sends.

- **Account identity binding (G-01)**: real x sends now verify that the
  browser's actual platform account matches the authorized one. A new
  `core/account_identity.py` gate (`AccountIdentityProof` +
  `enforce_identity`) runs on every real-send path — queue, single action and
  MCP — before dispatch: identity-bound policies (require_identity /
  allow_accounts / account_identities) require a freshly observed proof whose
  stable user_id exactly matches the authorized record, and queue_runner now
  passes the observed user_id (never a caller-supplied one) into approval
  checks. Unknown, mismatched, stale (>300s) or wrong-channel observations
  fail closed with distinct reason codes (`identity_unknown`,
  `identity_mismatch`, `identity_stale`, `identity_channel_mismatch`); name-only
  authorizations without identity records are blocked with
  `authorization_missing_identity` until re-authorized with identities; the
  session_gate 300s login-marker cache is never accepted as identity
  evidence. Receipts record authorized_account + observed identity; every
  decision appends to `data/identity/events.jsonl`. Unbound legacy policies
  keep their previous behaviour. The default CDP identity reader deliberately
  returns None (fail closed) until live wiring after the X campaign window
  (2026-11-19); email sender identity is not yet covered by this gate.
  Adversarial-review follow-ups: run_queue now forwards the observed user_id
  into every per-item run_action call (a require_identity queue previously
  passed the queue-level gate and then failed every item at approval), and the
  `mkt x <action> --go` CLI path (including `--adhoc`) runs the same identity
  gate with a new `--account` option — it can no longer bypass identity-bound
  authorizations. A no-mock queue integration test freezes the real path.

### Fixed

- **Calibration streak integrity (G-02)**: `mkt grow calibrate` now counts one
  valid calibration per calendar day. A valid day requires a same-day auto
  reading and a human reading that are both non-negative integers and strictly
  equal, a non-empty `--by` reviewer, and the current account handle; explicit
  `--mismatch` submissions and same-day conflicts void the day, seven
  submissions on the same day count as one, a missing calendar day resets the
  streak, and future `--on` dates are rejected. PROVEN is scoped to
  metric+handle+parser: account or reading-definition changes downgrade the
  verification back to UNVERIFIED and never extend to other metrics. Before
  this fix, any seven consecutive successful submissions — same-day repeats or
  entries without a human reading — promoted readings to PROVEN.
  Adversarial-review follow-ups: the streak is anchored to the submission day
  (an invalid same-day submission reports 0 instead of resurrecting a month-old
  run across a gap), and consecutive counting restarts from the current day
  whenever the scope credential is missing or changes, so a downgraded run
  cannot re-promote from historical valid days.

## 0.14.0 - 2026-11-14

### Added

- **Tactic portfolio recommender**: `mkt tactic recommend --facts <facts.json>
  [--goal awareness.followers]` turns the 82-tactic registry into an input to
  strategy design — transparent scoring (goal-family match +3, usable +2,
  channel match +2, phase +1), at most 2 picks per mechanism, stale evidence
  excluded by default. Every pick carries its full steps/quota/caution and a
  human-readable "why".
- **Competitor event watchline** (`core/watch.py` + `mkt watch
  start/add/remove/check/events/summary`): snapshots competitor price/status/
  changelog pages, diffs text signatures on a cron-friendly `check`, and emits
  events to `data/watch/events.jsonl` with the matching counter-tactics
  (price change → price-hike-harvest / vs-page-matrix / migration-narrative).
  Read-only; dead links are refused at add time; fetch failures are skipped,
  never mistaken for changes. Schema in `data/schemas.md`.
- **MCP `mkt_tactics`** (read-only): agents can access the whole strategy
  knowledge base at runtime — list/suggest with filters, show full entries,
  recommend a portfolio from product facts + goal, forbidden list and stale
  check. This is the on-demand access path for the four research rounds
  (82 tactics / 19 mechanism families / 11 disproven approaches); the
  human-readable index (now with per-family research-round provenance) lives
  in `docs/playbooks/战术/非常规增长战术总表.md`.
- Tests: `tests/test_tactic_capabilities.py` (11) — recommendation
  goal-family dominance, channel scoping, diversity/staleness, watch
  diff/failure/validate flows, MCP direct dispatch, CLI end-to-end with
  local `file://` pages (zero real network).

- **Goal-driven operations**: `mkt grow launch` compiles product facts and a
  growth goal into channel strategy, quotas, audience time windows and content
  briefs. Daily execution, results, weekly review and user decisions share one
  persistent run. X and email are the automated write channels in this release.
- **Session gate — an account is the browser's logged-in state**:
  `core/session_gate.py` is the single source of per-site login probes (300s
  cache, zero credentials). Real writes on CDP channels are gated after the
  approval check: a missing login state is rejected explicitly instead of
  surfacing as an indeterminate vendor failure. `mkt check` renders from the
  same table, `grow launch` verifies X login before the first reading, and
  `grow doctor` reports per-channel login state (logged-out is a blocker,
  missing debugging Chrome a warning).
- **Supply staging ahead of the day**: `grow_agent.produce` now fills deficits
  across the `supply.min_cover_days` window (per-day, keyed by
  `scheduled_for`) instead of only today — matching the doctor/grow_schedule
  supply check, so the "缺 1 条" evening warning is actually fillable in
  advance. Readings (`x stats`) report a missing login state explicitly
  instead of a vague "取不到", and `mkt check` can now probe Zhihu login
  state (manual-tier channel, visibility first). `grow_schedule.supply_status`
  gained `done_today` so a day's already-committed quota counts as coverage —
  the evening "缺 1 条" warning no longer fires when the day is actually done.
- **Local Codex content production**: bounded candidate qualification, drafting,
  independent review and queue replenishment using the locally authenticated
  Codex CLI. Retry backoff, daily attempt limits and per-run locks protect the
  execution loop. New runs configure the writer; live sending still requires
  standing authorization and explicit arm.
- **Relevant KOC discovery**: vertical evidence, precise queries, commercial
  signal scoring, cross-day author deduplication and an auditable target list.
  `mkt target` manages candidate → contacted → engaged → amplified → partner;
  each transition requires an evidence note. Tactic runs consume KOC outcomes.
- **Content operations and tactics**: editorial review, reader-first expression,
  channel capability checks, executable tactic constraints and evidence expiry.
- **Operations tools**: daemon, heartbeat, doctor, scheduler installation, daily
  Markdown/HTML cards and weekly result cards. Card delivery generates local
  files; sending email is a separate explicitly live operation.

### Fixed and integrated

- Integrated the evening iteration: 82 tactics, goal-based portfolio selection,
  read-only competitor page monitoring and MCP tactic access. Goal compilation
  embeds a white-risk advisory portfolio within the explicitly selected channels.
- Browser login checks require a positive authenticated UI marker; an unchanged
  URL is insufficient. Native Playwright `no_defaults` replaces dependence on a
  locally patched driver when supported by the installed version.
- Future supply crosses weekly quota boundaries. Competitor monitors ignore
  scripts, empty/challenge pages and failed fetches, isolate event histories by
  list and report changes as needing review rather than confirmed price hikes.

- Applied target trust scoring to automatic prospect ranking; unknown sponsor
  data stays unknown. Weekly target metrics count the correct event and time
  window, and KOC metrics exclude large accounts.
- Excluded stale queues from production inventory so expired content cannot
  prevent replenishment. Preserved stale-content rejection and send pacing.
- Bound generated launchd/cron/fallback scripts to their product workspace and
  stored absolute product-facts paths. Included reviewed experience records in
  installed distributions, with an isolated wheel acceptance check.
- Added atomic persistence and concurrency guards, target-list overwrite
  protection, normalized account deduplication and ambiguous-handle rejection.
- Unified X reply/publish execution, stable logical post identities and browser
  tab reuse. Readbacks select the actual published post, deduplicate daily
  measurements and require sufficient samples before proposing adjustments.
- Kept pause, suppression, budget, approval, identity and indeterminate-result
  safeguards in the shared execution path. Sending does not prove conversion;
  volume increases remain user decisions.

### Validation and limits

- 1,075 offline tests, zero-network demo, doctor self-test and isolated wheel
  acceptance. CI verifies Python 3.9 and 3.11 before release.
- X analytics readers remain UNVERIFIED until calibration is complete. Missing
  metrics remain unknown; no business growth or conversion is claimed.
- Existing live runs keep their authorization, quotas and model configuration.
  Enable the writer with `mkt grow agent <id>` when needed. Scheduler scripts
  generated before this release should be regenerated to bind the workspace.

## 0.13.0 - 2026-11-05

This release adds account-identity binding to standing authorization, closing
the gap between a declared account label and the platform identity behind it.

### Added

- **Identity records in routine policies**: `mkt policy enable --identity
  account=platform:user_id` (repeatable) plus `--require-identity`. Policies
  store `account_identities` and the requirement flag.
- **Identity-aware execution gate**: `allows(..., platform_user_id=...)` and
  `mkt action --platform-user-id` verify the platform user id when supplied;
  `--require-identity` rejects execution without a matching id.
- **Policy check/show**: `mkt policy check --platform-user-id` reports the
  identity result; `show` lists identity records and still audits broad scopes
  and missing expiry.

### Boundaries

- Identity records are verified_at-stamped audit data supplied by the operator;
  the repository does not perform OAuth or call platform APIs to prove them.
- Queue/pipeline callers must pass `platform_user_id` to benefit from the
  requirement; policies without identity records behave as before.

## 0.12.0 - 2026-11-05

This release turns declared execution and verification inputs into traceable
ones. It responds to the 2026-11-05 competitiveness review: design intent must
reach the execution payload, and result verification must rest on receipts
rather than booleans.

### Added

- **Execution payload binding** (`core/work_actions.py`): an accepted
  Deliverable may declare `execution_payload` {strategy, targets}. Materialize
  requires a byte-identical payload; legacy deliverables without it need an
  explicit `allow_unbound` and are recorded as `unbound_explicit` in
  provenance. Every request stores `payload_sha256` and binding mode.
- **Data receipts for verification** (`core/gtm_design.py`): `data_receipts`
  carry source, connection, event, window, sample and receipt id. `verifiable`
  now requires an event/window/sample receipt for event objectives; booleans
  (`data_connected`, `events_verified`) alone can only reach `declared_only`.
- **Motion hypotheses with fit** (`gtm_design`): proposals may carry
  `fit.audience_fit`, `fit.resource_fit` and `evidence`; accepted motions are
  ordered by fit, and motions without evidence are marked as hypotheses and
  listed in `combination.evidence_gaps`.
- **Policy audit**: `authorize(actions=None)` now grants only `email:message`
  at the API level too; `mkt policy show` warns about non-default actions in
  legacy policies and about missing expiry.

### Boundaries

- Payload binding proves the execution request matches the accepted content; it
  does not prove platform permission or business outcome.
- Receipts are operator-supplied records; the module does not verify a live
  database connection.
- Motion fit is an explicit planning judgement, not measured channel effect.

## 0.11.0 - 2026-11-05

This release merges the P0 execution-integrity work and fixes the design-layer
gaps found in the v0.10.0 review. The safety fixes are the priority: they were
developed on an integration branch and are now part of the released version.

### Security and execution integrity

- **MCP cannot raise channel permissions**: `allow_unverified` is removed from
  the tool surface and bypass attempts are rejected.
- **Unknown external results are first-class**: X execution claims intent
  atomically, records unknown outcomes, and requires human adjudication instead
  of blind retries.
- **Ledger integrity fails closed**: append is locked and atomic; corrupt lines
  raise instead of being silently dropped.
- **DNC suppression fails closed**: damaged or wrongly typed suppression data
  stops sending rather than skipping the broken record.

### GTM design fixes

- **User objective wins over Agent proposal**: `--objective` is never silently
  overwritten by `proposal.objective`.
- **Partial proposals no longer block**: object/objective/motion/channel are
  overridden independently; a proposal containing only channels keeps the
  inferred motions.
- **Motion combinations**: up to three motions with one primary and supporting
  motions that supply upstream conditions; `selected_motion` remains for
  compatibility.
- **Three readiness states**: `design_status`, `execution_readiness` and
  `measurement_readiness` are separate; `ready` no longer means executable.
- **Stricter verification**: `verifiable` requires a connected source; event
  objectives additionally require `events_verified`. Declared-only sources are
  marked `declared_only`.
- **Handoff binds the design**: `handoff` refuses `blocked` designs, passes the
  mapped orchestration recipe instead of re-inferring, and emits an
  `execution_plan` with a design SHA-256 fingerprint.
- **Narrower routine default**: `mkt policy enable` without `--actions` now
  authorizes only `email:message`; other actions must be listed explicitly.

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
