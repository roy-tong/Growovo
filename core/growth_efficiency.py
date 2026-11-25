"""Descriptive channel input efficiency from explicit investment and observed outcomes."""
from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation

from . import growth_cycle, growth_engagement, growth_experiments, growth_lifecycle, growth_service

SCHEMA = "growovo.channel-efficiency/1"
INPUT_SCHEMA = "growovo.channel-investment/1"


def _amount(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("cost.amount 必须是有限非负数字") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("cost.amount 必须是有限非负数字")
    return number


def _ratio(value, denominator):
    if denominator <= 0:
        return None
    return format((value / Decimal(denominator)).quantize(Decimal("0.01")), "f")


def _validate(spec, service_id):
    if not isinstance(spec, dict) or spec.get("schema") != INPUT_SCHEMA:
        raise ValueError("投入合同 schema 必须是 %s" % INPUT_SCHEMA)
    if spec.get("service_id") != service_id:
        raise ValueError("投入合同 service_id 不匹配")
    if not str(spec.get("investment_id") or "").strip():
        raise ValueError("investment_id 必填")
    start = growth_cycle._instant((spec.get("window") or {}).get("start"), "window.start")
    end = growth_cycle._instant((spec.get("window") or {}).get("end"), "window.end")
    if end <= start:
        raise ValueError("window.end 必须晚于 start")
    rows = spec.get("channels")
    if not isinstance(rows, list) or not rows:
        raise ValueError("channels 必须是非空数组")
    seen = set()
    for row in rows:
        channel = str(row.get("channel") or "").strip()
        if not channel or channel in seen:
            raise ValueError("channel 必须非空且不重复")
        seen.add(channel)
        minutes = row.get("human_minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes < 0:
            raise ValueError("human_minutes 必须是非负数字")
        if not str(row.get("time_source_ref") or "").strip():
            raise ValueError("time_source_ref 必填")
        if row.get("cost") is not None:
            cost = row["cost"]
            if not isinstance(cost, dict):
                raise ValueError("cost 必须是对象")
            _amount(cost.get("amount"))
            if not str(cost.get("currency") or "").strip() or not str(cost.get("source_ref") or "").strip():
                raise ValueError("cost.currency/source_ref 必填")
    return start, end


def build(data_dir, service_id, spec):
    growth_service.load(data_dir, service_id)
    start, end = _validate(spec, service_id)
    interactions = {row["channel"]: 0 for row in spec["channels"]}
    activations = {row["channel"]: 0 for row in spec["channels"]}
    conversation_records = 0
    for conversation in growth_engagement.list_conversations(data_dir, service_id):
        observed = growth_cycle._instant(conversation.get("received_at"), "received_at")
        if observed < start or observed >= end:
            continue
        conversation_records += 1
        channel = str(conversation.get("source") or "")
        if channel in interactions:
            interactions[channel] += 1

    activation_events = 0
    unqualified = 0
    unassigned = 0
    for experiment in growth_experiments.list_experiments(data_dir, service_id):
        for event in experiment.get("events") or []:
            if event.get("type") != "activation":
                continue
            observed = growth_cycle._instant(event.get("at"), "activation.at")
            if observed < start or observed >= end:
                continue
            activation_events += 1
            qualified, _ = growth_lifecycle._qualify_event(event, "activation")
            if not qualified:
                unqualified += 1
                continue
            channel = str(event.get("channel") or "")
            if channel in activations:
                activations[channel] += 1
            else:
                unassigned += 1

    rendered = []
    for source in spec["channels"]:
        channel = source["channel"]
        minutes = Decimal(str(source["human_minutes"]))
        cost = source.get("cost")
        amount = _amount(cost["amount"]) if cost else None
        rendered.append({
            "channel": channel,
            "human_minutes": source["human_minutes"],
            "time_source_ref": source["time_source_ref"],
            "cost": copy.deepcopy(cost),
            "observed_interactions": interactions[channel],
            "verified_activations": activations[channel],
            "minutes_per_interaction": _ratio(minutes, interactions[channel]),
            "minutes_per_verified_activation": _ratio(minutes, activations[channel]),
            "cost_per_interaction": _ratio(amount, interactions[channel]) if amount is not None else None,
            "cost_per_verified_activation": _ratio(amount, activations[channel]) if amount is not None else None,
            "currency": str(cost["currency"]).upper() if cost else None,
            "zero_denominator_rule": "null_not_zero",
        })
    return {
        "schema": SCHEMA,
        "investment_id": spec["investment_id"],
        "service_id": service_id,
        "window": {"start": spec["window"]["start"], "end": spec["window"]["end"],
                   "end_exclusive": True},
        "channels": rendered,
        "coverage": {
            "conversation_records_in_window": conversation_records,
            "activation_events_in_window": activation_events,
            "unqualified_activation_events": unqualified,
            "unassigned_verified_activations": unassigned,
        },
        "attribution": "explicit_channel_event_only_not_causal",
        "cross_currency_sum": "not_performed",
        "boundary": "Descriptive input per observed conversation or qualified activation with an explicit channel. Zero denominators and unassigned outcomes stay visible; no channel caused an activation and no ROI or budget recommendation is inferred.",
    }
