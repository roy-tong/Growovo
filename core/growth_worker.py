from __future__ import annotations

import datetime as dt
import os
import threading
import uuid

from . import engagement_cycle, github_app, github_events, gmail_api, gmail_events, growth_engagement, growth_runtime


def _now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def status(data_dir, service_id, account="default"):
    return growth_runtime.get_connector_cursor(data_dir, "worker", account, service_id) or {
        "status": "never_run", "service_id": service_id, "account": account,
    }


def history(data_dir, service_id, account="default", limit=100):
    rows = growth_runtime.list_connector_cursors(
        data_dir, "worker_cycle", account, service_id_prefix=str(service_id) + ":cycle:", limit=limit)
    return [row for row in rows if row.get("service_id") == service_id]


def run_once(data_dir, service_id, github_repo=None, gmail_sync=False, watch_topic=None,
             pubsub_subscription=None, account="default", github_client=None, gmail_client=None,
             wave_id=None, cycle_id=None, cycle=1):
    components = {}

    def capture(name, callback):
        try:
            components[name] = {"ok": True, "result": callback()}
        except Exception as exc:
            components[name] = {"ok": False, "error_type": type(exc).__name__,
                                "detail": str(exc)[:200]}

    capture("recovery", lambda: growth_runtime.recover_expired(data_dir))
    if wave_id:
        capture("legacy_wave", lambda: engagement_cycle.run(data_dir, go=True, wave_id=wave_id))
    if github_repo:
        def sync_github():
            client = github_client or github_app.GitHubAppClient.from_env()
            rows = client.sync_community_interactions(github_repo)
            result = growth_engagement.import_interactions(data_dir, service_id, rows)
            result["replayed"] = github_events.replay(data_dir, service_id)["processed"]
            return result

        capture("github", sync_github)
    needs_gmail_sync = gmail_sync or bool(pubsub_subscription)
    if needs_gmail_sync:
        capture("gmail", lambda: gmail_events.sync(
            data_dir, service_id, client=gmail_client, account=account))
    if watch_topic:
        cursor = growth_runtime.get_connector_cursor(data_dir, "gmail", account, service_id) or {}
        expiration = int(cursor.get("watch_expiration") or 0)
        due_at = int(dt.datetime.now().timestamp() * 1000) + 24 * 3600 * 1000
        if expiration <= due_at:
            capture("watch", lambda: gmail_events.renew_watch(
                data_dir, service_id, gmail_client or gmail_api.GmailClient.from_env(),
                watch_topic, account=account))
        else:
            components["watch"] = {"ok": True, "result": {"status": "not_due",
                                                          "expiration": str(expiration)}}
    if pubsub_subscription:
        capture("pubsub", lambda: gmail_events.pull_notifications(
            data_dir, service_id, gmail_client or gmail_api.GmailClient.from_env(),
            pubsub_subscription, account=account))
    cycle_id = str(cycle_id or uuid.uuid4().hex)
    heartbeat = {"last_run_at": _now(), "service_id": service_id,
                 "account": account, "cycle_id": cycle_id, "cycle": int(cycle),
                 "components": components}
    growth_runtime.save_connector_cursor(
        data_dir, "worker_cycle", account, str(service_id) + ":cycle:" + cycle_id, heartbeat)
    growth_runtime.save_connector_cursor(data_dir, "worker", account, service_id, heartbeat)
    return heartbeat


def serve(data_dir, service_id, github_repo=None, gmail_sync=False, watch_topic=None,
          pubsub_subscription=None, account="default", interval_seconds=300, once=False,
          webhook_port=0, max_cycles=None, wave_id=None, on_cycle=None):
    stop = threading.Event()
    server = None
    thread = None
    if webhook_port:
        secret = os.environ.get("MKT_GITHUB_WEBHOOK_SECRET") or ""
        if not secret:
            raise ValueError("启用 GitHub Webhook 需要 MKT_GITHUB_WEBHOOK_SECRET")
        server = github_events.create_server(data_dir, service_id, secret, port=webhook_port)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
    cycles = 0
    result = None
    try:
        while not stop.is_set():
            cycles += 1
            result = run_once(
                data_dir, service_id, github_repo=github_repo, gmail_sync=gmail_sync,
                watch_topic=watch_topic, pubsub_subscription=pubsub_subscription, account=account,
                wave_id=wave_id, cycle=cycles)
            if on_cycle is not None:
                on_cycle(result)
            if once or (max_cycles is not None and cycles >= max_cycles):
                break
            stop.wait(max(1, int(interval_seconds)))
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
    return result
