#!/usr/bin/env python3
"""GTM 设计器：对象 × 目标 × 机制 × 渠道。

v4.0 的核心变化不是再加一套行业模板，而是把「给谁增长、要什么结果、用什么
机制、在哪执行」拆成四个可以分别校验的维度：

    Object   要推广的业务对象（软件、内容账号、硬件、开源项目、服务、B2B）
    Objective 用户真正希望改善的业务结果
    Motion   增长机制（内容/社区/产品/销售/商业/合作）
    Channel  具体渠道（复用 core/platforms.py 的 37 个平台，不另建清单）

设计约束：
- 机制是候选，不是结论。Agent 可以提交候选机制与理由（``proposal``），
  本模块只做兼容性、约束与证据可行性检查，并保留被拒理由。
- 对象与目标由文本信号推断，也可以显式指定；置信度低时返回待澄清问题，
  不硬猜一个看起来完整的方案。
- 结果契约按目标生成，并显式区分「平台可观察」「业务可观察」「未验证」。
  Star 不是采用，播放量不是影响力，预售订单不是收入。
- 本模块只产出设计，不执行、不发送、不预测数值，也不把回执当业务结果。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .platforms import PLATFORMS
from .platforms import BY_ID as PLATFORM_BY_ID

SCHEMA = "growovo.gtm-design/1"
MAX_PRIMARY = 1
MAX_SUPPORT = 3

# ---------------------------------------------------------------------------
# 对象：先确定「增长谁」，而不是先选渠道
# ---------------------------------------------------------------------------
OBJECTS: Dict[str, Dict[str, Any]] = {
    "software_saas": {
        "label": "软件 / SaaS / AI 应用",
        "signals": [("saas", 3), ("软件", 2), ("ai 产品", 3), ("ai应用", 3), ("应用", 1),
                    ("app", 1), ("小程序", 2), ("插件", 2), ("api", 2), ("sdk", 2),
                    ("订阅", 2), ("工具", 1), ("网页", 1), ("agent", 1), ("平台", 1)],
        "platform_types": ["saas", "devtool", "consumer-app", "product", "sdk", "api"],
        "kinds": [],
        "motions": ["product_led", "content_led", "sales_led", "community_led"],
        "value_assets": ["可自助体验的产品", "文档与示例", "真实用户案例"],
        "success_note": "注册不等于激活，激活不等于付费。",
    },
    "content_account": {
        "label": "内容账号 / 自媒体",
        "signals": [("小红书", 3), ("公众号", 3), ("自媒体", 3), ("抖音", 3), ("b站", 3),
                    ("bilibili", 3), ("youtube", 3), ("视频号", 3), ("知乎", 3),
                    ("账号", 2), ("粉丝", 2), ("博主", 2), ("读者", 1), ("newsletter", 3),
                    ("播客", 2), ("内容号", 3)],
        "platform_types": [],
        "kinds": ["内容", "视频", "社媒"],
        "motions": ["content_led", "partnership_led", "commerce_led"],
        "value_assets": ["稳定选题与产出节奏", "可被关注的主页承接", "可复用内容资产"],
        "success_note": "播放量不等于影响力，粉丝数不等于目标受众。",
    },
    "hardware": {
        "label": "硬件 / 消费产品",
        "signals": [("硬件", 3), ("设备", 2), ("相机", 3), ("耳机", 3), ("机器人", 3),
                    ("量产", 3), ("供应链", 3), ("模具", 2), ("发货", 2), ("预售", 3),
                    ("京东", 2), ("天猫", 2), ("淘宝", 2), ("线下", 1), ("样机", 3),
                    ("消费电子", 3)],
        "platform_types": ["hardware", "product", "creative"],
        "kinds": [],
        "motions": ["content_led", "commerce_led", "partnership_led"],
        "value_assets": ["可展示样机与实拍", "价格、交付与售后承诺", "评测/试用样品"],
        "success_note": "线索不等于可履约需求，预售订单不等于收入。",
    },
    "oss_project": {
        "label": "开源项目 / 开发者生态",
        "signals": [("开源", 3), ("github", 3), ("仓库", 3), ("star", 2), ("贡献者", 3),
                    ("issue", 2), ("pull request", 3), ("pr", 1), ("文档站", 2),
                    ("cli", 1), ("维护者", 3), ("开发者生态", 3)],
        "platform_types": ["oss", "devtool", "sdk", "api"],
        "kinds": [],
        "motions": ["community_led", "content_led", "product_led", "partnership_led"],
        "value_assets": ["README 与一行安装", "可运行示例", "Issue/PR 响应机制"],
        "success_note": "Star 不等于采用，clone 不等于首次运行成功。",
    },
    "service": {
        "label": "服务 / 咨询 / 代运营",
        "signals": [("服务", 2), ("咨询", 3), ("代运营", 3), ("顾问", 3), ("外包", 3),
                    ("项目制", 3), ("交付", 1), ("客户", 1), ("工作室", 2)],
        "platform_types": ["b2b-saas", "saas"],
        "kinds": [],
        "motions": ["sales_led", "content_led", "partnership_led"],
        "value_assets": ["可验证的交付方法", "案例与客户证言", "报价与边界说明"],
        "success_note": "线索不等于合格机会，签约不等于交付价值。",
    },
    "b2b": {
        "label": "B2B / 企业软件",
        "signals": [("b2b", 3), ("企业", 2), ("大客户", 3), ("ka", 1), ("采购", 3),
                    ("招投标", 3), ("私有化", 3), ("pipeline", 3), ("crm", 3),
                    ("销售团队", 3), ("客单价", 2)],
        "platform_types": ["b2b-saas", "saas"],
        "kinds": [],
        "motions": ["sales_led", "content_led", "partnership_led"],
        "value_assets": ["账户研究与价值假设", "可验证的方案/POC", "商务与合规材料"],
        "success_note": "MQL 不等于收入，pipeline 不等于回款。",
    },
}

# ---------------------------------------------------------------------------
# 目标：不同目标用不同证据，禁止一套指标通吃
# ---------------------------------------------------------------------------
OBJECTIVES: Dict[str, Dict[str, Any]] = {
    "awareness": {
        "label": "被发现 / 建立认知",
        "signals": [("曝光", 3), ("声量", 3), ("认知", 3), ("被看见", 3), ("知名度", 3),
                    ("关注", 2), ("粉丝", 2), ("阅读", 2), ("播放", 2), ("种草", 3)],
        "primary": "目标人群中的有效到达",
        "definition": "目标画像人群里，真正读到/看完并记住一个具体主张的人数或比例",
        "leading": ["目标画像命中率", "完读/完播", "收藏与关注", "来自目标人群的主动搜索"],
        "guardrail": ["泛人群曝光", "互赞互评", "买量换来的低质关注"],
        "not_success": ["总播放量", "粉丝总数", "点赞数"],
        "sources": ["platform_insights"],
        "window": "2-6 周，按平台内容分发周期确定",
        "stop": "连续两周目标画像互动低于基线，先改选题与承接，不加投放",
    },
    "acquisition": {
        "label": "获取合格对象",
        "signals": [("获客", 3), ("线索", 3), ("注册", 3), ("留资", 3), ("试用", 2),
                    ("leads", 3), ("加微", 2), ("预约", 2), ("到店", 3),
                    ("pipeline", 2), ("商机", 3), ("询价", 2), ("客户", 1), ("销售", 1)],
        "primary": "合格对象的获取成本与数量",
        "definition": "满足事先写明的资格条件（画像、问题、预算/权限）的新对象",
        "leading": ["合格率", "来源渠道", "首次响应时长", "无效线索原因分布"],
        "guardrail": ["表单提交量", "留资率", "名单条数"],
        "not_success": ["线索总数", "表单提交", "加微人数"],
        "sources": ["crm", "platform_insights", "product_events"],
        "window": "2-4 周",
        "stop": "合格率低于约定下限时，先修资格定义与承接话术，不加渠道",
    },
    "activation": {
        "label": "首次价值 / 激活",
        "signals": [("激活", 3), ("首次成功", 3), ("上手", 3), ("onboarding", 3),
                    ("跑通", 3), ("首次运行", 3), ("aha", 2), ("转化", 1)],
        "primary": "首次价值事件的完成率",
        "definition": "新对象在约定窗口内完成一个可观察的首次价值动作（安装并成功运行、建首个项目、完成首单任务）",
        "leading": ["关键步骤流失", "首次成功耗时", "求助与报错", "文档/示例命中"],
        "guardrail": ["注册数", "下载量", "页面停留"],
        "not_success": ["安装量", "注册数", "文档访问量"],
        "sources": ["product_events", "platform_insights"],
        "window": "7-14 天",
        "stop": "首次成功率的瓶颈连续两轮不变，先修产品与文档，不扩获客",
    },
    "revenue": {
        "label": "付费 / 订单 / 回款",
        "signals": [("付费", 3), ("订单", 3), ("收入", 3), ("成交", 3), ("回款", 3),
                    ("预售", 3), ("销售额", 3), ("转化率", 2), ("续费", 2)],
        "primary": "可确认的付费金额或订单履约结果",
        "definition": "完成支付并满足退款/取消窗口后的净收入，或已履约的订单",
        "leading": ["加购/询价", "报价到成交", "客单价", "退款与取消原因"],
        "guardrail": ["GMV", "下单未支付", "预售未履约"],
        "not_success": ["GMV", "预售订单数", "意向金人数"],
        "sources": ["billing", "order_system", "crm"],
        "window": "4-8 周，覆盖退款与履约窗口",
        "stop": "退款/取消率超过约定阈值，先停量查交付与承诺一致性",
    },
    "retention": {
        "label": "留存 / 续费 / 复购",
        "signals": [("留存", 3), ("续费", 3), ("复购", 3), ("流失", 3), ("召回", 3),
                    ("活跃", 2), ("nps", 2), ("生命周期", 3)],
        "primary": "约定周期内的留存/续费/复购率",
        "definition": "同一批对象在第二个周期仍完成核心价值动作的比例",
        "leading": ["核心动作频次", "支持请求", "价值实现时点", "流失前兆"],
        "guardrail": ["登录数", "打开率", "群人数"],
        "not_success": ["DAU", "打开率", "群活跃"],
        "sources": ["product_events", "billing", "crm"],
        "window": "1-3 个自然周期",
        "stop": "留存下滑与获客同步发生时，先查产品价值与交付，不加拉新",
    },
    "contribution": {
        "label": "生态采用与贡献",
        "signals": [("贡献", 3), ("采用", 3), ("生态", 3), ("issue", 2), ("pr", 2),
                    ("维护者", 3), ("集成", 3), ("下游", 3), ("社区参与", 3)],
        "primary": "真实采用与可复现的贡献",
        "definition": "外部对象在真实任务中使用项目，并产生可核验的 issue/PR/集成/案例",
        "leading": ["首次成功运行", "问题质量", "外部集成数", "维护者响应"],
        "guardrail": ["Star", "Fork", "下载量"],
        "not_success": ["Star 数", "Fork 数", "榜单排名"],
        "sources": ["repo_events", "platform_insights"],
        "window": "4-8 周",
        "stop": "只有 Star 没有首次成功运行证据时，先修安装与示例，不做拉星活动",
    },
}

# ---------------------------------------------------------------------------
# 机制：可组合的能力，不是互斥行业模板
# ---------------------------------------------------------------------------
MOTIONS: Dict[str, Dict[str, Any]] = {
    "content_led": {
        "label": "内容增长",
        "why": "用可复用的内容资产换取目标人群的持续到达与信任",
        "objects": ["software_saas", "content_account", "hardware", "oss_project", "service", "b2b"],
        "objectives": ["awareness", "acquisition", "activation", "contribution"],
        "kinds": ["内容", "视频", "社媒", "社区", "搜索"],
        "requires": ["content_asset"],
        "sequence": ["定 3 个目标人群真实问题", "做 1 个可验证的价值资产（教程/实测/对比）",
                     "按平台原生形式发布", "用平台回执与目标画像互动核验到达"],
    },
    "community_led": {
        "label": "社区增长",
        "why": "在目标人群已经在的社区里用真实帮助建立关系，而不是发广告",
        "objects": ["oss_project", "software_saas", "b2b", "service"],
        "objectives": ["awareness", "acquisition", "activation", "contribution", "retention"],
        "kinds": ["社区", "直连"],
        "requires": ["community_presence", "technical_asset"],
        "sequence": ["选 2 个目标人群真实聚集的社区", "先回答 5 个真实问题并留下可复现资产",
                     "把高频问题做成文档/示例", "观察是否出现主动追问与采用"],
    },
    "product_led": {
        "label": "产品增长",
        "why": "让产品自己完成体验与价值证明，增长动作围绕激活路径而非曝光",
        "objects": ["software_saas", "oss_project", "content_account"],
        "objectives": ["acquisition", "activation", "retention", "revenue"],
        "kinds": ["搜索", "目录", "发射", "社区"],
        "requires": ["product_experience", "product_events"],
        "sequence": ["定义首次价值事件", "把入口到首次成功的路径做通",
                     "在搜索/目录/发射渠道承接意图", "按队列看激活而不是注册"],
    },
    "sales_led": {
        "label": "销售驱动",
        "why": "高客单或复杂决策需要账户研究、资格判断与人对人的推进",
        "objects": ["b2b", "software_saas", "service", "hardware"],
        "objectives": ["acquisition", "revenue", "retention"],
        "kinds": ["社媒", "直连", "目录", "社区"],
        "requires": ["account_list", "sales_capacity"],
        "sequence": ["圈定目标账户与购买组", "写清触发事件与价值假设",
                     "用可验证材料开启对话", "按资格标准交接，不把回复当机会"],
    },
    "commerce_led": {
        "label": "商业/交易增长",
        "why": "有明确购买路径与履约能力时，增长要直接对订单与交付负责",
        "objects": ["hardware", "content_account", "service"],
        "objectives": ["revenue", "acquisition", "awareness"],
        "kinds": ["目录", "发射", "视频", "内容"],
        "requires": ["purchase_path", "supply"],
        "sequence": ["写清购买场景与交付承诺", "准备可展示的实拍/评测素材",
                     "把流量接到可下单页面", "按订单、取消、退货、履约复盘"],
    },
    "partnership_led": {
        "label": "合作增长",
        "why": "借合作方已有的信任与分发，换取单靠自己拿不到的覆盖",
        "objects": ["oss_project", "hardware", "b2b", "content_account", "service", "software_saas"],
        "objectives": ["awareness", "acquisition", "contribution", "revenue"],
        "kinds": ["目录", "社区", "直连", "发射"],
        "requires": ["partner_list"],
        "sequence": ["列出能触达目标人群的合作方", "写清对对方的真实价值",
                     "用小规模联合动作验证", "按带来合格对象而非曝光结算"],
    },
}

REQUIREMENTS: Dict[str, Dict[str, Any]] = {
    "content_asset": {"label": "可发布的内容/素材", "producible": True},
    "community_presence": {"label": "真实社区身份与历史", "producible": True},
    "technical_asset": {"label": "可复现的技术资产", "producible": True},
    "product_experience": {"label": "可自助体验的产品", "producible": False},
    "product_events": {"label": "产品事件数据渠道", "producible": False},
    "account_list": {"label": "目标账户名单", "producible": True},
    "sales_capacity": {"label": "可承接的销售人力", "producible": False},
    "purchase_path": {"label": "可下单的购买路径", "producible": False},
    "supply": {"label": "供应与履约能力", "producible": False},
    "partner_list": {"label": "可接触的合作方名单", "producible": True},
}

# 资产/数据约束用统一词表，避免调用方各写一套
ASSET_ALIASES = {
    "content": "content_asset", "内容": "content_asset", "素材": "content_asset",
    "community": "community_presence", "社区": "community_presence",
    "technical": "technical_asset", "技术资产": "technical_asset",
    "product": "product_experience", "产品": "product_experience",
    "events": "product_events", "产品事件": "product_events",
    "accounts": "account_list", "名单": "account_list",
    "sales": "sales_capacity", "销售": "sales_capacity",
    "purchase": "purchase_path", "购买路径": "purchase_path",
    "supply": "supply", "供应": "supply",
    "partners": "partner_list", "合作方": "partner_list",
}

DATA_SOURCES: Dict[str, str] = {
    "platform_insights": "平台后台数据",
    "product_events": "产品事件",
    "billing": "账单/支付",
    "order_system": "订单/电商后台",
    "crm": "CRM/线索",
    "repo_events": "仓库事件（issue/PR/克隆）",
}

REGION_PLATFORMS: Dict[str, set] = {
    "cn": {"xiaohongshu", "zhihu", "wechat", "juejin", "bilibili", "douyin", "channels",
           "weibo", "douban", "tieba", "36kr", "csdn", "maimai", "taobao", "kuaishou",
           "jike", "sspai", "zhishixingqiu", "v2ex"},
    "global": {"github", "hackernews", "product-hunt", "reddit", "linkedin",
               "indie-hackers", "kickstarter", "instagram", "x", "email", "youtube",
               "seo", "newsletter", "directory-sites", "stack-overflow", "discord",
               "bluesky", "tiktok"},
}

# 文本没有给出目标时的对象默认值：只作为暂定，并在 unknowns 中明示
OBJECT_DEFAULT_OBJECTIVE = {
    "software_saas": "activation",
    "content_account": "awareness",
    "hardware": "revenue",
    "oss_project": "contribution",
    "service": "acquisition",
    "b2b": "acquisition",
}

PAID_MOTIONS = {"partnership_led"}


class GTMDesignError(ValueError):
    """输入本身不成立（不是策略不成立）。"""


def _norm(text: str) -> str:
    return (text or "").casefold()


def _match_signals(text: str, signals: List[Tuple[str, int]]) -> Tuple[int, List[str]]:
    low = _norm(text)
    score = 0
    hits: List[str] = []
    for token, weight in signals:
        token_low = token.casefold()
        if token_low in low:
            score += weight
            hits.append(token)
    return score, hits


def _rank(text: str, table: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for key, spec in table.items():
        score, hits = _match_signals(text, spec["signals"])
        rows.append({"id": key, "label": spec["label"], "score": score, "signals": hits})
    rows.sort(key=lambda row: (-row["score"], row["id"]))
    return rows


def _pick(ranked: List[Dict[str, Any]], explicit: Optional[str], kind: str) -> Dict[str, Any]:
    if explicit:
        if explicit not in {row["id"] for row in ranked}:
            raise GTMDesignError(f"未知的 {kind}: {explicit}")
        row = next(item for item in ranked if item["id"] == explicit)
        return {"id": row["id"], "label": row["label"], "confidence": 1.0,
                "source": "explicit", "signals": [], "alternatives": []}
    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    if top["score"] <= 0:
        return {"id": top["id"], "label": top["label"], "confidence": 0.2,
                "source": "default", "signals": [], "alternatives": []}
    confidence = min(0.95, 0.45 + 0.1 * top["score"])
    if second and second["score"] > 0:
        gap = top["score"] - second["score"]
        confidence = round(max(0.3, min(confidence, 0.5 + 0.05 * gap)), 2)
    alternatives = [{"id": row["id"], "label": row["label"], "score": row["score"]}
                    for row in ranked[1:4] if row["score"] > 0]
    return {"id": top["id"], "label": top["label"], "confidence": confidence,
            "source": "text", "signals": top["signals"], "alternatives": alternatives}


def normalize_constraints(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise GTMDesignError("constraints 必须是 JSON 对象")
    budget = raw.get("budget", "unknown")
    if isinstance(budget, (int, float)):
        budget = "none" if budget <= 0 else ("low" if budget < 5000 else "medium")
    if budget not in {"none", "low", "medium", "high", "unknown"}:
        raise GTMDesignError("budget 只能是 none/low/medium/high 或金额")
    region = str(raw.get("region") or "").strip().lower()
    if region not in {"", "cn", "global"}:
        raise GTMDesignError("region 只能是 cn/global 或留空")
    channels = raw.get("authorized_channels") or []
    if isinstance(channels, str):
        channels = [channels]
    channels = [str(item).strip() for item in channels if str(item).strip()]
    unknown = [item for item in channels if item not in PLATFORM_BY_ID]
    if unknown:
        raise GTMDesignError(f"未知渠道: {', '.join(unknown)}")
    assets_raw = raw.get("assets") or []
    if isinstance(assets_raw, str):
        assets_raw = [assets_raw]
    assets = []
    for item in assets_raw:
        key = ASSET_ALIASES.get(str(item).strip().lower(), str(item).strip())
        if key not in REQUIREMENTS:
            raise GTMDesignError(f"未知资产/能力: {item}")
        assets.append(key)
    sources_raw = raw.get("data_sources") or []
    if isinstance(sources_raw, str):
        sources_raw = [sources_raw]
    sources = [str(item).strip() for item in sources_raw if str(item).strip()]
    unknown_sources = [item for item in sources if item not in DATA_SOURCES]
    if unknown_sources:
        raise GTMDesignError(
            f"未知数据源: {', '.join(unknown_sources)}；只允许 {', '.join(sorted(DATA_SOURCES))}")
    return {
        "budget": budget,
        "region": region,
        "authorized_channels": sorted(set(channels)),
        "assets": sorted(set(assets)),
        "data_sources": sorted(set(sources)),
        "notes": str(raw.get("notes") or "").strip(),
    }


def _object_channels(object_id: str) -> List[Dict[str, Any]]:
    spec = OBJECTS[object_id]
    rows = []
    for platform in PLATFORMS:
        if spec["platform_types"]:
            types = set(platform.get("product_types") or [])
            if not types.intersection(spec["platform_types"]):
                continue
        elif platform.get("kind") not in spec["kinds"]:
            continue
        rows.append(platform)
    return rows


def _region_ok(platform_id: str, region: str) -> bool:
    if not region:
        return True
    return platform_id in REGION_PLATFORMS[region]


def _channel_rows(object_id: str, motion_id: str,
                  constraints: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    motion = MOTIONS[motion_id]
    authorized = set(constraints["authorized_channels"])
    eligible: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for platform in _object_channels(object_id):
        pid = platform["id"]
        kind = platform.get("kind") or ""
        reasons = []
        if motion["kinds"] and kind not in motion["kinds"]:
            reasons.append(f"渠道类型 {kind} 不属于 {motion['label']} 的常用分发方式")
        if not _region_ok(pid, constraints["region"]):
            reasons.append(f"地区约束 {constraints['region']} 不覆盖该平台")
        if reasons:
            excluded.append({"id": pid, "name": platform.get("name", pid),
                             "kind": kind, "reasons": reasons})
            continue
        status = platform.get("channel_status", "UNVERIFIED")
        score = {"PROVEN": 3, "PORTED": 2}.get(status, 1)
        if pid in authorized:
            score += 2
        eligible.append({
            "id": pid,
            "name": platform.get("name", pid),
            "kind": kind,
            "status": status,
            "authorized": pid in authorized,
            "score": score,
            "risk": platform.get("risk", ""),
            "account": list(platform.get("account") or [])[:2],
        })
    eligible.sort(key=lambda row: (-row["score"], row["id"]))
    return eligible, excluded


def _motion_status(motion_id: str, object_id: str, objective_id: str,
                   constraints: Dict[str, Any],
                   authorized_ids: set) -> Dict[str, Any]:
    motion = MOTIONS[motion_id]
    problems: List[str] = []
    missing: List[Dict[str, Any]] = []
    if object_id not in motion["objects"]:
        problems.append(f"{motion['label']} 不适用于 {OBJECTS[object_id]['label']}")
    if objective_id not in motion["objectives"]:
        problems.append(f"{motion['label']} 不常用来达成 {OBJECTIVES[objective_id]['label']}")
    available = set(constraints["assets"]) | set(constraints["data_sources"])
    for req in motion["requires"]:
        if req in available:
            continue
        spec = REQUIREMENTS[req]
        missing.append({"id": req, "label": spec["label"], "producible": spec["producible"]})
    # 「现在可执行」要求：机制适配、能力齐备、且至少有一个已授权且适配的渠道
    blocking = [item for item in missing if not item["producible"]]
    motion_channels, _ = _channel_rows(object_id, motion_id, constraints)
    has_authorized_channel = any(row["authorized"] for row in motion_channels)
    executable = not problems and not missing and has_authorized_channel
    return {
        "id": motion_id,
        "label": motion["label"],
        "why": motion["why"],
        "problems": problems,
        "missing": missing,
        "status": "rejected" if problems else ("needs_preparation" if blocking else "candidate"),
        "executable": bool(executable and authorized_ids),
        "sequence": list(motion["sequence"]),
    }


def _verification(objective_id: str, data_sources: List[str]) -> Dict[str, Any]:
    spec = OBJECTIVES[objective_id]
    available = [item for item in spec["sources"] if item in data_sources]
    if not data_sources:
        level = "unverified"
        limit = "未提供数据渠道：只能拿到平台回执，不能声称业务结果"
    elif available:
        level = "observable" if len(available) >= 1 else "partially_observable"
        limit = f"可用数据源：{', '.join(available)}；口径仍需人工确认"
    else:
        level = "unverified"
        limit = (f"缺少 {spec['label']} 需要的数据源（{', '.join(spec['sources'])}）："
                 "平台回执不能替代业务结果")
    return {"level": level, "available_sources": available, "limit": limit}


def _outcome_contract(objective_id: str, constraints: Dict[str, Any]) -> Dict[str, Any]:
    spec = OBJECTIVES[objective_id]
    verification = _verification(objective_id, constraints["data_sources"])
    return {
        "objective": objective_id,
        "label": spec["label"],
        "primary_signal": {
            "name": spec["primary"],
            "definition": spec["definition"],
            "source": spec["sources"],
            "current": "unmeasured",
        },
        "leading_signals": list(spec["leading"]),
        "guardrails": list(spec["guardrail"]),
        "not_success": list(spec["not_success"]),
        "observation_window": spec["window"],
        "stop_rule": spec["stop"],
        "verification": verification,
        "boundary": "结果契约只定义可观察信号与停止条件；不预测数值，不把回执当结果。",
    }


def _validate_proposal(proposal: Optional[Dict[str, Any]], object_id: str,
                       objective_id: str) -> Dict[str, Any]:
    if not proposal:
        return {"used": False, "motions": [], "channels": [], "rejected": []}
    if not isinstance(proposal, dict):
        raise GTMDesignError("proposal 必须是 JSON 对象")
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for item in proposal.get("motions") or []:
        if isinstance(item, str):
            item = {"id": item}
        mid = str(item.get("id") or "")
        why = str(item.get("why") or "")
        if mid not in MOTIONS:
            rejected.append({"id": mid, "reason": "未知机制"})
            continue
        spec = MOTIONS[mid]
        if object_id not in spec["objects"]:
            rejected.append({"id": mid, "reason": f"不适用于 {OBJECTS[object_id]['label']}"})
            continue
        if objective_id not in spec["objectives"]:
            rejected.append({"id": mid, "reason": f"不常用于 {OBJECTIVES[objective_id]['label']}"})
            continue
        accepted.append({"id": mid, "why": why})
    compatible = {row["id"] for row in _object_channels(object_id)}
    channels_accepted: List[str] = []
    for cid in proposal.get("channels") or []:
        if cid not in PLATFORM_BY_ID:
            rejected.append({"id": cid, "reason": "未知渠道"})
        elif cid not in compatible:
            rejected.append({"id": cid, "reason": f"不适合 {OBJECTS[object_id]['label']}"})
        else:
            channels_accepted.append(cid)
    return {"used": True, "motions": accepted,
            "channels": channels_accepted, "rejected": rejected}


def design(task: str, *, object_id: Optional[str] = None,
           objective_id: Optional[str] = None,
           constraints: Optional[Dict[str, Any]] = None,
           proposal: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把一个增长任务拆成对象/目标/机制/渠道，并给出结果契约与未知项。"""
    if not str(task or "").strip():
        raise GTMDesignError("task 不能为空")
    cons = normalize_constraints(constraints)
    object_ranked = _rank(task, OBJECTS)
    objective_ranked = _rank(task, OBJECTIVES)
    obj = _pick(object_ranked, object_id, "object")
    target = _pick(objective_ranked, objective_id, "objective")
    if proposal and proposal.get("objective"):
        target = _pick(objective_ranked, str(proposal["objective"]), "objective")
    target_note = ""
    if target["source"] == "default" and not (proposal and proposal.get("objective")):
        default_id = OBJECT_DEFAULT_OBJECTIVE[obj["id"]]
        target = {"id": default_id, "label": OBJECTIVES[default_id]["label"],
                  "confidence": 0.3, "source": "object_default", "signals": [],
                  "alternatives": []}
        target_note = (f"任务未明说目标，按「{obj['label']}」的常用目标暂定为"
                       f"「{target['label']}」；可用 --objective 覆盖")

    prop = _validate_proposal(proposal, obj["id"], target["id"])
    if prop["used"]:
        motion_ids = [item["id"] for item in prop["motions"]]
    else:
        motion_ids = [mid for mid in OBJECTS[obj["id"]]["motions"]
                      if target["id"] in MOTIONS[mid]["objectives"]]

    authorized_ids = set(cons["authorized_channels"])
    motions: List[Dict[str, Any]] = []
    seen = set()
    for mid in motion_ids:
        if mid in seen:
            continue
        seen.add(mid)
        motions.append(_motion_status(mid, obj["id"], target["id"], cons, authorized_ids))
    if prop["used"]:
        for row in motions:
            override = next((item for item in prop["motions"] if item["id"] == row["id"]), None)
            if override:
                row["agent_reason"] = override["why"]
    # 保持对象词表里声明的机制顺序，只把被拒的沉底；稳定排序不改同组相对顺序
    motions.sort(key=lambda row: row["status"] == "rejected")

    viable = [row for row in motions if row["status"] != "rejected"]
    chosen = viable[0] if viable else None
    channels: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    if chosen:
        channels, excluded = _channel_rows(obj["id"], chosen["id"], cons)
    if prop["used"] and prop["channels"]:
        order = {cid: idx for idx, cid in enumerate(prop["channels"])}
        channels.sort(key=lambda row: (order.get(row["id"], 99), -row["score"]))

    authorized_rows = [row for row in channels if row["authorized"]]
    primary = authorized_rows[:MAX_PRIMARY]
    support = authorized_rows[MAX_PRIMARY:MAX_PRIMARY + MAX_SUPPORT]
    unauthorized = [row for row in channels if not row["authorized"]]
    for row in channels:
        row["proposed"] = row["id"] in set(prop["channels"])
    unauthorized.sort(key=lambda row: (not row["proposed"], -row["score"], row["id"]))

    status = "ready"
    unknowns: List[str] = []
    if obj["source"] == "text" and obj["confidence"] < 0.6:
        unknowns.append(f"对象判定置信度较低（{obj['confidence']}，{obj['label']}），请用 --object 确认；低置信度不改变设计，但会改变执行判断")
    if obj["confidence"] < 0.4:
        status = "needs_clarification"
        unknowns.append(f"不确定要增长的对象是不是「{obj['label']}」；请明确对象类型")
    if target["confidence"] < 0.4 and target["source"] == "text":
        status = "needs_clarification"
        unknowns.append(f"不确定这轮要的结果是「{target['label']}」；请明确目标")
    if target_note:
        unknowns.append(target_note)
    if not viable:
        status = "blocked"
        unknowns.append("当前对象/目标下没有可用机制；需要补充对象或目标信息")
    elif chosen and any(not item["producible"] for item in chosen["missing"]) and status == "ready":
        status = "needs_preparation"
    if not cons["authorized_channels"]:
        unknowns.append("未提供已授权渠道：方案可设计，但现在不能执行任何发布")
    if cons["data_sources"]:
        missing_sources = [s for s in OBJECTIVES[target["id"]]["sources"]
                           if s not in cons["data_sources"]]
        if missing_sources:
            unknowns.append(f"结果核验缺少数据源：{', '.join(missing_sources)}")
    else:
        unknowns.append("未提供任何数据渠道：只能观察平台回执，业务结果无法验证")
    for item in (chosen["missing"] if chosen else []):
        if not item["producible"]:
            unknowns.append(f"缺少不可临时补齐的能力：{item['label']}")

    contract = _outcome_contract(target["id"], cons)
    return {
        "schema": SCHEMA,
        "task": task,
        "status": status,
        "object": obj,
        "objective": target,
        "motions": motions,
        "selected_motion": chosen["id"] if chosen else None,
        "channels": {"primary": primary, "supporting": support,
                     "unauthorized": unauthorized[:8],
                     "unauthorized_total": len(unauthorized),
                     "excluded": excluded[:8]},
        "constraints": cons,
        "constraint_checks": _constraint_checks(cons, chosen, obj["id"]),
        "outcome_contract": contract,
        "proposal": prop,
        "unknowns": list(dict.fromkeys(unknowns)),
        "next_steps": _next_steps(chosen, primary, cons),
        "handoff": {
            "role_orchestration": f"mkt orchestrate --motion {chosen['id']}" if chosen else "",
            "execution": "mkt plan/queue + 人审后执行；本设计不发送",
        },
        "boundary": ("对象/目标由信号推断，机制由兼容性与约束筛选；"
                     "设计不是执行结果，也不证明渠道有效。"),
    }


def _constraint_checks(cons: Dict[str, Any], chosen: Optional[Dict[str, Any]],
                       object_id: str) -> List[Dict[str, str]]:
    checks: List[Dict[str, str]] = []
    if not cons["authorized_channels"]:
        checks.append({"rule": "authorized_channels", "status": "warn",
                       "detail": "没有授权渠道：只能出方案与素材，不能发布"})
    else:
        known = ", ".join(cons["authorized_channels"])
        checks.append({"rule": "authorized_channels", "status": "pass",
                       "detail": f"已声明授权渠道：{known}；仍需逐条核对平台权限"})
    if cons["region"]:
        checks.append({"rule": "region", "status": "pass",
                       "detail": f"地区约束 {cons['region']} 已用于渠道过滤"})
    if chosen and chosen["id"] in PAID_MOTIONS:
        if cons["budget"] in {"none", "unknown"}:
            checks.append({"rule": "budget", "status": "warn",
                           "detail": "合作机制通常需要预算或对等资源；当前预算未确认"})
        else:
            checks.append({"rule": "budget", "status": "pass",
                           "detail": f"预算档位 {cons['budget']}，仍需按动作单独核算"})
    if object_id == "hardware" and chosen and chosen["id"] == "commerce_led":
        checks.append({"rule": "supply", "status": "warn" if "supply" not in cons["assets"] else "pass",
                       "detail": "硬件增长必须确认供应、交付与售后，否则订单会变成退款"})
    return checks


def _next_steps(chosen: Optional[Dict[str, Any]], primary: List[Dict[str, Any]],
                cons: Dict[str, Any]) -> List[str]:
    if not chosen:
        return ["补充对象、目标或约束后重新设计"]
    steps = list(chosen["sequence"])
    if not primary:
        steps.append("先补齐渠道授权与账号，再执行任何发布")
    if not cons["data_sources"]:
        steps.append("补一个能验证业务结果的数据渠道，否则只记录平台回执")
    return steps


def lint(payload: Dict[str, Any]) -> List[str]:
    """检查设计本身是否自洽；不评价策略好坏。"""
    problems: List[str] = []
    if payload.get("schema") != SCHEMA:
        problems.append(f"schema 应为 {SCHEMA}")
    if not str(payload.get("task") or "").strip():
        problems.append("task 为空")
    if payload.get("object", {}).get("id") not in OBJECTS:
        problems.append("object 非法")
    if payload.get("objective", {}).get("id") not in OBJECTIVES:
        problems.append("objective 非法")
    motions = payload.get("motions") or []
    if not motions:
        problems.append("没有任何候选机制")
    contract = payload.get("outcome_contract") or {}
    for key in ("primary_signal", "leading_signals", "guardrails", "not_success", "verification"):
        if key not in contract:
            problems.append(f"结果契约缺少 {key}")
    channels = payload.get("channels") or {}
    for bucket in ("primary", "supporting"):
        ids = [row["id"] for row in channels.get(bucket) or []]
        if len(ids) != len(set(ids)):
            problems.append(f"{bucket} 渠道重复")
    if len(channels.get("primary") or []) > MAX_PRIMARY:
        problems.append("主渠道超过上限")
    if payload.get("status") == "blocked" and not payload.get("unknowns"):
        problems.append("blocked 但没有说明未知项")
    return problems


def _table(headers: List[str], rows: List[List[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell).replace("|", "/") for cell in row) + " |")
    return "\n".join(lines)


def render(payload: Dict[str, Any]) -> str:
    lines = ["# GTM 设计", ""]
    obj = payload["object"]
    target = payload["objective"]
    lines.append(f"**任务**：{payload['task']}")
    lines.append(f"**对象**：{obj['label']}（置信度 {obj['confidence']}，来源 {obj['source']}）")
    lines.append(f"**目标**：{target['label']}（置信度 {target['confidence']}，来源 {target['source']}）")
    lines.append(f"**状态**：{payload['status']}")
    lines.append("")
    lines.append("## 候选机制")
    rows = []
    for row in payload["motions"]:
        missing = "、".join(item["label"] for item in row["missing"]) or "—"
        problems = "；".join(row["problems"]) or "—"
        rows.append([row["label"], row["status"], "是" if row["executable"] else "否",
                     missing, problems])
    lines.append(_table(["机制", "状态", "可执行", "缺口", "不适用原因"], rows))
    lines.append("")
    chosen = payload.get("selected_motion")
    if chosen:
        motion = next(row for row in payload["motions"] if row["id"] == chosen)
        lines.append(f"选择机制：**{motion['label']}** —— {motion['why']}")
        lines.extend([f"{idx + 1}. {step}" for idx, step in enumerate(motion["sequence"])])
        lines.append("")
    ch = payload["channels"]
    lines.append("## 渠道")
    for bucket, label in (("primary", "主渠道"), ("supporting", "辅助渠道"),
                          ("unauthorized", "未授权（需先授权）")):
        rows = [[row["name"], row["kind"], row["status"], "是" if row["authorized"] else "否"]
                for row in ch.get(bucket) or []]
        lines.append(f"**{label}**")
        lines.append(_table(["渠道", "类型", "证据状态", "已授权"], rows) if rows else "—")
        lines.append("")
    contract = payload["outcome_contract"]
    lines.append("## 结果契约")
    lines.append(f"- 主信号：{contract['primary_signal']['name']} —— {contract['primary_signal']['definition']}")
    lines.append(f"- 领先信号：{'、'.join(contract['leading_signals'])}")
    lines.append(f"- 反指标：{'、'.join(contract['guardrails'])}")
    lines.append(f"- 不算成功：{'、'.join(contract['not_success'])}")
    lines.append(f"- 观察窗口：{contract['observation_window']}")
    lines.append(f"- 停止条件：{contract['stop_rule']}")
    lines.append(f"- 验证等级：{contract['verification']['level']}（{contract['verification']['limit']}）")
    lines.append("")
    if payload["unknowns"]:
        lines.append("## 未知项")
        lines.extend([f"- {item}" for item in payload["unknowns"]])
        lines.append("")
    lines.append("## 下一步")
    lines.extend([f"{idx + 1}. {step}" for idx, step in enumerate(payload["next_steps"])])
    lines.append("")
    lines.append(f"> {payload['boundary']}")
    return "\n".join(lines)


def load_json_arg(value: str) -> Dict[str, Any]:
    """接受文件路径或内联 JSON。"""
    text = str(value or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        return json.loads(text)
    return json.loads(Path(text).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 交接：设计 → 岗位编排 / 目标契约
# ---------------------------------------------------------------------------
GOAL_HINTS: Dict[str, Dict[str, str]] = {
    "awareness": {"goal_id": "awareness", "reason": "设计目标是被发现与认知，对应声量目标"},
    "acquisition": {"goal_id": "adoption", "reason": "设计目标是合格对象获取，对应使用目标"},
    "activation": {"goal_id": "adoption", "reason": "设计目标是首次价值，对应使用目标"},
    "revenue": {"goal_id": "commercial", "reason": "设计目标是付费/订单/回款，对应商业目标"},
    "retention": {"goal_id": "commercial", "reason": "设计目标是留存/续费，对应商业目标"},
    "contribution": {"goal_id": "adoption", "reason": "设计目标是生态采用与贡献，对应使用目标"},
}


def handoff(payload: Dict[str, Any], *, product: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把设计交给岗位编排与目标契约；不执行、不写工作区。"""
    problems = lint(payload)
    if problems:
        raise GTMDesignError("设计不自洽，先运行 lint：" + "；".join(problems))
    objective = payload["objective"]["id"]
    hint = GOAL_HINTS[objective]
    result: Dict[str, Any] = {
        "schema": "growovo.gtm-handoff/1",
        "design_schema": payload.get("schema"),
        "task": payload["task"],
        "object": payload["object"],
        "objective": payload["objective"],
        "selected_motion": payload.get("selected_motion"),
        "outcome_contract": payload["outcome_contract"],
        "goal_hint": hint,
        "workstream": None,
        "notes": [],
        "next_commands": [],
    }
    if payload.get("status") == "blocked":
        result["notes"].append("设计状态为 blocked：先补对象/目标/机制，再交接")
    if not product:
        result["notes"].append("未提供产品事实包：只返回目标契约与岗位编排命令，不生成 Workstream")
    else:
        from . import orchestration
        plan = orchestration.orchestrate(
            product,
            OBJECTIVES[objective]["label"],
            payload["task"],
        )
        errors = orchestration.lint_orchestration(plan)
        plan["validation"] = {"ok": not errors, "errors": errors}
        result["workstream"] = plan
        result["notes"].append(
            "Workstream 是按产品事实与任务推断的候选岗位组合；它不是发布批准，也不代表设计选中的机制已被执行"
        )
    motion = payload.get("selected_motion") or ""
    result["next_commands"] = [
        f"mkt orchestrate --product <facts.json> --goal \"{OBJECTIVES[objective]['label']}\" --task \"{payload['task']}\"",
        f"mkt goal plan --facts <facts.json> --goal {hint['goal_id']}",
        "mkt plan / mkt queue：人审后执行；本交接不发送",
    ] if motion else ["补充机制后重新设计"]
    result["boundary"] = ("交接只把设计映射到既有岗位/目标接口；"
                          "不代表执行、渠道有效或业务结果成立。")
    return result


def render_handoff(payload: Dict[str, Any]) -> str:
    lines = ["# GTM 交接", ""]
    lines.append(f"**任务**：{payload['task']}")
    lines.append(f"**对象 → 目标**：{payload['object']['label']} → {payload['objective']['label']}")
    lines.append(f"**机制**：{payload.get('selected_motion') or '未选定'}")
    hint = payload["goal_hint"]
    lines.append(f"**目标契约映射**：`{hint['goal_id']}` —— {hint['reason']}")
    lines.append("")
    if payload.get("workstream"):
        plan = payload["workstream"]
        orders = (plan.get("workstream") or {}).get("work_orders") or []
        rows = [[row.get("role_family", ""), row.get("local_id", ""),
                 row.get("title", "")[:40]] for row in orders]
        lines.append("## 候选岗位与交付物")
        lines.append(_table(["岗位族", "工作单", "目的"], rows) if rows else "—")
        lines.append("")
        lines.append(f"编排校验：{'通过' if plan['validation']['ok'] else '失败'}")
    else:
        lines.append("## 候选岗位与交付物")
        lines.append("未提供产品事实包，暂不生成 Workstream。")
    if payload["notes"]:
        lines.append("")
        lines.append("## 说明")
        lines.extend(f"- {item}" for item in payload["notes"])
    lines.append("")
    lines.append("## 下一步")
    lines.extend(f"{idx}. `{cmd}`" for idx, cmd in enumerate(payload["next_commands"], start=1))
    lines.append("")
    lines.append(f"> {payload['boundary']}")
    return "\n".join(lines)
