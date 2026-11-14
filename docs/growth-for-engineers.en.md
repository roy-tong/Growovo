# Growth Is Now Your Job: An Engineer's Guide

> One line: you've been handed GTM and user growth for your product, but your training is in engineering. This guide translates growth into an engineering problem — systems, evidence, rollbacks, no mysticism.

## Who you are (two groups, one situation)

- **Indie developer / one-person company**: the product is built; nobody else is going to grow it.
- **Going full-stack inside a company**: your mandate expanded from code to GTM, and someone wants numbers.

What you share: engineering discipline, no growth training. Growovo's design assumption is exactly **treat growth as a systems problem** — which makes its onboarding cost lowest for people like you.

## Vocabulary map: what you know ↔ what Growovo calls it

| What you know | In Growovo | In one line |
|---|---|---|
| SLA / SLO | The [result contract](grow-闭环.md): hard commitments vs. explicit non-promises | Commits only to verifiable execution volume, honest readings, and closed-loop responsibility — never to platform reach |
| audit log | The ledger: every committed action, individually traceable | Every real send is recorded; nothing is overwritten |
| staging / canary | All write actions default to dry-run; `--go` for live; canary before scale-out | Exactly how you deploy |
| trust levels | Evidence grades: UNVERIFIED → PROVEN | Readings that haven't been reconciled never masquerade as verified |
| CI loop | `grow daemon` / `grow daily`: execute → read → adjust, every day | One screen a day, like a pipeline status page |
| alerting | Supply gaps alert 3 days early; anomalies halt for human adjudication | Fail fast, no retry storms |
| code review | `grow review --days 7` weekly retro | Review growth results like PRs; decide to continue or optimize |

## Onboarding path (seven steps, each with a command)

```bash
# 1. Diagnose: run everything with zero network and zero accounts first
python3 mkt.py demo
python3 mkt.py work-demo

# 2. Ground it: give product facts and one growth goal; get a strategy
python3 mkt.py grow launch g1 --facts facts.json \
  --goal awareness.followers --target 200 --handle your_handle

# 3. Guardrails: standing authorization replaces per-action review only;
#    payments/contracts/crisis response are never automated
python3 mkt.py policy enable --allow x:reply --by "$USER"
python3 mkt.py grow arm g1        # after checking strategy and authorization, go live

# 4. Execute: the daemon runs the loop — publish, read stats, adjust
python3 mkt.py grow daemon g1     # macOS background: grow install g1 --load

# 5. Progress: one screen a day — volume / readings / supply / guardrails
python3 mkt.py grow daily g1

# 6. Results: a weekly result card in the triple-metric format
#    (pieces published / daily impressions / new followers),
#    every line traceable, with boundary statements attached

# 7. Retro: attribution-driven suggestions; continue or optimize
python3 mkt.py grow review g1 --days 7
python3 mkt.py grow learn g1
```

## Three hard commitments / explicit non-promises

**Committed**: ① Execution volume — every committed action in the ledger, individually traceable. ② Honest readings — daily follower and impression snapshots, screenshot-backed, labelled by evidence grade. ③ Closed-loop responsibility — ≥90% completion auto-scales volume by 25% (2× cap), <50% raises a warning; a result card every week.

**Not promised**: numeric guarantees from platform algorithms. Impressions and followers are platform self-reported; missing readings say "unavailable", never 0. The promise is over-execution plus automatic tuning — not chest-thumping.

## Check the evidence before you trust it

- [Launch autopsy: 73 tweets in 11 hours, 0 new stars](launch-autopsy-20261105.md) — a real campaign run with this tool, the numbers it got, and the ledger gap it exposed in ourselves.
- [Why not just write a script, or use a generic automation platform?](compare.md) — point-by-point, with a repo file behind every claim.
- [Platform automation matrix](platform-automation-matrix.md) — phase one: X + Email fully automated; GitHub, Reddit, Hacker News, LinkedIn gated behind a supervised first run; manual channels such as Xiaohongshu and Zhihu get work orders only.

## Compliance floor (also your seatbelt)

Only accounts you own or are explicitly authorized to operate; zero credentials in the CDP path; no adversarial evasion (no CAPTCHA bypass, no fingerprint spoofing); no mass blasting. When a company account comes on board, authorization trails are fully auditable — **you won't get fired for the engine going rogue**.
