"""Named product-feedback review projections bound to exact conversation quotes."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from . import growth_engagement

SCHEMA = "growovo.product-feedback/1"
CATEGORIES = ("bug", "feature_request", "usability", "praise", "other")


def _id(review):
    value = "%s\0%s\0%s" % (review["conversation_id"], review["category"], review["evidence_quote"])
    return "feedback-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def build(data_dir, service_id, reviews, *, reviewed_by, reviewed_at=None):
    if not str(reviewed_by or "").strip():
        raise ValueError("reviewed_by 必填")
    if not isinstance(reviews, list):
        raise ValueError("feedback reviews 必须是数组")
    conversations = {row["id"]: row for row in growth_engagement.list_conversations(data_dir, service_id)}
    reviewed_at = reviewed_at or dt.datetime.now().astimezone().isoformat(timespec="seconds")
    rows = []
    seen = {}
    duplicates = 0
    for review in reviews:
        if not isinstance(review, dict):
            raise ValueError("feedback review 必须是对象")
        for key in ("conversation_id", "category", "summary", "evidence_quote"):
            if not str(review.get(key) or "").strip():
                raise ValueError("feedback.%s 必填" % key)
        if review["category"] not in CATEGORIES:
            raise ValueError("未知 feedback category")
        conversation = conversations.get(review["conversation_id"])
        if conversation is None:
            raise ValueError("未知 feedback conversation")
        bodies = [conversation.get("body") or ""]
        bodies.extend(str(message.get("body") or "") for message in conversation.get("messages") or [])
        if not any(review["evidence_quote"] in body for body in bodies):
            raise ValueError("evidence_quote 必须逐字来自原会话")
        feedback_id = _id(review)
        row = {
            "feedback_id": feedback_id,
            "category": review["category"], "summary": review["summary"],
            "subject_ref": "%s:%s" % (conversation.get("source") or "unknown",
                                      conversation.get("actor_id") or conversation.get("author") or "unknown"),
            "source": {
                "conversation_id": conversation["id"], "external_id": conversation.get("external_id") or "",
                "url": conversation.get("url") or "", "observed_at": conversation.get("received_at") or "",
                "evidence_quote": review["evidence_quote"],
            },
            "review": {"status": "reviewed", "reviewed_by": str(reviewed_by), "reviewed_at": reviewed_at},
            "canonical_evidence": False, "product_decision_status": "not_decided",
        }
        if feedback_id in seen:
            if seen[feedback_id] != row:
                raise ValueError("feedback_id conflict")
            duplicates += 1
            continue
        seen[feedback_id] = row
        rows.append(row)
    return {
        "schema": SCHEMA, "service_id": service_id, "feedback": rows,
        "summary": {"feedback": len(rows), "duplicates": duplicates,
                    "categories": {category: sum(row["category"] == category for row in rows)
                                   for category in CATEGORIES}},
        "source_state_mutated": False, "external_actions": 0,
        "boundary": "Named human classification and exact source quotes only. Feedback is not canonical demand evidence, a product decision, market prevalence or causal proof.",
    }


def write_new(document, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return target
