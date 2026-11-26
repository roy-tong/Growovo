#!/usr/bin/env python3
"""供给池 —— 夜间互动循环的执行前储备。

池子只存「对象 + 证据 + 分数」：目标 URL、作者、正文节选、发布时间、为什么
值得回。不存成稿正文：队列草稿超过 12 小时即过期（engagement-cycle.json
queue.max_age_hours），提前写好的正文到第二晚会被丢弃；正文在波次前按需
生成，池子保证的是对象不枯竭，而不是把 459 条动作一次性写成稿。

两个文件分开存，因为它们是两个维度：
  data/state/supply-pool.json   内容维度：推文 / issue / 频道贴的对象与证据
  data/state/koc-registry.json  账号维度：谁值得长期关注、是否已触达

只读约定之外没有副作用：merge/mark 写文件，pick/stats 不写。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

POOL_NAME = "supply-pool.json"
KOC_NAME = "koc-registry.json"
LOCK_NAME = "supply-pool.lock"
VERSION = 1
STATUSES = ("new", "used", "rejected", "expired")
# 池子只负责存证据；哪些渠道真正消费由波次目标（wave_targets）决定。
# 挖矿适配器目前只产 x/github/content，但 reddit/linkedin/hn 的候选也可
# 手工导入，不在池子层把渠道判死；email 不属于互动对象池。
CHANNELS = ("x", "github", "discord", "reddit", "linkedin", "hn")
ACTIONS = ("reply", "publish")
# 账本里出现这些终态的目标不再进池：已发过、可能已发过、明确不该再触达。
TOUCHED_RESULTS = ("committed", "indeterminate", "suppressed", "no-dm", "bounced")

_GH_URL = re.compile(r"github\.com/([^/\s]+)/([^/\s]+?)/(?:issues|pull)/(\d+)")
_X_URL = re.compile(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)/status/(\d+)")


class PoolError(RuntimeError):
    """候选无法入库：缺目标、缺证据或结构不合法。"""


def pool_path(data_dir) -> Path:
    return Path(data_dir) / "state" / POOL_NAME


def koc_path(data_dir) -> Path:
    return Path(data_dir) / "state" / KOC_NAME


def lock_path(data_dir) -> Path:
    return Path(data_dir) / "state" / LOCK_NAME


def _now() -> _dt.datetime:
    return _dt.datetime.now().astimezone()


def _iso(when: _dt.datetime) -> str:
    return when.astimezone().isoformat(timespec="seconds")


def _parse_time(text: str) -> Optional[_dt.datetime]:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        got = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if got.tzinfo is None:
        got = got.replace(tzinfo=_dt.timezone.utc)
    return got


def _epoch(text: str) -> float:
    got = _parse_time(text)
    return got.timestamp() if got else 0.0


def _empty_pool() -> Dict[str, Any]:
    return {"version": VERSION, "built_at": "", "items": {}, "last_build": []}


def load(data_dir) -> Dict[str, Any]:
    """读池子；文件不存在返回空池，结构损坏显式报错而不是静默清空。"""
    path = pool_path(data_dir)
    if not path.is_file():
        return _empty_pool()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise PoolError("supply-pool.json 不可读：%s" % exc)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), dict):
        raise PoolError("supply-pool.json 结构损坏：items 必须是对象")
    payload.setdefault("version", VERSION)
    payload.setdefault("built_at", "")
    payload.setdefault("last_build", [])
    return payload


def _atomic_write(path: Path, payload: Dict[str, Any]) -> Path:
    """同目录临时文件 + fsync + replace，与 engagement_cycle 的写法一致。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                    dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


def save(data_dir, payload: Dict[str, Any]) -> Path:
    payload = dict(payload)
    payload["version"] = VERSION
    return _atomic_write(pool_path(data_dir), payload)


def candidate_key(row: Dict[str, Any]) -> str:
    """稳定的对象键；同一目标反复挖到只更新，不重复占位。"""
    key = str(row.get("key") or "").strip()
    if key:
        return key
    channel = str(row.get("channel") or "").strip().lower()
    action = str(row.get("action") or "").strip().lower()
    target = str(row.get("target") or "").strip()
    if channel == "github":
        match = _GH_URL.search(target)
        if match:
            return "github:%s/%s#%s" % (match.group(1), match.group(2), match.group(3))
    if channel == "x":
        match = _X_URL.search(target)
        if match:
            return "x:%s/%s" % (match.group(1), match.group(2))
    if target:
        digest = hashlib.sha1(("%s|%s|%s" % (channel, action, target)).encode("utf-8"))
        return "%s:%s" % (channel, digest.hexdigest()[:16])
    raise PoolError("无法生成 key：缺少 target")


def _excerpt(text: str, limit: int = 2000) -> str:
    body = re.sub(r"\r\n?", "\n", str(text or "")).strip()
    if len(body) <= limit:
        return body
    return body[:limit].rstrip() + "…"


def normalize(row: Dict[str, Any], *, now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """校验并补齐一条候选；缺目标或缺证据直接拒绝，不入池。"""
    if not isinstance(row, dict):
        raise PoolError("候选必须是对象")
    now = now or _now()
    channel = str(row.get("channel") or row.get("platform") or "").strip().lower()
    action = str(row.get("action") or "reply").strip().lower()
    if channel not in CHANNELS:
        raise PoolError("不支持的渠道：%r" % channel)
    if action not in ACTIONS:
        raise PoolError("不支持的动作：%r" % action)
    target = str(row.get("target") or row.get("thread_ref") or "").strip()
    if action == "reply" and not target:
        raise PoolError("reply 缺少 target")
    title = str(row.get("title") or "").strip()
    context = _excerpt(row.get("context") or row.get("text") or row.get("body") or "")
    if not context and not title:
        raise PoolError("缺少正文节选：没有可核对的依据就不入库")
    published = str(row.get("published_at") or row.get("created_at") or "").strip()
    score = int(row.get("score") or 0)
    item = {
        "key": candidate_key(dict(row, channel=channel, action=action, target=target)),
        "channel": channel,
        "action": action,
        "target": target,
        "actor": str(row.get("actor") or row.get("handle") or row.get("author") or "").strip(),
        "title": title or context.split("\n", 1)[0][:140],
        "context": context,
        "published_at": published,
        "published_ts": _epoch(published),
        "score": max(0, min(100, score)),
        "tier": str(row.get("tier") or ""),
        "why": [str(x) for x in (row.get("why") or []) if str(x).strip()][:6],
        "risks": [str(x) for x in (row.get("risks") or []) if str(x).strip()][:6],
        "tags": [str(x) for x in (row.get("tags") or []) if str(x).strip()][:8],
        "source": str(row.get("source") or ""),
        "status": str(row.get("status") or "new"),
        "found_at": str(row.get("found_at") or _iso(now)),
        "last_seen": _iso(now),
    }
    if item["status"] not in STATUSES:
        raise PoolError("未知状态：%r" % item["status"])
    return item


def _touched(ledger) -> Set[Tuple[str, str]]:
    """账本里已触达的 (channel, target)；含 indeterminate 等不可重试终态。"""
    if ledger is None:
        return set()
    out: Set[Tuple[str, str]] = set()
    for rec in ledger.all():
        if str(rec.get("result") or "") in TOUCHED_RESULTS:
            out.add((str(rec.get("channel") or ""), str(rec.get("target") or "")))
    return out


def _locked(data_dir):
    """池子自己的非阻塞锁；不复用 engagement-cycle.lock，挖矿不挡波次。"""
    import fcntl

    path = lock_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def merge(data_dir, rows: Sequence[Dict[str, Any]], *, source: str = "",
          now: Optional[_dt.datetime] = None, profile: Optional[Dict[str, Any]] = None,
          ledger=None, build_note: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """合并候选：按 key 去重、跳过账本已触达目标、可选按产品画像打分。"""
    from . import grow_fit

    now = now or _now()
    handle = _locked(data_dir)
    if handle is None:
        return {"ok": False, "reason": "pool_lock_busy", "added": 0, "updated": 0}
    try:
        payload = load(data_dir)
        items: Dict[str, Any] = payload["items"]
        touched = _touched(ledger)
        added: List[str] = []
        updated: List[str] = []
        rejected: List[Dict[str, Any]] = []
        duplicates = 0
        excluded = 0
        for index, raw in enumerate(rows):
            try:
                item = normalize(raw, now=now)
            except PoolError as exc:
                rejected.append({"index": index, "reason": str(exc),
                                 "target": str((raw or {}).get("target") or "")})
                continue
            from .protected_content import is_target_suppressed
            if is_target_suppressed(data_dir, item["target"]):
                excluded += 1
                rejected.append({"target": item["target"], "reason": "protected_account"})
                continue
            # 只对 person/post 级目标去重：同一频道可以反复发布，
            # 但同一个 issue/推文只回复一次（账本已触达就不再来）。
            if item["action"] == "reply" and (item["channel"], item["target"]) in touched:
                excluded += 1
                continue
            if profile:
                post = {"text": item["context"] or item["title"],
                        "handle": item["actor"],
                        "engagement": raw.get("engagement") or {},
                        "age_hours": _age_hours(item["published_ts"], now)}
                account = raw.get("account") if isinstance(raw.get("account"), dict) else None
                fit = grow_fit.score_post(post, profile, account=account)
                item["score"] = int(fit["score"])
                item["tier"] = str(fit.get("tier") or "")
                item["why"] = list(fit.get("reasons") or [])[:6]
                item["risks"] = list(fit.get("risks") or [])[:6]
            item["source"] = item["source"] or str(source or "")
            existing = items.get(item["key"])
            if existing is None:
                items[item["key"]] = item
                added.append(item["key"])
                continue
            duplicates += 1
            keep_status = str(existing.get("status") or "new")
            merged = dict(existing)
            for field in ("title", "context", "published_at", "published_ts",
                          "actor", "tags", "score", "tier", "why", "risks"):
                if item.get(field) not in ("", None, [], 0, 0.0):
                    merged[field] = item[field]
            merged["status"] = keep_status
            merged["last_seen"] = item["last_seen"]
            merged["found_at"] = existing.get("found_at") or item["found_at"]
            if merged != existing:
                items[item["key"]] = merged
                updated.append(item["key"])
        if added:
            payload["built_at"] = _iso(now)
        if build_note:
            history = list(payload.get("last_build") or [])
            history.append(dict(build_note, at=_iso(now)))
            payload["last_build"] = history[-20:]
        save(data_dir, payload)
        return {"ok": True, "added": len(added), "updated": len(updated),
                "duplicates": duplicates, "excluded_touched": excluded,
                "rejected": rejected, "pool_size": len(items),
                "added_keys": added[:50], "pool_path": str(pool_path(data_dir))}
    finally:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _age_hours(published_ts: float, now: _dt.datetime) -> Optional[float]:
    if not published_ts:
        return None
    return max(0.0, (now.timestamp() - published_ts) / 3600.0)


def pick(data_dir, channel: str, action: str, n: int, *, min_score: int = 0,
         exclude: Iterable[str] = (),
         blocked: Iterable[Tuple[str, str]] = ()) -> List[Dict[str, Any]]:
    """按分数降序、发布时间降序取 N 条；只读，不改状态。

    blocked 是已触达/已隔离的 (channel, target) 集合：同一个目标不再进候选，
    避免"池子里还是 new、队列里又出现同一目标"。
    """
    payload = load(data_dir)
    skip = set(exclude)
    hit = set(blocked)
    rows = [row for row in payload["items"].values()
            if row.get("channel") == channel and row.get("action") == action
            and row.get("status") == "new" and int(row.get("score") or 0) >= min_score
            and row.get("key") not in skip
            and (str(row.get("channel") or ""), str(row.get("target") or "")) not in hit]
    rows.sort(key=lambda row: (-int(row.get("score") or 0),
                               -float(row.get("published_ts") or 0)))
    return rows[:max(0, int(n))]


def mark(data_dir, key: str, status: str, *, why: str = "",
         now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """标记对象状态；未知 key 报错，避免把拼错的键当成成功。"""
    if status not in STATUSES:
        raise PoolError("未知状态：%r" % status)
    now = now or _now()
    handle = _locked(data_dir)
    if handle is None:
        return {"ok": False, "reason": "pool_lock_busy"}
    try:
        payload = load(data_dir)
        item = payload["items"].get(str(key))
        if item is None:
            raise PoolError("池子里没有这个 key：%s" % key)
        item["status"] = status
        item["status_at"] = _iso(now)
        if why:
            item["status_why"] = str(why)
        save(data_dir, payload)
        return {"ok": True, "key": key, "status": status, "item": item}
    finally:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def prune(data_dir, *, days: int = 21, now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """清掉过期且超过 N 天的对象；new/used 不动。"""
    now = now or _now()
    cutoff = now.timestamp() - max(1, int(days)) * 86400
    payload = load(data_dir)
    items = payload["items"]
    drop = [key for key, item in items.items()
            if item.get("status") in ("expired", "rejected")
            and float(item.get("published_ts") or _epoch(item.get("found_at") or "")) < cutoff]
    for key in drop:
        items.pop(key, None)
    if drop:
        save(data_dir, payload)
    return {"ok": True, "removed": len(drop), "pool_size": len(items)}


def _split_target(key: str) -> Tuple[str, str]:
    if "/" not in key:
        raise PoolError("目标格式应为 channel/action：%s" % key)
    channel, action = key.split("/", 1)
    return channel.strip().lower(), action.strip().lower()


def stats(data_dir, policy: Dict[str, Any], *, nights: int = 3,
          now: Optional[_dt.datetime] = None,
          per_night: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """每渠道储备深度：available / need_per_night，缺口如实报。

    per_night 是各渠道的整夜可达上限（来自预算层，已扣平台日额度）。
    传入时按它压减需求：储备目标不应大于当晚真正发得出的量。
    """
    now = now or _now()
    payload = load(data_dir)
    window = (policy or {}).get("window") or {}
    waves = int(window.get("waves_per_night") or 9)
    targets = ((policy or {}).get("supply") or {}).get("wave_targets") or {}
    caps = dict(per_night or {})
    counts: Dict[Tuple[str, str], Dict[str, int]] = {}
    for item in payload["items"].values():
        pair = (str(item.get("channel") or ""), str(item.get("action") or ""))
        bucket = counts.setdefault(pair, {"new": 0, "used": 0, "rejected": 0, "expired": 0})
        status = str(item.get("status") or "new")
        if status in bucket:
            bucket[status] += 1
    rows = []
    total_need = 0
    total_available = 0
    for key, per_wave in targets.items():
        channel, action = _split_target(key)
        configured_night = int(per_wave) * waves
        cap = caps.get(key)
        need_night = min(configured_night, int(cap)) if cap is not None else configured_night
        need_total = need_night * max(1, int(nights))
        bucket = counts.get((channel, action)) or {"new": 0, "used": 0,
                                                   "rejected": 0, "expired": 0}
        available = bucket["new"]
        total_need += need_total
        total_available += available
        rows.append({
            "channel": channel, "action": action,
            "need_per_wave": int(per_wave), "need_per_night": need_night,
            "configured_per_night": configured_night,
            "capped_by_daily_budget": cap is not None and need_night < configured_night,
            "need_total": need_total, "available": available,
            "depth_nights": round(available / need_night, 2) if need_night else 0.0,
            "shortfall": max(0, need_total - available),
            "used": bucket["used"], "rejected": bucket["rejected"],
            "expired": bucket["expired"],
        })
    return {"nights": max(1, int(nights)), "waves_per_night": waves,
            "channels": rows,
            "totals": {"need_total": total_need, "available": total_available,
                       "shortfall": max(0, total_need - total_available),
                       "depth_nights": round(total_available / max(1, total_need / max(1, nights)), 2)},
            "koc": koc_stats(data_dir), "built_at": payload.get("built_at") or "",
            "pool_size": len(payload["items"]), "now": _iso(now)}


# ---- KOC 登记 ---------------------------------------------------------

def koc_load(data_dir) -> Dict[str, Any]:
    path = koc_path(data_dir)
    if not path.is_file():
        return {"version": VERSION, "updated_at": "", "accounts": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise PoolError("koc-registry.json 不可读：%s" % exc)
    if not isinstance(payload, dict) or not isinstance(payload.get("accounts"), dict):
        raise PoolError("koc-registry.json 结构损坏：accounts 必须是对象")
    payload.setdefault("version", VERSION)
    payload.setdefault("updated_at", "")
    return payload


def koc_save(data_dir, payload: Dict[str, Any]) -> Path:
    payload = dict(payload)
    payload["version"] = VERSION
    return _atomic_write(koc_path(data_dir), payload)


def koc_merge(data_dir, accounts: Sequence[Dict[str, Any]], *, source: str = "",
              now: Optional[_dt.datetime] = None,
              profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """合并账号：首次出现记 first_seen，重复出现累加 hits，不覆盖已触达事实。"""
    from . import grow_fit

    now = now or _now()
    handle = _locked(data_dir)
    if handle is None:
        return {"ok": False, "reason": "pool_lock_busy", "added": 0, "updated": 0}
    try:
        payload = koc_load(data_dir)
        known: Dict[str, Any] = payload["accounts"]
        added, updated, rejected = 0, 0, 0
        for raw in accounts:
            if not isinstance(raw, dict):
                rejected += 1
                continue
            name = str(raw.get("handle") or raw.get("actor") or "").strip().lstrip("@")
            if not name:
                rejected += 1
                continue
            from .protected_content import is_target_suppressed
            if is_target_suppressed(data_dir, name):
                rejected += 1
                continue
            followers = raw.get("followers")
            entry = dict(known.get(name) or {})
            entry["handle"] = name
            entry["first_seen"] = entry.get("first_seen") or _iso(now)[:10]
            entry["last_seen"] = _iso(now)[:10]
            entry["hits"] = int(entry.get("hits") or 0) + 1
            if isinstance(followers, int):
                entry["followers"] = followers
            if raw.get("bio"):
                entry["bio"] = _excerpt(raw["bio"], 500)
            if raw.get("best_url"):
                entry["best_url"] = str(raw["best_url"])
            if raw.get("topics"):
                topics = {str(t) for t in entry.get("topics") or []}
                topics.update(str(t) for t in raw["topics"] if str(t).strip())
                entry["topics"] = sorted(topics)[:12]
            sources = {str(s) for s in entry.get("sources") or []}
            if source:
                sources.add(str(source))
            entry["sources"] = sorted(sources)
            if profile:
                fit = grow_fit.koc_score({
                    "handle": name, "followers": entry.get("followers"),
                    "bio": entry.get("bio") or "",
                    "recent_posts": raw.get("recent_posts") or [],
                }, profile)
                entry["koc_score"] = int(fit["score"])
                entry["koc_tier"] = str(fit.get("tier") or "")
                entry["is_koc"] = bool(fit.get("is_koc"))
                entry["koc_why"] = list(fit.get("reasons") or [])[:5]
                entry["koc_risks"] = list(fit.get("risks") or [])[:5]
            else:
                entry.setdefault("koc_tier", grow_fit.account_tier(followers)
                                 if isinstance(followers, int) else "unknown")
            if name in known:
                updated += 1
            else:
                added += 1
            known[name] = entry
        if added or updated:
            payload["updated_at"] = _iso(now)
            koc_save(data_dir, payload)
        return {"ok": True, "added": added, "updated": updated,
                "rejected": rejected, "accounts": len(known),
                "path": str(koc_path(data_dir))}
    finally:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def koc_stats(data_dir) -> Dict[str, Any]:
    payload = koc_load(data_dir)
    accounts = payload["accounts"]
    tiers: Dict[str, int] = {}
    engaged = 0
    koc = 0
    for entry in accounts.values():
        tier = str(entry.get("koc_tier") or entry.get("tier") or "unknown")
        tiers[tier] = tiers.get(tier, 0) + 1
        if entry.get("is_koc"):
            koc += 1
        if entry.get("last_engaged_at") or entry.get("touches"):
            engaged += 1
    return {"accounts": len(accounts), "koc": koc,
            "untouched": len(accounts) - engaged, "engaged": engaged,
            "tiers": tiers, "updated_at": payload.get("updated_at") or ""}


def import_watchlist(data_dir, path, *, source: str = "legacy-watchlist",
                     now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """把旧 run 的 koc_watchlist.json 导入账号登记；已触达事实一并带过来。"""
    now = now or _now()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("accounts") or {}
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise PoolError("watchlist 结构不符：accounts 必须是对象或数组")
    merged = koc_merge(data_dir, rows, source=source, now=now)
    registry = koc_load(data_dir)
    for row in rows:
        name = str(row.get("handle") or "").strip()
        entry = registry["accounts"].get(name)
        if not entry:
            continue
        if row.get("last_engaged_at"):
            entry["last_engaged_at"] = row["last_engaged_at"]
        if isinstance(row.get("touches"), int):
            entry["touches"] = row["touches"]
        if row.get("best_url") and not entry.get("best_url"):
            entry["best_url"] = row["best_url"]
        if isinstance(row.get("score"), int):
            entry["post_score"] = row["score"]
    koc_save(data_dir, registry)
    merged["imported"] = len(rows)
    return merged


# ---- 波次工作清单 ---------------------------------------------------------

_REPO_RE = re.compile(r"^https?://github\.com/([^/]+/[^/]+)/", re.I)


def _repo_of(target: str) -> str:
    hit = _REPO_RE.match(str(target or ""))
    return hit.group(1).lower() if hit else ""


def wave_plan(data_dir, policy: Dict[str, Any], *, wave: int = 1,
              now: Optional[_dt.datetime] = None,
              out_path: Optional[Path] = None,
              ledger=None) -> Dict[str, Any]:
    """按策略的每波额度，从储备里取一批候选，写成某一波的工作清单。

    只取 status=new；同一波内 GitHub 每个仓库最多 1 条（自设质量纪律），
    Discord 轮换频道、同一波内不重复同一个频道。不写正文、不改状态：
    正文由写手在波次前补，缺依据就丢弃。清单是只读快照，重复生成即可覆盖。

    传 ledger 时额外排除已触达/已隔离目标；GitHub 同仓库不跨波重复，
    避免昨晚"去重后只剩 6 个新目标"这类空转。
    """
    now = now or _now()
    platforms = (policy or {}).get("platforms") or {}
    items: Dict[str, List[Dict[str, Any]]] = {}
    counts: Dict[str, int] = {}
    shortfall: Dict[str, int] = {}
    used_targets: Dict[str, set] = {}
    touched = _touched(ledger)
    if ledger is not None:
        for channel in platforms:
            for target in ledger.no_retry(channel):
                touched.add((str(channel), str(target)))
    used_repos = {_repo_of(target) for channel, target in touched
                  if channel == "github"} - {""}

    for channel, actions in platforms.items():
        for action, spec in (actions or {}).items():
            if not isinstance(spec, dict):
                continue
            need = int(spec.get("per_hour") or 0)
            if need <= 0:
                continue
            label = "%s/%s" % (channel, action)
            pool = pick(data_dir, channel, action, need * 4, blocked=touched)
            chosen: List[Dict[str, Any]] = []
            seen = used_targets.setdefault(label, set())
            for row in pool:
                if len(chosen) >= need:
                    break
                target = str(row.get("target") or "")
                if action == "reply" or channel == "github":
                    if not target or target in seen:
                        continue
                    if channel == "github":
                        repo = _repo_of(target)
                        if repo and (repo in seen or repo in used_repos):
                            continue
                    key = _repo_of(target) if channel == "github" else target
                elif channel == "discord":
                    key = target
                else:
                    key = str(row.get("key") or "")
                if not key or key in seen:
                    continue
                seen.add(key)
                chosen.append(row)
            # 同一行只进一个渠道/动作
            items[label] = chosen
            counts[label] = len(chosen)
            shortfall[label] = max(0, need - len(chosen))

    payload = {"version": 1, "kind": "wave-supply", "wave": int(wave),
               "built_at": _iso(now), "policy_suite": (policy or {}).get("suite") or "",
               "counts": counts, "shortfall": shortfall, "items": items,
               "note": "候选不是成稿：每条要在波次前补 text，没有真实依据就丢弃。"}
    target_path = Path(out_path) if out_path else Path(data_dir) / "state" / "wave-supply.json"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = target_path.with_suffix(target_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(target_path)
    payload["out"] = str(target_path)
    payload["total"] = sum(counts.values())
    return payload
