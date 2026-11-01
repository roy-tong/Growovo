<p align="center">
  <img src="docs/assets/logo.png" width="400" alt="Growovo">
</p>

<p align="center"><strong>Execute authorized recurring operations with reviewable results.</strong><br>
<em>把重复运营工作交给 Agent，留下可核验的执行记录。</em></p>

<p align="center"><a href="README.md">简体中文</a> · <strong>English</strong></p>

Growovo is built for operators who maintain product communities, content channels, and customer communication. You provide goals, authorized accounts, and task lists; everything is previewed and reviewed before any recurring action runs. Every action is recorded as success, failure, or indeterminate (needs human verification), with pause and follow-up handling.

The product consists of an **Operator Engine** (the execution core) and **Playbooks** (business workflows). The currently available GitHub Inbox Playbook triages issue lists and generates reply queues; content, email, and growth campaigns reuse the same execution core. CLI, MCP, and Skill share one set of approval, budget, read-back, and ledger mechanisms. A successful send only means the operation completed — business outcomes such as signups or payments are recorded separately.

[![tests](https://github.com/roy-tong/Growovo/actions/workflows/tests.yml/badge.svg)](https://github.com/roy-tong/Growovo/actions/workflows/tests.yml)

**Try one result now:** `python3 mkt.py demo` runs plan validation, approval, dry-run, and a ledger demo locally — no accounts or network required. Verification levels for live platform commands are in the table below. See [feature status & trade-offs](docs/feature-status.md).

- **Askovo** handles business research and validation, supplying the rationale for tasks.
- **Growovo** executes authorized operations tasks and records the results.
- **Verovo** links costs to business outcomes and computes per-task economics.

Each can be used independently. The `mkt.py` and `growovo-gtm` identifiers are kept for compatibility; GTM is the first application scenario.

## Compliance and discipline (read this first)

- Only for accounts **you own or operate**; never touch other people's accounts, no agency-style automation
- The CDP channel is zero-credential: no passwords, tokens, or cookies are ever stored; SMTP credentials come only from environment variables
- **All write operations default to dry-run; `--go` runs for real** — real runs must be bound to a **human-approved plan**: without plan governance the default is refusal; only the manual CLI path `--adhoc` explicitly allows a run (the ledger records adhoc=true; not available over MCP)
- Daily budgets are atomically reserved before sends; `indeterminate` (unknown outcome) can never be auto-resent — a human must run `ledger resolve`; DNC/bounced identities in contacts merge with `suppress.txt` into a single suppression source
- Fail and stop (no retry bombardment); a read-back mismatch never sends (codified after the 449 empty-body incident)
- Human pacing: randomized intervals + per-run hard cap + daily budget (see the table in core/throttle.py)
- No mass blasting: per-recipient personalized content is a precondition; leftover placeholders are rejected outright by the gates
- No adversarial techniques: no CAPTCHA bypassing, no fingerprint spoofing, no proxy pools

## Install and quickstart

```bash
pip install -r requirements.txt   # playwright only; CDP drives system Chrome, no browser download
python3 mkt.py init               # create directories + report credential gaps + print next steps
python3 mkt.py demo               # zero network, zero accounts; 30s through the 4 core steps: lint → review → write path → ledger
python3 mkt.py doctor             # environment check (--selftest runs all offline unit tests)
./launch.command                  # double-click works too: first run copies the login profile, starts CDP on 9222
python3 mkt.py check              # verify the CDP session and X/Gmail login state
```

## Command matrix and verification levels

| Command | What it does | Level |
|---|---|---|
| `python3 mkt.py status` | Global ledger aggregation | — |
| `python3 mkt.py check` | CDP session check (X/Gmail/Reddit/LinkedIn) | — |
| `python3 mkt.py demo` | Zero-network zero-account demo (plan lint → human-review gate → write-path dry-run → ledger; sandbox, safe to delete) | — |
| `python3 mkt.py doctor [--selftest]` | Environment check: core imports / data contracts / credentials / CDP / pause state | — |
| `python3 mkt.py report [--hours 24] [--format md]` | One-page operations report (anomalies / paused state / suppression list) | — |
| `python3 mkt.py init` | Workspace bootstrap: create directories / report credential gaps / next steps (`--workspace` or `MKT_HOME` isolates multiple products) | — |
| `python3 mkt.py next` | State machine: what to do now, and why | — |
| `mkt plan new/lint/show/list/approve/revoke` | **Plans and approval**: scaffolding, validation, signed approval events (signature + plan fingerprint + expiry) | — |
| `mkt queue lint <file>` / `mkt queue build` | **Edit-time gates**: placeholders/duplicates/length blocked when the queue is written; template + CSV generate per-recipient queues | — |
| `mkt campaign show/report/next` | **Campaign view**: plan/review/queue preflight/ledger/events/experiments/next step → one Markdown page | — |
| `mkt review <campaign>` | Human-review preview: every queue item through the gates + existing evidence + approve & run commands | — |
| `mkt ledger resolve <ref> --result … --by … --note …` | **Human adjudication of indeterminates** (append-only override; the original ledger line is never modified) | — |
| `mkt link create/list` | **Tracking links**: generate links with utm_campaign and persist them for event attribution | — |
| `mkt outcome import <csv>` / `mkt outcome sync` | **Outcome accounting**: idempotent CSV import + ledger aggregation into count snapshots | — |
| `mkt queue run <file> [--go]` | **All-channel queue execution** (email/hn/github/reddit/linkedin/x-reply), each item through the same pipeline | — |
| `mkt action <channel> <action> …` | Single write action (same gates/approval/ledger as queues); x message/engage pass through to the vendor | — |
| `mkt collect serve/event/import` | **Local collector**: `/t/<link>` click attribution, `/events` webhook, Plausible/PostHog CSV | — |
| `mkt experiment obs --id X --from-outcomes` | Experiment observations aggregated automatically from the event ledger (no more manual copying) | — |
| `python3 mkt.py pause --on 'reason' / --off` | **Global pause switch** (all write operations refuse to run, and the pause is logged) | — |
| `python3 mkt.py contacts add/find/dnc/mark/import` | Contact store operations (idempotent dedup / DNC hiding / er-batch import) | — |
| `mkt x publish/reply/message/engage/search/inbox …` | All X actions (vendor x-ops-automation) | **PROVEN** |
| `mkt email message --queue data/queues/w1.json [--go]` | SMTP send queue | **PORTED** |
| `mkt email inbox` / `mkt email bounces` | CDP read-only Gmail scan / bounce handling | **PORTED** (pattern PROVEN) |
| `mkt reddit search --query "…"` | Read-only search | **UNVERIFIED** |
| `mkt reddit reply --url <post> --text "…" [--go]` | Comment (read-back verification pipeline) | **UNVERIFIED** |
| `mkt reddit publish --sub <sub> --title … --body-file … [--go]` | Post (≤3/day) | **UNVERIFIED** |
| `mkt linkedin search --keywords "…"` | Read-only people search | **UNVERIFIED** |
| `mkt linkedin message --url <profile> --note-file … [--go]` | Semi-auto connection request (terminal confirmation) | **UNVERIFIED** |
| `mkt github audit --repo owner/repo` | **Repo health audit** (basics/community health/releases/contributor concentration/AI-readiness/Scorecard) | **PROVEN** |
| `mkt github inbox --repo owner/repo` | open issues/PR list + zero-reply flags (48h first-response patrol) | **PROVEN** |
| `mkt github search --query "…"` | Repo search (audience/competitor discovery) | **PROVEN** |
| `mkt github reply --repo o/r --number N --text "…" [--go]` | issue/PR comment (first write path on the api channel; requires `MKT_GITHUB_TOKEN`) | **UNVERIFIED** |
| `mkt playbook github-inbox --repo o/r --drafts drafts.json` | Inbox triage → candidate replies → standard queue (does not send; still requires approval/dry-run) | **PORTED** |
| `mkt hn search --query "…"` | HN discussion/audience discovery (Algolia public API, anonymous read-only) | **PROVEN** (read-only) |
| `mkt hn publish --title "Show HN: …" --url … [--go]` | Submit form submission (CDP; ≤1/day, title ≤80 chars; Show HN only for your own projects) | **UNVERIFIED** |
| `mkt hn reply --item item:N --text "…" [--go]` | Item comment (first use = the author's first comment on a Show HN, within 1h of submitting) | **UNVERIFIED** |
| `python3 mkt.py memory …` | Read-only operations memory from verified receipts (see the last section) | — |
| `python3 mkt.py workflow …` | Reusable recipes: learn / approve / replay (dry-run by default) / runs / reconcile | — |

Level meanings: PROVEN = verified on a real account; PORTED = the pattern is verified, this wrapper has not been run end-to-end; UNVERIFIED = selectors untested in practice — the first run must be supervised (first-run checklist in channel doc 03 §5).

## Planning layer: plans are data, human review is the gate (product doc 05 §2)

```bash
# 1. Generate a plan scaffold (or hand-write one per data/schemas.md)
mkt plan new --id askovo-p0 --goal 'get 20 activations' --beachhead 'independent UX researchers' \
  --hypothesis 'evidence grading is the buying motive' --channel email --wave-action message --size 10

# 2. Validate (schema / capability matrix / size ≤ core daily budget / preregistered criteria)
mkt plan lint --id askovo-p0

# 3. Human review: must be signed; writes an approval event + plan fingerprint
#    (modifying the plan after approval is caught by the fingerprint and the run is refused)
mkt plan approve askovo-p0 --by roy --days 7

# 4. Queue: built from template + targets CSV, or hand-written then gate-checked with lint
mkt queue build --campaign askovo-p0-w1 --channel email --wave-action message \
  --template assets/outreach.md --targets data/targets.csv
mkt queue lint data/queues/askovo-p0-w1.json

# 5. Human-review preview → dry run → real run
mkt review askovo-p0-w1
mkt email message --queue data/queues/askovo-p0-w1.json          # dry-run
mkt email message --queue data/queues/askovo-p0-w1.json --go     # requires approval

# 6. Accounting and retrospective
mkt link create --campaign askovo-p0-w1 --channel hn --url https://example.com
mkt outcome import data/events.csv --campaign askovo-p0-w1
mkt outcome sync --campaign askovo-p0-w1
mkt campaign report askovo-p0-w1
```

The plan fingerprint is the canonicalized sha256 of the plan content (excluding the review block); legacy plans carrying only `human_approved=true` still pass as legacy — all new approvals carry a fingerprint and an expiry.

Caps are a core property: a plan whose size exceeds the `core/throttle.py` daily budget is rejected by lint.

## MCP server (mcp_server.py, stdio, zero dependencies)

```json
{"mkt": {"command": "python3", "args": ["/absolute/path/Growovo/mcp_server.py"]}}
```

10 tools, read and write across all channels:

- Read: `mkt_status` / `mkt_channels` / `mkt_plan_lint` / `mkt_search` (x/reddit/linkedin/hn/github) / `mkt_ledger_query` / `mkt_campaign` / `mkt_inbox` (hn/github/email/x) / `mkt_audit` (github)
- Write: `mkt_run_queue` (all-channel queue, dry-run by default) / `mkt_action` (single action, same gates/approval/ledger)

Real runs of write operations always require a human-approved plan; UNVERIFIED channels need `allow_unverified=true` (manual first-run calibration); adhoc is CLI-manual-path only and never exposed over MCP. The MCP process also reads `MKT_HOME`, so it can use a different workspace than the CLI.

## Skills (skills/)

- `skills/growovo-gtm/SKILL.md`: the **GTM operations protocol** — the entry point is a growth goal (signup/activation/revenue), not a single channel action. It decomposes work via four engines (positioning → assets → distribution → outcomes) into seven steps: goal → plan → audience → review → execute → retrospect. Install into `~/.agents/skills/` to make it triggerable in hosts such as ZCode (content collaboration via warm-email-outreach / expert-review-outreach / b2b-account-mapping).
- `skills/github-project-ops/`: the **GitHub project operations protocol** (open and closed source alike) — positioning diagnosis (four quadrants + seven stages) → repo audit checklist → growth playbook → closed-source playbook library; includes the standalone audit script `scripts/repo_audit.py` (zero-dependency distribution). Relationship to the CLI: the skill is protocol and judgment; `mkt github *` is data collection and execution.

## Transport layer (core/transports.py)

ACTIVE×4: `api` (OAuth, Reddit phase 2) | `browser-cdp` (PROVEN) | `os-input` (macOS keyboard/mouse/AX simulation, UNVERIFIED, transports/os_input.py) | `protocol` (SMTP/IMAP, PROVEN).
RESERVE×6 (transport reserve): `webdriver` | `rss-public` (read-only) | `esp-api` (transactional email) | `mobile-appium` | `integration` (platform-native) | `semi-auto-ui` (human-confirmation fallback).
Each channel × action declares a preferred transport plus a fallback chain; **fallbacks are for "the road is blocked", not for "evading risk control"** — the response to risk-control signals is slower pacing or `mkt pause`, never switching masks. Risk thresholds and account limits live in project doc 06 (verified against official sources; code budgets always sit below official caps — see throttle.INTERVALS/OFFICIAL_LIMITS).

## Environment variables

| Variable | Purpose |
|---|---|
| `MKT_CDP_URL` | CDP endpoint (default http://127.0.0.1:9222) |
| `MKT_EMAIL_USER` / `MKT_EMAIL_APP_PASSWORD` | Gmail SMTP (App Password, never stored) |
| `MKT_LINKEDIN_AUTO=1` | Unlock fully automatic LinkedIn (not recommended; default is semi-auto with terminal confirmation) |
| `MKT_GITHUB_TOKEN` | GitHub PAT (only needed for write `reply`; read primitives work anonymously, 60 req/h) |

## The data trio (`data/`, gitignored)

- `queues/<campaign>.json` queues; `ledger.jsonl` the global ledger; `contacts.jsonl` the contact store
- `suppress.txt` suppression list (one target per line); `shots/` evidence; `state/` daily-budget state
- schemas in `data/schemas.md`

## Repository layout

```
core/      ledger (with human adjudication overrides) · gates (incl. DNC merge) · throttle atomic daily budgets · pipeline write-path transactions · evidence · cdp session · transports registry · planlint plan validation · approval events/plan fingerprints · queue edit-time gates · campaign view/review preview/next step · links tracking links · outcome_io CSV import & ledger aggregation · collector local webhook/redirect · action_runner single actions · queue_runner all-channel queues · playbooks GitHub Inbox triage/draft queues · workflows recipe learning/approval/replay · locators locator ladder · workspace multi-product isolation · experiments preregistration · attribution events/funnels/attribution · demo zero-account demo · doctor environment check
channels/  x/(vendor+adapter) · email_smtp · email_cdp · reddit · linkedin · github (first api-channel release) · hn (CDP write + Algolia read)
transports/  os_input (macOS keyboard/mouse/AX simulation, UNVERIFIED)
skills/    growovo-gtm (GTM operations protocol) · github-project-ops (GitHub project operations protocol + audit script)
tests/     offline unit tests (118, incl. the 449 incident regression, all-channel human-review gates/adhoc passthrough, approval fingerprints & expiry, atomic budget reservation, DNC/indeterminate blocking, queue edit-time gates, idempotent CSV import, ledger adjudication overrides, collector redirect/event auth, clean MCP stdout, GitHub Inbox Playbook triage/draft queues, funnel identity normalization & timing disclosure, value-event verification, experiment freezing & preregistration tamper-proofing, the demo loop and doctor tiers): python3 -m unittest discover tests -v
mcp_server.py  MCP shell (stdio JSON-RPC, zero dependencies)
```

The X channel vendors in x-ops-automation (upstream github: roy-tong/x-ops-automation);
**verified code is never rewritten** (see `channels/x/vendor/PROVENANCE.md`); re-sync when upstream fixes selectors.

## License

MIT (see [LICENSE](LICENSE)). `channels/x/vendor/` is vendored from the same author's repo x-ops-automation (MIT).

## Reusable workflows: learn once, replay carefully

The same class of task should not need a fresh model exploration of the page every time. `workflow` freezes steps, a locator ladder and readback conditions into a fingerprint-approved recipe; `--go` requires that approval, and every run is journalled step by step:

```bash
python3 mkt.py workflow learn hn-reply --trace trace.json      # one observed run → draft (never auto-approved)
python3 mkt.py workflow approve hn-reply --by "operator" --days 30
python3 mkt.py workflow replay hn-reply --var item=123 --var text="…"          # dry-run: resolve locators only
python3 mkt.py workflow replay hn-reply --cdp-url http://127.0.0.1:9222 … --go  # execute
python3 mkt.py workflow runs
python3 mkt.py workflow reconcile --run <id> --outcome not_sent --note "checked the platform" --by "operator"
```

When the page changes, recovery walks `css → placeholder → label → role → text`; visual recovery needs an operator-supplied provider and otherwise stops as `visual_unavailable` instead of guessing a click target. Writes keep the standard gates: `action` steps call `action_runner`, `commit` clicks check pause and the daily budget and write the ledger. See [workflows](docs/workflows.md).

## Operations memory: learn from verified platform receipts

Inspect recurring channel/action patterns without exposing contact details or automatically replaying any past action:

```bash
python3 mkt.py memory --campaign my-campaign
python3 mkt.py memory --channel github --output data/operations-memory.json
```

The summary reports confirmed platform operations, unknown/failing receipts, and **human-review candidates**. It intentionally excludes targets and message bodies. A successful platform action is **not** evidence of customer activation, and a pattern is never permission to replay: new runs still require fresh authorization, current platform-policy checks, and the standard execution gates. This is the read-only input to workflow learning: it shows patterns and risks, while an approved `mkt workflow` recipe performs the replay. See [feature status](docs/feature-status.md).
