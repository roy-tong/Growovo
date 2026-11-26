"""Persist an operator's reviewed schedule separately from consumable queue state."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from . import approval
from .run_manifest import source_fingerprint
from .storage import atomic_write_text

_active = ContextVar("scheduled_wave_scope", default=None)


class ScopeError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _record(data, config):
    return Path(data) / "approvals" / ("schedule-" + digest(str(Path(config).resolve())) + ".json")


def job_spec(job):
    return {k: v for k, v in job.items() if k not in {"status", "state", "business_result"}}


def _queue(config, job):
    if not job.get("queue"):
        raise ScopeError("scheduled_queue_required")
    path = (Path(config).parent / job["queue"]).resolve()
    if not path.is_file():
        raise ScopeError("scheduled_queue_missing")
    return path


def _signature(row):
    from .engagement_cycle import _normalize
    item = _normalize(row)
    return digest([item[k] for k in ("id", "channel", "action", "target", "text", "actor")])


def strategy_binding(config, job):
    if not job.get("strategy"):
        return None
    from . import output_strategy
    path = (Path(config).parent / job["strategy"]).resolve()
    if not path.is_file():
        raise ScopeError("strategy_missing")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != output_strategy.SCHEMA:
        raise ScopeError("strategy_invalid")
    actual = output_strategy.strategy_digest({key: value for key, value in document.items()
                                               if key != "strategy_sha256"})
    if actual != job.get("strategy_sha256"):
        raise ScopeError("strategy_changed")
    wave_id = str(job.get("wave") or job.get("id") or "")
    item_ids = [str(item.get("id")) for item in document.get("items") or []
                if str(item.get("wave_id") or "") == wave_id]
    if job.get("strategy_item_ids") is not None:
        requested = [str(value) for value in job["strategy_item_ids"]]
        if set(requested) != set(item_ids):
            raise ScopeError("strategy_item_scope_changed")
        item_ids = requested
    return {"path": str(path), "sha256": actual, "item_ids": item_ids}


def _handle(value):
    return str(value or "").strip().lstrip("@").casefold()


_ALLOWED_POLICY_OVERRIDES = {"window", "platforms", "interleave", "queue", "inbox", "content_rules"}


def _policy_overrides(job, *, start=None, end=None):
    value = job.get("policy_overrides") or {}
    if not isinstance(value, dict) or any(key not in _ALLOWED_POLICY_OVERRIDES for key in value):
        raise ScopeError("unknown_policy_override")
    if any(not isinstance(item, dict) for item in value.values()):
        raise ScopeError("invalid_policy_override")
    window = value.get("window") or {}
    if window.get("start_at") or window.get("end_at"):
        try:
            explicit_start = datetime.fromisoformat(str(window["start_at"]).replace("Z", "+00:00"))
            explicit_end = datetime.fromisoformat(str(window["end_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as exc:
            raise ScopeError("invalid_explicit_window") from exc
        if (explicit_start.tzinfo is None or explicit_end.tzinfo is None
                or explicit_end <= explicit_start):
            raise ScopeError("invalid_explicit_window")
        if start is not None and end is not None and (start < explicit_start or end > explicit_end):
            raise ScopeError("execution_window_mismatch")
    return value


def effective_policy(data, job=None):
    from .engagement_cycle import load_policy
    policy = load_policy(data)
    scope = _active.get()
    overrides = _policy_overrides(job) if job is not None else (
        (scope or {}).get("policy_overrides") or {})

    def merge(target, extra):
        for key, value in extra.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = value

    merge(policy, overrides)
    return policy


def approve(data, config, *, by="", preview=False):
    from .cycle_scheduler import _load_config
    from .engagement_cycle import load_queue, _normalize
    data = Path(data).resolve()
    if not preview and not str(by).strip():
        raise ScopeError("schedule_approver_required")
    settings = _load_config(config)
    if str(settings.get("plan_status", "")).startswith("STOPPED"):
        raise ScopeError("schedule_stopped")
    previous_path = _record(data, config)
    previous = json.loads(previous_path.read_text())["jobs"] if previous_path.exists() else {}
    scopes = {}
    capacities = {}
    prior_items = {}
    held_daily = {}
    for job in sorted(settings["jobs"], key=lambda row: datetime.fromisoformat(row["at"].replace("Z", "+00:00"))):
        if job.get("kind") != "wave":
            continue
        path = _queue(config, job)
        rows = [_normalize(row) for row in load_queue(path)]
        binding = strategy_binding(config, job)
        if any(row["channel"] == "github" for row in rows):
            import os
            import shutil
            from .bridge import gh_post
            command = str(gh_post.GH or "")
            available = (os.access(command, os.X_OK) if Path(command).is_absolute()
                         else bool(shutil.which(command)))
            if not available:
                raise ScopeError("scheduled_dependency_missing:gh")
        names = job.get("accounts", {})
        if not isinstance(names, dict):
            raise ScopeError("accounts must map each channel to its account handle")
        accounts = {k: _handle(v) for k, v in names.items()}
        plan_fp = None
        plan_spec = None
        if job.get("plan_id"):
            plan, _ = approval.find_plan(data, job["plan_id"])
            review = approval.verify_approval(plan, data_dir=data, workspace=str(data))
            bound = approval._queue_payloads(plan, data_dir=data)
            if review["legacy"] or str(path) not in {item["path"] for item in bound}:
                raise ScopeError("queue_outside_approved_plan")
            plan_fp = review["plan_sha256"]
            plan_spec = approval.fingerprint(plan)
            for row in rows:
                account = _handle(review["account"])
                if accounts.setdefault(row["channel"], account) != account:
                    raise ScopeError("account_outside_approved_plan")
        allowed = job.get("allowed_channels", list(accounts))
        if not isinstance(allowed, list) or any(channel not in accounts for channel in allowed):
            raise ScopeError("invalid_scheduled_channels")
        if any(row["channel"] not in allowed or accounts.get(row["channel"]) in ("", "default", None)
               for row in rows):
            raise ScopeError("scheduled_account_or_channel_missing")
        items = {row["id"]: _signature(row) for row in rows}
        if len(items) != len(rows):
            raise ScopeError("duplicate_queue_item_id")
        if binding and set(items) != set(binding["item_ids"]):
            raise ScopeError("strategy_item_mismatch")
        if int(job.get("expire_after_sec", job.get("budget_sec", 3000))) <= 0:
            raise ScopeError("positive_window_required")
        from . import engagement_cycle as cycle
        from .ledger import Ledger
        from .throttle import Throttle
        when = datetime.fromisoformat(job["at"].replace("Z", "+00:00"))
        end = when + timedelta(seconds=int(job.get("expire_after_sec", job.get("budget_sec", 3000))))
        if job.get("retry_of"):
            parent = scopes.get(job["retry_of"]) or previous.get(job["retry_of"])
            if not parent or parent.get("accounts") != accounts or any(
                    parent["items"].get(key) != value for key, value in items.items()):
                raise ScopeError("recovery_outside_original_scope")
            if when < datetime.fromisoformat(parent["start"]) or end > datetime.fromisoformat(parent["end"]):
                raise ScopeError("recovery_outside_original_window")
        overrides = _policy_overrides(job, start=when, end=end)
        policy = effective_policy(data, job)
        requested = (job.get("ovo") or {}).get("business_targets") or job.get("targets") or {}
        if not isinstance(requested, dict):
            raise ScopeError("invalid_scheduled_targets")
        if requested:
            if any(not isinstance(value, int) or isinstance(value, bool) or value < 0
                   for value in requested.values()):
                raise ScopeError("invalid_scheduled_targets")
            if any(not requested.get(row["channel"] + "/" + row["action"]) for row in rows):
                raise ScopeError("action_outside_scheduled_targets")
            policy.setdefault("supply", {})["wave_targets"] = requested
        # Simulate competing jobs without reserving real quota or writing a ledger.
        projected_rows = []
        for row in rows:
            identity = (row["channel"], accounts[row["channel"]], cycle._candidate_key(row))
            if identity not in prior_items:
                projected_rows.append(row)
        projection = cycle.plan(projected_rows, policy, ledger=Ledger(data / "ledger.jsonl"),
                                now=when, budget_sec=min(int(job.get("budget_sec", 3000)),
                                                         int(job.get("expire_after_sec", 3000))),
                                throttle=Throttle(data / "state"), data_dir=data,
                                accounts=accounts, runtime_gates=True)
        targets = ((job.get("ovo") or {}).get("business_targets") or job.get("targets") or {})
        if not targets:
            for row in rows:
                action = row["channel"] + "/" + row["action"]
                targets[action] = targets.get(action, 0) + 1
        eligible = Counter()
        blockers = Counter(row["reason"] for row in projection["skipped"])
        if len(rows) != len(projected_rows):
            blockers["reserved_by_earlier_job"] = len(rows) - len(projected_rows)
        for row in projection["scheduled"]:
            channel, action = row["channel"], row["action"]
            account = accounts[channel]
            throttle = Throttle(data / "state", account=account)
            key = (when.astimezone(timezone.utc).date().isoformat(), channel, account,
                   tuple(sorted(throttle.quota_actions(channel, action))))
            left = throttle.remaining(channel, action)
            if left is not None and held_daily.get(key, 0) >= left:
                blockers["daily_quota_reserved"] += 1
                continue
            held_daily[key] = held_daily.get(key, 0) + 1
            eligible[channel + "/" + action] += 1
            prior_items[(channel, account, cycle._candidate_key(row))] = job["id"]
        gaps = {key: value - eligible[key] for key, value in targets.items() if value > eligible[key]}
        capacities[job["id"]] = dict(targets=targets, eligible=dict(eligible), blockers=dict(blockers),
                                     preparation={"shortfall": gaps, "due_at": job["at"],
                                                  "owner": job.get("owner") or by or None} if gaps else None)
        scopes[job["id"]] = dict(job=digest(job_spec(job)), queue=str(path), items=items,
                                 accounts=accounts, plan_id=job.get("plan_id"), plan_sha256=plan_fp,
                                 capacity=capacities[job["id"]], plan_spec=plan_spec,
                                 policy_overrides=overrides,
                                 strategy_sha256=(binding or {}).get("sha256"),
                                 start=when.isoformat(), end=end.isoformat())
    if preview:
        return {"ok": True, "writes": 0, "capacity": capacities}
    from . import scheduled_build
    record = dict(by=by, workspace=str(data), jobs=scopes, schedule=digest(settings["jobs"]),
                  build=scheduled_build.freeze(data),
                  approved_at=datetime.now().astimezone().isoformat(),
                  source=source_fingerprint(Path(__file__).resolve().parents[1])["sha256"])
    atomic_write_text(_record(data, config), json.dumps(record, ensure_ascii=False, sort_keys=True))
    return {"ok": True, "approved_jobs": len(scopes), "source_sha256": record["source"],
            "capacity": capacities}


@contextmanager
def activate(data, config, job):
    path = _queue(config, job)
    approval_path = _record(data, config)
    if not approval_path.is_file():
        raise ScopeError("schedule_approval_required")
    settings = json.loads(Path(config).read_text())
    if str(settings.get("plan_status", "")).startswith("STOPPED"):
        raise ScopeError("schedule_stopped")
    record = json.loads(approval_path.read_text())
    scope = record["jobs"].get(job["id"])
    if not scope or scope["job"] != digest(job_spec(job)) or scope["queue"] != str(path):
        raise ScopeError("scheduled_job_changed")
    binding = strategy_binding(config, job)
    if binding and scope.get("strategy_sha256") != binding["sha256"]:
        raise ScopeError("strategy_changed")
    if record["workspace"] != str(Path(data).resolve()):
        raise ScopeError("scheduled_workspace_changed")
    if source_fingerprint(Path(__file__).resolve().parents[1])["sha256"] != record["source"]:
        raise ScopeError("source_version_changed")
    from .engagement_cycle import load_queue
    rows = load_queue(path)
    items = {row["id"]: _signature(row) for row in rows}
    # 少了的行只能由「已登记的出队事件」解释：早前批准的波次正常出队是进度，
    # 直接把快照改小才是篡改。旧实现一律当成 scheduled_item_changed，同一队列
    # 被多个波次共用时第二波就发不出去。正文被替换或出现未批准的新行仍然拒绝。
    from .queue_store import QueueStore
    consumed = {str(event.get("queue_item_id") or "")
                for event in QueueStore(path).journal.all() if event.get("kind") == "consume"}
    missing = set(scope["items"]) - set(items)
    if len(items) != len(rows) or missing - consumed or any(
            scope["items"].get(key) != value for key, value in items.items()):
        raise ScopeError("scheduled_item_changed")
    if binding and set(scope["items"]) != set(binding["item_ids"]):
        raise ScopeError("strategy_item_mismatch")
    start = datetime.fromisoformat(job["at"].replace("Z", "+00:00"))
    end = start + timedelta(seconds=int(job.get("expire_after_sec", job.get("budget_sec", 3000))))
    token = _active.set(dict(scope, job_id=job["id"], start=start, end=end, data=data, config=config, record=approval_path,
                             approval_digest=digest(record)))
    try:
        check_window()
        yield path, scope["capacity"]
    finally:
        _active.reset(token)


def check_window():
    scope = _active.get()
    if not scope:
        return
    now = datetime.now().astimezone()
    if not scope["start"] <= now < scope["end"]:
        raise ScopeError("outside_approved_window")
    from .cycle_scheduler import _load_config
    settings = _load_config(scope["config"])
    if str(settings.get("plan_status", "")).startswith("STOPPED"):
        raise ScopeError("schedule_stopped")
    if not any(digest(job_spec(job)) == scope["job"] for job in settings["jobs"]):
        raise ScopeError("scheduled_job_changed")
    if digest(json.loads(scope["record"].read_text())) != scope["approval_digest"]:
        raise ScopeError("schedule_approval_changed")
    if scope["plan_id"]:
        plan, _ = approval.find_plan(scope["data"], scope["plan_id"])
        review = (plan or {}).get("review") or {}
        if (not review.get("human_approved") or review.get("plan_sha256") != scope["plan_sha256"]
                or approval.fingerprint(plan) != scope["plan_spec"]):
            raise ScopeError("schedule_plan_approval_changed")
        if review.get("expires_at") and now >= datetime.fromisoformat(review["expires_at"]).astimezone():
            raise ScopeError("schedule_plan_approval_expired")


def check_item(row):
    scope = _active.get()
    if scope:
        check_window()
        if scope["items"].get(row["id"]) != _signature(row):
            raise ScopeError("scheduled_item_changed")


def check_identity(channel, handle):
    scope = _active.get()
    if scope:
        check_window()
        if not _handle(handle) or scope["accounts"].get(channel) != _handle(handle):
            raise ScopeError("scheduled_account_mismatch")


def job_id():
    scope = _active.get()
    return scope["job_id"] if scope else None


def targets():
    scope = _active.get()
    return scope["capacity"]["targets"] if scope else {}


def account(channel):
    scope = _active.get()
    return scope["accounts"].get(channel, "") if scope else ""

def pinned_build(data, config):
    path = _record(data, config)
    if path.is_file():
        return json.loads(path.read_text()).get("build")
    return None

def schedule_issue(data, config, settings):
    path = _record(data, config)
    if path.is_file() and json.loads(path.read_text()).get("schedule") != digest(settings["jobs"]):
        return "scheduled_job_changed"
    return ""

def requires_job(data, queue):
    """A previously bound queue cannot be dispatched by the legacy unscoped wave path."""
    if _active.get():
        return False
    target = str(Path(queue).resolve())
    for path in (Path(data) / "approvals").glob("schedule-*.json"):
        record = json.loads(path.read_text())
        if any(job.get("queue") == target for job in record.get("jobs", {}).values()):
            return True
    return False
