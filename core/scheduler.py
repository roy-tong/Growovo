#!/usr/bin/env python3
"""Persisted queue schedule: run due waves without losing the safety rails.

Growovo already has queues with edit-time lint, approval, pause, budget and a
ledger. What was missing was *when* to run the next wave and what to do after a
crash. This module adds only that:

- a schedule is a small local JSON file with explicit queues, interval, wave cap
  and ``go`` flag; nothing is inferred from the campaign name;
- every tick checks the global pause before touching a queue;
- a wave is journalled as ``running`` before it starts; an interrupted tick
  becomes ``needs_review`` and blocks further waves until a human resumes it;
- a failed or rejected queue stops the schedule instead of retrying it in a loop.

This is not a daemon. ``mkt schedule tick`` is designed to be called by cron,
launchd or a human; the state file makes each call idempotent.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .queue_runner import run_queue
from .safety import is_paused

SCHEMA_VERSION = "1.0"
KIND = "growovo.schedule"
STATUSES = ("active", "paused", "needs_review", "completed", "stopped")


class ScheduleError(RuntimeError):
    pass


def schedules_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "schedules"


def _path(data_dir: Path, schedule_id: str) -> Path:
    return schedules_dir(data_dir) / f"{schedule_id}.json"


def _now(value: Optional[datetime] = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load(data_dir: Path, schedule_id: str) -> Dict[str, Any]:
    path = _path(data_dir, schedule_id)
    if not path.is_file():
        raise ScheduleError(f"unknown schedule {schedule_id!r}")
    return json.loads(path.read_text(encoding="utf-8"))


def save(data_dir: Path, schedule: Dict[str, Any]) -> Path:
    path = _path(data_dir, schedule["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schedule, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def add(data_dir: Path, *, schedule_id: str, queues: List[str], interval_minutes: int,
        go: bool = False, max_waves: Optional[int] = None,
        start_at: Optional[datetime] = None) -> Dict[str, Any]:
    if not str(schedule_id or "").strip():
        raise ScheduleError("schedule needs an id")
    if interval_minutes < 1 or interval_minutes > 7 * 24 * 60:
        raise ScheduleError("interval_minutes must be 1..10080")
    if max_waves is not None and max_waves < 1:
        raise ScheduleError("max_waves must be positive")
    if not isinstance(queues, list) or not queues:
        raise ScheduleError("schedule needs at least one queue path")
    for queue in queues:
        if not Path(queue).is_file():
            raise ScheduleError(f"queue not found: {queue}")
    data_dir = Path(data_dir)
    start = _now(start_at)
    schedule = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "id": schedule_id,
        "queues": [str(Path(queue)) for queue in queues],
        "interval_minutes": interval_minutes,
        "go": bool(go),
        "max_waves": max_waves,
        "waves_done": 0,
        "status": "active",
        "created_at": start.isoformat(timespec="seconds"),
        "last_run_at": None,
        "next_due_at": start.isoformat(timespec="seconds"),
        "stop_reason": None,
        "history": [],
    }
    save(data_dir, schedule)
    return schedule


def list_schedules(data_dir: Path) -> List[Dict[str, Any]]:
    directory = schedules_dir(data_dir)
    if not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob("*.json")):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return rows


def resume(data_dir: Path, schedule_id: str, *, note: str, by: str) -> Dict[str, Any]:
    if not str(note or "").strip() or not str(by or "").strip():
        raise ScheduleError("resume needs a named operator and a note")
    schedule = load(data_dir, schedule_id)
    if schedule.get("status") not in ("needs_review", "paused", "stopped"):
        raise ScheduleError(f"schedule {schedule_id} is {schedule.get('status')}; nothing to resume")
    schedule["status"] = "active"
    schedule["stop_reason"] = None
    schedule["next_due_at"] = _now().isoformat(timespec="seconds")
    schedule.setdefault("history", []).append({
        "at": _now().isoformat(timespec="seconds"), "kind": "resume",
        "by": str(by), "note": str(note)[:300],
    })
    save(data_dir, schedule)
    return schedule


def tick(data_dir: Path, *, now: Optional[datetime] = None,
         runner: Callable[..., Any] = run_queue,
         log: Callable[..., Any] = print) -> Dict[str, Any]:
    """Run at most one due wave per schedule; return what happened."""
    data_dir = Path(data_dir)
    moment = _now(now)
    ran: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for schedule in list_schedules(data_dir):
        schedule_id = schedule.get("id")
        status = schedule.get("status")
        if status == "running":
            schedule["status"] = "needs_review"
            schedule["stop_reason"] = "interrupted_wave"
            save(data_dir, schedule)
            skipped.append({"id": schedule_id, "reason": "interrupted"})
            continue
        if status in ("needs_review", "stopped", "completed", "paused"):
            skipped.append({"id": schedule_id, "reason": status})
            continue
        due = _parse(str(schedule.get("next_due_at") or ""))
        if due is not None and due > moment:
            skipped.append({"id": schedule_id, "reason": "not_due"})
            continue
        if is_paused(data_dir):
            schedule["next_due_at"] = (moment + timedelta(minutes=int(schedule["interval_minutes"]))).isoformat(timespec="seconds")
            schedule.setdefault("history", []).append({
                "at": moment.isoformat(timespec="seconds"), "kind": "skip", "reason": "paused"})
            save(data_dir, schedule)
            skipped.append({"id": schedule_id, "reason": "paused"})
            continue
        schedule["status"] = "running"
        save(data_dir, schedule)
        wave: Dict[str, Any] = {"at": moment.isoformat(timespec="seconds"), "kind": "wave",
                                "queues": []}
        try:
            for queue_path in schedule.get("queues") or []:
                if is_paused(data_dir):
                    raise ScheduleError("global pause turned on during the wave")
                records = runner(Path(queue_path), data_dir=data_dir, go=bool(schedule.get("go")),
                                 log=log)
                records = list(records or [])
                results = [str(record.get("result") or "") for record in records if isinstance(record, dict)]
                wave["queues"].append({"queue": queue_path, "records": len(records),
                                       "results": results})
                if any(result in ("indeterminate", "failed", "rejected") for result in results):
                    raise ScheduleError(f"queue {queue_path} produced an unknown or failed result")
        except Exception as exc:
            schedule["status"] = "needs_review"
            schedule["stop_reason"] = f"{type(exc).__name__}: {exc}"[:300]
            wave["outcome"] = "needs_review"
            wave["error"] = str(exc)[:300]
            schedule.setdefault("history", []).append(wave)
            save(data_dir, schedule)
            ran.append({"id": schedule_id, "outcome": "needs_review", "error": str(exc)[:200]})
            continue
        schedule["waves_done"] = int(schedule.get("waves_done") or 0) + 1
        schedule["last_run_at"] = moment.isoformat(timespec="seconds")
        schedule["next_due_at"] = (moment + timedelta(minutes=int(schedule["interval_minutes"]))).isoformat(timespec="seconds")
        wave["outcome"] = "ok"
        schedule.setdefault("history", []).append(wave)
        max_waves = schedule.get("max_waves")
        if max_waves is not None and schedule["waves_done"] >= int(max_waves):
            schedule["status"] = "completed"
            schedule["stop_reason"] = "max_waves_reached"
        else:
            schedule["status"] = "active"
        save(data_dir, schedule)
        ran.append({"id": schedule_id, "outcome": wave["outcome"],
                    "waves_done": schedule["waves_done"]})
    return {"generated_at": moment.isoformat(timespec="seconds"),
            "ran": ran, "skipped": skipped, "due": len(ran)}
