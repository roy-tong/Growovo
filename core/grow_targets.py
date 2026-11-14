#!/usr/bin/env python3
"""回复目标发现 —— 把「今天回谁」从手工搜索变成一条可审计的命令。

定位：只读发现，不写内容、不发送。产出候选清单（作者/正文/链接/时间/互动数），
由供给 agent 按 brief 写回复后 `mkt grow stage` 入队，仍走同一套门禁。

纪律：
  - 只搜公开时间线，按发布时间/互动过滤，不抓私密内容；
  - 排除自己、已回复过的目标（账本 committed）、已暂存的队列目标；
  - 候选不是内容：不因为有候选就直接发。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

STATUS_RE = re.compile(r"^/([A-Za-z0-9_]+)/status/(\d+)")
NUM_RE = re.compile(r"(\d[\d,\.]*)\s*(万|K|M)?")


class TargetError(RuntimeError):
    pass


def _to_number(text: str) -> int:
    """'1,234' / '1.2K' / '3万' → int；解析不了返回 0。"""
    match = NUM_RE.search(str(text or ""))
    if not match:
        return 0
    raw, suffix = match.group(1).replace(",", ""), (match.group(2) or "").upper()
    try:
        value = float(raw)
    except ValueError:
        return 0
    if suffix == "K":
        value *= 1_000
    elif suffix == "M":
        value *= 1_000_000
    elif suffix == "万":
        value *= 10_000
    return int(value)


def parse_engagement(labels: Iterable[str]) -> Dict[str, int]:
    """从按钮 aria-label 里取互动数，中英文都认。"""
    out = {"replies": 0, "reposts": 0, "likes": 0}
    for label in labels or []:
        text = str(label or "")
        low = text.lower()
        number = _to_number(text)
        if "回复" in text or "repl" in low:
            out["replies"] = number
        elif "转帖" in text or "repost" in low or "retweet" in low:
            out["reposts"] = number
        elif "喜欢" in text or "like" in low:
            out["likes"] = number
    return out


def parse_status_url(href: str) -> Optional[Dict[str, str]]:
    """'/handle/status/123' → {'handle','tweet_id','url'}；不是推文链接返回 None。"""
    match = STATUS_RE.match(str(href or "").strip())
    if not match:
        return None
    handle, tweet_id = match.group(1), match.group(2)
    return {"handle": handle, "tweet_id": tweet_id,
            "url": f"https://x.com/{handle}/status/{tweet_id}"}


def age_hours(published_at: str, *, now: Optional[datetime] = None) -> Optional[float]:
    """ISO 发布时间 → 距今小时数；解析失败返回 None。"""
    try:
        parsed = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (current - parsed).total_seconds() / 3600.0)


def filter_candidates(candidates: List[Dict[str, Any]], *,
                      max_age_hours: float = 48,
                      min_likes: int = 0,
                      min_replies: int = 0,
                      exclude_handles: Iterable[str] = (),
                      exclude_urls: Iterable[str] = (),
                      now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """统一过滤：年龄、互动、作者、已处理目标。纯函数，可离线测试。"""
    skip_handles = {str(h).lstrip("@").lower() for h in exclude_handles if h}
    skip_urls = {str(u) for u in exclude_urls if u}
    out = []
    for row in candidates:
        handle = str(row.get("handle") or "").lstrip("@").lower()
        if handle and handle in skip_handles:
            continue
        if row.get("url") in skip_urls:
            continue
        hours = row.get("age_hours")
        if hours is None:
            hours = age_hours(row.get("published_at", ""), now=now)
        if hours is not None and hours > max_age_hours:
            continue
        engagement = row.get("engagement") or {}
        if int(engagement.get("likes") or 0) < min_likes:
            continue
        if int(engagement.get("replies") or 0) < min_replies:
            continue
        out.append(row)
    out.sort(key=lambda r: (r.get("age_hours") if r.get("age_hours") is not None else 1e9,
                            -int((r.get("engagement") or {}).get("likes") or 0)))
    return out


def _extract_article(article) -> Optional[Dict[str, Any]]:
    """从一个 article 元素提取候选；结构变化时返回 None 而不是猜。"""
    try:
        href = article.locator('a[href*="/status/"]').first.get_attribute("href")
    except Exception:  # noqa: BLE001
        return None
    parsed = parse_status_url(href or "")
    if not parsed:
        return None
    try:
        name_block = article.locator('[data-testid="User-Name"]').first.inner_text(timeout=2000)
    except Exception:  # noqa: BLE001
        name_block = ""
    handle = parsed["handle"]
    display = " ".join(name_block.split(" · ")[0].split("\n"))[:80] if name_block else handle
    try:
        text = article.locator('[data-testid="tweetText"]').first.inner_text(timeout=2000)
    except Exception:  # noqa: BLE001
        text = ""
    try:
        published = article.locator("time").first.get_attribute("datetime") or ""
    except Exception:  # noqa: BLE001
        published = ""
    try:
        labels = article.evaluate(
            "el => Array.from(el.querySelectorAll(\"[role='group'] button\"))"
            ".map(b => b.getAttribute('aria-label') || '')")
    except Exception:  # noqa: BLE001
        labels = []
    return {"handle": handle, "display_name": display,
            "url": parsed["url"], "tweet_id": parsed["tweet_id"],
            "text": " ".join(str(text or "").split())[:280],
            "published_at": published,
            "engagement": parse_engagement(labels)}


def discover(queries: List[str], *, limit: int = 10, max_age_hours: float = 48,
             min_likes: int = 0, min_replies: int = 0,
             exclude_handles: Iterable[str] = (), exclude_urls: Iterable[str] = (),
             pages: int = 1, cdp_url: Optional[str] = None,
             log=print) -> List[Dict[str, Any]]:
    """只读搜索并返回过滤后的候选。需要已登录的调试 Chrome。"""
    from . import cdp
    queries = [str(q).strip() for q in queries if str(q).strip()]
    if not queries:
        raise TargetError("至少给一个搜索词")
    browser, page = cdp.connect(cdp_url)
    raw: Dict[str, Dict[str, Any]] = {}
    try:
        for query in queries:
            encoded = query.replace(" ", "%20").replace('"', "%22")
            page.goto(f"https://x.com/search?q={encoded}&src=typed_query&f=live",
                      wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
            for page_index in range(max(1, int(pages))):
                if page_index:
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(2500)
                articles = page.locator('article[data-testid="tweet"]').all()
                for article in articles:
                    row = _extract_article(article)
                    if not row:
                        continue
                    row["age_hours"] = age_hours(row.get("published_at", ""))
                    row["query"] = query
                    raw.setdefault(row["url"], row)
                log(f"[targets] {query}: 已收 {len(raw)} 条")
    finally:
        browser.close()
    rows = filter_candidates(list(raw.values()), max_age_hours=max_age_hours,
                             min_likes=min_likes, min_replies=min_replies,
                             exclude_handles=exclude_handles,
                             exclude_urls=exclude_urls)
    return rows[:max(1, int(limit))]


def save_candidates(data_dir, run_id: str, rows: List[Dict[str, Any]],
                    *, on: str) -> Path:
    """落盘到 data/grow/<id>/targets/<date>.json；同日覆盖。"""
    directory = Path(data_dir) / "grow" / run_id / "targets"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{on}.json"
    path.write_text(json.dumps({"run": run_id, "on": on, "count": len(rows),
                                "candidates": rows}, ensure_ascii=False,
                               indent=1) + "\n", encoding="utf-8")
    return path


def load_candidates(data_dir, run_id: str, *, on: str) -> List[Dict[str, Any]]:
    path = Path(data_dir) / "grow" / run_id / "targets" / f"{on}.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("candidates") or [])


def render(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "没有符合条件的候选（放宽 --max-age-hours / --min-likes 或换搜索词）"
    lines = [f"候选 {len(rows)} 条（只读发现；写回复前仍要过 brief 与门禁）", ""]
    for i, row in enumerate(rows, 1):
        eng = row.get("engagement") or {}
        age = row.get("age_hours")
        age_text = f"{age:.0f} 小时前" if isinstance(age, (int, float)) else "时间未知"
        lines.append(f"[{i}] @{row['handle']}（{age_text}｜回复 {eng.get('replies', 0)}"
                     f"｜喜欢 {eng.get('likes', 0)}）")
        lines.append(f"    {row.get('text', '')[:180]}")
        lines.append(f"    {row.get('url', '')}")
        lines.append("")
    return "\n".join(lines)


def read_tweet_text(url: str, *, cdp_url: Optional[str] = None,
                    log=print) -> str:
    """打开一条推文，读正文（用于 stage 时自动补 target_text）。只读。"""
    from . import cdp
    browser, page = cdp.connect(cdp_url)
    try:
        if not cdp.safe_goto(page, url, log=log):
            return ""
        page.wait_for_timeout(3000)
        try:
            article = page.locator("article").first
            return " ".join(article.locator('[data-testid="tweetText"]')
                            .first.inner_text(timeout=3000).split())[:500]
        except Exception:  # noqa: BLE001
            return ""
    finally:
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass
