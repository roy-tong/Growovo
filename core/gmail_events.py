from __future__ import annotations

import base64
import datetime as dt
import json

from . import gmail_api, growth_engagement, growth_runtime


SOURCE = "gmail"


def _received_at(message):
    milliseconds = int(message.get("internalDate") or 0)
    return dt.datetime.fromtimestamp(milliseconds / 1000, tz=dt.timezone.utc).isoformat()


def normalize_message(message, account="default"):
    header = gmail_api.headers(message)
    body = gmail_api.decode_body(message.get("payload") or {}) or str(message.get("snippet") or "")
    thread_id = str(message.get("threadId") or "")
    return {
        "source": SOURCE, "external_id": str(message["id"]), "parent_id": thread_id,
        "conversation_key": thread_id, "author": header.get("from") or "unknown",
        "actor_id": "", "body": body, "received_at": _received_at(message),
        "url": f"gmail://threads/{thread_id}/messages/{message['id']}",
        "email_account": account, "thread_id": thread_id,
        "rfc_message_id": header.get("message-id") or "", "subject": header.get("subject") or "",
        "trust": "untrusted_external_content", "external_object_type": "email_message",
    }


def _ingest(data_dir, service_id, message, account):
    if "INBOX" not in (message.get("labelIds") or []):
        return {"added": 0, "duplicates": 0, "ignored": True}
    message_id = str(message["id"])
    stored = growth_runtime.store_source_event(
        data_dir, service_id, "message", message_id, message, source=SOURCE)

    def project(payload, connection=None):
        row = normalize_message(payload, account=account)
        return growth_engagement.import_interactions(data_dir, service_id, [row], connection=connection)

    projected = growth_runtime.project_source_event(
        data_dir, service_id, message_id, project, source=SOURCE)
    return dict(stored, projection=projected, ignored=False)


def replay(data_dir, service_id, account="default"):
    processed = 0
    for row in growth_runtime.pending_source_events(data_dir, service_id, source=SOURCE):
        def project(payload, connection=None):
            normalized = normalize_message(payload, account=account)
            return growth_engagement.import_interactions(
                data_dir, service_id, [normalized], connection=connection)

        growth_runtime.project_source_event(
            data_dir, service_id, row["source_event_id"], project, source=SOURCE)
        processed += 1
    return {"processed": processed}


def sync(data_dir, service_id, client=None, account="default"):
    client = client or gmail_api.GmailClient.from_env()
    cursor = growth_runtime.get_connector_cursor(data_dir, SOURCE, account, service_id) or {}
    full = not cursor.get("history_id")
    if full:
        result = client.full_sync()
    else:
        try:
            result = client.history_sync(cursor["history_id"])
        except gmail_api.GmailAPIError as exc:
            if exc.status != 404:
                raise
            result = client.full_sync()
            full = True
    added = 0
    duplicates = 0
    for message in result["messages"]:
        outcome = _ingest(data_dir, service_id, message, account)
        projection = outcome.get("projection") or {}
        added += int(projection.get("added") or 0)
        duplicates += int(projection.get("duplicates") or 0)
    replayed = replay(data_dir, service_id, account=account)["processed"]
    history_id = str(result.get("history_id") or cursor.get("history_id") or "")
    if not history_id:
        raise ValueError("Gmail 同步未返回 historyId，不能推进游标")
    cursor["history_id"] = history_id
    growth_runtime.save_connector_cursor(data_dir, SOURCE, account, service_id, cursor)
    return {"added": added, "duplicates": duplicates, "replayed": replayed,
            "full_sync": full, "history_id": history_id}


def renew_watch(data_dir, service_id, client, topic, account="default"):
    result = client.watch(topic)
    if not result.get("historyId") or not result.get("expiration"):
        raise ValueError("Gmail watch 未返回 historyId/expiration")
    cursor = growth_runtime.get_connector_cursor(data_dir, SOURCE, account, service_id) or {}
    cursor["history_id"] = str(result["historyId"])
    cursor["watch_expiration"] = str(result["expiration"])
    growth_runtime.save_connector_cursor(data_dir, SOURCE, account, service_id, cursor)
    return {"history_id": cursor["history_id"], "expiration": cursor["watch_expiration"]}


def _notification(message):
    data = ((message or {}).get("message") or {}).get("data") or ""
    padding = "=" * (-len(data) % 4)
    payload = json.loads(base64.urlsafe_b64decode((data + padding).encode("ascii")).decode("utf-8"))
    if not payload.get("emailAddress") or not payload.get("historyId"):
        raise ValueError("Gmail Pub/Sub 通知缺少 emailAddress/historyId")
    return payload


def pull_notifications(data_dir, service_id, client, subscription, account="default"):
    response = client.pubsub_pull(subscription)
    rows = response.get("receivedMessages") or []
    ack_ids = []
    for row in rows:
        _notification(row)
        sync(data_dir, service_id, client=client, account=account)
        if row.get("ackId"):
            ack_ids.append(row["ackId"])
    if ack_ids:
        client.pubsub_acknowledge(subscription, ack_ids)
    return {"received": len(rows), "acknowledged": len(ack_ids)}
