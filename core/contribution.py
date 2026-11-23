"""Append-only expert feedback contribution workflow; never performs Git writes."""
from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Any, Dict

from .storage import AppendOnlyJSONL

STATES = ("received", "triaged", "patch_proposed", "reviewed", "merged", "credited", "followed_up")


class ContributionStore:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "state" / "contributions.jsonl"
        self.store = AppendOnlyJSONL(self.path)

    def _latest(self, contribution_id):
        rows = [row for row in self.store.all() if row.get("contribution_id") == contribution_id]
        return rows[-1] if rows else None

    def record(self, interaction_id: str, *, kind: str, by: str, apply: bool = False):
        if kind not in {"material", "artifact"} or not interaction_id or not by:
            raise ValueError("贡献需要 interaction_id、material/artifact 和署名")
        cid = "contribution:" + hashlib.sha256((interaction_id + "\0" + kind).encode()).hexdigest()[:16]
        existing = self._latest(cid) if self.path.exists() else None
        if existing:
            return {"ok": True, "apply": apply, "duplicate": True, "contribution": existing}
        now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        record = {"schema_version": "growovo.contribution/1", "contribution_id": cid,
                  "interaction_id": interaction_id, "kind": kind, "status": "received",
                  "created_by": by, "created_at": now, "history": [{"at": now, "status": "received", "by": by}]}
        if apply:
            self.store.append(record)
        return {"ok": True, "apply": apply, "duplicate": False, "contribution": record}

    def transition(self, contribution_id: str, to: str, *, by: str, note: str = "",
                   change_ref: str = "", reviewer: str = "", credit_ref: str = "",
                   followup_ref: str = "", apply: bool = False):
        current = self._latest(contribution_id)
        if current is None or to not in STATES:
            raise ValueError("贡献不存在或目标状态无效")
        expected = STATES.index(current["status"]) + 1
        if to not in STATES or STATES.index(to) != expected:
            raise ValueError("贡献必须按 %s 顺序推进" % "→".join(STATES))
        if to == "triaged" and not note:
            raise ValueError("triaged 需要判断说明")
        if to == "patch_proposed" and not change_ref:
            raise ValueError("patch_proposed 需要 change_ref")
        if to == "reviewed" and not (reviewer or by):
            raise ValueError("reviewed 需要 reviewer")
        if to == "merged" and (current["status"] != "reviewed" or not change_ref):
            raise ValueError("merged 需要先 review 并提供真实 change_ref")
        if to == "credited" and not credit_ref:
            raise ValueError("credited 需要 credit_ref")
        if to == "followed_up" and not followup_ref:
            raise ValueError("followed_up 需要 followup_ref")
        updated = dict(current, status=to)
        updated.update(change_ref=change_ref or current.get("change_ref") or "",
                       reviewer=reviewer or (by if to == "reviewed" else current.get("reviewer") or ""),
                       credit_ref=credit_ref or current.get("credit_ref") or "",
                       followup_ref=followup_ref or current.get("followup_ref") or "")
        event = {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                 "status": to, "by": by, "note": note, "change_ref": change_ref}
        updated["history"] = list(current.get("history") or []) + [event]
        if apply:
            self.store.append(updated)
        return {"ok": True, "apply": apply, "contribution": updated,
                "git_writes": 0, "external_sends": 0}
