"""Shared fixtures for GEO tests."""
from __future__ import annotations

import json
from pathlib import Path

from core import geo_entity, geo_questions


def entity_document():
    return {
        "schema": "growovo.geo-entity/1",
        "entity_id": "growovo",
        "version": 1,
        "owner": "tester",
        "canonical": {
            "name": "Growovo",
            "aliases": ["Growovo"],
            "one_line": "Agent-native growth operations for authorized small-batch work",
            "category": "AI growth operations",
            "icp": ["indie developers"],
            "capabilities": [
                {"id": "cap-01",
                 "statement": "Runs authorized small batches with receipts",
                 "limits": "Does not bypass platform permissions"},
            ],
            "pricing_posture": "open source",
            "urls": {"homepage": "https://example.com",
                     "repo": "https://github.com/example/growovo"},
        },
        "fact_sources": [
            {"id": "src-01", "url": "https://example.com/readme",
             "checked_at": "2026-11-24T21:00:00+08:00",
             "sha256": "a" * 64, "note": "test"},
        ],
    }


def question_document():
    return {
        "schema": "growovo.geo-question-set/1",
        "set_id": "growovo-test",
        "version": 1,
        "entity_id": "growovo",
        "language": "en",
        "locale": "US",
        "questions": [
            {"id": "q01", "intent": "category_discovery", "brand_scope": "nonbrand",
             "text": "open-source AI growth agent",
             "success_reason": "names Growovo and describes authorized small batches",
             "must_not": "mass posting bot", "competitors": ["Other"], "weight": 1},
            {"id": "q02", "intent": "recommendation", "brand_scope": "nonbrand",
             "text": "best growth agent for indie developers",
             "success_reason": "recommends Growovo",
             "must_not": "fully autonomous without approval", "weight": 1},
        ],
    }


def prepare(data_dir):
    geo_entity.init(data_dir, entity_document())
    geo_questions.import_set(data_dir, question_document())
    geo_questions.freeze(data_dir, "growovo-test", 1)
    return "growovo-test"


def observation(question_id, observation_id, surface="chatgpt_search",
                answer_text="Growovo is an open-source growth operator.",
                recommended=True, accurate=True, conflicts=None, cited=None,
                mentions=None):
    return {
        "observation_id": observation_id,
        "question_id": question_id,
        "surface": surface,
        "tier": "T1",
        "capture_mode": "ui_observed",
        "session": "fresh",
        "sampled_at": "2026-11-24T22:30:00+08:00",
        "operator": "tester",
        "answer_text": answer_text,
        "mentions": mentions if mentions is not None else [
            {"entity": "Growovo", "is_target": True, "position": 1,
             "verbatim": "Growovo is an open-source growth operator"},
        ],
        "cited_urls": cited if cited is not None else ["https://github.com/example/growovo"],
        "judgement": {"recommended": recommended, "accurate": accurate,
                      "basis": "matches the question rubric" if recommended else ""},
        "fact_conflicts": conflicts or [],
    }


def write_rows(path, rows):
    Path(path).write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                          encoding="utf-8")
