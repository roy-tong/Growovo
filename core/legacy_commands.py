"""Compatibility handlers for demo, review-ops and proof commands."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import sys

from .ledger import Ledger


def demo(base, data):
    from .demo import run_demo
    base, data = Path(base), Path(data)
    default_data = (base / "data").resolve()
    if data == default_data:
        return run_demo(base if (base / "pyproject.toml").is_file() else Path.cwd())
    return run_demo(data.parent, data_dir=data / "demo")


def _now(value=""):
    if not value:
        return dt.datetime.now().astimezone()
    got = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return got if got.tzinfo else got.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)


def _candidates(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("candidates") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("求评候选文件必须是 JSON 数组，或含 candidates 数组")
    return rows


def _contacts(data_dir, legacy_root):
    from . import review_outreach
    rows = review_outreach.load_legacy_history(legacy_root)
    for row in Ledger(Path(data_dir) / "ledger.jsonl").all():
        if row.get("result") == "committed":
            rows.append({"target": row.get("target") or "", "sent_at": row.get("ts") or "",
                         "campaign": row.get("campaign") or "", "company": ""})
    return rows


def _check(data_dir, *, candidates_path, legacy_root, out_dir, campaign, now):
    from . import review_outreach
    from .gates import load_suppressions
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise ValueError("求评输出目录必须为空；不得覆盖已批准队列")
    out.mkdir(parents=True, exist_ok=True)
    candidates = _candidates(candidates_path)
    suppressions = set(load_suppressions(data_dir))
    suppressions.update(review_outreach.load_legacy_suppressions(legacy_root))
    contacts = _contacts(data_dir, legacy_root)
    email_candidates = [row for row in candidates
                        if str((row.get("person") or {}).get("channel") or "email") == "email"]
    github_candidates = [row for row in candidates
                         if str((row.get("person") or {}).get("channel") or "") == "github"]
    rejected = []
    for row in candidates:
        channel = str((row.get("person") or {}).get("channel") or "email")
        if channel not in ("email", "github"):
            rejected.append({"target": str((row.get("person") or {}).get("target") or ""),
                             "issues": ["channel_unsupported"]})
    email = review_outreach.build_email_queue(email_candidates, campaign=campaign, now=now,
                                               contacts=contacts, suppressions=suppressions)
    github_campaign = campaign + "_github"
    github = review_outreach.build_github_queue(github_candidates, campaign=github_campaign, now=now,
                                                 contacts=contacts, suppressions=suppressions)
    rejected.extend(email["rejected"])
    rejected.extend(github["rejected"])
    hashes = {}
    for name, built in ((campaign, email), (github_campaign, github)):
        queue = built.get("queue")
        if not queue:
            continue
        queue_text = json.dumps(queue, ensure_ascii=False, indent=2) + "\n"
        (out / (name + ".json")).write_text(queue_text, encoding="utf-8")
        hashes[name] = hashlib.sha256(queue_text.encode("utf-8")).hexdigest()
    business_goals = {}
    for built in (email, github):
        for objective, count in (built.get("business_goals") or {}).items():
            business_goals[objective] = business_goals.get(objective, 0) + count
    report = {"schema": "growovo.review-outreach-run/1", "campaign": campaign,
              "checked_at": now.isoformat(timespec="seconds"),
              "accepted": sum(len((built.get("queue") or {}).get("items") or [])
                              for built in (email, github)),
              "rejected": rejected, "queue_sha256": hashes.get(campaign, ""),
              "queue_sha256_by_campaign": hashes, "business_goals": business_goals,
              "capacity_per_channel_per_wave": review_outreach.CHANNEL_WAVE_CAPACITY,
              "quantity_policy": "eligible_only_no_padding", "human_approval_required": True,
              "external_action": "none"}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
    return report


def review_ops(data_dir, args):
    from . import review_outreach
    act = args.review_action
    if act == "feedback":
        result = review_outreach.sync_feedback(data_dir)
        print(json.dumps({"written": len(result["written"]), "unchanged": len(result["unchanged"]),
                          "skipped": len(result["skipped"]), "external_action": "none"},
                         ensure_ascii=False, indent=1))
        return 0
    now = _now(getattr(args, "now", ""))
    if act == "check":
        report = _check(data_dir, candidates_path=args.file, legacy_root=args.legacy_root,
                        out_dir=args.out, campaign=args.campaign, now=now)
        print(json.dumps({"accepted": report["accepted"], "rejected": len(report["rejected"]),
                          "business_goals": report["business_goals"],
                          "capacity_per_channel_per_wave": report["capacity_per_channel_per_wave"],
                          "out": str(Path(args.out)), "human_approval_required": True,
                          "external_action": "none"}, ensure_ascii=False, indent=1))
        return 0
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    def value(key, default):
        path = Path(config.get(key) or default)
        return path if path.is_absolute() else (config_path.parent / path).resolve()
    data = value("workspace", "data")
    out = value("out_root", "runs") / ("run-" + now.strftime("%Y%m%dT%H%M%S%z"))
    report = _check(data, candidates_path=value("candidates", "candidates.json"),
                    legacy_root=value("legacy_root", "legacy"), out_dir=out,
                    campaign=str(config.get("campaign") or "expert_review_v2"), now=now)
    feedback = review_outreach.sync_feedback(data, now=now)
    print(json.dumps({"accepted": report["accepted"], "rejected": len(report["rejected"]),
                      "business_goals": report["business_goals"],
                      "capacity_per_channel_per_wave": report["capacity_per_channel_per_wave"],
                      "feedback_written": len(feedback["written"]),
                      "feedback_skipped": len(feedback["skipped"]), "out": str(out),
                      "human_approval_required": True, "external_action": "none"},
                     ensure_ascii=False, indent=1))
    return 0


def proof(data_dir, args):
    from . import proof_export
    try:
        result = proof_export.export_proof(data_dir, args.run_id, args.out, by=args.by or "",
                                           classification=args.classification or "private")
    except (proof_export.ProofExportError, proof_export.verovo_handoff.HandoffError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
