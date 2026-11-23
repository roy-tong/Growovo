"""Draft-only B2B/warm/expert/my-voice pipeline with fact preservation."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from typing import Any, Dict

from .outreach import AccountMap, DraftBundle, HookEvidence, OutreachError
from . import voice

ANTI_TEMPLATE = ("i saw your work", "hope this email finds you well", "i came across your profile",
                 "this is not a blast", "i'll keep this short")
SCENES = {"warm": "direct", "expert": "direct", "gtm": "gtm", "social": "social",
          "report": "report", "evidence": "evidence"}


def prepare_draft(request: Dict[str, Any], *, today: str) -> Dict[str, Any]:
    errors = []
    try:
        account = AccountMap(**request["account"]).validate()
    except (KeyError, TypeError, OutreachError) as exc:
        return {"ok": False, "errors": ["account_mapping:%s" % exc], "writes": 0, "sends": 0}
    hook = HookEvidence(**request["hook"])
    if not hook.fresh(today):
        errors.append("stale_hook")
    body = str(request.get("body") or "")
    mode = str(request.get("mode") or "warm")
    if mode not in {"warm", "expert"}:
        errors.append("unsupported_mode")
    low = body.casefold()
    if any(term in low for term in ANTI_TEMPLATE) or "—" in body:
        errors.append("anti_template")
    if body.count("?") + body.count("？") != 1:
        errors.append("question_count")
    if not str(request.get("personalization_token") or "") or \
            str(request["personalization_token"]).casefold() not in low:
        errors.append("name_substitution_failed")
    if not str(request.get("sender_name") or "") or not str(request.get("identity_link") or ""):
        errors.append("identity_incomplete")
    if not 40 <= len(body.split()) <= 220:
        errors.append("word_count")
    if mode == "expert":
        if not str(request.get("artifact_ref") or ""):
            errors.append("artifact_required")
        if len(request.get("feedback_examples") or []) < 2:
            errors.append("feedback_examples_required")
        if int(request.get("cta_level", 99)) > 2:
            errors.append("cta_too_expensive")
    profile = request.get("voice_profile") or {}
    if not str(profile.get("source_version") or ""):
        errors.append("voice_profile_version_missing")
    scene = str(profile.get("scene") or mode)
    if scene not in SCENES:
        errors.append("voice_scene_unknown")
    preserved = voice.check_preservation(str(request.get("source_text") or ""), body,
                                         facts=request.get("facts"),
                                         disclosures=request.get("disclosures"))
    if not preserved["ok"]:
        errors.append("facts_not_preserved")
    bundle = DraftBundle(mode=mode, subject=str(request.get("subject") or ""), body=body,
                         account=account, hook=hook, artifact_ref=str(request.get("artifact_ref") or ""),
                         feedback_examples=list(request.get("feedback_examples") or []),
                         cta_level=int(request.get("cta_level") or 0), voice_scene=scene,
                         checks={"preservation": preserved, "profile_version": profile.get("source_version")})
    return {"ok": not errors, "errors": sorted(set(errors)), "voice_scene": SCENES.get(scene, scene),
            "draft": dict(asdict(bundle), body_sha256=bundle.body_sha256),
            "policy_sha256": hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest(),
            "writes": 0, "sends": 0, "approved": False,
            "note": "只校验草稿合同；不选择目标、不授权、不发送、不提交仓库。"}
