# How to verify these cases

These three cases are self-dogfood: the vendor is also the client. That makes them easy to write and hard to trust, so this page says exactly what you can check yourself and what you have to take on faith.

**Short version:** commits, stars, issues, URLs and file contents are all checkable. The minutes ledgers and the internal decision records are not public, so any claim that depends on them is a claim, not a proof.

## What is checkable, and how

| Claim | How to check | What it does not tell you |
|---|---|---|
| "Commit X landed" | `git log` / the GitHub commit page | Whether the change was good |
| "N tests pass" | `python3 -m unittest discover -s tests` on a clean checkout | That the product works on any real platform |
| "`mkt demo` runs offline in ~2s" | run it | Anything about a real channel |
| "Star / fork counts" | `api.github.com/repos/roy-tong/Growovo` | Whether the stars are organic — see the note below |
| "Channel published at URL" | open the URL, check status 200 and content | Whether anyone read it |
| "The article is live" | HTTP 200 + the text | Whether it converted |
| "Response times recorded" | GitHub issue timestamps | Whether the responder was a human |
| "Approval gate rejected content X" | `mkt github reply` with an em dash in the body, dry-run | That every gate works the same way |

## What you cannot check (and why we say so)

| Not checkable | Why | How it could be faked |
|---|---|---|
| Operator/human minutes | Private workspace ledger | Someone could just write smaller numbers |
| PRD / positioning decisions | Internal documents | Retro-editing |
| Why a channel was judged dead | Internal A/B records | Selective memory |
| P0 gate inputs (GitHub traffic) | Requires repo-owner token | Cherry-picked windows |

**This is the honest limit of all three cases.** They demonstrate a pipeline and a discipline; they cannot prove the minutes are real. Anyone claiming otherwise about a self-dogfood case is selling.

## Star counts deserve their own paragraph

The Verovo repo carries 92 purchased baseline stars from an earlier period, permanently excluded from attribution and recorded in its retrospective (case 2). Its star/fork ratio of 22.0 sits outside the usual 5–20 band and is flagged by our own audit.

So: **do not read 220 stars as 220 people who found the project useful.** The case says this, and this page repeats it because it is the single easiest number to over-read.

## What a third-party case would look like

None exists yet. When one does, it will meet the same standard and additionally carry:

1. the client's own written authorisation (opt-in), with the scope of what may be published;
2. the client's own system events for the outcome claim, not our logs;
3. the ability for the client to correct the "what this cannot prove" section.

Until then, the third-party case slot on the [case index](README.md) stays empty. That is the current state, not a gap we intend to paper over.

## If you find a claim here that does not hold

That is a bug. Open an issue with the claim and the check you ran. Corrections are recorded as new commits, not silent edits — the case set has already been corrected twice for exactly this reason (a wrong blog URL, and a merge count with no reproducible source).
