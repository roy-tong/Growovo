"""Canonical policy hashes and ordered, explainable planning decisions."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List


PRECEDENCE = ("suppress/pause", "identity/approval", "cooldown", "safety",
              "content", "quota", "eligibility/ranking")


class EffectivePolicy:
    def __init__(self, raw: Dict[str, Any]):
        self.raw = copy.deepcopy(raw or {})
        canonical = json.dumps(self.raw, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
        self.sha256 = hashlib.sha256(canonical).hexdigest()
        self.decisions: List[Dict[str, Any]] = []

    @classmethod
    def compile(cls, policy: Dict[str, Any], now=None, data_dir=None):
        return cls(policy)

    @staticmethod
    def _decision(allowed: bool, stage: str, reason: str):
        return {"allowed": allowed, "stage": stage, "reason": reason,
                "rule": stage if allowed else reason}

    def decide(self, row: Dict[str, Any], *, stage: str = "plan", signals=None) -> Dict[str, Any]:
        signals = signals or {}
        ordered = (
            ("suppress", "suppressed"), ("suppress", "paused"),
            ("identity", "identity_missing"), ("approval", "approval_missing"),
            ("cooldown", "cooldown_active"),
            ("safety", "safety_cap_missing"), ("safety", "safety_cap_exhausted"),
            ("content", "content_failed"), ("quota", "quota_exceeded"),
        )
        for name, signal in ordered:
            if signals.get(signal):
                return self._decision(False, name, signal)
        from . import content_rules
        reason = content_rules.check_row(row, self.raw)
        if reason:
            return self._decision(False, "content", reason)
        return self._decision(True, "allowed", "allowed")

    def from_legacy_reason(self, reason: str) -> Dict[str, Any]:
        reason = str(reason or "unknown")
        if reason in {"suppressed", "paused", "global_pause"}:
            stage = "suppress"
        elif reason.startswith("identity"):
            stage = "identity"
        elif reason.startswith("approval") or reason == "permission_denied":
            stage = "approval"
        elif reason in {"actor_cap", "channel_hold", "contact_cooldown", "company_cooldown"}:
            stage = "cooldown"
        elif reason.startswith("safety_"):
            stage = "safety"
        elif reason in {"x_link_in_body", "reply_context_missing", "content_gate_blocked"}:
            stage = "content"
        elif reason in {"night_quota_exhausted", "hour_quota_exhausted", "daily_quota_exhausted"}:
            stage = "quota"
        else:
            stage = "eligibility"
        return self._decision(False, stage, reason)

    def record(self, row: Dict[str, Any], decision: Dict[str, Any]):
        self.decisions.append(dict(channel=str(row.get("channel") or ""),
                                   action=str(row.get("action") or ""),
                                   target=str(row.get("target") or ""), **decision))

    def allow(self, row: Dict[str, Any]):
        self.record(row, self._decision(True, "allowed", "allowed"))

    def explain(self) -> Dict[str, Any]:
        return {"schema_version": "growovo.effective-policy/1", "sha256": self.sha256,
                "precedence": list(PRECEDENCE), "decisions": copy.deepcopy(self.decisions),
                "note": "策略哈希绑定原始配置；决策记录解释既有门禁，不替代审批、身份或执行器。"}
