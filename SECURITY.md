# Security policy

Growovo executes operations on accounts you own. A bug here can send something you did not approve, resend an unknown result, or leak a credential. Please report those privately.

## Reporting

Use GitHub's **private vulnerability reporting** on this repository (Security → Report a vulnerability). If that is unavailable, email the maintainer listed on the GitHub profile.

Do not open a public issue for a vulnerability, and do not include real credentials, tokens, session cookies, or personal data in a report. Redact first.

## What we treat as a vulnerability

- Bypassing approval, dry-run, pause, daily budget, suppression or DNC checks.
- Sending when read-back verification disagrees.
- Retrying an `indeterminate` outcome automatically.
- Writing, rewriting or silently dropping ledger rows; making a failed write look committed.
- Leaking credentials, cookies, tokens or profile data into logs, files, receipts or the repository.
- Executing instructions embedded in platform page text (prompt injection reaching a command path).
- MCP tool paths that grant write/replay rights they claim not to have.

## Out of scope

- Platforms rate-limiting or challenging automation — that is expected behaviour, not a vulnerability.
- Terms-of-service questions about whether automation is allowed on a given platform.
- Findings that require an already-compromised machine or an attacker with the operator's OS account.
- Missing rate limits on public read-only endpoints.

## What to expect

- Acknowledgement within **3 working days**.
- A fix or a documented mitigation for confirmed issues, with credit unless you ask otherwise.
- We will not ask you to test against accounts you do not own, or to send real messages as part of a PoC.

## Supported versions

The latest tagged release on `main`. Older tags are not separately patched.
