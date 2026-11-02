# Case: Verovo — what one full GTM cycle taught Growovo

> **Role**: the "verification layer" of the ovo family (LLM output verification). What makes this case special: it predates Growovo, and the holes it fell into became Growovo's defaults directly.
> **Period**: 2026-08-16 → 2026-10-31 (~2.5-month full-cycle retrospective) · current phase executing · data as of 2026-11-02
> **[中文版](verovo.md)**

## Background

[Verovo](https://github.com/roy-tong/Verovo) (formerly AgentMeasure / MeteringQA) verifies LLM outputs. From August to October 2026 it went through a textbook GTM trial-and-error cycle: outreach and production capacity fully maxed out, business funnel at zero the whole way. The retrospective is not a failure report — **the controlled experiments and kill lines that cycle produced are worth more than most success stories**, and nearly every rule in Growovo's compliance section traces back to a line in this retrospective.

## Totals (honesty first)

| Dimension | Number |
|---|---|
| Outreach volume | ~1,200 emails (including one 449-empty-body incident batch), ~900 X actions |
| Supply-side output | 110+ tool audits, 45+ verified bugs, upstream merges (**reported per date**: 14 at report publication @2026-09-09; 23 per the claims index @2026-10-11. A [GitHub search](https://github.com/search?q=author%3Aroy-tong+type%3Apr+is%3Amerged) counts every merged PR on the account, not audit fixes, so it is not the same measure), 5 external contributors, 1 CI adoption, 1 standard adoption |
| Stars | 92 purchased baseline stars (permanently excluded from attribution) → 220 (API, 2026-11-02); ~16 natural stars over the whole period, of which **only 2 are attributable, both from the delivery chain**. Note the star/fork ratio of 22.0, outside the usual 5–20 band (audit, 2026-11-02): quoting this star count requires that caveat |
| Business results | 0 qualified buyer conversations, 0 paid, 0 repeat |

## Channel verdicts, with controls

**Killed (own A/B data, no going back):**

| Play | Evidence |
|---|---|
| High-volume X cold outreach | ~120 requests over 4 days: 0 stars / 0 tasks / 0 backflow; **same-period control** (upstream delivery chain) +1 star +1 standard adoption — our own data, no more arguing needed |
| $990 prepaid cold email | 40 sent, 0 replies, past the preregistered decision line (if the true rate were 5%, all-zero over 40 has probability 12.9% — what died is the "ICP × price × channel" combination, not the market) |
| Gmail Web UI automated bulk send | 449 empty-body incident; channel closed permanently |
| Bought stars | 92+42+~13; a 3.7% fork rate exposed it immediately — iron rule: "no fake volume, ever" |

**Alive (with numbers):**

| Play | Evidence |
|---|---|
| Upstream delivery chain (audit → issue → PR → merge) | Both attributable natural stars came from it; merges, CI adoption and standard adoption don't depreciate with star counts |
| Review-request 1:1 email | 209 sent → 6+ substantive replies ≈ **5.3%**; control arm with cold-question phrasing 0/26 |
| Free task → trust | n=2, mechanism proven (a 74-line audit report earned a consistent re-test and authorized public case) |

## How this retrospective became Growovo's defaults

| The price Verovo paid | What Growovo hardcodes |
|---|---|
| 449 empty-body incident | Readback mismatch never sends; `indeterminate` results never auto-resend — a human must `ledger resolve` |
| Recurring scale impulses (199/day → 500+/day → 100-message batches, each falsified) | All writes default to dry-run; a plan whose size exceeds the kernel daily budget is rejected at lint |
| Bought stars poisoning all early attribution | The ledger records "platform action succeeded" and "business outcome" as two independent fields; sent ≠ converted |
| Refusing to stop at the line (the $990 line burned 6 weeks before the verdict) | Decision lines preregistered: 40 emails, 10 qualified quotes, 2 weeks of zero growth — at the line, kill |
| "Ten seconds from the account owner" actions stuck for weeks | Human review is an explicit gate (signature + fingerprint + validity window); human actions are listed, never silently awaited |

This is not "we learned from failure" rhetoric — it is a line-by-line price→mechanism mapping within the same workflow.

## Current phase (×10 aggressive targets, preregistered)

Since 2026-10-31, Verovo instantiates the ovo family growth methodology in its own execution master: targets up an order of magnitude (stars ≥2,200, ≥20 paying customers, ≥$25,000, deadline 2026-12-31), launch week 11/09–13, **mid-check 11/14: if the launch doesn't happen or adds <300 stars → year-end targets drop one tier**, trigger data on record. The master opens with an honesty preamble: at current measured coefficients (delivery chain ~1–3 stars/week, two cold rounds at 0 conversion), ×10 is not reachable in 9 weeks; the plausibly reachable path (launch pulse × scaled distribution × free-task funnel) is unverified for this project — if it doesn't run, downgrade per the rules; no forcing, no silent target edits.

## Launch-week ledger (from 2026-11-02; operator and human separate)

| Date | Action | operator | human | Evidence |
|---|---|---:|---:|---|
| 2026-11-02 | Launch pack M1–M7 (OG image, HN, PH, newsletter, dev.to, case cards, demo plan) | 103 | 0 | workspace `verovo/campaign/`, material states in `04-落地台账.csv` |
| 2026-11-02 | Pre-flight outbound link check (6 links, all 200) | 8 | 0 | curl |
| 2026-11-02 | M8 domestic drafts ×3 (billing-bug explainer / reconciliation how-to / free entry) | 40 | 0 | workspace `verovo/campaign/M8-domestic.md` |
| **Total** | | **151** | **0** | — |

**Platform action ≠ business outcome**: everything above is material production and pre-flight. **Nothing has been published.** What remains is human action (PH/HN posting, WeChat paste), not operator time.

## Status and evidence level

| Item | Status |
|---|---|
| Stars / forks | 220 / 10 (API, 2026-11-02) |
| Channel conclusions | All 4 kills rest on own controls or preregistered decision lines; alive plays carry their sample sizes (n=209+26, n=2) |
| Unverified channels | LinkedIn, G2 named-lead outreach — the investment never actually happened; not counted as "channel doesn't work" |
| What this case proves about Growovo | The methodology wasn't designed first and sold later — it paid its price in a real GTM cycle first, then froze that price into defaults |
