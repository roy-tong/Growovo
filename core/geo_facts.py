"""Compare the GEO entity facts with local README, package and agent-readable assets."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import geo_entity
from .geo_entity import GeoError


SCHEMA = "growovo.geo-facts/1"
TEXT_ASSETS = ("README.md", "README.en.md", "README.zh-CN.md", "llms.txt",
               "AGENTS.md", "mcp_server.py", "pyproject.toml")
URL_RE = re.compile(r"https?://[^\s)\"'>]+")


def _normalize(text):
    return " ".join(re.findall(r"[a-z0-9\u4e00-\u9fff]+", text.lower()))


def _tokens(text):
    return set(_normalize(text).split())


def _overlap(left, right):
    if not left or not right:
        return 0.0
    return len(left & right) / float(len(left | right))


def _containment(left, right):
    """How much of `left` appears in `right`; document length must not dilute it."""
    if not left:
        return 0.0
    return len(left & right) / float(len(left))


def _repo_key(url):
    text = str(url or "").strip().rstrip("/")
    if text.lower().endswith(".git"):
        text = text[:-4]
    return text.lower()


def _has_cjk(text):
    return any("一" <= char <= "鿿" for char in text)


def _check(asset, check, status, detail, evidence=None):
    return {"asset": asset, "check": check, "status": status,
            "detail": detail, "evidence": evidence or {}}


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GeoError("资产不可读：%s（%s）" % (path, exc), "GEO_FACTS_UNREADABLE") from exc


def _urls(text):
    return {value.rstrip(".,;") for value in URL_RE.findall(text)}


def check(data_dir, repo, *, site=None):
    entity = geo_entity.load(data_dir)
    root = Path(repo).expanduser().resolve()
    if not root.is_dir():
        raise GeoError("repo 不是目录：%s" % root, "GEO_FACTS_REPO")
    canonical = entity.get("canonical") or {}
    name = str(canonical.get("name") or "")
    one_line = str(canonical.get("one_line") or "")
    urls = canonical.get("urls") or {}
    repo_url = str(urls.get("repo") or "")
    homepage = str(urls.get("homepage") or "")
    facts = [row for row in entity.get("fact_sources") or [] if row.get("url")]
    checks = []
    for asset in TEXT_ASSETS:
        path = root / asset
        if not path.is_file():
            checks.append(_check(asset, "exists", "not_applicable", "文件不存在"))
            continue
        text = _read(path)
        checks.append(_check(asset, "exists", "pass", "文件存在"))
        lowered = text.lower()
        if asset.startswith("README") or asset == "llms.txt":
            if name and name.lower() not in lowered:
                checks.append(_check(asset, "name", "warn", "未出现事实表名称 %s" % name))
            else:
                checks.append(_check(asset, "name", "pass", "出现事实表名称"))
            if repo_url and repo_url not in text:
                checks.append(_check(asset, "repo_url", "warn", "未出现事实表仓库 URL"))
            else:
                checks.append(_check(asset, "repo_url", "pass", "出现仓库 URL"))
            if homepage and homepage not in text:
                checks.append(_check(asset, "homepage", "warn", "未出现事实表主页 URL"))
            else:
                checks.append(_check(asset, "homepage", "pass", "出现主页 URL"))
            if one_line and ".zh-cn." in asset.lower() and not _has_cjk(one_line):
                checks.append(_check(asset, "one_line", "not_applicable",
                                     "中文字档与英文一句话描述语言不同，跳过重合度比对"))
            else:
                overlap = _containment(_tokens(one_line), _tokens(text[:20000]))
                if one_line and overlap < 0.5:
                    checks.append(_check(asset, "one_line", "warn",
                                         "一句话描述在文档中的词覆盖度较低（%.2f）" % overlap,
                                         {"overlap": round(overlap, 3), "expected": one_line}))
                else:
                    checks.append(_check(asset, "one_line", "pass",
                                         "一句话描述在文档中的词覆盖度正常（%.2f）" % overlap,
                                         {"overlap": round(overlap, 3)}))
        if asset in ("README.md", "README.en.md", "README.zh-CN.md", "llms.txt"):
            base_repo = _repo_key(repo_url)
            foreign = sorted(url for url in _urls(text)
                             if "github.com" in url and base_repo
                             and _repo_key(url) != base_repo
                             and not _repo_key(url).startswith(base_repo + "/"))
            if foreign:
                checks.append(_check(asset, "foreign_repo", "warn",
                                     "出现其他 GitHub 仓库 URL", {"urls": foreign[:5]}))
            else:
                checks.append(_check(asset, "foreign_repo", "pass", "未出现其他 GitHub 仓库 URL"))
        if asset == "pyproject.toml":
            match = re.search(r"(?ms)^\[project\].*?^name\s*=\s*\"([^\"]+)\"", text)
            if match and match.group(1).strip().lower() not in (
                    name.lower(), str(entity.get("entity_id") or "").lower()):
                checks.append(_check(asset, "package_name", "warn",
                                     "包名 %s 与事实表不一致" % match.group(1)))
            else:
                checks.append(_check(asset, "package_name", "pass", "包名一致"))
            desc = re.search(r"(?ms)^\[project\].*?^description\s*=\s*\"([^\"]+)\"", text)
            if desc:
                overlap = _overlap(_tokens(one_line), _tokens(desc.group(1)))
                status = "pass" if overlap >= 0.25 else "warn"
                checks.append(_check(asset, "package_description", status,
                                     "包描述与事实表重合度 %.2f" % overlap,
                                     {"overlap": round(overlap, 3)}))
    if site:
        checks.append(_check("site", "checked", "unknown",
                             "站点由 mkt seo audit 检查；此处不重复抓取", {"site": site}))
    summary = {status: 0 for status in ("pass", "warn", "fail", "unknown", "not_applicable")}
    for row in checks:
        summary[row["status"]] += 1
    return {
        "schema": SCHEMA,
        "repo": str(root),
        "entity_version": int(entity["version"]),
        "fact_urls": [row["url"] for row in facts],
        "checks": checks,
        "summary": summary,
    }


def render(result):
    lines = [
        "# 事实一致性检查 · %s" % result.get("repo", ""),
        "",
        "| 资产 | 检查 | 状态 | 说明 |",
        "| --- | --- | --- | --- |",
    ]
    for row in result.get("checks") or []:
        lines.append("| %s | %s | %s | %s |" % (
            row["asset"], row["check"], row["status"],
            str(row.get("detail", "")).replace("|", "\\|")))
    lines.extend(["", "## 汇总", ""])
    for status, count in (result.get("summary") or {}).items():
        lines.append("- %s: %d" % (status, count))
    lines.append("")
    return "\n".join(lines)
