# Case: Growovo — the operator eats its own dog food

> **Role**: Growovo's own repository and GTM. Before the tool had any external user, its own rename, docs, tests and dogfood loop ran through its own gates.
> **Period**: repo created 2026-10-04 (as marketing-ops-automation) → present · data as of 2026-11-02
> **[中文版](growovo.md)**

## What this case proves

The easiest place for a tool repo to fake it is "never using itself". Growovo's bootstrap path: **every self-operations action goes through its own pipeline** — plans, human review, dry-runs, ledger, minutes logging — the exact same one delivered to external clients.

## Actions that happened (all verifiable)

| Action | Evidence |
|---|---|
| Repo renamed marketing-ops-automation → Growovo; README rewritten to operator positioning | [c305fe0](https://github.com/roy-tong/Growovo/commit/c305fe0); rename verified via API |
| Zero-account four-step demo (lint → human-review gate → write-path dry-run → ledger) | [a76f523](https://github.com/roy-tong/Growovo/commit/a76f523); reproduce locally in 30 seconds with `python3 mkt.py demo` |
| Bilingual READMEs at full parity (Chinese default + English 1:1, complete command matrix included) | [README.md](../../README.md) / [README.en.md](../../README.en.md) |
| Offline tests | 186, gated by GitHub Actions, `python3 -m unittest discover tests` |
| Task-outcome records (platform action / task outcome / business outcome as three independent layers; contradictions rejected) | [ea194e1](https://github.com/roy-tong/Growovo/commit/ea194e1), `core/task_outcome.py` |
| GitHub expert-task benchmark (33 cases, 21 dev / 12 holdout; `--fail-on-harmful` turns the benchmark into a gate) | `scripts/github_benchmark.py` + `tests/fixtures/github_benchmark/` |
| Skill protocols published | `skills/growovo-gtm` (GTM operating protocol), `skills/github-project-ops` (GitHub project operations) |

## The dogfood loop: client lessons flow back into the product

The Askovo P0 (see [case 1](askovo.en.md)) is not one-way service. Within the first week a full loop closed:

1. the GTM readiness check (12 dimensions) built during dogfooding was absorbed into Growovo's toolchain (v1.1);
2. the 2 script false positives it exposed were fixed on the spot, passing score 30 → 32;
3. the handling pattern for "not yet on PyPI" blockers (verify an alternative path first; keep waiting off the critical path) entered the operating manual.

Every GTM action logs operator/human minutes (the Askovo ledger's first day: 275 / 5 minutes) — the same ledger is both a service deliverable and a source of product requirements.

## Status and evidence level (as of 2026-11-02)

| Item | Status |
|---|---|
| Stars / forks | 1 / 0. Recorded as-is: very early bootstrap, no growth outcome to claim |
| Tests | 186 offline tests green; real-account verification labeled per channel PROVEN / PORTED / UNVERIFIED — no uniform claim |
| Boundaries | Explicitly not doing: bulk cold email, paid ads, one-click follower growth, adversarial evasion of platform controls |
| Next checkpoints | Askovo D14 gate (2026-11-14) and the Verovo launch-week mid-check (11/14) — both preregistered; no narrative before the data |
