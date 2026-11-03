#!/usr/bin/env python3
"""GTM diagnosis engine (product strategy v2, module M1).

Turns a product facts pack into a platform-priority GTM report. Read-only:
no model calls, no network, no writes. Design constraints from the strategy
doc (ai输出/Growovo-产品策略v2-七步闭环-20261103.md):

- every recommendation line must carry an evidence label (no naked advice);
- platform knowledge lives in a registry whose entries cite their sources;
- value events must be defined up front, approximations flagged explicitly;
- the report is a scaffold for judgment, not a promise of outcomes.

Facts pack example (JSON)::

    {
      "product": {"name": "x", "what": "…", "url": "…", "stage": "launched",
                  "product_type": "devtool"},
      "icp": [{"hypothesis": "…", "tags": ["developers"],
               "evidence_label": "hypothesis", "evidence": "…"}],
      "goals": [{"layer": "G2", "metric": "激活", "target": 20}],
      "value_events": [{"name": "激活", "source": "产品内事件",
                        "is_approximation": false}],
      "constraints": {"red_lines": ["不做群发"], "human_minutes_budget": 720},
      "owned_assets": {"platforms": ["github"]}
    }

Evidence labels: ``self_ab`` (own controlled comparison), ``self_incident``
(own incident, usually negative evidence), ``external_baseline`` (third-party
verified numbers), ``hypothesis`` (untested, must be labeled as such).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

LABELS = {
    "self_ab": "已验证·自家A/B",
    "self_incident": "已验证·自家事故",
    "self_research": "已验证·自家研究",
    "external_baseline": "已验证·外部基准",
    "hypothesis": "假设·待测",
}

# 平台注册表：docs/playbooks/ 手册库的结构化投影。motions 每条必须带 label + evidence，
# 与 docs/cases 案例互链；cold_start/scale_gate 同样挂标签；risk 指向手册或 docs/risk/。
# 手册是活文档：打法被实测推翻时先改手册，再同步本表。
PLATFORMS: List[Dict[str, Any]] = [
    {
        "id": "github",
        "name": "GitHub",
        "icp_tags": ["developers", "oss-maintainers", "technical-founders"],
        "product_types": ["devtool", "oss", "sdk", "api"],
        "channel_status": "PROVEN",
        "motions": [
            {"motion": "上游交付链：审计→issue→PR→合并", "label": "self_ab",
             "evidence": "Verovo 全周期复盘 2026-10-31：唯二可归因自然星均来自交付链"},
            {"motion": "仓库 README 问题钩子 + 一键安装", "label": "hypothesis",
             "evidence": "Askovo P0 在跑（a6055ad），D14=2026-11-14 回填"},
            {"motion": "买星 / 拉票式求 star", "label": "self_incident",
             "evidence": "Verovo：92+42+~13 购入星污染全部归因，铁律「不刷量永久」",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "README 首屏：一句话定位 + 问题钩子 + 一行安装",
                "issue 入口留「免费任务/反馈」通道",
                "上游交付链首批 5 个候选仓库（审计→有价值 issue）",
            ],
            "expected": "自然 star 个位数/周 [已验证·自家系数：交付链 ~1–3 star/周，Verovo 2026-10-31]",
        },
        "scale_gate": "连续 2 周 star 净增 ≥10 且有人带真实材料进仓 → 才允许加投入",
        "risk": "低-中（API 限额与 spam 判定见 docs/risk/github.md）",
    },
    {
        "id": "hackernews",
        "name": "Hacker News",
        "icp_tags": ["developers", "technical-founders", "researchers"],
        "product_types": ["devtool", "oss", "sdk", "api", "research"],
        "channel_status": "PROVEN（只读）/ UNVERIFIED（写）",
        "motions": [
            {"motion": "Show HN（自有项目，人审零 LLM 底线）", "label": "hypothesis",
             "evidence": "Askovo P0 备稿在等发布窗口；首例数据 D14 回填"},
            {"motion": "机打内容 / LLM 生成文本直接提交", "label": "self_incident",
             "evidence": "Verovo Show HN 被分类器判 genAI kill（2026-10）→ 红线「零 LLM、只读、不拉票」",
             "killed": True},
            {"motion": "目标客群语料挖掘（Algolia 只读）", "label": "external_baseline",
             "evidence": "Askovo P0：8,235 条语料→478 候选的可复现管线（2026-10-31）"},
        ],
        "cold_start": {
            "checklist": [
                "标题 ≤80 字符，Show HN 限自有项目",
                "发布窗口：周二–周四 美东上午",
                "发布后 1h 内跟首评（设计动机/数据）",
            ],
            "expected": "launch 脉冲量级，方差极大 [假设·待测：本项目未验证]",
        },
        "scale_gate": "Show HN 进前 30 或带 ≥10 个真实讨论 → 才规划第二次发射",
        "risk": "中（官方指南亲核：禁 AI 文/禁自动发帖/禁拉票，见 docs/risk/hackernews.md）",
    },
    {
        "id": "product-hunt",
        "name": "Product Hunt",
        "icp_tags": ["technical-founders", "designers", "indie-makers"],
        "product_types": ["product", "devtool", "saas"],
        "channel_status": "UNVERIFIED",
        "motions": [
            {"motion": "首发 launch（含 maker 故事页，全天答评）", "label": "external_baseline",
             "evidence": "Momen 当日 #1 ≈500 注册/+500% 流量；官方唯一铁律原文「不能直接求票」（手册 product-hunt.md §3）"},
            {"motion": "拉票 / 激励投票 / 动员新注册小号", "label": "external_baseline",
             "evidence": "官方 Launch Guide 明令；ML 投票环检测连真票作废（手册 §3②）",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "产品页：30s 演示 GIF + 首图 + 3 张真实案例卡",
                "maker 页与关系披露；账号有基本活跃历史",
                "00:01–02:00 PT 发布，全天在线答评",
            ],
            "expected": "脉冲型：#1 量级数百注册（Momen/Swytchcode 先例），平庸 launch 仅 20–30 活跃支持者量级 [外部基准]",
        },
        "scale_gate": "launch 日进品类前 10 → 才规划二次曝光或跨站同步",
        "risk": "中（一票≠一分加权制；官方规则已核，见 docs/playbooks/海外/product-hunt.md）",
    },
    {
        "id": "xiaohongshu",
        "name": "小红书",
        "icp_tags": ["consumers", "researchers", "indie-makers"],
        "product_types": ["consumer-app", "product", "saas"],
        "channel_status": "UNVERIFIED",
        "motions": [
            {"motion": "独立产品冷启动：内测过程笔记 + 评论区共创", "label": "external_baseline",
             "evidence": "SunAlly 首篇 3000 赞/2 月 2 万用户/90% 用户来自站内；官方 5 万独立开发者（手册 xiaohongshu.md §4）"},
            {"motion": "先搜再做：关键词语料读评论挖需求再定方向", "label": "external_baseline",
             "evidence": "SunAlly 读 1000+ 条「晒太阳」笔记评论后调整产品方向（同上）"},
            {"motion": "笔记/私信导流微信、免费资料引流站外", "label": "external_baseline",
             "evidence": "《交易导流违规管理细则》2025-03-12：五情形违规，免费不豁免、小号连坐（手册 §3②）",
             "killed": True},
            {"motion": "AI 批量产笔记不标识", "label": "external_baseline",
             "evidence": "AIGC 标识办法 2025-09-01 施行；约 60 万篇未标识笔记被限流（手册 §3③）",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "搜产品关键词：笔记量与评论区痛点（先搜再做）",
                "首篇发内测过程：截图 + 你为什么做它 + 你的故事",
                "评论区当客服与共创区；好建议公开采纳",
            ],
            "expected": "案例区间：一周 800 内测、两月 2 万用户（SunAlly，外部先例，方差大）",
        },
        "scale_gate": "评论区稳定出现「怎么下载/求链接」→ 才值得加产能",
        "risk": "中-高（导流红线最严平台；官方规则部分已核，见手册）",
    },
    {
        "id": "zhihu",
        "name": "知乎",
        "icp_tags": ["developers", "b2b-buyers", "product-managers"],
        "product_types": ["b2b-saas", "saas", "devtool", "product"],
        "channel_status": "UNVERIFIED",
        "motions": [
            {"motion": "回答决策型问题（高关注低竞争选题）", "label": "external_baseline",
             "evidence": "B2B 决策人带问题搜知乎；回答长尾持续数月（手册 zhihu.md §2/§4，二手已标注）"},
            {"motion": "模板化回答矩阵引向同一产品", "label": "external_baseline",
             "evidence": "营销号专项：伪造经历/不当获利通常永久封禁（官方口径，手册 §3②）",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "搜品类词，选关注 100+ / 回答 <50 的问题",
                "「先说结论」结构 + 真实使用细节 + 承认局限",
                "前 10 答集中在同一领域（领域权重逻辑）",
            ],
            "expected": "搜索长尾 2–8 周起量，好回答持续数年 [外部基准·二手]",
        },
        "scale_gate": "单篇回答月均阅读稳定 + 出现私信/评论咨询 → 扩到相邻问题",
        "risk": "中（盐值体系+营销号专项；排序算法官方未公开，见手册）",
    },
    {
        "id": "wechat",
        "name": "微信公众号",
        "icp_tags": ["consumers", "b2b-buyers", "product-managers"],
        "product_types": ["saas", "product", "consumer-app"],
        "channel_status": "UNVERIFIED",
        "motions": [
            {"motion": "搜一搜占位：产品词/品类词文章（别处看到→微信搜你）", "label": "external_baseline",
             "evidence": "兴趣推荐时代搜一搜占入口 ~15%；零粉被看见三条路径之首（手册 wechat-official.md §2）"},
            {"motion": "「关注后回复 XX 领资料」式诱导关注", "label": "external_baseline",
             "evidence": "官方运营规范：诱导关注处罚梯度直至封号（手册 §3①）",
             "killed": True},
            {"motion": "正文引导加个人微信/进群（非企微通道）", "label": "external_baseline",
             "evidence": "违规导流，重者限制群发；合规路径=企微/客服组件（手册 §3②）",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "注册即占产品名（名称唯一性）",
                "3–5 篇「产品是什么/解决什么/怎么用」，标题埋产品词",
                "AI 辅助写作主动声明；AI 占比大不声明原创",
            ],
            "expected": "不做冷启动渠道：承接外部平台搜索，4–8 周搜一搜起量 [推测]",
        },
        "scale_gate": "搜一搜产品词稳定有量 + 推荐漏斗进池 → 加内容产能",
        "risk": "中（诱导类处罚重；AI 声明公告已核，见手册）",
    },
    {
        "id": "juejin",
        "name": "掘金",
        "icp_tags": ["developers"],
        "product_types": ["devtool", "sdk", "api", "oss"],
        "channel_status": "UNVERIFIED",
        "motions": [
            {"motion": "实践长文（真实项目+数据+踩坑），产品作为工具出现", "label": "external_baseline",
             "evidence": "官方推荐标准：原创/本人翻译+启发性+排版（官方小册已核，手册 juejin.md §2）"},
            {"motion": "官方活动拿首波流量（日新/金石计划，首发原创）", "label": "external_baseline",
             "evidence": "日新计划上榜规则官方帖已核（手册 §3④）"},
            {"motion": "转载/拼凑/百科式无个人洞察文", "label": "external_baseline",
             "evidence": "官方「不会被推荐」清单点名（2022-01 官方帖，手册 §2）",
             "killed": True},
            {"motion": "互赞群", "label": "external_baseline",
             "evidence": "官方通报锁定 6 用户降权+违规登记+掘力值扣减（手册 §3③）",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "一篇「用 XX 解决 XX 的完整实践」（先审后推机制）",
                "参加日新/金石计划拿官方流量位",
                "上首页后不随意编辑（可能掉出）",
            ],
            "expected": "首页推荐 1–3 天见分晓；搜索长尾数周 [外部基准]",
        },
        "scale_gate": "垂直发文数据累积明显（作者实测复盘）→ 提频",
        "risk": "低-中（先审后推+刷赞留痕处罚，见手册）",
    },
    {
        "id": "reddit",
        "name": "Reddit",
        "icp_tags": ["developers", "technical-founders"],
        "product_types": ["devtool", "oss", "saas", "consumer-app"],
        "channel_status": "UNVERIFIED",
        "motions": [
            {"motion": "居民期（4–8 周）真实答问，产品以披露身份回应求助帖", "label": "external_baseline",
             "evidence": "官方 10:1 自我推广指南（原文直引）+ 各 sub 自治规则（手册 reddit.md §3①）"},
            {"motion": "AI 生成内容不披露 / 新号直发产品链接", "label": "self_incident",
             "evidence": "Verovo 2026-10：r/SaaS 被 AutoModerator 判 AI 删帖；2024–25 各 sub AI 新政收紧",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "读目标 sub sidebar/pinned/删帖模式（先判断允不允许自我推广）",
                "账号 4–8 周居民期：答专业问题攒 karma",
                "提及产品必须披露 maker 身份，严格遵守 10:1",
            ],
            "expected": "Google 对 Reddit 帖权重高，长尾双倍；居民期 4–8 周无产出是纪律成本 [外部基准]",
        },
        "scale_gate": "常答的 sub 内声望建立 + 评论出现「link?」 → 才考虑 show 帖",
        "risk": "高（AutoMod 静默过滤零数据；自家判 AI 删帖事故，见手册）",
    },
    {
        "id": "v2ex",
        "name": "V2EX",
        "icp_tags": ["developers", "indie-makers"],
        "product_types": ["devtool", "oss", "consumer-app", "saas"],
        "channel_status": "UNVERIFIED",
        "motions": [
            {"motion": "「分享创造」节点发布：产品+解决什么+技术栈+社区福利", "label": "external_baseline",
             "evidence": "社区共识：分享自己作品（含商业）在分享创造允许且受欢迎（官方规则页已核，手册 v2ex.md §2）"},
            {"motion": "综合节点发广告 / 伪装普通帖软广", "label": "external_baseline",
             "evidence": "官方规则禁止；批量注册发推广封号（手册 §3）",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "账号先参与两周技术讨论",
                "「分享创造」发布：标题=产品名+一句话价值；附邀请码/免费额度",
                "诚实写局限；被处理不换号（加重）",
            ],
            "expected": "发布后 48h 是全部窗口；同行反馈+早期用户（量级无对照，推测几十–几百 UV）",
        },
        "scale_gate": "一次发布拿到有效反馈与首批用户 → 重大更新再发",
        "risk": "低-中（人工管理+自治举报；冻结可申诉，见手册）",
    },
    {
        "id": "bilibili",
        "name": "B站",
        "icp_tags": ["developers", "consumers", "students"],
        "product_types": ["devtool", "saas", "product"],
        "channel_status": "UNVERIFIED",
        "motions": [
            {"motion": "完整使用教程/过程视频（10 分钟内高信息密度）", "label": "external_baseline",
             "evidence": "初始池阈值完播>40%/点赞>5% 进推荐；深度内容按消耗时长不亏（手册 bilibili.md §2，第三方源已标注）"},
            {"motion": "商业合作走花火报备", "label": "external_baseline",
             "evidence": "花火为官方商业接单平台（存在已核，细则未核）"},
            {"motion": "搬运/二创无授权", "label": "external_baseline",
             "evidence": "版权+原创判定双重死（手册 §5）",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "一条教程视频：标题带搜索词（XX 教程/测评）",
                "月 2–4 条精品 > 周更水文",
                "简介放产品链接（较宽松）+ UTM",
            ],
            "expected": "视频长尾数月；小播放高收藏的教程可能是最值钱的 [外部基准]",
        },
        "scale_gate": "完播率与收藏率达标（播放>40%/点赞>5% 线）→ 加产能",
        "risk": "低-中（先审+原创指纹，见手册）",
    },
    {
        "id": "douyin",
        "name": "抖音",
        "icp_tags": ["consumers"],
        "product_types": ["consumer-app", "product"],
        "channel_status": "UNVERIFIED",
        "motions": [
            {"motion": "消费级 AI 工具的「惊艳效果」演示钩子", "label": "hypothesis",
             "evidence": "算法转向收藏/长时播指标对工具类更友好（36氪，手册 douyin.md §2）；本项目未验证"},
            {"motion": "未报备软广私单（星图外接单）", "label": "external_baseline",
             "evidence": "商业合作必须走巨量星图报备（官方帮助中心已核）；私单有限流风险（手册 §3）",
             "killed": True},
            {"motion": "devtool/B2B 在抖音做增长", "label": "hypothesis",
             "evidence": "人群错配；工具类投入产出低于 B站/小红书（手册 §10 判断）",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "3 条测试视频看收藏率（钩子型内容）",
                "前 3 秒给结论/冲突",
            ],
            "expected": "单条 24–72h 定生死；一条视频成本半天起、10–30 条出一条 [行业经验，未核实]",
        },
        "scale_gate": "收藏率稳定 + 评论「怎么下载」 → 加产能",
        "risk": "中（未报备限流；AI 标识国家规定适用）",
    },
    {
        "id": "channels",
        "name": "视频号",
        "icp_tags": ["consumers", "b2b-buyers"],
        "product_types": ["saas", "product"],
        "channel_status": "UNVERIFIED",
        "motions": [
            {"motion": "公众号内容切片 + 微信群首发转发（私域放大器）", "label": "hypothesis",
             "evidence": "社交链分发、转发是第一指标（第三方对比分析，手册 channels.md §2）"},
            {"motion": "零私域团队冷启动视频号", "label": "hypothesis",
             "evidence": "无社交节点=无分发（手册 §10 判断：有私域才做）",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "先有公众号/企微承接再开视频号",
                "把公众号最高打开率的三篇改成 60s 口播",
            ],
            "expected": "跟随私域规模；有群的团队当周可见转发数据 [假设]",
        },
        "scale_gate": "转发数据稳定 → 直播试水（知识付费场景）",
        "risk": "中（微信统一风控，AI 声明公告适用；生态组件非独立渠道）",
    },
    {
        "id": "linkedin",
        "name": "LinkedIn",
        "icp_tags": ["b2b-buyers", "product-managers", "technical-founders"],
        "product_types": ["b2b-saas", "saas", "devtool"],
        "channel_status": "半自动（UNVERIFIED）",
        "motions": [
            {"motion": "定制化连接（引用对方公开具体事）+ 低量节奏（≤8/日）", "label": "external_baseline",
             "evidence": "封号触发器=低接受率/低回复率而非数量（社区逆向，手册 linkedin.md §3）"},
            {"motion": "任何第三方自动化工具", "label": "external_baseline",
             "evidence": "官方政策明文禁止一切第三方自动化（官方帮助页已核）",
             "killed": True},
            {"motion": "模板群发冷触达", "label": "self_ab",
             "evidence": "Verovo X 实测平移：模板冷触达 ≈1.5% vs 深度定制 2/2（X 手册实测）",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "档案做实（标题=价值主张）；内容互动两周",
                "连接说明 200 字内定制；接受率 <30% 停手修定向",
                "人工动作单管理（自家实测：卡在验证墙数周）",
            ],
            "expected": "B2B 线索周期 2–8 周 [外部基准]",
        },
        "scale_gate": "接受率 ≥30% 且出现实质回复 → 小批量扩（仍人工确认）",
        "risk": "高（官方禁自动化；自家半自动受限实测，见手册）",
    },
    {
        "id": "indie-hackers",
        "name": "Indie Hackers",
        "icp_tags": ["indie-makers", "technical-founders"],
        "product_types": ["saas", "devtool", "oss", "consumer-app"],
        "channel_status": "UNVERIFIED",
        "motions": [
            {"motion": "真数字复盘帖（含失败）+ Product 页联动 + IH 传统社区优惠", "label": "external_baseline",
             "evidence": "带数字复盘是站内经典文体；社区对纯自我推广疲劳有公开批评（手册 indie-hackers.md §3）"},
            {"motion": "通稿式发布帖", "label": "external_baseline",
             "evidence": "build-in-public 回音室批评（社区讨论）；零互动沉底（手册 §5）",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "写「我做到 XX 的完整复盘」（小数字也行，真实第一）",
                "Product 页 + 社区帖联动，帖给社区专属优惠",
                "评论区的 stack/pricing 提问逐条回答",
            ],
            "expected": "脉冲 1–3 天 + 同行长尾；量级推测数十–数百精准访问 [无对照]",
        },
        "scale_gate": "复盘帖稳定有同行互动 → 月更制度化",
        "risk": "低（举报+人工管理；文化红线重于规则红线）",
    },
]

REQUIRED_FACTS = ("product", "icp", "goals", "value_events", "constraints", "owned_assets")
REQUIRED_PRODUCT = ("name", "what", "url", "stage")
BANDS = ((5, "P0"), (3, "P1"), (1, "P2"))


def _validate_facts(facts: Dict[str, Any]) -> None:
    for key in REQUIRED_FACTS:
        if key not in facts:
            raise ValueError(f"facts 缺少必需字段: {key}")
    for key in REQUIRED_PRODUCT:
        if not facts["product"].get(key):
            raise ValueError(f"product.{key} 不能为空")
    if not facts["icp"]:
        raise ValueError("icp 不能为空：没有 ICP 假设就无法做平台匹配")
    for i, hyp in enumerate(facts["icp"]):
        if hyp.get("evidence_label") not in LABELS:
            raise ValueError(f"icp[{i}].evidence_label 必须是 {sorted(LABELS)} 之一")
        if not hyp.get("evidence"):
            raise ValueError(f"icp[{i}].evidence 缺失：每个假设必须挂出处")
    if not facts["value_events"]:
        raise ValueError("value_events 不能为空：开工前必须定义价值事件（激活/付费）")
    for i, ev in enumerate(facts["value_events"]):
        if not ev.get("source"):
            raise ValueError(f"value_events[{i}].source 缺失：价值事件必须写明数据来源")
        if not isinstance(ev.get("is_approximation"), bool):
            raise ValueError(f"value_events[{i}].is_approximation 必须显式 true/false")
    if not facts["goals"]:
        raise ValueError("goals 不能为空：没有目标数字不谈策略")


def _confidence(icp: List[Dict[str, Any]]) -> Dict[str, Any]:
    verified = [h for h in icp if h["evidence_label"] != "hypothesis"]
    share = round(len(verified) / len(icp), 2) if icp else 0.0
    if share >= 0.66:
        level = "高"
    elif share >= 0.34:
        level = "中"
    else:
        level = "低"
    return {"level": level, "verified_share": share}


def _score(platform: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    tags: List[str] = []
    for hyp in facts["icp"]:
        tags.extend(hyp.get("tags", []))
    matched_tags = sorted(set(tags) & set(platform["icp_tags"]))
    # product_type 是可选维度：注册表按 product_types 匹配单一主类型
    ptype = facts["product"].get("product_type")
    type_match = ptype in platform["product_types"] if ptype else False
    owned = platform["id"] in facts["owned_assets"].get("platforms", [])
    score = 2 * len(matched_tags) + (2 if type_match else 0) + (1 if owned else 0)
    return {"score": score, "matched_tags": matched_tags, "type_match": type_match, "owned": owned}


def _priority(score: int) -> str:
    for floor, label in BANDS:
        if score >= floor:
            return label
    return "—"


def diagnose(facts: Dict[str, Any]) -> Dict[str, Any]:
    _validate_facts(facts)
    conf = _confidence(facts["icp"])
    rows = []
    for platform in sorted(PLATFORMS, key=lambda p: p["id"]):
        s = _score(platform, facts)
        alive = [m for m in platform["motions"] if not m.get("killed")]
        killed = [m for m in platform["motions"] if m.get("killed")]
        rows.append({
            "id": platform["id"],
            "name": platform["name"],
            "priority": _priority(s["score"]),
            "score": s["score"],
            "matched_tags": s["matched_tags"],
            "channel_status": platform["channel_status"],
            "motions": deepcopy(alive),
            "killed_motions": deepcopy(killed),
            "cold_start": deepcopy(platform["cold_start"]),
            "scale_gate": platform["scale_gate"],
            "risk": platform["risk"],
        })
    rows.sort(key=lambda r: (-r["score"], r["id"]))

    unresolved: List[str] = []
    if conf["level"] == "低":
        unresolved.append("ICP 假设全部或大部分未经验证——首轮目标应设为「判据收集」而非放量，先跑 20–40 个触达过判定线")
    if any(ev["is_approximation"] for ev in facts["value_events"]):
        unresolved.append("存在近似口径的价值事件：报告中已标注，不得当作精确数引用")
    if not any(r["priority"] == "P0" for r in rows):
        unresolved.append("没有平台够到 P0：ICP tags 与注册表无交集，先补 ICP 研究再诊断")

    return {
        "product": facts["product"],
        "goals": facts["goals"],
        "confidence": conf,
        "platforms": rows,
        "value_events": facts["value_events"],
        "unresolved": unresolved,
    }


def markdown(payload: Dict[str, Any]) -> str:
    p = payload["product"]
    conf = payload["confidence"]
    lines: List[str] = []
    lines.append(f"# GTM 诊断报告 · {p['name']}")
    lines.append("")
    lines.append(f"> 产品：{p['what']}（{p['url']}，阶段 {p['stage']}）")
    goals = "；".join(f"{g['layer']} {g['metric']}={g['target']}" for g in payload["goals"])
    lines.append(f"> 目标：{goals}")
    lines.append(f"> 策略置信度：**{conf['level']}**（ICP 假设 {int(conf['verified_share']*100)}% 已验证）")
    lines.append("")
    lines.append("## 平台优先级")
    lines.append("")
    lines.append("| 平台 | 优先级 | 匹配依据 | 渠道状态 |")
    lines.append("|---|---|---|---|")
    for r in payload["platforms"]:
        basis = "、".join(r["matched_tags"]) or "（无 tag 交集）"
        if r["score"] and not r["matched_tags"]:
            basis += " 类型/自有资产"
        lines.append(f"| {r['name']} | {r['priority']} | {basis} | {r['channel_status']} |")
    lines.append("")
    for r in payload["platforms"]:
        if r["priority"] == "—":
            continue
        lines.append(f"## {r['name']}（{r['priority']}）")
        lines.append("")
        lines.append("### 获客动作矩阵（每条带证据标签）")
        lines.append("")
        lines.append("| 动作 | 证据 | 出处 |")
        lines.append("|---|---|---|")
        for m in r["motions"]:
            lines.append(f"| {m['motion']} | {LABELS[m['label']]} | {m['evidence']} |")
        if r["killed_motions"]:
            lines.append("")
            lines.append("**判死动作（不做）**：")
            for m in r["killed_motions"]:
                lines.append(f"- ~~{m['motion']}~~ —— {LABELS[m['label']]}：{m['evidence']}")
        lines.append("")
        lines.append("### 冷启动清单")
        lines.append("")
        for item in r["cold_start"]["checklist"]:
            lines.append(f"- [ ] {item}")
        lines.append(f"- 预期区间：{r['cold_start']['expected']}")
        lines.append("")
        lines.append(f"### 放量判据（预注册）\n\n{r['scale_gate']}")
        lines.append("")
        lines.append(f"风控：{r['risk']}")
        lines.append("")
    lines.append("## 价值事件口径")
    lines.append("")
    for ev in payload["value_events"]:
        approx = "（**近似口径，已标注**）" if ev["is_approximation"] else ""
        lines.append(f"- **{ev['name']}**：来源 {ev['source']}{approx}")
    lines.append("")
    if payload["unresolved"]:
        lines.append("## 未决与诚实声明")
        lines.append("")
        for u in payload["unresolved"]:
            lines.append(f"- {u}")
        lines.append("")
    lines.append("## 下一步")
    lines.append("")
    lines.append("1. 人审本报告（每条推荐已挂证据标签；假设级条目按「待测」管理）")
    lines.append("2. `mkt plan new` 建首个 campaign plan，size 不超内核日预算")
    lines.append("3. 冷启动清单执行；复盘用 `mkt gtm retro`（W2 上线）回流本报告")
    lines.append("")
    return "\n".join(lines)
