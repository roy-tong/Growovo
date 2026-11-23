"""Evidence-qualified review outreach and private feedback projections.

This module only evaluates candidates and writes local queues or private feedback
files. External writes stay in the normal approved Growovo execution paths.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import reply_case
from .gates import GateError, check_content, check_target, load_suppress
from .queue import lint_queue

SCHEMA = "growovo.review-candidate/1"
FEEDBACK_SCHEMA = "growovo.review-feedback/1"
GENERIC_MAILBOXES = frozenset({
    "contact", "info", "hello", "sales", "support", "admin", "team", "office",
})
ANTI_TEMPLATE = (
    "i saw your work", "i'm building", "a quick yes/no",
    "one boundary i can't settle", "context if useful", "either answer would help",
    "hope this email finds you well", "hope this finds you well",
    "i hope you're doing well", "i came across your profile",
    "this is not a blast", "i'll keep this short", "i'd love to hear your thoughts",
    "冒昧打扰", "久仰大名", "期待您的回复",
)
REVIEW_RUN_PREFIXES = ("expert_review", "review_", "warm_")


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _moment(value: Any, *, default_tz=None) -> Optional[datetime]:
    text = _text(value)
    if not text:
        return None
    try:
        got = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if got.tzinfo is None and default_tz is not None:
        got = got.replace(tzinfo=default_tz)
    return got


def _add(issues: List[str], code: str) -> None:
    if code not in issues:
        issues.append(code)


def evaluate_candidate(candidate: Dict[str, Any], *, now: datetime,
                       contacts: Iterable[Dict[str, Any]] = (),
                       suppressions: Iterable[str] = ()) -> List[str]:
    issues: List[str] = []
    if not isinstance(candidate, dict) or candidate.get("schema") != SCHEMA:
        return ["candidate_schema"]
    person = candidate.get("person") or {}
    hook = candidate.get("hook") or {}
    artifact = candidate.get("artifact") or {}
    sender = candidate.get("sender") or {}
    body = _text(candidate.get("body"))
    subject = _text(candidate.get("subject"))
    mode = _text(candidate.get("mode"))
    target = _text(person.get("target"))
    channel = _text(person.get("channel")) or "email"
    company = _text(person.get("company"))

    try:
        check_target(channel, target)
    except GateError:
        _add(issues, "target_invalid")
    suppressed = {_text(item).casefold() for item in suppressions}
    if target.casefold() in suppressed:
        _add(issues, "suppressed")

    if not _text(person.get("name")) or not _text(hook.get("contribution")):
        _add(issues, "identity_or_hook_missing")
    hook_url = _text(hook.get("url"))
    if not hook_url.startswith("https://"):
        _add(issues, "hook_source_missing")
    checked = _moment(hook.get("checked_at"), default_tz=now.tzinfo)
    if checked is None or checked.astimezone(now.tzinfo).date() != now.date():
        _add(issues, "hook_not_checked_today")

    if person.get("account_type") == "company":
        required = ("name", "role", "department")
        local_part = target.split("@", 1)[0].casefold()
        if any(not _text(person.get(key)) for key in required) \
                or local_part in GENERIC_MAILBOXES:
            _add(issues, "company_account_requires_person")
        try:
            hop = int(person.get("persona_hop") or 0)
            rotation = int(person.get("rotation") or 0)
        except (TypeError, ValueError):
            hop, rotation = 0, 99
        if hop not in (1, 2, 3, 4):
            _add(issues, "persona_hop_invalid")
        if rotation > 3:
            _add(issues, "company_cooled_after_three_rotations")

    matching_contacts = []
    for contact in contacts:
        when = _moment(contact.get("sent_at"), default_tz=now.tzinfo)
        if when is None:
            continue
        when = when.astimezone(now.tzinfo)
        age = now - when
        same_target = _text(contact.get("target")).casefold() == target.casefold()
        if same_target:
            matching_contacts.append(contact)
        if same_target and timedelta(0) <= age < timedelta(days=30):
            _add(issues, "recipient_cooldown_30d")
        same_company = company and _text(contact.get("company")).casefold() == company.casefold()
        if same_company and timedelta(0) <= age < timedelta(days=3):
            _add(issues, "company_cooldown_3d")
    if len(matching_contacts) >= 2 and not _text(candidate.get("reentry_reason")):
        _add(issues, "recipient_reentry_requires_new_evidence")

    try:
        check_content(subject, label="subject ")
        check_content(body, label="body ")
    except GateError:
        _add(issues, "content_integrity")
    low = body.casefold()
    if any(phrase in low for phrase in ANTI_TEMPLATE) or "\u2013" in body:
        _add(issues, "anti_template")
    if any(line.lstrip().startswith(("- ", "* ", "•")) for line in body.splitlines()):
        _add(issues, "email_bullet_list")
    first_name = _text(person.get("name")).split(" ", 1)[0]
    if first_name and first_name not in body:
        _add(issues, "recipient_specific_opening_missing")
    if channel == "email":
        if not _text(sender.get("name")) or not _text(sender.get("identity_url")):
            _add(issues, "sender_identity_missing")
        elif sender["name"] not in body or sender["identity_url"] not in body:
            _add(issues, "sender_identity_missing")
    elif channel == "github" and _text(artifact.get("url")) not in body:
        _add(issues, "artifact_source_missing")

    words = len(body.split())
    questions = body.count("?") + body.count("？")
    if mode == "expert_review":
        if not 60 <= words <= 130:
            _add(issues, "expert_word_count")
        if questions != 1:
            _add(issues, "question_count")
        sentences = len(re.findall(r"[.!?？]+(?=\s|$)", body))
        if not 5 <= sentences <= 8:
            _add(issues, "expert_sentence_count")
        ref = _text(artifact.get("ref"))
        if not ref or "/" not in ref or ref.startswith("."):
            _add(issues, "artifact_too_broad")
        if not _text(artifact.get("url")).startswith("https://"):
            _add(issues, "artifact_source_missing")
        examples = [_text(item) for item in candidate.get("feedback_examples") or []
                    if _text(item)]
        if not 2 <= len(examples) <= 3 \
                or sum(example.casefold() in low for example in examples) < 2:
            _add(issues, "feedback_examples_missing")
    elif mode == "warm":
        if not 50 <= words <= 200:
            _add(issues, "warm_word_count")
        if questions > 1:
            _add(issues, "question_count")
    else:
        _add(issues, "mode_invalid")
    return issues


def _build_queue(candidates: Sequence[Dict[str, Any]], *, channel: str,
                 campaign: str, now: datetime, contacts: Iterable[Dict[str, Any]],
                 suppressions: Iterable[str]) -> Dict[str, Any]:
    contact_rows = list(contacts)
    items: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    seen_bodies = set()
    for candidate in candidates:
        target = _text((candidate.get("person") or {}).get("target"))
        issues = evaluate_candidate(
            candidate, now=now, contacts=contact_rows, suppressions=suppressions)
        actual_channel = _text((candidate.get("person") or {}).get("channel") or "email")
        if actual_channel != channel:
            _add(issues, "channel_not_" + channel)
        body_hash = hashlib.sha256(_text(candidate.get("body")).encode("utf-8")).hexdigest()
        if body_hash in seen_bodies:
            _add(issues, "duplicate_body")
        if len(items) >= 5:
            _add(issues, "batch_cap_5")
        if issues:
            rejected.append({"target": target, "issues": issues})
            continue
        seen_bodies.add(body_hash)
        item = {
            "id": "%s-%03d" % (campaign, len(items) + 1),
            "target": target,
            "body": _text(candidate.get("body")),
            "review": {
                key: copy.deepcopy(candidate.get(key)) for key in
                ("mode", "person", "hook", "artifact", "feedback_examples", "sender")
            },
            "review_candidate_sha256": body_hash,
        }
        if channel == "email":
            item["subject"] = _text(candidate.get("subject"))
        items.append(item)
    action = "message" if channel == "email" else "reply"
    queue = {"campaign": campaign, "channel": channel, "action": action, "items": items}
    if items:
        errors = lint_queue(queue)
        if errors:
            return {"queue": None, "rejected": rejected + [{"target": "queue", "issues": errors}],
                    "human_approval_required": True, "external_action": "none"}
    else:
        queue = None
    return {"queue": queue, "rejected": rejected, "human_approval_required": True,
            "external_action": "none"}


def build_email_queue(candidates: Sequence[Dict[str, Any]], *, campaign: str,
                      now: datetime, contacts: Iterable[Dict[str, Any]],
                      suppressions: Iterable[str]) -> Dict[str, Any]:
    return _build_queue(candidates, channel="email", campaign=campaign, now=now,
                        contacts=contacts, suppressions=suppressions)


def build_github_queue(candidates: Sequence[Dict[str, Any]], *, campaign: str,
                       now: datetime, contacts: Iterable[Dict[str, Any]],
                       suppressions: Iterable[str]) -> Dict[str, Any]:
    return _build_queue(candidates, channel="github", campaign=campaign, now=now,
                        contacts=contacts, suppressions=suppressions)


def load_legacy_history(root) -> List[Dict[str, str]]:
    path = Path(root) / "sent_log.txt"
    if not path.is_file():
        return []
    rows: List[Dict[str, str]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = raw.strip().split("|")
        if len(parts) < 3:
            continue
        if ":" in parts[0] and "@" in parts[1]:
            target, marker = parts[1], parts[0]
        else:
            target, marker = parts[0], parts[1]
        campaign = marker.split(":", 1)[0].strip()
        if not target or not campaign:
            continue
        rows.append({"target": target.strip(), "campaign": campaign,
                     "sent_at": parts[2].strip(), "company": ""})
    return rows


def load_legacy_suppressions(root) -> set:
    return load_suppress(Path(root) / "suppress.txt")


def _atomic_private_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-%d" % os.getpid())
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def sync_feedback(data_dir, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    data = Path(data_dir)
    at = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    written: List[str] = []
    unchanged: List[str] = []
    skipped: List[Dict[str, str]] = []
    for cid, case in sorted(reply_case.load_latest(data).items()):
        run_id = _text(case.get("run_id"))
        if not run_id.startswith(REVIEW_RUN_PREFIXES):
            continue
        if case.get("review_state") not in ("confirmed", "overruled") \
                or not _text(case.get("reviewer")) \
                or case.get("human_label") not in ("interested", "question"):
            skipped.append({"reply_case_id": cid, "reason": "human_review_required"})
            continue
        feedback_id = "rf-" + hashlib.sha256(cid.encode("utf-8")).hexdigest()[:16]
        excerpt = str(case.get("raw_text") or "")[:500]
        record = {
            "schema": FEEDBACK_SCHEMA,
            "feedback_id": feedback_id,
            "reply_case_id": cid,
            "run_id": run_id,
            "source_ref": _text(case.get("source_ref")),
            "received_at": _text(case.get("received_at")),
            "human_label": case.get("human_label"),
            "reviewer": _text(case.get("reviewer")),
            "reviewed_at": _text(case.get("reviewed_at")),
            "feedback_excerpt": excerpt,
            "private": True,
            "sanitization_required": True,
            "repository_write": "not_performed",
            "next_action": "sanitize_then_choose_issue_spec_or_fixture_and_return_public_link",
            "projected_at": at,
        }
        encoded = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path = data / "review-feedback" / (feedback_id + ".json")
        markdown = (
            "# PRIVATE DRAFT: repository feedback\n\n"
            "Not approved for public release. Remove identities and private text before use.\n\n"
            "- Reply case: `%s`\n- Human reviewer: `%s`\n- Repository write: `not_performed`\n\n"
            "## Private excerpt\n\n%s\n\n"
            "## Next action\n\nSanitize, choose an issue/spec/fixture change, land it through "
            "the normal repository review, then return the public link to the reviewer.\n"
        ) % (cid, record["reviewer"], "\n".join("> " + line for line in excerpt.splitlines()))
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = None
            if isinstance(existing, dict):
                existing["projected_at"] = at
                if existing == record:
                    unchanged.append(str(path))
                    continue
        _atomic_private_write(path, encoded)
        _atomic_private_write(path.with_suffix(".md"), markdown)
        written.append(str(path))
    return {"written": written, "unchanged": unchanged, "skipped": skipped,
            "external_action": "none"}


__all__ = [
    "SCHEMA", "FEEDBACK_SCHEMA", "evaluate_candidate", "build_email_queue",
    "build_github_queue", "load_legacy_history", "load_legacy_suppressions", "sync_feedback",
]
