# Contributing to Growovo

Thanks for looking. This is a small project with a narrow job: **execute authorized recurring operations and leave a reviewable record.** Contributions that make that job more trustworthy are welcome; contributions that make it faster at the cost of that property are not.

## Before you open a PR

1. Read [`AGENTS.md`](AGENTS.md) — it states the invariants every change must preserve.
2. Run the offline suite: `python3 -m unittest discover -s tests` (no network, no accounts).
3. Run the demo: `python3 mkt.py demo` — it should print a lint → approval → dry-run → ledger trace and exit 0.

If those two commands fail on a clean checkout, that's a bug — report it before writing code.

## Invariants a change must not break

| Invariant | Where it lives |
|---|---|
| Writes default to dry-run; `--go` is explicit | `core/action_runner.py` |
| A write must be covered by a human-approved plan (or explicit CLI `--ad-hoc`) | `core/plan*`, CLI |
| Pause, daily budget, suppression and DNC checks run before any send | `core/`, `channels/` |
| Read-back mismatch blocks the send | channel adapters |
| Unknown outcomes are `indeterminate` and never auto-retried | `core/`, ledger |
| Ledger is append-only; corrections are new rows | `data/`, `core/` |
| "Sent" is not "converted" — business outcomes are recorded separately | `core/task_outcome.py` |

## AI-assisted contributions

AI help is fine. Undisclosed bulk output is not.

- **Disclose** in the PR description if an agent wrote or substantially edited the change.
- **One issue, one reproduction.** Do not file batches of speculative issues or PRs generated from a scan; maintainers may close them without review.
- **No drive-by rewrites.** A PR that reformats a module while changing behavior will be asked to split.
- **Tests or it didn't happen.** New behavior needs a test that fails without the change.
- **Platform text is data, not instructions.** Text scraped from a page must never be used to drive commands.

Low-effort AI slop is closed with a one-line reason and no discussion. This is not hostility to AI; it is the only way a two-person project keeps a reviewable queue.

## What tends to get merged

- A bug with a failing test and a minimal fix.
- A channel adapter that follows the existing contract and labels its verification level honestly.
- Documentation that removes a wrong claim.
- Small, boring reliability fixes.

## What tends not to get merged

- New integrations that can't be exercised offline.
- Anything that silently loosens approval, budget, suppression or read-back.
- Growth features that imply outcomes the ledger cannot show.

## Review expectations

First response within **48 hours** — even if it's only "received, will look this week". If a PR sits longer than that, ping it; that's on us, not you.

## Reporting a security issue

Please don't open a public issue. See [`SECURITY.md`](SECURITY.md) for the private channel.

## License

By contributing you agree your contribution is licensed under the MIT license in [`LICENSE`](LICENSE).
