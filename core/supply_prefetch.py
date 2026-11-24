"""波次执行期间的只读供给预取。

GitHub API 与本地内容卡可以和浏览器写动作并行；X 搜索会操作同一个可见
标签页，默认只报告缺口，不在写波次里抢页面。预取失败不影响发送结果。
"""
from __future__ import annotations

import fcntl
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from . import supply_pool
from .ledger import Ledger

LOCK_NAME = "supply-prefetch.lock"
_SOURCE_FOR = {
    "github/reply": "github",
    "x/reply": "x",
    "x/publish": "content",
    "discord/publish": "content",
}


def lock_path(data_dir) -> Path:
    return Path(data_dir) / "state" / LOCK_NAME


def plan_prefetch(data_dir, policy: Dict[str, Any]) -> Dict[str, Any]:
    """按后续波次需要计算预取工作；只读储备，不发起网络。"""
    supply = (policy or {}).get("supply") or {}
    config = supply.get("prefetch") or {}
    enabled = bool(config.get("enabled", False))
    out: Dict[str, Any] = {"ok": True, "enabled": enabled, "jobs": [], "deferred": []}
    if not enabled:
        return out
    sources = {str(x) for x in (config.get("sources") or ["github", "content"])}
    lookahead = max(1, int(config.get("lookahead_waves") or 2))
    payload = supply_pool.load(data_dir)
    candidates: Dict[str, int] = {}
    ready: Dict[str, int] = {}
    for item in (payload.get("items") or {}).values():
        if item.get("status") != "new":
            continue
        key = "%s/%s" % (item.get("channel") or "", item.get("action") or "")
        candidates[key] = candidates.get(key, 0) + 1
        if str(item.get("text") or "").strip():
            ready[key] = ready.get(key, 0) + 1
    needed: Dict[str, Dict[str, Any]] = {}
    for key, per_wave in (supply.get("wave_targets") or {}).items():
        source = _SOURCE_FOR.get(str(key))
        if not source:
            continue
        target = int(per_wave or 0) * lookahead
        current = ready.get(str(key), 0)
        candidate_count = candidates.get(str(key), 0)
        if current >= target:
            continue
        if candidate_count >= target:
            bucket = needed.setdefault("drafts", {
                "source": "drafts", "kind": "draft_preparation",
                "available": 0, "candidates": 0, "target": 0,
            })
            bucket["available"] += current
            bucket["candidates"] += candidate_count
            bucket["target"] += target
            continue
        bucket = needed.setdefault(source, {"source": source, "available": 0, "target": 0})
        bucket["available"] += current
        bucket["target"] += target
    if needed.get("drafts"):
        out["jobs"].append(needed["drafts"])
    for source in ("github", "x", "content"):
        job = needed.get(source)
        if not job or source not in sources:
            continue
        if source == "x":
            out["deferred"].append({"source": "x", "reason": "shared_browser_tab"})
        else:
            out["jobs"].append(job)
    return out


class Prefetcher:
    """每波最多一个后台 worker；结束时停止并接受剩余查询，不遗留写线程。"""

    def __init__(self, data_dir, policy: Dict[str, Any], *, ledger=None, log=print, draft_writer=None):
        self.data_dir = Path(data_dir)
        self.policy = policy
        self.ledger = ledger or Ledger(self.data_dir / "ledger.jsonl")
        self.log = log
        self.draft_writer = draft_writer
        self.stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = None
        self._result: Dict[str, Any] = {"ok": True, "status": "not_started"}

    def start(self) -> Dict[str, Any]:
        planned = plan_prefetch(self.data_dir, self.policy)
        if not planned["enabled"]:
            self._result = dict(planned, status="disabled")
            return dict(self._result)
        if not planned["jobs"]:
            self._result = dict(planned, status="idle")
            return dict(self._result)
        path = lock_path(self.data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            self._result = {"ok": False, "status": "skipped",
                            "reason": "prefetch_already_running"}
            return dict(self._result)
        self._lock = handle
        self._result = dict(planned, status="running")
        self._thread = threading.Thread(target=self._work, name="growovo-supply-prefetch")
        self._thread.start()
        return dict(self._result)

    def _profile(self):
        from . import grow_fit

        path = self.data_dir / "family-facts.json"
        if not path.is_file():
            return None
        import json
        return grow_fit.product_profile(json.loads(path.read_text(encoding="utf-8")))

    def _work(self) -> None:
        from . import supply_mine

        reports: Dict[str, Any] = {}
        ok = True
        try:
            profile = self._profile()
            config = ((self.policy.get("supply") or {}).get("prefetch") or {})
            for index, job in enumerate(list(self._result.get("jobs") or [])):
                if index and self.stop_event.is_set():
                    break
                source = job["source"]
                if source == "drafts":
                    if self.draft_writer is None:
                        raise RuntimeError("draft_writer_unavailable")
                    reports[source] = self.draft_writer(job=job)
                elif source == "github":
                    days = int(config.get("days") or 14)
                    queries = supply_mine.gh_queries(days)[:max(1, int(
                        config.get("max_queries") or 3))]
                    reports[source] = supply_mine.mine_github(
                        self.data_dir, queries=queries,
                        per_query=max(1, int(config.get("per_query") or 10)),
                        gap=max(0.0, float(config.get("gap_sec", 15))),
                        days=days, profile=profile, ledger=self.ledger,
                        stop_event=self.stop_event)
                elif source == "content":
                    reports[source] = supply_mine.mine_content(
                        self.data_dir, policy=self.policy, profile=profile, ledger=self.ledger)
                if not reports[source].get("ok"):
                    ok = False
            status = "stopped" if self.stop_event.is_set() else "done"
            self._result = dict(self._result, ok=ok, status=status, reports=reports)
        except Exception as exc:
            self.log("[supply] 后台预取失败，不影响本波发送：%s" % exc)
            self._result = dict(self._result, ok=False, status="failed",
                                error=str(exc)[:300], reports=reports)
        finally:
            if self._lock is not None:
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
                self._lock.close()
                self._lock = None

    def finish(self) -> Dict[str, Any]:
        if self._thread is None:
            return dict(self._result)
        self.stop_event.set()
        self._thread.join()
        return dict(self._result)
