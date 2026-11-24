from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import urllib.request
from pathlib import Path


def _now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _text(path):
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _paragraphs(readme):
    return [row.strip() for row in readme.split("\n\n") if row.strip()]


def inspect_repository(repo_path, slug=""):
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise ValueError(f"仓库目录不存在：{root}")
    readme_path = root / "README.md"
    readme = _text(readme_path)
    if not readme:
        raise ValueError(f"仓库缺少可读 README.md：{readme_path}")
    paragraphs = _paragraphs(readme)
    manifest = _text(root / "pyproject.toml")
    manifest_name = re.search(r"(?m)^name\s*=\s*['\"]([^'\"]+)['\"]", manifest)
    heading = (manifest_name.group(1) if manifest_name else
               next((row[2:].strip() for row in paragraphs if row.startswith("# ")), root.name))
    description = next((row for row in paragraphs if not row.startswith("#") and not row.startswith("<")
                        and len(row) > 20), "")
    quickstart = "mkt.py demo" in readme or "askovo demo" in readme or "verovo review --sample" in readme
    agent_contract = (root / "AGENTS.md").is_file() and (root / "llms.txt").is_file()
    activation = any(token in readme for token in ("activation", "激活", "activated_users")
                     or (root / "docs" / "delivery-contract.json").is_file())
    metrics = {"status": "UNKNOWN", "stars": None, "forks": None, "open_issues": None,
               "slug": slug, "reason": "not_fetched"}
    readiness = {
        "quickstart": {"passed": quickstart, "evidence": "README.md#demo" if quickstart else ""},
        "agent_contract": {"passed": agent_contract,
                           "evidence": "AGENTS.md + llms.txt" if agent_contract else ""},
        "activation_events": {"passed": activation,
                              "evidence": "README/delivery contract" if activation else ""},
    }
    if not quickstart:
        first = "build_reproducible_demo"
    elif not activation:
        first = "instrument_activation_events"
    else:
        first = "find_first_users"
    return {
        "schema": "growovo.repository-intake/1",
        "product": {
            "name": heading,
            "url": ("https://github.com/" + slug) if slug else "",
            "what": description,
        },
        "repository_path": str(root),
        "evidence": {
            "readme_path": str(readme_path),
            "readme_sha256": hashlib.sha256(readme_path.read_bytes()).hexdigest(),
            "inspected_at": _now(),
        },
        "readiness": readiness,
        "recommended_first_action": first,
        "metrics": metrics,
    }


def fetch_repo_metadata(slug):
    url = "https://api.github.com/repos/" + str(slug).strip("/")
    request = urllib.request.Request(url, headers={"User-Agent": "growovo-intake"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
        payload = json.loads(raw.decode("utf-8"))
        return {
            "status": "OBSERVED", "slug": slug,
            "stars": payload.get("stargazers_count"),
            "forks": payload.get("forks_count"),
            "open_issues": payload.get("open_issues_count"),
            "source": url,
            "evidence_sha256": hashlib.sha256(raw).hexdigest(),
            "fetched_at": _now(),
        }
    except Exception as exc:
        return {"status": "UNKNOWN", "slug": slug, "stars": None, "forks": None,
                "open_issues": None, "source": url, "reason": str(exc)[:200]}


def build_goal(report, *, execution_intent="live_with_manual_fallback", human_minutes=120):
    now = dt.datetime.now().astimezone()
    end = now + dt.timedelta(days=7)
    return {
        "schema": "growovo.growth-goal/1",
        "mode": "autopilot",
        "product": dict(report["product"]),
        "window": {"start": now.isoformat(timespec="seconds"),
                   "end": end.isoformat(timespec="seconds")},
        "outcomes": [
            {"id": "activation", "role": "primary", "metric": "activated_users", "target": 1},
            {"id": "stars", "role": "secondary", "metric": "github_stars", "target_delta": 3},
            {"id": "followers", "role": "secondary", "metric": "x_followers", "target_delta": 3},
        ],
        "execution_intent": execution_intent,
        "on_channel_blocked": "manual_handoff",
        "constraints": {"human_minutes": int(human_minutes), "channels": ["x", "github"],
                        "no_paid_procurement": True},
        "audience": {"id": "repository-evaluators", "role": "Developers evaluating the repository",
                     "trigger": "Needs a reproducible first result before trusting the product"},
        "assets": [{"id": "repository-demo", "kind": "reproducible_demo",
                    "url": report["product"].get("url") or "", "cta": "run_demo",
                    "reproduced": False, "sha256": ""}],
        "intake": {
            "repository_path": report["repository_path"],
            "evidence": report["evidence"],
            "readiness": report["readiness"],
            "recommended_first_action": report["recommended_first_action"],
            "metrics_status": report["metrics"]["status"],
            "metrics": report["metrics"],
        },
    }
