"""Read-only lifecycle and follow-up projection over existing Growth Service state."""
from __future__ import annotations

import datetime as dt

from . import gates, growth_engagement, growth_experiments, growth_opportunities, journey

SCHEMA = "growovo.lifecycle/1"
STAGES = ("unknown", "discovery", "engagement", "trial", "activation", "retention", "payment")
RANK = {stage: index for index, stage in enumerate(STAGES)}
EVENT_STAGES = {
    "signal": "discovery", "discovery": "discovery",
    "click": "engagement", "engagement": "engagement", "reply": "engagement",
    "install": "trial", "trial": "trial", "signup": "trial",
    "activation": "activation", "activated": "activation",
    "retention": "retention", "retained": "retention",
    "payment": "payment", "paid": "payment",
}


def _now(value=None):
    if value:
        return growth_engagement._instant(value)
    return dt.datetime.now().astimezone()


def _identity(source, value, identity_map):
    raw = "%s:%s" % (source, value)
    canonical = journey.normalize_identity(raw, identity_map)
    return canonical, raw


def _is_suppressed(suppressions, source, actor, canonical, *refs):
    candidates = {str(value).strip() for value in (actor, canonical, *refs)
                  if str(value or "").strip()}
    if actor:
        candidates.add("%s:%s" % (source, actor))
        if source == "x" and not str(actor).startswith("@"):
            candidates.add("@" + str(actor))
    return bool(candidates & set(suppressions))


def _contact(contacts, canonical, raw):
    row = contacts.setdefault(canonical, {
        "subject_id": canonical, "aliases": set(), "evidence": [],
        "unqualified_events": [], "followups": [], "suppressed": False,
    })
    row["aliases"].update((canonical, raw))
    return row


def _qualify_event(event, stage):
    if event.get("approximated") is True or event.get("approximate") is True:
        return False, "approximated event"
    if stage in ("activation", "retention", "payment") and not journey._verified(event):
        return False, "%s lacks verified method/ref and source_ref" % stage
    return True, ""


def _relationship_state(row, evidence, moment, *, risk_days, churn_days):
    if row.get("suppressed"):
        return "do_not_contact"
    if any(item.get("next_step") != "suppressed" for item in row.get("followups") or []):
        return "follow_up_due"
    if not evidence:
        return "unknown"
    latest = evidence[-1]
    observed_at = growth_engagement._instant(latest["at"])
    age_days = (moment - observed_at).total_seconds() / 86400
    historical = max((item["stage"] for item in evidence), key=lambda name: RANK[name])
    activated = RANK[historical] >= RANK["activation"]
    if latest["stage"] == "retention" and len(evidence) > 1:
        previous_at = growth_engagement._instant(evidence[-2]["at"])
        if (observed_at - previous_at).total_seconds() / 86400 > risk_days:
            return "reactivated"
    if age_days > churn_days:
        return "churned" if activated else "silent"
    if age_days > risk_days:
        return "churn_risk" if activated else "silent"
    return "active" if activated else "engaged"


def project(data_dir, service_id, *, now=None, stale_minutes=60, identity_map=None,
            relationship_risk_days=7, relationship_churn_days=30):
    if relationship_risk_days <= 0 or relationship_churn_days <= relationship_risk_days:
        raise ValueError("relationship churn days must exceed positive risk days")
    moment = _now(now)
    suppressions = gates.load_suppressions(data_dir)
    contacts = {}
    queue = []
    suppressed_queue = []

    for opportunity in growth_opportunities.list_opportunities(data_dir, service_id, now=moment):
        source = str(opportunity.get("source") or "unknown")
        actor = str(opportunity.get("actor_id") or opportunity.get("author") or "")
        if not actor:
            continue
        canonical, raw = _identity(source, actor, identity_map)
        contact = _contact(contacts, canonical, raw)
        contact["evidence"].append({
            "stage": "discovery", "ref": opportunity["id"], "source": source,
            "at": str(opportunity.get("observed_at") or ""),
            "basis": "recorded_market_opportunity",
        })
        if opportunity.get("status") == "pending" and opportunity.get("freshness_status") == "current":
            item = {
                "kind": "opportunity", "ref": opportunity["id"], "subject_id": canonical,
                "next_step": opportunity.get("decision"), "score": opportunity.get("score"),
                "due_at": str(opportunity.get("observed_at") or ""),
                "stale": False, "priority_class": "opportunity",
            }
            suppressed = _is_suppressed(
                suppressions, source, actor, canonical,
                opportunity.get("target"), opportunity.get("url"))
            if suppressed:
                contact["suppressed"] = True
                item["next_step"] = "suppressed"
                item["suppression_reason"] = "do_not_contact_or_bounced"
            contact["followups"].append(item)
            (suppressed_queue if suppressed else queue).append(item)

    for conversation in growth_engagement.list_conversations(data_dir, service_id):
        source = str(conversation.get("source") or "unknown")
        actor = str(conversation.get("actor_id") or conversation.get("author") or "")
        if not actor:
            continue
        canonical, raw = _identity(source, actor, identity_map)
        contact = _contact(contacts, canonical, raw)
        contact["evidence"].append({
            "stage": "engagement", "ref": conversation["id"], "source": source,
            "at": conversation["received_at"], "basis": "observed_inbound_conversation",
        })
        if conversation.get("status") in growth_engagement.OPEN:
            age = (moment - growth_engagement._instant(conversation["received_at"])).total_seconds() / 60
            item = {
                "kind": "conversation", "ref": conversation["id"], "subject_id": canonical,
                "next_step": "draft" if conversation["status"] == "needs_draft" else conversation["status"],
                "due_at": conversation["received_at"], "stale": age > int(stale_minutes),
                "priority_class": "existing_user" if conversation.get("existing_user") else "conversation",
            }
            suppressed = _is_suppressed(
                suppressions, source, actor, canonical, conversation.get("url"))
            if suppressed:
                contact["suppressed"] = True
                item["next_step"] = "suppressed"
                item["suppression_reason"] = "do_not_contact_or_bounced"
            contact["followups"].append(item)
            (suppressed_queue if suppressed else queue).append(item)

    for experiment in growth_experiments.list_experiments(data_dir, service_id):
        for event in experiment.get("events") or []:
            source = str(event.get("source") or "unknown")
            subject = str(event.get("subject") or "")
            if not subject:
                continue
            canonical, raw = _identity(source, subject, identity_map)
            contact = _contact(contacts, canonical, raw)
            stage = EVENT_STAGES.get(str(event.get("type") or "").lower())
            if not stage:
                contact["unqualified_events"].append({
                    "ref": event["id"], "type": event.get("type"), "reason": "unknown lifecycle event",
                })
                continue
            qualified, reason = _qualify_event(event, stage)
            if not qualified:
                contact["unqualified_events"].append({
                    "ref": event["id"], "type": event.get("type"), "reason": reason,
                })
                continue
            contact["evidence"].append({
                "stage": stage, "ref": event["id"], "source": source,
                "at": event["at"],
                "basis": "verified_product_event" if stage in ("activation", "retention", "payment") else "recorded_product_event",
                "experiment_id": experiment["id"],
            })

    rendered = []
    counts = {stage: 0 for stage in STAGES}
    relationship_counts = {}
    for canonical in sorted(contacts):
        row = contacts[canonical]
        activation_times = [growth_engagement._instant(item["at"])
                            for item in row["evidence"] if item["stage"] == "activation"]
        retained = []
        for item in row["evidence"]:
            if item["stage"] != "retention":
                retained.append(item)
                continue
            observed_at = growth_engagement._instant(item["at"])
            if any(activated_at < observed_at for activated_at in activation_times):
                retained.append(item)
            else:
                row["unqualified_events"].append({
                    "ref": item["ref"], "type": "retention",
                    "reason": "retention lacks an earlier verified activation",
                })
        evidence = sorted(retained, key=lambda item: (item.get("at") or "", item["ref"]))
        if evidence:
            furthest = max(evidence, key=lambda item: RANK[item["stage"]])
            stage = furthest["stage"]
            basis = furthest
        else:
            stage = "unknown"
            basis = None
        observed_stages = {item["stage"] for item in evidence}
        missing = [name for name in STAGES[1:RANK[stage]] if name not in observed_stages] \
            if stage != "unknown" else []
        relationship = _relationship_state(
            row, evidence, moment, risk_days=relationship_risk_days,
            churn_days=relationship_churn_days)
        rendered.append({
            "subject_id": canonical,
            "aliases": sorted(row["aliases"]),
            "stage": stage,
            "historical_lifecycle": stage,
            "current_relationship_state": relationship,
            "last_observed_at": evidence[-1]["at"] if evidence else "",
            "stage_basis": basis,
            "missing_prior_stage_evidence": missing,
            "evidence": evidence,
            "unqualified_events": sorted(row["unqualified_events"], key=lambda item: item["ref"]),
            "followups": sorted(row["followups"], key=lambda item: (item["kind"], item["ref"])),
        })
        counts[stage] += 1
        relationship_counts[relationship] = relationship_counts.get(relationship, 0) + 1
    priority = {"existing_user": 0, "conversation": 1, "opportunity": 2}
    queue.sort(key=lambda item: (priority[item["priority_class"]], item.get("due_at") or "9999", item["ref"]))
    suppressed_queue.sort(key=lambda item: (
        priority[item["priority_class"]], item.get("due_at") or "9999", item["ref"]))
    counts["actionable_followups"] = len(queue)
    counts["suppressed_followups"] = len(suppressed_queue)
    return {
        "schema": SCHEMA,
        "service_id": service_id,
        "as_of": moment.isoformat(timespec="seconds"),
        "stale_minutes": int(stale_minutes),
        "relationship_windows_days": {"risk": relationship_risk_days,
                                      "churn": relationship_churn_days},
        "relationship_counts": relationship_counts,
        "contacts": rendered,
        "counts": counts,
        "followup_queue": queue,
        "suppressed_followups": suppressed_queue,
        "suppression_rule": "suppress.txt plus contacts DNC/bounced via the shared execution suppression loader; damaged sources fail closed",
        "identity_rule": "source-prefixed identities merge only through an explicit identity_map",
        "qualification_rule": "activation/retention/payment need verified method/ref and source_ref; retention also needs an earlier verified activation",
        "boundary": "Historical furthest stage is separate from current relationship health. Follow-up candidates still require DNC, account, scope and execution checks; no causal growth result is claimed.",
    }
