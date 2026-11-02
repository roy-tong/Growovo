# How Growovo compares

> 中文摘要：这篇不是功能对照表，是三个诚实的比较——**和手写脚本比、和通用自动化平台比、和一个能直接操作浏览器的 Agent 比**。每一节都写了 Growovo 在哪输。页末列出本页每条主张对应的仓库文件，你可以自己核。

**Short version.** Growovo is narrower than a general automation platform and less flexible than a script you write yourself. It exists for one property neither gives you: **a write that happened on a real account has an approval, a budget check, a read-back, and a ledger row — or it does not happen.**

## 1. vs a script you write yourself

| | Your script | Growovo |
|---|---|---|
| Flexibility | Whatever you write | Fixed command surface + playbooks |
| Setup time | Hours to days, per channel | `pip install` / clone, then `mkt demo` in 30s |
| Approval gate | You build it | Built in (signed approval, plan fingerprint, expiry) |
| Dry-run default | You build it | Every write is dry-run unless `--go` |
| Budget / throttle | You build it | Atomic pre-reservation + per-channel tables |
| Read-back verification | You build it | Required; mismatch blocks the send |
| Unknown outcome | Usually retried (the dangerous part) | `indeterminate`, never auto-retried, needs a human |
| Audit trail | Your logs, if you kept them | Append-only ledger, one row per action |

**Where the script wins:** anything idiosyncratic. If your operation is unusual, a 40-line script is better than adapting a framework.

**Where Growovo wins:** the moment a script touches a real account, you need the last five rows of that table — and most scripts get the "unknown outcome" case wrong on the first try.

## 2. vs a general automation platform (n8n, Zapier, Make, and similar)

| | General automation platform | Growovo |
|---|---|---|
| Breadth of integrations | Hundreds; genuinely better | A handful of channels, each labelled PROVEN / PORTED / UNVERIFIED |
| Visual builder | Yes | No — CLI, MCP and a Skill |
| "What should I do next" judgment | You design it | Playbooks propose; a human approves |
| Safety on a live account | Per-workflow, up to you | Invariants in the core path, not per workflow |
| Ledger semantics | Run history | Action / task outcome / business outcome, kept separate |
| Best at | Connecting systems you already run | Deciding and executing recurring outward operations |

**Where they win:** everything that is "move data between two systems". Use them for that.

**Where Growovo wins:** outward actions (a comment, a reply, a post, an issue) where "it ran successfully" is not the same as "it was right", and where a retry after a timeout is a real risk.

A concrete difference: a general workflow that times out usually retries. Growovo records `indeterminate` and refuses to resend until a human checks the platform. That single behaviour is worth more than any integration count when the target is someone else's inbox.

## 3. vs an agent with browser/computer access

| | General agent + browser | Growovo |
|---|---|---|
| Deciding what to do | The model, per session | Playbook + plan, reviewable before execution |
| Persistence | The chat session | Ledger, queue, schedule, memory |
| Budget | Whatever the prompt says | Enforced in code before the send |
| Reproducibility | Re-prompted each time | Same gates, same ledger shape |
| Risk profile | Broad, unbounded | Narrow command surface, explicit authorisation |

**Where the agent wins:** exploration, and one-off tasks with no repetition.

**Where Growovo wins:** the tenth time you do the same thing, where you want the same gate to run — and a record that survives the session.

## What Growovo is not

- Not a CRM, helpdesk, or ad platform. Not a scraper.
- Not a bulk sender. Rate limits and human pacing are in the core path, not a config option.
- Not a guarantee of business outcomes. A sent message is not a signup, and the ledger keeps them apart.
- Not a replacement for deciding what to do. It executes decisions you have already made, and records what happened.

## Verify this page

Every claim above maps to something you can read or run:

| Claim | Where |
|---|---|
| Approval is signed, fingerprinted, time-bounded | `core/` approval + plan modules, `mkt plan approve` |
| Writes are dry-run by default | `core/action_runner.py`, `python3 mkt.py demo` |
| Unknown outcomes are never auto-retried | `core/`, `mkt ledger resolve` |
| Ledger is append-only | `data/`, `mkt status` |
| Verification levels are per channel, not global | [`docs/feature-status.md`](feature-status.md), README command matrix |
| Playbooks propose, humans approve | `mkt playbook`, `mkt review` |

If any of these turn out to be false, that is a bug — please open an issue.
