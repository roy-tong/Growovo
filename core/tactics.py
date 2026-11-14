#!/usr/bin/env python3
"""非常规增长战术注册表：结构是数据，不是流程。

来源：2026-11-14 对经验语料库（约 620 篇）与外部研究的综述。当时的结论：
常规做法（持续发原创内容、慢慢养号）在小账号上效率低，因为有信息增量的
评论、已验证的选题、搜索位置、官方流量入口这些位置，不按粉丝量分配机会。
每条战术都是一个可查证据、可定配额、可判风险的对象；执行仍走 mkt tactic
（记录与判定）与 mkt queue（既有审批发送路径）两条线，本模块不发送任何东西。

七个机制族（MECHANISMS）回答"为什么有效"；每条战术（TACTICS）回答"具体
做什么、做多少、什么算数"。战术会过时（机会随采用人数衰减），所以每条带
revalidate_days，到期未复核的用 stale() 找出来。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 机制族：七个"为什么有效"。给 suggest() 与手册总表分组用。
# ---------------------------------------------------------------------------
MECHANISMS: Dict[str, Dict[str, str]] = {
    "borrowed_audience": {
        "name": "借别人的受众",
        "principle": "自己账号的分发接近零时，别人的评论区/回答区是唯一能免费触达"
                     "大量精准人群的位置；位置按内容质量和速度分配，不按粉丝量。",
        "ask": "目标人群现在聚在谁的评论区里？我能不能在几分钟内说出有信息增量的话？",
    },
    "proven_demand": {
        "name": "用已验证的需求",
        "principle": "别人已经火过的选题说明需求被市场验证过，平台对同类内容有持续"
                     "分发惯性；新账号最大的成本是试错，先从验证过的需求做起。",
        "ask": "过去 7 天内，谁的哪条内容已经替我验证了需求？我只借框架不逐字抄了吗？",
    },
    "search_seat": {
        "name": "占搜索位置",
        "principle": "所有人挤推荐流时，搜索入口竞争小得多；搜索流量代表明确需求，"
                     "且长尾极长，一条内容能持续被搜到很多年。",
        "ask": "用户会搜的原话是什么？这个词的搜索结果页现在排在前排的是谁？",
    },
    "official_tap": {
        "name": "用官方流量入口",
        "principle": "平台为扶持某个方向会真金白银补贴流量（官方话题、创作活动、"
                     "新品奖励），这些入口常被多数人忽略。",
        "ask": "这个平台本周官方在推什么话题/活动？从官方入口发布和从 + 号发布差多少？",
    },
    "volume_probability": {
        "name": "用数量换概率",
        "principle": "推荐池是概率游戏：单篇成本降到接近零后，总量上去，期望值放大；"
                     "多数沉默是正常成本，不是失败。",
        "ask": "我把单条成本降下来了吗？看的是总量数据而不是每条都焦虑了吗？",
    },
    "rule_window": {
        "name": "用规则的时间窗",
        "principle": "各平台的排序和判定都有公开或被逆向出的规则：前几十分钟的互动"
                     "速度、近期星速、首小时排名等。窗口内的动作价值是窗口外的数倍。",
        "ask": "这件事的价值集中在哪个时间窗里？我有没有守在那个窗口前？",
    },
    "story_pull": {
        "name": "用情绪和故事",
        "principle": "分发机制奖励引发反应的内容，不奖励正确的内容；真实的失败数据、"
                     "公开的增长过程、悬念叙事本身就是分发引擎。",
        "ask": "这条内容有让人想回复/转发的情绪钩子吗？数据是真实可查的吗？",
    },
    # ---- 以下三条来自研究 II（2026-11-14）：回答"先打谁、什么顺序、什么结构" ----
    "trust_nodes": {
        "name": "借信任节点",
        "principle": "信任随粉丝量与商单密度衰减：万粉级垂直账号的互动率是百万粉账号的"
                     "数倍，未被商业化的信任最便宜；传播节点和付费节点是两类人，初期优先前者。",
        "ask": "这个人群里谁被信任、谁会说话？他还在信任价格最低的窗口期吗？",
    },
    "sequence_window": {
        "name": "用次序与衰减窗",
        "principle": "渠道会衰减、网络有密度临界点、稀缺有叙事保鲜期；同一动作放在"
                     "衰减曲线左端，产出高一个数量级。",
        "ask": "这件事在时间轴上哪个位置价值最大？我是进场偏早还是偏晚？",
    },
    "compound_loop": {
        "name": "让输出变回燃料",
        "principle": "漏斗流过即漏掉，每个新用户都要重新花钱；把过程的输出（新用户/"
                     "新内容/新数据）回流为输入，才有复利。",
        "ask": "这批用户的输出，怎么变成下一批用户的输入？",
    },
    # ---- 以下四条来自研究 III（2026-11-14）：补全策略空间的空白轴 ----
    "product_embedded": {
        "name": "让产品自己分发",
        "principle": "产出物离开产品边界的每一刻（分享链接/导出文件/外发邮件/嵌入）"
                     "都是零获客成本的分发位；分发量=免费用户产出量×曝光。",
        "ask": "产品的每个出口都装上署名与体验页了吗？邀请是不是用户的必经之路？",
    },
    "value_exchange": {
        "name": "改变给什么",
        "principle": "给什么、怎么给、以什么形式给，决定了增长的结构：锁资产不锁"
                     "功能、私有数据换公开引用、定价相对化——交换结构本身就是武器。",
        "ask": "我给出去的东西里，有没有别人拿不到的（私有数据/相对价格/资产赎回权）？",
    },
    "paid_amplify": {
        "name": "只放大赢家",
        "principle": "付费的最高杠杆不是买流量，是给已被有机验证的东西加杠杆："
                     "创意测试免费外包给有机算法，付费只做放大器，且只碰暖流量。",
        "ask": "要放大的内容互动超账号基线 2-3 倍了吗？暖池和冷流量预算分开管了吗？",
    },
    "competitor_events": {
        "name": "对手事件套利",
        "principle": "对手的涨价、故障、招聘、日落是带时间戳的公开事件——它们的"
                     "用户在那一刻变成高意图人群，预制的承接管线把它变成获客战役。",
        "ask": "对手的哪些公开页面在被监控？事件发生后 48 小时内我的承接物是什么？",
    },
    # ---- 以下五条来自研究 IV（2026-11-14）：自建场地/联盟/浪潮/透明/Agent 位 ----
    "own_the_venue": {
        "name": "自建场地与裁判席",
        "principle": "不租别人的流量位，把评选权/入口权/定义权/认证权做成资产——"
                     "权威结构建成后，流量是结构自带的副产品，竞品用钱买不到同等位置。",
        "ask": "这个细分领域里，哪张裁判席还空着（奖项/榜单/目录/认证/周报）？",
    },
    "alliance_bundle": {
        "name": "联盟与捆绑",
        "principle": "小产品之间的组织化互相借力：把互补方的受众、结账台与信任"
                     "当作自己的分发基础设施，成本结构换成分成。",
        "ask": "谁的客户已经是我的用户？我们能把什么捆在一起卖给同一批人？",
    },
    "event_waves": {
        "name": "宏观浪潮承接",
        "principle": "预算周期、法规生效日、平台迁移、政策公告都是可预期的需求浪潮；"
                     "需求爆发在公告日而非生效日，接住浪潮靠预制而非反应快，"
                     "尖峰流量 90 天内不转自有渠道就归零。",
        "ask": "未来 12 个月的浪潮日历上，哪个浪会冲刷我的人群？我的预制物是什么？",
    },
    "radical_transparency": {
        "name": "透明经营",
        "principle": "公开经营数据与决策是注意力资产：涨时是信任引擎，跌时是负债；"
                     "透明有生命周期——设好退出触发器，公开总结而非实时面板。",
        "ask": "公开什么能换来注意（收入/公式/复盘），公开什么会武装对手（分客群流失/定价弹性）？",
    },
    "agent_surfaces": {
        "name": "Agent 分发位",
        "principle": "AI 助手生态出现三层新分发位：上架位（安装即常驻渠道）、"
                     "引用位（被 AI 转述，产品页是引用主体）、可读位（agent 能否"
                     "直接试用产品成为新的'可爬取性'）。",
        "ask": "我的产品装得进 agent 的默认搜索路径吗？产品页能被 agent 转述吗？",
    },
}

# 风险分档：白帽可以长期持有；浅灰守住体验底线；灰只在特定窗口有正期望。
RISK_GRADES = {
    "white": "白帽：机制利用，不碰平台规则红线，可当长期资产",
    "light_gray": "浅灰：合规但依赖执行质量，跌破体验底线杠杆归零",
    "gray": "灰：仅特定窗口正期望，先算账号被封的沉没成本",
}
RISK_ORDER = ["white", "light_gray", "gray"]

# ---------------------------------------------------------------------------
# 战术注册表
# ---------------------------------------------------------------------------
TACTICS: Dict[str, Dict[str, Any]] = {
    # ---- X：当前主战场，证据最足 ----
    "x-reply-guy": {
        "name": "X 高价值回帖（四周）",
        "channel": "x",
        "mechanism": "borrowed_audience",
        "status": "usable",
        "risk": "white",
        "idea": "一条有信息增量的回复排进热帖评论区，触达远超自己发的原创帖："
                "X 算法里回复权重约是点赞的 9 倍，追踪样本里 84% 的小账号靠回帖涨到一万粉。",
        "steps": [
            "建 10-15 个目标账号的私密列表并开通知（选自己粉丝量 5-20 倍的账号，"
            "太大被淹没、太小没流量）",
            "只回发布 30 分钟内、已有回复少于 20 条的帖子；长推文串只回第一条",
            "每天 15-20 条高质量回复，七种内容公式轮换，每条独立成立、值得被点开主页",
            "回复里永不放外链、不推销；争取作者回应（作者回你是强分发信号）",
            "70% 时间回帖、30% 原创发帖：原创是转化层，主页必须有东西可看",
            "每周记录主页访问与新增粉丝，对照自己原创帖的数据验证配比",
            "每周做一次触达自检（四类静默限流：搜索屏蔽/建议屏蔽/幽灵/回复折叠）；"
            "回复被折叠时全部互动白做，先停 24 小时再降频",
        ],
        "quota": {
            "daily_min": 15, "daily_cap": 20,
            "per_target_daily_max": 2, "per_hour_max": 20,
            "reply_within_minutes": 30, "skip_if_post_replies_over": 20,
            "same_formula_max_streak": 4,
            "min_sample": 80, "deleted_max": 0.15,
            "response_pass": 0.10, "response_maybe": 0.03,
        },
        "formulas": ["数据型", "经验型", "反共识型", "提问型", "洞见型", "放大型", "资源型"],
        "verdict_metrics": ["profile_visits_per_week", "new_followers_per_week"],
        "queue_action": "reply",
        "evidence": [
            "自家实测（2026-11）：回帖触达明显大于原创帖",
            "Teract 追踪 300 个小账号：84% 以回帖为主要增长手段",
            "X 2023 开源算法分析：回复权重约 9 倍于点赞（dev.to/axrisi）",
        ],
        "caution": "手写为主，X 对自动化互动敏感；回复被折叠进'更多回复'时停 24 小时再降频恢复。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 60,
    },
    "x-intent-search": {
        "name": "X 搜索意图回复",
        "channel": "x",
        "mechanism": "borrowed_audience",
        "status": "proposed",
        "risk": "white",
        "idea": "公开表达'求推荐/受够了'的帖子是转化率最高的回复对象，高级搜索可以每天找到他们。",
        "steps": [
            "每天扫一遍：\"looking for recommendations\" 加品类词、\"any tool for\" 加场景、"
            "\"frustrated with\" 加竞品名、\"wish there was\"",
            "先给真正有用的回复建立关系，再在合适上下文介绍产品并披露身份",
            "每条必须按帖子定制，绝不群发模板（模板回复=举报素材）",
        ],
        "quota": {"daily_cap": 5, "per_target_daily_max": 1, "min_sample": 40},
        "evidence": ["Teract indie hackers 策略文（teract.ai）：意图帖话术与节奏"],
        "caution": "同一天对多个意图帖发相似内容会被判垃圾；先价值后产品。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 60,
    },
    "x-quote-tweet": {
        "name": "X 引用转推五种格式",
        "channel": "x",
        "mechanism": "borrowed_audience",
        "status": "proposed",
        "risk": "light_gray",
        "idea": "引用转推是'上自己主页的回复'：同时触达自己的关注者和原帖作者的受众。",
        "steps": [
            "原帖发出 1-4 小时内引用；五种格式：补充价值/礼貌反驳（常最高触达）/"
            "转译给自己受众/附自家实测数据/旧观点翻新",
            "每周 3-8 条；反驳要带证据且不对人，羞辱式反驳会被集中屏蔽",
        ],
        "quota": {"weekly_min": 3, "weekly_cap": 8},
        "evidence": ["postory.io 引用转推策略：主页访问约 2-3 倍于普通回复"],
        "caution": "为了反驳而反驳会引来举报；只引有真实增量可加的帖。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 60,
    },
    # ---- Reddit / 海外社区 ----
    "reddit-first-comment": {
        "name": "Reddit 高意图帖首评占位",
        "channel": "reddit",
        "mechanism": "borrowed_audience",
        "status": "proposed",
        "risk": "white",
        "idea": "第一条有用评论吃掉一个问题下的大部分注意力；只提品牌名、永不贴链接，"
                "用户事后自己搜名字找来，帖子还会被 Google 和 AI 搜索长期引用。",
        "steps": [
            "先过账号门槛（多数 sub 要求账龄与积分），按 9:1 纪律攒记录",
            "圈定约 7 个目标客户聚集的 sub，监控'求工具/受够了/放弃某方案'类问题",
            "问题出现后抢第一条有用评论；约 2 成回复在自然上下文里提品牌名，绝不贴 URL",
            "对比帖里诚实列出竞品并承认其更强，反而更容易被当作可信信源",
        ],
        "quota": {"daily_cap": 6, "min_sample": 40, "link_ban": True},
        "evidence": [
            "语料（reddit/f7ea9ca440）：直接获客且帖子被 Google、ChatGPT 抓取形成复利",
            "语料（reddit/cccd7c9ef9）：客观对比帖 18 天内被 ChatGPT 引用",
        ],
        "caution": "被识别为水军会连帖带号一起没；评论必须真有用，账号先当居民再出手。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 60,
    },
    "aeo-deep-guide": {
        "name": "AI 引用深指南（AEO）",
        "channel": "google",
        "mechanism": "search_seat",
        "status": "proposed",
        "risk": "white",
        "idea": "AI 助手回答'用什么工具'时会引用第三方页面；被详述才有用，光上榜没用。",
        "steps": [
            "自有站写一篇 6000 字级'何时、为何选我们'深指南，必须包含'不能做什么'",
            "找出 AI 在目标问题里已经引用的第三方页面，想办法让自己的内容进去",
            "每周固定一组买家问题，逐个 AI 助手跑，记录是否被点名与被引页面",
        ],
        "quota": {"weekly_min": 1},
        "evidence": [
            "语料（reddit/5457f41a7c）：深指南被点名率 69% vs 列表页 14%；"
            "短文 21 天首次被引、长指南约 4 个月",
        ],
        "caution": "引用不等于流量，AEO 只做品牌防御与第三方渗透，别当主流量渠道。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "hn-data-post": {
        "name": "HN 数据贴形态",
        "channel": "hackernews",
        "mechanism": "rule_window",
        "status": "proposed",
        "risk": "white",
        "idea": "HN 数据贴的正确形态：链接直接指向分析本身、页面无注册入口、"
                "产品只在文末一行；反着做会被社区标记。",
        "steps": [
            "做一个别人没做过的数据扫描（真实、可复算）",
            "分析正文作为提交链接；产品/工具降到最底部一行",
            "发布后守两小时逐条回应批评，附证据与边界说明",
        ],
        "quota": {},
        "evidence": ["语料（reddit/616d360716）：8062 个 Show HN 域名扫描帖，首次形态错被标记后总结"],
        "caution": "自吹式'研究'会被识破；发布时间选能全程在线守帖的时段。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "ph-first-hour": {
        "name": "Product Hunt 首小时节奏",
        "channel": "product-hunt",
        "mechanism": "rule_window",
        "status": "proposed",
        "risk": "white",
        "idea": "PH 按早期支持速度排名，发布后 2-4 小时的表现基本决定全天走势。",
        "steps": [
            "提前 2-4 周造势（预告页、邮件列表）；找有信誉的 hunter",
            "12:01 AM PT 发布；首小时集中真实支持者互动（评论比点赞更有价值）",
            "全天守着回复每条评论；绝不群发求票（异常投票会被作废）",
        ],
        "quota": {},
        "evidence": ["startuplaunchstrategy.com：首小时决定全天；PH 官方发布指南"],
        "caution": "近年上榜流量红利缩水，把它当背书与外链渠道，不当主战场。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "github-star-sprint": {
        "name": "GitHub 星速集中日",
        "channel": "github",
        "mechanism": "rule_window",
        "status": "proposed",
        "risk": "white",
        "idea": "trending 按近期星速而非总量排序：把全渠道流量集中到一天导星，"
                "上榜后形成'曝光换更多星'的循环。",
        "steps": [
            "README 顶配（动图演示、快速开始、徽章、用例）先备好",
            "发布日把 X/社区/邮件列表的注意力集中导向仓库点星",
            "绝不买星、不搞互赞星（自家事故：买星污染归因且会被清理）",
        ],
        "quota": {},
        "evidence": ["GitHub 社区对 trending 星速规则的长期观察；自家买星事故复盘"],
        "caution": "README 不行的仓库上了 trending 也留不住人；先内容后冲榜。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "linkedin-early-comments": {
        "name": "LinkedIn 每日十条早评论",
        "channel": "linkedin",
        "mechanism": "borrowed_audience",
        "status": "proposed",
        "risk": "white",
        "idea": "普通帖曝光在降，头部创作者帖子的评论区曝光在涨；把每条评论写成"
                "有钩子有结构的'迷你帖子'。",
        "steps": [
            "建 50 人评论清单（头部 10 + 同量级 10 + 中腰部 30）并开通知",
            "发帖后 30 分钟内留 3-4 行含真实观点的评论；每天 10 条",
            "发自己帖子前 15-30 分钟先去评论别人（发帖+互动才有增长）；"
            "不用 AI 写评论，平台会过滤",
        ],
        "quota": {"daily_min": 10, "daily_cap": 10, "reply_within_minutes": 30},
        "evidence": ["Jasmin Alic 三年 36 万粉主要靠评论；语料（reddit/4423be08e1）早评论窗获客案例"],
        "caution": "互评小组检测率高；只做真人真话的松散互助。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 60,
    },
    "ai-directory-submit": {
        "name": "AI 工具目录提交",
        "channel": "ai-directories",
        "mechanism": "official_tap",
        "status": "usable",
        "risk": "white",
        "idea": "AI 目录站靠品类搜索吃大流量，免费提交等于白拿分类页展示与外链，"
                "是一次性低成本动作。",
        "steps": [
            "按优先级提交：There's An AI For You → Futurepedia → Toolify → Product Hunt",
            "描述写成搜索意图词（'AI tool for X'）而不是营销口号",
            "提交后记录审核状态；付费加速只考虑高权重目录",
        ],
        "quota": {"total_min": 4, "total_cap": 10},
        "verdict_metrics": ["submissions", "directory_referral_visits"],
        "evidence": ["TAAFT 域名权重约 77，免费/49 美元加速且链接可传递权重"],
        "caution": "目录流量是一次性脉冲；价值在长尾搜索页与外链，不在当天访问。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 120,
    },
    # ---- 国内平台 ----
    "zhihu-question-radar": {
        "name": "知乎潜力问题雷达",
        "channel": "zhihu",
        "mechanism": "rule_window",
        "status": "proposed",
        "risk": "white",
        "idea": "热榜一半是周期性复发的老问题；在问题上榜前埋回答，"
                "流量涌入时已经排在前面。挑'关注多、回答少'的问题避开大号。",
        "steps": [
            "监控热度排名快速上升的问题，发现后立即回答",
            "周期性热点（行业大会、开学季、年报季）提前 2-4 周埋内容",
            "同时吃知乎站内搜索和百度搜索两份长尾：优先答百度结果页靠前的知乎问题",
            "每天最多答 2 个问题（多了会被判刷题）",
        ],
        "quota": {"daily_cap": 2, "min_sample": 20},
        "evidence": ["语料（zhihu）：热榜 50% 周期性复发；'关注高回答少'选问题法被多篇复现"],
        "caution": "突发热点难测且易翻车；机构号不碰敏感社会话题。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "zhihu-bigv-window": {
        "name": "知乎大号进场窗口抢答",
        "channel": "zhihu",
        "mechanism": "borrowed_audience",
        "status": "proposed",
        "risk": "white",
        "idea": "大号关注或回答某问题后，其关注者时间线会集体看到该问题；"
                "大号进场不是结束，是流量窗口开启的信号。",
        "steps": [
            "事件开始发酵时搜相关关键词，把相关问题先点关注收藏",
            "第一批大号陆续进场时立刻跟进回答（内容不能太差）",
            "每天最多 2 个回答；回答里不放联系方式",
        ],
        "quota": {"daily_cap": 2},
        "evidence": ["语料（zhihu）：达摩院问题实操，跟进者借大号关注获得大量点赞"],
        "caution": "内容差会被后来的反对票压下去；窗口短，动作要快但话要真。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "zhihu-roundup-answer": {
        "name": "知乎盘点式回答",
        "channel": "zhihu",
        "mechanism": "borrowed_audience",
        "status": "proposed",
        "risk": "white",
        "idea": "把一个问题下所有回答整理成结构化盘点（表格+可核对的数字），"
                "借全部答主的工作坐收最高赞：读者要的是省时间的结论。",
        "steps": [
            "选清单类问题；把高赞回答逐条核对后做成表格",
            "配可验证的权威数字与原文链接（原回答常缺链接，这是差异化）",
            "开头三行讲清筛选标准与工作量；结尾再引导，不在开头硬广",
        ],
        "quota": {},
        "evidence": ["语料（zhihu）：跨专业新手用该法 3 天收藏破 2 万"],
        "caution": "'看完了全部回答'这类浮夸表述会被识破；整理必须真实做过。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "xhs-proven-note-remake": {
        "name": "小红书已验证笔记复刻",
        "channel": "xiaohongshu",
        "mechanism": "proven_demand",
        "status": "proposed",
        "risk": "light_gray",
        "idea": "7 天内低粉账号的爆款说明选题强而非账号强；拆框架用自己的话重做，"
                "把试错成本降到接近零。",
        "steps": [
            "找 5-10 个 1-5 万粉的成长型账号（头部不可复刻）",
            "关键词搜索切'最热'，只做千赞以上、近 7 天、低粉账号发火的选题",
            "拆标题套路、开头钩子、结构、高频词，用自己的话重述；只借框架不逐字抄",
            "同账号重发自己火过的内容不触发跨账号查重（仅限起号期，平台规则在收紧）",
        ],
        "quota": {"daily_cap": 3},
        "evidence": ["语料（xiaohongshu）：'起号最快的方式'系列，一月涨粉 3k+；多篇复现"],
        "caution": "跨账号同文判重复、流量骤降；照抄会被投诉侵权。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 45,
    },
    "xhs-search-seat": {
        "name": "小红书搜索占位",
        "channel": "xiaohongshu",
        "mechanism": "search_seat",
        "status": "proposed",
        "risk": "white",
        "idea": "搜不到不是被限流，是没进搜索池；标题从情绪型改成信息型，"
                "能匹配的搜索词从 0 变 4，笔记吃长尾搜索流量。",
        "steps": [
            "发布后把标题原样粘进搜索框切'最新'自检，搜不到就改标题用'二次编辑'",
            "绝不删了重发（互动数据归零）；大词降级拆长尾词",
            "核心词进标题、长尾词埋正文、拓展词做标签；用用户的原话命名概念",
        ],
        "quota": {},
        "evidence": ["语料（xiaohongshu）：限流 5000 字长文与'为什么搜不到'多篇交叉复现"],
        "caution": "关键词堆砌会被判低质；先保证内容配得上这个搜索词。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 60,
    },
    "xhs-comment-mining": {
        "name": "小红书爆款评论区找选题",
        "channel": "xiaohongshu",
        "mechanism": "proven_demand",
        "status": "proposed",
        "risk": "white",
        "idea": "爆款评论区是现成的需求池：'求方法'和'我试过不行'的评论"
                "直接就是下一篇的标题。",
        "steps": [
            "每天翻 5 条同赛道爆款评论，只记两类：问方法的、说踩坑的",
            "把评论原话（不是结论）改成陈述句标题",
            "攒够一周选题就停，不无限刷",
        ],
        "quota": {"daily_cap": 5},
        "evidence": ["语料（xiaohongshu）：359 条评论占账号互动 91% 的案例；'选题别自己想'"],
        "caution": "问题可以借，答案必须是自己的，否则是洗稿。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 60,
    },
    "xhs-official-tap": {
        "name": "小红书官方流量入口",
        "channel": "xiaohongshu",
        "mechanism": "official_tap",
        "status": "proposed",
        "risk": "white",
        "idea": "从首页加号发布吃不到官方活动流量；创作者中心的灵感入口和官方话题"
                "自带流量池，1000 粉后带货笔记还有额外曝光奖励。",
        "steps": [
            "发布一律走创作中心-笔记灵感，选有流量扶持的话题",
            "盯官方账号每周的热点话题（瓜分官方流量池）",
            "新笔记发出后的第 1 小时守着评论区（初始曝光窗口）",
        ],
        "quota": {},
        "evidence": ["语料（xiaohongshu/bilibili ASR）：官方入口与千粉奖励多篇复现"],
        "caution": "扶持力度随平台政策波动；商业内容比例守住 5:1。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 45,
    },
    "bilibili-vertical-arbitrage": {
        "name": "B 站竖屏内容形态差",
        "channel": "bilibili",
        "mechanism": "proven_demand",
        "status": "proposed",
        "risk": "light_gray",
        "idea": "B 站竖屏供给远小于抖音：同等内容在抖音已验证、在 B 站竞争小，"
                "分发效率高得多。",
        "steps": [
            "找抖音已验证的竖屏选题，用 B 站化表达重做（造梗、留弹幕位）",
            "不逐字搬运（会查重）；承接 B 站存量热区流量",
        ],
        "quota": {},
        "evidence": ["语料（bilibili）：同内容小红书未出圈、B 站 4 个月 220 万粉案例"],
        "caution": "红利窗口随平台供给变化衰减；纯搬运会被查重处理。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 60,
    },
    "bilibili-coop-post": {
        "name": "B 站话题入口联合投稿",
        "channel": "bilibili",
        "mechanism": "borrowed_audience",
        "status": "proposed",
        "risk": "white",
        "idea": "联合投稿本来要 1000 粉，但走'参与话题'入口可以绕过门槛，"
                "视频同时显示在双方主页，直接借对方关注者池。",
        "steps": [
            "投稿时选'参与话题'找同类总动员话题，更多设置里发起联合投稿",
            "找粉丝量大于自己的同类 UP 互投；每月上限 6 次",
        ],
        "quota": {"monthly_cap": 6},
        "evidence": ["语料（bilibili）：绕门槛教程视频，万级播放"],
        "caution": "对方可拒绝；内容不对等时很难约到。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "wechat-sousou-matrix": {
        "name": "微信搜一搜长尾占位",
        "channel": "wechat",
        "mechanism": "search_seat",
        "status": "proposed",
        "risk": "gray",
        "idea": "搜一搜流量被导向公众号；用 AI 辅助批量生产长尾问答文、"
                "少量账号铺同一地域/领域标签，垄断一个词的搜索结果页。",
        "steps": [
            "扒搜一搜下拉词，按'用户拿着问题搜'的长尾词生成问答式干货",
            "人工逐篇把关再发；标题必须含关键词（无关键词基本不参与排名）",
            "2-5 个号铺同一标签，文章末尾放个人号入口（公众号规则允许）",
        ],
        "quota": {},
        "evidence": ["语料（wechat）：5 号矩阵每月稳定被动咨询 60+；新号排名案例多篇"],
        "caution": "纯 AI 低质批量文会被降权；灰度在于量产边界，人工把关是底线。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 60,
    },
    "gzh-lowfan-archaeology": {
        "name": "公众号低粉爆款考古",
        "channel": "wechat",
        "mechanism": "proven_demand",
        "status": "proposed",
        "risk": "white",
        "idea": "账号数据平平但单篇爆了的选题说明选题自带流量；同行看不上，竞争极小。",
        "steps": [
            "刷'看一看'找高阅读文，点进作者主页验证：其他篇两位数而这篇几万",
            "只挖 7 天内的新爆款（超过一个月红利枯竭）；改写不能一字不动",
        ],
        "quota": {},
        "evidence": ["语料（bilibili ASR 转公众号方法论）：低粉爆款挖掘实操"],
        "caution": "照搬判同质化；红利窗口约 1-2 周，动作要快。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 60,
    },
    # ---- 以下来自研究 II（2026-11-14）：目标选择与次序层 ----
    "koc-first": {
        "name": "KOC 优先（增长初期先打信任节点）",
        "channel": "通用",
        "mechanism": "trust_nodes",
        "status": "usable",
        "risk": "white",
        "idea": "初期重点争取 KOC（垂直领域几千到几万粉、有声量、未商业化、被粉丝"
                "真实信任的账号），而非头部名人或大量普通用户：1-5 万粉互动率是 50 万+ "
                "粉的 2-4 倍，72% 用户更信任 10 万粉以下创作者，且 KOC 会再传播。",
        "steps": [
            "识别标准：粉丝 1 千-5 万、垂直领域、评论区有真实对话、商单痕迹少于 3 条",
            "找法：话题标签排名、竞品评论区活跃推荐者、垂直社群里被反复 @ 求推荐的人",
            "接触：先给价值（引用其观点并署名、用其内容做案例、免费额度），共创而非投放",
            "先 10 个深度共创验证单个 KOC 带来的转化，再复制到 50-100 个",
            "每个 KOC 用 UTM 单独追踪；优先锁定未商业化窗口期，给长期合作/创始权益",
        ],
        "quota": {"daily_min": 5, "daily_cap": 15, "per_target_daily_max": 1,
                  "min_sample": 40},
        "verdict_metrics": ["koc_engaged_per_week", "koc_amplifications_per_week"],
        "evidence": [
            "HypeAuditor 基准：百万粉以上互动率中位数 0.33% vs nano 1.78-4%",
            "YouGov/HEC：72% 用户更信任 10 万粉以下创作者；名人真诚认可仅 23%",
            "TikTok 1.5-5 万粉创作者互动率 17.96%（Influencer Marketing Hub 2024 汇编）",
        ],
        "caution": "合作须按平台规则披露赠送关系；不披露的'野生好评'是浅灰且会反噬。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "natural-cluster-seed": {
        "name": "以自然簇为种子单位",
        "channel": "通用",
        "mechanism": "trust_nodes",
        "status": "proposed",
        "risk": "white",
        "idea": "协作类产品的种子单位是'一个已有内部关系的团队'而非散装个人："
                "Slack 创建团队的 90%+ 从不邀请任何人，但用起来的团队 2000 条消息后"
                "93% 留存——关系密度是留存的单位。",
        "steps": [
            "先请 6-10 家友好公司的整个团队试用（Slack 原话：央求+哄劝朋友公司）",
            "选会自然外扩的簇型：团队/班级/门店/社群",
            "给簇内'引路人'（中层组织者）说服全簇的弹药",
            "考核簇内渗透率而非个体注册量",
        ],
        "quota": {"total_min": 6, "total_cap": 10},
        "evidence": ["First Round 对 Stewart Butterfield 专访：2000 条消息魔法数字、93% 留存"],
        "caution": "散装个人永不激活；别用个体 DAU 考核冷启动。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "density-first": {
        "name": "密度优先：先烧透一个小容器",
        "channel": "通用",
        "mechanism": "trust_nodes",
        "status": "proposed",
        "risk": "white",
        "idea": "PG 的 contained fire：Facebook 先只做哈佛（逐校手工做课程表），"
                "容器内 30-50% 渗透再开相邻容器——网络产品在低密度下对谁都死。",
        "steps": [
            "选一个封闭、高频见面、有天然名单的容器（学校/公司/行业/城市）",
            "做到容器内 30-50% 渗透再开下一个，扩张只沿相邻容器",
            "观察密度指标（互动率/互关率）而非总量",
        ],
        "quota": {},
        "evidence": ["PG《Do Things that Don't Scale》：contained fire 与 Zuckerberg 课程表"],
        "caution": "撒得越开每个用户看到的产品越空；广撒网反共识于此。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "superfan-first": {
        "name": "Superfan 优先：先要 100 个铁杆",
        "channel": "通用",
        "mechanism": "trust_nodes",
        "status": "proposed",
        "risk": "white",
        "idea": "真粉是面向普通粉丝的首席营销力量；Kickstarter 成功项目平均仅 241 个"
                "支持者——千级铁杆即可撑起一个产品，追总量是漏桶。",
        "steps": [
            "定义真粉行为信号：复购、二次传播、主动反馈、点名要你而非品类",
            "资源倾斜给已出现的真粉；设计只有真粉会买的高价交付",
            "节奏目标：每天新增 1 个真粉（3 年=1000 人）",
        ],
        "quota": {},
        "evidence": ["Kevin Kelly《1000 True Fans》；a16z《100 True Fans》修正版"],
        "caution": "普通粉丝不付费也不传播；粉丝总量不是北极星。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "founder-manual-recruit": {
        "name": "创始人手工招募前一百人",
        "channel": "通用",
        "mechanism": "trust_nodes",
        "status": "proposed",
        "risk": "white",
        "idea": "Stripe 的 Collison installation：对方一说愿意试就当场装好；"
                "Airbnb 上门拍照 30 天定生死——每周 10% 复合下，手工用户是复利公式首项。",
        "steps": [
            "创始人本人每天固定小时数招募（不可外包）",
            "对方答应后不放走：当天完成配置与首次使用",
            "极致宠爱前 100 人（手写卡/公开致谢/每条反馈都回）",
            "挑一个痛点极尖锐的用户按'只为这一家'深度服务，再找相似的下一个",
        ],
        "quota": {},
        "evidence": ["PG《Do Things that Don't Scale》：Collison installation、Airbnb、Wufoo 手写卡"],
        "caution": "'好产品会自己传播'是错觉；模仿大公司的专业冷漠是陷阱。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "beachhead-sequence": {
        "name": "保龄球瓶次序：先打透一个细分",
        "channel": "通用",
        "mechanism": "sequence_window",
        "status": "proposed",
        "risk": "white",
        "idea": "实用主义买家只信'同行的参考案例'——集中打透一个能 100% 占据的细分，"
                "3-5 个可公开引用的标杆再撞相邻瓶；同时打多个行业=谁都没打透。",
        "steps": [
            "头瓶标准：痛点最痛+可触达（有名单/会议）+有话语权密度",
            "在该细分做出完整方案，拿下 3-5 个可公开引用的标杆",
            "扩张只选相邻细分（相似痛点/采购链）",
        ],
        "quota": {},
        "evidence": ["Moore《跨越鸿沟》beachhead；Lenny's Newsletter 2024 对 Moore 的采访"],
        "caution": "'大 TAM、多行业同时开'反共识于此。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "competitor-migrants": {
        "name": "竞品迁移者优先",
        "channel": "通用",
        "mechanism": "proven_demand",
        "status": "proposed",
        "risk": "white",
        "idea": "已被品类教育过的人迁移成本最低：Slack 早期 20-30% 用户来自 "
                "HipChat/IRC 迁移——竞品不满者是自带需求定义能力的现成买家。",
        "steps": [
            "监控竞品社区/评价区的抱怨主题（功能缺失、涨价、客服）",
            "做竞品导入工具、迁移指南、'从 X 到我们'对照页",
            "卡竞品涨价/改版事件窗主动承接",
        ],
        "quota": {},
        "evidence": ["First Round Slack 专访：20-30% 迁移者估算"],
        "caution": "冒充用户抹黑竞品是深灰，不做。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 120,
    },
    "growth-loop-design": {
        "name": "增长环设计：输出变回燃料",
        "channel": "通用",
        "mechanism": "compound_loop",
        "status": "proposed",
        "risk": "white",
        "idea": "漏斗流过即漏掉；环把输出回流为输入才有复利——用户产出内容→新流量→"
                "新用户（Pinterest），或每个用户拉队友（Slack），或双边互拉（Uber）。",
        "steps": [
            "画一张'输出如何变回输入'的环图，环跑通前不叠加漏斗型投放",
            "为产品选定一种主环：内容环/推荐环/协作环/双边环",
            "度量环速（每用户带来的下批输入）而非单次转化率",
        ],
        "quota": {},
        "evidence": ["Balfour《Growth Loops are the New Funnels》（Reforge，2018）"],
        "caution": "按 AARRR 搭团队会把组织推向线性买量。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "waitlist-queue-ladder": {
        "name": "候补队列与阶梯奖励",
        "channel": "通用",
        "mechanism": "compound_loop",
        "status": "proposed",
        "risk": "white",
        "idea": "排队位置本身是被感知的奖励（成本约 0）：透明公示前后人数、邀请即插队"
                "（Robinhood 百万候补）；确定性阶梯奖品全是自家产品（Harry's 一周 10 万"
                "邮箱、77% 注册来自推荐）。",
        "steps": [
            "公示前后人数+个人专属链接+邀请成功即前移",
            "阶梯奖励用自家产品，好友验证后解锁（防刷）",
            "同 IP 重复注册拦截、退信视为无效，分别统计有效/无效",
            "等待期持续 nurture（候补是暖名单，转化基准 2-5%）",
        ],
        "quota": {},
        "evidence": ["Marcel van Oost Robinhood 复盘；Tim Ferriss 博客 Harry's 原始复盘"],
        "caution": "迟迟不放行，位置焦虑转怨气；奖励必引刷单，防御与活动同时上线。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "invite-scarcity": {
        "name": "邀请制稀缺（须有真实理由）",
        "channel": "通用",
        "mechanism": "compound_loop",
        "status": "proposed",
        "risk": "light_gray",
        "idea": "限量邀请码让转发权本身稀缺：Gmail 码曾卖 $150、Clubhouse 黑市 "
                "$50-125 且邀请制期 1000 万周活——但开放后 9 月跌至 350 万，稀缺密度"
                "必须沉淀为内生网络。",
        "steps": [
            "稀缺必须给诚实理由（产能/手工 onboarding/监管），OnePlus 式表演稀缺被识破即反噬",
            "名额稀缺且不均（活跃者多得），邀请行为加仪式感",
            "设'开闸'条件（密度达标）而非时间表；黑市价是支付意愿探测器",
        ],
        "quota": {},
        "evidence": ["Gmail/Clubhouse 公开数据；OnePlus 反噬案例（Reddit/Cornell 分析）"],
        "caution": "无真实约束兜底的人为稀缺=自毁信任资产。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "term-seeding": {
        "name": "造词播种：零竞争词+定向私信",
        "channel": "通用",
        "mechanism": "compound_loop",
        "status": "proposed",
        "risk": "white",
        "idea": "造一个之前不存在的词（搜索引擎零竞争）→定向私信有影响力的种子人群"
                "请其采纳→关键节点引爆→造词者天然获得定义权与第一名位置：每个用这个词"
                "的人都在免费传播造词者的资产（Sean Ellis 的 growth hacker 战役）。",
        "steps": [
            "造词并给出定义（一句话说清'它是什么、替代什么旧概念'）",
            "列 20-50 个种子影响者名单，定向私信请其试用该词（KOC 播种）",
            "等关键节点引爆；原帖被冷落不放弃，持续播种 1-2 年",
        ],
        "quota": {"total_cap": 50},
        "evidence": ["Andrew Chen 2012《Growth Hacker is the New VP Marketing》引爆点；Ellis 战役二手复盘"],
        "caution": "词造了没人用是可容忍的失败；成本极低值得试。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "bluesky-starter-pack": {
        "name": "Bluesky Starter Pack 策展人红利",
        "channel": "bluesky",
        "mechanism": "borrowed_audience",
        "status": "proposed",
        "risk": "white",
        "idea": "pack 峰值占全站每日关注操作的 43%；成员比同类账号多 85% 新粉，"
                "而 pack 创建者的增益比成员更大——冷启动最优解是当策展人，不是求收录。",
        "steps": [
            "选一个人群足够窄但迁移量大的 niche（职业×立场）",
            "收 100-150 个目标账号（自己也在其中），做成 pack",
            "把 pack 链接发到该人群聚集地（X 迁移讨论、Reddit）",
            "被整包关注时系统通知每个成员，形成二次传播",
        ],
        "quota": {"total_min": 1, "total_cap": 3},
        "evidence": ["ICWSM 2025（Song）：43% 日关注占比、+85% 粉丝/+70% 赞/+60% 发帖"],
        "caution": "未经同意把人塞进 pack 已被记录为滥用形态；策展须是真实推荐。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 60,
    },
    "new-platform-first-two-weeks": {
        "name": "新平台首两周高频占位",
        "channel": "通用",
        "mechanism": "rule_window",
        "status": "proposed",
        "risk": "white",
        "idea": "新平台冷启动期内容真空+算法强行分发：Threads 5 天 1 亿用户、WhatsApp "
                "Channels 7 周 5 亿 MAU——同一动作在窗口期执行=平台级流量补贴，且关注"
                "关系先发占位不可逆。",
        "steps": [
            "新平台/surface 一上线即进场，前两周每天 3-5 帖",
            "把旧平台验证过的爆款重发（跨平台不算重复）",
            "用平台原生格式（pack/custom feed/频道），不照搬推文",
            "补贴期平台（如 Lemon8 类）'赚窗口不押注'：模板化搬运，不投平台专属资产",
        ],
        "quota": {"daily_min": 3, "daily_cap": 5, "window_days": 14},
        "evidence": ["Threads 100M/5 天（Meta 公报）；WhatsApp Channels 500M/7 周"],
        "caution": "窗口 2-8 周随内容密度关闭；新平台粉丝含金量低，占位价值在先发关系。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 45,
    },
    "marketplace-rank": {
        "name": "市场内排名寄生",
        "channel": "marketplaces",
        "mechanism": "search_seat",
        "status": "proposed",
        "risk": "white",
        "idea": "类目第一名可以完全隐形于 Google——流量几乎全来自市场内搜索与榜单："
                "评分数量是站内排名最大权重，先把 listing 做到榜首比做独立站更划算"
                "（Easlo 两年 $500k、Thomas Frank 单年 $100 万+）。",
        "steps": [
            "同时上架 Notion Marketplace/Gumroad 等市场位",
            "优化评分数量与定价（榜首标杆：$79、200 评分）而非先做外链",
            "描述写成搜索意图词；逐语言本地化吃非英语区搜索",
        ],
        "quota": {"total_min": 2, "total_cap": 5},
        "evidence": ["Indie Hackers 对 Gumroad notion template 类目 #1 的审计"],
        "caution": "单一平台依赖是结构性风险；榜首后仍需自有渠道承接。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "awesome-list-matrix": {
        "name": "awesome 列表批量提交",
        "channel": "github",
        "mechanism": "official_tap",
        "status": "proposed",
        "risk": "white",
        "idea": "往 awesome-X 列表提 PR 是体力活矩阵游戏：一次提交 48 个列表 44 个 PR，"
                "被合并后获得列表受众的长尾点击。",
        "steps": [
            "先让项目满 30 天（主列表门槛）",
            "用现成 300+ 提交目录筛列表（含权重/流量数据）",
            "每个 PR 遵守各列表格式，认真回应维护者互审",
        ],
        "quota": {"total_min": 20, "total_cap": 48},
        "evidence": ["dev.to《What we learned from 48 GitHub lists and 44 pull requests》"],
        "caution": "垃圾 PR 会被维护者拉黑；必须是真项目真价值。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 120,
    },
    "substack-notes": {
        "name": "Substack Notes 引擎",
        "channel": "substack",
        "mechanism": "borrowed_audience",
        "status": "proposed",
        "risk": "white",
        "idea": "短内容进入订阅者混合信息流，转发/restack 触发二级分发：有创作者 "
                "70%+ 订阅增长归因 Notes；但 Notes 的 views 不值钱，订阅转化才值钱。",
        "steps": [
            "日更短内容（频率/短长度/发布时机是核心变量）",
            "把回复和 restack 当分发动作做；profile 页放明确订阅钩子",
        ],
        "quota": {"daily_min": 1, "daily_cap": 5},
        "evidence": ["Escape the Cubicle：两年 0→20,000+ 订阅、70%+ 归因 Notes"],
        "caution": "2025 算法改动后旧打法部分失效，需重学；算法依赖度高。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 60,
    },
    "youtube-comment-funnel": {
        "name": "YouTube 置顶评论漏斗",
        "channel": "youtube",
        "mechanism": "borrowed_audience",
        "status": "proposed",
        "risk": "white",
        "idea": "大量观众看评论先于看完视频：自家视频发布即自评并置顶（唯一可挂链接的"
                "评论区位置，放 lead magnet）；同 niche 头部频道发布后 1-2 小时内抢前排"
                "干货评论借流量。",
        "steps": [
            "自家视频：发布即发一条复述价值+CTA 的评论并置顶",
            "蹭位：盯头部频道 1-2 小时内的新视频，第一条发补充型干货评论（不是夸）",
            "评论资料卡（头像+频道名）就是广告位；不带裸链接防 spam 判定",
        ],
        "quota": {"daily_cap": 5},
        "evidence": ["impactplus 置顶评论 lead magnet 案例；r/NewTubers 实践共识"],
        "caution": "纯引流裸链接会被判 spam；蹭位评论必须真增值。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "comment-to-dm-spiral": {
        "name": "评论换私信螺旋",
        "channel": "instagram",
        "mechanism": "compound_loop",
        "status": "proposed",
        "risk": "light_gray",
        "idea": "CTA 不说'私信我'而说'评论 WORD 领取'：评论喂算法扩大分发→更多评论→"
                "自动 DM 触达（打开率约 90%）——算法吃评论、转化吃 DM，一鱼两吃"
                "（Amy Porterfield 单漏斗 $1.4M）。",
        "steps": [
            "Reels 里设评论关键词钩子（'评论模板领取'）",
            "ManyChat 官方合规模式设关键词→自动 DM 链路",
            "DM 第二条跟进转化（链接/日历/优惠码）；每周 2-3 条带钩子 Reels 维持螺旋",
        ],
        "quota": {"weekly_min": 2, "weekly_cap": 3},
        "evidence": ["ManyChat 官方案例：$1.4M；DM 打开约 90%/回复约 60% 对比研究"],
        "caution": "须在平台官方自动化合规模式内；过度触发会被限 DM 权限。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 60,
    },
    "x-thread-hook": {
        "name": "X 长推钩子五成法则",
        "channel": "x",
        "mechanism": "story_pull",
        "status": "proposed",
        "risk": "white",
        "idea": "thread 的可见性入口是折叠的第一条：只有钩子能赢得展开，thread 结构"
                "拉长停留时长（算法核心信号）——多家指南一致配比：50% 创作时间花在第一句。",
        "steps": [
            "钩子公式：好奇心缺口/大胆断言/高具体性数字（$0→$60K 的 7 个决定）",
            "第一条 1-2 行不剧透结论；结尾一条放 CTA+关注理由",
            "纪念日复盘帖（'一年前我 0 收入，今天……'）：起点越惨对比张力越强",
        ],
        "quota": {"weekly_cap": 4},
        "evidence": ["30+ hooks 公式分解（aifreeforever）；X 2026 增长复盘"],
        "caution": "夸张钩子兑现不了会在第 9 秒被划走并记住你。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "v2ex-free-first": {
        "name": "V2EX 免费无注册钩子",
        "channel": "v2ex",
        "mechanism": "borrowed_audience",
        "status": "proposed",
        "risk": "white",
        "idea": "社区对营销腔零容忍、对'完全免费、无需注册、无限制'极度宽容——被喷的"
                "从来不是推广而是'注册即付费'；降低体验门槛本身就是最好的帖子里钩子"
                "（实例：3800 注册、约 6% 付费转化）。",
        "steps": [
            "注册满 30 天（节点门槛），期间正常参与讨论",
            "发帖标题写明技术栈与定位，正文强调免费无注册",
            "全程泡评论区回复（评论区互动决定二次曝光），后置付费转化",
        ],
        "quota": {"monthly_cap": 2},
        "evidence": ["V2EX《独立开发一年半，一个丑工具月流水 4700》等实战帖"],
        "caution": "发帖频率过高或纯广告号会被喷并失去节点权限。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "cross-platform-trend": {
        "name": "跨平台趋势时间差搬运",
        "channel": "tiktok",
        "mechanism": "proven_demand",
        "status": "proposed",
        "risk": "light_gray",
        "idea": "同一趋势在两平台有时间差红利：TikTok 验证过的格式在 Reels 侧常常未"
                "饱和——第一个把趋势搬过去的人独占早期分发；趋势可用窗口仅 3-5 天且在缩短。",
        "steps": [
            "监控 TikTok 创意中心热门音频的上升榜（不是总榜）",
            "挑 Reels 还没人用的，48 小时内本地化重制（不带水印，官方降权水印内容）",
            "发布挂同款音频名；在趋势上升期而非峰值入场（峰值=接盘）",
        ],
        "quota": {"weekly_cap": 3},
        "evidence": ["TikTok 趋势寿命统计（3-5 天）；跨平台 trend hijacking 指南"],
        "caution": "带水印直接搬运会被降权；社区型趋势可二次跟进，热点型不可。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 45,
    },
    # ---- 以下来自研究 III（2026-11-14）：产品内生 / 价值交换 / 付费放大 / 对手事件 ----
    "output-attribution": {
        "name": "产出物署名（水印即移除权商品）",
        "channel": "产品",
        "mechanism": "product_embedded",
        "status": "usable",
        "risk": "white",
        "idea": "免费用户每一份产出物自带署名，被分享的每一刻都是零获客成本分发；"
                "付费解锁的不是功能而是去署名权——转化与分发共享同一个开关"
                "（Calendly 徽章带来 25% 新用户，循环中位 24 小时）。",
        "steps": [
            "审计产品所有出口：分享链接、导出文件、外发邮件、嵌入代码，逐个装署名",
            "署名放在产出的社会性最强位置；点击进入'这份产物怎么做'演示页而非首页",
            "清晰到让在意形象的人付费移除（他们付费意愿最高）；付费前永不弹窗",
            "给主动佩戴等级：愿意带署名的用户换额外额度（署名从税变成交易）",
        ],
        "quota": {"total_min": 1},
        "verdict_metrics": ["signup_from_output_share", "badge_removal_conversion"],
        "evidence": [
            "Calendly 徽章曝光后 25% 新用户注册、循环 24h（FlowJam 拆解）",
            "Zoom 结束页 overlay+40 分钟墙贡献 IPO 前 55% 新客户",
            "Canva 每月 10 亿份设计物、1K→10M 用户三年基本零付费获客",
        ],
        "caution": "假可移除（付费了还留）是深灰；美观敏感用户需给足移除路径。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "viewer-page-funnel": {
        "name": "观看页即落地页 + 交互墙",
        "channel": "产品",
        "mechanism": "product_embedded",
        "status": "proposed",
        "risk": "white",
        "idea": "产出物消费端保留完整产品界面，注册推迟到'我也想来一个'的瞬间"
                "（Loom 观看超 75% 视频者 32% 七天内注册）；被动消费零门槛，"
                "主动动作（评论/编辑/导出）触发注册墙——门在价值之后。",
        "steps": [
            "观看/填写页保留产品完整界面与操作按钮（不做成裸内容页）",
            "结尾 overlay 给'自己做一个'入口；第二条内容引导浏览分享者的工作区",
            "交互墙精确卡在社交动作上：点赞/评论/编辑的瞬间才要求注册",
        ],
        "quota": {},
        "evidence": ["Loom 32%/7 天转化；Figma 评论需登录、view-only 自由传播"],
        "caution": "KPI 是完播率不是即时注册率；门放太前会杀死分发。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "invite-first-action": {
        "name": "邀请内嵌第一动作",
        "channel": "产品",
        "mechanism": "product_embedded",
        "status": "proposed",
        "risk": "white",
        "idea": "首个高价值动作天然需要第二个人，邀请发生在激活最兴奋的时刻而非"
                "事后弹窗（Slack 邀请循环 1.8 天，约为行业基准 48h 的 3 倍速度）。",
        "steps": [
            "设计单人无法完成的首个闭环（需要协作者/审阅者/收件人）",
            "机器人代办邀请（Slackbot 模式：建工作区即自动通知队友）",
            "邀请循环周期压到 48h 内并按周度量",
        ],
        "quota": {},
        "evidence": ["Slack 30% 新工作区诞生于既有工作区内；协作类循环 K 0.2-0.5"],
        "caution": "伪造的协作必要是浅灰；真实需求才算数。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "group-ceiling": {
        "name": "群体天花板（锁协作连续性）",
        "channel": "产品",
        "mechanism": "product_embedded",
        "status": "proposed",
        "risk": "light_gray",
        "idea": "免费额度限制协作连续性（历史/时长/参与者数）而非高级功能：触顶"
                "痛点由全员共同感知，升级压力由集体共识推动——使用者替你完成销售。",
        "steps": [
            "额度选社交可见的资源；触顶前预警全员，让迁移决定公开化",
            "付费页展示'解锁的是全团队'而非个人",
            "墙内内容可见不可读（显示'你有 10000 条锁定历史'）",
        ],
        "quota": {},
        "evidence": ["Zoom 40 分钟截止贡献 55% 新客户；Slack 10k 消息触顶推动全公司迁移"],
        "caution": "透明与提前告知是白帽前提；坐牢式锁定的舆论反弹参考 Unity 事故。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "champion-sales-kit": {
        "name": "内部推动者弹药库",
        "channel": "产品",
        "mechanism": "product_embedded",
        "status": "proposed",
        "risk": "white",
        "idea": "成交发生在 champion 转身向老板汇报的会议室里——把内部销售材料做进"
                "产品（自动填该团队真实使用数据的 ROI 一页纸、安全问卷预答、异议话术），"
                "等于给每个基层用户配了销售助理。",
        "steps": [
            "检测到多席位使用信号时推送'内部审批包'",
            "四件套：business case 模板（5 分钟可改写成自己的）、ROI 计算器、"
            "安全/预算/竞品三问标准答案、转发给上级的邮件模板",
            "附'如何回答 IT 质疑'话术；同域 5 个自发注册时自动衔接销售",
        ],
        "quota": {},
        "evidence": ["champion enablement 共识（Revspire/CMX）；同域>5 注册自动建销售机会"],
        "caution": "材料放产品内、champion 最焦虑的时刻，比放营销官网有效得多。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "activation-threshold": {
        "name": "激活阈值工程",
        "channel": "产品",
        "mechanism": "product_embedded",
        "status": "proposed",
        "risk": "white",
        "idea": "用 cohort 数据找'跨过后留存跳变'的使用阈值（Slack 2000 条消息式），"
                "把 onboarding 全部对齐到推用户过线——阈值达成前不推任何变现。",
        "steps": [
            "画首周使用频次 vs 8 周留存曲线找拐点",
            "onboarding 呈现为进度条/checklist，每项对应阈值构成行为",
            "空状态做成首屏广告：一句话价值+进度可视化+唯一 CTA",
        ],
        "quota": {},
        "evidence": ["Mode Analytics：Slack 2000 消息后约 93% 团队留存；Twitter 30 follows 同源"],
        "caution": "阈值是 cohort 分析产物，不是拍脑袋的北极星。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "cohort-onboarding": {
        "name": "批次开班制入场",
        "channel": "产品",
        "mechanism": "product_embedded",
        "status": "proposed",
        "risk": "white",
        "idea": "把注册从'随时可以'改成'现在必须'：限额开班、满员即关，同期生"
                "社交网络让留存与推荐发生在用户之间；每届毕业生是下一届的招生宣传。",
        "steps": [
            "每周一个开班日，同期完成激活 checklist；限额真实且执行",
            "闭班期 waitlist 蓄水而非偷偷放行；编号批次强化仪式感",
            "先找到付费方资助你做内容，再卖席位（Outseta 25 万欧元第一课）",
        ],
        "quota": {},
        "evidence": ["On Deck 80-100 人/批；Outseta cohort 课程 9 条经验"],
        "caution": "虚假倒计时/假满员是深灰；工具型产品可降维成 cohort 式 onboarding。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "lock-assets-not-features": {
        "name": "锁资产不锁功能",
        "channel": "产品",
        "mechanism": "value_exchange",
        "status": "proposed",
        "risk": "white",
        "idea": "免费版不阻断使用，把用户积累的资产留在付费墙后——用户越成功付费"
                "理由越强，付费赎回的是自己的过去；行业 freemium 转化 2-5%，最佳 8-15%。",
        "steps": [
            "免费额度选历史/存量维度而非当次使用维度",
            "找到自己的'2000 条消息'阈值，全产品引导对齐到推用户过线",
        ],
        "quota": {},
        "evidence": ["Slack 免费版锁 10k 可搜索消息；OpenView/ProfitWell freemium 基准"],
        "caution": "坐牢式锁定有舆论反弹边界；透明是前提。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "data-benchmark-report": {
        "name": "私有数据换公开引用",
        "channel": "内容",
        "mechanism": "value_exchange",
        "status": "proposed",
        "risk": "white",
        "idea": "你每天产生的私有数据对市场是稀缺信息：清洗成公开基准研究，用别人"
                "拿不到的数据换别人给不起的外链——Ahrefs 一篇研究拿到 3600 个引用域名。",
        "steps": [
            "从遥测数据找'人人想知道但没人测过'的问题；样本量进标题",
            "每个结论做成可截图数据点；明示方法与局限（反而提高被引用率）",
            "年度化成固定命名系列（Octoverse 模式）；报告页免 gate 拿链接，"
            "原始数据集下载层收线索",
        ],
        "quota": {"yearly_min": 1},
        "evidence": ["Ahrefs AI Overview 研究：7.5 万品牌、3600 引用域名；GitHub Octoverse"],
        "caution": "用户数据脱敏合规是硬边界；样本偏差会被公开挑战。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "works-with-matrix": {
        "name": "集成页矩阵与伙伴目录",
        "channel": "产品",
        "mechanism": "value_exchange",
        "status": "proposed",
        "risk": "white",
        "idea": "每集成一页（/integrations/{tool}），吃的全是'两个品牌词'的高意图"
                "长尾——Zapier 数百万 connect-A-to-B 页是其 SEO 护城河本体；集成"
                "目录表现解锁平台联合营销。",
        "steps": [
            "优先上架 Zapier/Slack 类目录；养健康度爬 Silver→Gold 解锁推广",
            "每个集成独立落地页：用例、模板、视频；与伙伴互链",
            "再加一层模板变体：'用 A+B 完成{任务}'",
        ],
        "quota": {"total_min": 5},
        "evidence": ["Zapier 目录 9000+ apps、Partner Program 三级制"],
        "caution": "页面数据需随集成现状维护；平台规则变更不可控。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 120,
    },
    "amplify-organic-winners": {
        "name": "只放大赢家（Spark/Post ID 式）",
        "channel": "付费",
        "mechanism": "paid_amplify",
        "status": "proposed",
        "risk": "white",
        "idea": "创意测试免费外包给有机算法，付费只放大已跑赢的内容：直接用原帖"
                "投放保留全部社交证明（CTR +68%、CPC -45%，贡献 40% 转化）。",
        "steps": [
            "监控自然帖互动，超账号基线 2-3 倍者 48h 内上放大",
            "Meta 用 existing Post ID（复制广告会清零赞评）；TikTok 走 Spark 授权",
            "LinkedIn 放大创始人个人帖（TLA：CTR 比单图高 252%），$10/天起步",
        ],
        "quota": {"weekly_cap": 3},
        "verdict_metrics": ["cpc_vs_cold_baseline", "amplified_post_ctr"],
        "evidence": ["Coegi Spark Ads 案例；LinkedIn TLA 官方数据"],
        "caution": "Boost 按钮定向残缺是陷阱，走正规 Ads Manager；niche 帖投 niche 受众。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "warm-pool-first": {
        "name": "暖池优先律",
        "channel": "付费",
        "mechanism": "paid_amplify",
        "status": "proposed",
        "risk": "white",
        "idea": "日预算低于 50 美元时禁止冷启动（学习期永远毕不了业）：100% 投给"
                "互动过的人/访客（暖受众转化数倍于冷流量），冷启动交给有机内容免费做。",
        "steps": [
            "建互动自定义受众，窗口收窄到 14-30 天只追新鲜暖",
            "暖池与冷流量预算物理分离（小暖池会吸干整个 campaign）",
            "频次>=3 且 CTR 连降即停投养池；每 1-2 周换创意",
        ],
        "quota": {},
        "evidence": ["Meta 学习期约 50 转化/周；暖受众转化倍数与饱和数据（marketdisrupt 等）"],
        "caution": "暖池是消耗性资产，疲劳后继续投=花钱买负印象。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 120,
    },
    "micro-abm": {
        "name": "微 ABM（只投百家目标公司）",
        "channel": "付费",
        "mechanism": "paid_amplify",
        "status": "proposed",
        "risk": "white",
        "idea": "上传 50-100 家目标公司名单做公司定向，零浪费展示——受众窄不是"
                "问题而是特性：展示即触达，频次即心智；与创始人内容放大组合最强。",
        "steps": [
            "列目标公司 CSV 上传 LinkedIn 公司定向",
            "30 天一轮：前 15 天纯曝光，后 15 天加 CTA",
            "与销售/私信动作同步排期",
        ],
        "quota": {"total_min": 50, "total_cap": 200},
        "evidence": ["LinkedIn 公司定向机制；25 账号起步的 30 天 ABM pilot 框架"],
        "caution": "名单质量决定一切；CPC 高，日预算太低跑不动。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 120,
    },
    "newsletter-direct-buy": {
        "name": "小刊直采与保底条款",
        "channel": "付费",
        "mechanism": "paid_amplify",
        "status": "proposed",
        "risk": "light_gray",
        "idea": "绕过广告网络（2 万美元门槛）直采 1-5K 订阅的 niche 刊（25-200 美元/位）；"
                "按真实打开还价；保底点击条款（不达标免费重发）把方差风险转回刊主。",
        "steps": [
            "找同 niche 小刊，按每千真实打开 50-100 欧还价",
            "谈保底点击；要求去重 unique clicks（防过滤器假点击）",
            "预算拆 8-10 次小试建每刊 CPA 表，复购赢家刊",
            "投放前过单位经济闸门：LTV>=250 美元且 ARPU>=40 美元；"
            "承接物用即时价值物而非工具注册",
        ],
        "quota": {"per_test_cap": 300},
        "evidence": ["isora 750 美元/124 点击/0 转化的完整反面案例与门槛公式"],
        "caution": "单刊质量方差极大；低于单位经济线投放前就输了。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "cloud-credits-budget": {
        "name": "云额度变现（省下的即广告预算）",
        "channel": "付费",
        "mechanism": "paid_amplify",
        "status": "proposed",
        "risk": "white",
        "idea": "startup 计划的云额度不是技术福利，是增长预算的隐形来源——把本来"
                "要付的基础设施账单清零，省下的现金直接转投放。",
        "steps": [
            "三家同时申请：微软 Founders Hub（门槛最低，1000-15 万美元梯子）、"
            "Google Cloud（最高 35 万）、AWS Activate",
            "附带的 SaaS 额度（CRM/建站/AI）本身就是订阅费抵扣",
            "条款逐年变，落地前逐家官网核验",
        ],
        "quota": {"total_min": 1},
        "evidence": ["三家 startup 计划的 2026 口径（官网为准）"],
        "caution": "额度有效期与用量解锁条件要读细；不算收入算成本抵扣。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "price-hike-harvest": {
        "name": "对手涨价日收割",
        "channel": "通用",
        "mechanism": "competitor_events",
        "status": "proposed",
        "risk": "white",
        "idea": "对手涨价那一刻，它的付费用户全体变成价格敏感高意图人群：监控价格页"
                "diff，48 小时内上线成本计算器与迁移对照——MailerLite 90 天承接了 "
                "321 个从 Mailchimp 迁出的域名。",
        "steps": [
            "每日 diff 竞品价格页与 changelog RSS；涨价>15%/计划合并即触发",
            "预制'从 X 迁移'落地页+一键迁移工具+对照计算器",
            "收编自然出现的'ditch X'文章（主动供稿给作者）",
        ],
        "quota": {},
        "evidence": ["Mailchimp 2019 涨价与 MailerLite 承接数据"],
        "caution": "只比事实不诽谤；数据失准会被反噬。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 120,
    },
    "status-page-arbitrage": {
        "name": "对手故障承接",
        "channel": "通用",
        "mechanism": "competitor_events",
        "status": "proposed",
        "risk": "light_gray",
        "idea": "竞品 status page 是带时间戳的公开故障账本：故障>30 分钟时其客户"
                "痛感与迁移搜索双双峰值——预写三件套在窗口内释放。",
        "steps": [
            "聚合监控竞品 status page RSS",
            "触发即发：讲韧性/迁移的帖子（不嘲讽）、搜索词投放、"
            "给公开抱怨者发帮助性内容（不是推销）",
        ],
        "quota": {},
        "evidence": ["CAM 把 status page 当销售角度的框架；StatusGator 监控 12700+ 服务"],
        "caution": "趁灾营销观感风险高：卖安全感，不踩对手。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 120,
    },
    "lost-deal-harvest": {
        "name": "输单收割系统",
        "channel": "通用",
        "mechanism": "competitor_events",
        "status": "proposed",
        "risk": "white",
        "idea": "输给竞品的单是'竞品交付失败'事件的行权凭证：输单时访谈录入决定"
                "因素与合同期，竞品故障/续约前 60 天低摩擦回收。",
        "steps": [
            "输单 48h 内发访谈（问'最终决定因素'不问'为什么不选我们'）",
            "回访触发器=竞品故障事件+续约窗口",
            "回收话术只谈拆除迁移成本：免费迁移/代付/买断剩余合同",
        ],
        "quota": {},
        "evidence": ["方向性机制（冷触达回流低个位数 vs 交付不达预期时 10-25%），执行前先用自家数据验证基线"],
        "caution": "证据等级较低，先小样本跑。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "vs-page-matrix": {
        "name": "对比页矩阵（含合规协议）",
        "channel": "搜索",
        "mechanism": "competitor_events",
        "status": "proposed",
        "risk": "white",
        "idea": "alternative 类词转化率 7.5%+（所有词类最高）：每竞品三件套"
                "（alternatives 长文/双方对比/三方对比）——DR28 新站靠此打法数周上首页。",
        "steps": [
            "每个主要竞品一页，域名级 URL；长文形态不是薄落地页",
            "每个负面对比 claim 附来源与日期戳；竞品改版 7 天内更新",
            "每季度查 AI 引擎引用的是哪个版本（Graphite 因过时数据丢单）",
        ],
        "quota": {"total_min": 3},
        "evidence": ["Grow & Convert：7.5%/5% 转化、149 注册案例；Lanham Act 允许 honest disparagement"],
        "caution": "指名竞品合法，风险在数据无佐证；把对比页当代码仓库维护。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "migration-narrative": {
        "name": "迁移叙事三段式",
        "channel": "内容",
        "mechanism": "competitor_events",
        "status": "proposed",
        "risk": "white",
        "idea": "迁移内容的最大转化变量不是'我们更好'而是切换风险拆除：成本审计"
                "工具→具名客户故事→零风险出口（免费迁移/买断比打折转化更高）。",
        "steps": [
            "每竞品一套三段式内容",
            "邮件序列按竞品涨价/故障事件触发（与收割管线联动）",
        ],
        "quota": {},
        "evidence": ["SaaStr 切换战术清单；8 个 B2B 战役的 Switch & Save 公共结构"],
        "caution": "具名故事需客户授权；审计工具数字需可佐证。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 120,
    },
    "executive-dinner-series": {
        "name": "高管晚宴系列",
        "channel": "线下",
        "mechanism": "trust_nodes",
        "status": "proposed",
        "risk": "white",
        "idea": "物理事件的稀缺品是同侪密度不是内容：8-15 人晚宴、单一 ICP 单一痛点、"
                "无 slides——webinar 平均转化仅 2.3%，晚宴 30 天 4 个后续会议是及格线。",
        "steps": [
            "名单=目标 3 倍；客户占席 40-50%（同行证言以非销售口吻发生）",
            "重量级客户/前辈个人署名邀请（确认率 25-40%）；卖家个人邮箱执行",
            "开场 3 分钟抛一个挑衅性问题然后闭嘴；90 分钟=5 分钟框架/70 讨论/15 综合",
            "24h 内 debrief、1 周内个人化感谢信引用席间原话",
        ],
        "quota": {"monthly_cap": 1},
        "verdict_metrics": ["meetings_30d", "pipeline_30d"],
        "evidence": ["Attendir 晚宴 playbook（单桌 5-10K vs 展位 30K）；MarketingProfs 数据"],
        "caution": "人多、pitch、主题模糊是三大失败模式；第一场宾客晒图即下一场名单。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "counter-positioning": {
        "name": "反定位公开叙事",
        "channel": "内容",
        "mechanism": "story_pull",
        "status": "proposed",
        "risk": "white",
        "idea": "把'他们为什么不学我们？因为他们靠 X 赚钱'讲给客户听：攻击点选在对手"
                "损益表的对侧（free vs 订阅/local vs 云/隐私 vs 广告）——功能对比下个月"
                "就补上，商业模式对比永远补不上（Blockbuster 8 亿美元滞纳金无法跟进）。",
        "steps": [
            "先过三问：新模式独立成立吗/对手跟进的净现值为负吗/管理层敢吗",
            "给每个主要竞品画'它无法做的事'清单（从收入结构推导）",
            "写成公开的'为什么我们永远不做 X'宣言页；每季度复核对手是否已能还手",
        ],
        "quota": {},
        "evidence": ["Commoncog 反定位 22 案例（Fidelity/Vanguard、Dell/Compaq）"],
        "caution": "limited power：通常只防住特定对手且持续几年，叙事要有过期日。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    # ---- 以下来自研究 IV（2026-11-14）：自建场地/联盟/浪潮/透明/Agent 位 ----
    "run-your-own-awards": {
        "name": "自办年度奖项",
        "channel": "通用",
        "mechanism": "own_the_venue",
        "status": "proposed",
        "risk": "white",
        "idea": "你办'X of the Year'，提名者为了赢会自发动员自己的用户来投票——"
                "每个参选产品都变成你的拉新渠道，奖项是'把行业的营销预算收编为你的"
                "获客动作'（Golden Kitty 单品类 11,497 条提名）。",
        "steps": [
            "定义 10-20 个品类（品类即你所在生态的地图）；公开提名+投票窗口（Q4 情绪最好）",
            "实体奖杯（分享率核心变量）+ 可嵌入徽章（免费外链逐年累积）",
            "给全部参选者发拉票工具包（预写文案+链接）",
            "第一届声量不足的解法：提名制——你先填满 80% 名额，留 20% 公开提名",
        ],
        "quota": {"yearly_min": 1},
        "verdict_metrics": ["nominations_growth_yoy", "voter_emails_collected"],
        "evidence": ["Product Hunt Golden Kitty Awards 第七届单品类 11,497 提名；获奖者长期挂徽章"],
        "caution": "权威是'办'出来的不是先有的：规则透明+每年坚持+实体奖杯即可起盘。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 365,
    },
    "niche-directory-site": {
        "name": "细分目录站",
        "channel": "web",
        "mechanism": "own_the_venue",
        "status": "proposed",
        "risk": "white",
        "idea": "为正在爆炸的品类建全量目录（每个工具一页吃长尾搜索），工具方免费提交、"
                "付费置顶——目录成为品类默认入口（TAAFT：一人项目 90M 用户、月访 "
                "320-450 万）。窗口期 6-18 个月，晚一年入口位已被占。",
        "steps": [
            "选供给爆炸需求迷茫的品类；前 300 条手工录入建底座",
            "开放免费提交（1-2 天审核制造稀缺）+ 付费置顶层",
            "每天 30-60 分钟审核维护；判定：品类词+list 关键词占位",
        ],
        "quota": {},
        "evidence": ["theresanaiforthat.com：2022-12 上线、DR≈77、9000 万累计用户"],
        "caution": "窗口赌错品类则血本无归；大平台官方目录下场会截流。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "certification-funnel": {
        "name": "免费认证学院",
        "channel": "web",
        "mechanism": "own_the_venue",
        "status": "proposed",
        "risk": "white",
        "idea": "把方法论做成免费认证课程：学员为职业资本完课并把证书徽章挂 LinkedIn——"
                "每个毕业生等于在目标受众池里挂了一块永久广告（HubSpot 单门课年发 "
                "11.2 万徽章），认证人群即最优质线索池。",
        "steps": [
            "把产品隐含方法论显性化为 5-8 小时课程+考试（考核真有难度，价值来自难度）",
            "颁发可一键添加到 LinkedIn 的徽章；设 1-2 年有效期制造周期复访",
        ],
        "quota": {},
        "evidence": ["HubSpot Academy Inbound 认证年发 112,000+ 徽章（G2 口径）"],
        "caution": "课程是获客产品不是变现产品：收费筛掉流量，免费+认证筛掉不认真的人。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 365,
    },
    "newsletter-of-record": {
        "name": "领域官方周报",
        "channel": "email",
        "mechanism": "own_the_venue",
        "status": "proposed",
        "risk": "white",
        "idea": "每周固定点评该领域 2-3 个最新工具，48 期不动摇——'想跟上这个领域→订阅"
                "这份周报'成为集体习惯（Console.dev 一人维护 3 万+订阅）。每周只写 "
                "2-3 条+公开筛选标准=公开宣示裁判身份。",
        "steps": [
            "公布入选标准页（这是裁判证书）；固定发送日 48 期不动摇",
            "每期被点评对象自动成为传播节点（@他们）",
            "变现后置：赞助位卖给想触达这批读者的人（通常是被点评过的公司）",
        ],
        "quota": {"weekly_min": 1, "weekly_cap": 1},
        "verdict_metrics": ["subscribers", "open_rate"],
        "evidence": ["console.dev：30,000+ 订阅、公开筛选标准、赞助变现"],
        "caution": "数量越少入选越值钱；最坏结果沉淀为高粘性邮件列表仍是资产。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "ltd-injection": {
        "name": "LTD 一次性注入",
        "channel": "marketplaces",
        "mechanism": "alliance_bundle",
        "status": "proposed",
        "risk": "light_gray",
        "idea": "在 AppSumo 类平台卖终身授权换现金+用户+评价（复盘：$100K/875 单）；"
                "反共识三条：LTD 用户 6 个月留存 63% vs 其他渠道 48%；不是快钱（Net 60 "
                "月结+60 天退款）；自带买家拿 95% 分成——用自己链接导自有受众最划算。",
        "steps": [
            "上架前算预支月数=LTD 价/月订阅价；每档设用量上限把重度留给订阅",
            "把自有 X/邮件受众用自己的链接导入（吃 95% 档+平台结账与评价体系）",
            "判定：扣退款净到手、退款率、6 个月活跃率、每用户月均工单数",
        ],
        "quota": {"total_cap": 1},
        "evidence": ["Reddit AppSumo 一手复盘：$100K/875 单；留存 63% vs 48%；95%/70% 分成档"],
        "caution": "支持义务是永久的（上架条款要求一年更新支持）；把自有受众变终身用户有机会成本。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "bundle-rideshare": {
        "name": "订阅捆绑搭车",
        "channel": "marketplaces",
        "mechanism": "alliance_bundle",
        "status": "proposed",
        "risk": "white",
        "idea": "把产品放进别人的订阅包（Setapp 模式）：平台负责获客收款按用量分钱"
                "（开发者 70%/平台 10%/带来用户的伙伴 20%）——高频小工具比低频大工具划算。",
        "steps": [
            "评估自家产品在该平台的使用频次分位；高频者分得更多",
            "上架首日约 3 万独立曝光（Setapp 口径）；判定：月活使用次数与每用户分成",
        ],
        "quota": {"total_cap": 2},
        "evidence": ["setapp.com/developers：分成结构与上架曝光数据"],
        "caution": "分成按用量不按品牌；20% 伙伴分成归属细则要读开发者文档。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "integration-partner-loop": {
        "name": "集成伙伴互挂",
        "channel": "产品",
        "mechanism": "alliance_bundle",
        "status": "proposed",
        "risk": "white",
        "idea": "与互补产品做集成并互挂集成目录：伙伴带来的不是流量而是成交率"
                "（Crossbeam 客户自述：伙伴参与的单成交快 53%、CAC 低 72%）。",
        "steps": [
            "先做一个真实可用的集成，再要对方目录位",
            "前 10 个伙伴从现有用户已在用的工具里挑，不做陌生外联",
            "对照双方客户重叠名单做定向推荐；判定：伙伴来源注册数/激活率/成交周期",
        ],
        "quota": {"total_min": 3, "total_cap": 10},
        "evidence": ["Crossbeam 案例组（利益相关方自述）：Pigment 首年 16% 新客收入来自伙伴"],
        "caution": "样本是有销售团队的 B2B，不能直接外推到一人公司。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "q4-budget-flush": {
        "name": "Q4 预算冲单窗",
        "channel": "销售",
        "mechanism": "event_waves",
        "status": "proposed",
        "risk": "white",
        "idea": "企业年度预算 12 月过期且花不掉明年被砍——10 月才是起跑线（法务采购要 "
                "6-8 周），12 月中旬前签约；财年错位可打三轮（联邦 9 月合同量比月均+75%、"
                "日企 3 月）。",
        "steps": [
            "监控：Gartner/IDC 季度报告的 budget flush 表述；按财年末月分段客户名单",
            "预制：'年底前花掉的 N 种方式'话术、年度折扣、48 小时出合同的轻量采购路径",
            "节奏：联邦 7 月启动、美企 10 月、日企 1 月；12 月 10 日后停止催签",
            "B2B 黑五只对已在试用/谈判中的账户推年度折扣（缩短已有 deal 决策链）",
        ],
        "quota": {"yearly_min": 1},
        "evidence": ["NTU：联邦 9 月合同授予激增约 75%；kōdlo：B2B 成交周期 4.6-6.5 个月"],
        "caution": "Q4 冲单透支 Q1：1 月是评估月不是成交月——做选型清单，不做限时优惠。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 365,
    },
    "sunset-pipeline": {
        "name": "大产品关停承接",
        "channel": "通用",
        "mechanism": "event_waves",
        "status": "proposed",
        "risk": "white",
        "idea": "大厂关停产品时用户被迫 2-16 周内迁移：Feedly 在 Google Reader 关停公告前"
                "几个月就秘密建好迁移基础设施（Normandy 项目），公告后 48 小时吃进 50 万"
                "用户——速度来自预制，不来自反应快。",
        "steps": [
            "维护 sunset 观察清单（产品>5 年未更新/被收购整合/API 定价突变是前兆）",
            "为每个'如果它死了用户去哪'的邻接产品预备一键导入器+迁移指南模板（留空产品名）",
            "公告后 24 小时内上线迁移页并投对手品牌词；前 72 小时全渠道饱和",
        ],
        "quota": {},
        "evidence": ["CNET/The Verge：Feedly 因 Reader 关停 48 小时新增 50 万用户"],
        "caution": "承接方不需要更好，只需要'迁移成本最低+像它'；尖峰 90 天内必须转邮件列表。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 365,
    },
    "regulatory-deadline": {
        "name": "监管生效日日历",
        "channel": "内容",
        "mechanism": "event_waves",
        "status": "proposed",
        "risk": "white",
        "idea": "法规生效日是不可延期的需求 deadline：GDPR 创造隐私科技品类（OneTrust "
                "做到营收 $4.64 亿）；红利不在最全的合规产品，在'最早做生效日倒计时内容+"
                "免费检测工具'——买家恐慌期先搜'我该怎么办'。",
        "steps": [
            "监控：欧盟官方公报/各州立法生效日历（隐私法扎堆 1 月 1 日生效）",
            "预制：自检清单+免费扫描工具+'距生效还有 N 天'倒计时页",
            "节奏：生效前 12 个月布局搜索、前 3 个月付费转化、生效后 30 天收割拖延者长尾",
        ],
        "quota": {},
        "evidence": ["OneTrust：GDPR 前夜的免费 cookie 扫描起家，六轮融资 11 亿美元"],
        "caution": "需求爆发在公告日不在生效日；同一主题第二三次公告的浪潮强度递减。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 365,
    },
    "platform-migration-waves": {
        "name": "平台迁移潮承接",
        "channel": "通用",
        "mechanism": "event_waves",
        "status": "proposed",
        "risk": "white",
        "idea": "平台每次规则恶化触发迁移潮（X 限流→Threads 5 天 1 亿；大选后 Bluesky "
                "数日 70 万用户）——爆发在 2-5 天、衰减在 4-8 周；注册不等于留存"
                "（Bluesky 3600 万注册只有 1300 万 90 天活跃）。",
        "steps": [
            "监控：X 的 API/定价/限流公告与政治事件（历史两大潮都是政治触发）",
            "预制：迁移指南模板、'X 替代品对比'常青页（提前养权重）、全平台同名占位",
            "事件日 24 小时内高频发'迁来第一件事'内容；前两周转化关注，之后立刻导邮件列表",
        ],
        "quota": {},
        "evidence": ["arXiv The Rise of Bluesky：2800 万/3000 万注册与 90 天活跃 1300 万"],
        "caution": "迁移潮是尖峰不是转移；对冲尖峰回落的唯一方式是自有渠道。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "open-metrics-with-exit": {
        "name": "公开收入（带退出机制）",
        "channel": "内容",
        "mechanism": "radical_transparency",
        "status": "proposed",
        "risk": "light_gray",
        "idea": "公开收入换注意与信任（Baremetrics 125,000+ 追踪者），但透明有生命周期："
                "头部 open startup 几乎全部退出且低调退出（Transistor 18 个月后因竞争"
                "关闭）——把'停止公开'设计进战略，公开总结而非实时面板。",
        "steps": [
            "用第三方验证面板（Stripe 直出）消数字注水怀疑；每月配带叙事的总结",
            "红线：收入可滞后公开；分客群流失/定价弹性/营销实验结果永不公开（对手能看到你两边棋盘）",
            "预设退出触发器：出现直接竞对/数字转跌/同行普及；退出时换成月度叙事总结",
        ],
        "quota": {"monthly_min": 1},
        "evidence": ["Baremetrics 公开收入后 125k+ 追踪者；Transistor/Buffer 退出案例（Arvid Kahl 一手复盘）"],
        "caution": "实时面板只能展示上涨叙事；跌的时候按四要素框架披露（语境先行/配对指标/主动告知/延迟发布）。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 180,
    },
    "public-roadmap-voting": {
        "name": "公开路线图投票",
        "channel": "产品",
        "mechanism": "radical_transparency",
        "status": "proposed",
        "risk": "white",
        "idea": "公开 roadmap+投票把'我们决定做什么'变成'你们决定'：每张票是行为级需求"
                "信号，投票者自发回访看进度（Canny 数据：功能请求 +80%、支持收件箱 -20%）。",
        "steps": [
            "公开投票板，按票数而非'喊得最响'排优先级",
            "deal-breaker 级请求显式标记状态（计划中/不考虑）——预期管理本身就是留存",
            "'缺功能而流失'的潜客转化为'等通知'的关注者",
        ],
        "quota": {},
        "evidence": ["Canny 案例组：请求 +80%、inbound -20%；Missive 捕获流失潜客"],
        "caution": "最安全的一种透明（不暴露财务）；唯一成本是承诺管理。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 365,
    },
    "changelog-as-content": {
        "name": "更新日志即内容",
        "channel": "产品",
        "mechanism": "radical_transparency",
        "status": "proposed",
        "risk": "white",
        "idea": "changelog 不是通知栏是内容资产：每条写'为什么做'，月更 2-4 次成节奏——"
                "Linear 无博客轰炸靠 changelog 成为设计圈标杆（月均约 3 次）。",
        "steps": [
            "公开 changelog 页+每条附一行决策理由；每条配一个极简运行示例",
            "稳定月更 3 次左右；对订阅者是'产品活着且在变好'的持续证明",
        ],
        "quota": {"monthly_min": 2, "monthly_cap": 4},
        "evidence": ["Linear 192 条 changelog 分析：月均 3 次、每条解释 why"],
        "caution": "公开事故复盘（postmortem）是同族重武器：详细披露的净收益为正——不披露的流失更狠。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 365,
    },
    "mcp-registry-listing": {
        "name": "MCP 双上架与 Agent 清单位",
        "channel": "agent",
        "mechanism": "agent_surfaces",
        "status": "usable",
        "risk": "white",
        "idea": "官方 MCP Registry 是下游目录的供给方（一次发布多处出现），Glama 收录约 "
                "12 万 server——被装进 Claude/Cursor 的配置就是常驻分发；评估口径是"
                "安装数不是 UV。",
        "steps": [
            "package.json 加 mcpName → npm publish → mcp-publisher init 发布到官方 Registry",
            "Glama 点 Add Server（合计<1 小时）；描述与 README 保持跨目录一致",
            "向 awesome-mcp-servers/awesome-claude-skills 提 PR；维护双 manifest"
            "（.claude-plugin/ 与 .github/）装进 agent 默认搜索路径",
            "mcp.so 类长尾目录可选（$39 快速位仅作占名）",
        ],
        "quota": {"total_min": 2},
        "verdict_metrics": ["installs", "registry_search_rank", "npm_weekly_downloads"],
        "evidence": ["mcpfinder 目录现状（2026-11：Smithery 已停）；Glama 约 12 万 server、月访约 8.2 万"],
        "caution": "Smithery 基础设施已死别浪费时间；生态窗口短（多数<2 年）但上架动作极便宜。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "product-page-decision-layer": {
        "name": "产品页决定层",
        "channel": "web",
        "mechanism": "agent_surfaces",
        "status": "usable",
        "risk": "white",
        "idea": "AI 助手引用的是产品页不是博客：产品/服务页占 ChatGPT 引用约 41%、"
                "Perplexity 约 45%——产品页上有没有机器可读的'是什么/适合谁/怎么接入'"
                "直接决定被转述率。",
        "steps": [
            "一句话定义（供 agent 转述）+ 结构化能力清单（schema 标注）+ 无登录试用入口",
            "内容按引用源三形态分发：LinkedIn 长文（跨平台最稳引用源）+ 视频演示 + 产品页",
            "每周用固定 prompt 组查 AI 引擎是否点名（Reddit 在 ChatGPT 端引用已从 60% 崩到 10%）",
        ],
        "quota": {"total_min": 1},
        "verdict_metrics": ["ai_citations", "agent_crawler_hits"],
        "evidence": ["Semrush 1 亿引用研究（产品页 41%/45%、引用格局剧变）；Yext 680 万引用：86% 来自品牌级页面"],
        "caution": "不要投入 llms.txt（已实测无效，见红线）；引用监测用 Semrush 免费工具即可。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 90,
    },
    "agent-ready-api": {
        "name": "Agent 可试用性",
        "channel": "产品",
        "mechanism": "agent_surfaces",
        "status": "proposed",
        "risk": "white",
        "idea": "'产品能不能被 agent 试用'成为新的'能不能被爬'：公开 API+无登录 demo+MCP "
                "接口=2005 年的可爬取站点——agent 能发现、理解、操作、支付你，才会被推荐。",
        "steps": [
            "文档页加 agent 视角区块：机器可读能力清单、无需注册的 demo 端点",
            "AGENTS.md 说明（主流 agent 会主动读）",
            "监控 agent UA 抓取量（GPTBot/ClaudeBot 等）与 demo 调用量",
        ],
        "quota": {},
        "evidence": ["Stripe agent-ready 指南；AgentReady 开放标准（早期共识，方向性）"],
        "caution": "早期无大厂确认权重，属'做了不亏'的基础设施。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 120,
    },
    "n8n-template-market": {
        "name": "工作流模板市场",
        "channel": "agent",
        "mechanism": "agent_surfaces",
        "status": "proposed",
        "risk": "white",
        "idea": "n8n 官方模板库已有 9,843 个模板且高度 AI 向：把执行流程做成模板=工作流层"
                "的 app store——用户搜'cold outreach automation'时装模板=装你的依赖。",
        "steps": [
            "把核心闭环做成 3-5 个模板提交官方库；同步开独立 awesome 列表收长尾",
            "模板内引导 API key 申请（模板→付费依赖的转化钩子）",
            "Zapier 侧申请 AI partners 目录",
        ],
        "quota": {"total_min": 3, "total_cap": 5},
        "evidence": ["n8n 模板库 9,843 个；Zapier 9000+ 集成的同构模式"],
        "caution": "模板免费=免费获客漏斗，但需维护与平台版本兼容。",
        "evidence_date": "2026-11-14",
        "revalidate_days": 120,
    },
}

# ---------------------------------------------------------------------------
# 已证伪的做法：证据齐全的死路，任何战术执行都不得触碰。
# ---------------------------------------------------------------------------
FORBIDDEN: List[Dict[str, str]] = [
    {"id": "buy-audience", "name": "买粉买量",
     "why": "粉丝清零+长期限流；自家买星事故：92+42+13 颗星全部污染归因",
     "evidence": "自家事故复盘 + 语料多篇证伪"},
    {"id": "scripted-engagement", "name": "脚本自动互动/批量自动回复",
     "why": "X 明确反对自动化，批量回复是封号路径；平台已清理百万级托管账号",
     "evidence": "语料（xiaohongshu）：'用 AI 批量发帖是封号最快的方式'"},
    {"id": "ai-hosting", "name": "AI 全托管账号",
     "why": "平台批量处置托管账号；AI 生成内容必须显式标识后人工把关",
     "evidence": "语料：120 万个托管账号被处置"},
    {"id": "contact-in-comments", "name": "评论区/私信直接留联系方式",
     "why": "新规后按行为判罚不按字面判罚，谐音缩写藏图全部识别，关联账号连坐",
     "evidence": "语料（xiaohongshu）2026-10-18 新规解读多篇"},
    {"id": "same-content-carpet", "name": "同一段内容原样铺多个问题/平台",
     "why": "知乎会清空全部回答；各平台查重判搬运",
     "evidence": "语料（zhihu）运营攻略明确警告"},
    {"id": "vote-solicitation", "name": "拉票/互赞环",
     "why": "社区用高精度识别协同投票，短暂登顶后按垃圾处理",
     "evidence": "HN/PH/Reddit 社区案例多篇"},
    {"id": "cold-dm-blast", "name": "群发模板私信",
     "why": "回复率约 2% 且是举报素材；先在公开场合互动养熟再私信",
     "evidence": "Teract：冷私信 2% vs 互动养熟后 18%"},
    {"id": "engagement-pods", "name": "互动小组（pods/协同放大）",
     "why": "X 排名系统已明确检测并惩罚 pods、购买互动、协同放大——早期互动信号"
            "异常是最显眼的红旗，恰好打在战术最依赖的测试窗口上",
     "evidence": "2024-25 多轮学术审计（120 个 sock-puppet、6 周推荐流审计）施压平台收紧"},
    {"id": "fake-scarcity", "name": "表演性稀缺（无真实约束的人为稀缺）",
     "why": "稀缺剧本的杠杆全部抵押在'叙事真实性'上：被用户识破即信任崩塌"
            "（OnePlus 邀请制被斥为 cynical attempt，次年放弃）",
     "evidence": "Robinhood/Monzo/Superhuman（有真实理由）成功 vs OnePlus（无理由）反噬的对照"},
    {"id": "llms-txt-aeo", "name": "llms.txt 作为 AI 可见性手段",
     "why": "大样本实测（94,614 个 URL）显示 llms.txt 与 AI 引用频率零相关，约 97% "
            "的 llms.txt 未收到任何 AI 爬虫使用；无主流 AI 助手确认读取",
     "evidence": "ALLMO.ai 94,614 URL 研究；CueScout 537 SaaS 站测试；Google 官方表态几乎无用"},
    {"id": "gpt-store-as-revenue", "name": "把 GPT Store 当收入渠道",
     "why": "分布极端头重脚轻：约 90% 的 GPT 不到 100 次对话，创作者多数月入 "
            "$100-500 封顶——它只配当零成本漏斗顶部，不配当收入预期",
     "evidence": "Su et al. 2025（ACM，75.5 万 GPT 追踪）；Wired 2024-10 开发者报道"},
]

_ALLOWED_STATUS = {"usable", "proposed"}
_ALLOWED_RISK = set(RISK_ORDER)


def spec(tactic_id: str) -> Optional[Dict[str, Any]]:
    t = TACTICS.get(tactic_id)
    return {"id": tactic_id, **t} if t else None


def suggest(channel: str = "", mechanism: str = "", risk_max: str = "gray",
            status: str = "", include_stale: bool = False,
            today: Optional[str] = None) -> List[Dict[str, Any]]:
    """按渠道/机制/风险上限/状态筛选战术。默认放开到全部风险，白帽优先排序。

    衰减定律（研究 II）：机会随采用人数衰减，证据过期（超过 revalidate_days）
    的战术默认不进建议清单——先复核证据再用；include_stale=True 放开。
    """
    if risk_max not in _ALLOWED_RISK:
        raise ValueError(f"risk_max 必须是 {'/'.join(RISK_ORDER)} 之一: {risk_max}")
    ceiling = RISK_ORDER.index(risk_max)
    stale_ids = set() if include_stale else {x["id"] for x in stale(today)}
    out = []
    for tid, t in TACTICS.items():
        if tid in stale_ids:
            continue
        if channel and t["channel"] != channel:
            continue
        if mechanism and t["mechanism"] != mechanism:
            continue
        if RISK_ORDER.index(t["risk"]) > ceiling:
            continue
        if status and t["status"] != status:
            continue
        out.append({"id": tid, **t})
    out.sort(key=lambda x: (RISK_ORDER.index(x["risk"]), x["channel"], x["id"]))
    return out


# ---------------------------------------------------------------------------
# 战术组合推荐：把注册表变成"定策略"的输入。
# 规则透明可解释：每条入选理由都写明（目标族/渠道匹配/阶段/成熟度），人可复核。
# ---------------------------------------------------------------------------
# 不绑定具体平台的 channel：任何产品都能用
GENERIC_CHANNELS = {"通用", "产品", "内容", "web", "销售", "agent", "搜索", "付费", "线下", "marketplaces"}

# 目标族 → 优先机制面（awareness 获客 / adoption 使用与放大 / commercial 商业）
FAMILY_MECHANISMS = {
    "awareness": {"trust_nodes", "borrowed_audience", "search_seat", "official_tap"},
    "adoption": {"product_embedded", "compound_loop", "proven_demand", "rule_window"},
    "commercial": {"competitor_events", "paid_amplify", "value_exchange", "alliance_bundle"},
}

# 阶段 → 加分机制面（冷启动先建信任；放量期靠结构与付费杠杆）
PHASE_MECHANISMS = {
    "cold_start": {"trust_nodes", "borrowed_audience", "proven_demand"},
    "scale": {"compound_loop", "paid_amplify", "volume_probability", "own_the_venue"},
}


def recommend(facts: Dict[str, Any], goal_id: str = "", *, channels: Optional[List[str]] = None,
              phase: str = "cold_start", risk_max: str = "white",
              max_picks: int = 6, include_stale: bool = False,
              today: Optional[str] = None) -> Dict[str, Any]:
    """按产品事实包（可选目标）选一个战术组合，返回入选理由与完整战术参数。

    打分（透明规则）：
      +3 机制命中目标族优先面（目标决定打法方向，权重最高）
      +2 status=usable（证据与执行参数成熟）
      +2 渠道匹配（已有账号平台）或通用渠道
      +1 机制命中阶段面
    机制多样性：同一机制最多入选 2 条；过期证据默认排除（衰减定律）。
    """
    if phase not in PHASE_MECHANISMS:
        raise ValueError(f"phase 应为 {'/'.join(PHASE_MECHANISMS)} 之一: {phase}")
    if risk_max not in _ALLOWED_RISK:
        raise ValueError(f"risk_max 必须是 {'/'.join(RISK_ORDER)} 之一: {risk_max}")

    # 目标族：优先从 goals 注册表取，取不到按 awareness 处理（增长初期默认）
    family = "awareness"
    goal_note = ""
    if goal_id:
        try:
            from .goals import get as goal_get
            family = goal_get(goal_id).get("family", family)
        except Exception as exc:  # 目标库不可用时降级并如实说明
            goal_note = f"目标 {goal_id} 读取失败（{type(exc).__name__}），按 awareness 处理"
    family_set = FAMILY_MECHANISMS.get(family, FAMILY_MECHANISMS["awareness"])
    phase_set = PHASE_MECHANISMS[phase]

    owned = set((facts.get("owned_assets") or {}).get("platforms") or [])
    wanted_channels = set(channels) if channels is not None else owned
    if type(max_picks) is not int or not 0 <= max_picks <= 50:
        raise ValueError("max_picks 必须是 0-50 的整数")
    ceiling = RISK_ORDER.index(risk_max)
    stale_ids = set() if include_stale else {x["id"] for x in stale(today)}

    scored = []
    for tid, t in TACTICS.items():
        if tid in stale_ids or RISK_ORDER.index(t["risk"]) > ceiling:
            continue
        reasons = []
        score = 0
        if t["mechanism"] in family_set:
            score += 3
            reasons.append(f"命中{family}目标的优先机制：{MECHANISMS[t['mechanism']]['name']}")
        if t["status"] == "usable":
            score += 2
            reasons.append("执行参数与证据成熟（usable）")
        if t["channel"] in GENERIC_CHANNELS or t["channel"] in wanted_channels:
            score += 2
            reasons.append(f"渠道可用（{t['channel']}）")
        elif wanted_channels:
            continue  # 有明确渠道范围时，范围外平台战术不入选
        if t["mechanism"] in phase_set:
            score += 1
            reasons.append(f"适配{phase}阶段")
        scored.append((score, tid, t, reasons))

    scored.sort(key=lambda x: (-x[0], x[1]))
    picks: List[Dict[str, Any]] = []
    mech_count: Dict[str, int] = {}
    for score, tid, t, reasons in scored:
        if len(picks) >= max_picks:
            break
        m = t["mechanism"]
        if mech_count.get(m, 0) >= 2:
            continue  # 机制多样性：同机制最多 2 条
        mech_count[m] = mech_count.get(m, 0) + 1
        picks.append({
            "id": tid, "name": t["name"], "mechanism": m,
            "mechanism_name": MECHANISMS[m]["name"],
            "channel": t["channel"], "status": t["status"], "risk": t["risk"],
            "idea": t["idea"], "steps": t["steps"],
            "quota": t.get("quota") or {}, "caution": t["caution"],
            "score": score, "why": reasons,
        })
    notes = [goal_note] if goal_note else []
    if not picks:
        notes.append("没有同时满足渠道与风险约束的战术：放宽 --risk-max 或 --include-stale")
    return {"family": family, "phase": phase, "risk_max": risk_max,
            "picks": picks, "notes": notes}


def stale(today: Optional[str] = None) -> List[Dict[str, Any]]:
    """证据到期未复核的战术（机会随采用人数衰减，过期战术先复核再使用）。"""
    today = today or date.today().isoformat()
    out = []
    for tid, t in TACTICS.items():
        days = (date.fromisoformat(today) - date.fromisoformat(t["evidence_date"])).days
        if days > t["revalidate_days"]:
            out.append({"id": tid, "name": t["name"],
                        "evidence_date": t["evidence_date"],
                        "revalidate_days": t["revalidate_days"], "days_since": days})
    return out


def lint() -> List[str]:
    """注册表不变量；空列表=通过。测试与 mkt tactic lint 共用。"""
    errs: List[str] = []
    for tid, t in TACTICS.items():
        if not tid or not tid.replace("-", "a").replace("_", "a").isalnum():
            errs.append(f"{tid}: id 应为小写连字符")
        for field in ("name", "channel", "mechanism", "status", "risk",
                      "idea", "steps", "evidence", "evidence_date", "revalidate_days"):
            if not t.get(field):
                errs.append(f"{tid}: 缺字段 {field}")
        if t.get("mechanism") not in MECHANISMS:
            errs.append(f"{tid}: 未知机制 {t.get('mechanism')!r}")
        if t.get("status") not in _ALLOWED_STATUS:
            errs.append(f"{tid}: status 应为 usable/proposed")
        if t.get("risk") not in _ALLOWED_RISK:
            errs.append(f"{tid}: risk 应为 {'/'.join(RISK_ORDER)}")
        if t.get("status") == "usable":
            for field in ("quota", "verdict_metrics", "caution"):
                if not t.get(field):
                    errs.append(f"{tid}: usable 战术必须有 {field}")
        q = t.get("quota") or {}
        if q.get("daily_min") is not None and q.get("daily_cap") is not None \
                and q["daily_min"] > q["daily_cap"]:
            errs.append(f"{tid}: daily_min 大于 daily_cap")
        try:
            date.fromisoformat(str(t.get("evidence_date")))
        except (TypeError, ValueError):
            errs.append(f"{tid}: evidence_date 应为 YYYY-MM-DD")
        if not isinstance(t.get("revalidate_days"), int) or t["revalidate_days"] <= 0:
            errs.append(f"{tid}: revalidate_days 应为正整数")
    seen = set()
    for f in FORBIDDEN:
        for field in ("id", "name", "why", "evidence"):
            if not f.get(field):
                errs.append(f"FORBIDDEN {f.get('id')}: 缺字段 {field}")
        if f.get("id") in seen:
            errs.append(f"FORBIDDEN id 重复: {f.get('id')}")
        seen.add(f.get("id"))
    return errs


# 机制族 → 研究轮次出处（总表与研究报告的对照索引）
MECHANISM_ROUNDS = {
    "borrowed_audience": "I", "proven_demand": "I", "search_seat": "I",
    "official_tap": "I", "volume_probability": "I", "rule_window": "I", "story_pull": "I",
    "trust_nodes": "II", "sequence_window": "II", "compound_loop": "II",
    "product_embedded": "III", "value_exchange": "III",
    "paid_amplify": "III", "competitor_events": "III",
    "own_the_venue": "IV", "alliance_bundle": "IV", "event_waves": "IV",
    "radical_transparency": "IV", "agent_surfaces": "IV",
}


def render_index() -> str:
    """按机制族渲染手册总表（markdown），供 docs/playbooks/战术/ 落盘。"""
    lines = [
        "# 非常规增长战术总表",
        "",
        "> 结构化数据在 `core/tactics.py`（TACTICS 注册表）。这里是人读版，"
        "两边靠 `mkt tactic lint` 与本表生成函数对齐。",
        "> 来源综述：2026-11-14 经验语料库（约 620 篇）+ 四轮外部研究"
        "（I 平台战术 / II 目标选择与次序 / III 产品内生与价值交换 / IV 自建场地与 Agent 位）。",
        "",
        "| 轮 | 机制族 |",
        "|---|---|",
    ]
    for mid, m in MECHANISMS.items():
        lines.append(f"| {MECHANISM_ROUNDS.get(mid, '?')} | {m['name']}（{mid}） |")
    lines.append("")
    for mid, m in MECHANISMS.items():
        lines += [f"## {m['name']}（研究 {MECHANISM_ROUNDS.get(mid, '?')}）", "",
                  f"原理：{m['principle']}", "",
                  f"开工前先问：{m['ask']}", ""]
        for tid, t in TACTICS.items():
            if t["mechanism"] != mid:
                continue
            risk = RISK_GRADES[t["risk"]].split("：", 1)[0]
            lines.append(f"- **{t['name']}**（`{tid}`｜{t['channel']}｜{risk}｜{t['status']}）：{t['idea']}")
        lines.append("")
    lines += ["## 已证伪，不要做", ""]
    for f in FORBIDDEN:
        lines.append(f"- **{f['name']}**：{f['why']}（{f['evidence']}）")
    lines += [
        "",
        "风险分档：",
        "",
    ]
    for key in RISK_ORDER:
        lines.append(f"- {RISK_GRADES[key]}")
    lines.append("")
    return "\n".join(lines)
