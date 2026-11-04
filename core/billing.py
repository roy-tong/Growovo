#!/usr/bin/env python3
"""Candidate pricing shapes and explicit subscription/hybrid quote experiments.

Per-action and per-outcome remain the default candidates. Subscription is not a
forbidden word, but it must be an explicit experimental model with recorded
customer acceptance; a legacy ``monthly_fee`` key alone is still ambiguous and
is rejected rather than silently changing the business shape.

Outcome amounts come only from the caller's verified customer-system events.
Accepting a quote is commercial feedback, not proof of payment or product value.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_PRICING: Dict[str, Any] = {
    "model": "action_outcome",
    "setup_fee": 3000.0,        # 元，一次性
    "per_action": 40.0,         # 元 / 动作单元
    "per_outcome": 800.0,       # 元 / 客户系统可验证结果
    "outcome_cap_ratio": 1.0,   # 结果费封顶 = 系数 ×（起步费 + 动作费）
    "subscription_fee": 0.0,    # 仅显式 subscription / hybrid 实验计入
    "settlement_days": 30,
    "billing_period_days": 30,
    "currency": "CNY",
}
PRICING_MODELS = ("action_outcome", "action", "outcome", "subscription", "hybrid")
EXPERIMENT_MODELS = {"subscription", "hybrid"}
MODEL_ALIASES = {
    "per_action": "action", "per-action": "action", "按动作": "action",
    "per_outcome": "outcome", "per-outcome": "outcome", "按结果": "outcome",
    "action+outcome": "action_outcome", "action-outcome": "action_outcome",
    "monthly": "subscription", "订阅": "subscription", "混合": "hybrid",
}
# Kept for backwards compatibility: these keys need an explicit model now.
FORBIDDEN_KEYS = ("monthly", "monthly_fee", "subscription", "per_month", "seat_monthly")
SUBSCRIPTION_ALIASES = ("monthly_fee", "subscription", "monthly", "per_month")
_ALLOWED_KEYS = set(DEFAULT_PRICING) | set(FORBIDDEN_KEYS) | {"pricing_model", "experiment_id"}
_NUMERIC_KEYS = ("setup_fee", "per_action", "per_outcome", "outcome_cap_ratio", "subscription_fee")


def _pricing_model(pricing: Dict[str, Any]) -> str:
    first, second = pricing.get("model"), pricing.get("pricing_model")
    if first and second and first != second:
        raise ValueError("model 与 pricing_model 不能冲突")
    value = str(first or second or "action_outcome").strip().lower()
    value = MODEL_ALIASES.get(value, value)
    if value not in PRICING_MODELS:
        raise ValueError(f"未知报价模型：{value}；可选 {', '.join(PRICING_MODELS)}")
    return value


def _check_no_monthly(pricing: Dict[str, Any], model: Optional[str] = None) -> None:
    model = model or _pricing_model(pricing)
    if model in EXPERIMENT_MODELS:
        return
    for key in FORBIDDEN_KEYS:
        if key in pricing:
            raise ValueError(
                f"不接受孤立的 {key!r} 月费字段。若要做订阅实验，请显式设置 "
                "model='subscription' 或 model='hybrid'，并记录客户接受度。")


def merge_pricing(pricing: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(DEFAULT_PRICING)
    pricing = dict(pricing or {})
    model = _pricing_model(pricing)
    _check_no_monthly(pricing, model)
    unknown = set(pricing) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(f"未知定价字段：{sorted(unknown)}")
    if "seat_monthly" in pricing:
        raise ValueError("seat_monthly 尚未实现座位数核算，不能按 flat fee 静默处理")
    for key, value in pricing.items():
        if key not in FORBIDDEN_KEYS and key != "pricing_model":
            merged[key] = value
    merged["model"] = model
    fee_values = [(key, pricing[key]) for key in SUBSCRIPTION_ALIASES if key in pricing]
    if "subscription_fee" in pricing:
        fee_values.append(("subscription_fee", pricing["subscription_fee"]))
    if fee_values:
        first = fee_values[0][1]
        if any(value != first for _, value in fee_values[1:]):
            raise ValueError("订阅费字段互相冲突")
        merged["subscription_fee"] = first
    if model not in EXPERIMENT_MODELS and merged["subscription_fee"]:
        raise ValueError("只有 subscription / hybrid 实验可以设置订阅费")
    for key in _NUMERIC_KEYS:
        value = merged[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"{key} 必须是非负数字")
        merged[key] = float(value)
    for key in ("settlement_days", "billing_period_days"):
        value = merged[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{key} 必须是正整数")
    if not str(merged["currency"]).strip():
        raise ValueError("currency 不能为空")
    merged["experiment_id"] = str(merged.get("experiment_id") or "").strip()
    return merged


def quote(actions: int, outcomes: int, pricing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Quote one explicit model; subscription/hybrid are marked experimental."""
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in (actions, outcomes)):
        raise ValueError("动作数与结果数必须是非负整数")
    p = merge_pricing(pricing)
    model = p["model"]
    has_action = model in ("action_outcome", "action", "hybrid")
    has_outcome = model in ("action_outcome", "outcome", "hybrid")
    has_subscription = model in EXPERIMENT_MODELS
    setup = round(p["setup_fee"] if model != "subscription" else 0.0, 2)
    subscription_amount = round(p["subscription_fee"] if has_subscription else 0.0, 2)
    action_amount = round(actions * p["per_action"] if has_action else 0.0, 2)
    raw_outcome = round(outcomes * p["per_outcome"] if has_outcome else 0.0, 2)
    # Subscription revenue does not raise the seller's outcome-risk cap.
    cap_base = round(setup + action_amount, 2)
    cap = round(p["outcome_cap_ratio"] * cap_base, 2) if has_outcome else 0.0
    outcome_amount = round(min(raw_outcome, cap), 2) if has_outcome else 0.0
    total = round(setup + subscription_amount + action_amount + outcome_amount, 2)
    formulas = {
        "action_outcome": "起步费 + 动作单元×单价 + min(结果×单价, 封顶)；不收月费",
        "action": "起步费 + 动作单元×单价；不收月费",
        "outcome": "起步费 + min(结果×单价, 封顶)；不收月费",
        "subscription": "订阅费×1个账期（报价实验）；动作与结果仅记录、不计费",
        "hybrid": "起步费 + 订阅费 + 动作单元×单价 + min(结果×单价, 封顶)；报价实验",
    }
    experiment_id = p["experiment_id"]
    return {
        "currency": p["currency"],
        "model": model,
        "pricing_model": model,
        "experimental": model in EXPERIMENT_MODELS or bool(experiment_id),
        "experiment_id": experiment_id or None,
        "acceptance": "unrecorded",
        "setup_fee": setup,
        "actions": actions,
        "per_action": p["per_action"],
        "action_amount": action_amount,
        "outcomes": outcomes,
        "per_outcome": p["per_outcome"],
        "outcome_amount": outcome_amount,
        "outcome_raw": raw_outcome,
        "outcome_capped": raw_outcome > cap if has_outcome else False,
        "outcome_cap": cap,
        "subscription_fee": subscription_amount,
        "subscription_amount": subscription_amount,
        "billing_period_days": p["billing_period_days"] if has_subscription else None,
        "total": total,
        "settlement_days": p["settlement_days"],
        "formula": formulas[model],
    }


def quote_experiment(actions: int, outcomes: int, pricing: Dict[str, Any], *,
                     experiment_id: str) -> Dict[str, Any]:
    """Explicit experiment entry point used by offer/acceptance logging."""
    if not str(experiment_id or "").strip():
        raise ValueError("报价实验必须有 experiment_id")
    model = _pricing_model(pricing)
    if model not in EXPERIMENT_MODELS:
        raise ValueError("quote_experiment 仅用于 subscription / hybrid；其他模型可直接 quote")
    payload = dict(pricing)
    payload["experiment_id"] = experiment_id.strip()
    return quote(actions, outcomes, payload)


def describe(q: Dict[str, Any]) -> str:
    model = q.get("model", "action_outcome")
    title = f"报价（{q['currency']}）"
    if q.get("experimental"):
        title += f" · {model} 实验"
    lines = [title]
    if model != "subscription":
        lines.append(f"  起步费            {q['setup_fee']:>10.2f}")
    if model in ("action_outcome", "action", "hybrid"):
        lines.append(f"  动作 {q['actions']:>3} × {q['per_action']:<6}  {q['action_amount']:>10.2f}")
    if model in EXPERIMENT_MODELS:
        lines.append(f"  订阅/账期         {q['subscription_amount']:>10.2f}")
    if model in ("action_outcome", "outcome", "hybrid"):
        capped = "（已封顶）" if q["outcome_capped"] else ""
        lines.append(f"  结果 {q['outcomes']:>3} × {q['per_outcome']:<6}  "
                     f"{q['outcome_amount']:>10.2f}{capped}")
    lines += [f"  {'─' * 34}", f"  合计              {q['total']:>10.2f}"]
    if model in EXPERIMENT_MODELS:
        lines.append(f"  账期 {q['billing_period_days']} 天；报价实验，接受度需另行记录")
    else:
        lines.append(f"  结算窗口 {q['settlement_days']} 天；不收月费")
    return "\n".join(lines)


def count_actions(entries: Iterable[Dict[str, Any]]) -> int:
    """Count actual action units only; dry-run/unknown are never billable sends."""
    n = 0
    for e in entries:
        result = (e.get("result") or e.get("status") or "").lower()
        if result in ("committed", "sent", "ok", "success"):
            n += 1
    return n


def count_outcomes(events: Iterable[Dict[str, Any]]) -> int:
    """Count customer-system outcomes; approximations are never billable."""
    n = 0
    for e in events:
        if e.get("is_approximation") is True:
            continue
        if e.get("verified") is True or e.get("type") in ("signup", "activation", "paid"):
            n += 1
    return n


def load_jsonl(path) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def quote_from_files(ledger_path, outcomes_path, pricing=None) -> Dict[str, Any]:
    return quote(count_actions(load_jsonl(ledger_path)),
                 count_outcomes(load_jsonl(outcomes_path)),
                 pricing)


def _now(value: Optional[str] = None) -> str:
    if value:
        text = str(value).strip()
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return text
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _acceptance(value: Any) -> Dict[str, Any]:
    if value is True or (isinstance(value, str) and value.lower() in ("accepted", "yes", "true", "接受")):
        return {"status": "accepted", "accepted": True}
    if value is False or (isinstance(value, str) and value.lower() in ("rejected", "no", "false", "拒绝")):
        return {"status": "rejected", "accepted": False}
    if value is None or (isinstance(value, str) and value.lower() in ("pending", "unknown", "待回复", "未知")):
        return {"status": "pending" if value is None else "unknown", "accepted": None}
    raise ValueError("accepted 只能是布尔值、accepted/rejected、pending/unknown")


class PricingExperimentLog:
    """JSONL offers and responses; acceptance is never inferred from a quote."""

    def __init__(self, path):
        self.path = Path(path)

    def _append(self, row: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def offer(self, experiment_id: str, q: Optional[Dict[str, Any]] = None, *,
              actions: int = 0, outcomes: int = 0, pricing: Optional[Dict[str, Any]] = None,
              prospect: str = "", offered_at: Optional[str] = None) -> Dict[str, Any]:
        eid = str(experiment_id or "").strip()
        if not eid:
            raise ValueError("报价实验必须有 experiment_id")
        if any(x.get("event") == "offer" and x.get("experiment_id") == eid for x in load_jsonl(self.path)):
            raise ValueError("experiment_id 已存在，不能重复报价")
        if q is None:
            if not pricing:
                raise ValueError("offer 需要 quote 或 pricing")
            q = quote_experiment(actions, outcomes, pricing, experiment_id=eid)
        else:
            q = dict(q)
            if q.get("experiment_id") not in (None, eid):
                raise ValueError("quote 与 offer 的 experiment_id 不一致")
            q["experiment_id"] = eid
            q["experimental"] = True
        row = {
            "event": "offer", "experiment_id": eid, "model": q.get("model"),
            "total": q.get("total"), "currency": q.get("currency"),
            "prospect": str(prospect or ""), "offered_at": _now(offered_at), "quote": q,
        }
        self._append(row)
        return row

    def record(self, experiment_id: str, accepted: Any, *, reason: str = "",
               responded_at: Optional[str] = None, actor: str = "") -> Dict[str, Any]:
        eid = str(experiment_id or "").strip()
        rows = load_jsonl(self.path)
        offers = [x for x in rows if x.get("event") == "offer" and x.get("experiment_id") == eid]
        if not offers:
            raise ValueError("记录接受度前必须先登记报价")
        if any(x.get("event") == "response" and x.get("experiment_id") == eid for x in rows):
            raise ValueError("该报价已经记录过接受度，不能重复计数")
        state = _acceptance(accepted)
        row = {
            "event": "response", "experiment_id": eid, **state,
            "model": offers[-1].get("model"), "reason": str(reason or ""),
            "actor": str(actor or ""), "responded_at": _now(responded_at),
        }
        self._append(row)
        return row

    def summary(self) -> Dict[str, Any]:
        rows = load_jsonl(self.path)
        offers = {x["experiment_id"]: x for x in rows if x.get("event") == "offer"}
        responses = {x["experiment_id"]: x for x in rows if x.get("event") == "response"}
        by_model: Dict[str, Dict[str, Any]] = {}
        for eid, offer in offers.items():
            model = str(offer.get("model") or "unknown")
            bucket = by_model.setdefault(model, {"offered": 0, "accepted": 0,
                                                 "rejected": 0, "pending": 0,
                                                 "acceptance_rate": None})
            bucket["offered"] += 1
            response = responses.get(eid)
            if not response or response.get("accepted") is None:
                bucket["pending"] += 1
            elif response["accepted"] is True:
                bucket["accepted"] += 1
            else:
                bucket["rejected"] += 1
        for bucket in by_model.values():
            decided = bucket["accepted"] + bucket["rejected"]
            bucket["acceptance_rate"] = round(bucket["accepted"] / decided, 4) if decided else None
        accepted = sum(x["accepted"] for x in by_model.values())
        rejected = sum(x["rejected"] for x in by_model.values())
        decided = accepted + rejected
        return {
            "offered": len(offers), "accepted": accepted, "rejected": rejected,
            "pending": len(offers) - decided,
            "acceptance_rate": round(accepted / decided, 4) if decided else None,
            "by_model": by_model,
            "note": "接受报价只表示商业反馈，不等于已付款、激活或产品价值已验证",
        }


def record_acceptance(path, experiment_id: str, accepted: Any, **kwargs: Any) -> Dict[str, Any]:
    return PricingExperimentLog(path).record(experiment_id, accepted, **kwargs)


def acceptance_summary(path) -> Dict[str, Any]:
    return PricingExperimentLog(path).summary()
