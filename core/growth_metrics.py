from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from channels.x import read_stats
from . import growth_service
from .growth_intake import fetch_repo_metadata


def _evidence(path):
    candidate = Path(path)
    return {"path": str(candidate), "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()}


def collect_metrics(data_dir, repos, x_handle, phase="baseline"):
    stamp = dt.datetime.now().astimezone().isoformat(timespec="microseconds")
    directory = Path(data_dir) / "state" / "growth-metrics" / (
        stamp.replace(":", "").replace("+", "_") + "-" + phase)
    directory.mkdir(parents=True, exist_ok=True)
    metrics = {"github_stars_by_repo": {}}
    evidence = []
    totals = []
    for repo in repos:
        metadata = fetch_repo_metadata(repo)
        path = directory / (repo.replace("/", "__") + ".json")
        path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        evidence.append(_evidence(path))
        metrics["github_stars_by_repo"][repo] = metadata
        totals.append(metadata.get("stars") if metadata.get("status") == "OBSERVED" else None)
    metrics["github_stars"] = sum(totals) if all(isinstance(value, int) for value in totals) else None
    if x_handle:
        stats = read_stats.read(
            x_handle, directory / "x", data_dir=data_dir, log=lambda *_: None)
        metrics["x_followers"] = stats.get("followers") if isinstance(stats.get("followers"), int) else None
        if isinstance(stats.get("impressions_yesterday"), int):
            metrics["x_impressions"] = stats["impressions_yesterday"]
        if stats.get("saved") and Path(stats["saved"]).is_file():
            evidence.append(_evidence(stats["saved"]))
        for shot in stats.get("screenshots") or []:
            if Path(shot).is_file():
                evidence.append(_evidence(shot))
    else:
        metrics["x_followers"] = None
    return {"metrics": metrics, "evidence": evidence,
            "observed_at": stamp, "phase": phase, "causal_credit": "not_claimed"}


def observe_live(data_dir, service_id, phase, repos, x_handle, by):
    observed = collect_metrics(data_dir, repos, x_handle, phase=phase)
    return growth_service.observe(
        data_dir, service_id, phase, observed["metrics"], by=by,
        evidence={"live": observed})
