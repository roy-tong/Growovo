"""Read-only projection of drafted and currently eligible engagement supply."""
from __future__ import annotations

import copy
import datetime as dt
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict


def _start(policy: Dict[str, Any], now: dt.datetime) -> tuple:
    from .engagement_cycle import night_start
    if night_start(now, policy):
        return now, False
    hour = int((policy.get("window") or {}).get("start_hour", 22))
    candidate = now.replace(hour=hour, minute=5, second=0, microsecond=0)
    if candidate < now:
        candidate += dt.timedelta(days=1)
    return candidate, True


def assess(data_dir, policy: Dict[str, Any], *, now=None, waves: int = 3) -> Dict[str, Any]:
    from . import engagement_cycle as cycle
    from . import supply_pool
    from .ledger import Ledger
    from .throttle import Throttle
    data_dir = Path(data_dir)
    current = now or cycle._now()
    start, assumed = _start(policy, current)
    count = max(1, int(waves))
    queue_path = data_dir / "state" / "engagement-queue.json"
    initial = cycle.load_queue(queue_path)
    remaining = [dict(row) for row in initial]
    ledger = Ledger(data_dir / "ledger.jsonl")
    simulation = copy.deepcopy(policy)
    section = dict(simulation.get("content_rules") or {})
    section["enforce"] = False
    simulation["content_rules"] = section
    targets = (simulation.get("supply") or {}).get("wave_targets") or {}
    eligible = {key: [] for key in targets}
    blockers = defaultdict(Counter)
    for wave_index in range(count):
        when = start + dt.timedelta(hours=wave_index)
        prepared = cycle.plan(remaining, simulation, ledger=ledger, now=when,
                              budget_sec=1000000, throttle=Throttle(data_dir / "state"),
                              data_dir=data_dir)
        if wave_index == 0:
            for skipped in prepared["skipped"]:
                if skipped.get("reason") == "wave_target_met":
                    continue
                key = "%s/%s" % (skipped.get("channel") or "unknown", skipped.get("action") or "unknown")
                blockers[key][str(skipped.get("reason") or "unknown")] += 1
        chosen_ids = {str(row.get("id") or "") for row in prepared["scheduled"]}
        for key in targets:
            eligible[key].append(int((prepared["planned"] or {}).get(key, 0)))
        if chosen_ids:
            remaining = [row for row in remaining if str(row.get("id") or "") not in chosen_ids]
        else:
            # No row can be consumed; later waves have the same supply unless time rules change.
            pass
    candidates = supply_pool.stats(data_dir, simulation, nights=count, now=current)
    candidate_counts = {"%s/%s" % (row["channel"], row["action"]): row["available"]
                        for row in candidates["channels"]}
    rows = {}
    for key, target_value in targets.items():
        channel, action = key.split("/", 1)
        raw = [cycle._normalize(row) for row in initial
               if cycle._normalize(row)["channel"] == channel
               and cycle._normalize(row)["action"] == action]
        target = int(target_value or 0)
        projected = eligible[key]
        depth = 0
        for value in projected:
            if value < target:
                break
            depth += 1
        rows[key] = {
            "candidate_available": int(candidate_counts.get(key, 0)),
            "queued": len(raw),
            "drafted": sum(1 for row in raw if row["text"]),
            "blank": sum(1 for row in raw if not row["text"]),
            "target_per_wave": target,
            "eligible_by_wave": projected,
            "ready_depth_waves": depth,
            "shortfall": sum(max(0, target - value) for value in projected),
            "blockers": dict(sorted(blockers[key].items())),
        }
    return {
        "schema_version": "growovo.supply-readiness/1",
        "projection_only": True, "writes": 0, "sends": 0,
        "starts_at": start.isoformat(timespec="seconds"),
        "window_assumed": assumed, "waves": count,
        "by_key": rows,
        "queue_path": str(queue_path),
        "note": "候选、成稿和当波 eligible 分开；投影不发送、不改账本，也不预测未来新入站或平台拒绝。",
    }
