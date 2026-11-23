"""Versioned outreach contracts; no destination selection or sending."""
from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
import hashlib
from typing import Any, Dict, List


class OutreachError(ValueError):
    pass


@dataclass
class AccountMap:
    handle: str
    account_type: str
    company_id: str = ""
    person_id: str = ""
    department: str = ""
    persona_rank: int = 0
    evidence: List[str] = field(default_factory=list)
    replacement_history: List[Dict[str, Any]] = field(default_factory=list)
    company_rounds: int = 0
    status: str = "new"

    def validate(self):
        if self.account_type not in {"person", "company"}:
            raise OutreachError("account_type 必须是 person/company")
        if not self.evidence:
            raise OutreachError("账号映射需要公开来源证据")
        if self.account_type == "company" and (
                not self.company_id or not self.person_id or not self.department
                or self.persona_rank not in {1, 2, 3, 4}):
            raise OutreachError("公司账号必须映射到具体员工、部门和 persona 跳序")
        return self

    def next_replacement(self):
        if self.company_rounds >= 3 or self.persona_rank >= 4 or self.status == "cooled":
            return {"allowed": False, "reason": "company_replacement_exhausted"}
        directions = {1: "same_department_peer", 2: "manager", 3: "adjacent_department"}
        return {"allowed": True, "persona_rank": self.persona_rank + 1,
                "direction": directions.get(self.persona_rank, "other_named_person")}


@dataclass
class HookEvidence:
    source_url: str
    quote: str
    subject_ref: str
    observed_at: str
    verified_at: str
    valid_until: str = ""

    def fresh(self, today: str) -> bool:
        try:
            verified = dt.datetime.fromisoformat(self.verified_at.replace("Z", "+00:00"))
            observed = dt.datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        if not self.source_url.startswith(("https://", "http://")) or not self.quote.strip():
            return False
        if verified.date().isoformat() != str(today) or observed > verified:
            return False
        if self.valid_until:
            valid = dt.datetime.fromisoformat(self.valid_until.replace("Z", "+00:00"))
            if verified > valid:
                return False
        return True


@dataclass
class DraftBundle:
    mode: str
    subject: str
    body: str
    account: AccountMap
    hook: HookEvidence
    artifact_ref: str = ""
    feedback_examples: List[str] = field(default_factory=list)
    cta_level: int = 0
    voice_scene: str = "direct"
    checks: Dict[str, Any] = field(default_factory=dict)

    @property
    def body_sha256(self):
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()
