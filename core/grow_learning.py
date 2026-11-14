#!/usr/bin/env python3
"""每日复盘→次日优化：把「发出去之后发生了什么」变成可执行的调整。

链路：
  1. 采集：读自己已发布帖子的公开指标（查看/喜欢/回复/转帖），落
     `data/grow/<id>/post_metrics.jsonl`；
  2. 归因：用 stage 时记录的内容索引（公式/时间窗/受众）把指标分组；
  3. 决策：只有样本 ≥3 的分组才允许提调整；调整以「混合权重」形式给出，
     带 rollback 与观察窗，不直接改策略选择。

纪律：样本不足时只记录「继续采集」，不硬调；公开指标是相关不是因果；
回复率/互动率是比值口径，不证明内容质量。
"""
from __future__ import annotations

import json
import re
import statistics
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

NUM_RE = re.compile(r"([\d][\d,]*(?:\.\d+)?)\s*(万|K|M)?")
VIEW_RE = re.compile(r"([\d][\d,]*(?:\.\d+)?)\s*(万|K|M)?\s*(?:次)?(?:查看|views?)", re.I)
MIN_SAMPLE = 3


class LearningError(RuntimeError):
    pass


def _number(text: str) -> Optional[int]:
    match = NUM_RE.search(str(text or ""))
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = (match.group(2) or "").upper()
    value *= {"K": 1_000, "M": 1_000_000, "万": 10_000}.get(suffix, 1)
    return int(value)


def parse_post_metrics(labels: Sequence[str], body: str = "") -> Dict[str, Any]:
    """从帖子页的 aria-label 与正文里取指标；取不到写 None，不猜 0。"""
    out: Dict[str, Any] = {"views": None, "likes": None, "replies": None,
                           "reposts": None}
    view_match = VIEW_RE.search(str(body or ""))
    if view_match:
        out["views"] = _number(view_match.group(0))
    for label in labels or []:
        text = str(label or "")
        low = text.lower()
        value = _number(text)
        if value is None:
            continue
        if "、" in text or ("replies" in low and "likes" in low):
            continue
        if "查看" in text or "观看" in text or "view" in low:
            out["views"] = value
        elif "回复" in text or "repl" in low:
            out["replies"] = value
        elif "转帖" in text or "repost" in low or "retweet" in low:
            out["reposts"] = value
        elif "喜欢" in text or "like" in low:
            out["likes"] = value
    return out


def read_post_metrics(url: str, *, cdp_url: Optional[str] = None,
                      log=print, evidence_dir=None) -> Dict[str, Any]:
    """打开自己的帖子页读公开指标。只读；失败返回 note。"""
    from . import cdp
    browser, page = cdp.connect(cdp_url)
    try:
        if not cdp.safe_goto(page, url, log=log):
            return {"url": url, "note": "页面打不开"}
        page.wait_for_timeout(3500)
        # Only the requested status: the page also contains its parent and other replies.
        status_id = str(url).rstrip("/").split("/")[-1].split("?")[0]
        article = None
        for candidate in page.locator('article[data-testid="tweet"]').all():
            links = candidate.locator('a:has(time)').evaluate_all(
                "els => els.map(e => e.getAttribute('href') || '')")
            if any(re.search(r"/status/" + re.escape(status_id) + r"(?:[/?]|$)", link)
                   for link in links):
                article = candidate
                break
        if article is None:
            return {"url": url, "note": "未找到自己的目标帖子，未读取整页指标"}
        body = article.inner_text()
        try:
            labels = article.locator("[aria-label]").evaluate_all(
                "els => els.map(e => e.getAttribute('aria-label') || '')")
        except Exception:  # noqa: BLE001
            labels = []
        metrics = parse_post_metrics(labels, body)
        metrics.update({"url": url,
                        "read_at": datetime.now().isoformat(timespec="seconds")})
        if evidence_dir is not None:
            directory = Path(evidence_dir)
            directory.mkdir(parents=True, exist_ok=True)
            shot = directory / f"{date.today().isoformat()}-{status_id}.png"
            article.screenshot(path=str(shot))
            metrics["source_ref"] = str(shot)
        return metrics
    finally:
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass


def match_own_article(articles: List[Dict[str, Any]], *, handle: str,
                      body: str) -> Optional[str]:
    """从页面文章列表里找作者是自己、正文前缀匹配的那条，返回其 URL。纯函数。"""
    want_handle = str(handle or "").lstrip("@").lower()
    prefix = " ".join(str(body or "").split())[:40].lower()
    if not want_handle or not prefix:
        return None
    for row in articles:
        if str(row.get("handle") or "").lstrip("@").lower() != want_handle:
            continue
        text = " ".join(str(row.get("text") or "").split()).lower()
        if text.startswith(prefix) or prefix in text:
            return str(row.get("url") or "")
    return None


def resolve_post_url(target_url: str, body: str, handle: str, *,
                     cdp_url: Optional[str] = None, log=print) -> Optional[str]:
    """回复发出后，回目标帖找自己那条回复的 URL；找不到返回 None（不猜）。"""
    from . import cdp
    from .grow_targets import _extract_article
    browser, page = cdp.connect(cdp_url)
    try:
        if not cdp.safe_goto(page, target_url, log=log):
            return None
        page.wait_for_timeout(4000)
        try:
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            pass
        articles = []
        for loc in page.locator('article[data-testid="tweet"]').all()[:40]:
            row = _extract_article(loc)
            if row:
                articles.append(row)
        return match_own_article(articles, handle=handle, body=body)
    finally:
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass


def _metrics_path(data_dir: Path, run_id: str) -> Path:
    return Path(data_dir) / "grow" / run_id / "post_metrics.jsonl"


def load_metrics(data_dir, run_id: str) -> List[Dict[str, Any]]:
    path = _metrics_path(Path(data_dir), run_id)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def append_metrics(data_dir, run_id: str, rows: Iterable[Dict[str, Any]]) -> int:
    path = _metrics_path(Path(data_dir), run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def _index_key(target: str) -> str:
    return str(target or "").removeprefix("tweet:").strip()


def collect(data_dir, run_id: str, *, rows: Sequence[Dict[str, Any]] = (),
            limit: int = 20, sleep_seconds: float = 2.0,
            reader=None, on: Optional[str] = None, log=print) -> Dict[str, Any]:
    """每日刷新已发布帖子的指标；同日成功读数去重，失败可以重试。"""
    data_dir = Path(data_dir)
    on = on or date.today().isoformat()
    existing = {_index_key(row.get("target") or row.get("url"))
                for row in load_metrics(data_dir, run_id)
                if str(row.get("observed_on") or row.get("read_at") or "")[:10] == on
                and isinstance(row.get("views"), int) and row["views"] >= 0}
    seen = set(existing)
    pending = []
    for row in rows:
        target = _index_key(row.get("target") or row.get("url"))
        fetch_url = _index_key(row.get("published_url") or row.get("url") or target)
        if not target or not fetch_url or target in seen:
            continue
        seen.add(target)
        pending.append((target, fetch_url))
        if len(pending) >= max(1, int(limit)):
            break
    fetched = []
    for target, fetch_url in pending:
        try:
            metrics = (reader(fetch_url) if reader is not None
                       else read_post_metrics(fetch_url, log=log,
                            evidence_dir=Path(data_dir) / "grow" / run_id / "evidence")) or {}
            metrics = dict(metrics)
        except Exception as exc:
            metrics = {"note": f"读取失败：{type(exc).__name__}: {exc}"[:300]}
        metrics["observed_on"] = on
        metrics.setdefault("url", fetch_url)
        metrics["target"] = target
        fetched.append(metrics)
        if reader is None and sleep_seconds:
            time.sleep(max(0.0, float(sleep_seconds)))
        log(f"[learn] {fetch_url} → views={metrics.get('views')} "
            f"likes={metrics.get('likes')} replies={metrics.get('replies')}")
    written = append_metrics(data_dir, run_id, fetched) if fetched else 0
    return {"pending": len(pending), "written": written, "items": fetched}


def latest_metrics(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """一条帖子只算一个样本；保留最近一次有效快照，失败不覆盖有效读数。"""
    latest = {}
    for row in rows:
        key = _index_key(row.get("target") or row.get("url"))
        if not key or not isinstance(row.get("views"), int) or row["views"] < 0:
            continue
        stamp = str(row.get("observed_on") or row.get("read_at") or "")
        previous = latest.get(key)
        if previous is None or stamp >= str(previous.get("observed_on") or previous.get("read_at") or ""):
            latest[key] = row
    return list(latest.values())


def _median(values: List[float]) -> Optional[float]:
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 3) if values else None


def _bucket(rows: List[Dict[str, Any]], key_fn) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = key_fn(row) or "unknown"
        groups.setdefault(str(key), []).append(row)
    out = {}
    for key, items in groups.items():
        views = [int(r["views"]) for r in items if isinstance(r.get("views"), int)]
        replies = [int(r["replies"]) for r in items if isinstance(r.get("replies"), int)]
        likes = [int(r["likes"]) for r in items if isinstance(r.get("likes"), int)]
        rates = [int(r["replies"]) / int(r["views"])
                 for r in items
                 if isinstance(r.get("replies"), int) and isinstance(r.get("views"), int)
                 and int(r["views"]) > 0]
        out[key] = {"n": len(items), "rate_n": len(rates), "views_median": _median(views),
                    "likes_median": _median(likes),
                    "replies_median": _median(replies),
                    "reply_rate_median": _median(rates)}
    return out


def analyze(data_dir, run_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """按公式/时间窗/受众分组；样本不足只给建议不调参。"""
    metrics = latest_metrics(load_metrics(data_dir, run_id))
    index = {_index_key(k): v for k, v in (data.get("content_index") or {}).items()}
    rows = []
    for row in metrics:
        key = _index_key(row.get("target") or row.get("url"))
        meta = index.get(key) or {}
        rows.append(dict(row, formula=meta.get("formula") or "unknown",
                         window=meta.get("window") or "default",
                         audience=meta.get("audience") or "unknown",
                         author_tier=meta.get("author_tier") or "unknown",
                         koc=bool(meta.get("koc"))))
    if not rows:
        return {"sample": 0, "buckets": {}, "changes": [],
                "recommendation": "还没有帖子指标：先 mkt grow metrics 采集"}
    by_formula = _bucket(rows, lambda r: r.get("formula"))
    by_window = _bucket(rows, lambda r: r.get("window"))
    by_tier = _bucket(rows, lambda r: r.get("author_tier"))
    usable = {k: v for k, v in by_formula.items()
              if k != "unknown" and v["rate_n"] >= MIN_SAMPLE and v.get("reply_rate_median") is not None}
    changes: List[Dict[str, Any]] = []
    recommendation = "继续采集：有效样本不足，先不要改公式配比"
    if usable:
        best_key, best = max(usable.items(), key=lambda kv: kv[1]["reply_rate_median"] or 0)
        worst_key, worst = min(usable.items(), key=lambda kv: kv[1]["reply_rate_median"] or 0)
        overall = _median([r["reply_rate_median"] for r in usable.values() if r["reply_rate_median"] is not None])
        if (best["reply_rate_median"] or 0) > (overall or 0) * 1.2 and best["reply_rate_median"]:
            changes.append({
                "param": "content.mix_weights", "from": "current",
                "to": {"boost": best_key, "reduce": worst_key},
                "rollback": "恢复原配比",
                "detail": (f"{best_key} 的回复率中位数 {best['reply_rate_median']:.1%}"
                           f"（n={best['n']}）高于整体 {overall:.1%}；"
                           f"{worst_key} 较低；建议下次配比倾斜，观察 1 周")})
            recommendation = f"把 {best_key} 权重上调 10 个百分点，{worst_key} 下调，观察一周"
    koc_usable = {k: v for k, v in by_tier.items()
                  if k == "koc" and v["rate_n"] >= MIN_SAMPLE
                  and v.get("reply_rate_median") is not None}
    other = {k: v for k, v in by_tier.items()
             if k not in ("koc", "unknown") and v["rate_n"] >= MIN_SAMPLE
             and v.get("reply_rate_median") is not None}
    if koc_usable and other:
        koc_rate = koc_usable["koc"]["reply_rate_median"] or 0
        other_rate = max((v["reply_rate_median"] or 0) for v in other.values())
        if koc_rate > other_rate * 1.2:
            changes.append({
                "param": "target.koc_share",
                "from": "current", "to": {"koc_share": 0.7,
                                           "reason": f"KOC 回复率 {koc_rate:.1%} vs 其他 {other_rate:.1%}"},
                "rollback": "恢复 KOC 占比目标 50%",
                "detail": (f"KOC 组回复率中位数 {koc_rate:.1%}（n={koc_usable['koc']['n']}）"
                           f"高于非 KOC 组；次日把 KOC 触达目标提到 70%")})
            recommendation = (recommendation + "；" if recommendation else "") + \
                "KOC 组反馈更好，次日优先 KOC"
    return {"sample": len(rows), "buckets": {"formula": by_formula,
                                             "window": by_window,
                                             "author_tier": by_tier},
            "changes": changes, "recommendation": recommendation,
            "min_sample": MIN_SAMPLE}


def write_learning(data_dir, run_id: str, payload: Dict[str, Any], *,
                   on: Optional[str] = None) -> Path:
    directory = Path(data_dir) / "grow" / run_id / "learning"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{on or date.today().isoformat()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    latest = directory / "latest.json"
    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    return path


def latest_proposal(data_dir, run_id: str) -> Dict[str, Any]:
    path = Path(data_dir) / "grow" / run_id / "learning" / "latest.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def render(payload: Dict[str, Any]) -> str:
    lines = ["# 内容学习（样本驱动，非因果）", ""]
    lines.append(f"样本：{payload.get('sample', 0)} 条帖子｜最小分组样本 {payload.get('min_sample', MIN_SAMPLE)}")
    lines.append("")
    buckets = (payload.get("buckets") or {}).get("formula") or {}
    if buckets:
        lines += ["| 公式 | 样本 | 查看中位 | 回复中位 | 回复率中位 |",
                  "|---|---|---|---|---|"]
        for key, row in sorted(buckets.items(), key=lambda kv: -kv[1]["n"]):
            lines.append(f"| {key} | {row['n']} | {row.get('views_median')} | "
                         f"{row.get('replies_median')} | "
                         f"{row.get('reply_rate_median')} |")
        lines.append("")
    lines.append(f"建议：{payload.get('recommendation', '')}")
    for change in payload.get("changes") or []:
        lines.append(f"- {change.get('detail')}")
    lines += ["", "边界：公开指标是相关不是因果；样本 <3 的分组只展示不调参。", ""]
    return "\n".join(lines)
