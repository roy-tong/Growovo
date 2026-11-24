from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class GmailAPIError(RuntimeError):
    def __init__(self, status, detail=""):
        super().__init__(f"Gmail API HTTP {status}: {str(detail)[:200]}")
        self.status = int(status)
        self.detail = str(detail)[:300]


def decode_body(payload):
    if not isinstance(payload, dict):
        return ""
    data = (payload.get("body") or {}).get("data")
    if data:
        padding = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode((data + padding).encode("ascii")).decode("utf-8", errors="replace")
    for part in payload.get("parts") or []:
        text = decode_body(part)
        if text:
            return text
    return ""


def headers(message):
    return {str(row.get("name") or "").lower(): str(row.get("value") or "")
            for row in (message.get("payload") or {}).get("headers") or []}


class GmailClient:
    def __init__(self, access_token=None, transport=None):
        self.access_token = str(access_token or os.environ.get("MKT_GMAIL_ACCESS_TOKEN") or "")
        self.transport = transport or self._urllib_transport
        self._cached_token = ""
        self._token_expires = 0
        self._token_lock = threading.Lock()

    @classmethod
    def from_env(cls):
        token_path = os.environ.get("MKT_GMAIL_OAUTH_TOKEN_PATH") or ""
        client_path = os.environ.get("MKT_GMAIL_OAUTH_CLIENT_PATH") or ""
        token = json.loads(Path(token_path).read_text(encoding="utf-8")) if token_path else {}
        client = json.loads(Path(client_path).read_text(encoding="utf-8")) if client_path else {}
        oauth_client = client.get("installed") or client.get("web") or client
        values = {
            "MKT_GMAIL_ACCESS_TOKEN": (None if token.get("refresh_token") else
                                       token.get("access_token") or token.get("token")),
            "MKT_GMAIL_REFRESH_TOKEN": token.get("refresh_token"),
            "MKT_GMAIL_CLIENT_ID": token.get("client_id") or oauth_client.get("client_id"),
            "MKT_GMAIL_CLIENT_SECRET": token.get("client_secret") or oauth_client.get("client_secret"),
        }
        for name, value in values.items():
            if value and not os.environ.get(name):
                os.environ[name] = str(value)
        return cls()

    def _refresh_token(self):
        refresh = os.environ.get("MKT_GMAIL_REFRESH_TOKEN") or ""
        client_id = os.environ.get("MKT_GMAIL_CLIENT_ID") or ""
        client_secret = os.environ.get("MKT_GMAIL_CLIENT_SECRET") or ""
        if not all((refresh, client_id, client_secret)):
            raise ValueError("Gmail 需要 MKT_GMAIL_ACCESS_TOKEN 或完整 OAuth refresh 配置")
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token", "refresh_token": refresh,
            "client_id": client_id, "client_secret": client_secret,
        }).encode("utf-8")
        request = urllib.request.Request(
            "https://oauth2.googleapis.com/token", data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise GmailAPIError(exc.code, "OAuth token refresh failed") from exc
        self._cached_token = str(payload.get("access_token") or "")
        self._token_expires = time.time() + int(payload.get("expires_in") or 0)
        if not self._cached_token:
            raise ValueError("Gmail OAuth 未返回 access token")
        return self._cached_token

    def _token(self):
        if self.access_token:
            return self.access_token
        if self._cached_token and self._token_expires > time.time() + 60:
            return self._cached_token
        with self._token_lock:
            if self._cached_token and self._token_expires > time.time() + 60:
                return self._cached_token
            return self._refresh_token()

    def _urllib_transport(self, method, path, token=None, params=None, payload=None):
        base = path if path.startswith("https://") else "https://gmail.googleapis.com" + path
        if params:
            base += ("&" if "?" in base else "?") + urllib.parse.urlencode(params, doseq=True)
        data = None
        headers = {"Authorization": f"Bearer {token}"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(base, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise GmailAPIError(exc.code, exc.read().decode("utf-8", errors="replace")) from exc
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _request(self, method, path, params=None, payload=None):
        return self.transport(method, path, token=self._token(), params=params, payload=payload)

    def get_profile(self):
        return self._request("GET", "/gmail/v1/users/me/profile")

    def get_message(self, message_id):
        return self._request("GET", f"/gmail/v1/users/me/messages/{message_id}", params={"format": "full"})

    def full_sync(self):
        output = []
        params = {"labelIds": "INBOX", "maxResults": 100}
        latest = ""
        while True:
            response = self._request("GET", "/gmail/v1/users/me/messages", params=params)
            items = response.get("messages") or []
            if len(items) > 1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    output.extend(pool.map(self.get_message, (item["id"] for item in items)))
            else:
                for item in items:
                    output.append(self.get_message(item["id"]))
            for message in output:
                history = str(message.get("historyId") or "")
                if history and (not latest or int(history) > int(latest)):
                    latest = history
            token = response.get("nextPageToken")
            if not token:
                break
            params["pageToken"] = token
        return {"messages": output, "history_id": latest}

    def history_sync(self, start_history_id):
        output = []
        params = {"startHistoryId": start_history_id, "labelId": "INBOX", "maxResults": 100}
        latest = str(start_history_id)
        seen = set()
        while True:
            response = self._request("GET", "/gmail/v1/users/me/history", params=params)
            for record in response.get("history") or []:
                for added in record.get("messagesAdded") or []:
                    message_id = (added.get("message") or {}).get("id")
                    if message_id and message_id not in seen:
                        output.append(self.get_message(message_id))
                        seen.add(message_id)
            latest = str(response.get("historyId") or latest)
            token = response.get("nextPageToken")
            if not token:
                break
            params["pageToken"] = token
        return {"messages": output, "history_id": latest}

    def watch(self, topic):
        return self._request(
            "POST", "/gmail/v1/users/me/watch", payload={"topicName": topic, "labelIds": ["INBOX"]})

    def send_message(self, thread_id, raw):
        payload = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id
        return self._request("POST", "/gmail/v1/users/me/messages/send", payload=payload)

    def list_thread(self, thread_id):
        return self._request("GET", f"/gmail/v1/users/me/threads/{thread_id}")

    def pubsub_pull(self, subscription, max_messages=10):
        return self._request(
            "POST", f"https://pubsub.googleapis.com/v1/{subscription}:pull",
            payload={"maxMessages": int(max_messages)})

    def pubsub_acknowledge(self, subscription, ack_ids):
        return self._request(
            "POST", f"https://pubsub.googleapis.com/v1/{subscription}:acknowledge",
            payload={"ackIds": list(ack_ids)})
