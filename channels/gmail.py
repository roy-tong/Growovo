from __future__ import annotations

import base64
from email.message import EmailMessage
from pathlib import Path

from core.evidence import Evidence, log
from core.gmail_api import GmailClient, decode_body, headers
from core.ledger import Ledger
from core.pipeline import standard_pipeline
from core.throttle import Throttle


DATA = Path(__file__).resolve().parents[1] / "data"


def _client(token=None, client=None):
    if client is not None:
        return client
    return GmailClient(access_token=token) if token else GmailClient.from_env()


def _raw(recipient, text, subject="", in_reply_to=""):
    message = EmailMessage()
    message["To"] = recipient
    message["Subject"] = subject or "Re: your question"
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(text)
    return base64.urlsafe_b64encode(message.as_bytes()).rstrip(b"=").decode("ascii")


def _matches(message, thread_id, recipient, text):
    header = headers(message)
    body = decode_body(message.get("payload") or {})
    return (str(message.get("threadId") or "") == thread_id
            and recipient.lower() in header.get("to", "").lower()
            and " ".join(body.split()) == " ".join(text.split()))


def reply(thread_id, recipient, text, *, go=False, adhoc=False, campaign=None, ledger=None, log=log,
          data_dir=None, token=None, client=None, subject="", in_reply_to=""):
    data = Path(data_dir or DATA)
    ledger = ledger or Ledger(data / "ledger.jsonl")
    throttle = Throttle(data / "state")
    evidence = Evidence(data / "shots" / (campaign or "adhoc"))
    app = _client(token=token, client=client)
    response = {}

    def _compose(item):
        if not thread_id and not subject:
            raise RuntimeError("Gmail 回复需要 thread_id；新邮件需要 subject")

    def _fill(item):
        pass

    def _readback(item):
        return text

    def _send(item):
        nonlocal response
        response = app.send_message(thread_id, _raw(recipient, text, subject, in_reply_to))
        if not response.get("id") or not response.get("threadId"):
            raise RuntimeError("Gmail 未返回完整 message/thread 回执")

    def _confirm(item, timeout=0):
        observed = app.get_message(response["id"])
        return _matches(observed, thread_id or response["threadId"], recipient, text)

    def _shot(item):
        return evidence.write_json("gmail-reply", {"thread_id": thread_id, "body": text[:500]})

    if go:
        import time
        time.sleep(throttle.interval("email", "message"))
    pipe = standard_pipeline(data, ledger, adhoc=adhoc)
    return pipe.run_one(
        {"id": thread_id or "new-message"}, channel="email", action="message", campaign=campaign or "",
        transport="gmail-api", expect=text, target=recipient,
        compose=_compose, fill=_fill, readback=_readback, send=_send, confirm=_confirm,
        shot=_shot, receipt=lambda item: (
            f"gmail://threads/{response.get('threadId')}/messages/{response.get('id')}"),
        dry_run=not go, max_len=60000)


def find_sent_replies(thread_id, recipient, text, *, token=None, client=None):
    app = _client(token=token, client=client)
    thread = app.list_thread(thread_id)
    return [row for row in thread.get("messages") or []
            if "SENT" in (row.get("labelIds") or []) and _matches(row, thread_id, recipient, text)]
