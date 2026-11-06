# Launch autopsy: 73 tweets in 11 hours, 0 new stars

**Snapshot: 2026-11-06 09:20 +08. Window: 2026-11-05 20:32 → 2026-11-06 07:23 (+08).**

This is the complete record of a launch night we ran for three developer tools from one X account. We are publishing it because the numbers are bad, they are verifiable, and the failure has a shape that repeats across solo projects.

Every claim below is either a GitHub API reading, an X post ID you can open, or a timestamp computed from that ID. Nothing here is estimated except where it says so.

## What we ran

| Item | Count | Where it is |
|---|---|---|
| X threads | 3 (one per product), 12 tweets each | first tweets: [Verovo](https://x.com/elliwoodtong/status/2118320540234686518) · [Askovo](https://x.com/elliwoodtong/status/2118408109710622721) · [Growovo](https://x.com/elliwoodtong/status/2118413861573583152) |
| Thread tweets | 36 | IDs on file for all 36 |
| Replies to other people's posts | 34 | IDs on file, page-verified |
| Standalone posts | at least 3 landed; 13 more drafted and never verified as posted | — |
| **Total published** | **73+ tweets in 10h 51m** | from one account with 31 followers |

The account had 1,644 tweets before the night. The night added roughly 4.4% of its entire lifetime output.

## What it produced

| Product | Stars before (2026-11-04, API) | Stars after (2026-11-06, API) | Delta |
|---|---|---|---|
| Verovo | 218 | 218 | **0** |
| Askovo | 1 | 1 | **0** |
| Growovo | 1 | 1 | **0** |

No forks. No issues. No replies from anyone we did not already know. Thread view counts read 4 on the page DOM — a reading we do not trust as measurement, and it does not change the conclusion, because the star numbers are page-independent and exact.

## The timeline, recovered from tweet IDs

X post IDs encode their creation time in the high bits. We decoded them instead of trusting our own notes.

| Time (+08) | Event |
|---|---|
| 20:32:27 | Verovo thread opens |
| 21:10 | Platform starts silently dropping standalone posts. No error, no banner, no rejection notice. The composer returned success. |
| 23:00 | Re-probe: three posts attempted, zero appeared in search or on the profile. Stopped the queue. |
| 02:00 | Third probe passed. Resumed. |
| 02:20:26 | Askovo thread opens |
| 02:43:17 | Growovo thread opens |
| 04:13:46 | Last reply of the first reply pool |
| 07:23:14 | Final reply posted |

Two things stand out in that table. First, the silent drop: the platform returned a successful submit while the post never existed. Any pipeline that checks "did the submit call return without error" would have counted 100% success on a night where a third of the posts never shipped. We were saved only because a second check searched for the text afterwards.

Second, at 02:00 in the morning we resumed posting to an audience that was asleep, because the pipeline was ready and the queue was full. Readiness is not the same as timing.

## Five findings

**1. Reply-window reach is real; broadcast reach from a small account is not.**
All 34 replies to other people's posts went through. The 36 thread tweets reached almost nobody. On an account with 31 followers, a cold thread has no distribution path — it is not competing on quality, it is invisible. The replies worked because each one landed inside somebody else's existing audience, and even then only as many as the original post's traffic supported.

**2. Volume cannot manufacture a reason to care.**
We had 73 pieces of content and zero propositions a stranger could act on. Nothing in the night told a reader what was broken in their own setup. The one product with 218 stars got them from a different mechanism entirely: we audited ~110 other tools' token accounting with a synthetic fixture, found 45+ reproducible bugs, and opened issues. Ten fixes were merged. The stars came from that, months before any thread.

**3. Our own tool would have caught the failure, and we did not use it.**
Growovo's whole value proposition is a reviewable ledger: every action recorded as success, failure, or *indeterminate*. The launch night bypassed the ledger. It ran through three ad-hoc scripts writing progress JSONs to a local folder. That is why "at least 3 standalone posts" above has a qualifier on it — we cannot fully reconstruct the night from its own records. A campaign run through the product would have produced an auditable file per action, and the silent drops would have appeared as indeterminate rows instead of vanishing.

**4. The rate limit was not the constraint. The attention budget was.**
The account's daily posting limit was 50 posts and 200 replies. We used 73 total over 11 hours, well under it. The platform's anti-automation heuristics, not its quotas, decided what shipped. But even a night where every post landed would have changed nothing, because the constraint was never capacity — it was that nobody was waiting.

**5. "Build in public" through volume is just a diary.**
A record of what you did is not public building. A record of what you tested, what the result was, and what you changed is. Nobody can act on "we shipped two releases this week". Somebody can act on "we posted 73 times and got nothing; here is why, and here is what we changed."

## What we changed

We stopped the volume plan. The planned 500-post, 14-day campaign is cancelled. What replaces it, for all three products:

- **One audit artifact per product, published before promotion.** Something a stranger can verify without trusting us: a reproducible bug, a dataset, a claim traced to its source.
- **X becomes an announcement board.** Three posts a week, each pointing at an artifact, none of them announcing that we exist.
- **Every action goes through the ledger**, including the actions that fail. This document exists because we lacked that, and had to reconstruct the night from post IDs.

## Reproduce our numbers

- Stars: `curl -s https://api.github.com/repos/roy-tong/Verovo` → compare `stargazers_count` with the 2026-11-04 reading of 218.
- Post times: `datetime.fromtimestamp((id >> 22) + 1288834974657, tz=+08)` for any ID above.
- The ledger gap: this repository's `data/ledger.jsonl` has 3 rows covering 2026-11-02. It has none for the launch night.

## What this does not prove

- It does not prove the products are wrong. 73 tweets is a distribution test, not a product test.
- It does not prove X is useless for everyone. It proves X with 31 followers and no artifact is useless for us.
- It does not prove the audit mechanism will work twice. It worked once, on one product, with a real technical finding. Treating one success as a formula is the same mistake as this launch.

---

*Written 2026-11-06. Compiled from `battle-report.md`, queue progress files, tweet IDs, and GitHub API readings taken on 2026-11-04 and 2026-11-06.*
