#!/usr/bin/env python3
"""对手事件监控：把对手的公开页面变成带时间戳的套利信号源。

研究 III/IV 的对手事件轴（competitor_events）的执行化：对手的价格页、
status 页、changelog 是公开的、可预期的变化点——涨价/故障/改版发生时，
它的用户变成高意图人群，预制的承接管线把它变成获客战役。

本模块只做三件事：快照对手页面 → 定期 diff → 变化时产出事件并联动
战术注册表（该跑哪个承接战术）。不发送任何东西；承接动作走既有
战术执行与审批路径。

G-06 起，抓取前后都先过 ``core/watch_safety.py``：

- 只收 http/https；file:// 只在「显式本地模式 + 注入本地抓取器」时可用
  （测试/离线演示），生产默认路径一律拒绝。
- 每一跳重定向都重新做地址判定；localhost/内网/链路本地/IPv6 回环等
  在 DNS 解析之后也会被拒绝。
- 403/验证码/登录墙/空壳页不改基线；脚本/时间戳/随机 token 不算竞争事件。
- 整页 diff 在人工核实前只记 ``page_content_changed``（needs_review）；
  价格只做结构化 token 提示，人工确认写入追加式审计，不改原事件。

数据落盘：data/watch/<list_id>.json（监控清单与快照）、
data/watch/events.jsonl（变化事件，追加式）、
data/watch/event_verifications.jsonl（人工核实记录，追加式）。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import tempfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import re

from . import watch_safety as safety
from .grow_lock import locked, run_lock
from .ledger import _append_line

SCHEMA = "growovo.watch-list/1"

# 监控类型 → 变化时建议的承接战术（战术注册表 id）；只是候选，需人工核实。
KIND_TACTICS = {
    "price": ["price-hike-harvest", "vs-page-matrix", "migration-narrative"],
    "status": ["status-page-arbitrage"],
    "changelog": ["vs-page-matrix", "lost-deal-harvest"],
}

#: 人工核实可以声明的结论；未核实前事件只是 page_content_changed。
CONFIRM_CLAIMS = ("price_change", "status_incident", "changelog_update",
                  "content_only", "rejected")

FETCH_TIMEOUT = safety.FETCH_TIMEOUT
MAX_BYTES = safety.MAX_BYTES


class WatchError(RuntimeError):
    pass


def _default_fetch(url: str) -> str:
    """兼容入口：生产抓取（手工重定向 + 地址判定 + 响应边界）。"""
    return safety.fetch_page(url)["text"]


def local_file_fetcher(url: str) -> str:
    """显式本地模式专用：只读本机文件，不联网。"""
    info = safety.check_url(url, allow_local_file=True)
    return Path(info["path"]).read_text(encoding="utf-8", errors="replace")


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
    marker = safety.looks_like_challenge(text)
    if marker:
        raise WatchError(
            f"访问验证页（{marker}）：保留原快照，等待下一次成功读取")
    if len(text) < 20:
        raise WatchError("空页或内容过少：保留原快照，等待下一次成功读取")
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


def _structured(entry: Dict[str, Any], before: str,
                after: str) -> Dict[str, Any]:
    """结构化价格 token：只做提示，不替人下「涨价」结论。"""
    before_prices = safety.extract_price_tokens(before)
    after_prices = safety.extract_price_tokens(after)
    hint = None
    if entry.get("kind") == "price" and before_prices != after_prices:
        hint = "price_tokens_changed"
    return {"price_tokens_before": before_prices,
            "price_tokens_after": after_prices,
            "hint": hint,
            "noise_filtered": True}


class WatchList:
    def __init__(self, path):
        self.path = Path(path)
        self.events_path = self.path.parent / "events.jsonl"
        self.verifications_path = self.path.parent / "event_verifications.jsonl"

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

    def _snapshot_url(self, url: str, *, fetcher=None, transport=None,
                      resolver=None, allow_local_file: bool = False,
                      file_fetcher=None) -> Dict[str, Any]:
        """抓一次快照；所有 URL 先过地址策略，再决定用哪条传输路径。"""
        try:
            info = safety.check_url(url, resolver=resolver,
                                    allow_local_file=allow_local_file)
            if info["scheme"] == "file":
                if not allow_local_file:
                    raise safety.UrlPolicyError(
                        "FILE_DISABLED", "file:// 只在显式本地模式可用")
                local = fetcher or file_fetcher
                if local is None:
                    raise safety.UrlPolicyError(
                        "FILE_DISABLED",
                        "file:// 只在显式本地模式 + 注入本地抓取器时可用（测试/离线演示）")
                return {"text": _snapshot(local(url)), "final_url": url,
                        "redirects": [], "addresses": info["addresses"]}
            if fetcher is not None and transport is None:
                # 兼容旧调用：单跳注入抓取器；静态地址规则已经过，DNS 由注入方负责。
                return {"text": _snapshot(fetcher(url)), "final_url": url,
                        "redirects": [], "addresses": info["addresses"]}
            page = safety.fetch_page(url, transport=transport,
                                     resolver=resolver,
                                     allow_local_file=allow_local_file)
            return {"text": _snapshot(page["text"]),
                    "final_url": page["final_url"],
                    "redirects": page["redirects"],
                    "addresses": info["addresses"]}
        except safety.UrlPolicyError as exc:
            raise WatchError(f"URL 拒绝（{exc.code}）：{exc}") from exc

    @locked
    def add(self, url: str, kind: str, competitor: str, *, note: str = "",
            fetcher: Optional[Callable[[str], str]] = None,
            transport: Optional[Callable[[str], Dict[str, Any]]] = None,
            resolver=None, allow_local_file: bool = False,
            file_fetcher: Optional[Callable[[str], str]] = None
            ) -> Dict[str, Any]:
        """加一个监控项：立刻抓基线快照（抓不到就拒绝，不收死链）。"""
        if kind not in KIND_TACTICS:
            raise WatchError(f"kind 应为 {'/'.join(KIND_TACTICS)} 之一: {kind}")
        data = self.load()
        if any(e["url"] == url for e in data["entries"]):
            raise WatchError(f"{url} 已在监控清单里")
        try:
            snapshot = self._snapshot_url(
                url, fetcher=fetcher, transport=transport, resolver=resolver,
                allow_local_file=allow_local_file,
                file_fetcher=file_fetcher)
        except WatchError:
            raise
        except Exception as exc:  # noqa: BLE001 抓取失败原样上报
            raise WatchError(
                f"基线快照抓取失败，不收录：{type(exc).__name__}: {exc}") from exc
        text = snapshot["text"]
        if len(text) < 20:
            raise WatchError(
                f"页面内容过少（签名 {len(text)} 字符），疑似反爬或空页，不收录")
        entry = {
            "url": url, "kind": kind, "competitor": competitor, "note": note,
            "added_at": datetime.now().isoformat(timespec="seconds"),
            "baseline_len": len(text),
            "last_snapshot": text,
            "last_final_url": snapshot["final_url"],
            "last_checked": None, "last_change": None, "changes": 0,
        }
        data["entries"].append(entry)
        self.save(data)
        return {k: entry[k] for k in ("url", "kind", "competitor",
                                      "baseline_len", "changes",
                                      "last_final_url")}

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
    def check(self, *, fetcher: Optional[Callable[[str], str]] = None,
              transport: Optional[Callable[[str], Dict[str, Any]]] = None,
              resolver=None, allow_local_file: bool = False,
              file_fetcher: Optional[Callable[[str], str]] = None,
              log: Callable[..., Any] = print) -> Dict[str, Any]:
        """逐项抓取并与上次快照 diff；变化即写事件并联动承接战术。

        抓取失败/403/验证页跳过且**不改基线**（下次再查）；只有实质文本
        变化才产出事件。噪声（时间戳/随机 token）会更新基线但不产事件。
        """
        data = self.load()
        events: List[Dict[str, Any]] = []
        failed = 0
        now = datetime.now().isoformat(timespec="seconds")
        for entry in data["entries"]:
            try:
                snapshot = self._snapshot_url(
                    entry["url"], fetcher=fetcher, transport=transport,
                    resolver=resolver, allow_local_file=allow_local_file,
                    file_fetcher=file_fetcher)
            except Exception as exc:  # noqa: BLE001 网络失败/验证页 ≠ 页面变化
                failed += 1
                entry["last_checked"] = now
                log(f"[watch] {entry['competitor']} {entry['kind']} 抓取失败："
                    f"{type(exc).__name__}: {str(exc)[:80]}", "WARN")
                continue
            current = snapshot["text"]
            entry["last_checked"] = now
            entry["last_final_url"] = snapshot["final_url"]
            previous = entry["last_snapshot"]
            if current == previous:
                continue
            if not safety.is_significant_change(previous, current):
                # 只有时间戳/随机 token 变了：更新基线，不算竞争事件。
                entry["last_snapshot"] = current
                continue
            excerpt = diff_excerpt(previous, current)
            event = {
                "id": hashlib.sha256(json.dumps(
                    [data["id"], entry["url"], previous, current, now[:10]],
                    ensure_ascii=False).encode()).hexdigest(),
                "event_type": "page_content_changed",
                "verification": "needs_review",
                "claim": "content_changed",
                "claim_status": "unverified",
                "on": now[:10], "ts": now,
                "list": data["id"],
                "competitor": entry["competitor"], "kind": entry["kind"],
                "url": entry["url"],
                "final_url": snapshot["final_url"],
                "redirects": list(snapshot["redirects"]),
                "before_len": len(previous),
                "after_len": len(current),
                "diff": excerpt,
                "structured": _structured(entry, previous, current),
                "tactics": list(KIND_TACTICS[entry["kind"]]),
                "tactics_status": "candidate_after_review",
            }
            entry["last_snapshot"] = current
            entry["last_change"] = now
            entry["changes"] += 1
            with run_lock(self.events_path):
                known = {row.get("id") for row in self.events(limit=10 ** 6)}
                if event["id"] not in known:
                    _append_line(self.events_path,
                                 json.dumps(event, ensure_ascii=False) + "\n")
                    events.append(event)
        self.save(data)
        return {"checked": len(data["entries"]) - failed, "failed": failed,
                "events": events}

    # ---------- 人工核实（追加式审计，不改原事件） ----------

    def _raw_events(self) -> List[Dict[str, Any]]:
        if not self.events_path.is_file():
            return []
        rows = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def _verifications(self) -> Dict[str, Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if self.verifications_path.is_file():
            for line in self.verifications_path.read_text(
                    encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        latest: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if row.get("event_id"):
                latest[str(row["event_id"])] = row
        return latest

    def confirm_event(self, event_id: str, *, claim: str, by: str,
                      note: str) -> Dict[str, Any]:
        """人工核实一条事件：只追加审计，不修改原事件与原始快照。"""
        event_id = str(event_id or "").strip()
        claim = str(claim or "").strip()
        by = str(by or "").strip()
        note = str(note or "").strip()
        if claim not in CONFIRM_CLAIMS:
            raise WatchError(f"claim 应为 {'/'.join(CONFIRM_CLAIMS)} 之一：{claim!r}")
        if not by:
            raise WatchError("人工核实必须署名（--by）：机器不能替人下结论")
        if not note:
            raise WatchError("人工核实必须写依据（--note，如截图/链接）")
        data = self.load()
        hit = next((row for row in self._raw_events()
                    if row.get("id") == event_id
                    and row.get("list") == data["id"]), None)
        if hit is None:
            raise WatchError(f"清单 {data['id']} 里没有事件 {event_id}")
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "list": data["id"],
            "event_id": event_id,
            "claim": claim,
            "by": by,
            "note": note[:500],
            "event_on": hit.get("on"),
            "url": hit.get("url"),
        }
        _append_line(self.verifications_path,
                     json.dumps(record, ensure_ascii=False) + "\n")
        return record

    # ---------- 读数 ----------

    def events(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = [r for r in self._raw_events() if r.get("list") == self.load()["id"]]
        verifications = self._verifications()
        views = []
        for row in rows:
            view = dict(row)
            record = verifications.get(str(row.get("id")))
            if record:
                view["verification"] = ("rejected"
                                        if record.get("claim") == "rejected"
                                        else "confirmed")
                view["claim"] = record.get("claim")
                view["verified_by"] = record.get("by")
                view["verified_at"] = record.get("ts")
                view["verify_note"] = record.get("note")
            views.append(view)
        return list(reversed(views[-limit:])) if limit > 0 else []

    def summary(self) -> Dict[str, Any]:
        data = self.load()
        by_kind: Dict[str, int] = {}
        for e in data["entries"]:
            by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        views = self.events(limit=10 ** 6)
        return {"list": data["id"], "entries": len(data["entries"]),
                "by_kind": by_kind,
                "changed": sum(1 for e in data["entries"] if e["changes"]),
                "events_total": len(views),
                "confirmed": sum(1 for e in views
                                 if e.get("verification") == "confirmed"),
                "needs_review": sum(1 for e in views
                                    if e.get("verification") == "needs_review"),
                "rejected": sum(1 for e in views
                                if e.get("verification") == "rejected")}
