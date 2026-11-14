#!/usr/bin/env python3
"""渠道：不是「选平台」，是「选具体的地方、具体的人、具体的规则」。

平台注册表回答「这个平台能不能做」；本模块回答「这一轮到底去哪个帖子、
哪个群、哪个 newsletter，为什么，能带多少对话，什么时候停」。

三个关键区分：

1. **平台 ≠ 渠道**：Reddit 是平台，r/FinOps 是渠道；LinkedIn 是平台，
   FinOps/FP&A 群是渠道。渠道必须具体到「人已经在哪讨论」。
2. **借场 ≠ 发广告**：借场是先答问题、再给方法；promo=banned 的地方只能听和答，
   不能发产品帖。
3. **经济学是假设，不是预测**：触达→回复→合格→样本→付费，每一层都是区间；
   没有实测之前，不许当成 forecast。

输出：渠道组合（主/集中发布/辅助/旁听）、容量、经济学区间、预注册实验、红线。
只读，不写平台、不发消息。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .expression import plain_output
from . import gtm

SCHEMA = "growovo.channel-plan/1"

ACCESS_SCORE = {"public": 2, "join": 1, "login": 0, "paid": 0}
PROMO_SCORE = {"allowed": 1, "limited": 0, "banned": -3}

# 渠道库：每个条目都必须写清楚「谁在里面、能不能发、发什么、多久见效、风险」。
# size 是量级，不是精确数；evidence 是这条渠道被选中的依据，不是效果证明。
VENUES: List[Dict[str, Any]] = [
    {
        "id": "reddit-finops", "platform": "reddit", "name": "r/FinOps",
        "url": "https://www.reddit.com/r/FinOps/",
        "audience": ["b2b-buyers"], "size": 60_000, "access": "public",
        "promo": "limited", "formats": ["question", "method post", "comment"],
        "signal_density": 3, "competition": 2, "speed": 3, "risk": 2,
        "role_hint": "primary",
        "evidence": "2026H2 10+ 条第一人称「分不到团队/对不上发票」",
        "notes": "厂商帖敏感；先答问题，再提方法；一天最多 1 帖 + 3 答。",
    },
    {
        "id": "reddit-cfo", "platform": "reddit", "name": "r/CFO",
        "url": "https://www.reddit.com/r/CFO/",
        "audience": ["b2b-buyers"], "size": 120_000, "access": "public",
        "promo": "limited", "formats": ["question", "comment"],
        "signal_density": 2, "competition": 2, "speed": 2, "risk": 2,
        "role_hint": "support",
        "evidence": "WTP/控制类讨论；预算方与接收方",
        "notes": "财务视角；不贴价格；先回答控制/关账问题。",
    },
    {
        "id": "reddit-aiagents", "platform": "reddit", "name": "r/AI_Agents",
        "url": "https://www.reddit.com/r/AI_Agents/",
        "audience": ["developers", "technical-founders"], "size": 150_000,
        "access": "public", "promo": "allowed",
        "formats": ["question", "method post", "comment"],
        "signal_density": 3, "competition": 2, "speed": 3, "risk": 1,
        "role_hint": "support",
        "evidence": "「给客户跑 agent 怎么拆 token 成本」类第一人称",
        "notes": "AI 交付方；允许披露后发方法；不群发。",
    },
    {
        "id": "hackernews", "platform": "hackernews", "name": "Hacker News (Show HN)",
        "url": "https://news.ycombinator.com/show",
        "audience": ["developers", "technical-founders"], "size": 5_000_000,
        "access": "public", "promo": "allowed",
        "formats": ["show hn", "comment"],
        "signal_density": 2, "competition": 3, "speed": 3, "risk": 3,
        "role_hint": "launch",
        "evidence": "Show HN 是自有项目的一次性集中发布；不背获客指标",
        "notes": "一次机会；标题具体、首评给方法；被 flag 不重发。",
    },
    {
        "id": "finops-foundation-slack", "platform": "community",
        "name": "FinOps Foundation Slack", "url": "https://www.finops.org/",
        "audience": ["b2b-buyers"], "size": 20_000, "access": "join",
        "promo": "banned", "formats": ["question", "answer"],
        "signal_density": 3, "competition": 1, "speed": 2, "risk": 2,
        "role_hint": "ambient",
        "evidence": "FinOps 从业者主聚集地；版规通常禁止推广",
        "notes": "只能听和答；先答 2 个问题，再发自己的问题；不发链接。",
    },
    {
        "id": "mlops-community", "platform": "community",
        "name": "MLOps Community Slack", "url": "https://mlops.community/",
        "audience": ["developers", "technical-founders"], "size": 30_000,
        "access": "join", "promo": "limited",
        "formats": ["question", "answer"],
        "signal_density": 2, "competition": 1, "speed": 2, "risk": 1,
        "role_hint": "support",
        "evidence": "LLM 基础设施/成本讨论；技术语气",
        "notes": "只发方法/问题；被问再给链接。",
    },
    {
        "id": "latent-space", "platform": "community", "name": "Latent Space Discord",
        "url": "https://www.latent.space/",
        "audience": ["developers", "technical-founders"], "size": 20_000,
        "access": "join", "promo": "limited",
        "formats": ["question", "answer"],
        "signal_density": 2, "competition": 1, "speed": 2, "risk": 1,
        "role_hint": "support",
        "evidence": "AI 工程师社区；成本/推理讨论",
        "notes": "技术语气；不发产品名。",
    },
    {
        "id": "linkedin-finops", "platform": "linkedin",
        "name": "LinkedIn FinOps/FP&A", "url": "https://www.linkedin.com/",
        "audience": ["b2b-buyers", "product-managers"], "size": 200_000,
        "access": "login", "promo": "limited",
        "formats": ["post", "comment", "connection note"],
        "signal_density": 2, "competition": 2, "speed": 1, "risk": 2,
        "role_hint": "support",
        "evidence": "职业身份可核；冷连接接受率低",
        "notes": "1 帖/天；连接 ≤10/天；备注 ≤200 字符；不群发。",
    },
    {
        "id": "indie-hackers", "platform": "indie-hackers", "name": "Indie Hackers",
        "url": "https://www.indiehackers.com/",
        "audience": ["technical-founders"], "size": 500_000, "access": "public",
        "promo": "allowed", "formats": ["post", "comment"],
        "signal_density": 1, "competition": 2, "speed": 2, "risk": 1,
        "role_hint": "not_now",
        "evidence": "独立开发者；与 FinOps 买家重合低",
        "notes": "除非目标是独立开发者，否则不优先。",
    },
    {
        "id": "finops-newsletter", "platform": "newsletter",
        "name": "FinOps newsletter / 顾问转发", "url": "",
        "audience": ["b2b-buyers"], "size": 20_000, "access": "paid",
        "promo": "allowed", "formats": ["sponsorship", "guest post"],
        "signal_density": 2, "competition": 1, "speed": 2, "risk": 1,
        "role_hint": "ambient",
        "evidence": "借场：别人的受众；需要付费或关系",
        "notes": "先问能不能转载/引用，不买硬广；有预算再考虑。",
    },
    {
        "id": "finops-x", "platform": "community", "name": "FinOps X / 本地 meetup",
        "url": "https://www.finops.org/events/",
        "audience": ["b2b-buyers"], "size": 5_000, "access": "paid",
        "promo": "limited", "formats": ["talk", "hallway"],
        "signal_density": 3, "competition": 1, "speed": 1, "risk": 1,
        "role_hint": "ambient",
        "evidence": "高信任、慢；适合第二季度",
        "notes": "先听，不推销；换名片后 48h 内跟进。",
    },
    {
        "id": "github-repo", "platform": "github", "name": "自有 GitHub 仓库",
        "url": "",
        "audience": ["developers", "technical-founders"], "size": 220,
        "access": "public", "promo": "allowed",
        "formats": ["readme", "discussion", "issue"],
        "signal_density": 1, "competition": 1, "speed": 1, "risk": 1,
        "role_hint": "ambient",
        "evidence": "已有 220 star/10 fork；信誉层，不是获客层",
        "notes": "用 Discussion/README 承接，不做 mass DM。",
    },
]


# 每个 venue 的「为什么」：不是字段，是判断。渲染时优先用这些，不用分数。
VENUE_STORIES: Dict[str, Dict[str, Any]] = {
    "reddit-finops": {
        "scene": "月底关账、CFO 要按团队拆、续约前拉数、账单环比异常。",
        "pain": "「AI 那行掉进 other row，我拆不开」「CFO 问我每个团队花多少，我答不出」"
                "「发票和用量对不上，最后谁把它摊平」。",
        "substitute": "电子表格 + 现有 FinOps 工具 + 工程师手工拉数；工具只看总数，自建只覆盖云。",
        "why_here": "2026H2 有 10+ 条第一人称痛点；人已经在讨论，不需要教育；"
                    "公开可搜索、可按时间复核；允许方法帖和回答。",
        "what_to_say": "先答问题，再给 7 条差异清单；不贴产品链接；有人问再给。",
        "proof": "开源 checker + 合成样例 + 7 failure modes。",
        "signal": "≥3 条实质回复，或 ≥1 份脱敏账期。",
        "falsifier": "10 条动作 0 回复；版规删帖；只有看客没有数据。",
        "evidence_level": "observed",
        "data_basis": "Askovo 31 条证据（Reddit 19/HN 12），r/FinOps 2026H2 10+ 条第一人称，2026-11-03。",
        "voices": [
            {"quote": "Took me until the following Wednesday to admit I couldn't.",
             "source": "r/FinOps，2026-08-24"},
            {"quote": "The AI and GPU line is about 4x last year, but our cost tool drops "
                      "nearly all of it into one 'other' row.",
             "source": "r/FinOps，2026-08-29"},
        ],
    },
    "reddit-cfo": {
        "scene": "预算、关账、控制、AI 进总账。",
        "pain": "「发票审批后才发现问题」「AI 支出没有归属」「什么财务问题你愿意付钱解决」。",
        "substitute": "财务表格 + AP 工具 + 顾问；CFO 通常不直接选工具。",
        "why_here": "预算方/交付物接收方；能验证「谁签字」；但不一定是第一个买家。",
        "what_to_say": "控制/关账角度；不卖工具；先回答控制规则。",
        "proof": "CFO 一页 + 控制规则（AI 可准备不可过账、可回放）。",
        "signal": "≥1 个 CFO/FP&A 愿意看样本或引荐 FinOps。",
        "falsifier": "只讨论 AI 战略，不讨论账；或只想要免费模板。",
        "evidence_level": "clue",
        "data_basis": "r/CFO WTP/controls 帖（2026-10），样本 2 条。",
        "voices": [
            {"quote": "AI can prepare an entry but never post one.",
             "source": "r/CFO，2026-10-07"},
        ],
    },
    "reddit-aiagents": {
        "scene": "给客户跑 agent，月底开票、拆成本。",
        "pain": "「provider 账单是一个大数，怎么拆到每个客户」「token 成本转嫁客户，按什么价」。",
        "substitute": "按 token 比例估、加 markup、老板拍。",
        "why_here": "AI 交付方是并列候选，可能更快付钱；允许披露后发方法。",
        "what_to_say": "一个客户一个账期，invoice × usage × contract；先给方法。",
        "proof": "同一套 checker + 3 页样例。",
        "signal": "≥1 个客户账期样本。",
        "falsifier": "只问技术不问开票；或只要免费工具。",
        "evidence_level": "clue",
        "data_basis": "r/AI_Agents 2026-10-29 + HN 2026-10-16，样本 2–3 条。",
        "voices": [
            {"quote": "How are you attributing token costs per client?",
             "source": "r/AI_Agents，2026-10-29"},
        ],
    },
    "hackernews": {
        "scene": "开发者/创始人刷 Show HN，找新工具/新方法。",
        "pain": "「AI 账单对不上，不知道从哪查」「想知道别人怎么处理」。",
        "substitute": "自己写脚本、看供应商 dashboard、问同事。",
        "why_here": "一次性集中发布能带来最大曝光；技术受众能判断 checker 真假；Show HN 允许自有项目。",
        "what_to_say": "标题具体、首评给 3 个最意外的差异、请对方拿一个账期来试。",
        "proof": "可复现 checker + 合成样例。",
        "signal": "≥20 条评论或 ≥20 star；≥1 个数据意愿。",
        "falsifier": "2 小时 <20 分；被 flag；评论全是技术细节没有人问账。",
        "evidence_level": "clue",
        "data_basis": "Show HN 经验；本产品未实测。",
    },
    "finops-foundation-slack": {
        "scene": "同行问 AI 成本怎么分、工具怎么选。",
        "pain": "同上，但更专业、更愿意给细节。",
        "substitute": "工具 + 自建 + 顾问。",
        "why_here": "高信任、高信号；但版规通常禁止推广。",
        "what_to_say": "只问问题/答问题；不发链接；被问再给。",
        "proof": "方法/清单。",
        "signal": "≥2 条实质对话或 1 个私信邀请。",
        "falsifier": "被警告/被移出；或只围观。",
        "evidence_level": "clue",
        "data_basis": "FinOps Foundation 社区（未实测）。",
    },
    "mlops-community": {
        "scene": "LLM 基础设施/成本讨论。",
        "pain": "推理成本、缓存、token 计费。",
        "substitute": "自建监控/供应商 dashboard。",
        "why_here": "技术语气、能验证方法；但买家少。",
        "what_to_say": "只发方法/问题；被问再给链接。",
        "proof": "7 条清单中的 2 条。",
        "signal": "≥1 条实质回复。",
        "falsifier": "0 回复或只聊技术。",
        "evidence_level": "hypothesis",
        "data_basis": "MLOps Community（未实测）。",
    },
    "latent-space": {
        "scene": "AI 工程师讨论推理/成本。",
        "pain": "同上。",
        "substitute": "自建/供应商后台。",
        "why_here": "技术语气、能验证方法；但买家少。",
        "what_to_say": "技术问题开场；不发产品名。",
        "proof": "7 条清单中的 2 条。",
        "signal": "≥1 条实质回复。",
        "falsifier": "0 回复或只聊技术。",
        "evidence_level": "hypothesis",
        "data_basis": "Latent Space（未实测）。",
    },
    "linkedin-finops": {
        "scene": "职业身份下看 FinOps/FP&A 内容。",
        "pain": "同上，偏管理/预算。",
        "substitute": "工具/顾问/内部团队。",
        "why_here": "可核身份、可定向；但冷连接接受率低、节奏慢。",
        "what_to_say": "1 帖 + 10 条连接备注；不群发。",
        "proof": "方法页/checklist。",
        "signal": "≥3 个接受 + ≥1 个对话。",
        "falsifier": "接受率 <30%；只点赞不对话。",
        "evidence_level": "hypothesis",
        "data_basis": "上一轮 LinkedIn 触达 0 付费。",
    },
    "indie-hackers": {
        "scene": "独立开发者找增长/工具。",
        "pain": "获客、定价、build in public。",
        "substitute": "自己做/买工具。",
        "why_here": "与 FinOps 买家重合低。",
        "what_to_say": "除非目标换成独立开发者，否则不发。",
        "proof": "—",
        "signal": "—",
        "falsifier": "目标不匹配。",
        "evidence_level": "hypothesis",
        "data_basis": "平台定位。",
    },
    "finops-newsletter": {
        "scene": "订阅者读 FinOps 内容。",
        "pain": "同上。",
        "substitute": "工具/顾问。",
        "why_here": "借场：别人的受众；需要付费/关系。",
        "what_to_say": "先问能不能转载/引用，不买硬广。",
        "proof": "方法页。",
        "signal": "≥1 次转载或 1 个引荐。",
        "falsifier": "没有回复/报价太高。",
        "evidence_level": "hypothesis",
        "data_basis": "FinOps newsletter（未接触）。",
    },
    "finops-x": {
        "scene": "会议/meetup。",
        "pain": "同上，高信任。",
        "substitute": "工具/顾问。",
        "why_here": "高信任、慢；适合第二季度。",
        "what_to_say": "先听，不推销；换名片后 48h 跟进。",
        "proof": "方法页。",
        "signal": "≥3 张有效名片或 1 个后续会。",
        "falsifier": "没有预算/没有后续。",
        "evidence_level": "hypothesis",
        "data_basis": "FinOps X（未参加）。",
    },
    "github-repo": {
        "scene": "开发者看仓库/issue。",
        "pain": "代码/集成问题。",
        "substitute": "自己读代码。",
        "why_here": "信誉层，不是获客层；已有 220 star/10 fork。",
        "what_to_say": "Discussion/README 承接。",
        "proof": "代码/样例。",
        "signal": "≥1 个外部 issue/PR。",
        "falsifier": "只有 star 没有 issue。",
        "evidence_level": "observed",
        "data_basis": "GitHub API 2026-11-03。",
    },
}

# 不做什么：每条都要写「为什么不」和「什么情况下重新考虑」。
NOT_DOING = [
    {"name": "LinkedIn 冷连接",
     "why": "职业身份可核，但冷连接接受率低、节奏慢；上一轮 LinkedIn 触达 0 付费。",
     "reconsider": "有 10 个可识别、可引荐的对象，且能拿到具体触发事件。"},
    {"name": "Product Hunt",
     "why": "受众是产品猎人，不是 FinOps 买家；一次性曝光与月度关账不匹配。",
     "reconsider": "目标换成独立开发者/消费产品。"},
    {"name": "冷邮件",
     "why": "上一轮约 1,200 封 → 0 付费；通道已关；批量触达 = 单位价值下降。",
     "reconsider": "有明确授权名单 + 一对一内容 + 可验证的回执。"},
    {"name": "投流",
     "why": "没有预算；B2B 高信任任务，广告转化低。",
     "reconsider": "有可重复的付费转化证据。"},
    {"name": "Indie Hackers",
     "why": "独立开发者与 FinOps 买家重合低。",
     "reconsider": "目标换成独立开发者。"},
    {"name": "X 冷触达",
     "why": "上一轮约 900 次动作 → 5 回应；注意力渠道，不是商业渠道。",
     "reconsider": "只做放大/深度对话，不做冷启动。"},
]


def venue(venue_id: str) -> Dict[str, Any]:
    for row in VENUES:
        if row["id"] == venue_id:
            return dict(row)
    raise ValueError(f"没有这个渠道：{venue_id}")


def _tags(facts: Dict[str, Any]) -> set:
    tags = set()
    for hyp in facts.get("icp", []):
        tags.update(hyp.get("tags", []))
    return tags


def _goal_platforms(goal_plan: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not goal_plan:
        return {}
    platforms = goal_plan.get("platforms") or {}
    out = {}
    if platforms.get("primary"):
        out[platforms["primary"]["id"]] = "primary"
    if platforms.get("launch"):
        out[platforms["launch"]["id"]] = "launch"
    for row in platforms.get("support") or []:
        out[row["id"]] = "support"
    return out


def score_venue(row: Dict[str, Any], facts: Dict[str, Any],
                goal_plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    tags = _tags(facts)
    audience_hits = sorted(tags & set(row["audience"]))
    primary_tag = ""
    if facts.get("icp") and facts["icp"][0].get("tags"):
        primary_tag = facts["icp"][0]["tags"][0]
    primary_hit = 1 if primary_tag and primary_tag in row["audience"] else 0
    role = _goal_platforms(goal_plan).get(row["platform"], "")
    goal_score = {"primary": 3, "launch": 2, "support": 1}.get(role, 0)
    owned = row["id"] in ((facts.get("owned_assets") or {}).get("venues") or [])
    score = (len(audience_hits) * 3 + primary_hit * 3 + goal_score * 2
             + row["signal_density"] * 2 + row["speed"]
             + ACCESS_SCORE.get(row["access"], 0)
             + PROMO_SCORE.get(row["promo"], 0) - row["competition"] - row["risk"]
             + (1 if owned else 0))
    return {"id": row["id"], "name": row["name"], "platform": row["platform"],
            "audience_hits": audience_hits, "goal_role": role, "owned": owned,
            "score": score, "size": row["size"], "access": row["access"],
            "promo": row["promo"], "signal_density": row["signal_density"],
            "competition": row["competition"], "speed": row["speed"],
            "risk": row["risk"], "evidence": row["evidence"], "notes": row["notes"],
            "role_hint": row.get("role_hint", ""),
            "story": dict(VENUE_STORIES.get(row["id"], {}))}


def rank(facts: Dict[str, Any],
         goal_plan: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    rows = [score_venue(v, facts, goal_plan) for v in VENUES]
    rows.sort(key=lambda r: (-r["score"], r["id"]))
    return rows


def _assign(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    usable = [r for r in rows if r["promo"] != "banned" and r["access"] in ("public", "join")]
    launch = next((r for r in usable if r["role_hint"] == "launch"), None)
    primary = next((r for r in usable if r is not launch and r["role_hint"] != "not_now"), None)
    support = [r for r in usable if r is not primary and r is not launch][:2]
    ambient = [r for r in rows if r["promo"] == "banned" or r["access"] in ("login", "paid")]
    not_now = [r for r in rows if r not in usable and r not in ambient]
    return {"primary": primary, "launch": launch, "support": support,
            "ambient": ambient[:2], "not_now": not_now[:3]}


def _economics(roles: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    """触达→回复→合格→样本→付费，全部是假设区间。"""
    assumptions = {
        "post_reach_rate": 0.01, "post_reach_cap": 1500,
        "comment_reach": 100, "comment_count": 3,
        "launch_reach_rate": 0.02, "launch_reach_cap": 5000,
        "post_reply_rate": 0.015, "comment_reply_rate": 0.05,
        "launch_reply_rate": 0.01, "dm_asks": 30, "dm_reply_rate": 0.10,
        "qualified_rate": 0.20, "sample_rate": 0.25, "close_rate": 0.25,
    }
    steps = []
    primary = roles.get("primary")
    launch = roles.get("launch")
    if primary:
        steps.append(("主渠道：1 帖",
                      min(primary["size"] * assumptions["post_reach_rate"],
                          assumptions["post_reach_cap"]),
                      assumptions["post_reply_rate"]))
        steps.append((f"主渠道：{assumptions['comment_count']} 条回答",
                      assumptions["comment_count"] * assumptions["comment_reach"],
                      assumptions["comment_reply_rate"]))
    if launch:
        steps.append(("集中发布：1 帖",
                      min(launch["size"] * assumptions["launch_reach_rate"],
                          assumptions["launch_reach_cap"]),
                      assumptions["launch_reply_rate"]))
    steps.append((f"直连：{assumptions['dm_asks']} 条私信",
                  assumptions["dm_asks"],
                  assumptions["dm_reply_rate"]))
    total_reach = sum(s[1] for s in steps)
    total_replies = sum(s[1] * s[2] for s in steps)
    qualified = total_replies * assumptions["qualified_rate"]
    samples = qualified * assumptions["sample_rate"]
    paid = samples * assumptions["close_rate"]
    minutes = int(((facts.get("constraints") or {}).get("human_minutes_budget")) or 840)
    return {
        "assumptions": assumptions,
        "steps": [{"name": n, "reach": round(r), "reply_rate": rate,
                   "replies": round(r * rate, 1)} for n, r, rate in steps],
        "expected": {
            "reach": round(total_reach), "replies": round(total_replies, 1),
            "qualified": round(qualified, 1), "samples": round(samples, 1),
            "paid": round(paid, 1),
        },
        "cost": {"human_minutes": minutes,
                 "cost_per_reply": round(minutes / max(total_replies, 0.1), 1),
                 "cost_per_conversation": round(minutes / max(qualified, 0.1), 1)},
        "boundary": ("假设区间，不是预测。触达率/回复率/合格率/样本率/成交率都必须用真实数据回填；"
                     "没有回填之前，只用于排期和止损，不用于承诺收入。"),
    }


def _capacity(roles: Dict[str, Any]) -> List[Dict[str, Any]]:
    limits = {"post": 1, "comment": 5, "dm": 5}
    rows = []
    for role in ("primary", "launch", "support"):
        items = roles.get(role)
        if not items:
            continue
        for item in (items if isinstance(items, list) else [items]):
            for action, limit in limits.items():
                rows.append({"role": role, "venue": item["name"],
                             "action": action, "per_day": limit, "limit": limit,
                             "ok": True})
    return rows


def _experiments(roles: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    if roles.get("primary"):
        out.append({
            "venue": roles["primary"]["name"], "role": "primary",
            "action": "1 篇方法帖 + 3 条回答，7 天",
            "metric": "replies", "pass": ">=3 条实质回复或 >=1 份数据",
            "stop": "10 条动作 0 回复 → 换话术，不换渠道",
        })
    if roles.get("launch"):
        out.append({
            "venue": roles["launch"]["name"], "role": "launch",
            "action": "1 次集中发布，2 小时内回完评论",
            "metric": "comments", "pass": ">=20 条评论或 >=20 star",
            "stop": "被 flag → 不重发，转 Reddit/X",
        })
    if roles.get("support"):
        out.append({
            "venue": roles["support"][0]["name"], "role": "support",
            "action": "每周 2 次价值回答，4 周",
            "metric": "replies", "pass": ">=1 条实质回复",
            "stop": "0 回复 → 降为旁听",
        })
    out.append({
        "venue": "直连", "role": "direct",
        "action": "30 条具名私信，3 天",
        "metric": "data_shares", "pass": ">=1 份脱敏数据",
        "stop": "10 条 0 回复 → 改问法/换名单，不加量",
    })
    return out


def plan(facts: Dict[str, Any],
         goal_plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """生成渠道组合；只读，不写平台、不发消息。"""
    gtm._validate_facts(facts)
    rows = rank(facts, goal_plan)
    roles = _assign(rows)
    if roles["primary"] is None:
        raise ValueError("没有可用主渠道：检查 promo/access 或 facts 的 ICP 标签")
    return {
        "schema": SCHEMA,
        "product": (facts.get("product") or {}).get("name", ""),
        "goal": (goal_plan or {}).get("goal"),
        "secondary": (goal_plan or {}).get("secondary"),
        "venues": roles,
        "ranking": rows,
        "economics": _economics(roles, facts),
        "capacity": _capacity(roles),
        "experiments": _experiments(roles),
        "rules": [
            "版规禁止推广的地方只旁听/回答，不发产品帖",
            "一天一个渠道最多 1 帖 + 5 条回答；不跨渠道复制同一段",
            "同一对象 3 天内最多 2 次动作；拒绝即不再触达",
            "先答问题，再给方法；产品链接只在对方问时给",
            "渠道效果只由「被问到/样本/付费」判断，不由曝光判断",
        ],
        "boundary": ("渠道组合是排期假设，不是渠道效果证明。"
                     "借场不等于发广告；旁听不等于触达；曝光不等于需求。"),
    }


EVIDENCE_PLAIN = {"observed": "● 已观察到", "clue": "◐ 有线索", "hypothesis": "○ 假设"}


@plain_output
def render(plan: Dict[str, Any]) -> str:
    from .narrative import table
    g = plan.get("goal") or {}
    roles = plan["venues"]
    primary = roles.get("primary")
    launch = roles.get("launch")
    support = roles.get("support") or []
    ambient = roles.get("ambient") or []
    lines = [f"# 渠道判断 · {plan['product']}"
             + (f" · {g.get('name')}" if g else ""), ""]
    lines.append("## 结论")
    lines.append("")
    names = ([primary["name"]] if primary else [])
    if launch:
        names.append(launch["name"])
    names.extend(x["name"] for x in support)
    lines.append(f"这一轮只在 **{'、'.join(names)}** 做，别的地方先不碰。")
    lines.append("")
    lines.append("不是因为这里人多，是因为它们同时满足四个条件："
                 "**人在、痛在、规则允许、我们能给东西**。少一个，就不值得投。")
    lines.append("")
    lines.append("这四个角色也不是四个渠道，是一条路径：**主渠道持续遇到人，"
                 "集中发布把人一次性聚过来，辅助接住主渠道覆盖不到的人，旁听让我们知道同行在说什么。**"
                 "缺一个，路径就断一截。")
    lines.append("")
    lines.append("## 为什么是这几处")
    lines.append("")
    for role_label, items in (("主渠道", [primary] if primary else []),
                              ("集中发布", [launch] if launch else []),
                              ("辅助", support),
                              ("旁听", ambient)):
        for item in items:
            story = item.get("story") or {}
            lines.append(f"### {item['name']}（{role_label}）")
            lines.append("")
            lines.append(f"**谁在这里**：{story.get('scene', '—')}")
            lines.append("")
            lines.append(f"**他现在的难处**：{story.get('pain', '—')}")
            lines.append("")
            voices = story.get("voices") or []
            if voices:
                for v in voices[:2]:
                    lines.append(f"> “{v['quote']}” —— {v['source']}")
                    lines.append("")
            lines.append(f"**他今天怎么解决**：{story.get('substitute', '—')}")
            lines.append("")
            lines.append(f"**为什么是这里，不是别处**：{story.get('why_here', '—')}")
            lines.append("")
            lines.append(f"**我们在这里说什么**：{story.get('what_to_say', '—')}")
            lines.append("")
            lines.append(f"**凭什么信**：{story.get('proof', '—')}")
            lines.append("")
            lines.append(f"**怎么判断有没有用**：{story.get('signal', '—')}")
            lines.append("")
            lines.append(f"**什么情况下换**：{story.get('falsifier', '—')}")
            lines.append("")
            lines.append(f"**证据强度**：{EVIDENCE_PLAIN.get(story.get('evidence_level'), '○ 假设')}"
                         f"（{story.get('data_basis', '—')}）")
            lines.append("")
    lines.append("## 为什么不是别处")
    lines.append("")
    lines.append(table(["渠道", "为什么不选", "什么情况下重新考虑"],
                       [[x["name"], x["why"], x["reconsider"]] for x in NOT_DOING]))
    lines.append("")
    lines.append("## 这笔账怎么算")
    lines.append("")
    e = plan["economics"]
    for step in e["steps"]:
        lines.append(f"- {step['name']}：约 {step['reach']:.0f} 人看到，"
                     f"按 {step['reply_rate']:.0%} 回复，约 {step['replies']:.0f} 条回复。")
    lines.append("")
    lines.append(f"合计约 **{e['expected']['replies']:.0f} 条回复、"
                 f"{e['expected']['qualified']:.0f} 个合格对话、"
                 f"{e['expected']['samples']:.0f} 个样本、"
                 f"{e['expected']['paid']:.0f} 个付费**。")
    lines.append("")
    lines.append(f"这不是预测，是排期：实际回复率只有一半，就按一半重算；"
                 f"10 条动作 0 回复，就停。每个合格对话约 "
                 f"{e['cost']['cost_per_conversation']:.0f} 分钟；超过 2 小时，"
                 f"说明名单或话术不对。")
    lines.append("")
    lines.append("## 节奏")
    lines.append("")
    lines.append("- 主渠道：1 帖/天、5 条回答/天；同一段不跨 venue 复制。")
    lines.append("- 集中发布：1 次，不重发；2 小时内回完评论。")
    lines.append("- 私信：5 条/天；同一对象 3 天内最多 2 次动作。")
    lines.append("- 旁听：只答不问、不发链接；被问再给。")
    lines.append("")
    lines.append("## 什么情况下这条路不成立")
    lines.append("")
    lines.append("- 主渠道 10 条动作 0 回复：不是渠道问题，是话术/名单问题；换话术，不换 venue。")
    lines.append("- 集中发布后 2 小时 <20 分或被 flag：不重发；把同一篇换开头去 Reddit/X。")
    lines.append("- 连续 2 周 0 份数据：offer 或人群不对；改「我帮你跑」的问法，不加曝光。")
    lines.append("- 只有浏览没有对话：这是曝光，不是渠道；停，别自我安慰。")
    lines.append("")
    lines.append("## 纪律")
    lines.append("")
    for rule in plan["rules"]:
        lines.append(f"- {rule}")
    lines.append("")
    lines.append(f"> {plan['boundary']}")
    lines.append("")
    return "\n".join(lines)
