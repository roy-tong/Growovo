from __future__ import annotations

import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import growth_engagement, growth_runtime


SUPPORTED = {"issues": ("opened",), "issue_comment": ("created",),
             "discussion": ("created",), "discussion_comment": ("created",)}


def verify_signature(secret, body, signature_header):
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not key:
        raise ValueError("GitHub webhook secret 必填")
    expected = "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(signature_header or "")):
        raise ValueError("invalid GitHub webhook signature")


def _normalize(event_type, payload, delivery_id):
    repository = (payload.get("repository") or {}).get("full_name") or ""
    installation = str((payload.get("installation") or {}).get("id") or "")
    base = {
        "source": "github_app", "repository": repository, "installation_id": installation,
        "source_event_id": delivery_id, "trust": "untrusted_external_content",
    }
    if event_type == "issues":
        issue = payload.get("issue") or {}
        number = issue.get("number")
        title = str(issue.get("title") or "")
        body = str(issue.get("body") or "")
        return dict(base, external_id=f"issue-{number}", parent_id=f"{repository}#{number}",
                    conversation_key=f"{repository}#{number}", author=(issue.get("user") or {}).get("login") or "unknown",
                    actor_id=str((issue.get("user") or {}).get("id") or ""),
                    body=title + ("\n\n" + body if body else ""), received_at=issue.get("created_at"),
                    url=issue.get("html_url") or "", external_object_type="issue")
    if event_type == "discussion":
        discussion = payload.get("discussion") or {}
        number = discussion.get("number")
        title = str(discussion.get("title") or "")
        body = str(discussion.get("body") or "")
        return dict(base, external_id=f"discussion-{number}", parent_id=f"{repository}/discussion#{number}",
                    conversation_key=f"{repository}/discussion#{number}",
                    author=(discussion.get("user") or {}).get("login") or "unknown",
                    actor_id=str((discussion.get("user") or {}).get("id") or ""),
                    body=title + ("\n\n" + body if body else ""), received_at=discussion.get("created_at"),
                    url=discussion.get("html_url") or "", external_object_type="discussion")
    if event_type == "discussion_comment":
        discussion = payload.get("discussion") or {}
        comment = payload.get("comment") or {}
        number = discussion.get("number")
        return dict(base, external_id=f"discussion-comment-{comment.get('id')}",
                    parent_id=f"{repository}/discussion#{number}",
                    conversation_key=f"{repository}/discussion#{number}",
                    author=(comment.get("user") or {}).get("login") or "unknown",
                    actor_id=str((comment.get("user") or {}).get("id") or ""),
                    body=str(comment.get("body") or ""), received_at=comment.get("created_at"),
                    url=comment.get("html_url") or "", external_object_type="discussion_comment")
    comment = payload.get("comment") or {}
    issue = payload.get("issue") or {}
    number = issue.get("number")
    return dict(base, external_id=f"issue-comment-{comment.get('id')}", parent_id=f"{repository}#{number}",
                conversation_key=f"{repository}#{number}", author=(comment.get("user") or {}).get("login") or "unknown",
                actor_id=str((comment.get("user") or {}).get("id") or ""),
                body=str(comment.get("body") or ""), received_at=comment.get("created_at"),
                url=comment.get("html_url") or "", external_object_type="issue_comment")


def _process(data_dir, service_id, event_type, payload, delivery_id):
    def project(stored_payload, connection=None):
        row = _normalize(event_type, stored_payload, delivery_id)
        return growth_engagement.import_interactions(data_dir, service_id, [row], connection=connection)

    return growth_runtime.project_source_event(data_dir, service_id, delivery_id, project)


def ingest(data_dir, service_id, event_type, payload, delivery_id):
    if event_type not in SUPPORTED or payload.get("action") not in SUPPORTED[event_type]:
        return {"stored": False, "duplicate": False, "ignored": True}
    if not str(delivery_id or "").strip():
        raise ValueError("X-GitHub-Delivery 必填")
    stored = growth_runtime.store_source_event(
        data_dir, service_id, event_type, delivery_id, payload)
    processed = _process(data_dir, service_id, event_type, payload, delivery_id)
    return dict(stored, ignored=False, projection=processed)


def receive(data_dir, service_id, event_type, raw_body, delivery_id, signature, secret):
    verify_signature(secret, raw_body, signature)
    payload = json.loads(raw_body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub webhook JSON 必须是对象")
    return ingest(data_dir, service_id, event_type, payload, delivery_id)


def replay(data_dir, service_id):
    processed = 0
    for row in growth_runtime.pending_source_events(data_dir, service_id):
        _process(data_dir, service_id, row["event_type"], row["payload"], row["source_event_id"])
        processed += 1
    return {"processed": processed}


def create_server(data_dir, service_id, secret, host="127.0.0.1", port=8765):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length") or "0")
                if length <= 0 or length > 1024 * 1024:
                    raise ValueError("invalid webhook content length")
                body = self.rfile.read(length)
                result = receive(
                    data_dir, service_id, self.headers.get("X-GitHub-Event") or "", body,
                    self.headers.get("X-GitHub-Delivery") or "",
                    self.headers.get("X-Hub-Signature-256") or "", secret)
                status = 200
            except (ValueError, json.JSONDecodeError) as exc:
                result = {"error": str(exc)[:200]}
                status = 401
            except Exception as exc:
                result = {"error": type(exc).__name__}
                status = 500
            raw = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format, *args):
            return

    return ThreadingHTTPServer((host, port), Handler)


def serve(data_dir, service_id, secret, host="127.0.0.1", port=8765):
    server = create_server(data_dir, service_id, secret, host=host, port=port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
