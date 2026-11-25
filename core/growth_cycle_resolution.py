"""Read-only Growovo cycle projection into the shared Problem Resolution Contract."""
from __future__ import annotations

SCHEMA = "ovo.problem-resolution/1"


def _decision_ref(cycle, decision):
    return "growth-cycle:%s/%s/decision:%s" % (
        cycle["service_id"], cycle["cycle_id"], decision.get("proposal_id") or "unknown")


def _base(cycle, kind, code, summary, effect, evidence_refs):
    return {
        "schema": SCHEMA,
        "problem_id": cycle["cycle_id"] + "-resolution",
        "product": "growovo",
        "task_id": cycle["service_id"],
        "observed_at": (cycle.get("executed_at") or (cycle.get("proposal") or {}).get("received_at")
                        or cycle.get("created_at")),
        "observation": {
            "code": code,
            "summary": summary,
            "external_effect_status": effect,
            "evidence_refs": evidence_refs,
        },
        "requested_resolution": kind,
    }


def _business(cycle, code, summary, effect, refs, options):
    document = _base(cycle, "business_decision", code, summary, effect, refs)
    document["decision"] = {
        "evidence_refs": refs,
        "options": [{"option_id": option_id, "impact": impact} for option_id, impact in options],
    }
    return document


def from_cycle(cycle):
    cycle_id = str(cycle.get("cycle_id") or "")
    cycle_ref = "growth-cycle:%s/%s" % (cycle.get("service_id"), cycle_id)
    executions = cycle.get("executions") or []
    decisions = cycle.get("decisions") or []
    unknown = any((row.get("result") or {}).get("status") in ("waiting_verification", "indeterminate")
                  for row in executions)
    effect = "unknown" if executions else "none"
    if unknown:
        refs = [cycle_ref] + ["growth-action:%s" % (row.get("action_id") or "unknown")
                              for row in executions
                              if (row.get("result") or {}).get("status") in ("waiting_verification", "indeterminate")]
        document = _base(cycle, "auto_fix", "EXTERNAL_RESULT_UNKNOWN",
                         "An external action may have taken effect; reconcile before any retry.",
                         "unknown", refs)
        document["fix"] = {"action_id": "reconcile-external-receipt", "side_effect_free": True,
                           "same_authorization": True, "max_attempts": 1}
        return document

    if cycle.get("status") == "expired":
        document = _base(cycle, "stop", "GROWTH_CYCLE_EXPIRED",
                         "The cycle deadline has passed; preserve state and do not execute late proposals.",
                         effect, [cycle_ref])
        document["stop"] = {"reason": "growth cycle expired", "preserve_state": True}
        return document

    mapping = next((row for row in executions if row.get("execution") == "mapping_required"), None)
    if mapping is not None:
        document = _base(cycle, "data_needed", "GROWTH_ACTION_MAPPING_MISSING",
                         "A proposal has no executable growth action mapping.",
                         effect, [cycle_ref, _decision_ref(cycle, mapping)])
        document["data"] = {
            "request_id": cycle_id + "-action-mapping",
            "source": "existing-growth-action-registry",
            "access_basis": "existing service action contract",
            "automatic_fetch_attempted": True,
            "side_effect_free_fetch": True,
        }
        return document

    if cycle.get("status") == "proposed":
        refused = [row for row in decisions if not row.get("allowed")]
        refs = [cycle_ref] + [_decision_ref(cycle, row) for row in decisions]
        missing_grants = [row for row in refused if row.get("reason") == "no_active_grant"]
        if missing_grants:
            document = _base(cycle, "permission_needed", "GROWTH_GRANT_MISSING",
                             "The proposed actions need only the missing growth grants.",
                             effect, refs)
            document["permission"] = {
                "request_id": cycle_id + "-missing-grants",
                "scopes": sorted({"growth-action-class:%s" % row.get("action_class")
                                  for row in missing_grants}),
                "incremental": True,
                "missing_only": True,
            }
            return document
        if refused:
            return _business(
                cycle, "GROWTH_PROPOSAL_REFUSED", "Refused proposals need scope or strategy review.",
                effect, refs,
                (("revise_proposal", "Prepare a narrower proposal; no refused action is executed."),
                 ("defer_cycle", "Leave the cycle unexecuted and preserve the current evidence.")))
        return _business(
            cycle, "GROWTH_PROPOSAL_AWAITING_EXECUTION_DECISION",
            "Accepted proposals still need the named execution decision.", effect, refs,
            (("execute_accepted", "Execute only accepted actions through the existing gates."),
             ("defer_cycle", "Make no external change and preserve the proposal.")))

    if cycle.get("status") == "blocked":
        refs = [cycle_ref] + [_decision_ref(cycle, row) for row in executions]
        return _business(
            cycle, "GROWTH_EXECUTION_BLOCKED", "Execution is blocked; inspect evidence before changing strategy.",
            effect, refs,
            (("inspect_and_revise", "Review receipts and prepare a new bounded proposal."),
             ("pause_related_actions", "Stop related actions and preserve the current state.")))

    raise ValueError("cycle has no resolution problem: %s" % cycle.get("status"))
