"""Bounded, read-only technical SEO audit using the Python standard library."""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
import urllib.robotparser
import uuid
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .storage import AtomicJSONStore


SCHEMA = "growovo.seo-audit/1"
USER_AGENT = "GrowovoSEOAudit/0.1 (+https://github.com/roy-tong/Growovo)"
AI_BOTS = ("GPTBot", "ClaudeBot", "PerplexityBot", "Googlebot")
CITY_WORDS = (
    "beijing", "shanghai", "shenzhen", "guangzhou", "hangzhou", "chengdu",
    "suzhou", "chongqing", "nanjing", "wuhan", "xian", "tianjin",
    "北京", "上海", "深圳", "广州", "杭州", "成都", "苏州", "重庆", "南京", "武汉", "西安", "天津",
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def audit_path(data_dir, audit_id: str) -> Path:
    return Path(data_dir) / "geo" / "audits" / (audit_id + ".json")


def _check(check_id, status, detail, evidence=None):
    return {"id": check_id, "status": status, "detail": detail,
            "evidence": evidence or {}}


def _fetch(url, *, timeout=10, max_bytes=2_000_000):
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "final_url": response.geturl(),
                "content_type": response.headers.get_content_type(),
                "charset": response.headers.get_content_charset() or "utf-8",
                "body": body,
                "truncated": len(body) > max_bytes,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(max_bytes + 1)
        return {"ok": False, "status": int(exc.code), "final_url": url,
                "content_type": exc.headers.get_content_type() if exc.headers else "",
                "charset": "utf-8", "body": body, "truncated": len(body) > max_bytes,
                "elapsed_ms": int((time.monotonic() - started) * 1000)}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "status": None, "final_url": url, "content_type": "",
                "charset": "utf-8", "body": b"", "truncated": False,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "error": str(exc)[:300]}


def _decode(response):
    try:
        return response["body"].decode(response.get("charset") or "utf-8", "replace")
    except (LookupError, UnicodeError):
        return response["body"].decode("utf-8", "replace")


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.canonical = ""
        self.hreflang = []
        self.h1 = []
        self.jsonld = []
        self.links = []
        self.text = []
        self._in_title = False
        self._in_h1 = False
        self._in_script = False
        self._script_type = ""
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._in_h1 = True
            self._buffer = []
        elif tag == "meta" and attrs.get("name", "").lower() == "description":
            self.description = attrs.get("content", "") or ""
        elif tag == "link":
            rel = (attrs.get("rel") or "").lower()
            if "canonical" in rel:
                self.canonical = attrs.get("href", "") or ""
            if "alternate" in rel and attrs.get("hreflang"):
                self.hreflang.append({"hreflang": attrs.get("hreflang"),
                                      "href": attrs.get("href", "")})
        elif tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        elif tag == "script" and "ld+json" in (attrs.get("type") or "").lower():
            self._in_script = True
            self._script_type = "ld+json"
            self._buffer = []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "h1":
            self._in_h1 = False
            self.h1.append(" ".join("".join(self._buffer).split()))
        elif tag == "script" and self._in_script:
            self._in_script = False
            if self._script_type == "ld+json":
                self.jsonld.append("".join(self._buffer))
            self._buffer = []

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_h1:
            self._buffer.append(data)
        elif self._in_script:
            self._buffer.append(data)
        else:
            self.text.append(data)


def _parse_html(text):
    parser = _PageParser()
    parser.feed(text)
    return parser


def _normalize(url):
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return "%s://%s%s" % (parsed.scheme, parsed.netloc, path)


def _robots(base, timeout):
    response = _fetch(urljoin(base, "/robots.txt"), timeout=timeout)
    if response.get("status") == 404:
        return {"present": False, "status": 404, "text": "", "error": ""}
    if not response.get("ok"):
        return {"present": False, "status": response.get("status"),
                "text": "", "error": response.get("error") or "robots.txt 不可读"}
    return {"present": True, "status": response.get("status"),
            "text": _decode(response), "error": ""}


def _robots_allows(text, url):
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(text.splitlines())
    result = {}
    for agent in (USER_AGENT,) + AI_BOTS:
        try:
            result[agent] = parser.can_fetch(agent, url)
        except Exception:
            result[agent] = None
    return result


def _sitemap(base, timeout, limit=20):
    response = _fetch(urljoin(base, "/sitemap.xml"), timeout=timeout)
    if not response.get("ok"):
        return {"present": False, "urls": [], "error": response.get("error") or
                "sitemap 返回 %s" % response.get("status")}
    text = _decode(response)
    urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text, flags=re.I)
    return {"present": True, "urls": urls[:limit], "error": ""}


def _shingles(text):
    tokens = re.findall(r"[a-z0-9\u4e00-\u9fff]+", text.lower())
    if len(tokens) < 3:
        return set()
    return {" ".join(tokens[index:index + 3]) for index in range(len(tokens) - 2)}


def _jaccard(left, right):
    if not left or not right:
        return 0.0
    return len(left & right) / float(len(left | right))


def _sample_pages(base, sitemap, timeout, sample):
    seen = {_normalize(base)}
    pages = [base]
    for url in sitemap.get("urls") or []:
        if len(pages) >= sample:
            break
        absolute = urljoin(base, url)
        if _normalize(absolute) in seen:
            continue
        seen.add(_normalize(absolute))
        pages.append(absolute)
    results = []
    for url in pages:
        response = _fetch(url, timeout=timeout)
        if not response.get("ok") or "html" not in (response.get("content_type") or ""):
            continue
        parser = _parse_html(_decode(response))
        text = " ".join(parser.text)
        results.append({"url": url, "text": text, "shingles": _shingles(text)})
    return results


def audit(url, *, entity=None, sample=10, timeout=10):
    if not str(url).startswith(("http://", "https://")):
        raise ValueError("url 必须是 http(s) URL")
    audit_id = "seo-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    response = _fetch(url, timeout=timeout)
    checks = []
    if not response.get("ok") or "html" not in (response.get("content_type") or ""):
        checks.append(_check("fetch", "fail",
                             "页面不可访问或不是 HTML：%s" % (
                                 response.get("error") or response.get("status")),
                             {"status": response.get("status"),
                              "content_type": response.get("content_type")}))
        for check_id in ("robots", "sitemap", "canonical", "hreflang", "title",
                         "description", "h1", "structured_data", "llms_txt",
                         "duplicate", "doorway", "index_status"):
            checks.append(_check(check_id, "unknown", "页面不可访问，无法检查"))
        return _result(audit_id, url, response.get("final_url") or url, checks)

    parser = _parse_html(_decode(response))
    base = response.get("final_url") or url
    checks.append(_check("fetch", "pass", "HTTP %s；%s" % (
        response.get("status"), response.get("content_type")),
        {"elapsed_ms": response.get("elapsed_ms"), "truncated": response.get("truncated")}))

    robots = _robots(base, timeout)
    if not robots["present"]:
        checks.append(_check("robots", "warn" if robots["status"] == 404 else "unknown",
                             "robots.txt 不存在或不可读：%s" % (
                                 robots["error"] or robots["status"])))
    else:
        allows = _robots_allows(robots["text"], base)
        blocked = [agent for agent, allowed in allows.items() if allowed is False]
        if blocked:
            checks.append(_check("robots", "fail",
                                 "这些 agent 被 robots.txt 拒绝：%s" % ", ".join(blocked),
                                 {"allows": allows}))
        else:
            checks.append(_check("robots", "pass", "robots.txt 允许审计 UA 与目标 AI 爬虫",
                                 {"allows": allows}))

    sitemap = _sitemap(base, timeout)
    if not sitemap["present"]:
        checks.append(_check("sitemap", "warn", sitemap["error"]))
    else:
        checks.append(_check("sitemap", "pass" if sitemap["urls"] else "warn",
                             "sitemap 含 %d 个 URL" % len(sitemap["urls"]),
                             {"sample": sitemap["urls"][:5]}))

    if not parser.canonical:
        checks.append(_check("canonical", "warn", "未声明 canonical"))
    elif _normalize(urljoin(base, parser.canonical)) != _normalize(base):
        checks.append(_check("canonical", "fail",
                             "canonical 指向别处：%s" % parser.canonical))
    else:
        checks.append(_check("canonical", "pass", "canonical 自指"))

    if not parser.hreflang:
        checks.append(_check("hreflang", "warn", "未声明 hreflang；单语站可忽略"))
    elif any(not str(row.get("href") or "").startswith(("http://", "https://"))
             for row in parser.hreflang):
        checks.append(_check("hreflang", "fail", "hreflang 含非绝对 URL",
                             {"hreflang": parser.hreflang[:10]}))
    else:
        checks.append(_check("hreflang", "pass", "hreflang 使用绝对 URL",
                             {"hreflang": parser.hreflang[:10]}))

    title = " ".join(parser.title.split())
    if not title:
        checks.append(_check("title", "fail", "缺少 title"))
    elif 15 <= len(title) <= 65:
        checks.append(_check("title", "pass", "title 长度 %d" % len(title), {"title": title}))
    else:
        checks.append(_check("title", "warn", "title 长度 %d，建议 15–65" % len(title),
                             {"title": title}))

    description = " ".join(parser.description.split())
    if not description:
        checks.append(_check("description", "warn", "缺少 meta description"))
    elif 50 <= len(description) <= 160:
        checks.append(_check("description", "pass", "description 长度 %d" % len(description)))
    else:
        checks.append(_check("description", "warn",
                             "description 长度 %d，建议 50–160" % len(description)))

    if len(parser.h1) == 1 and parser.h1[0]:
        checks.append(_check("h1", "pass", "恰好一个 H1", {"h1": parser.h1[0]}))
    else:
        checks.append(_check("h1", "warn", "H1 数量为 %d" % len(parser.h1)))

    if not parser.jsonld:
        checks.append(_check("structured_data", "warn", "未发现 JSON-LD"))
    else:
        parsed = []
        invalid = []
        for index, block in enumerate(parser.jsonld):
            try:
                parsed.append(json.loads(block))
            except json.JSONDecodeError as exc:
                invalid.append({"index": index, "error": str(exc)[:120]})
        if invalid:
            checks.append(_check("structured_data", "fail", "JSON-LD 无法解析", {"invalid": invalid}))
        else:
            checks.append(_check("structured_data", "pass",
                                 "发现 %d 个 JSON-LD 块" % len(parsed)))
            if entity:
                canonical = entity.get("canonical") or {}
                names = {str((row.get("name") if isinstance(row, dict) else "") or "")
                         for row in parsed if isinstance(row, dict)}
                names.discard("")
                expected = str(canonical.get("name") or "")
                if expected and names and expected not in names:
                    checks.append(_check("facts_match", "fail",
                                         "JSON-LD name 与事实表不一致：%s" % ", ".join(sorted(names)),
                                         {"expected": expected}))
                elif expected and not names:
                    checks.append(_check("facts_match", "warn", "JSON-LD 未声明 name"))
                else:
                    checks.append(_check("facts_match", "pass", "JSON-LD name 与事实表一致"))

    llms = _fetch(urljoin(base, "/llms.txt"), timeout=timeout)
    if llms.get("ok"):
        checks.append(_check("llms_txt", "pass", "llms.txt 存在；它不是排名杠杆",
                             {"bytes": len(llms.get("body") or b"")}))
    else:
        checks.append(_check("llms_txt", "warn", "llms.txt 不存在；可选，不是排名杠杆"))

    pages = _sample_pages(base, sitemap, timeout, max(1, int(sample)))
    duplicates = []
    for index, left in enumerate(pages):
        for right in pages[index + 1:]:
            similarity = _jaccard(left["shingles"], right["shingles"])
            if similarity >= 0.85:
                duplicates.append({"left": left["url"], "right": right["url"],
                                   "similarity": round(similarity, 3)})
    if duplicates:
        checks.append(_check("duplicate", "warn", "发现近重复页面", {"pairs": duplicates[:10]}))
    else:
        checks.append(_check("duplicate", "pass", "抽样页面未发现近重复"))

    city_pages = []
    for page in pages:
        text = page["text"].lower()
        found = [word for word in CITY_WORDS if word in text]
        if found:
            city_pages.append({"url": page["url"], "cities": found})
    if len(city_pages) >= 3 and duplicates:
        checks.append(_check("doorway", "fail",
                             "多个高相似页面只替换城市词，存在门页风险",
                             {"pages": city_pages[:10]}))
    elif len(city_pages) >= 3:
        checks.append(_check("doorway", "warn",
                             "多个页面含城市词；需确认是否有真实本地业务",
                             {"pages": city_pages[:10]}))
    else:
        checks.append(_check("doorway", "pass", "未发现城市门页特征"))

    checks.append(_check("index_status", "unknown", "GSC 未配置；不猜测索引状态"))
    return _result(audit_id, url, base, checks)


def _result(audit_id, url, final_url, checks):
    summary = {status: 0 for status in ("pass", "warn", "fail", "unknown", "not_applicable")}
    for row in checks:
        summary[row["status"]] += 1
    return {
        "schema": SCHEMA,
        "audit_id": audit_id,
        "url": url,
        "final_url": final_url,
        "checked_at": _now(),
        "user_agent": USER_AGENT,
        "checks": checks,
        "summary": summary,
    }


def save(data_dir, result):
    AtomicJSONStore(audit_path(data_dir, result["audit_id"])).save(result)
    return audit_path(data_dir, result["audit_id"])


def render(result):
    lines = [
        "# 技术 SEO 审计 · %s" % result.get("url", ""),
        "",
        "> 最终 URL：%s；检查时间：%s；UA：%s"
        % (result.get("final_url", ""), result.get("checked_at", ""),
           result.get("user_agent", "")),
        "",
        "| 检查 | 状态 | 说明 |",
        "| --- | --- | --- |",
    ]
    for row in result.get("checks") or []:
        lines.append("| %s | %s | %s |" % (
            row["id"], row["status"], str(row.get("detail", "")).replace("|", "\\|")))
    lines.extend(["", "## 汇总", ""])
    for status, count in (result.get("summary") or {}).items():
        lines.append("- %s: %d" % (status, count))
    lines.extend([
        "",
        "## 边界",
        "",
        "- 只读审计，不自动改站、不自动提交索引。",
        "- `index_status` 在没有 GSC 凭据时保持 unknown；不猜测。",
        "- 近重复与门页是抽样判断；需要人工确认是否有真实本地业务。",
        "",
    ])
    return "\n".join(lines)
