"""Agent-readiness check: consistent facts, machine-readable entry points and observed calls."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from . import geo_entity, geo_facts
from .geo_entity import GeoError


SCHEMA = "growovo.geo-agent-check/1"
BOTS = ("GPTBot", "ClaudeBot", "PerplexityBot", "Googlebot", "Bingbot", "CCBot")
TOOL_RE = re.compile(r'"name"\s*:\s*"(mkt_[a-z0-9_]+)"')


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _rows(path):
    if not path:
        return []
    file_path = Path(path)
    if not file_path.is_file():
        raise GeoError("日志文件不存在：%s" % file_path, "GEO_AGENT_LOG")
    text = file_path.read_text(encoding="utf-8")
    if file_path.suffix.lower() == ".csv":
        return [dict(row) for row in csv.DictReader(text.splitlines())]
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _crawler_hits(path):
    if not path:
        return {"status": "not_run", "reason": "no_log", "lower_bound": True,
                "total": 0, "by_bot": {}}
    rows = _rows(path)
    by_bot = {}
    for row in rows:
        ua = str(row.get("user_agent") or row.get("ua") or row.get("agent") or "")
        matched = next((bot for bot in BOTS if bot.lower() in ua.lower()), "")
        if matched:
            by_bot[matched] = by_bot.get(matched, 0) + 1
    return {"status": "observed", "source": str(path), "lower_bound": True,
            "total": sum(by_bot.values()), "by_bot": by_bot,
            "note": "UA 可伪造；这是下限，不是平台确认的抓取量。"}


def _usage(path):
    if not path:
        return {"status": "not_run", "reason": "no_usage_log", "calls": 0,
                "success": 0, "success_rate": None}
    rows = _rows(path)
    calls = len(rows)
    success = sum(1 for row in rows if row.get("success") is True)
    return {"status": "observed", "source": str(path), "calls": calls,
            "success": success,
            "success_rate": (success / float(calls)) if calls else None}


def check(data_dir, repo, *, logs="", usage=""):
    entity = geo_entity.load(data_dir)
    root = Path(repo).expanduser().resolve()
    if not root.is_dir():
        raise GeoError("repo 不是目录：%s" % root, "GEO_AGENT_REPO")
    facts = geo_facts.check(data_dir, str(root))
    canonical = entity.get("canonical") or {}
    name = str(canonical.get("name") or "")
    urls = canonical.get("urls") or {}
    assets = []
    for asset in ("llms.txt", "mcp_server.py", "AGENTS.md", "agent-tools.json"):
        path = root / asset
        if not path.is_file():
            assets.append({"path": asset, "exists": False, "status": "not_applicable"})
            continue
        text = _read_text(path)
        row = {"path": asset, "exists": True,
               "name_present": bool(name and name.lower() in text.lower()),
               "repo_url_present": bool(urls.get("repo") and urls["repo"] in text),
               "homepage_present": bool(urls.get("homepage") and urls["homepage"] in text)}
        if asset == "mcp_server.py":
            row["tools"] = sorted(set(TOOL_RE.findall(text)))
        assets.append(row)
    summary = {"facts": facts.get("summary") or {},
               "assets": {"present": sum(1 for row in assets if row.get("exists")),
                          "total": len(assets)},
               "crawler": _crawler_hits(logs),
               "usage": _usage(usage)}
    return {
        "schema": SCHEMA,
        "repo": str(root),
        "entity_version": int(entity["version"]),
        "assets": assets,
        "facts": facts,
        "summary": summary,
        "boundary": ("爬虫 UA 与调用日志都是下限；被爬不等于被引用，被调用不等于业务结果。"),
    }
