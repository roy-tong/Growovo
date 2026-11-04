# Case: Askovo — Growovo's first dogfood client (P0, in progress)

> **Role**: Growovo takes over Askovo's GTM as the operator, using one real launch to prove the operator-minute / human-minute ledger and the four-phase discipline.
> **Period**: started 2026-10-31 · data as of 2026-11-02 (no verdict date since 2026-11-03; running on an iteration cadence)
> **[中文版](askovo.md)**

## Background and goal

[Askovo](https://github.com/roy-tong/askovo) is the "judgment layer" of the ovo family: a SURE evidence engine that grounds product and business reasoning in verifiable evidence chains. Its relationship with Growovo is upstream/downstream — Askovo answers "what should be done", Growovo answers "where do users come from".

This case runs Askovo as Growovo's **first dogfood client**: no new capabilities, no demo data — one end-to-end pass through the four GTM phases.

It answers one question: **how many human minutes does one pass cost, and does it actually bring people in.** Since 2026-11-03 there is no verdict date and no kill line — the product is still iterating, and a number fixed in advance would bend the work toward the number instead of the outcome.

Two numbers still matter, but they are no longer a pass/fail gate: **total human minutes** for one full pass, and **whether anyone actually showed up**. Both are reported as measured, not as a score.

The "value event" definition was pinned on day one: since Askovo is not yet on PyPI, measurement uses GitHub traffic as a **labelled approximation**, never presented as an exact number.

## Execution (define / build / run / measure)

**Define (completed 2026-10-31)**

- GTM readiness check across 12 dimensions: 30/36 passing; the check itself surfaced 2 script false positives — fixed on the spot and re-run (30→32).
- Produced `positioning.md`: ICP (independent UX researchers), three claims, three channel hypotheses, value-event definition; the positioning hook (launder liability — the legal risk of passing off laundered data) was distilled from public pain-point corpora.

**Build (completed 2026-10-31)**

Five assets passed the my-voice style bar in one pass: landing page, Show HN post + first comment, comparison page, demo script, FAQ.

**Run (in progress, 2 of 3 channels executed)**

| Channel | Action | Result |
|---|---|---|
| GitHub README | Problem hook + one-line install (verified in a clean venv: 2.1.0 installs, demo passes) | ✅ Live ([a6055ad](https://github.com/roy-tong/askovo/commit/a6055ad)) |
| English blog | Long-form comparison "Askovo vs your research stack", pushed | ✅ Live at <https://roy-tong.github.io/notes/askovo-vs-your-research-stack-en/> (200 on 2026-11-02; the URL logged on 10-31 had the wrong path and case, corrected and recorded as a lesson) |
| Show HN | Post + first comment drafted and ready | ⏸ Requires the account owner to paste (~60 seconds); Growovo does not post on behalf of anyone |

**Measure**: GitHub traffic is a labelled approximation (downloads ≠ users) and is flagged as such in every report; there is no D14 verdict any more.

## The ledger (this case's core output)

Every step records two minute counts: operator (agent working time) / human (time only a person can spend).

| As of | Operator | Human | Budget headroom |
|---|---|---|---|
| 2026-10-31 | 275 min | **5 min** | 62% (720 human-minute budget) |

Where the 5 human minutes went: reviewing the positioning document. Everything else — readiness check, false-positive fixes, five assets, install-path verification, testing and committing 65 uncommitted files (only after 119 tests went green, [5f2e338](https://github.com/roy-tong/askovo/commit/5f2e338)), README rewrite, blog publication, channel liveness checks — was operator time.

One episode: Askovo had no PyPI release and no upload credentials, so the install path pivoted the same day to `pipx install git+https://…`, verified in a clean venv. Blocking issues don't wait — find a verified alternative path first and take "waiting for credentials" off the critical path.

## Research side (chain built the same day)

Askovo eats its own dog food: a seven-node askovo-on-askovo study run on the SURE engine.

- Corpus pipeline: 8,235 HN items → 3,849 relevant → 478 candidates (rule set v1, reproducible and idempotent).
- 26 real evidence items registered, including the first batch of publicly self-reported pain-point evidence (E1×6 / E2×2, pending the account owner's review).
- **The audit's first pass caught a missing chain reference** (two evidence items absent from the judgment pool) — the evidence engine's first real interception happened on itself.

## Status and evidence level (as of 2026-11-02)

| Item | Status |
|---|---|
| Repo signals | 1 star / 0 forks (cold-start baseline, recorded as-is) |
| Registrations / value events | **0** — measurement phase not started; no business results to claim |
| Show HN | Not posted (waiting on the human action) |
| Product side | Askovo kept shipping during dogfood: 2.3.0 ([fe8451d](https://github.com/roy-tong/askovo/commit/fe8451d)) → 2.6.0 (2026-11-01) |

**This case cannot currently claim any growth outcome.** What it can claim: a minutes ledger, a fixed false positive, a gap caught by its own audit. The next step is no longer waiting for a verdict day — it is running the "right people + useful words + keep following up" loop for four weeks and writing the result back honestly.
