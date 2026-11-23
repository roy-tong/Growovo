#!/usr/bin/env python3
"""平台风控参数层：把「平台在用什么信号判定机器」折成可执行的停手条件。

为什么单独一层
--------------
``core/platform_strategy.py`` 管「怎么写、发多少、什么不能写」；它回答不了
「同一个动作做到第几次会被平台当成机器」。2026 年 11 月这轮采集补上的正是
这一层：公众号把「脚本批量连续发布」写成明文违规，小红书把设备环境一致性
做成硬红线，X 把带链接帖的单价抬到纯文本的 13 倍，Reddit 的 public API 进入
关停倒计时。这些不是内容规则，是**通道与预算规则**，混进审稿器会互相干扰。

三条纪律写在代码里
------------------
1. 每条信号必须四件齐全：``threshold``（阈值）、``action``（触发后做什么）、
   ``source``（可追到来源 id）、``grade``（official / craft / lead）。
   缺一件 ``validate()`` 就报错。
2. ``source`` 只能指向本平台 ``sources`` 里声明过的 id；来源不落地等于没来源。
3. 只写「避免触发」的信号，不写绕过实现。验证码、指纹、代理池、马甲矩阵、
   AI 人性化改写这五类做法不在本模块的表达范围内。
4. ``observed`` 只收社区经验值（平台不公开的秒级/次数阈值），且只允许用来
   放慢节奏和提前熔断；它不能放宽 ``signals`` 里的任何阈值。

边界：本层不证明平台会或不会识别某个账号，只给出「按已知规则应该停在哪」。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, List, Optional

from . import platforms as platform_registry

SCHEMA = "growovo.platform-risk/1"

# 平台风控规则比内容玩法换得更快；60 天与策略层同窗口。
REVIEW_WINDOW_DAYS = 60

GRADES = ("official", "craft", "lead")
LEVELS = ("block", "throttle", "watch")
OBSERVED_CONFIDENCE = ("high", "medium", "low")

# ---------------------------------------------------------------------------
# 风控注册表
#
#   as_of        本卡最新事实对应的时点（不是采集时点）
#   sources      来源清单；signal.source 必须指向这里的 id
#   signals      检测信号：id / level / signal / threshold / action / source / grade
#   unverified   本轮没查到的，写清楚，不装懂
#   reviewed_at  本轮复核日；超过 REVIEW_WINDOW_DAYS 标「待刷新」
# ---------------------------------------------------------------------------
RISK: Dict[str, Dict[str, Any]] = {
    # ------------------------------------------------------------- 公众号
    "wechat": {
        "as_of": "2026-05",
        "sources": [
            {"id": "wechat-automation-2026-03",
             "label": "微信公众平台运营规范·非真人自动化创作（2026-03-27，媒体逐条转述）",
             "url": "https://www.thepaper.cn/newsDetail_forward_32934993", "grade": "official"},
            {"id": "wechat-diversion-2026-05",
             "label": "公众号账号检测新增导流维度（2026-05，媒体转述）",
             "url": "https://m.sohu.com/a/1038473275_122279841", "grade": "craft"},
        ],
        "signals": [
            {"id": "scripted_bulk_publish", "level": "block",
             "signal": "脚本、程序托管等自动化方式批量、连续发布",
             "threshold": "单日原创 ≤1 篇；两次群发间隔 ≥20 小时；任何批量循环发布都算违规",
             "action": "定时任务只出草稿，群发必须逐篇人工确认；批量循环路径不接线",
             "source": "wechat-automation-2026-03", "grade": "official"},
            {"id": "diversion_fields", "level": "throttle",
             "signal": "简介/正文/评论/消息里的联系方式、外链、二维码",
             "threshold": "正文 0 个联系方式、0 个站外链接、0 个二维码",
             "action": "发布前拦截；命中后文章会标「不符合内容推荐条件」，删掉再发",
             "source": "wechat-diversion-2026-05", "grade": "craft"},
            {"id": "low_read_through", "level": "watch",
             "signal": "大量读者在 30% 进度前退出，被判低质或标题欺诈",
             "threshold": "单篇 30% 前进度退出占比明显高于账号均值",
             "action": "不改发布参数，改内容：标题承诺与正文一致、首屏三段给结论",
             "source": "wechat-automation-2026-03", "grade": "lead"},
        ],
        "observed": [],
        "unverified": [
            "规范原文页在 mp.weixin.qq.com 登录墙后，本轮只核到媒体逐条转述。",
            "「30% 进度前退出判低质」只有单一来源，按方向使用，不当阈值。",
        ],
        "reviewed_at": "2026-11-21",
    },

    # ------------------------------------------------------------- 小红书
    "xiaohongshu": {
        "as_of": "2026-04-27",
        "sources": [
            {"id": "xhs-2026-04-rules",
             "label": "小红书 2026-04 新规「人工+AI 双审」8 条红线（媒体逐条转述）",
             "url": "https://www.ucsiwei.com/news/xiaohongshu-2026-april-new-rules-8-red", "grade": "official"},
        ],
        "signals": [
            {"id": "shared_environment", "level": "block",
             "signal": "同一 WiFi 下同时在线超过 3 个账号，触发设备环境异常",
             "threshold": "同一网络同时在线账号 ≤3，且须为不同实名主体",
             "action": "立即停止该网络下一切发布，账号静默 7 天；不在同一网络登录第二个号",
             "source": "xhs-2026-04-rules", "grade": "official"},
            {"id": "login_churn", "level": "block",
             "signal": "单日登录/登出超过 2 次，账号被标「营销设备」",
             "threshold": "单账号单日登录/登出 ≤2 次；换设备后 24 小时内不切回",
             "action": "停止切换设备；已标记的账号停止发布，只做浏览与回评",
             "source": "xhs-2026-04-rules", "grade": "official"},
            {"id": "emulator_or_farm", "level": "block",
             "signal": "模拟器、云手机、群控",
             "threshold": "0 使用",
             "action": "不安装、不接入；检测到即停止该账号一切操作",
             "source": "xhs-2026-04-rules", "grade": "official"},
            {"id": "duplicate_material", "level": "block",
             "signal": "同素材多账号相似度超过 50%（含自我抄袭）",
             "threshold": "跨账号相似度硬线 50%，内控取 30%；同一素材 30 天内不复用",
             "action": "阻断发布；换素材或换角度，不允许改标题重发",
             "source": "xhs-2026-04-rules", "grade": "official"},
            {"id": "ai_unlabeled", "level": "throttle",
             "signal": "AI 生成或大幅改写未标识",
             "threshold": "AI 参与度 >30% 必须打「AI 辅助创作」标识",
             "action": "阻断发布直到加标识；已限流内容先补标再申诉",
             "source": "xhs-2026-04-rules", "grade": "official"},
            {"id": "private_diversion", "level": "block",
             "signal": "变体词（薇/卫星）、小卡片、包裹二维码导流私域",
             "threshold": "0 处",
             "action": "发布前拦截；已发内容删除，承接只走平台内沟通",
             "source": "xhs-2026-04-rules", "grade": "official"},
            {"id": "absolute_terms", "level": "throttle",
             "signal": "广告极限词（最好/最强/100%/彻底解决）",
             "threshold": "0 处",
             "action": "发布前替换为可验证的具体表述",
             "source": "xhs-2026-04-rules", "grade": "official"},
            {"id": "unreported_promo", "level": "throttle",
             "signal": "商业推广未走蒲公英报备",
             "threshold": "商业推广 100% 报备",
             "action": "转蒲公英流程，不直接发",
             "source": "xhs-2026-04-rules", "grade": "official"},
        ],
        "observed": [],
        "unverified": [
            "8 条红线的平台原文页未拿到，只有媒体逐条转述。",
            "「同 WiFi >3 账号」可能是「异常设备环境」的描述性说法，不是精确阈值。",
        ],
        "reviewed_at": "2026-11-21",
    },

    # ------------------------------------------------------------- 抖音
    "douyin": {
        "as_of": "2025-09-01",
        "sources": [
            {"id": "douyin-aigc-2025-09",
             "label": "《人工智能生成合成内容标识办法》2025-09-01 生效（媒体报道）",
             "url": "https://www.stdaily.com/web/gdxw/2025-09/02/content_393775.html", "grade": "official"},
            {"id": "douyin-convention-34",
             "label": "《社区自律公约》第 34 条恶意导流（2026-10 媒体引述）",
             "url": "https://noobclaw.com/cn/blog/douyin-image-text-post-traffic/", "grade": "official"},
        ],
        "signals": [
            {"id": "aigc_unlabeled", "level": "throttle",
             "signal": "AIGC 内容未主动标识，被加「疑似 AI 生成」标识并限流",
             "threshold": "AI 参与度 >30% 必须标识；纯 AI 生成不做主推",
             "action": "发布前补标识；已限流内容先补标再申诉",
             "source": "douyin-aigc-2025-09", "grade": "official"},
            {"id": "cross_platform_mark", "level": "block",
             "signal": "画面或文案出现其他平台账号、水印、logo",
             "threshold": "0 处",
             "action": "阻断发布，回剪辑重做；素材逐帧检查角标",
             "source": "douyin-convention-34", "grade": "official"},
            {"id": "contact_diversion", "level": "throttle",
             "signal": "简介/评论/私信导流到站外",
             "threshold": "0 联系方式",
             "action": "删除，改走平台内私信",
             "source": "douyin-convention-34", "grade": "official"},
        ],
        "observed": [],
        "unverified": [
            "《社区自律公约》2026 版原文未拿到，第 34 条条号来自媒体引述。",
        ],
        "reviewed_at": "2026-11-21",
    },

    # ------------------------------------------------------------- 知乎
    "zhihu": {
        "as_of": "2026-11-21",
        "sources": [
            {"id": "zhihu-legacy",
             "label": "知乎旧版社区规范（2026 年新规原文未核）",
             "url": "https://www.zhihu.com/terms", "grade": "lead"},
        ],
        "signals": [
            {"id": "contact_diversion", "level": "throttle",
             "signal": "回答/评论/私信里的联系方式、站外链接",
             "threshold": "0 联系方式",
             "action": "发布前拦截；承接走平台内咨询入口",
             "source": "zhihu-legacy", "grade": "lead"},
            {"id": "bulk_answer", "level": "watch",
             "signal": "短时间大量回答、模板化结构",
             "threshold": "周 2–5 篇；单日 ≤2 篇",
             "action": "降速，不追热点式批量作答",
             "source": "zhihu-legacy", "grade": "lead"},
            {"id": "ai_unlabeled", "level": "watch",
             "signal": "AI 生成内容未声明",
             "threshold": "未定（知乎是否成文未核）",
             "action": "只记录，不改行为；成文后再升为门禁",
             "source": "zhihu-legacy", "grade": "lead"},
        ],
        "observed": [],
        "unverified": [
            "2026 年知乎官方规范原文未检索到；本节全部为旧版规范与二手总结。",
        ],
        "reviewed_at": "2026-11-21",
    },

    # ------------------------------------------------------------- X
    "x": {
        "as_of": "2026-02",
        "sources": [
            {"id": "x-automation-rules",
             "label": "X Automation Rules（长期有效，2026 未放宽）",
             "url": "https://help.x.com/en/rules-and-policies/x-automation", "grade": "official"},
            {"id": "x-pay-per-use-2026-02",
             "label": "X 开发者 pay-per-use 价目（2026-02，多家服务商一致转述）",
             "url": "https://www.networkx.ai/blog/x-twitter-api-limits-in-2026-what-every-automation-user-needs-to-know",
             "grade": "craft"},
        ],
        "signals": [
            {"id": "automated_likes", "level": "block",
             "signal": "程序化点赞",
             "threshold": "0 次",
             "action": "不实现、不接线；engagement 只走手动或人工确认",
             "source": "x-automation-rules", "grade": "official"},
            {"id": "bulk_follow", "level": "block",
             "signal": "批量关注/取关",
             "threshold": "0 次",
             "action": "不实现、不接线",
             "source": "x-automation-rules", "grade": "official"},
            {"id": "scripting_website", "level": "block",
             "signal": "脚本操作 x.com 网页端",
             "threshold": "0 次",
             "action": "ALLOWED_ROUTE 只有 official_api；网页端脚本路径不放行",
             "source": "x-automation-rules", "grade": "official"},
            {"id": "cross_account_duplicate", "level": "block",
             "signal": "多账号发同一内容",
             "threshold": "0 次",
             "action": "阻断；一账号一用途",
             "source": "x-automation-rules", "grade": "official"},
            {"id": "aggressive_dm", "level": "block",
             "signal": "高频、模板化私信",
             "threshold": "0 次",
             "action": "不实现批量私信；message 必须逐条人工确认",
             "source": "x-automation-rules", "grade": "official"},
            {"id": "account_count_over_10", "level": "block",
             "signal": "单开发者账号数超过 10 个，或账号用途重复",
             "threshold": "≤10 个，且用途互不重复",
             "action": "新增账号前先证明用途不同；做不到就不加",
             "source": "x-automation-rules", "grade": "official"},
            {"id": "link_post_cost", "level": "throttle",
             "signal": "带链接帖按 $0.20/条计费，是纯文本的 13 倍",
             "threshold": "单日带链接帖 ≤5 条（≈$1.00/日）",
             "action": "成本闸门：超限即停；默认把链接放首条回复",
             "source": "x-pay-per-use-2026-02", "grade": "craft"},
            {"id": "reply_downvote", "level": "watch",
             "signal": "回复被读者点踩（not interested/misleading/AI-generated/spam）并进排序",
             "threshold": "连续 7 天回复互动率下降",
             "action": "暂停自动回复，改人工；只记录，不改权重参数",
             "source": "x-automation-rules", "grade": "lead"},
        ],
        "observed": [
            {"id": "x-two-tier-window",
             "claim": "读接口同时受 15 分钟与 24 小时两个窗口约束；短窗口恢复不代表长窗口恢复，短窗口 reset 后继续突发会换来数小时冷却",
             "threshold": "按端点，15 分钟与 24 小时双窗口",
             "window_seconds": 900, "confidence": "high",
             "source_urls": ["https://docs.twexapi.io/fr/guides/twitter-api-429-403-401",
                             "https://api.sorsa.io/blog/twitter-api-rate-limits-2026",
                             "https://climbx.so/blog/x-api-limits"],
             "as_of": "2026-11-21"},
            {"id": "x-429-code-88",
             "claim": "超读限额返回 429 + error code 88，并带 x-rate-limit-reset 时间戳；等到该时间戳是唯一不会延长封锁的恢复方式",
             "threshold": "429 code 88 + x-rate-limit-reset",
             "window_seconds": None, "confidence": "high",
             "source_urls": ["https://docs.twexapi.io/fr/guides/twitter-api-429-403-401",
                             "https://www.networkx.ai/blog/x-twitter-api-limits-in-2026-what-every-automation-user-needs-to-know"],
             "as_of": "2026-11-21"},
        ],
        "unverified": [
            "pay-per-use 精确价目以 X 开发者后台为准；$0.20/带链接帖是多源转述。",
            "「回复点踩进排序」来自产品观察，官方未发布排序公式。",
        ],
        "reviewed_at": "2026-11-21",
    },

    # ------------------------------------------------------------- Reddit
    "reddit": {
        "as_of": "2026-09-30",
        "sources": [
            {"id": "reddit-api-shutdown-2026-09",
             "label": "r/redditdev 官方时间表（2026-09-30，多家二手一致转述）",
             "url": "https://reduck.ai/use-cases/reddit-api-alternatives", "grade": "craft"},
            {"id": "reddit-api-terms",
             "label": "Reddit Data API Terms / 自助服务规则（2026-08）",
             "url": "https://www.redditapis.com/blogs/reddit-bot-rules-2026", "grade": "official"},
            {"id": "reddit-automod-2026",
             "label": "各 sub 账号门槛与 CQS（2026-05/06 社区汇总）",
             "url": "https://www.subredditanalyzer.com/how-much-karma-do-you-need-to-post-on-reddit", "grade": "lead"},
        ],
        "signals": [
            {"id": "self_promo_ratio", "level": "throttle",
             "signal": "自我推广占比超过 1/10",
             "threshold": "官方 9:1 是底线；内控按 20:1",
             "action": "发布前算最近 20 条内容的推广占比，超 5% 即拦",
             "source": "reddit-api-terms", "grade": "official"},
            {"id": "api_shutdown_2027", "level": "block",
             "signal": "public API 2027-03 全部关闭；2027-01-12 起切断未登记应用",
             "threshold": "2027-01-12 为硬截止日",
             "action": "2026-12-31 前标记通道即将失效；截止后禁用自动发布，只保留人工",
             "source": "reddit-api-shutdown-2026-09", "grade": "craft"},
            {"id": "cqs_low", "level": "throttle",
             "signal": "Contributor Quality Score = Low/Lowest，即使 karma 达标也被 AutoMod 拦",
             "threshold": "CQS 非 Low 以下才发帖",
             "action": "不在该 sub 发帖，转 comment 参与",
             "source": "reddit-automod-2026", "grade": "lead"},
            {"id": "karma_gate", "level": "throttle",
             "signal": "未达 sub 的 comment karma / 号龄门槛",
             "threshold": "小型 sub 多数无门槛；中型 50–200 comment karma + 7–30 天；大型 200–500 + 14–30 天",
             "action": "karma 未达标前不做任何发布，先用 comment 养号",
             "source": "reddit-automod-2026", "grade": "lead"},
            {"id": "shadowban", "level": "watch",
             "signal": "帖子零曝光零互动但账号没有通知",
             "threshold": "未登录访问 reddit.com/user/<name> 看不到自己的帖子",
             "action": "停发 14 天并申诉，不换号重来",
             "source": "reddit-automod-2026", "grade": "lead"},
        ],
        "observed": [
            {"id": "reddit-young-account-write-throttle",
             "claim": "新号或低 karma 账号大约每 15 分钟只能写入 1 次；拒绝出现在 json.errors 的 RATELIMIT 里（you are doing that too much），HTTP 可能仍是 200",
             "threshold": "新号/低 karma 账号 1 次写入 / 15 分钟",
             "window_seconds": 900, "confidence": "high",
             "source_urls": ["https://certifiedkarma.com/blog/reddit-doing-that-too-much",
                             "https://www.soar.sh/blog/reddit-error-doing-that-too-much"],
             "as_of": "2026-11-21"},
            {"id": "reddit-subreddit-karma-gate",
             "claim": "子版准入门槛随规模上升：5k 订阅以下通常无门槛，50k–500k 需 200–500 combined karma 并养 2–4 周，大型子版 500–2000+ 需 3–6 个月；移除常常是静默的，登录状态看帖子仍像活着",
             "threshold": "50k–500k 订阅的子版需 200–500 combined karma",
             "window_seconds": None, "confidence": "high",
             "source_urls": ["https://warmstart.app/guides/reddit-karma-requirements",
                             "https://www.subredditanalyzer.com/how-much-karma-do-you-need-to-post-on-reddit"],
             "as_of": "2026-07-08"},
            {"id": "reddit-crosspost-dilution",
             "claim": "同一链接一天内推进 2–3 个子版会引来静默移除和 shadowban 风险；自我推广比例按子版分别执行，不是全局",
             "threshold": "同一链接每周最多 2 个子版",
             "window_seconds": None, "confidence": "medium",
             "source_urls": ["https://www.soar.sh/blog/self-promotion-rules-by-subreddit-database",
                             "https://www.ogsmedia.com/reddit-shadowban/"],
             "as_of": "2026-11-21"},
        ],
        "unverified": [
            "2026-09-30 官方时间表原帖未直接读到，只有多家二手一致转述。",
            "各 sub 的 AutoMod 门槛是社区汇总，不是官方文档。",
        ],
        "reviewed_at": "2026-11-21",
    },

    # ------------------------------------------------------------- LinkedIn
    "linkedin": {
        "as_of": "2026-07-20",
        "sources": [
            {"id": "linkedin-2026-limits",
             "label": "LinkedIn 2026 社区逆向阈值（2026-07-20）",
             "url": "https://www.yalc.ai/blog/linkedin-automation-limits-and-rules/", "grade": "lead"},
        ],
        "signals": [
            {"id": "browser_plugin", "level": "block",
             "signal": "浏览器插件式自动化（当前封号最高频触发源）",
             "threshold": "0 使用",
             "action": "不安装、不运行；所有 X 非 API 网页脚本写均被平台许可门禁止",
             "source": "linkedin-2026-limits", "grade": "lead"},
            {"id": "invite_burst", "level": "throttle",
             "signal": "连接请求超过日/周配额",
             "threshold": "≤20/日、≤100/周；新号前 14 天不发邀请，之后每周 +5",
             "action": "日/周配额用尽即停，不跨账号借额度",
             "source": "linkedin-2026-limits", "grade": "lead"},
            {"id": "pending_backlog", "level": "throttle",
             "signal": "pending 邀请积压被当作骚扰信号",
             "threshold": "pending <500，内控超 480 停止新增",
             "action": "停止新增邀请，先清理积压",
             "source": "linkedin-2026-limits", "grade": "lead"},
            {"id": "profile_scrape_burst", "level": "throttle",
             "signal": "短时间大量查看他人主页",
             "threshold": "不做批量查看",
             "action": "人工浏览，不批量",
             "source": "linkedin-2026-limits", "grade": "lead"},
        ],
        "observed": [],
        "unverified": [
            "LinkedIn 官方从未公布具体限额；20–25/日、100/周来自社区长期观察。",
        ],
        "reviewed_at": "2026-11-21",
    },

    # ------------------------------------------------------------- Hacker News
    "hackernews": {
        "as_of": "2026-11-21",
        "sources": [
            {"id": "hn-guidelines",
             "label": "Hacker News Guidelines（页面无版本号，长期稳定）",
             "url": "https://news.ycombinator.com/newsguidelines.html", "grade": "official"},
            {"id": "hn-api",
             "label": "Hacker News Firebase API（官方只读接口）",
             "url": "https://github.com/HackerNews/API", "grade": "official"},
        ],
        "signals": [
            {"id": "promo_primary", "level": "block",
             "signal": "账号主要用于推广，而不是参与技术讨论",
             "threshold": "最近 10 条内容里推广 >3 条即停",
             "action": "停发推广，先回技术讨论攒信誉",
             "source": "hn-guidelines", "grade": "official"},
            {"id": "vote_solicitation", "level": "block",
             "signal": "请求朋友或读者点赞、评论自己的帖子",
             "threshold": "0 次",
             "action": "删掉一切拉票请求，不组织投票",
             "source": "hn-guidelines", "grade": "official"},
            {"id": "show_hn_not_tryable", "level": "throttle",
             "signal": "Show HN 发的是落地页或纯博客文，读者无法直接试用",
             "threshold": "必须有一个能打开就用的东西",
             "action": "改成可直接试用的链接；否则不发 Show HN",
             "source": "hn-guidelines", "grade": "official"},
            {"id": "posting_burst", "level": "throttle",
             "signal": "短时间连续发帖触发 “You're posting too fast” 提示",
             "threshold": "新号 ≤1 帖/日；回复间隔 ≥60 秒",
             "action": "看到提示即停 24 小时，不换号重试",
             "source": "hn-api", "grade": "lead"},
        ],
        "observed": [],
        "unverified": [
            "HN 指南页本轮网络不可达，条文按长期稳定版本；下次复核须重读原文。",
            "HN 未公布发帖频率硬阈值；“posting too fast” 是页面级提示，不是文档条款。",
        ],
        "reviewed_at": "2026-11-21",
    },

    # ------------------------------------------------------------- GitHub
    "github": {
        "as_of": "2026-10-05",
        "sources": [
            {"id": "github-rate-limits",
             "label": "GitHub REST API rate limits（2026-10 文档）",
             "url": "https://docs.github.com/rest/using-the-rest-api/rate-limits-for-the-rest-api", "grade": "official"},
        ],
        "signals": [
            {"id": "primary_rate_limit", "level": "throttle",
             "signal": "认证请求 5000/小时用尽",
             "threshold": "剩余 <10% 即停",
             "action": "按 X-RateLimit-Reset 等待，不做重试风暴",
             "source": "github-rate-limits", "grade": "official"},
            {"id": "secondary_rate_limit", "level": "throttle",
             "signal": "REST 二级限流 900 points/分钟用尽",
             "threshold": "连续 3 次命中即当日停写",
             "action": "退避 60 秒；仍命中则停止该仓库一切写操作",
             "source": "github-rate-limits", "grade": "official"},
            {"id": "search_quota", "level": "throttle",
             "signal": "Search API 30/分钟用尽（与 REST 配额分开计算）",
             "threshold": "单次循环 ≤20 次查询",
             "action": "留 10 次余量，超限等待下一分钟",
             "source": "github-rate-limits", "grade": "official"},
            {"id": "unauthenticated", "level": "throttle",
             "signal": "未认证请求 60/小时",
             "threshold": "0 次批量未认证请求",
             "action": "批量操作必须带认证；未认证只做单次探索",
             "source": "github-rate-limits", "grade": "official"},
        ],
        "observed": [
            {"id": "github-secondary-900-points",
             "claim": "REST 二级限流允许 900 points/分钟，超限返回 403 + retry-after 而不是 429；只按状态码分类会把限流误判成权限问题",
             "threshold": "900 points/分钟，403 + retry-after",
             "window_seconds": 60, "confidence": "high",
             "source_urls": ["https://docs.github.com/rest/using-the-rest-api/rate-limits-for-the-rest-api",
                             "https://caseywest.com/github-gives-you-two-rate-limits/"],
             "as_of": "2026-11-21"},
            {"id": "github-graphql-2000-points",
             "claim": "GraphQL 走同一条 403 路径，上限 2,000 points/分钟；一次查询的成本不等于调用次数",
             "threshold": "2,000 points/分钟",
             "window_seconds": 60, "confidence": "high",
             "source_urls": ["https://docs.github.com/rest/using-the-rest-api/rate-limits-for-the-rest-api"],
             "as_of": "2026-11-21"},
            {"id": "github-concurrency-100",
             "claim": "并发请求上限 100，超过即 403；并发和速率是两道独立天花板",
             "threshold": "100 并发请求",
             "window_seconds": None, "confidence": "high",
             "source_urls": ["https://docs.github.com/rest/using-the-rest-api/rate-limits-for-the-rest-api"],
             "as_of": "2026-11-21"},
        ],
        "unverified": [],
        "reviewed_at": "2026-11-21",
    },
}

# 风控层可以比内容策略层多覆盖（github 有执行动作但暂无内容策略卡）；
# 反过来不行：每个 STRATEGY 平台必须有风控卡。test_platform_risk 会检查。
PLATFORMS = tuple(RISK)


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    # 政策月份只有年月时按当月 1 日计，避免为了过校验编一个具体日期。
    if len(text) == 7:
        try:
            return date.fromisoformat(text + "-01")
        except ValueError:
            return None
    return None


def _known() -> Dict[str, Dict[str, Any]]:
    return {pid: spec for pid, spec in RISK.items() if pid in platform_registry.BY_ID}


def get(platform: str) -> Dict[str, Any]:
    pid = str(platform or "").strip().lower()
    if pid not in RISK:
        raise KeyError(f"没有这个平台的风控卡：{platform}")
    return RISK[pid]


def signals(platform: str, *, level: Optional[str] = None,
            grade: Optional[str] = None) -> List[Dict[str, Any]]:
    out = [dict(s) for s in get(platform).get("signals") or []]
    if level:
        out = [s for s in out if s.get("level") == level]
    if grade:
        out = [s for s in out if s.get("grade") == grade]
    return out


def age_days(platform: str, on: Optional[Any] = None) -> Optional[int]:
    reviewed = _as_date(get(platform).get("reviewed_at"))
    if reviewed is None:
        return None
    today = _as_date(on) or date.today()
    return (today - reviewed).days


def stale(on: Optional[Any] = None) -> List[Dict[str, Any]]:
    out = []
    for pid in RISK:
        days = age_days(pid, on)
        if days is None or days > REVIEW_WINDOW_DAYS:
            out.append({"platform": pid, "reviewed_at": get(pid).get("reviewed_at"),
                        "age_days": days, "window_days": REVIEW_WINDOW_DAYS})
    return out


def card(platform: str, *, on: Optional[Any] = None) -> Dict[str, Any]:
    pid = str(platform or "").strip().lower()
    spec = get(pid)
    days = age_days(pid, on)
    sources = [dict(s) for s in spec.get("sources") or []]
    by_id = {s["id"]: s for s in sources}
    rows = []
    for sig in spec.get("signals") or []:
        row = dict(sig)
        src = by_id.get(str(sig.get("source") or ""))
        row["source_label"] = (src or {}).get("label")
        row["source_url"] = (src or {}).get("url")
        rows.append(row)
    return {
        "schema_version": SCHEMA,
        "platform": pid,
        "as_of": spec.get("as_of"),
        "sources": sources,
        "signals": rows,
        "observed": [dict(o) for o in spec.get("observed") or []],
        "observed_counts": {k: sum(1 for o in spec.get("observed") or []
                                   if o.get("confidence") == k)
                            for k in OBSERVED_CONFIDENCE},
        "observed_note": "社区经验值只用于放慢节奏和提前熔断，不作为放行依据",
        "unverified": list(spec.get("unverified") or []),
        "reviewed_at": spec.get("reviewed_at"),
        "age_days": days,
        "stale": days is None or days > REVIEW_WINDOW_DAYS,
        "window_days": REVIEW_WINDOW_DAYS,
        "scope": "只写检测信号与停手动作；不含任何绕过实现",
    }


def validate(platforms: Optional[Iterable[str]] = None) -> Dict[str, List[str]]:
    """返回 ``{platform: [问题]}``；空 dict 表示全部通过。"""
    problems: Dict[str, List[str]] = {}
    for pid in (platforms or RISK):
        issues: List[str] = []
        if pid not in RISK:
            problems[pid] = ["没有这个平台的风控卡"]
            continue
        if pid not in platform_registry.BY_ID:
            problems[pid] = ["platforms.py 里没有这个平台，风控卡会与执行层脱节"]
            continue
        spec = RISK[pid]
        reviewed = _as_date(spec.get("reviewed_at"))
        if reviewed is None:
            issues.append("缺少有效 reviewed_at")
        elif reviewed > date.today():
            issues.append("reviewed_at 在未来")
        as_of = _as_date(spec.get("as_of"))
        if as_of is None:
            issues.append("缺少有效 as_of")
        elif as_of > date.today():
            issues.append("as_of 在未来")
        sources = spec.get("sources") or []
        if not sources:
            issues.append("没有任何来源")
        source_ids = set()
        for src in sources:
            sid = str(src.get("id") or "").strip()
            if not sid:
                issues.append("来源缺少 id")
                continue
            if sid in source_ids:
                issues.append(f"来源 id 重复：{sid}")
            source_ids.add(sid)
            if not src.get("label"):
                issues.append(f"来源 {sid} 缺少 label")
            if not src.get("url"):
                issues.append(f"来源 {sid} 缺少 url")
            if src.get("grade") not in GRADES:
                issues.append(f"来源 {sid} 的 grade 非法")
        seen = set()
        for sig in spec.get("signals") or []:
            code = str(sig.get("id") or "").strip()
            if not code:
                issues.append("信号缺少 id")
                continue
            if code in seen:
                issues.append(f"信号 id 重复：{code}")
            seen.add(code)
            if sig.get("level") not in LEVELS:
                issues.append(f"信号 {code} 的 level 非法")
            if not sig.get("signal"):
                issues.append(f"信号 {code} 缺少描述")
            if not sig.get("threshold"):
                issues.append(f"信号 {code} 缺少阈值")
            if not sig.get("action"):
                issues.append(f"信号 {code} 缺少触发后动作")
            if sig.get("grade") not in GRADES:
                issues.append(f"信号 {code} 缺少合法 grade")
            ref = str(sig.get("source") or "").strip()
            if not ref:
                issues.append(f"信号 {code} 没有来源")
            elif ref not in source_ids:
                issues.append(f"信号 {code} 指向未声明的来源：{ref}")
        if not spec.get("signals"):
            issues.append("没有任何检测信号")
        observed = spec.get("observed")
        if not isinstance(observed, list):
            issues.append("缺少 observed 列表（可以为空列表）")
        else:
            seen_obs = set()
            for item in observed:
                if not isinstance(item, dict):
                    issues.append("observed 里有非字典条目")
                    continue
                oid = str(item.get("id") or "").strip()
                if not oid:
                    issues.append("经验条目缺少 id")
                    continue
                if oid in seen_obs:
                    issues.append(f"经验 id 重复：{oid}")
                seen_obs.add(oid)
                if not item.get("claim"):
                    issues.append(f"经验 {oid} 缺少 claim")
                if not item.get("threshold"):
                    issues.append(f"经验 {oid} 缺少 threshold")
                if item.get("confidence") not in OBSERVED_CONFIDENCE:
                    issues.append(f"经验 {oid} 的 confidence 非法")
                obs_day = _as_date(item.get("as_of"))
                if obs_day is None:
                    issues.append(f"经验 {oid} 缺少有效 as_of")
                elif obs_day > date.today():
                    issues.append(f"经验 {oid} 的 as_of 在未来")
                window = item.get("window_seconds")
                if window is not None and (isinstance(window, bool)
                                           or not isinstance(window, int) or window < 0):
                    issues.append(f"经验 {oid} 的 window_seconds 必须是非负整数或 null")
                urls = item.get("source_urls")
                if not isinstance(urls, list) or not urls:
                    issues.append(f"经验 {oid} 没有来源链接")
                else:
                    for url in urls:
                        text = str(url or "").strip()
                        if not (text.startswith("http://") or text.startswith("https://")):
                            issues.append(f"经验 {oid} 的来源链接不是 http(s)：{text or '空'}")
        if issues:
            problems[pid] = issues
    return problems


def render_card(platform: str, *, on: Optional[Any] = None) -> str:
    c = card(platform, on=on)
    lines = [f"## 风控（as_of {c['as_of']}｜复核 {c['reviewed_at']}）", ""]
    if c["stale"]:
        lines.append(f"> **待刷新**：已 {c['age_days']} 天，窗口 {c['window_days']} 天。")
        lines.append("")
    order = {name: i for i, name in enumerate(LEVELS)}
    rows = sorted(c["signals"], key=lambda s: order.get(s.get("level"), 99))
    for sig in rows:
        lines.append(f"- [{sig['level']}][{sig['grade']}] {sig['signal']}")
        lines.append(f"  - 阈值：{sig['threshold']}")
        lines.append(f"  - 触发后：{sig['action']}")
        if sig.get("source_label"):
            lines.append(f"  - 来源：{sig['source_label']}")
    if c["observed"]:
        lines.append("")
        lines.append("社区经验（只用于放慢节奏）：")
        for item in c["observed"]:
            lines.append(f"- [{item['confidence']} {item['as_of']}] {item['claim']}")
            lines.append(f"  - 阈值：{item['threshold']}")
            urls = "；".join(item.get("source_urls") or [])
            if urls:
                lines.append(f"  - 来源：{urls}")
    if c["unverified"]:
        lines.append("")
        lines.append("未核项：")
        lines += [f"- {x}" for x in c["unverified"]]
    lines.append("")
    lines.append(f"> {c['scope']}。")
    return "\n".join(lines)
