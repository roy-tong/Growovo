"""Human-audited KOC qualification and reproducible heuristic tier updates."""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Iterable

from . import grow_fit
from . import supply_pool


def _now(value=None):
    return value or dt.datetime.now().astimezone()


def _tier(entry: Dict[str, Any]) -> str:
    followers = entry.get("followers")
    return grow_fit.account_tier(followers) if isinstance(followers, int) else "unknown"


def _enriched(entry: Dict[str, Any]) -> bool:
    return bool(str(entry.get("bio") or "").strip()) and isinstance(entry.get("followers"), int)


def _qualified(entry: Dict[str, Any]) -> bool:
    qualification = entry.get("qualification") or {}
    return bool(qualification.get("status") == "qualified"
                and qualification.get("by") and qualification.get("at")
                and qualification.get("evidence"))


def report(data_dir) -> Dict[str, Any]:
    accounts = supply_pool.koc_load(data_dir)["accounts"]
    counts = {key: 0 for key in ("registered", "enriched", "qualified", "engaged",
                                  "replied", "tier_stale", "excluded", "eligible_for_outreach")}
    tiers = {}
    rows = []
    for handle in sorted(accounts):
        entry = accounts[handle]
        enriched = _enriched(entry)
        qualified = _qualified(entry)
        engaged = bool(entry.get("last_engaged_at") or int(entry.get("touches") or 0))
        replied = bool(entry.get("reply_receipts") or entry.get("inbound_replies"))
        expected = _tier(entry)
        current = str(entry.get("koc_tier") or "unknown")
        stale = current != expected
        qualification = entry.get("qualification") or {}
        excluded = qualification.get("status") == "excluded"
        eligible = qualified and enriched and not excluded
        values = {"registered": True, "enriched": enriched, "qualified": qualified,
                  "engaged": engaged, "replied": replied, "tier_stale": stale,
                  "excluded": excluded, "eligible_for_outreach": eligible}
        for key, value in values.items():
            counts[key] += int(value)
        tiers[current] = tiers.get(current, 0) + 1
        rows.append({"handle": handle, "enriched": enriched, "qualified": qualified,
                     "engaged": engaged, "replied": replied, "current_tier": current,
                     "computed_tier": expected, "tier_stale": stale,
                     "qualification_status": qualification.get("status") or "unreviewed",
                     "eligible_for_outreach": eligible})
    return {"schema_version": "growovo.koc-qualification/1", "counts": counts,
            "tiers": dict(sorted(tiers.items())), "accounts": rows,
            "note": "registered/enriched/qualified/engaged/replied 是不同状态；is_koc 启发式标签不替代人工资格。"}


def qualify(data_dir, handle: str, *, status: str, by: str, evidence: Iterable[str],
            account_type: str, contact_handle: str = "", note: str = "", now=None) -> Dict[str, Any]:
    if status not in {"qualified", "needs_review", "excluded"}:
        raise ValueError("KOC 资格状态必须是 qualified/needs_review/excluded")
    if not str(by or "").strip():
        raise ValueError("KOC 资格判定必须署名")
    if account_type not in {"person", "company"}:
        raise ValueError("account_type 必须是 person/company；公司账号需先明确类型")
    if account_type == "company" and not str(contact_handle or "").strip():
        raise ValueError("公司账号必须填写具体联系人")
    evidence = [str(item).strip() for item in evidence if str(item).strip()]
    if status == "qualified" and not evidence:
        raise ValueError("qualified 必须提供可核对证据")
    account_name = str(handle or "").strip().lstrip("@")
    lock_handle = supply_pool._locked(data_dir)
    if lock_handle is None:
        return {"ok": False, "reason": "pool_lock_busy"}
    try:
        payload = supply_pool.koc_load(data_dir)
        name = account_name
        entry = payload["accounts"].get(name)
        if entry is None:
            raise ValueError("KOC 登记里没有这个账号：%s" % name)
        if status == "qualified" and not _enriched(entry):
            raise ValueError("qualified 前先补全 bio 和 followers")
        at = _now(now).isoformat(timespec="seconds")
        qualification = {"status": status, "by": by, "at": at, "evidence": evidence,
                         "account_type": account_type, "contact_handle": contact_handle,
                         "note": note}
        entry["qualification"] = qualification
        entry.setdefault("qualification_history", []).append(qualification)
        supply_pool.koc_save(data_dir, payload)
        return {"ok": True, "handle": name, "qualification": qualification}
    finally:
        import fcntl
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def recompute(data_dir, *, apply: bool = False, by: str = "", now=None) -> Dict[str, Any]:
    if apply and not str(by or "").strip():
        raise ValueError("重分层写入必须署名")
    payload = supply_pool.koc_load(data_dir)
    changes = []
    for name, entry in payload["accounts"].items():
        before = str(entry.get("koc_tier") or "unknown")
        after = _tier(entry)
        if before != after:
            changes.append({"handle": name, "before": before, "after": after})
    if not apply:
        return {"ok": True, "apply": False, "changed": len(changes), "changes": changes}
    lock = supply_pool._locked(data_dir)
    if lock is None:
        return {"ok": False, "reason": "pool_lock_busy"}
    try:
        payload = supply_pool.koc_load(data_dir)
        at = _now(now).isoformat(timespec="seconds")
        for change in changes:
            entry = payload["accounts"][change["handle"]]
            entry["koc_tier"] = _tier(entry)
            entry["tier_updated_at"] = at
            entry["tier_updated_by"] = by
        if changes:
            payload["updated_at"] = at
            supply_pool.koc_save(data_dir, payload)
        return {"ok": True, "apply": True, "changed": len(changes), "changes": changes, "by": by}
    finally:
        import fcntl
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
