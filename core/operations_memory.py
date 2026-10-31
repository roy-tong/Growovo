"""Read-only operations memory built from real execution receipts.

The memory is a human-reviewable signal, not a learned executable workflow.
No target identities, message bodies, credentials, or platform account state
are exported, and this module NEVER performs an external action.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

RESULTS = {"committed", "indeterminate", "failed", "rejected", "dry-run",
           "no-dm", "bounced", "suppressed", "resolved_committed",
           "resolved_not_sent"}
RISK_RESULTS = {"indeterminate", "failed", "rejected", "no-dm", "bounced", "suppressed"}


def summarize(records, *, campaign=None, channel=None, minimum_confirmed=3):
    """Summarize a ledger into anonymized, non-executable operation patterns.

    This summarizes written receipts only. A committed platform action is not
    proof of a business outcome. In particular, unknown result must never
    become evidence that a workflow is safe to replay.
    """
    if not isinstance(records, list):
        raise ValueError("records must be a list")
    if not isinstance(minimum_confirmed, int) or minimum_confirmed < 1:
        raise ValueError("minimum_confirmed must be a positive integer")
    groups = defaultdict(list)
    for row in records:
        if not isinstance(row, dict):
            continue
        if campaign is not None and row.get("campaign") != campaign:
            continue
        if channel is not None and row.get("channel") != channel:
            continue
        ch, action = str(row.get("channel") or ""), str(row.get("action") or "")
        if not ch or not action:
            continue
        groups[(ch, action)].append(row)
    patterns = []
    for (ch, action), rows in sorted(groups.items()):
        counted = Counter(str(row.get("result") or "") for row in rows)
        confirmed = counted.get("committed", 0) + counted.get("resolved_committed", 0)
        risks = sum(counted.get(result, 0) for result in RISK_RESULTS)
        blockers = []
        if counted.get("indeterminate"):
            blockers.append("unknown external results require reconciliation")
        if counted.get("rejected") or counted.get("suppressed"):
            blockers.append("gate or suppression rejection exists")
        if counted.get("failed"):
            blockers.append("execution failures require investigation")
        if confirmed < minimum_confirmed:
            blockers.append("insufficient confirmed successful operations")
        patterns.append({
            "channel": ch,
            "action": action,
            "campaign": campaign if campaign is not None else "multiple",
            "receipt_count": len(rows),
            "confirmed_operations": confirmed,
            "risk_receipts": risks,
            "results": dict(sorted(counted.items())),
            "candidate_for_review": not blockers,
            "review_blockers": blockers,
            "automatic_replay_allowed": False,
        })
    return {
        "schema_version": "1.0",
        "kind": "growovo.operations-memory",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filter": {"campaign": campaign, "channel": channel},
        "patterns": patterns,
        "note": ("An action receipt is not a conversion, platform-policy approval "
                 "or transferable execution recipe. Humans must review platform "
                 "rules, authorization and actual task contents before re-use."),
    }
