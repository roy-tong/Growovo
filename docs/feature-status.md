# Growovo: implementation status

Reviewed 2026-11-01 against the repository implementation. Available means local implementation, not production validation on every platform.

| Feature | Scope | Status |
|---|---|---|
| G01–G04 | Task/plan, capability registry and account checks | Available for existing channels; planning remains Agent-assisted |
| G05, G08–G13 | API/browser execution, playbooks, gates, state, readback, queue and approval | Available with per-channel PROVEN/PORTED/UNVERIFIED limits. `mkt workflow` replays a versioned, fingerprint-approved recipe; `action` steps still go through `action_runner`, and `commit` clicks check pause, daily budget and write the ledger |
| G06–G07 | Semantic and visual recovery | Semantic ladder (`css → placeholder → label → role → text`) shipped in `core/locators.py` and used by workflow replay. Visual recovery is an operator-supplied provider hook; with none configured the run stops as `visual_unavailable`. Stagehand/Midscene are not bundled |
| G14–G15 | Operations memory and optimization | `mkt memory` summarizes receipts; `mkt workflow learn` turns an observed trace into a draft workflow (ladder-ordered locators, no auto-approval); optimization remains planned |
| G16 | Third-party playbook marketplace | Deferred |

Build: business playbooks, authorization, policy, result verification and execution ledger. Reuse: existing Playwright/CDP and platform APIs. No new browser framework is required for queue reliability. Stagehand and Midscene remain optional recovery candidates; no fork is needed for receipt-only memory.

The read-only `mkt_memory` MCP tool exposes anonymized receipt patterns to Agents; it never grants replay rights. `mkt_workflow` is read-only too: list/show/lint/runs. Approval and replay stay in the human CLI path.

Recovery: send/confirmation exceptions are `indeterminate`, so a timeout cannot authorize a resend. Workflow runs journal every step; an `indeterminate` or interrupted run blocks further `--go` replay until `mkt workflow reconcile --run … --outcome sent|not_sent --note …` records a human decision. A queue stops on failed, rejected or unknown results. Check platform state and use `ledger resolve` with an evidence note before retrying an unknown result. Process termination during a send still needs external inspection; exactly-once delivery is not guaranteed.
