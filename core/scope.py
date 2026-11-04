#!/usr/bin/env python3
"""统一结果范围：campaign + subject + time window。

任何 result/revise/progress/journey 都只读同一范围。指定 campaign 后，缺失
campaign 的历史记录不会被悄悄回退成全量数据。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .followups import Followups


def make_scope(campaign: str = "", subject: str = "", since: str = "", until: str = "") -> Dict[str, str]:
    return {k: v for k, v in (("campaign", campaign), ("subject", subject),
                              ("since", since), ("until", until)) if v}


def _time_of(row: Dict[str, Any]) -> str:
    for key in ("ts", "at", "time", "date", "created", "created_at", "day"):
        value = row.get(key)
        if value:
            return str(value)[:10]
    return ""


def _subject_of(row: Dict[str, Any]) -> str:
    for key in ("subject", "target", "person", "object", "account", "repo"):
        value = row.get(key)
        if value:
            return str(value)
    return ""


def matches(row: Dict[str, Any], scope: Optional[Dict[str, str]]) -> bool:
    scope = scope or {}
    campaign = scope.get("campaign")
    if campaign and row.get("campaign") != campaign:
        return False
    subject = scope.get("subject")
    if subject and _subject_of(row) != subject:
        return False
    since, until = scope.get("since"), scope.get("until")
    if since or until:
        ts = _time_of(row)
        if not ts:
            return False
        if since and ts < since:
            return False
        if until and ts > until:
            return False
    return True


def filter_rows(rows: Iterable[Dict[str, Any]], scope: Optional[Dict[str, str]]) -> List[Dict[str, Any]]:
    return [r for r in rows if matches(r, scope)]


class ScopedFollowups(Followups):
    """Followups 的只读范围投影，不回写，也不回退到全量。"""

    def __init__(self, delegate: Followups, scope: Optional[Dict[str, str]]):
        self._scope = dict(scope or {})
        time_scope = {k: v for k, v in self._scope.items() if k != "campaign"}
        rows: List[Dict[str, Any]] = []
        for original in delegate.all():
            campaign = self._scope.get("campaign")
            touches = original.get("touches") or []
            if campaign:
                # 老记录可能没有 campaign；只有明确同 campaign 的对象/接触才能进入。
                if original.get("campaign") != campaign:
                    touches = [t for t in touches if t.get("campaign") == campaign]
                    if not touches:
                        continue
                else:
                    touches = [t for t in touches if not t.get("campaign") or t.get("campaign") == campaign]
            scoped = dict(original)
            scoped["touches"] = [t for t in touches if matches(t, time_scope)]
            subject = self._scope.get("subject")
            if subject and str(original.get("target") or original.get("subject") or "") != subject:
                continue
            since, until = self._scope.get("since"), self._scope.get("until")
            if since or until:
                if not scoped["touches"]:
                    continue
                # 对象创建时间不能代替窗口内真实互动；窗口内无接触即排除。
                if not any(matches(t, time_scope) for t in scoped["touches"]):
                    continue
            rows.append(scoped)
        self._rows = rows

    def all(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._rows]


def scope_from_args(args) -> Dict[str, str]:
    return make_scope(
        campaign=str(getattr(args, "campaign", "") or ""),
        subject=str(getattr(args, "subject", "") or ""),
        since=str(getattr(args, "since", "") or ""),
        until=str(getattr(args, "until", "") or ""),
    )
