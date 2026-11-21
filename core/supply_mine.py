"""供给发现：把「今晚回什么、发什么」变成可复核的候选，写进 supply-pool。

三路来源：
  github   公开 issue（gh api search），只要未关闭、非 PR、无人认领、评论少的；
  x        搜索时间线（复用操作者已打开的 X 标签页），抽作者、正文、时间与互动数；
  content  内容角度卡：把人族事实包里的真实工件整理成「可以写什么」，不是成稿。

只读：不关注、不点赞、不发送、不改队列；写池子走 supply_pool.merge 的原子写。
平台文本是不可信数据，只作为「回什么」的依据，不当作事实直接转述。
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import quote

from . import grow_fit, supply_pool

GH_WINDOW_DAYS = 14

# 搜索词按产品事实定：只放能定位到具体问题的人话术语。
# 不用「AI」「产品」这类泛词，否则搜回来的都是噪音。
GH_TOPICS = (
    '"AI invoice" reconciliation',
    '"llm cost" tracking',
    '"token accounting"',
    '"agent observability"',
    '"llm evals"',
    '"agent evals"',
    '"context engineering"',
    '"usage based billing"',
    'finops "llm" cost',
    '"AI spend"',
    '"customer discovery" product',
    '"delivery verification"',
)

# X 上的「token」「agent」大量指向加密代币与币圈项目，单用会挖回稳定币、代币经济的帖子。
# 查询串一律带上 AI 侧的具体词，抽取后再过一道相关性门槛。
X_QUERIES = (
    '"llm cost" -filter:replies lang:en',
    '"llm spend" openai lang:en',
    '"api bill" openai lang:en',
    '"agent observability" lang:en',
    '"llm evals" lang:en',
    '"prompt caching" cost lang:en',
    '"ai invoice" lang:en',
    'finops llm lang:en',
    '"context window" cost lang:en',
    '"usage-based billing" ai lang:en',
    '"token usage" claude lang:en',
    '"cost per request" llm lang:en',
    '"openai api" cost lang:en',
    '"anthropic api" cost lang:en',
    '"inference cost" lang:en',
    '"embedding cost" lang:en',
    '"cost per token" lang:en',
    '"model routing" cost lang:en',
    '"batch api" cost lang:en',
    '"cached tokens" lang:en',
    '"token spend" lang:en',
    '"ai unit economics" lang:en',
    '"llm monitoring" lang:en',
    '"agent trace" lang:en',
)

# 相关性门槛：至少命中一个 AI 侧强特征，否则当作无关内容丢弃。
X_AI_MARKERS = (
    "llm", "gpt", "openai", "anthropic", "claude", "gemini", "llama", "mistral",
    "inference", "embedding", "fine-tun", "llmops", "context window", "langchain",
    "mcp server", "prompt caching", "token usage", "usage-based billing",
    "api bill", "evals", "agent observability", "vector database", "rag pipeline",
    "ai spend", "ai cost", "finops", "copilot", "cursor",
    "cost per token", "token spend", "inference cost", "embedding cost",
    "model routing", "batch api", "cached token", "eval harness", "agent trace",
    "llm monitoring", "unit economics", "gpu cost", "gpu bill",
)
X_OFFTOPIC_MARKERS = (
    "stablecoin", "blockchain", "defi", "nft", "memecoin", "presale", "staking",
    "onchain", "on-chain", "crypto", "web3", "tokenomics", "whitepaper",
    "nasdaq", "nvda", "etf", "token utility", "airdrop",
)

_PR_RE = re.compile(r"\b(rate limit|secondary rate|abuse detection)\b", re.I)


def x_is_on_topic(text: str) -> bool:
    """X 抽回的正文是否真的在谈 AI 用量/成本，而不是币圈或行情。"""
    low = str(text or "").lower()
    if not low:
        return False
    if not any(marker in low for marker in X_AI_MARKERS):
        return False
    if any(marker in low for marker in X_OFFTOPIC_MARKERS):
        # 币圈词与 AI 词同现时仍可能是噪音，只有明确谈成本/账单/评测才保留。
        keep = ("cost", "bill", "invoice", "spend", "pricing", "eval",
                "observability", "latency")
        return any(word in low for word in keep)
    return True


def _parse_time(text: str) -> Optional[datetime]:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        got = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if got.tzinfo is None:
        got = got.replace(tzinfo=timezone.utc)
    return got


def gh_queries(days: int = GH_WINDOW_DAYS, *, today: Optional[date] = None,
               topics: Sequence[str] = GH_TOPICS) -> List[str]:
    """生成 GitHub 搜索串：未关闭、非 PR 由调用方过滤，评论数限制在 query 里。"""
    today = today or date.today()
    since = (today - timedelta(days=max(1, int(days)))).isoformat()
    out = []
    for topic in topics:
        out.append("is:issue is:open comments:0..2 no:assignee created:>%s %s"
                   % (since, topic))
    return out


def _is_promo(text: str) -> bool:
    low = str(text or "").lower()
    return any(marker in low for marker in grow_fit.PROMO_MARKERS)


def _github_row(item: Dict[str, Any], query: str) -> Optional[Dict[str, Any]]:
    """把 gh search 结果转成池候选；PR、已关闭、自家仓库直接丢弃。"""
    if item.get("is_pr"):
        return None
    repo = str(item.get("repo") or "")
    if repo.lower().startswith("roy-tong/"):
        return None
    url = str(item.get("url") or "")
    if not url:
        return None
    title = str(item.get("title") or "").strip()
    body = str(item.get("body") or "").strip()
    combined = (title + "\n\n" + body).strip()
    if _is_promo(combined):
        return None
    return {
        "channel": "github",
        "action": "reply",
        "target": url,
        "actor": str(item.get("author") or ""),
        "title": title,
        "context": combined,
        "published_at": str(item.get("created_at") or ""),
        "engagement": {"replies": int(item.get("comments") or 0)},
        "tags": ["github-search", repo] if repo else ["github-search"],
        "source": "gh-search:%s" % query[:60],
    }


def mine_github(data_dir, *, queries: Optional[Sequence[str]] = None,
                per_query: int = 20, gap: float = 3.0, days: int = GH_WINDOW_DAYS,
                now: Optional[datetime] = None, profile: Optional[Dict[str, Any]] = None,
                ledger=None, sleep=time.sleep,
                search=None) -> Dict[str, Any]:
    """跑 GitHub 搜索并合并入池；返回逐条查询的报告。

    search 参数只为测试注入，默认走 core.bridge.gh_post.search（gh CLI）。
    """
    if search is None:
        from .bridge.gh_post import search as search  # 延迟导入：离线测试不碰 gh
    queries = list(queries or gh_queries(days))
    now = now or datetime.now().astimezone()
    report: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []
    for index, query in enumerate(queries):
        if index and gap:
            sleep(float(gap))
        got = search(query, int(per_query))
        if not got.get("ok"):
            err = str(got.get("err") or "")
            retried = False
            if _PR_RE.search(err):
                sleep(60)
                got = search(query, int(per_query))
                retried = True
            if not got.get("ok"):
                report.append({"query": query[:80], "ok": False,
                               "error": err[:200], "retried": retried})
                continue
        items = got.get("items") or []
        rows = [row for row in (_github_row(it, query) for it in items) if row]
        all_rows.extend(rows)
        report.append({
            "query": query[:80],
            "ok": True,
            "total": got.get("total"),
            "returned": len(items),
            "kept": len(rows),
            "skipped_pr": sum(1 for it in items if it.get("is_pr")),
        })
    merged = supply_pool.merge(data_dir, all_rows, source="gh-search",
                               now=now, profile=profile, ledger=ledger,
                               build_note={"kind": "github", "at": now.isoformat(timespec="seconds"),
                                           "queries": len(queries), "kept": len(all_rows)})
    return {"ok": bool(merged.get("ok")), "merge": merged,
            "rows": len(all_rows), "queries": report}


# ---- X -------------------------------------------------------------------

X_EXTRACT = r"""
(() => {
  const out = [];
  const seen = new Set();
  const num = (sel) => {
    const el = document.querySelector(sel);
    return el;
  };
  const count = (art, sel) => {
    const el = art.querySelector(sel);
    if (!el) return 0;
    const label = String(el.getAttribute('aria-label') || el.innerText || '');
    const m = label.replace(/,/g, '').match(/([\d.]+)\s*([KMB万千]?)/i);
    if (!m) return 0;
    const n = parseFloat(m[1]);
    const unit = (m[2] || '').toUpperCase();
    const scale = unit === 'K' || unit === '千' ? 1e3
      : unit === 'M' || unit === '百' ? 1e6
      : unit === 'B' ? 1e9 : unit === '万' ? 1e4 : 1;
    return Math.round(n * scale);
  };
  for (const art of document.querySelectorAll('article[data-testid="tweet"]')) {
    const links = Array.from(art.querySelectorAll('a[href*="/status/"]'));
    if (!links.length) continue;
    const link = links[0];
    let url;
    try { url = new URL(link.getAttribute('href'), location.origin); } catch (e) { continue; }
    const m = url.pathname.match(/^\/([A-Za-z0-9_]+)\/status\/(\d+)/);
    if (!m) continue;
    const id = m[2];
    if (seen.has(id)) continue;
    seen.add(id);
    const box = art.querySelector('[data-testid="User-Name"]');
    const hm = box ? String(box.innerText || '').match(/@([A-Za-z0-9_]{1,15})/) : null;
    const textEl = art.querySelector('[data-testid="tweetText"]');
    const t = art.querySelector('time[datetime]');
    out.push({
      handle: m[1] || (hm ? hm[1] : ''),
      id: id,
      url: 'https://x.com/' + m[1] + '/status/' + id,
      text: textEl ? String(textEl.innerText || '') : '',
      at: t ? String(t.getAttribute('datetime') || '') : '',
      replies: count(art, '[data-testid="reply"]'),
      likes: count(art, '[data-testid="like"]')
    });
  }
  return out;
})()
"""


def x_rows(posts: Sequence[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """X 抽取结果转池候选；转推/引用指向别人的帖子按原文链接去重。"""
    rows: List[Dict[str, Any]] = []
    for post in posts:
        text = str(post.get("text") or "").strip()
        url = str(post.get("url") or "").strip()
        handle = str(post.get("handle") or "").strip()
        if not url or not text or not handle:
            continue
        if _is_promo(text):
            continue
        if not x_is_on_topic(text):
            continue
        rows.append({
            "channel": "x",
            "action": "reply",
            "target": url,
            "actor": handle,
            "context": text,
            "published_at": str(post.get("at") or ""),
            "engagement": {"replies": int(post.get("replies") or 0),
                           "likes": int(post.get("likes") or 0)},
            "tags": ["x-search"],
            "source": "x-search:%s" % query[:60],
        })
    return rows


def mine_x(data_dir, *, queries: Optional[Sequence[str]] = None,
           limit: int = 12, session: Optional[str] = None, gap: float = 2.0,
           now: Optional[datetime] = None, profile: Optional[Dict[str, Any]] = None,
           ledger=None, sleep=time.sleep, attach=None) -> Dict[str, Any]:
    """在已打开的 X 标签页上跑搜索并合并入池；找不到标签页返回 tab_not_found。"""
    from .browser_attach import BridgeError, BrowserAttach

    queries = list(queries or X_QUERIES)
    now = now or datetime.now().astimezone()
    bridge = attach or BrowserAttach(session)
    try:
        tab = bridge.attach_site("x", prefer_visible=True)
    except BridgeError as exc:
        return {"ok": False, "reason": exc.code, "message": exc.message,
                "queries": [], "rows": 0}
    identity = ""
    try:
        identity = str((bridge.observe_identity("x", tab) or {}).get("screen_name") or "")
    except Exception:
        identity = ""

    report: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []
    for index, query in enumerate(queries):
        if index and gap:
            sleep(float(gap))
        url = "https://x.com/search?q=%s&f=live" % quote(query)
        try:
            bridge.route_spa(tab, url)
        except Exception as exc:
            report.append({"query": query[:80], "ok": False, "error": str(exc)[:200]})
            continue
        posts: List[Dict[str, Any]] = []
        seen = set()
        for attempt in range(8):
            sleep(0.5 if attempt < 2 else 0.8)
            try:
                got = bridge.eval(tab, X_EXTRACT)
            except Exception as exc:
                report.append({"query": query[:80], "ok": False, "error": str(exc)[:200]})
                got = None
            if isinstance(got, list):
                for post in got:
                    if post.get("id") and post["id"] not in seen:
                        seen.add(post["id"])
                        posts.append(post)
            if len(posts) >= limit:
                break
            if attempt in (2, 4):
                try:
                    bridge.eval(tab, "window.scrollBy(0, 1200); true")
                except Exception:
                    pass
        rows = x_rows(posts[:limit], query)
        all_rows.extend(rows)
        report.append({"query": query[:80], "ok": True,
                       "returned": len(posts), "kept": len(rows)})
    merged = supply_pool.merge(data_dir, all_rows, source="x-search",
                               now=now, profile=profile, ledger=ledger,
                               build_note={"kind": "x", "at": now.isoformat(timespec="seconds"),
                                           "identity": identity,
                                           "queries": len(queries), "kept": len(all_rows)})
    koc = {"ok": False, "reason": "no_rows"} if not all_rows else supply_pool.koc_merge(
        data_dir,
        [{"handle": row["actor"], "best_url": row["target"],
          "topics": [str(t) for t in row.get("tags") or [] if not str(t).startswith("x-")]}
         for row in all_rows],
        source="x-search", now=now, profile=profile)
    return {"ok": bool(merged.get("ok")), "identity": identity, "tab": tab,
            "merge": merged, "rows": len(all_rows), "koc": koc, "queries": report}


# ---- 内容角度卡 -----------------------------------------------------------

# 每张卡是「可以写什么」的角度 + 可核对的事实依据，不是成稿。
# 写成 3 条以上时再拆；同一角度不在一夜里重复用。
ANGLE_SEEDS = (
    {
        "slug": "invoice-rebuild",
        "title": "把 AI 供应商账单拆到每个任务",
        "context": "事实：Verovo 做过账单重构，把多月 AI 账单按任务重算单位成本。"
                   "角度：账单上的一行 API 费用，怎么落到每个具体任务；"
                   "哪些费用无法归因，只能单列。",
        "tags": ["verovo", "token-accounting"],
        "channels": ["x", "discord"],
    },
    {
        "slug": "tool-audit-110",
        "title": "110 个工具审计：哪些在用、哪些只占预算",
        "context": "事实：做过 110 个工具的用量审计，按实际调用与账单对账。"
                   "角度：审计时先看哪三个数、哪些工具看起来在用其实没有调用。",
        "tags": ["verovo", "finops"],
        "channels": ["x", "discord"],
    },
    {
        "slug": "evidence-grades",
        "title": "研究结论的证据分级 E0–E5",
        "context": "事实：Askovo 的研究链给每条结论标证据等级，E0 是假设，E5 是可复核的外部证据。"
                   "角度：为什么把 E0 当结论用会做错产品决定；一条结论升级到 E3 需要什么。",
        "tags": ["askovo", "research"],
        "channels": ["x", "discord"],
    },
    {
        "slug": "reviewable-ledger",
        "title": "自动执行要留下可复核的回执",
        "context": "事实：Growovo 的每次执行都写账本：动作、目标、真实回执、结果状态。"
                   "角度：自动化里最贵的不是发出去，而是发出去之后能不能证明发到了哪。"
                   "unknown-result 为什么要停下来等人核。",
        "tags": ["growovo", "agent"],
        "channels": ["x", "discord"],
    },
    {
        "slug": "reply-no-link",
        "title": "回复不带链接的互动纪律",
        "context": "事实：互动纪律里写死「回复不带链接」，避免被当作推广。"
                   "角度：不带链接时怎么让感兴趣的人找到项目；这条纪律牺牲了什么。",
        "tags": ["growovo", "growth"],
        "channels": ["x", "discord"],
    },
    {
        "slug": "night-automation-limits",
        "title": "夜间自动化的边界：哪些动作必须停",
        "context": "事实：夜间执行有审批、dry-run、暂停、额度、抑制名单与 unknown-result 保护。"
                   "角度：一条动作在什么情况下必须停在「等人核」，而不是自动重试。",
        "tags": ["growovo", "automation"],
        "channels": ["x", "discord"],
    },
    {
        "slug": "zero-paid-customers",
        "title": "开源项目的真实读数：221 stars、0 付费",
        "context": "事实：三仓库合计约 221 stars，付费客户 0。"
                   "角度：把这两个数放在一起看，说明现在验证的是什么、没验证的是什么。",
        "tags": ["family", "build-in-public"],
        "channels": ["x", "discord"],
    },
    {
        "slug": "cost-per-verified-success",
        "title": "每个「核验通过的成功」花多少钱",
        "context": "事实：Verovo 的口径是 cost per verified success，而不是每次调用成本。"
                   "角度：分母从调用换成核验通过，会给采购和续约带来什么变化。",
        "tags": ["verovo", "cost"],
        "channels": ["x", "discord"],
    },
    {
        "slug": "customer-discovery-chain",
        "title": "需求验证为什么要留证据链",
        "context": "事实：Askovo 的主链是 scene→user→pain→need→feature→product→business，逐节点留证据。"
                   "角度：跳到 feature 直接做，通常在哪个节点上出错；怎么用一次访谈验证两个节点。",
        "tags": ["askovo", "discovery"],
        "channels": ["x", "discord"],
    },
    {
        "slug": "delivery-verification",
        "title": "交付验收：供应商说做完了，怎么核",
        "context": "事实：Verovo 做交付核验，把合同承诺与可观察交付物对起来。"
                   "角度：验收时先固定哪几个可复核字段；没有读数的承诺怎么写进验收单。",
        "tags": ["verovo", "verification"],
        "channels": ["x", "discord"],
    },
    {
        "slug": "token-budget-per-feature",
        "title": "给每个功能设 token 预算",
        "context": "事实：token accounting 的实际做法是把用量按功能、按任务归集。"
                   "角度：没有预算时成本在哪儿漏；设了预算后哪类功能最先被砍。",
        "tags": ["verovo", "token-accounting"],
        "channels": ["x"],
    },
    {
        "slug": "agent-evals-before-scale",
        "title": "扩量前先有 eval，不是先有流量",
        "context": "事实：agent observability 与 evals 是可复核的扩量前提。"
                   "角度：没有 eval 的 agent 扩量，失败会以什么形式出现；最小 eval 集包含什么。",
        "tags": ["growovo", "evals"],
        "channels": ["x"],
    },
)


def content_angles(*, channels: Sequence[str] = ("x", "discord"),
                   discord_ids: Sequence[str] = (),
                   seeds: Sequence[Dict[str, Any]] = ANGLE_SEEDS) -> List[Dict[str, Any]]:
    """把角度种子转成池候选；Discord 必须落到 policy 允许的频道 id。"""
    rows: List[Dict[str, Any]] = []
    discord_ids = [str(x) for x in discord_ids if str(x).strip()]
    used = 0
    for seed in seeds:
        for channel in seed.get("channels") or ():
            if channel not in channels:
                continue
            row = {
                "key": "content:%s:publish:%s" % (channel, seed["slug"]),
                "channel": channel,
                "action": "publish",
                "target": "",
                "actor": "",
                "title": seed["title"],
                "context": seed["context"],
                "tags": ["angle"] + list(seed.get("tags") or []),
                "source": "content-angle",
            }
            if channel == "discord":
                if not discord_ids:
                    continue
                row["target"] = discord_ids[used % len(discord_ids)]
                used += 1
                row["tags"].append("channel-id-unverified")
            if channel == "x":
                row["tags"].append("text-not-written")
            rows.append(row)
    return rows


def mine_content(data_dir, *, policy: Optional[Dict[str, Any]] = None,
                 channels: Sequence[str] = ("x", "discord"),
                 seeds: Sequence[Dict[str, Any]] = ANGLE_SEEDS,
                 now: Optional[datetime] = None,
                 profile: Optional[Dict[str, Any]] = None,
                 ledger=None) -> Dict[str, Any]:
    """合并内容角度卡；不动队列，正文仍由波次前的准备任务写。"""
    now = now or datetime.now().astimezone()
    policy = policy or {}
    ids = (((policy.get("channels") or {}).get("discord_allowed_ids")) or [])
    rows = content_angles(channels=channels, discord_ids=ids, seeds=seeds)
    merged = supply_pool.merge(data_dir, rows, source="content-angle",
                               now=now, profile=profile, ledger=ledger,
                               build_note={"kind": "content",
                                           "at": now.isoformat(timespec="seconds"),
                                           "angles": len(rows)})
    return {"ok": bool(merged.get("ok")), "merge": merged, "rows": len(rows)}
