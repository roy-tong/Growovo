from __future__ import annotations

import copy
import datetime as dt
import fcntl
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from .storage import AppendOnlyJSONL, AtomicJSONStore


SCHEMA = """
CREATE TABLE IF NOT EXISTS services (
    service_id TEXT PRIMARY KEY,
    service_json TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_service_idx ON events(service_id, created_at, event_id);
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    worker TEXT NOT NULL,
    intent_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    lease_expires_at INTEGER NOT NULL,
    external INTEGER NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attempts_service_idx ON attempts(service_id, created_at, attempt_id);
CREATE INDEX IF NOT EXISTS attempts_lease_idx ON attempts(status, lease_expires_at);
CREATE TABLE IF NOT EXISTS source_events (
    source TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL,
    PRIMARY KEY(source, source_event_id, service_id)
);
CREATE INDEX IF NOT EXISTS source_events_pending_idx ON source_events(service_id, processed, received_at);
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id TEXT NOT NULL,
    intent_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    external INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(job_id, intent_sha256)
);
CREATE TABLE IF NOT EXISTS execution_capabilities (
    account TEXT NOT NULL,
    platform TEXT NOT NULL,
    action TEXT NOT NULL,
    route TEXT NOT NULL,
    record_json TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(account, platform, action, route)
);
CREATE TABLE IF NOT EXISTS connector_cursors (
    connector TEXT NOT NULL,
    account TEXT NOT NULL,
    service_id TEXT NOT NULL,
    cursor_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(connector, account, service_id)
);
CREATE TABLE IF NOT EXISTS execution_leases (
    account TEXT NOT NULL,
    route TEXT NOT NULL,
    owner TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY(account, route)
);
CREATE TABLE IF NOT EXISTS domain_states (
    domain TEXT NOT NULL,
    object_id TEXT NOT NULL,
    state_json TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(domain, object_id)
);
CREATE TABLE IF NOT EXISTS conversation_states (
    service_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(service_id, conversation_id)
);
CREATE TABLE IF NOT EXISTS conversation_messages (
    service_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    message_json TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY(service_id, conversation_id, external_id)
);
CREATE INDEX IF NOT EXISTS conversation_messages_idx ON conversation_messages(service_id, conversation_id, received_at, external_id);
"""


def _now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _epoch(value=None):
    if value is None:
        return int(dt.datetime.now().timestamp())
    if isinstance(value, dt.datetime):
        return int(value.timestamp())
    return int(dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())


def _service_path(data_dir, service_id):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-_]*", str(service_id or "")):
        raise ValueError("service_id 格式不正确")
    return Path(data_dir) / "state" / "growth-services" / f"{service_id}.json"


def _event_projection(data_dir):
    return AppendOnlyJSONL(Path(data_dir) / "state" / "growth-events.jsonl")


def _database_path(data_dir):
    return Path(data_dir) / "state" / "growth-runtime.sqlite3"


_SCHEMA_READY = set()
_SCHEMA_INIT_LOCK = threading.Lock()


def _initialize_schema(data_dir):
    path = _database_path(data_dir)
    key = str(path)
    if key in _SCHEMA_READY:
        return
    with _SCHEMA_INIT_LOCK:
        if key in _SCHEMA_READY:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(".init.lock").open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            connection = sqlite3.connect(str(path), timeout=5)
            try:
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(SCHEMA)
            finally:
                connection.close()
        _SCHEMA_READY.add(key)


@contextmanager
def _connection(data_dir):
    _initialize_schema(data_dir)
    path = _database_path(data_dir)
    connection = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        yield connection
    finally:
        connection.close()


@contextmanager
def _transaction(data_dir):
    with _connection(data_dir) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()


def _load_tx(connection, data_dir, service_id):
    row = connection.execute(
        "SELECT service_json, version FROM services WHERE service_id = ?", (service_id,)).fetchone()
    if row is not None:
        return json.loads(row["service_json"]), int(row["version"])
    path = _service_path(data_dir, service_id)
    if not path.is_file():
        raise ValueError(f"未知增长服务 {service_id}")
    service = AtomicJSONStore(path, schema="1").load()
    now = str(service.get("updated_at") or _now())
    connection.execute(
        "INSERT INTO services(service_id, service_json, version, updated_at) VALUES (?, ?, 0, ?)",
        (service_id, json.dumps(service, ensure_ascii=False), now))
    return service, 0


def _insert_event_tx(connection, service_id, event):
    value = copy.deepcopy(event)
    value.setdefault("service_id", service_id)
    value.setdefault("at", _now())
    event_id = str(value.get("event_id") or "event-" + uuid.uuid4().hex)
    value["event_id"] = event_id
    connection.execute(
        "INSERT INTO events(event_id, service_id, kind, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (event_id, service_id, str(value["kind"]), json.dumps(value, ensure_ascii=False), value["at"]))
    return value


def _save_tx(connection, data_dir, service, version, event=None):
    service_id = service["id"]
    service["updated_at"] = _now()
    payload = json.dumps(service, ensure_ascii=False)
    changed = connection.execute(
        "UPDATE services SET service_json = ?, version = version + 1, updated_at = ? "
        "WHERE service_id = ? AND version = ?",
        (payload, service["updated_at"], service_id, version)).rowcount
    if changed != 1:
        raise RuntimeError(f"增长服务版本冲突 {service_id}")
    stored_event = _insert_event_tx(connection, service_id, event) if event else None
    AtomicJSONStore(_service_path(data_dir, service_id), schema="1").save(service)
    if stored_event:
        _event_projection(data_dir).append(stored_event)
    return stored_event


def _sync_attempts_tx(connection, service, before):
    previous = {row["id"]: row for row in before.get("actions") or []}
    for action in service.get("actions") or []:
        old = previous.get(action["id"]) or {}
        attempt_id = action.get("active_attempt_id") or old.get("active_attempt_id")
        active = bool(attempt_id)
        if not attempt_id and old.get("status") == "waiting_verification" \
                and action.get("status") in {"completed", "waiting_manual"}:
            attempt_id = action.get("last_attempt_id") or old.get("last_attempt_id")
        if not attempt_id or action.get("status") == "in_progress":
            continue
        connection.execute(
            "UPDATE attempts SET status = ?, evidence_json = ?, updated_at = ? WHERE attempt_id = ?",
            (str(action["status"]), json.dumps(action.get("evidence"), ensure_ascii=False),
             _now(), attempt_id))
        if active:
            action["last_attempt_id"] = attempt_id
            action.pop("active_attempt_id", None)


def create_service(data_dir, service):
    service_id = service["id"]
    with _transaction(data_dir) as connection:
        row = connection.execute(
            "SELECT service_json FROM services WHERE service_id = ?", (service_id,)).fetchone()
        if row is not None:
            return json.loads(row["service_json"])
        path = _service_path(data_dir, service_id)
        if path.is_file():
            existing = AtomicJSONStore(path, schema="1").load()
            connection.execute(
                "INSERT INTO services(service_id, service_json, version, updated_at) VALUES (?, ?, 0, ?)",
                (service_id, json.dumps(existing, ensure_ascii=False), existing.get("updated_at") or _now()))
            return existing
        value = copy.deepcopy(service)
        connection.execute(
            "INSERT INTO services(service_id, service_json, version, updated_at) VALUES (?, ?, 0, ?)",
            (service_id, json.dumps(value, ensure_ascii=False), value.get("updated_at") or _now()))
        event = {"kind": "service_started", "at": value.get("updated_at") or _now()}
        _save_tx(connection, data_dir, value, 0, event)
        return value


def load_service(data_dir, service_id):
    with _transaction(data_dir) as connection:
        service, _ = _load_tx(connection, data_dir, service_id)
        return service


def update_service(data_dir, service_id, mutator, event=None):
    with _transaction(data_dir) as connection:
        service, version = _load_tx(connection, data_dir, service_id)
        before = copy.deepcopy(service)
        result = mutator(service)
        if result is None:
            result = service
        _sync_attempts_tx(connection, service, before)
        stored_event = event(result) if callable(event) else event
        _save_tx(connection, data_dir, service, version, stored_event)
        return result


def events(data_dir, service_id):
    with _connection(data_dir) as connection:
        rows = connection.execute(
            "SELECT payload_json FROM events WHERE service_id = ? ORDER BY created_at, event_id",
            (service_id,)).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]


def attempts(data_dir, service_id):
    with _connection(data_dir) as connection:
        rows = connection.execute(
            "SELECT * FROM attempts WHERE service_id = ? ORDER BY created_at, attempt_id",
            (service_id,)).fetchall()
    return [{
        "id": row["attempt_id"], "service_id": row["service_id"], "action_id": row["action_id"],
        "worker": row["worker"], "intent_sha256": row["intent_sha256"], "status": row["status"],
        "lease_expires_at": row["lease_expires_at"], "external": bool(row["external"]),
        "evidence": json.loads(row["evidence_json"]), "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    } for row in rows]


def claim_action(data_dir, service_id, action_id, worker, payload, lease_seconds=300):
    if not str(worker or "").strip():
        raise ValueError("worker 必填")
    intent = hashlib.sha256(json.dumps(
        payload or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str).encode("utf-8")).hexdigest()
    with _transaction(data_dir) as connection:
        service, version = _load_tx(connection, data_dir, service_id)
        action = next((row for row in service["actions"] if row["id"] == action_id), None)
        if action is None:
            raise ValueError(f"服务 {service_id} 没有动作 {action_id}")
        status = str(action.get("status") or "pending")
        if status in {"completed", "waiting_manual", "waiting_verification"}:
            return {"status": status, "action_id": action_id, "already_completed": status == "completed"}
        if status == "in_progress" and action.get("active_attempt_id"):
            attempt = connection.execute(
                "SELECT intent_sha256 FROM attempts WHERE attempt_id = ?",
                (action["active_attempt_id"],)).fetchone()
            return {"status": "in_progress", "action_id": action_id,
                    "attempt_id": action["active_attempt_id"],
                    "intent_mismatch": bool(attempt and attempt["intent_sha256"] != intent)}
        before = copy.deepcopy(service)
        attempt_id = "attempt-" + uuid.uuid4().hex
        now = _now()
        lease_until = _epoch() + int(lease_seconds)
        connection.execute(
            "INSERT INTO attempts(attempt_id, service_id, action_id, worker, intent_sha256, status, "
            "lease_expires_at, external, evidence_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'claimed', ?, ?, '{}', ?, ?)",
            (attempt_id, service_id, action_id, str(worker), intent, lease_until,
             int(action.get("kind") == "channel"), now, now))
        action["status"] = "in_progress"
        action["active_attempt_id"] = attempt_id
        event = {"kind": "action_claimed", "action_id": action_id, "attempt_id": attempt_id,
                 "worker": str(worker), "intent_sha256": intent, "lease_expires_at": lease_until}
        _save_tx(connection, data_dir, service, version, event)
        return {"status": "claimed", "action_id": action_id, "attempt_id": attempt_id,
                "lease_expires_at": lease_until}


def recover_expired(data_dir, now=None):
    moment = _epoch(now)
    with _connection(data_dir) as connection:
        rows = connection.execute(
            "SELECT attempt_id, service_id, action_id FROM attempts "
            "WHERE status = 'claimed' AND lease_expires_at <= ? ORDER BY lease_expires_at",
            (moment,)).fetchall()
        expired = [dict(row) for row in rows]
    recovered = 0
    for row in expired:
        def mutate(service):
            action = next((item for item in service["actions"]
                           if item["id"] == row["action_id"]
                           and item.get("active_attempt_id") == row["attempt_id"]), None)
            if action is None:
                return service
            external = action.get("kind") == "channel"
            action["status"] = "waiting_verification" if external else "pending"
            action["evidence"] = {"attempt_id": row["attempt_id"], "recovery": "lease_expired"}
            action.pop("active_attempt_id", None)
            connection.execute(
                "UPDATE attempts SET status = ?, updated_at = ? WHERE attempt_id = ?",
                ("expired_unknown" if external else "expired_retryable", _now(), row["attempt_id"]))
            return service

        with _transaction(data_dir) as connection:
            service, version = _load_tx(connection, data_dir, row["service_id"])
            mutate(service)
            event = {"kind": "action_recovered", "action_id": row["action_id"],
                     "attempt_id": row["attempt_id"], "reason": "lease_expired"}
            _save_tx(connection, data_dir, service, version, event)
        recovered += 1
    return {"recovered": recovered, "attempt_ids": [row["attempt_id"] for row in expired]}


def store_source_event(data_dir, service_id, event_type, source_event_id, payload, source="github_app"):
    received_at = _now()
    with _transaction(data_dir) as connection:
        changed = connection.execute(
            "INSERT OR IGNORE INTO source_events(source, source_event_id, service_id, event_type, "
            "payload_json, processed, received_at) VALUES (?, ?, ?, ?, ?, 0, ?)",
            (source, str(source_event_id), service_id, event_type,
             json.dumps(payload, ensure_ascii=False), received_at)).rowcount
        return {"stored": changed == 1, "duplicate": changed == 0,
                "source_event_id": str(source_event_id), "received_at": received_at}


def pending_source_events(data_dir, service_id, source="github_app"):
    with _connection(data_dir) as connection:
        rows = connection.execute(
            "SELECT * FROM source_events WHERE service_id = ? AND source = ? AND processed = 0 "
            "ORDER BY received_at, source_event_id", (service_id, source)).fetchall()
    return [{"source": row["source"], "source_event_id": row["source_event_id"],
             "service_id": row["service_id"], "event_type": row["event_type"],
             "payload": json.loads(row["payload_json"]), "received_at": row["received_at"]}
            for row in rows]


def mark_source_event_processed(data_dir, service_id, source_event_id, source="github_app"):
    with _transaction(data_dir) as connection:
        changed = connection.execute(
            "UPDATE source_events SET processed = 1 WHERE service_id = ? AND source = ? AND source_event_id = ?",
            (service_id, source, str(source_event_id))).rowcount
        if changed != 1:
            raise ValueError(f"未知来源事件 {source_event_id}")


def _scheduled_job_intent(job):
    value = {key: copy.deepcopy(val) for key, val in job.items() if key not in {"status", "state"}}
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")).hexdigest()


def claim_scheduled_job(data_dir, job, external=False):
    job_id = str(job["id"])
    intent = _scheduled_job_intent(job)
    with _transaction(data_dir) as connection:
        row = connection.execute(
            "SELECT status, result_json FROM scheduled_jobs WHERE job_id = ? AND intent_sha256 = ?",
            (job_id, intent)).fetchone()
        if row is not None and row["status"] != "claimed":
            return {"execute": False, "result": json.loads(row["result_json"]),
                    "status": row["status"], "intent_sha256": intent}
        if row is not None and external:
            result = {"ok": True, "results": [{"result": "indeterminate"}],
                      "reason": "previous_attempt_indeterminate"}
            return {"execute": False, "result": result, "status": "needs_reconciliation",
                    "intent_sha256": intent}
        connection.execute(
            "INSERT INTO scheduled_jobs(job_id, intent_sha256, status, result_json, external, updated_at) "
            "VALUES (?, ?, 'claimed', '{}', ?, ?) "
            "ON CONFLICT(job_id, intent_sha256) DO UPDATE SET status = 'claimed', updated_at = excluded.updated_at",
            (job_id, intent, int(bool(external)), _now()))
        return {"execute": True, "status": "claimed", "intent_sha256": intent}


def finish_scheduled_job(data_dir, job, status, result):
    job_id = str(job["id"])
    intent = _scheduled_job_intent(job)
    with _transaction(data_dir) as connection:
        connection.execute(
            "UPDATE scheduled_jobs SET status = ?, result_json = ?, updated_at = ? "
            "WHERE job_id = ? AND intent_sha256 = ?",
            (status, json.dumps(result, ensure_ascii=False), _now(), job_id, intent))


def release_scheduled_job(data_dir, job):
    with _transaction(data_dir) as connection:
        connection.execute(
            "DELETE FROM scheduled_jobs WHERE job_id = ? AND intent_sha256 = ? AND status = 'claimed'",
            (str(job["id"]), _scheduled_job_intent(job)))


def project_source_event(data_dir, service_id, source_event_id, projector, source="github_app"):
    with _transaction(data_dir) as connection:
        row = connection.execute(
            "SELECT payload_json, processed FROM source_events "
            "WHERE service_id = ? AND source = ? AND source_event_id = ?",
            (service_id, source, str(source_event_id))).fetchone()
        if row is None:
            raise ValueError(f"未知来源事件 {source_event_id}")
        if row["processed"]:
            return {"added": 0, "duplicates": 1, "already_processed": True}
        result = projector(json.loads(row["payload_json"]), connection=connection)
        connection.execute(
            "UPDATE source_events SET processed = 1 WHERE service_id = ? AND source = ? AND source_event_id = ?",
            (service_id, source, str(source_event_id)))
        return result


def upsert_execution_capability(data_dir, record, by):
    identity = tuple(str(record[key]) for key in ("account", "platform", "action", "route"))
    with _transaction(data_dir) as connection:
        connection.execute(
            "INSERT INTO execution_capabilities(account, platform, action, route, record_json, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account, platform, action, route) DO UPDATE SET "
            "record_json = excluded.record_json, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (*identity, json.dumps(record, ensure_ascii=False), by, _now()))


def execution_capability_records(data_dir, account=None, platform=None):
    query = "SELECT record_json FROM execution_capabilities"
    clauses = []
    params = []
    if account is not None:
        clauses.append("account = ?")
        params.append(account)
    if platform is not None:
        clauses.append("platform = ?")
        params.append(platform)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY account, platform, action, route"
    with _connection(data_dir) as connection:
        rows = connection.execute(query, params).fetchall()
    return [json.loads(row["record_json"]) for row in rows]


def save_connector_cursor(data_dir, connector, account, service_id, cursor):
    with _transaction(data_dir) as connection:
        connection.execute(
            "INSERT INTO connector_cursors(connector, account, service_id, cursor_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(connector, account, service_id) DO UPDATE SET "
            "cursor_json = excluded.cursor_json, updated_at = excluded.updated_at",
            (connector, account, service_id, json.dumps(cursor, ensure_ascii=False), _now()))
        return copy.deepcopy(cursor)


def get_connector_cursor(data_dir, connector, account, service_id):
    with _connection(data_dir) as connection:
        row = connection.execute(
            "SELECT cursor_json FROM connector_cursors WHERE connector = ? AND account = ? AND service_id = ?",
            (connector, account, service_id)).fetchone()
    return json.loads(row["cursor_json"]) if row else None


def list_connector_cursors(data_dir, connector, account, service_id_prefix="", limit=100):
    prefix = str(service_id_prefix or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with _connection(data_dir) as connection:
        rows = connection.execute(
            "SELECT cursor_json FROM connector_cursors "
            "WHERE connector = ? AND account = ? AND service_id LIKE ? ESCAPE '\\' "
            "ORDER BY updated_at DESC, rowid DESC LIMIT ?",
            (connector, account, prefix + "%", int(limit))).fetchall()
    return [json.loads(row["cursor_json"]) for row in reversed(rows)]


_MISSING = object()


@contextmanager
def _domain_transaction(data_dir, connection=None):
    if connection is not None:
        yield connection
    else:
        with _transaction(data_dir) as owned:
            yield owned


def _load_domain_tx(connection, data_dir, domain, object_id, path, default=_MISSING):
    row = connection.execute(
        "SELECT state_json, version FROM domain_states WHERE domain = ? AND object_id = ?",
        (domain, object_id)).fetchone()
    if row is not None:
        return json.loads(row["state_json"]), int(row["version"])
    path = Path(path)
    if path.is_file():
        state = AtomicJSONStore(path, schema="1").load()
    elif default is not _MISSING:
        state = copy.deepcopy(default)
    else:
        raise ValueError(f"未知状态 {domain}/{object_id}")
    connection.execute(
        "INSERT INTO domain_states(domain, object_id, state_json, version, updated_at) VALUES (?, ?, ?, 0, ?)",
        (domain, object_id, json.dumps(state, ensure_ascii=False), _now()))
    return state, 0


def load_domain_state(data_dir, domain, object_id, path, default=_MISSING, connection=None):
    with _domain_transaction(data_dir, connection) as active:
        state, _ = _load_domain_tx(active, data_dir, domain, object_id, path, default)
        AtomicJSONStore(Path(path), schema="1").save(state)
        return state


def list_domain_states(data_dir, domain, object_id_prefix=""):
    with _connection(data_dir) as connection:
        rows = connection.execute(
            "SELECT state_json FROM domain_states WHERE domain = ? AND object_id LIKE ? "
            "ORDER BY object_id", (domain, str(object_id_prefix) + "%")).fetchall()
    return [json.loads(row["state_json"]) for row in rows]


def update_domain_state(data_dir, domain, object_id, path, mutator, default=_MISSING, connection=None):
    with _domain_transaction(data_dir, connection) as active:
        state, version = _load_domain_tx(active, data_dir, domain, object_id, path, default)
        result = mutator(state)
        if result is None:
            result = state
        active.execute(
            "UPDATE domain_states SET state_json = ?, version = version + 1, updated_at = ? "
            "WHERE domain = ? AND object_id = ? AND version = ?",
            (json.dumps(state, ensure_ascii=False), _now(), domain, object_id, version))
        return result


def _engagement_path(data_dir, service_id):
    return Path(data_dir) / "state" / "growth-engagement" / f"{service_id}.json"


def _load_engagement_tx(connection, data_dir, service_id):
    rows = connection.execute(
        "SELECT conversation_id, state_json FROM conversation_states WHERE service_id = ? ORDER BY conversation_id",
        (service_id,)).fetchall()
    if rows:
        conversations = []
        for row in rows:
            conversation = json.loads(row["state_json"])
            messages = connection.execute(
                "SELECT external_id, message_json FROM conversation_messages "
                "WHERE service_id = ? AND conversation_id = ? ORDER BY received_at, external_id",
                (service_id, row["conversation_id"])).fetchall()
            conversation["messages"] = [json.loads(message["message_json"]) for message in messages]
            conversation["interaction_ids"] = [message["external_id"] for message in messages]
            conversations.append(conversation)
        return {"conversations": conversations}
    legacy, _ = _load_domain_tx(
        connection, data_dir, "growth_engagement", service_id,
        _engagement_path(data_dir, service_id), {"conversations": []})
    for conversation in legacy.get("conversations") or []:
        _upsert_conversation_tx(connection, service_id, conversation)
    return legacy


def _upsert_conversation_tx(connection, service_id, conversation):
    connection.execute(
        "INSERT INTO conversation_states(service_id, conversation_id, state_json, updated_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(service_id, conversation_id) DO UPDATE SET "
        "state_json = excluded.state_json, updated_at = excluded.updated_at",
        (service_id, conversation["id"], json.dumps(conversation, ensure_ascii=False), _now()))
    for message in conversation.get("messages") or []:
        external_id = str(message.get("external_id") or "")
        if not external_id:
            continue
        connection.execute(
            "INSERT OR IGNORE INTO conversation_messages(service_id, conversation_id, external_id, "
            "message_json, received_at) VALUES (?, ?, ?, ?, ?)",
            (service_id, conversation["id"], external_id,
             json.dumps(message, ensure_ascii=False), str(message.get("received_at") or "")))


def load_engagement(data_dir, service_id, connection=None):
    with _domain_transaction(data_dir, connection) as active:
        payload = _load_engagement_tx(active, data_dir, service_id)
        AtomicJSONStore(_engagement_path(data_dir, service_id), schema="1").save(payload)
        return payload


def update_engagement(data_dir, service_id, mutator, connection=None):
    with _domain_transaction(data_dir, connection) as active:
        payload = _load_engagement_tx(active, data_dir, service_id)
        result = mutator(payload)
        for conversation in payload["conversations"]:
            _upsert_conversation_tx(active, service_id, conversation)
        return result


def import_engagement(data_dir, service_id, rows, connection=None):
    return update_engagement(data_dir, service_id, rows, connection=connection)


def conversation_message_count(data_dir, service_id):
    with _connection(data_dir) as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM conversation_messages WHERE service_id = ?",
            (service_id,)).fetchone()
        return int(row["count"])


def try_acquire_execution_lane(data_dir, account, route, owner, lease_seconds=300):
    now = _epoch()
    with _transaction(data_dir) as connection:
        connection.execute("DELETE FROM execution_leases WHERE expires_at <= ?", (now,))
        existing = connection.execute(
            "SELECT owner FROM execution_leases WHERE account = ? AND route = ?",
            (account, route)).fetchone()
        if existing is not None:
            if existing["owner"] != owner:
                return False
            connection.execute(
                "UPDATE execution_leases SET expires_at = ? WHERE account = ? AND route = ? AND owner = ?",
                (now + int(lease_seconds), account, route, owner))
            return True
        changed = connection.execute(
            "INSERT INTO execution_leases(account, route, owner, expires_at) VALUES (?, ?, ?, ?)",
            (account, route, owner, now + int(lease_seconds))).rowcount
        return changed == 1


def release_execution_lane(data_dir, account, route, owner):
    with _transaction(data_dir) as connection:
        connection.execute(
            "DELETE FROM execution_leases WHERE account = ? AND route = ? AND owner = ?",
            (account, route, owner))
