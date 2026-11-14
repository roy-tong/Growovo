#!/usr/bin/env python3
"""账号/帖子契合度 —— 从「搜到什么回什么」升级为「谁值得回、回哪条、怎么回」。

两层评分：
  1. 帖子契合度：这条帖子跟产品要解决的问题有多近（关键词、求助信号、互动量级、新鲜度）；
  2. 账号契合度：发帖人是不是我们要找的人（bio/近期内容、粉丝量级、是否营销号）。

只读：不关注、不点赞、不发送。产出候选与建议角度，写内容仍走 briefs + stage + 门禁。

评分的用途不是「自动决定发什么」，而是把有限的手工/agent 产能排到最可能
产生对话的对象上；分数低不等于不能回，只代表优先级低。
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
WORD_RE = re.compile(r"[a-z][a-z0-9_\-]{2,}")

# 泛词不参与匹配，否则「ai / 产品 / 工具」会把所有帖子都打成高分
STOPWORDS: Set[str] = {
    "the", "and", "for", "with", "that", "this", "from", "your", "you", "our",
    "are", "was", "were", "will", "can", "not", "but", "all", "any", "how",
    "what", "why", "who", "when", "where", "into", "over", "than", "then",
    "产品", "工具", "平台", "公司", "用户", "一个", "我们", "你们", "他们",
    "可以", "如何", "什么", "为什么", "怎么", "已经", "还是", "就是", "这个",
}

# 求助/讨论信号：出现越多越值得回（不是广告词，是「有人在解决问题」）
INTENT_CUES = (
    "how do you", "how do i", "anyone", "recommend", "looking for", "stuck",
    "frustrat", "pain", "we use", "we spend", "our bill", "invoice", "reconcil",
    "cost", "budget", "spend", "audit", "allocate", "chargeback", "finops",
    "怎么", "如何", "有没有", "求推荐", "头疼", "麻烦", "账单", "对账", "分摊",
    "成本", "预算", "发票", "复核",
)

# 营销号/低质信号：降低优先级，不绝对排除
PROMO_MARKERS = (
    "link in bio", "dm me", "dm for", "free trial", "sign up", "limited offer",
    "giveaway", "airdrop", "click here", "buy now", "discount code",
    "私信", "加微", "扫码", "限时", "免费领", "抽奖",
)

# 中英术语桥：避免「中文 facts + 英文推文」互相匹配不上。
ALIASES = {
    "发票": ("invoice", "billing"), "对账": ("reconcile", "reconciliation"),
    "账单": ("invoice", "bill", "billing"), "账期": ("billing period", "cycle"),
    "分摊": ("allocate", "allocation", "split"), "成本": ("cost", "spend"),
    "支出": ("spend", "expense"), "预算": ("budget",), "合同": ("contract",),
    "供应商": ("vendor", "supplier"), "复核": ("review", "audit"),
    "审计": ("audit",), "费用": ("cost", "fee", "expense"),
    "关账": ("close", "closing"), "财务": ("finance", "finops"),
    "团队": ("team",), "用量": ("usage",), "价格": ("pricing", "price"),
}

SMALL_ACCOUNT = 200
MID_ACCOUNT = 50_000
BIG_ACCOUNT = 300_000

# 早期增长优先 KOC：粉丝几千到几万、垂直领域有声量、没有专业商业化，
# 对粉丝更像真实的人，愿意信任和转述。头部大号和小号都不是第一优先。
KOC_BAND = (1_000, 50_000)
MICRO_BAND = (200, 1_000)
MID_BAND = (50_000, 300_000)


class FitError(RuntimeError):
    pass


def _cjk_terms(text: str) -> List[str]:
    """中文按标点/连词切块，保留 2-8 字术语；不生成滑窗 n-gram（噪声太大）。"""
    raw = re.sub(r"\s+", " ", str(text or ""))
    chunks = re.split(r"[，,。；;：:！!？?\n\r\t（）()\[\]/|]+", raw)
    out: List[str] = []
    for chunk in chunks:
        for piece in re.split(r"(?:与|和|及|、|以及)", chunk):
            piece = piece.strip().strip("的了吧呢")
            piece = re.sub(r"^[A-Za-z0-9\s]+", "", piece)  # 去掉前面的英文/数字
            piece = re.sub(r"^(?:一个|一家|每个|每|所有|该|这个|那个)", "", piece)
            piece = re.sub(r"(?:怎么做|怎么|如何|是什么|多少|吗|呢|吧)$", "", piece)
            if 2 <= len(piece) <= 8 and piece not in STOPWORDS:
                out.append(piece)
    return out


def terms(text: str) -> Set[str]:
    """轻量分词：英文词 + 中文 2-4 字片段；去掉泛词。"""
    raw = str(text or "").lower()
    words = {w for w in WORD_RE.findall(raw) if w not in STOPWORDS}
    words.update(t for t in _cjk_terms(raw) if t not in STOPWORDS)
    return words


def product_profile(facts: Dict[str, Any]) -> Dict[str, Any]:
    """从 facts 提取产品关键词与目标人群标签（不引入外部词典）。"""
    product = facts.get("product") or {}
    icp = facts.get("icp") or []
    goal_texts = [str(g.get("name") or "") for g in facts.get("goals") or []]
    context_texts = [str(c.get("note") or "") for c in facts.get("context") or []]
    text_parts = [str(product.get("name") or ""), str(product.get("what") or "")]
    for row in icp:
        text_parts.append(str(row.get("hypothesis") or ""))
        text_parts.extend(str(t) for t in row.get("tags") or [])
    text_parts.extend(goal_texts)
    text_parts.extend(context_texts)
    keywords = set(terms(" ".join(text_parts)))
    for zh, aliases in ALIASES.items():
        if any(zh in str(keyword) for keyword in list(keywords)):
            keywords.update(aliases)
    audience = set()
    for row in icp:
        audience.update(terms(row.get("hypothesis") or ""))
        audience.update(str(t).lower() for t in row.get("tags") or [])
    return {"name": str(product.get("name") or ""),
            "url": str(product.get("url") or ""),
            "what": str(product.get("what") or ""),
            "keywords": keywords, "audience": audience,
            "value_events": [str(v.get("name") or "") for v in
                              facts.get("value_events") or []]}


def _keyword_hits(text: str, keywords: Iterable[str]) -> List[str]:
    low = str(text or "").lower()
    hits = []
    for word in keywords:
        word = str(word).strip()
        if word and (re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", low)
                     if word.isascii() else word in low):
            hits.append(word)
    return hits


def score_post(post: Dict[str, Any], profile: Dict[str, Any], *,
               account: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """给一条帖子 0–100 分，并给出可解释的理由。"""
    text = str(post.get("text") or "")
    low = text.lower()
    score = 0
    reasons: List[str] = []
    risks: List[str] = []

    hits = _keyword_hits(text, profile.get("keywords") or ())
    if hits:
        gained = min(40, 10 * len(hits))
        score += gained
        reasons.append(f"命中产品关键词 {len(hits)} 个（{'、'.join(hits[:4])}）")

    cues = [cue for cue in INTENT_CUES if cue in low]
    if cues:
        score += min(20, 8 * len(cues))
        reasons.append(f"有讨论/求助信号 {len(cues)} 个（{'、'.join(cues[:3])}）")
    if "?" in text or "？" in text:
        score += 8
        reasons.append("本身就是提问，回复有自然位置")

    length = len(text)
    if 40 <= length <= 280:
        score += 5
    elif length < 20:
        score -= 8
        risks.append("内容太短，缺少可回应的具体信息")

    engagement = post.get("engagement") or {}
    replies = int(engagement.get("replies") or 0)
    likes = int(engagement.get("likes") or 0)
    if 1 <= replies <= 30:
        score += 8
        reasons.append("已有少量讨论，适合加入而不是被淹没")
    if likes <= 500:
        score += 4
    if replies > 200 or likes > 5000:
        score -= 6
        risks.append("互动量过大，回复很容易被淹没")

    hours = post.get("age_hours")
    if isinstance(hours, (int, float)):
        if hours <= 24:
            score += 8
            reasons.append("24 小时内，时效性好")
        elif hours <= 72:
            score += 4
        else:
            score -= 6
            risks.append("超过 72 小时，讨论热度大概率已过")

    if account:
        bio = str(account.get("bio") or "")
        account_hits = _keyword_hits(bio, profile.get("audience") or ())
        if account_hits:
            score += min(12, 4 * len(account_hits))
            reasons.append(f"作者 bio 与目标人群相关（{'、'.join(account_hits[:3])}）")
        followers = account.get("followers")
        tier = account_tier(followers)
        if tier == "koc":
            score += 12
            reasons.append("作者是 KOC 量级：垂直声量、回复更可能被看到")
        elif tier == "micro":
            score += 6
        elif tier == "mid":
            score += 2
        elif tier == "macro":
            score -= 10
            risks.append("作者是头部大号，回复几乎不会被看到")
        elif tier == "nano":
            score += 2
            risks.append("作者粉丝很少，扩散有限（但对话意愿可能更高）")
        recent = " ".join(str(t) for t in account.get("recent_posts") or [])
        if recent:
            recent_hits = _keyword_hits(recent, profile.get("keywords") or ())
            if recent_hits:
                score += min(12, 4 * len(recent_hits))
                reasons.append(f"作者近期内容持续讨论同类问题"
                               f"（{'、'.join(recent_hits[:3])}）")
        if any(marker in bio.lower() for marker in PROMO_MARKERS):
            score -= 10
            risks.append("作者 bio 有营销号特征")

    if any(marker in low for marker in PROMO_MARKERS):
        score -= 15
        risks.append("帖子本身像广告/引流")

    score = max(0, min(100, score))
    tier = "A" if score >= 70 else "B" if score >= 50 else "C"
    return {"score": score, "tier": tier, "reasons": reasons,
            "risks": risks, "keyword_hits": hits}


def account_tier(followers: Optional[int]) -> str:
    """按粉丝量分档：nano / koc / mid / macro / unknown。"""
    if not isinstance(followers, int):
        return "unknown"
    if followers < MICRO_BAND[0]:
        return "nano"
    if followers < KOC_BAND[0]:
        return "micro"
    if followers <= KOC_BAND[1]:
        return "koc"
    if followers < BIG_ACCOUNT:
        return "mid"
    return "macro"


def koc_score(account: Optional[Dict[str, Any]],
              profile: Dict[str, Any]) -> Dict[str, Any]:
    """KOC 契合度：量级 + 垂直相关 + 不像营销号。只评账号，不评单条帖子。"""
    account = account or {}
    followers = account.get("followers")
    tier = account_tier(followers)
    score = 0
    reasons: List[str] = []
    risks: List[str] = []
    if tier == "koc":
        score += 30
        reasons.append("粉丝量在 KOC 区间（1k–50k）：垂直声量够，又没被商业化运营磨平")
    elif tier == "micro":
        score += 14
        reasons.append("小号（200–1k）：对话意愿高，但扩散有限")
    elif tier == "mid":
        score += 8
        reasons.append("中腰部（50k–300k）：能带量，但回复更容易被淹没")
    elif tier == "macro":
        score -= 12
        risks.append("头部大号：回复几乎不会被看到，早期不把产能花在这里")
    elif tier == "nano":
        score += 4
        risks.append("粉丝很少：可以对话，但短期不带来扩散")
    else:
        score += 2
        risks.append("粉丝数未知：先读一次主页再判断")

    bio = str(account.get("bio") or "")
    hits = _keyword_hits(bio, profile.get("audience") or ()) +         _keyword_hits(bio, profile.get("keywords") or ())
    if hits:
        score += min(18, 6 * len(set(hits)))
        reasons.append(f"bio 命中垂直关键词（{'、'.join(sorted(set(hits))[:3])}）")
    recent = " ".join(str(t) for t in account.get("recent_posts") or [])
    recent_hits = []
    if recent:
        recent_hits = _keyword_hits(recent, profile.get("keywords") or ())
        if recent_hits:
            score += min(12, 4 * len(recent_hits))
            reasons.append("近期内容持续讨论同类问题")
    if any(marker in bio.lower() for marker in PROMO_MARKERS):
        score -= 15
        risks.append("bio 有营销号特征（link in bio / DM 等）")
    if any(marker in recent.lower() for marker in PROMO_MARKERS):
        score -= 8
        risks.append("近期内容有引流特征")
    score = max(0, min(100, score))
    generic = {"ai", "business", "team", "product", "software", "cost", "budget", "growth"}
    bio_domain = {h for h in hits if h not in generic and len(h) >= 3}
    relevant_posts = sum(len({h for h in _keyword_hits(str(post), profile.get("keywords") or ())
                             if h not in generic and len(h) >= 3}) >= 2
                         for post in account.get("recent_posts") or [])
    vertical = bool(bio_domain) or relevant_posts >= 2
    if not vertical:
        risks.append("只有粉丝量证据，没有垂直领域背景或持续相关内容，暂不认作 KOC")
    from .targeting import score_candidate, TargetingError
    trust = None
    if type(followers) is int and followers > 0:
        try:
            trust = score_candidate({"followers": followers,
                "engagement_rate": account.get("engagement_rate"),
                "sponsored_recent": account.get("sponsored_recent"),
                "niche_match": vertical})
        except TargetingError:
            risks.append("信任评分输入无效：保留原始契合度，等待核实")
    return {"score": score, "tier": tier, "reasons": reasons, "risks": risks,
            "trust": trust,
            "is_koc": tier == "koc" and vertical, "vertical_evidence": vertical}


def score_account(posts: Sequence[Dict[str, Any]], profile: Dict[str, Any],
                  account: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """账号分 = 最佳帖子分 × 0.7 + 次佳/均值 × 0.3；空列表返回 C。"""
    if not posts:
        return {"score": 0, "tier": "C", "best_post": None,
                "reasons": ["没有可评分的帖子"], "risks": []}
    scored = []
    for post in posts:
        result = score_post(post, profile, account=account)
        scored.append(dict(post, fit=result))
    scored.sort(key=lambda row: -int(row["fit"]["score"]))
    best = scored[0]
    support = scored[1:3]
    support_score = (sum(int(r["fit"]["score"]) for r in support) / len(support)
                     if support else int(best["fit"]["score"]))
    total = round(int(best["fit"]["score"]) * 0.7 + support_score * 0.3)
    reasons = list(best["fit"]["reasons"])
    if support:
        reasons.append(f"另有 {len(support)} 条相关帖子，主题一致性较好")
    risks = list(best["fit"]["risks"])
    tier = "A" if total >= 70 else "B" if total >= 50 else "C"
    return {"score": total, "tier": tier, "best_post": best,
            "posts": scored, "reasons": reasons, "risks": risks}


def group_by_handle(rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        handle = str(row.get("handle") or "").strip().lstrip("@")
        if not handle:
            continue
        grouped.setdefault(handle, []).append(row)
    return grouped


def auto_queries(profile: Dict[str, Any], *, limit: int = 3) -> List[str]:
    """从产品关键词自动拼搜索词：优先领域短语，其次中文术语，最后长词。"""
    words = {str(k).lower() for k in profile.get("keywords") or ()}
    product = str(profile.get("name") or "").lower()
    phrases: List[str] = []
    if words.intersection({"ai", "token", "finops"}) and "invoice" in words:
        phrases.extend(["AI invoice reconciliation", "LLM billing usage", "FinOps AI costs"])
    pairs = (("invoice", "reconciliation"), ("cost", "allocation"),
             ("billing", "reconciliation"), ("ai", "spend"),
             ("vendor", "invoice"), ("token", "cost"))
    for first, second in pairs:
        if first in words and second in words:
            phrases.append(f"{first} {second}")
    cjk = [str(k) for k in profile.get("keywords") or ()
           if not str(k).isascii() and len(str(k)) >= 3 and str(k) != product]
    phrases.extend(cjk[:2])
    for word in sorted(words, key=lambda w: (-len(w), w)):
        if len(word) >= 5 and word != product and not any(word in p for p in phrases):
            phrases.append(word)
    seen, out = set(), []
    for phrase in phrases:
        if phrase and phrase not in seen:
            seen.add(phrase)
            out.append(phrase)
    return out[:max(1, int(limit))]


def preselect_handles(rows: Sequence[Dict[str, Any]], profile: Dict[str, Any],
                      *, limit: int = 8) -> List[str]:
    """第一段筛选：只按帖子分挑作者，避免把读主页配额花在无关账号上。"""
    scored: List[tuple] = []
    for handle, posts in group_by_handle(rows).items():
        best = max((score_post(post, profile)["score"] for post in posts),
                   default=0)
        scored.append((int(best), len(posts), handle))
    scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
    return [row[2] for row in scored[:max(0, int(limit))]]


def rank_prospects(rows: Sequence[Dict[str, Any]], profile: Dict[str, Any],
                   accounts: Optional[Dict[str, Dict[str, Any]]] = None,
                   *, koc_only: bool = False,
                   min_followers: Optional[int] = None,
                   max_followers: Optional[int] = None,
                   exclude_handles: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """按作者聚合、评分、排序；早期默认 KOC 优先。

    koc_only=True：只保留粉丝量在 KOC 区间（1k–50k）且能读到粉丝数的账号；
    粉丝数未知的不算 KOC（宁缺毋滥）。
    """
    accounts = accounts or {}
    skip = {str(h).lstrip("@").lower() for h in exclude_handles if h}
    out = []
    for handle, posts in group_by_handle(rows).items():
        if handle.lower() in skip:
            continue
        account = accounts.get(handle) or accounts.get("@" + handle) or {}
        followers = account.get("followers")
        koc = koc_score(account, profile)
        if koc_only and not koc["is_koc"]:
            continue
        if min_followers is not None and (not isinstance(followers, int)
                                          or followers < min_followers):
            continue
        if max_followers is not None and (not isinstance(followers, int)
                                          or followers > max_followers):
            continue
        result = score_account(posts, profile, account=account)
        best = result.get("best_post") or {}
        out.append({"handle": handle, "account": account,
                    "score": result["score"], "tier": result["tier"],
                    "koc_score": koc["score"], "koc_tier": koc["tier"],
                    "trust": koc["trust"],
                    "is_koc": koc["is_koc"],
                    "koc_reasons": koc["reasons"], "koc_risks": koc["risks"],
                    "reasons": result["reasons"], "risks": result["risks"],
                    "post_count": len(posts),
                    "best_post": {k: best.get(k) for k in
                                  ("url", "text", "age_hours", "engagement")},
                    "posts": result.get("posts") or []})
    # KOC 优先：先看是否 KOC，再看完契合度与相关帖数
    out.sort(key=lambda row: (0 if row.get("is_koc") else 1,
                              -int((row.get("trust") or {}).get("score", 0)),
                              -int(row["koc_score"]),
                              -int(row["score"]), -int(row["post_count"])))
    return out


def select_prospect_mix(rows, *, limit=8, koc_share=0.6, koc_only=False):
    """从已经过契合度排序的候选中分配名额；缺一类时用另一类补足。"""
    limit = max(0, int(limit))
    kocs = [r for r in rows if r.get("is_koc")]
    others = [r for r in rows if not r.get("is_koc")]
    if koc_only:
        return kocs[:limit]
    quota = int(limit * max(0.0, min(1.0, float(koc_share))) + 0.5)
    chosen = kocs[:quota] + others[:limit - quota]
    used = {str(r.get("handle", "")).lower() for r in chosen}
    for row in rows:
        if len(chosen) >= limit:
            break
        if str(row.get("handle", "")).lower() not in used:
            chosen.append(row)
            used.add(str(row.get("handle", "")).lower())
    return chosen


# ---------------------------------------------------------------------------
# 线上补充：读作者主页（只读）
# ---------------------------------------------------------------------------

FOLLOWERS_RE = re.compile(r"([\d][\d,]*(?:\.\d+)?)\s*(万|K|M)?\s*(?:位)?(?:关注者|Followers)",
                          re.I)


def parse_account_text(text: str) -> Dict[str, Any]:
    """从个人主页文本里取 bio、粉丝数、近期内容（离线可测）。"""
    body = str(text or "")
    follower_match = FOLLOWERS_RE.search(body)
    followers = None
    if follower_match:
        raw = follower_match.group(1).replace(",", "")
        suffix = (follower_match.group(2) or "").upper()
        try:
            value = float(raw)
            value *= {"K": 1_000, "M": 1_000_000, "万": 10_000}.get(suffix, 1)
            followers = int(value)
        except ValueError:
            followers = None
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    bio = ""
    for line in lines:
        if len(line) >= 20 and not line.startswith("@") and "加入" not in line:
            bio = line[:300]
            break
    return {"bio": bio, "followers": followers,
            "raw_excerpt": " | ".join(lines[:20])[:800]}


def enrich_account(handle: str, *, cdp_url: Optional[str] = None,
                   sleep_seconds: float = 2.0, log=print) -> Dict[str, Any]:
    """打开作者主页读 bio/粉丝/近期帖子。只读，失败返回 note 不抛。"""
    from . import cdp
    handle = str(handle or "").lstrip("@").strip()
    if not handle:
        raise FitError("handle 为空")
    browser, page = cdp.connect(cdp_url)
    try:
        ok = cdp.safe_goto(page, f"https://x.com/{handle}", log=log)
        if not ok:
            return {"handle": handle, "note": "主页打不开"}
        page.wait_for_timeout(3500)
        body = page.inner_text("body")
        parsed = parse_account_text(body)
        try:
            parsed["bio"] = page.locator('[data-testid="UserDescription"]').first.inner_text(timeout=2000)
        except Exception:
            parsed["bio"] = ""  # Never substitute a recent post for the profile bio.
        recent = []
        try:
            for loc in page.locator('[data-testid="tweetText"]').all()[:5]:
                recent.append(loc.inner_text()[:200])
        except Exception:  # noqa: BLE001
            pass
        parsed.update({"handle": handle, "recent_posts": recent})
        return parsed
    finally:
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass
        if sleep_seconds:
            time.sleep(max(0.0, float(sleep_seconds)))


def save_prospects(data_dir, run_id: str, rows: List[Dict[str, Any]],
                   *, on: str) -> Path:
    directory = Path(data_dir) / "grow" / run_id / "prospects"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{on}.json"
    path.write_text(json.dumps({"run": run_id, "on": on, "count": len(rows),
                                "prospects": rows}, ensure_ascii=False,
                               indent=1) + "\n", encoding="utf-8")
    return path


def render(rows: List[Dict[str, Any]], *, limit: int = 10) -> str:
    if not rows:
        return "没有候选账号（放宽搜索条件或换关键词）"
    lines = [f"候选账号 {len(rows)} 个（按契合度排序；只读发现，不发送）", ""]
    for row in rows[:limit]:
        account = row.get("account") or {}
        followers = account.get("followers")
        followers_text = f"{followers} 粉丝" if followers is not None else "粉丝数未知"
        koc_tag = "KOC " if row.get("is_koc") else ""
        lines.append(f"[{row['tier']}] {koc_tag}@{row['handle']}（{row['score']} 分｜"
                     f"{followers_text}｜{row['post_count']} 条相关）")
        if account.get("bio"):
            lines.append(f"    bio：{account['bio'][:120]}")
        for reason in row.get("reasons") or []:
            lines.append(f"    + {reason}")
        for risk in row.get("risks") or []:
            lines.append(f"    - {risk}")
        best = row.get("best_post") or {}
        if best.get("text"):
            lines.append(f"    最佳帖子：{best['text'][:140]}")
            lines.append(f"    {best.get('url', '')}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# KOC 名单：跨天保留，避免每天重复触达同一批人
# ---------------------------------------------------------------------------

def load_watchlist(data_dir, run_id: str) -> Dict[str, Dict[str, Any]]:
    path = Path(data_dir) / "grow" / run_id / "koc_watchlist.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return dict(data.get("accounts") or {})


def update_watchlist(data_dir, run_id: str, prospects: Sequence[Dict[str, Any]],
                     *, on: str, engaged: Iterable[str] = ()) -> Path:
    """把本轮候选并入名单：保留首次出现、更新最后出现/分数/触达状态。"""
    path = Path(data_dir) / "grow" / run_id / "koc_watchlist.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    accounts = load_watchlist(data_dir, run_id)
    engaged_set = {str(h).lstrip("@").lower() for h in engaged if h}
    for row in prospects:
        handle = str(row.get("handle") or "").lstrip("@")
        if not handle:
            continue
        current = accounts.get(handle) or {"handle": handle, "first_seen": on}
        current.update({
            "handle": handle,
            "tier": row.get("tier"),
            "koc_tier": row.get("koc_tier"),
            "is_koc": bool(row.get("is_koc")),
            "score": row.get("score"),
            "koc_score": row.get("koc_score"),
            "followers": (row.get("account") or {}).get("followers"),
            "bio": (row.get("account") or {}).get("bio"),
            "best_url": (row.get("best_post") or {}).get("url"),
            "last_seen": on,
        })
        if handle.lower() in engaged_set:
            current["last_engaged_at"] = on
            current["touches"] = int(current.get("touches") or 0) + 1
        accounts[handle] = current
    path.write_text(json.dumps({"run": run_id, "updated_at": on,
                                "accounts": accounts}, ensure_ascii=False,
                               indent=1) + "\n", encoding="utf-8")
    return path


def mark_engaged(data_dir, run_id: str, handle: str, *, on: str,
                 meta: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """stage 一条内容时记一次触达：名单里累加 touches / last_engaged_at。"""
    handle = str(handle or "").lstrip("@").strip()
    if not handle:
        return None
    path = Path(data_dir) / "grow" / run_id / "koc_watchlist.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    accounts = load_watchlist(data_dir, run_id)
    row = accounts.get(handle) or {"handle": handle, "first_seen": on}
    row.update({k: v for k, v in (meta or {}).items() if v is not None})
    row["last_engaged_at"] = on
    row["touches"] = int(row.get("touches") or 0) + 1
    accounts[handle] = row
    path.write_text(json.dumps({"run": run_id, "updated_at": on,
                                "accounts": accounts}, ensure_ascii=False,
                               indent=1) + "\n", encoding="utf-8")
    return path


def render_watchlist(accounts: Dict[str, Dict[str, Any]], *, limit: int = 30) -> str:
    if not accounts:
        return "名单为空：先跑 mkt grow prospects"
    rows = sorted(accounts.values(), key=lambda row: (
        0 if row.get("last_engaged_at") else 1,
        -int(row.get("score") or 0)))
    lines = [f"KOC 名单 {len(accounts)} 人（已触达的排前面）", ""]
    for row in rows[:limit]:
        touched = f"｜已触达 {row.get('last_engaged_at')}（{row.get('touches', 0)} 次）" \
            if row.get("last_engaged_at") else "｜未触达"
        followers = row.get("followers")
        lines.append(f"@{row['handle']}（{row.get('tier')}｜{row.get('score')} 分｜"
                     f"{followers if followers is not None else '粉丝未知'}{touched}）")
    return "\n".join(lines)
