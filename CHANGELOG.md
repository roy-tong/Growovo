# Changelog

## 0.23.0 — 2026-11-26

### Added

- Persistent lifecycle projections for contacts, conversations, opportunities, activation, retention and follow-up queues, with explicit cross-source identity maps and shared suppression. Product feedback exports require named classification and exact source quotes without becoming canonical demand evidence.
- Explicit Host Agent wake/proposal contracts, bounded execution-feedback cycles, controlled local Agent discovery/install/first-success runs, shared experiment exports, channel efficiency and business-first period reports.
- Opportunity freshness contracts keep expired or unknown-freshness records visible but out of execution and lifecycle follow-up queues.

### Fixed

- Retention requires a verified event and an earlier verified activation; unverified or unsupported events stay visible without advancing lifecycle state.
- Period reports assign execution/results by `executed_at` rather than backdating cross-midnight work to cycle creation. Result-unknown and missing-grant cycles expose reconciliation or incremental-permission next actions without automatic retry.

### Verification

- 2598 offline tests and the zero-network demo pass. Isolated wheel chains preserve the boundary between committed actions, interactions, activation, retention and causal growth; no production continuous worker or external business outcome is implied.

## 0.22.0 — 2026-11-25

### Added

- GEO workspace: `mkt geo entity|questions|run|capture|score|report|facts` stores a canonical entity fact table, frozen question sets, tiered observations, Wilson intervals, explicit `not_run` rows and content-bound reports. `mkt seo audit` adds a read-only robots/sitemap/canonical/hreflang/JSON-LD/duplicate/doorway audit. Captures stay import-first: no login, CAPTCHA bypass or automatic consumer-AI querying.
- Risk-tiered authorization: `growth authorize grant|revoke|list` and `growth policy explain` cover action class, scope, quota, expiry and revocation. Routine replies and outreach require grants; public statements and money/contract actions require named review and cannot be standing grants.
- Observable growth cycle: `growth cycle request|propose|execute|status|list` records `growth.cycle-request/1` → `growth.cycle-proposal/1`, applies the risk policy to every proposal, refuses missing grants/quota/DNC/expired cycles, and routes accepted actions through the existing execution gateway. The worker reports due cycles but never captures or sends on its own.
- GEO experiments and agent readiness: `mkt geo experiment create|report|status` requires a pre-registered baseline/intervention/control design and writes `inconclusive` when the sample is too small. `mkt geo agent-check` compares entity facts with `llms.txt`/MCP/AGENTS and reports crawler/usage counts as a lower bound.
- Account-level daily caps: `core/throttle.py` resolves `data/policies/account-quotas.json` by platform plus account id/handle. X Premium's 500 posts/day is an owner account fact, not a global default; unknown accounts keep the conservative budget.

### Changed

- Result cards now lead with the business goal, confirmed actions, target-audience interactions, activation/payment status, platform estimates and plain-language next steps. Execution receipts, L1/L2/L3 codes, attribution, evidence coverage and replay boundaries remain in the appendix with unchanged calculations and no inference from actions to conversion.
- Channel actions can no longer be marked `completed` without a completion type. `verified` needs a receipt (`action_id`, `channel`, `sent_at`, `idempotency_key`, `readback`); `attested_manual` needs a named operator and a reason. Metrics split into `completed_verified` / `completed_attested` / `completed_declared`, and downstream actions resting on a manual attestation are marked `dependency_quality: attested`.
- `growth evaluate` records per-metric provenance and adds `primary_verified` next to `primary_success`. Bare numbers stay `owner_declared`; only same-definition observed or independently verified pairs can be `primary_verified`, and unverified numbers no longer trigger a "repeat" recommendation.

### Fixed

- Execution leases carry a monotonic fence in `execution_fence_counters`. A heartbeat that loses the lease marks its state lost, and `run_action` re-checks ownership at entry and before dispatch; a lost lease returns `lease_lost` with `retryable: false` instead of writing.

### Verification

- 2542 offline tests and the zero-network demo pass; new coverage includes lease takeover, completion-type enforcement, provenance, GEO scoring/reproducibility, SEO fixtures, risk grants, cycle refusal paths, account-level quota resolution and business-first result cards. A wheel installed into an empty venv exposes `mkt geo`, `mkt seo` and `mkt demo`.

## 0.21.0 — 2026-11-24

### Added

- AI Growth Service now persists goals, domain state, conversations, opportunities, assets, experiments, attempts and connector cursors in SQLite with JSON compatibility projections. GitHub App Issues/Discussions and Gmail two-way synchronization join the same runtime.
- Account × platform × action × route capability records separate implemented, available, verified and authorized read/write/readback state. Long-running workers share the unified scheduler and action gateway.

### Fixed

- Final release review rejects credential-shaped fields throughout capability records, including client secrets, API keys, passwords and private-key paths.
- Account/route execution leases now renew while a slow write is active; an expired lease cannot let a second writer enter merely because the first action exceeded five minutes.

### Verification

- 2461 offline tests and the zero-network demo pass, including slow-writer lease renewal, credential-field rejection, GitHub/Gmail local golden chains and the unified runtime regressions. Real canary evidence remains scoped to the explicitly documented GitHub/Gmail/X actions; no production worker or business outcome is inferred.

### Changed

- Business execution level (AUTO/GATED/MANUAL) is now independent of technical first-run status. X, Discord and Email are AUTO; GitHub remains GATED. The blanket fail-closed rule for non-API X browser writes recorded under 0.18.1/0.19.0 was superseded; account authorization, observed identity, pause, DNC, quotas, readback and unknown-result reconciliation remain. See `docs/feature-status.md` (2026-11-24).
- X `publish`/`reply` now default to `browser-attach` (WebBridge managed tab), so identity observation and execution share one browser; routine X writes also use owner-attested instead of defaulting to routine CDP, and X reads default to WebBridge. Vendor CDP 9222 is explicit `--legacy-cdp` only. `mkt x` joins `action_gateway`, and `cdp.connect` no longer creates a WebBridge tab before connecting to the debug browser. See `docs/feature-status.md` (2026-11-24).

## 0.20.0 — 2026-11-23

### Changed

- Business observations now distinguish `owner_declared`, `observed` and `independently_verified`. Hash-bound evidence, subject identity, metric definitions, pair identity and legal time order are required for corresponding grades and deltas; late baselines invalidate the old pair.
- `runtime` and `outreach` register through the shared `CommandRegistry`. The offline demo writes four OperationsLog rows and `data/demo/proof-bundle`, with the intentional d-003 failure preserved as `proof_status=FAIL`.
- Chinese and English public pages split X read-only verification from DISABLED non-API writes and document the three observation grades and demo proof path.

### Fixed

- Proof traces omit `external_id` when no platform receipt exists instead of writing a contract-invalid null value. Mixed naive/timezone-aware timestamps now validate without crashing the shared acceptance layer.
- Independent verification now requires an independent control domain, authority and verified result. Comparisons expose deterministic IDs and superseded history, and MCP joins the existing X negative-write matrix.
- `mkt validation run` adds read-only real-user validation orchestration with evidence-gated states, second-batch proof, core adoption metrics and separate contract/business/time semantics.

### Verification

- 2257 offline tests pass, including observation pairing, independent verification, shared CLI registration, demo proof export, validation orchestration and public-claim regressions. The common Proof validator returns the expected business FAIL with no structural errors.

## 0.19.0 — 2026-11-23

A compatibility-preserving architecture release that combines the v0.18.1 platform-security boundary, evidence-qualified review operations, deterministic execution evidence and a progressive modular runtime.

### Added

- **One runtime and policy contract**: `RunContext` correlates run/attempt/action ids through manifests, wave results and operations logs. Atomic JSON/JSONL storage fails loudly on corruption; EffectivePolicy binds a canonical policy hash and ordered decision explanations without replacing existing approval or transport gates.
- **Durable local execution state**: queue snapshots now replay consume events after interruption without replaying external sends. Both scheduler interfaces share one job state machine for dependencies, expiry, interruption, lock deferral and indeterminate no-retry handling.
- **Deterministic evidence and projections**: `cycle report`, independent structural/live receipt audits, inbox ownership, eligible-supply forecasts, KOC qualification, business baseline/final observations and rebuildable named projections separate execution receipts from replies, product use and causal claims.
- **Qualified outreach and feedback**: B2B company-to-person mapping, same-day public hooks, warm/expert draft gates and scene-routed my-voice metadata produce unapproved DraftBundles only. Append-only feedback contributions require review and a real change reference before `merged`; no automatic repository write, approval or send is added.
- **`mkt review-ops`** adds named-person/account mapping, cross-campaign cooldown, company spacing, specific review artifacts and anti-template rules for native unapproved email/GitHub queues. Business goals define reader value, observable outcomes and non-goals; capacity is three deep-research items per channel per wave with no quota padding. Confirmed replies project to idempotent private feedback drafts without external actions.
- **Modern lazy CLI and compatibility handlers**: `runtime context/project`, `outreach prepare/feedback`, `review-ops` and `proof export` coexist with existing cycle/supply/grow commands.

### Changed

- Planning uses a one-pass ledger index and batch inbox terminal lookup. On the local 500-action/5000-ledger fixture, planning fell from about 0.397 seconds to 0.0276 seconds with the same 500 scheduled actions.
- Queue persistence compacts snapshots after local consume events; malformed queues/journals/schedules fail before an external write instead of becoming empty work.
- Chinese and English landing pages combine the growth value proposition with the exact automation boundary and a machine-readable `growovo:auto-channels=email` declaration.

### Security

- All v0.18.1 X protections remain: scripted non-API X browser writes fail closed before vendor execution or committed-ledger append. X read-only collection, dry runs, drafts and human handoff remain; automated writing requires a compliant official API.
- Pause, DNC/suppression, approval, identity, readback, independent safety caps and unknown-result reconciliation remain mandatory. Content adapters cannot select destinations, approve, send, commit or push.

### Fixed

- Operations logs accept target rejection, unavailable/unrenderable, deferred and blocked outcomes. Indeterminate wave results stop the affected channel pending named human resolution.
- Discord native reply fixtures now verify explicit reply-target binding and a genuinely new readback row; production fail-closed checks are unchanged.

### Verification

- 2242 offline tests pass, followed by the five-check zero-network demo. Wheel and sdist build successfully; isolated installed-wheel acceptance passes outside the source checkout. No real account sends or X writes are performed.

## 0.18.1 — 2026-11-23

### Security

- X is no longer eligible for scripted non-API browser writes. Both standing owner-attested `browser-attach` and one-off `user_visible_cdp --go` return `platform_permission_required`, even with a complete operator file, observed identity and available quota; all four write actions are refused before the X vendor. `cycle` excludes X before dispatch, and the browser-attach adapter refuses before its engine, quota reservation or committed ledger append. X read-only collection, dry runs, drafts and human operation directly in X remain; automated writing requires a separately connected, compliant official API. GitHub/Discord owner-attested behavior is unchanged. The offline suite and five-check demo pass without any real X write.

### Changed

- The controlled-run pages now separate execution, qualified conversations, independently observed product use and second-batch evidence. A sanitized task-result Issue Form and Discord links provide a public feedback path without private recipients, content or credentials. Chinese/English landing pages, FAQ, JSON-LD and `llms.txt` use the same X platform boundary as the execution gates.

## 0.18.0 — 2026-11-22

### Added

- **Installed demo workspaces stay outside the package.** `mkt demo` writes to `./data/demo` after wheel installation and honors an explicit `--workspace`; source-checkout behavior is unchanged. The approved queue contains only the three executable fixtures; the placeholder sample stays outside approval and demonstrates rejection separately.

- **Plan approval now rechecks every bound queue** through `lint_queue` and the shared read-only content/target gate before writing an approval. Invalid or placeholder-bearing content is rejected before a plan can be approved; the existing plan+queue+account+workspace fingerprint remains unchanged.

- **`mkt --workspace DATA proof export RUN --out NEW_DIR --by NAME`** creates a read-only Prove Anything bundle without external actions. Private targets, bodies, accounts, subjects and authorization documents are excluded. Execution receipts, independent readback and external events map separately to P0/P2/P4; unknown states remain INDETERMINATE and no causal credit is assigned.

Quota limits now slow work down instead of closing it. Approval, dry-run, pause,
DNC/suppression, identity binding, ledger dedupe, readback and unknown-result
protections are unchanged.

### Changed

- **Over-limit email and queue batches are sliced, not refused**:
  `channels/email_smtp.py` and `core/queue_runner.py` send the part that fits
  the per-run cap and the remaining daily budget, then return the rest as
  `result: "deferred"` rows carrying a `next` hint. A dry run is never sliced,
  so a preview without `--go` still shows the whole batch. Approval, pause,
  DNC/suppression, identity, readback and unknown-result refusals still stop
  the batch.
- **`core/throttle.py` exposes the limits as advice**: `run_cap`,
  `slice_batches` and `advice` report the per-run cap, how many batches with the
  configured pause the requested size would take, and how many rows move to the
  next budget day (UTC midnight, 08:00 Beijing time) or to another channel.
  `check_run` keeps its existing raise-on-over-limit contract for callers that
  want a hard refusal.
- **Single-action quota refusals name the next step**: `channels/linkedin.py`
  and `channels/x/adapter.py` say whether to resume next budget day or use
  email instead, and warn that topping up the same day raises platform risk.

### Added

- `tests/test_core.py` pins the slicing boundaries (120 rows at a cap of 50 →
  50/50/20), the unlimited case and the advice text.

## 0.17.1 — 2026-11-21

Fixes the 0.17.0 wheel, which omitted `core/bridge` and made
`core.browser_attach` unimportable after a pip install. Source checkouts were
not affected.

### Fixed

- `[tool.setuptools] packages` now lists `core.bridge`, so the installed wheel
  carries the browser bridges the attach transport imports.
- `tests/test_packaging.py` fails when a package directory on disk is missing
  from that list, so the next subpackage cannot repeat this.

## 0.17.0 — 2026-11-20

A nightly engagement cycle that acts in the operator's own logged-in browser:
it reads notifications first, replies to people who answered, then interleaves
X, GitHub and Discord actions under per-platform limits.

### Added

- **`browser-attach` transport reuses the operator's open tabs**: `core/browser_attach.py`
  drives the WebBridge extension against tabs the operator already has open, so
  no separate debug port, no browser launch and no copied cookies. Reads and writes
  both default to reuse-only: a missing site tab raises `tab_not_found`; only an
  explicit low-level `ensure_tab(..., allow_new=True)` may open one, and `cycle`
  never enables it. The logged-in account is read from what the page shows — X's
  account menu `@handle`, Discord's user panel username, GitHub's `user-login`
  meta tag. The identity record keeps `mode=cdp_observed` as the existing gate
  label for "observed from the browser that will execute"; it does not imply CDP.
- **`core/inbox_replies.py` collects who is waiting for an answer**: X mentions
  from the notifications page, GitHub unread notifications filtered to mention
  and comment reasons through `gh api`, and Discord replies found in each
  allowed channel. `plan_replies` orders X, GitHub, Discord and caps actions per
  person per night.
- **`core/engagement_cycle.py` plans and runs one wave**: `plan` puts inbox
  replies first, then rotates platforms so two actions on the same platform keep
  a minimum gap, and refuses an action with a named reason. `enqueue` atomically
  merges prepared drafts; real waves use a non-blocking lock, atomic queue writes,
  a hard execution deadline and ledger-based counts. `run` calls
  `core/action_runner.run_action(transport="browser-attach")` and stops a channel
  on a platform warning instead of retrying.
- **`core/browser_write.py`** is the browser-attach transport adapter for X
  reply/publish, GitHub comment and Discord channel post: pause, do-not-contact,
  owner-attested authorization, throttle reservation, readback and one ledger
  append run in that order. `action_runner` adds normalized results, task outcomes
  and operations logs without a second ledger append or quota reservation.
- **`mkt cycle` CLI**: `smoke`, `quota`, `pending-auth`, `attach`, `inbox`,
  `enqueue`, `plan`, `run`. `attach` and `inbox` are read-only. Single actions and
  non-email queues can explicitly select `--transport browser-attach`.

### Changed

- `core/transports.py` puts `browser-attach` first for X publish/reply and
  Discord publish, and second after the API for GitHub comments.
- `core/platform_compliance.py` accepts `owner_attested_session` as a route that
  still requires a freshly observed browser identity, and
  `core/execution_policy.py` issues and checks the per-platform authorization
  files under `data/policies/owner-attested-<platform>.json`.
- `core/throttle.py` counts `browser-attach` writes in the per-platform budgets.
- **Nightly capacity covers the requested nine full waves**: the X ceilings are
  now 90 original posts and 180 replies per night, matching 10/20 per hour for
  nine waves; GitHub remains 135 and Discord 54. The supply panel tracks the
  full 10/20/15/6 per-wave preparation targets. `plan` and `run` default to a
  50-minute budget, leaving ten minutes before the next hourly wave. This
  workspace uses calendar-hour quotas so a previous 43.5-minute wave does not
  consume the next wave's entire rolling-hour budget; unconfigured workspaces
  retain rolling 60-minute quotas. Channel gaps and all stop conditions are
  unchanged. These are capacity ceilings, not instructions to fill them with
  unqualified content. A nine-wave simulated ledger schedules all 459 actions
  without exceeding any hourly or nightly ceiling.

### Fixed

- **`cycle run` and `cycle attach` now share one WebBridge session**:
  `run` reached `BrowserAttach()` through `core/browser_write.execute`, which
  fell back to the environment/default session (`growovo`) while `attach` had
  been pointed at `ovo-night-run`. In that default session the only X tab was a
  hidden notifications page, so every X action was refused as `tab_hidden` for
  the whole night. The policy now carries `bridge.session`, `run` and
  `collect_inbox` accept `--session` and apply it to the bridge before planning,
  and the wave summary reports the session actually used. Covered by
  `tests/test_engagement_cycle.py::BridgeSessionTest`.
- **Discord submits now happen, instead of typing the text and stopping**:
  `core/bridge/discord_engine.py` sends Enter by dispatching a `keydown` inside
  the page on the composer. `Input.dispatchKeyEvent` never reached the page
  through the WebBridge bridge — a `keydown` probe installed on the document
  recorded nothing while `Input.insertText` over the same channel worked — so
  the composer kept the text and the channel got no message. Three channel
  posts on 2026-11-21 01:10 confirmed the new path against the message list.
- **`actor_cap` no longer blocks our own original posts**: `usage()` counted
  every committed row's `actor`, so the X original posts written as
  `actor=owner` consumed the "two actions per external person per night" budget
  and the third original post of the night was refused. The cap now applies only
  to `action=reply`, matching the docstring and the policy note, and `plan()`
  derives `actor_key` the same way. Covered by
  `tests/test_engagement_cycle.py::PlanTest::test_own_publish_does_not_consume_actor_cap`.
- **Drafts left over from an earlier night no longer go out as if fresh**:
  `core/engagement_cycle.py` refuses a non-inbox draft older than
  `queue.max_age_hours` (12) with `stale_row`, so a wave that could not send a
  queued row in its own night does not spend the next night's quota on stale
  text. The age comes from the row's `queued_at`, falling back to midnight of a
  `night-YYYYMMDD` campaign; a row with neither stamp stays eligible rather than
  discarding an operator's text. `run` prunes stale rows from the queue file
  only under `--go` and counts them separately as `pruned_stale`, not
  `consumed`; a dry run reports them in `stale` and writes nothing. Covered by
  `tests/test_engagement_cycle.py::StaleQueueTest`.
- **Legacy cycle receipts consume the same quotas**: committed rows written
  before the `transport` field was populated now count toward the nightly and
  rolling-hour budgets. Other explicit transports remain outside the
  browser-attach budget, so the five legacy rows from the first night are no
  longer omitted from X and Discord usage.
- **Inbox collection cannot overwrite an executing wave's queue**:
  `cycle inbox` merges through `engagement_cycle.merge_inbox` under the same
  non-blocking lock as `run` and `enqueue`. A lock conflict returns
  `wave_already_running`, exits 2 and leaves the queue unchanged instead of
  reporting a successful merge.
- **Single browser actions inherit the configured WebBridge session**:
  `mkt action --transport browser-attach` now applies `bridge.session` from the
  cycle policy instead of silently staying in the default `growovo` session;
  `--session` remains available as an explicit override. This fixes the
  pre-send `tab_not_found` found by the 2026-11-21 live canary.
- **X readback allows for delayed profile rendering**: the default condition-
  polling window is 30 seconds rather than 12. The live canary post appeared
  after the old window and was found later by the same reader without another
  send; a fake-clock regression covers a post that first appears at second 13.
  No post-fix live post has been made, so this remains offline-verified.
- **A missing site tab defers only that channel, not the whole wave**:
  `core/engagement_cycle.py` records `tab_not_found` / `tab_open_timeout` in
  `deferred_channels` and continues with the other platforms, so a night with
  no Discord tab no longer aborts the X portion. Covered by
  `tests/test_engagement_cycle.py::MissingTabTest` and exercised against the
  live session: the wave reported `ok true`, `committed 0`,
  `deferred_channels ["discord","github"]`, and wrote no ledger row.
- **X original failures now carry a readable reason**: a send whose composer
  did not clear returned `err: null`, which is why the 2026-11-20 23:10 ledger
  row read `failed` with an empty error. The engine now reports
  `composer did not clear after send`; the click was still dispatched, so
  `browser_write` classifies it as `indeterminate`, never `failed`. Covered by
  `tests/test_x_engine.py::SendReportsStatusTest::test_original_reports_readable_error_when_composer_does_not_clear`.
- **A reply composer that does not open gets one clean reload**: when Reply is
  clicked but no composer appears within eight seconds, `reply_native` reloads
  the status page and clicks Reply once more before failing. Nothing has been
  typed at that point, so the retry cannot double-post. The failure detail now
  reads `no composer after reopen` and keeps the second attempt's reason in
  `reopen_err`. Covered by
  `tests/test_x_engine.py::SendReportsStatusTest::test_reply_reopens_composer_after_full_nav`.
- **Ledger details keep enough of the engine output to diagnose a failure**:
  the per-row `detail` cap moved from 400 to 1200 characters so fields such as
  `enter`, `composerAfter` and `send` survive. The classification is unchanged.

### Notes

- `mkt cycle run` uses `core/action_runner` with an explicit browser-attach
  transport. Its owner-attested authorization intentionally replaces the generic
  plan/routine approval check, not the other safety gates; the operations log
  records the route, platform and authorization-file SHA-256. X status IDs and
  permalinks, verified GitHub URLs and real Discord server IDs flow into receipts;
  prefix-only X readback does not claim a full-text hash match.
- Premium posting ceilings are not published by X. The configured budgets
  (90 original posts and 180 replies per night) are self-imposed capacity
  ceilings derived from the requested nine waves, not a platform guarantee or
  a claim about the account's official maximum.
- Discord has no per-message reply through this path; a Discord answer is a
  channel post that mentions the person.

## 0.16.3 — 2026-11-20

Stable task identifiers in the Verovo handoff, so the ledger can name what it
received instead of inventing ids.

### Added

- **the Verovo handoff names every task it carries**: an operation becomes
  `growovo:<run>/op/<id>` and a business event `growovo:<run>/ev/<id>`, listed
  in a `task_bindings` array that sits inside the handoff integrity hash.
  `write_handoff` returns the binding count so the caller can print what it
  produced, and the package's assumptions state that a task id is stable only
  inside the package — it is not the buyer's business task number. The
  downstream ledger stores bindings as given: a binding names evidence, it
  never upgrades it to a booking. `tests/test_verovo_handoff.py` covers the
  shape and both prefixes (5 cases).

## 0.16.2 — 2026-11-20

Round 3 usability close-out: the CLI contract, shared error semantics, handoff
premises and content counter-examples. No new platform or channel capability.

### Changed

- **CLI operation contract is one rule instead of four habits**: `queue
  build/run/lint`, `campaign` and `review` resolve `--out`, print the resolution
  rule ("relative paths resolve under the workspace `data/`") and report the
  absolute path they actually used. Missing arguments and business refusals now
  raise the structured error below instead of a traceback. **`queue run` exits 2
  on a business refusal; it used to exit 1.** Exit 3 still means an unknown
  external submission. Scripts that treated every non-zero exit as "unknown
  send" must check 3 explicitly.
- **SOP coverage is visible where the user reads it**: `mkt sop coverage`,
  `mkt sop list` and `mkt capability lint` print `SOP 覆盖 N/18 个能力域有可执行
  SOP（未覆盖 …）` so "已运行" is not read as "全部能力域已覆盖". On this build
  the honest number is 1/18.

### Added

- **Seven shared error families, one vocabulary, no shared database**
  (`core/error_semantics.py`, [docs/error-semantics.md](docs/error-semantics.md)):
  NEEDS_USER_INPUT, UNSUPPORTED_CAPABILITY, INVALID_INPUT, PERMISSION_DENIED,
  SAFETY_BLOCKED, STALE_OR_CONFLICT, CONTRACT_ERROR. Every user-facing refusal
  names what is missing, its impact, the legal next action and a copyable
  example. `GROWOVO_ERROR_JSON=1` prints the same payload as JSON
  (`growovo.error/1`). Verovo maps its frozen C-01 codes to the same families in
  `buyer_ledger/error_semantics.py`; Askovo is unchanged this round.
- **`grow handoff-verovo` carries premises, not just evidence**: the handoff now
  includes a `premises` block (source objects and versions, scope, does-not-cover,
  assumptions, limits, manual review state, evidence refs) inside the
  integrity hash. Verovo preserves it verbatim, records
  `premises_do_not_upgrade_grade: true`, and adds one open item to re-check the
  premises against buyer-controlled sources. Old handoffs without premises still
  import.
- **G-18 counter-example fixtures and a human usability checklist**:
  `content_problems()` now also rejects one sentence pasted into three or more
  non-identifier fields (`TEMPLATE_REPEAT`) and copy whose `context_ref.version`
  is missing from, or older than, the referenced context (`STALE_REF`).
  `tests/test_content_trust_fixtures.py` holds ten counter-examples (placeholder,
  repeated template, generic advice, wrong audience, wrong product fact, wrong
  objective, too short, stale version, missing version) that must be rejected at
  both submit time and review time; two repetitions still pass, freezing the
  threshold. The file documents the six-item human usability checklist. Passing
  the gate still does not prove the copy is worth sending.

### Tests

- Full suite: `Ran 1828 tests in 45.729s — OK`; `python3 mkt.py demo` PASS;
  `python3 mkt.py voice plain` PASS.
- New: `tests/test_cli_contract.py` (6), `tests/test_content_trust_fixtures.py`
  (10), two premises cases added to `tests/test_verovo_handoff.py` (now 4).
  Content gate plus fixtures: 30 tests OK.

## 0.16.1 — 2026-11-20

G-AUTO-01, G-GUI-01 and G-13b from the 2026-11-20 main review.

### Fixed

- **P0 G-AUTO-01 platform capability and platform permission are now the same
  question**: `platform_caps.classify()` reports both the technical tier and
  whether autonomous execution is allowed, and a channel that is technically
  AUTO but has no autonomous permission is downgraded to `GATED`.
  `is_auto()`/`auto_channels()` now require both, so the compiler,
  `grow runbook`, `mkt grow platforms` and the wizard only treat Email as an
  autonomous channel. A plan that asks for autonomous X execution is refused
  at compile time (`UNSUPPORTED_SCOPE`) instead of passing setup and being
  rejected at run time; X remains available as an assisted, single-shot
  action. README (EN/CN), the growth-for-engineers guide, the feature status
  and the platform automation matrix now say the same thing: only Email is
  promised unattended autonomous execution.
- **P1 G-GUI-01 the local control panel checks who is asking**: requests are
  rejected unless `Host` is one of the loopback authorities the server is
  bound to, and write requests must carry an allowed `Origin` (non-browser
  agents without an Origin header still work). A blank or whitespace-only
  token now refuses to start with `TOKEN_REQUIRED`.
  `tests/test_gui_security.py` freezes the boundary with 12 adversarial cases:
  three loopback hosts, malicious Host, DNS-rebinding Host, wrong port, empty
  Host, cross-site Origin, `Origin: null`, same-origin write, agent write
  without Origin, wrong token, empty token.
- **P1 G-13b an unknown send result or a broken audit log halts the batch, not
  just the call**: an `indeterminate` SMTP result now writes a persistent
  batch block (`data/state/email-batch-block-<campaign>.json`) and stops the
  queue; a failed operation-log write does the same, because the audit chain
  is no longer continuous. Re-running the queue refuses with `BatchBlocked`
  until an operator inspects the situation and passes `--resume-batch`; the
  MCP `run_queue` path does not expose that flag, so an agent cannot clear a
  block by itself. The block reason, target and next action are written to
  the email result JSON and to stderr.

### Tests

- `tests/test_gui_security.py` + `tests/test_gui_server.py`: 28 tests OK.
- `tests/test_email_indeterminate.py`: 13 tests OK, including cross-process
  block persistence, resume clearing, and audit-gap halting.
- Full suite: `Ran 1799 tests in 45.192s — OK`; `python3 mkt.py demo` PASS.

## 0.16.0 — 2026-11-20

G-18 from the 2026-11-19/20 full-scenario end-to-end test.

### Fixed

- **G-18 the acceptance gate now judges content usability, not field presence**:
  35 records carrying `raft:` placeholders had passed acceptance because every
  required field was non-empty. Acceptance now rejects placeholder markers
  (`raft:`, `TODO`, `TBD`, `{{...}}`, `lorem ipsum`), template residue,
  copy below the minimum semantic paragraph count, and copy whose goal,
  audience or product facts do not match the queue record. Each refusal names
  the missing or offending item and the fix. `tests/test_content_trust_gate.py`
  holds the counter-example fixtures (placeholder, generic, wrong audience,
  wrong product fact) and asserts each is refused.
- **SOP coverage is printed as N/18**: `mkt sop` and the queue summary show the
  actual covered domain count, so "ran" is not read as "all capabilities
  covered".
- **Deliverable handoff carries the same trust verdict**: `core/deliverables.py`
  refuses to export a deliverable whose copy failed the trust gate.

### Note

The regex + minimum-length + required-section check is the first gate only.
It does not by itself prove the copy is valuable; human usability review stays
required before a real campaign.

G-12 / G-13 / G-14 from the 2026-11-18 review.

### Fixed

- **G-13 SMTP unknown result stops the batch**: `send_queue` halts on the first
  `indeterminate` (the server may have accepted the message), marks the record
  `halted`, writes the batch summary with `not_attempted`, and the CLI exits 3
  for `mkt email` and `mkt queue run`. The target stays on the ledger's
  no-retry list until `mkt ledger resolve`; the fake-SMTP test injects a
  post-DATA timeout and asserts one send, zero follow-ups and no auto-retry.
- **Operations log lives in the unified email core**: `send_queue` records every
  attempt (queue and direct CLI share one entry), and a failed write leaves
  `audit-gaps.jsonl` plus `rec["audit_gap"]`, which also exits 3 — a sent action
  with no operations row is never presented as fully verified.
- **G-14 X autonomous and user-visible CDP writes are separated**: routine/non-API
  autonomous writes still call `require_autonomous_write` and fail closed. A
  non-routine `--go` publish/reply may instead use `user_visible_cdp` only after
  `cdp_observed` captures the actual browser account and proof ref; existing
  plan/adhoc approval, pause, DNC, quota, idempotency and receipt gates remain.
  Message/engage and standing CDP automation are not included. The matrix exposes
  `policy.autonomous_write=false` plus `policy.user_visible_cdp_write=true`.
  This is an operator-requested route, not a claim of X approval; no live write
  was performed by the tests.
- **G-12 content positioning follows product facts**: the hardcoded
  “硬件产品经理/电商运营” reader, “硬件主线/视频工具” themes and fixed
  2026-11-07 start are gone. `mkt content brief/plan --facts` derives reader,
  themes, metrics and CTA from `product`/`icp`/`value_events`; unknown products
  get a neutral pool. `mkt content plan` defaults to the local today, and an
  explicit past `--start` is labelled `replay`.

- **Growovo → Verovo evidence handoff**: `mkt grow handoff-verovo RUN --out
  NEW_FILE` exports matching operation/account/receipt rows, product events and
  qualifications, Askovo context snapshots and the outcome projection with a
  SHA-256 integrity fingerprint. Export executes no external action. The package
  remains `evidence_only_not_canonical`: it neither accepts supplier delivery nor
  recognizes revenue or authorizes payment.

Tests: `tests/test_email_indeterminate.py` (9), `tests/test_platform_compliance.py`
(12), `tests/test_content_positioning.py` (8), and
`tests/test_verovo_handoff.py`.

## 0.15.2 — 2026-11-18

Earlier review follow-ups now rebased onto 0.15.1.

### Fixed

- **G-5 — SMTP acceptance receipts**: every committed email carries a receipt
  (message_id generated before the send, `stage: smtp_accepted`) that records
  what the protocol can honestly prove — server acceptance, not delivery.
- **Guided commands are copy-pasteable** (e2e pass): `review`, `execute`,
  `next`, queue build and work-action outputs print the exact accepted forms
  (`mkt plan approve --id <pid>`, `mkt queue run <queue>`); `pause --on
  'reason'` works as documented; the pause gate covers dry-run too; evidence
  writes respect workspace isolation (`MKT_HOME`/`--workspace`); `goal list`
  shows goal ids; cadence placeholders and input errors give actionable text.

### Performance

- **G-6 — ledger read dedup**: ledger reads reuse one stat-keyed view until
  the underlying files change.

## 0.15.1 — 2026-11-18

Review follow-up from the 2026-11-17 audit (G-9): the benchmark holdout set
splits into its own file (`tests/fixtures/github_benchmark/holdout.jsonl`)
and is invisible to dev iteration, so the benchmark gate measures generalisation,
not memorisation of the dev split.

## 0.15.0 — 2026-11-18

This release closes the delivery golden chain end to end: a unified append-only
operations log with readback consistency, a machine-readable delivery contract
with the §5 procurement branch, live X acceptance tooling (read-only preflight,
dry-run rehearsal, isolated-profile rules) and the suspended/locked account
gate from the 2026-11-17 live finding. On top of it: the platform strategy
layer compiles the 2026-11-17 harvest into craft rules with source tracing,
the collection engine gains a diagnostic-probe tactics family and HN content
contract, the experience library moves to evidence tracks (594 records), and
`mkt demo` now walks one full successful delivery before the three blocked
counter-examples. 1590 tests pass.

### Demo: one successful delivery plus three blocked samples

`mkt demo` now walks a complete success path first: a queue item passes the
gates, is sent through an offline in-process transport (`demo-memory`), lands in
the ledger as `committed`, gets one L1 receipt, and appears on the daily result
card together with the three-layer view. The three blocked samples (dry-run /
rejected / failed) stay as the counter-example. Every number comes from the
demo ledger and L2/L3 remain `UNKNOWN`, never 0. The sandbox is rebuilt on every
run, so the demo is repeatable even after a `committed` row exists.

### Website: English launch-autopsy guide and stricter SEO gate

`website/guides/launch-autopsy.en.html` is a faithful English translation of the
Chinese guide (same numbers and limits); the two pages declare reciprocal
`hreflang`. `scripts/seo_check.py` now also checks `og:title`, required JSON-LD,
reciprocal `hreflang` and local resources, and the sitemap lists the English
guide.

### Email channel proven on real sends: SOCKS proxy, attachments, longer timeouts (2026-11-17)

The SMTP channel shipped as PORTED (code moved from newoutreach, never run
inside this repo). A real campaign today ran the same pattern end-to-end —
10 emails with a 6.9MB PDF attachment each, all accepted — and exposed two
environment facts plus one missing feature, now folded back into the channel:

- **`MKT_EMAIL_SOCKS`**: on this machine direct connections to
  `smtp.gmail.com:465/587` time out; the system proxy (ApexCore,
  `127.0.0.1:7890`, SOCKS5) is required. Setting this var routes SMTP through
  PySocks (imported lazily; no new dependency when unset).
- **`MKT_EMAIL_TIMEOUT`**: 6.9MB attachments through the proxy need ≥300s;
  default stays 60s.
- **`attachments`** on queue items: local file paths, MIME inferred from
  extension.
- Channel verification label updated PORTED → **PROVEN (this machine)**.
  Gmail web automation is read-only from here on: it can fill and even attach
  via paste events, but the send action is not reliable enough to own.
  Full write-up: `docs/email-pipeline-20261117.md`.

### Experience library: every record now has an evidence track (2026-11-17)

412 of 594 records had no `knowledge_type`, so `eligible()` rejected them and
they were display-only. The fix is classification with an explicit lower
track, not a bulk promotion:

- **Two new tracks**: `multi_source_practice` (≥2 independent sources, a
  recorded observation date, rights basis and an `ingest` record) and
  `unverified_lead` (single-source or undated: display only, never a rule).
  `multi_source_practice` is usable for **content craft** (`craft` purpose,
  `mkt exp usable`, `as_prompt_lines`, voice tips); `purpose=strategy` still
  requires a human batch review, so nothing gained formal strategy status by
  this migration.
- **`mkt exp adopt [--apply] [--revisit-leads] [--date-from-git]`**:
  idempotent migration of untyped records. 60 records qualified as
  multi-source practice (42 from a recorded collection date, 18 dated from
  the git commit that first recorded the claim; `ingest.date_basis` says
  which). The remaining 352 became `unverified_lead`.
- **No fabricated review**: the migration adds sources, dates, rights basis
  and an ingest pipeline record; it never writes a reviewer, a review time
  or a source-domain claim. Legacy `kind`/`human_reviewed` booleans still do
  not restore eligibility.
- `single_expert_experience` stays experiment-only; single-source short-term
  tactics cannot enter craft. Tests cover the promotion, the lead track and
  the strategy boundary.

### Platform strategy layer: the 2026-11-17 harvest becomes executable (v0.15.0)

The 2026-11-17 collection grew the experience library from 210 to 594
records and added the WeChat operations manual. Most of that growth is
summary-level, single-source or outright article noise, so folding it into
the kernel needed a filter, not a bulk import:

- **`core/platform_strategy.py`** (new) is the layer between platform facts
  and writing rules: role, cadence, limits, content/execution gates,
  monetisation thresholds, cold-start ladder, and claim-cited evidence for
  xiaohongshu, wechat, zhihu, douyin, X, Reddit, Hacker News and LinkedIn.
  Every `craft` rule must exist verbatim in `经验库/experience.jsonl` with
  **at least two independent sources**; official rules cite a manual section;
  single-source secondhand leads (all 27 new X records) are shown but never
  become gates or defaults. `validate()` fails the build when a claim is
  rewritten, deleted, or has its support inflated.
- **A freshness window is code, not prose**: 60 days after `reviewed_at`
  `stale()` marks the card "待刷新"; the card never pretends an old rule
  still holds.
- **`core/content_ops.py`** now has five channel contracts (xiaohongshu,
  wechat, zhihu, douyin, blog). Cadence, body/title/image limits and gates
  are read from the strategy layer, so numbers live in one place. Ten text
  detectors enforce the rules that can be checked offline: contact/ diverted
  traffic, external links, follow/share inducement, absolute claims,
  clickbait, anxiety selling, ad begging, image count and hashtag count.
  Gates that cannot be checked from text (AIGC labelling, 90-day repost,
  Reddit 10:1, HN no-AI/no-auto-post, LinkedIn third-party automation) stay
  in the human checklist and execution-scope list instead of being faked.
- **Default cadence corrected to the evidence**: xiaohongshu now schedules
  3–5 posts/week, not two a day. The user-specified "daily two" tactic is
  still available via `mkt content plan --cadence daily-two`, is labelled as
  a tactic rather than a platform default, and its effect remains unverified.
- **CLI/MCP**: `mkt strategy card <platform>`, `mkt strategy cards`,
  `mkt strategy validate`; `mkt content plan --cadence`; read-only
  `mkt_platform_strategy` (card or coverage table).
- **Email channel completed**: `attachments` (pre-flight existence/empty
  check, MIME by extension, relative paths resolved against the queue file),
  `MKT_EMAIL_SOCKS` (validated `host:port`, clear error when PySocks is
  missing) and `MKT_EMAIL_TIMEOUT`. `build_message` now uses `EmailMessage`:
  the previous `MIMEText.add_attachment` call does not exist and would have
  crashed on the first attachment send. 10 offline tests cover it.

Boundaries kept explicit: the layer constrains content and operations; it
does not prove platform distribution, growth or revenue. X's automation
policy (official API only) is recorded as an execution boundary, not
silently resolved, and the existing CDP write path is flagged rather than
changed in this release.

### Logged-in is not usable: suspended/locked account gate (live finding 2026-11-17)

The isolated live-acceptance browser turned out to hold a **suspended** X
account: login markers all passed and the preflight said `ready=true`, but
every send would have been refused by the platform. Login state and account
standing are now separate signals:

- `session_gate.check_login` reads the logged-in page body (stability-polling
  through progressive hydration) and reports `account_state`:
  `suspended` / `locked` when the platform banner is rendered, `None` when no
  adverse signal is present. `status=logged_in` semantics are unchanged.
- `action_runner.gate_browser_login` raises `ActionError` (ledger records
  rejected) when `account_state` is suspended/locked — a usable account is a
  precondition for any real send, exactly like login itself.
- `acceptance_live_x.py --preflight` gains an `x_account_usable` check, so
  `ready=true` now also means the account is not platform-suspended.

### Golden chain preparation (review 2026-11-17: P2 unified operations log, P0 delivery contract, §5 procurement branch, §9 acceptance script)

- **Live preflight** (`scripts/acceptance_live_x.py --preflight`): read-only
  checks for the authorizing human, isolated empty profile, independent CDP
  port (9222 refused), dedicated X test target, content and browser endpoint.
  It never sends, creates a policy or touches profiles; `ready=true` still
  requires the human to run the live command explicitly.
- **Unified operations log** (`core/operations_log.py`, `data/operations.jsonl`):
  every real/dry-run action appends one complete record with the §4.2 minimum
  field list (operation_id/run_id/queue_item_id, channel/action/target/
  content_sha256, browser_context_ref — endpoint only, never credentials,
  account.platform_user_id/handle/observed_at/proof_ref, operator/approval_ref/
  policy_sha256, started_at/finished_at/result, receipt.id/url/readback_sha256,
  artifacts[].path/sha256, error.code/retryable/recovery_ref). Validation fails
  loud — a record missing fields, or whose artifact hash disagrees with the
  file, is rejected whole rather than appended half-written. Entries are wired
  at every send entry point: queue runner (queue_item_id + operator per item,
  email batch included), single-action runner (`mkt action`/MCP), the `mkt x`
  CLI passthrough (ledger capture, no time heuristics), and grow tick passes
  `run_id`/`operator=grow:<run>`.
- **Account switch never crosses identities**: each record carries only the
  account observed for that operation; when the observed platform id changes
  between consecutive operations on a channel the new record notes
  `account.switched_from` (who/which operation) for audit. The G-01/G-01b gate
  semantics are unchanged — bound authorizations still block a different
  account outright.
- **Readback consistency**: `receipt.readback_sha256` (or
  `readback.status=mismatched`) differing from `content_sha256` forces the
  record to `indeterminate` with `error.readback_mismatch`,
  `retryable=false` and a `ledger resolve` recovery ref — reusing the
  unknown-receipt no-retry mechanism. The X adapter maps a vendor reply
  `readback {url,id,sha256}` onto the ledger row as a receipt and downgrades
  hash-mismatched rows to `indeterminate` with a readback detail.
- **requires_procurement branch** (`core/procurement.py`): paid channels/data
  sources requested at strategy compile (x_ads/ads/google_ads/linkedin_ads/
  x_api_paid/sales_navigator/paid_api …) raise a structured §5 JSON
  (`terminal_state=BLOCKED_USER_INPUT`, `transaction_executed=false`,
  item/why/user_action/alternatives) that survives `CompileError → GrowError →
  mkt grow start` and prints as JSON. Growovo never buys, never silently skips.
- **Machine-readable delivery contract** (`docs/delivery-contract.json`):
  supported intentions, required inputs, the five terminal states, exclusions,
  the procurement template, the operations-log minimum fields (asserted equal
  to `core/operations_log.MINIMUM_FIELDS` by test), evidence grades, and an
  honest live_status. `mkt init` renders the same boundaries from the same
  file — CLI first interaction and docs cannot drift apart.
- **Live X acceptance script** (`scripts/acceptance_live_x.py`): the §9
  deliverable up to the step before live execution. `--dry-run` rehearses the
  whole chain offline (real policy/identity/executor code paths, offline vendor
  and readback stand-ins, six-party separation table, operations log, honest
  UNKNOWN reading, `--simulate-readback-mismatch` rehearses the indeterminate
  halt). Live mode requires explicit `--authorize-by <name>`, an isolated
  profile and a CDP port other than 9222 (the campaign port is refused), and a
  dedicated test tweet. **The live run has NOT been executed in this change** —
  it is the user's explicit step; nothing here is marked live-proven.

### C-01-Lite minimal delegation projection

`mkt grow status <run> --explain` now also returns a `delegation` block with the
eight frozen fields (task_id / task_state / next_actor / next_required_action /
blocked_reason_code / result_ref / authorization_scope / recovery_entry) plus a
one-line boundary. `task_state` maps the canonical run status onto the six-word
C-01 vocabulary, PARTIAL when there are open gaps; the existing flat fields stay
untouched.

### G-10 execution endpoint binding and G-11 strict qualification reads

- **G-10**: the identity gate now refuses to send unless the CDP endpoint the
  identity was read from is the same endpoint the X vendor executes against
  (`MKT_CDP_IDENTITY_URL`/`PORT`/`MKT_CDP_URL` vs `X_CDP_URL`). A mismatch is
  `identity_endpoint_mismatch` with zero vendor calls; multiple X pages in
  different browser contexts stay ambiguous and are refused instead of guessed.
- **G-11**: `outcomes/qualifications.jsonl` is read through one strict parser.
  A broken line or a non-boolean `qualifies` raises `LedgerReadError` instead of
  silently skipping the revocation and reporting verified counts, and the cache
  is keyed by the file's mtime/size so a long-lived `OutcomeLedger` sees an
  external revoke immediately.

### G-07 single business-result reading and G-08 measurement requalification

- **G-07**: revoking an event's qualification no longer leaves two answers. The
  ledger keeps every row (append-only, auditable), but `funnel()` drops the
  projections of revoked events, `grow_learning` and the weekly card read the
  same qualified L3 numbers, `prospect_journey` filters revoked events before
  first/repeat decisions, and `disqualified` (who, why, when) is disclosed on
  both the L3 summary and the funnel payload. `mkt grow qualify --list
  [--event ID]` prints the audit trail. Revoking a first activation correctly
  promotes the next real activation to "first".
- **G-08**: `calibrate` now separates "passed seven consecutive days once" from
  "the current reading is trustworthy". A conflicting or invalid reading after
  PROVEN downgrades the current reading to `CONFLICTED`, sets
  `requalify_required` and a due date while keeping `promoted_at`; seven further
  valid days restore PROVEN and clear the requirement. `grow status` exposes the
  state (`measurement` block) so a downgraded reading is never shown as proven.

### G-01b live account identity and legacy authorization

- The CDP identity reader is implemented (`core/cdp_identity.py`): it lists the
  debug endpoint on loopback only, connects to the page target over a
  hand-written WebSocket, and accepts **only** a stable numeric platform id from
  `verify_credentials` or the `twid` cookie. A handle, a display name or a login
  marker is never promoted to an identity. It is off by default
  (`MKT_CDP_IDENTITY=1`), has a 5 s timeout, records every probe reason in
  `data/identity/probes.jsonl`, and fails closed.
- **AC-H is enforced**: a legacy authorization that does not name a concrete
  platform account no longer bypasses the gate. Real sends are refused with
  `AUTH_REQUIRED` / `authorization_not_identity_bound` (or
  `authorization_missing_identity` when only account names exist) and a migration
  command; read-only and dry-run flows keep working and report the blocker.
- `mkt policy migrate-identity` lists the accounts that still lack a binding and,
  with `--apply --by NAME --identity account=platform:user_id`, writes the
  binding; `mkt policy identity` shows whether the reader is enabled and what the
  last probe returned; `mkt doctor` reports a 「账号身份门禁」 row.
- All three real-send entries (queue, `mkt x --go`, MCP) enforce the same gate
  and now record `authorized_account` / `observed_platform_identity` /
  `identity_proof_ref` on the ledger row; the CLI path previously enforced the
  gate but dropped the proof.

### Fixed

- **Pinned DNS check for competitor watch fetches (G-09)**: the watch policy
  validated every address that DNS returned and then fetched the **URL**, so the
  transport resolved the domain a second time; a rebinding between the check and
  the connect could make the monitor read an internal page while the policy had
  approved a public one (the old production path passed no resolver at all unless
  a test injected one). New `core/safe_fetch.py` resolves once, classifies
  **every** returned address (one loopback/private/link-local/reserved/ULA/
  IPv4-mapped address rejects the whole fetch), pins the first validated address
  and dials **that IP**. HTTP uses `http.client.HTTPConnection(host=<ip>)` with an
  explicit `Host` header for the original domain (bracketed IPv6, non-default
  ports kept). HTTPS keeps SNI and certificate/hostname verification on the
  original domain through `ssl.create_default_context()` +
  `wrap_socket(server_hostname=<domain>)`; an `SSLContext` with
  `check_hostname`/`CERT_REQUIRED` disabled is refused before connecting, so
  connecting to an IP never turns verification off and never switches it to the
  IP. `getpeername` is compared with the pinned address (IPv4-mapped forms
  normalised) and a mismatch aborts the read. Redirects are manual: every
  `Location` re-enters the full resolve → classify → pin → connect flow, with a
  hop limit, loop detection, no https→http downgrade, and private/loopback
  targets are never dialled. Connect timeout, read timeout, total fetch budget,
  hop limit, declared and actual byte cap and content-type gate all fail closed
  with machine codes mapped to the C-01 families (`POLICY_DENIED`,
  `STALE_SOURCE`, `RATE_LIMITED`). `core/watch.py`'s production path now uses the
  pinned fetcher; every entry stores `last_fetch` and every change event carries
  `fetch` with `resolved_ips`, `pinned_ip`, `redirect_chain`, `bytes`,
  `allowed`, `reason`, `mode` and `connector`. The legacy injected
  `transport`/`fetcher`/`file://` test paths still work but are labelled
  `injected_transport`/`injected_fetcher`/`local_file` and never report a pinned
  address. Boundaries are documented and not relaxed for compatibility: one
  pinned IP with no multi-IP failover (CDN anycast/geo-DNS can serve different
  content from another address; a failed first address fails the fetch instead
  of falling back), direct sockets only (the system proxy is bypassed),
  HTTP/1.1 only (no HTTP/2/ALPN), no connection reuse, IPv6 requires a working
  IPv6 route, and an injected connector that does not report its peer address
  cannot be re-checked (tests/offline only).

- **Operator-declared trusted verifiers (final review)**: internal
  buyer-controlled systems (`internal-billing`, `warehouse`,
  `admin-console`, …) were rejected as verifiers unless they happened to be in
  the default whitelist, while a self-declared `verified_by="stripe"` with a
  self-computed hash was accepted. `mkt grow events import` now accepts
  repeatable `--trusted-verifier <id>`; the ids are **added** to the default
  whitelist (not replacing it), recorded as `import_trusted_verifiers` on every
  imported event, and echoed in the import result. The declaration is an
  operator-signed action (`--by` still required) but it cannot whitelist
  vendor/supplier/seller/sales identities — those remain unverified even when
  declared. The documentation now states plainly that a verifier id is a
  declarative identity and that the evidence hash only proves the declared
  fields are intact; it does not prove the payment provider received money.
  Stronger assurance requires a payment-provider API or a buyer finance export.

- **Idempotent change retry (final review)**: `apply_changes` checked whether
  an approval was already consumed before checking whether the change was
  already in effect, so retrying the same change after a partial or interrupted
  apply returned `held/REVIEW_REQUIRED` ("approval already consumed") or wrote
  a duplicate strategy change. The idempotency check now runs first: if applying
  the change to a copy leaves the strategy hash unchanged (target already
  equals `to`), the item is reported in `already_applied` and no approval is
  needed. A retry of only such items returns
  `no_op/already_applied` with no new change record; a mixed batch skips the
  landed item and processes the rest normally. The single-use approval rule
  remains in force for changes that still need to land, and the
  rollback-then-replay guard is unchanged.

- **Default incident gate on library entry points (final review)**: the CLI
  was gated, but `GrowRunV2.arm(by=...)` without `blocking_check` and
  `GrowRunV2.resume(...)` could still bypass the incident scan when called
  directly. Both now default to the shared `blocking_incidents()` scan through
  a small `core/grow_incidents.py` adapter (it lazily imports the single
  scanner in `grow_runbook`, so `grow_v2` never imports runbook at module load
  and there is no cycle). An unresolved `indeterminate` or any blocking alert
  now raises `INDETERMINATE_EXTERNAL_ACTION`/the incident's C-01 code with the
  INC id before `go` or `status` changes; warning-only alerts still allow
  resume. Explicit `blocking_check=` remains available for tests/orchestration
  and overrides the default, but callers no longer need to remember it.

- **Shared resume gate (D3, G-05)**: `mkt grow resume <run> --by ... --note ...`
  without `--after-reconciliation` used to call `GrowRunV2.resume()` directly,
  which checked only status/note and flipped `needs_attention` back to `active`
  without looking at the ledger. Both resume paths now go through
  `grow_runbook.resume_run`: the default path runs the same incident scan as
  the reconciliation path, refuses while an `indeterminate` receipt or any
  other blocking alert remains, keeps the status unchanged, and prints the
  exact next step (`mkt ledger resolve <ref>` plus `mkt grow resume ...
  --after-reconciliation <INC-id> --by ... --note ...`). Only runs with no
  blocking alert (warnings are fine) return to `active`; the global pause and
  every execution gate still apply. Legacy v1 resume keeps its old path.

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
