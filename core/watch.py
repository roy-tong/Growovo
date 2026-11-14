#!/usr/bin/env python3
"""对手事件监控：把对手的公开页面变成带时间戳的套利信号源。

研究 III/IV 的对手事件轴（competitor_events）的执行化：对手的价格页、
status 页、changelog 是公开的、可预期的变化点——涨价/故障/改版发生时，
它的用户变成高意图人群，预制的承接管线把它变成获客战役。

本模块只做三件事：快照对手页面 → 定期 diff → 变化时产出事件并联动
战术注册表（该跑哪个承接战术）。不发送任何东西；承接动作走既有
战术执行与审批路径。

数据落盘：data/watch/<list_id>.json（监控清单与快照）、
data/watch/events.jsonl（变化事件，追加式）。
"""
from __future__ import annotations

import difflib
import json
import os
import hashlib
import tempfile
from html.parser import HTMLParser
from .grow_lock import locked, run_lock
import re
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SCHEMA = "growovo.watch-list/1"

# 监控类型 → 变化时建议的承接战术（战术注册表 id）
KIND_TACTICS = {
    "price": ["price-hike-harvest", "vs-page-matrix", "migration-narrative"],
    "status": ["status-page-arbitrage"],
    "changelog": ["vs-page-matrix", "lost-deal-harvest"],
}

FETCH_TIMEOUT = 15
_USER_AGENT = "Mozilla/5.0 (compatible; GrowovoWatch/1.0)"


class WatchError(RuntimeError):
    pass


def _default_fetch(url: str) -> str:
    """默认抓取器：urllib 只读 GET。测试用注入的假抓取器，不碰网络。"""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        raw = resp.read(2_000_001)
        if len(raw) > 2_000_000:
            raise WatchError("页面超过 2 MB，拒绝无界快照")
        return raw.decode("utf-8", errors="replace")


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def normalize(html: str) -> str:
    """Visible text only: scripts, styling and HTML entities are not events."""
    parser = _VisibleText()
    parser.feed(html or "")
    return _WS_RE.sub(" ", " ".join(parser.parts)).strip()


def _snapshot(raw: str) -> str:
    text = normalize(raw)
    if len(text) < 20 or any(marker in text.lower() for marker in
            ("checking your browser", "verify you are human", "just a moment", "access denied")):
        raise WatchError("空页或访问验证页：保留原快照，等待下一次成功读取")
    return text


def diff_excerpt(before: str, after: str, limit: int = 160) -> str:
    """提取第一处实质差异的前后片段（变化点两侧各带上下文，人可读）。"""
    sm = difflib.SequenceMatcher(a=before, b=after, autojunk=True)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            left = before[max(0, i1 - 30):min(len(before), i2 + 20)].strip()
            right = after[max(0, j1 - 30):min(len(after), j2 + 20)].strip()
            return f"{left!r} → {right!r}"[:limit]
    return ""


class WatchList:
    def __init__(self, path):
        self.path = Path(path)
        self.events_path = self.path.parent / "events.jsonl"

    # ---------- 存取 ----------

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            raise FileNotFoundError(f"没有这个监控清单：{self.path}")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent,
                                         delete=False, encoding="utf-8") as fh:
            temporary = Path(fh.name)
            try:
                fh.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)

    # ---------- 生命周期 ----------

    @classmethod
    def start(cls, path, list_id: str, *, owner: str = "") -> Dict[str, Any]:
        data = {"schema": SCHEMA, "id": list_id, "owner": owner,
                "created": datetime.now().isoformat(timespec="seconds"),
                "entries": [], "history": []}
        with run_lock(path):
            if Path(path).exists():
                raise WatchError("监控清单已存在；不会覆盖基线与历史")
            cls(path).save(data)
        return data

    @locked
    def add(self, url: str, kind: str, competitor: str, *, note: str = "",
            fetcher: Callable[[str], str] = _default_fetch) -> Dict[str, Any]:
        """加一个监控项：立刻抓基线快照（抓不到就拒绝，不收死链）。"""
        if kind not in KIND_TACTICS:
            raise WatchError(f"kind 应为 {'/'.join(KIND_TACTICS)} 之一: {kind}")
        if not str(url or "").startswith(("http://", "https://", "file://")):
            raise WatchError(f"url 应为 http(s):// 或 file://（本地测试）: {url}")
        data = self.load()
        if any(e["url"] == url for e in data["entries"]):
            raise WatchError(f"{url} 已在监控清单里")
        try:
            snapshot = _snapshot(fetcher(url))
        except Exception as exc:  # noqa: BLE001 抓取失败原样上报
            raise WatchError(f"基线快照抓取失败，不收录：{type(exc).__name__}: {exc}") from exc
        if len(snapshot) < 20:
            raise WatchError(f"页面内容过少（签名 {len(snapshot)} 字符），疑似反爬或空页，不收录")
        entry = {
            "url": url, "kind": kind, "competitor": competitor, "note": note,
            "added_at": datetime.now().isoformat(timespec="seconds"),
            "baseline_len": len(snapshot),
            "last_snapshot": snapshot,
            "last_checked": None, "last_change": None, "changes": 0,
        }
        data["entries"].append(entry)
        self.save(data)
        return {k: entry[k] for k in ("url", "kind", "competitor",
                                      "baseline_len", "changes")}

    @locked
    def remove(self, url: str) -> Dict[str, Any]:
        data = self.load()
        entry = next((e for e in data["entries"] if e["url"] == url), None)
        if entry is None:
            raise WatchError(f"清单里没有 {url}")
        data["entries"] = [e for e in data["entries"] if e["url"] != url]
        self.save(data)
        return entry

    # ---------- 核心循环 ----------

    @locked
    def check(self, *, fetcher: Callable[[str], str] = _default_fetch,
              log: Callable[..., Any] = print) -> Dict[str, Any]:
        """逐项抓取并与上次快照 diff；变化即写事件并联动承接战术。

        抓取失败的项跳过不误报（下次再查）；变化判定以文本签名为准。
        """
        data = self.load()
        events: List[Dict[str, Any]] = []
        failed = 0
        now = datetime.now().isoformat(timespec="seconds")
        for entry in data["entries"]:
            try:
                current = _snapshot(fetcher(entry["url"]))
            except Exception as exc:  # noqa: BLE001 网络失败不等于页面变化
                failed += 1
                log(f"[watch] {entry['competitor']} {entry['kind']} 抓取失败："
                    f"{type(exc).__name__}: {str(exc)[:80]}", "WARN")
                continue
            entry["last_checked"] = now
            if current == entry["last_snapshot"]:
                continue
            excerpt = diff_excerpt(entry["last_snapshot"], current)
            event = {
                "id": hashlib.sha256(json.dumps([data["id"], entry["url"],
                    entry["last_snapshot"], current, now[:10]], ensure_ascii=False).encode()).hexdigest(),
                "event_type": "page_content_changed", "verification": "needs_review",
                "on": now[:10], "ts": now,
                "list": data["id"],
                "competitor": entry["competitor"], "kind": entry["kind"],
                "url": entry["url"],
                "before_len": len(entry["last_snapshot"]),
                "after_len": len(current),
                "diff": excerpt,
                "tactics": list(KIND_TACTICS[entry["kind"]]),
            }
            entry["last_snapshot"] = current
            entry["last_change"] = now
            entry["changes"] += 1
            with run_lock(self.events_path):
                known = {row.get("id") for row in self.events(limit=10 ** 6)}
                if event["id"] not in known:
                    with self.events_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                        fh.flush()
                        os.fsync(fh.fileno())
                    events.append(event)
        self.save(data)
        return {"checked": len(data["entries"]) - failed, "failed": failed, "events": events}

    # ---------- 读数 ----------

    def events(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.events_path.is_file():
            return []
        rows = [json.loads(line) for line in
                self.events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        rows = [r for r in rows if r.get("list") == self.load()["id"]]
        return list(reversed(rows[-limit:])) if limit > 0 else []

    def summary(self) -> Dict[str, Any]:
        data = self.load()
        by_kind: Dict[str, int] = {}
        for e in data["entries"]:
            by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        return {"list": data["id"], "entries": len(data["entries"]),
                "by_kind": by_kind,
                "changed": sum(1 for e in data["entries"] if e["changes"]),
                "events_total": len(self.events(limit=10 ** 6))}
