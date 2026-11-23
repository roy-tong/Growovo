"""Immutable correlation context shared by application use cases."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import datetime as dt
import hashlib
from pathlib import Path
from typing import Any, Dict, Optional
import uuid


def clock(now=None) -> dt.datetime:
    value = now or dt.datetime.now().astimezone()
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)


@dataclass(frozen=True)
class RunContext:
    workspace_id: str
    run_id: str
    attempt_id: str
    campaign: str
    wave: str
    created_at: str
    parent_attempt_id: Optional[str] = None
    action_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, data_dir, campaign: str, wave: str, *, now=None, metadata=None):
        workspace = str(Path(data_dir).resolve())
        run_id = "run:" + hashlib.sha256(
            (workspace + "\0" + str(campaign)).encode("utf-8")).hexdigest()[:16]
        return cls(workspace_id=workspace, run_id=run_id,
                   attempt_id="attempt:" + uuid.uuid4().hex[:16],
                   campaign=str(campaign), wave=str(wave),
                   created_at=clock(now).isoformat(timespec="seconds"),
                   metadata=dict(metadata or {}))

    def child(self, *, action_id: str = "", metadata=None):
        return replace(self, attempt_id="attempt:" + uuid.uuid4().hex[:16],
                       parent_attempt_id=self.attempt_id, action_id=action_id,
                       metadata=dict(self.metadata, **(metadata or {})))

    def correlation(self) -> Dict[str, str]:
        return {"workspace_id": self.workspace_id, "run_id": self.run_id,
                "attempt_id": self.attempt_id, "parent_attempt_id": self.parent_attempt_id or "",
                "campaign": self.campaign, "wave": self.wave, "action_id": self.action_id}
