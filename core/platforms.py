#!/usr/bin/env python3
"""平台知识库：全链路唯一的平台数据源。

以前每个环节各维护一份清单——诊断有 16 个、策略有 11 个、执行只有 5 个、日志只认 11 个，
所以"支持哪些平台"永远对不上。现在统一在这里：

    gtm       用它做平台排位
    strategy  用它取账号要求、冷启动阶段、放大条件、风控映射、内容档位
    execute   用它判断这条渠道能不能自动执行（write），不能就出人工执行单
    logscan   用它识别日志里提到了哪个平台（aliases）
    cadence   用它决定第一周要不要养号

新增平台只改这一个文件。手册在 docs/playbooks/，本文件只放可执行参数。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 渠道类型决定默认打法；单个平台可以用 OVERRIDES 覆盖
# ---------------------------------------------------------------------------
KIND_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "社区": {
        "account": ["一个有历史的真实账号", "先读社区规则与置顶"],
        "residency": 14,
        "tier": "A 人写人发",
        "stages": [("有资格", "内容能正常发布、不被直接过滤", 0),
                   ("被看见", "帖子有基础曝光或 ≥10 赞同", 14),
                   ("被问到", "≥3 条实质回复或追问", 28),
                   ("被用上", "有人真的照着做", 45),
                   ("被付费", "≥1 笔", 90)],
        "scale": {"signal": "连续 2 周有「被问到」",
                  "how": "加人（更多社区或更大池子），不加频率"},
        "risks": [("版规判定", "帖子被移走 / 账号被警告", "回到纯分享，去掉一切 CTA"),
                  ("刷量检测", "互动数与曝光明显不匹配", "停一切互赞互评")],
    },
    "社媒": {
        "account": ["有历史的个人号", "不需要买粉"],
        "residency": 14,
        "tier": "B 人改写 + 披露",
        "stages": [("有资格", "帖子有非零曝光", 0),
                   ("被看见", "单帖曝光 500+", 14),
                   ("被问到", "≥3 条私信或追问", 28),
                   ("被用上", "有人点进主页或注册", 45),
                   ("被付费", "≥1 笔", 90)],
        "scale": {"signal": "连续 2 周主页点击或私信 ≥5/周",
                  "how": "加人（更多具体对象），绝不加频率"},
        "risks": [("反自动化", "登录验证 / 发帖被限", "停自动化，只保留人工发"),
                  ("平台操纵", "多号协同互动", "只用主号")],
    },
    "内容": {
        "account": ["实名账号", "主页能承接（简介/置顶/入口）"],
        "residency": 14,
        "tier": "A 人写人发",
        "stages": [("有资格", "内容不被限流", 0),
                   ("被看见", "单篇曝光 1000+", 14),
                   ("被问到", "评论区 ≥3 条问怎么用", 28),
                   ("被用上", "有人真的用上并反馈", 45),
                   ("被付费", "≥1 笔", 90)],
        "scale": {"signal": "评论区稳定出现「怎么下载/怎么用」",
                  "how": "加人（更多同类选题），不做投放"},
        "risks": [("导流检测", "评论或私信被删", "去掉联系方式，走平台内沟通"),
                  ("内容判定", "内容不进推荐", "先做 3 篇纯分享再谈产品")],
    },
    "视频": {
        "account": ["实名账号", "有基础剪辑产能"],
        "residency": 7,
        "tier": "A 人写人发",
        "stages": [("有资格", "视频能过审", 0),
                   ("被看见", "完播率 ≥30% 或播放 1000+", 7),
                   ("被问到", "评论区 ≥3 条问用法", 28),
                   ("被用上", "有人真的用上", 60),
                   ("被付费", "≥1 笔", 120)],
        "scale": {"signal": "单条视频 30 天后还在被搜到",
                  "how": "加人（同题材系列），不追日更"},
        "risks": [("虚假互动", "播放与互动比例异常", "停一切刷量"),
                  ("内容判定", "视频被限流", "降频、提高制作质量")],
    },
    "集中发布": {
        "account": ["账号有基本活跃历史", "物料齐全"],
        "residency": 7,
        "tier": "B 人改写 + 披露",
        "stages": [("有资格", "内容能正常提交", 0),
                   ("被看见", "进榜单或获得曝光", 1),
                   ("被问到", "≥3 条实质评论", 2),
                   ("被用上", "≥5 个真实注册", 7),
                   ("被付费", "≥1 笔", 30)],
        "scale": {"signal": "集中发布带来真实讨论，而不只是票数",
                  "how": "不追加投放；把流量导进邮件列表"},
        "risks": [("拉票检测", "票数曲线异常", "停止一切求票，只回评"),
                  ("关联账号", "多号集中投票", "只用本人号")],
    },
    "直连": {
        "account": ["自有域名或账号", "退信/送达率正常"],
        "residency": 0,
        "tier": "C AI 生成 + 人工改写",
        "stages": [("有资格", "能进收件箱或正常投递", 0),
                   ("被看见", "打开率 ≥30%", 7),
                   ("被问到", "≥3 条实质回信", 14),
                   ("被用上", "有人约具体场景", 30),
                   ("被付费", "≥1 笔", 60)],
        "scale": {"signal": "连续 2 周回信率 ≥5%",
                  "how": "加人（更多合格对象），不群发"},
        "risks": [("退信率", ">5% 退信", "立刻停，清洗名单"),
                  ("频率", "同域大量发送", "同域 ≤5/日")],
    },
    "搜索": {
        "account": ["自有站点或文档站", "能被索引"],
        "residency": 0,
        "tier": "C AI 生成 + 人工改写",
        "stages": [("有资格", "页面能被索引", 0),
                   ("被看见", "出现曝光与点击", 90),
                   ("被问到", "有人从搜索进到安装页", 120),
                   ("被用上", "有人真的装上", 180),
                   ("被付费", "≥1 笔", 365)],
        "scale": {"signal": "某个词连续 4 周稳定带来点击",
                  "how": "加人（更多问题页），不做批量生成"},
        "risks": [("批量内容", "页面不被索引或排名骤降", "停批量，只写有独立价值的页"),
                  ("站点处罚", "Search Console 出现手动操作", "删低质页并复审")],
    },
    "目录": {
        "account": ["产品网站与定价页就绪", "企业账号"],
        "residency": 0,
        "tier": "B 人改写 + 披露",
        "stages": [("有资格", "产品页通过收录", 0),
                   ("被看见", "产品页有浏览", 30),
                   ("被问到", "有客户留言或提问", 60),
                   ("被用上", "从目录来的注册", 90),
                   ("被付费", "≥1 笔", 180)],
        "scale": {"signal": "从目录带来可归因注册",
                  "how": "加人（更多目录站），不买排名"},
        "risks": [("评价操纵", "评价被删", "只请真实客户留言"),
                  ("付费位", "排名靠钱不靠评价", "不买排名")],
    },
}

# 单个平台的覆盖（已写手册的那批，参数更具体）
OVERRIDES: Dict[str, Dict[str, Any]] = {
 "github": {
  "account": [
   "一个真实个人身份（不是产品官方号）",
   "有历史 PR 记录",
   "配好 MKT_GITHUB_TOKEN（匿名 60 次/时不够）"
  ],
  "residency": 0,
  "stages": [
   [
    "有资格",
    "能正常提 issue/PR，不被判 spam",
    0
   ],
   [
    "被看见",
    "≥3 个提交被维护者回应（不一定是合并）",
    14
   ],
   [
    "被问到",
    "≥2 个维护者主动追问细节",
    28
   ],
   [
    "被用上",
    "≥1 个合并或采纳",
    45
   ],
   [
    "被付费",
    "≥1 个从仓库走来的付费委托",
    90
   ]
  ],
  "scale": {
   "signal": "连续 2 周每周 ≥2 次合并",
   "how": "加人（更多上游仓库），不提高单仓频率"
  },
  "risks": [
   [
    "滥用模式检测",
    "issue 被关且无回复 / 账号受限",
    "回到只做有最小复现的提交"
   ],
   [
    "维护者举报",
    "提交被标记 spam",
    "停一周，只做公开答疑"
   ],
   [
    "速率限制 403",
    "API 返回 403",
    "退到 60 次/时，或配 token"
   ]
  ],
  "tier": "B 人改写 + 披露"
 },
 "hackernews": {
  "account": [
   "注册 ≥3 个月的号（新号 Show HN 过不了）",
   "karma 有积累",
   "账号本人持有"
  ],
  "residency": 21,
  "stages": [
   [
    "有资格",
    "账号能正常发帖、不被判 dead",
    0
   ],
   [
    "被看见",
    "帖子进前 30 或 100+ 分",
    1
   ],
   [
    "被问到",
    "≥5 条实质评论（问用法/原理）",
    2
   ],
   [
    "被用上",
    "评论区有人贴出自己的使用结果",
    7
   ],
   [
    "被付费",
    "从 HN 来的付费对话",
    30
   ]
  ],
  "scale": {
   "signal": "一次集中发布带来 ≥10 条讨论且有人追问",
   "how": "不追加投放；转去承接渠道（GitHub）加人"
  },
  "risks": [
   [
    "genAI 文本分类器",
    "帖子 30 分钟零评论且排名不动",
    "改成人写人发，删掉一切 AI 痕迹"
   ],
   [
    "社区 flag",
    "帖子变灰 / dead",
    "不重发、不换号，去评论区攒信誉 2 周"
   ],
   [
    "投票异常",
    "分数与评论数明显不匹配",
    "停止任何拉票动作"
   ]
  ],
  "tier": "A 人写人发"
 },
 "product-hunt": {
  "account": [
   "maker 账号（关系必须披露）",
   "产品页素材齐全",
   "当天全天在线"
  ],
  "residency": 7,
  "stages": [
   [
    "有资格",
    "产品页可通过审核",
    0
   ],
   [
    "被看见",
    "当日进品类前 10",
    1
   ],
   [
    "被问到",
    "评论里 ≥3 个具体使用场景的问题",
    1
   ],
   [
    "被用上",
    "≥5 个真实注册",
    7
   ],
   [
    "被付费",
    "≥1 笔",
    30
   ]
  ],
  "scale": {
   "signal": "launch 进前 10 且评论质量好",
   "how": "不追加第二次 launch；把流量导进邮件列表"
  },
  "risks": [
   [
    "拉票检测",
    "投票曲线异常",
    "停止一切求票，只回评"
   ],
   [
    "关联账号",
    "多号集中投票",
    "只用本人号，不找朋友代投"
   ]
  ],
  "tier": "B 人改写 + 披露"
 },
 "reddit": {
  "account": [
   "账号 ≥3 个月、karma ≥100",
   "先读目标 sub 的 sidebar 与置顶"
  ],
  "residency": 28,
  "stages": [
   [
    "有资格",
    "帖子不被 AutoMod 直接过滤",
    0
   ],
   [
    "被看见",
    "帖子有 ≥10 upvote",
    14
   ],
   [
    "被问到",
    "≥3 条实质回复",
    28
   ],
   [
    "被用上",
    "有人贴出自己的使用",
    45
   ],
   [
    "被付费",
    "≥1 笔",
    90
   ]
  ],
  "scale": {
   "signal": "某 sub 连续 2 周回应率 ≥10%",
   "how": "加人（扩到第 5–8 个 sub），不提高单 sub 频率"
  },
  "risks": [
   [
    "AI 内容判定",
    "帖子秒删 / 被 AutoMod 打回",
    "改人写；披露身份"
   ],
   [
    "低 karma 门槛",
    "帖子无曝光且无任何反馈",
    "只评论不发主贴，养 4 周"
   ],
   [
    "shadowban",
    "别人看不到你的回复",
    "停止该 sub 一切动作，申诉后重建"
   ]
  ],
  "tier": "A 人写人发"
 },
 "x": {
  "account": [
   "有历史的个人号",
   "不需要买粉"
  ],
  "residency": 14,
  "stages": [
   [
    "有资格",
    "帖子有基础曝光（非零）",
    0
   ],
   [
    "被看见",
    "单帖曝光 500+",
    14
   ],
   [
    "被问到",
    "≥3 条私信或追问",
    28
   ],
   [
    "被用上",
    "有人点进仓库并 star",
    45
   ],
   [
    "被付费",
    "≥1 笔",
    90
   ]
  ],
  "scale": {
   "signal": "连续 2 周主页点击 ≥50/周",
   "how": "加人（更多具体对象），绝不加频率"
  },
  "risks": [
   [
    "反自动化",
    "登录验证 / 发帖被限",
    "停自动化，只保留人工发布"
   ],
   [
    "平台操纵",
    "多号协同互动",
    "只用主号"
   ],
   [
    "频率异常",
    "帖子零曝光",
    "日更降到 2 条以内"
   ]
  ],
  "tier": "B 人改写 + 披露"
 },
 "email": {
  "account": [
   "自有域名 + SPF/DKIM",
   "退信率 <5%"
  ],
  "residency": 0,
  "stages": [
   [
    "有资格",
    "能进收件箱（非垃圾箱）",
    0
   ],
   [
    "被看见",
    "打开率 ≥30%",
    7
   ],
   [
    "被问到",
    "≥3 条实质回信",
    14
   ],
   [
    "被用上",
    "有人约具体场景",
    30
   ],
   [
    "被付费",
    "≥1 笔",
    60
   ]
  ],
  "scale": {
   "signal": "连续 2 周回信率 ≥5%",
   "how": "加人（更多合格对象），不群发"
  },
  "risks": [
   [
    "退信率",
    ">5% 退信",
    "立刻停，清洗名单"
   ],
   [
    "发送频率",
    "同域大量发送",
    "同域 ≤5/日，分批"
   ],
   [
    "内容判定",
    "被标 spam",
    "改成一封一封写"
   ]
  ],
  "tier": "C AI 生成 + 人工改写"
 },
 "xiaohongshu": {
  "account": [
   "实名号，别用新号发广告",
   "主页简介要能承接"
  ],
  "residency": 14,
  "stages": [
   [
    "有资格",
    "标题能被搜到（进搜索池），不是「没被限流」",
    0
   ],
   [
    "被看见",
    "曝光≥自己 30 天中位数，且点击率不低于自己均值",
    14
   ],
   [
    "被问到",
    "评论区 ≥5 条问怎么用",
    28
   ],
   [
    "被用上",
    "有人真的用上",
    45
   ],
   [
    "被付费",
    "≥1 笔",
    90
   ]
  ],
  "scale": {
   "signal": "评论区稳定出现「怎么下载」",
   "how": "加人（更多同类选题），不做投放"
  },
  "risks": [
   [
    "导流检测",
    "评论/私信被删",
    "去掉一切联系方式，走平台内沟通"
   ],
   [
    "AI 内容标识",
    "笔记被限流",
    "人写，AI 只做选题"
   ],
   [
    "营销判定",
    "笔记不进推荐",
    "先做 3 篇纯分享再谈产品"
   ]
  ],
  "tier": "A 人写人发"
 },
 "zhihu": {
  "account": [
   "个人号，领域聚焦",
   "前 10 个回答集中一个领域"
  ],
  "residency": 7,
  "stages": [
   [
    "有资格",
    "回答不被折叠",
    0
   ],
   [
    "被看见",
    "单篇阅读 1000+",
    21
   ],
   [
    "被问到",
    "≥3 条具体咨询",
    45
   ],
   [
    "被用上",
    "有人拿去用并反馈",
    90
   ],
   [
    "被付费",
    "≥1 笔",
    120
   ]
  ],
  "scale": {
   "signal": "单篇回答月均阅读稳定 + 出现私信咨询",
   "how": "加人（相邻问题），不发新贴"
  },
  "risks": [
   [
    "营销号专项",
    "回答被删 / 账号受限",
    "去产品化，只答问题"
   ],
   [
    "导流判定",
    "简介或正文被处理",
    "不放联系方式"
   ]
  ],
  "tier": "B 人改写 + 披露"
 },
 "juejin": {
  "account": [
   "技术号",
   "垂直领域发文"
  ],
  "residency": 7,
  "stages": [
   [
    "有资格",
    "文章能过审",
    0
   ],
   [
    "被看见",
    "进首页推荐",
    7
   ],
   [
    "被问到",
    "评论区 ≥3 个技术问题",
    21
   ],
   [
    "被用上",
    "有人照着做并反馈",
    45
   ],
   [
    "被付费",
    "≥1 笔",
    120
   ]
  ],
  "scale": {
   "signal": "垂直发文数据累积明显",
   "how": "加人（提频），保持质量"
  },
  "risks": [
   [
    "刷赞检测",
    "互动异常",
    "停一切互赞"
   ],
   [
    "搬运判定",
    "文章被下架",
    "只发原创实践"
   ]
  ],
  "tier": "B 人改写 + 披露"
 },
 "v2ex": {
  "account": [
   "有历史的号（新号发推广会被喷）",
   "先参与两周技术讨论"
  ],
  "residency": 14,
  "stages": [
   [
    "有资格",
    "能在分享创造节点发帖",
    0
   ],
   [
    "被看见",
    "帖子 48h 内有 ≥1000 浏览",
    2
   ],
   [
    "被问到",
    "≥3 条回帖问细节",
    7
   ],
   [
    "被用上",
    "有人真的装了",
    30
   ],
   [
    "被付费",
    "≥1 笔",
    90
   ]
  ],
  "scale": {
   "signal": "一次发布拿到有效反馈",
   "how": "重大更新再发一次，不连续发"
  },
  "risks": [
   [
    "广告判定",
    "帖子被移走 / 账号被警告",
    "改成纯分享，去掉 CTA"
   ],
   [
    "批量注册",
    "账号被关联",
    "只用主号"
   ]
  ],
  "tier": "A 人写人发"
 },
 "indie-hackers": {
  "account": [
   "真实身份",
   "先评论别人的帖子"
  ],
  "residency": 7,
  "stages": [
   [
    "有资格",
    "帖子能正常展示",
    0
   ],
   [
    "被看见",
    "帖子有 ≥5 条回复",
    7
   ],
   [
    "被问到",
    "≥3 个具体问题",
    21
   ],
   [
    "被用上",
    "有人注册",
    45
   ],
   [
    "被付费",
    "≥1 笔",
    90
   ]
  ],
  "scale": {
   "signal": "复盘帖稳定有同行互动",
   "how": "月更制度化，不日更"
  },
  "risks": [
   [
    "推广疲劳",
    "帖子零互动",
    "只发带数字的复盘"
   ],
   [
    "回音室",
    "只有同行没有客户",
    "换渠道，不加大"
   ]
  ],
  "tier": "B 人改写 + 披露"
 }
}

_NEW = [
    {"id": "x", "name": "X", "kind": "社媒",
     "aliases": ("X ", "Twitter", "推特", "推文", "tweet", "冷触达"),
     "icp_tags": ["developers", "technical-founders", "indie-makers"], "product_types": ["devtool", "oss", "saas", "product"],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "针对具体人的深度技术对话", "label": "self_ab", "evidence": "深度技术对话有真实回应；小样本不外推"},
                 {"motion": "模板化冷回复 / 批量 @", "label": "self_incident", "evidence": "模板化冷触达效率低，禁止批量骚扰", "killed": True}],
     "cold_start": {"checklist": ["有历史的个人号", "先评论三周再发原创"],
                    "expected": "深对话偶发有效；模板化接近 0 [自家实测]"},
     "scale_gate": "连续 2 周主页点击或私信 ≥5/周 才加量",
     "risk": "中（反自动化与频率限流，见 docs/risk）"},
    {"id": "email", "name": "邮件 / 一对一外联", "kind": "直连",
     "aliases": ("邮件", "Gmail", "邮箱", "冷邮件", "发信", "求评"),
     "icp_tags": ["developers", "technical-founders", "b2b-buyers"], "product_types": ["devtool", "oss", "saas", "b2b-saas"],
     "channel_status": "PORTED",
     "motions": [{"motion": "求评式 1:1（把对方放专家位，钩子用他公开的数字）", "label": "self_ab", "evidence": "一对一求评的候选做法；效果以自有实测为准"},
                 {"motion": "批量群发 / 冷邮件报价", "label": "self_incident", "evidence": "批量冷邮件与无差别报价已有多起失败记录", "killed": True}],
     "cold_start": {"checklist": ["自有域名 + SPF/DKIM", "一封一封写，不群发"],
                    "expected": "求评式约 5% 回信；批量接近 0 [自家实测]"},
     "scale_gate": "连续 2 周回信率 ≥5% 才加人",
     "risk": "中（退信率 >5% 立即停，见 docs/risk）"},
    {"id": "youtube", "name": "YouTube", "kind": "视频",
     "aliases": ('YouTube', '油管'),
     "icp_tags": ['developers', 'consumers'], "product_types": ['devtool', 'saas', 'consumer-app'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "完整使用教程（一个问题一条，10 分钟内）", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "搬运或纯产品宣传片", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "教程长尾以年计；10–20 条出一条能跑的 [行业经验]"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "seo", "name": "Google SEO", "kind": "搜索",
     "aliases": ('SEO', 'Google 搜索', '搜索排名'),
     "icp_tags": ['developers', 'b2b-buyers', 'technical-founders'], "product_types": ['devtool', 'saas', 'oss', 'b2b-saas'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "问题页/对比页/报错页（每页解决一个具体问题）", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "用 AI 批量生成页面冲数量（命中 scaled content abuse）", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "新页 2–8 周起量，新站 3–6 个月 [官方未承诺时间表]"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "newsletter", "name": "Newsletter 投稿", "kind": "直连",
     "aliases": ('newsletter', '邮件刊物', 'TLDR', 'Console', 'Latent Space'),
     "icp_tags": ['developers', 'technical-founders', 'indie-makers'], "product_types": ['devtool', 'saas', 'oss'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "编辑推荐或赞助位（走官网 submit/advertise）", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "给几十家刊物群发同一封投稿信", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "单次曝光 24–48 小时归零，存档页有长尾 [站点原文]"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "directory-sites", "name": "软件目录站", "kind": "目录",
     "aliases": ('G2', 'Capterra', 'AlternativeTo', '目录站'),
     "icp_tags": ['b2b-buyers', 'product-managers'], "product_types": ['b2b-saas', 'saas', 'devtool'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "收录 + 认领 + 真实客户评价", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "买好评或批量注册刷评价", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "收录几周，评价积累数月 [官方口径未核]"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "stack-overflow", "name": "Stack Overflow", "kind": "社区",
     "aliases": ('Stack Overflow', 'SO', 'StackOverflow'),
     "icp_tags": ['developers'], "product_types": ['devtool', 'oss', 'sdk', 'api'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "长期答真问题，账号资料放一次主页链接", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "答案里贴产品链接或自问自答", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "账号信誉 3–6 个月，之后长期带访问"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "discord", "name": "Discord / Slack 社群", "kind": "社区",
     "aliases": ('Discord', 'Slack', '社群', '服务器'),
     "icp_tags": ['developers', 'technical-founders'], "product_types": ['devtool', 'oss', 'saas'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "进群当居民，只答问题；自推频道才提产品", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "进群第一句发链接或私信群成员", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "立刻被踢；被记住要 2–3 个月"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "bluesky", "name": "Bluesky / Mastodon", "kind": "社媒",
     "aliases": ('Bluesky', 'Mastodon', '长毛象'),
     "icp_tags": ['developers', 'technical-founders', 'researchers'], "product_types": ['devtool', 'oss', 'saas'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "build in public 同步、技术短评", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "跨实例批量刷同一条", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "技术圈浓度高但天花板低 [未核]"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "zhishixingqiu", "name": "知识星球", "kind": "直连",
     "aliases": ('知识星球', '星球'),
     "icp_tags": ['indie-makers', 'product-managers'], "product_types": ['saas', 'product', 'consumer-app'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "付费社群沉淀：每周固定更新 + 答疑", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "没有公开关注者就开星球", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "付费即交付责任；续费率是核心 [协议 2026-05-18 版]"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "weibo", "name": "微博", "kind": "社媒",
     "aliases": ('微博', 'Weibo'),
     "icp_tags": ['consumers', 'technical-founders'], "product_types": ['consumer-app', 'product'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "技术热点短评、事件驱动发声", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "买热搜或水军转发", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "热点驱动，长尾弱；工具类默认不做"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "douban", "name": "豆瓣", "kind": "社区",
     "aliases": ('豆瓣', '小组'),
     "icp_tags": ['consumers', 'designers'], "product_types": ['consumer-app', 'product'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "相关小组内的真实分享", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "小组内发广告", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "仅当产品与书影音强相关；死群多"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "tieba", "name": "百度贴吧", "kind": "社区",
     "aliases": ('贴吧', '百度贴吧'),
     "icp_tags": ['consumers'], "product_types": ['consumer-app', 'product'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "特定兴趣吧的长期参与", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "吧内发广告（秒删）", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "生态老化，独立产品性价比低"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "36kr", "name": "36氪 / 虎嗅", "kind": "直连",
     "aliases": ('36氪', '虎嗅', '科技媒体'),
     "icp_tags": ['b2b-buyers', 'technical-founders'], "product_types": ['b2b-saas', 'saas', 'product'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "融资或里程碑节点的媒体投稿", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "软文投放冒充报道", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "背书价值大于流量；日常获客价值低"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "csdn", "name": "CSDN", "kind": "社区",
     "aliases": ('CSDN',),
     "icp_tags": ['developers', 'students'], "product_types": ['devtool', 'oss', 'sdk'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "原创实践长文为主", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "搬运、拼凑、洗稿", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "技术流量大但转化弱 [未核]"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "maimai", "name": "脉脉", "kind": "社媒",
     "aliases": ('脉脉',),
     "icp_tags": ['b2b-buyers', 'product-managers'], "product_types": ['b2b-saas', 'saas'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "行业话题下的专业发言", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "匿名区爆料式营销", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "职场语境，B2B 有人群 [未核]"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "taobao", "name": "淘宝 / 天猫", "kind": "目录",
     "aliases": ('淘宝', '天猫', '电商'),
     "icp_tags": ['consumers'], "product_types": ['product', 'hardware', 'consumer-app'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "电商主图+详情页+付费推广", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "刷单或虚假评价", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "平台规则严；适合实物或数字商品"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "kuaishou", "name": "快手", "kind": "视频",
     "aliases": ('快手',),
     "icp_tags": ['consumers'], "product_types": ['consumer-app', 'product'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "下沉市场的生活场景演示", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "搬运或刷量", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "与抖音人群不同，工具类基本不适用"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "tiktok", "name": "TikTok", "kind": "视频",
     "aliases": ('TikTok',),
     "icp_tags": ['consumers'], "product_types": ['consumer-app', 'product'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "效果型视觉钩子（前 3 秒）", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "买量或虚假互动", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "消费级可选，工具类不做"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "jike", "name": "即刻", "kind": "社区",
     "aliases": ('即刻',),
     "icp_tags": ['indie-makers', 'technical-founders'], "product_types": ['saas', 'product', 'devtool'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "产品圈层里的真实分享与互动", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "一上来就发产品链接", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "圈子小但质量高；三个月才被记住"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
    {"id": "sspai", "name": "少数派", "kind": "内容",
     "aliases": ('少数派',),
     "icp_tags": ['designers', 'product-managers', 'indie-makers'], "product_types": ['saas', 'product', 'hardware'],
     "channel_status": "UNVERIFIED",
     "motions": [{"motion": "投稿深度评测与工作流文章", "label": "external_baseline", "evidence": "见 docs/playbooks 对应手册"},
                 {"motion": "自己发推广文（走编辑制）", "label": "external_baseline", "evidence": "手册 §5 死路", "killed": True}],
     "cold_start": {"checklist": ["读平台规则与置顶", "准备第一个能用的东西"], "expected": "编辑审核制；一年 2–3 篇即可"},
     "scale_gate": "连续 2 周有「被问到」才加投入",
     "risk": "中（细节见 docs/risk 与对应手册）"},
]



# ---------------------------------------------------------------------------
# 渠道类型与别名（原注册表里的 16 个）
# ---------------------------------------------------------------------------
_CORE = [
    {
        "id": "github",
        "name": "GitHub",
        "icp_tags": ["developers", "oss-maintainers", "technical-founders"],
        "product_types": ["devtool", "oss", "sdk", "api"],
        "channel_status": "PROVEN",
        "motions": [
            {"motion": "上游交付链：审计→issue→PR→合并", "label": "self_ab",
             "evidence": "版本化案例：上游交付链的适用条件与真实采纳证据"},
            {"motion": "仓库 README 问题钩子 + 一键安装", "label": "hypothesis",
             "evidence": "产品发布动作需以真实使用结果验证"},
            {"motion": "买星 / 拉票式求 star", "label": "self_incident",
             "evidence": "禁止买星、拉票与虚假互动",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "README 首屏：一句话定位 + 问题钩子 + 一行安装",
                "issue 入口留「免费任务/反馈」通道",
                "上游交付链首批 5 个候选仓库（审计→有价值 issue）",
            ],
            "expected": "以真实讨论与采用为观察，不设 Star 目标",
        },
        "scale_gate": "连续出现真实讨论与采用证据后再评估加量",
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
             "evidence": "发布窗口与结果以真实案例复盘为准"},
            {"motion": "机打内容 / LLM 生成文本直接提交", "label": "self_incident",
             "evidence": "平台明确限制时禁止 AI 生成提交与拉票",
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
        "scale_gate": "Show HN 进前 30 或带 ≥10 个真实讨论 → 才规划第二次集中发布",
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
                "发布前全文搜绝对化词（最好/第一/100%），全部换掉——最常见的广告判定触发",
                "发布后 24 小时内：标题原样粘进搜索框、筛选切「最新」，搜不到=没进搜索池，改标题重发",
                "不满意的笔记设私密，不要删（删笔记有记录，一天删多会掉权重）",
            ],
            "expected": "案例区间：一周 800 内测、两月 2 万用户（SunAlly，外部先例，方差大）",
        },
        "scale_gate": ("连续 3 篇阅读量不低于自己 30 天中位数、且都能被搜到 → 才加量；"
                       "只看到单篇爆就加量会把配额烧光"),
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
                "先建 20–50 个关键词库，再从库里找问题——站内随手搜出来的题通常已经被答烂",
                "搜品类词，选关注 100+ / 回答 <50 的问题",
                "「先说结论」结构 + 真实使用细节 + 承认局限；每篇 ≥800 字 + ≥2 张图 + 小标题",
                "不复制、不搬运：重复内容是知乎最忌讳的一条，比写得短更致命",
                "前 10 答集中在同一领域（领域权重逻辑）",
            ],
            "expected": "搜索长尾 2–8 周起量，好回答持续数年 [外部基准·二手]",
        },
        "scale_gate": "连续 3 篇进话题前 10，或出现私信/评论咨询 → 扩到相邻问题",
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
                "关键词自然出现即可：标题、首段、小标题、结尾各一次，别堆砌",
                "群发后立刻用搜一搜搜自己的标题，验证有没有被收录——30 秒的动作，大多数人没做",
                "AI 辅助写作主动声明；AI 占比大不声明原创",
            ],
            "expected": "不做冷启动渠道：承接外部平台搜索，4–8 周搜一搜起量 [推测]",
        },
        "scale_gate": "连续 3 篇能被搜一搜搜到、且阅读不低于自己中位数 → 才加内容产能",
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
             "evidence": "各 sub 对 AI 内容与自我推广的判定需逐版核查",
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
             "evidence": "模板化触达与深度定制的对比见版本化实测案例",
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
    {
        "id": "kickstarter",
        "name": "Kickstarter（众筹）",
        "icp_tags": ["consumers", "designers"],
        "product_types": ["hardware", "creative"],
        "channel_status": "UNVERIFIED",
        "motions": [
            {"motion": "prelaunch 邮件列表 2–3 个月 + 首 48h 冲 30% 目标", "label": "external_baseline",
             "evidence": "老手基准：列表单独 24h 冲 ~30% 才算准备好；早期速度换算法排名与 25–50% 站内流量（手册 kickstarter.md §2/§4，二手基准已标注）"},
            {"motion": "无列表裸发众筹", "label": "hypothesis",
             "evidence": "all-or-nothing 机制下首周冷启动≈死（手册 §5 判断）",
             "killed": True},
            {"motion": "纯软件/SaaS 上众筹", "label": "hypothesis",
             "evidence": "品类不匹配，成功率低（手册 §10 判断，未对照验证）",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "提前 2–3 个月建列表（落地页+社群）",
                "90 秒视频：为什么存在+实物+团队",
                "邮件序列照成熟模板：欢迎→问题→幕后→社证→倒数",
            ],
            "expected": "首 48h 冲 30% 目标 → 算法接力；中段死水期是常态 [外部基准·二手]",
        },
        "scale_gate": "上线 48h 达 30% → 才谈媒体放大与追加档位",
        "risk": "中（审核前置+假认领检测；Indiegogo 灵活 funding 可接力）",
    },
    {
        "id": "instagram",
        "name": "Instagram",
        "icp_tags": ["consumers", "designers"],
        "product_types": ["consumer-app", "product"],
        "channel_status": "UNVERIFIED",
        "motions": [
            {"motion": "Reels 视觉钩子：前 3 秒定生死，分享率为北极星", "label": "external_baseline",
             "evidence": "官方口径：Reels 三大信号=观看时长/点赞触达/分享触达（Mosseri 2025-01，手册 instagram.md §2）"},
            {"motion": "经官方 Graph API/官方合作工具发布与排期", "label": "external_baseline",
             "evidence": "自动化合规四级分层的 T1/T2 白名单（docs/risk/automation-compliance.md）"},
            {"motion": "连接第三方自动化/机器人工具", "label": "external_baseline",
             "evidence": "Meta 主动封号（含 AI 执法误伤案例，UBOS 报道，手册 §3/§8）",
             "killed": True},
            {"motion": "devtool/B2B 在 Instagram 做增长", "label": "hypothesis",
             "evidence": "人群错配（手册 §10 判断）",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "Business/Creator 账号（拿 Insights）",
                "3–5 条 Reels 测一个视觉钩子（使用前后对比）",
                "付费合作必须打官方 Paid partnership 标签",
            ],
            "expected": "2–6 周看信号趋势；分享/DM 是转化前信号（官方口径）[外部基准]",
        },
        "scale_gate": "分享率稳定 + DM 咨询出现 → 同格式批量变体",
        "risk": "高（第三方工具封号高压；官方 API 合规路径明确）",
    },
]



_KIND = {
    "github": "社区", "reddit": "社区", "v2ex": "社区", "stack-overflow": "社区",
    "discord": "社区", "indie-hackers": "社区", "jike": "社区", "douban": "社区",
    "tieba": "社区", "csdn": "社区",
    "x": "社媒", "linkedin": "社媒", "weibo": "社媒", "bluesky": "社媒", "maimai": "社媒",
    "zhihu": "内容", "juejin": "内容", "sspai": "内容", "wechat": "内容", "xiaohongshu": "内容",
    "youtube": "视频", "bilibili": "视频", "douyin": "视频", "channels": "视频",
    "kuaishou": "视频", "tiktok": "视频", "instagram": "视频",
    "hackernews": "集中发布", "product-hunt": "集中发布", "kickstarter": "集中发布",
    "email": "直连", "newsletter": "直连", "zhishixingqiu": "直连", "36kr": "直连",
    "seo": "搜索", "directory-sites": "目录", "taobao": "目录",
}

_ALIASES = {
    "github": ("GitHub", "issue", "PR", "合并", "上游"),
    "x": ("X ", "Twitter", "推特", "推文", "tweet"),
    "email": ("邮件", "Gmail", "邮箱", "冷邮件", "发信"),
    "hackernews": ("Hacker News", "HN ", "Show HN"),
    "product-hunt": ("Product Hunt", "PH "),
    "reddit": ("Reddit", "r/"),
    "zhihu": ("知乎",),
    "juejin": ("掘金", "juejin"),
    "wechat": ("公众号", "微信"),
    "xiaohongshu": ("小红书",),
    "v2ex": ("V2EX", "v2ex"),
    "bilibili": ("B站", "bilibili", "哔哩哔哩"),
    "douyin": ("抖音", "douyin"),
    "channels": ("视频号",),
    "linkedin": ("LinkedIn", "领英"),
    "indie-hackers": ("Indie Hackers", "IH"),
    "youtube": ("YouTube", "油管"),
    "seo": ("SEO", "搜索排名", "Google 搜索"),
    "newsletter": ("newsletter", "邮件刊物", "TLDR", "Console"),
    "directory-sites": ("G2", "Capterra", "AlternativeTo", "目录站"),
    "stack-overflow": ("Stack Overflow", "StackOverflow"),
    "discord": ("Discord", "Slack", "服务器"),
    "bluesky": ("Bluesky", "Mastodon", "长毛象"),
    "zhishixingqiu": ("知识星球", "星球"),
    "weibo": ("微博", "Weibo"),
    "douban": ("豆瓣",),
    "tieba": ("贴吧", "百度贴吧"),
    "36kr": ("36氪", "虎嗅"),
    "csdn": ("CSDN",),
    "maimai": ("脉脉",),
    "taobao": ("淘宝", "天猫"),
    "kuaishou": ("快手",),
    "tiktok": ("TikTok",),
    "jike": ("即刻",),
    "sspai": ("少数派",),
    "kickstarter": ("Kickstarter", "众筹"),
    "instagram": ("Instagram", "IG"),
}

# 哪些渠道有可自动执行的写通道：(内核渠道, 动作)。没有的就是只能人工发。
WRITE = {
    "github": ("github", "reply"),
    "email": ("email", "message"),
    "x": ("x", "reply"),
    "reddit": ("reddit", "reply"),
    "hackernews": ("hn", "reply"),
    "linkedin": ("linkedin", "message"),
}


def _ops(pid: str, kind: str) -> Dict[str, Any]:
    base = dict(KIND_DEFAULTS.get(kind, KIND_DEFAULTS["社区"]))
    base.update(OVERRIDES.get(pid, {}))
    return base


def _enrich(entry: Dict[str, Any]) -> Dict[str, Any]:
    pid = entry["id"]
    kind = entry.get("kind") or _KIND.get(pid, "社区")
    ops = _ops(pid, kind)
    out = dict(entry)
    out["kind"] = kind
    out["aliases"] = entry.get("aliases") or _ALIASES.get(pid, (entry.get("name", pid),))
    out["account"] = ops["account"]
    out["residency"] = ops["residency"]
    out["stages"] = [tuple(s) for s in ops["stages"]]
    out["scale"] = ops["scale"]
    out["risks"] = [tuple(r) for r in ops["risks"]]
    out["tier"] = ops["tier"]
    out["write"] = WRITE.get(pid)
    return out


PLATFORMS: List[Dict[str, Any]] = [_enrich(p) for p in (_CORE + _NEW)]

BY_ID: Dict[str, Dict[str, Any]] = {p["id"]: p for p in PLATFORMS}

# 日志扫描用：id -> 别名
SCAN_CHANNELS: Dict[str, Tuple[str, ...]] = {p["id"]: tuple(p["aliases"]) for p in PLATFORMS}

# ---------------------------------------------------------------------------
# 经验库回填：有些平台的判据只能通过「别人真跑过」才知道怎么定。
# 这里把 2026-11-04 那轮语料里被反复验证的判据钉到对应平台上，
# 每条都标了来源篇数，方便以后重采时判断是否还成立。
# ---------------------------------------------------------------------------
_EVIDENCE_TWEAKS: Dict[str, Dict[str, Any]] = {
    # 公众号：搜一搜收录是「被看见」的前置条件（3 条经验，2 篇以上提到）
    "wechat": {
        "stages": [("有资格", "内容不被限流，且搜一搜能搜到自己的标题", 0),
                   ("被看见", "单篇阅读≥1000", 14),
                   ("被问到", "评论区 ≥3 条问怎么用", 28),
                   ("被用上", "有人真的用上并反馈", 45),
                   ("被付费", "≥1 笔", 90)],
        "evidence": "经验库 wechat.md：搜一搜收录/关键词四处各一次（各 2 篇）",
    },
    # 知乎：先建关键词库，再选题（2 篇提到）
    "zhihu": {
        "stages": [("有资格", "回答不被折叠", 0),
                   ("被看见", "单篇阅读≥1000，且收藏高于点赞的 10%", 21),
                   ("被问到", "≥3 条具体咨询", 45),
                   ("被用上", "有人拿去用并反馈", 90),
                   ("被付费", "≥1 笔", 120)],
        "evidence": "经验库 zhihu.md：关键词库/不搬运/图文并茂（各 2–3 篇）",
    },
}

for _pid, _tw in _EVIDENCE_TWEAKS.items():
    if _pid in BY_ID:
        _p = BY_ID[_pid]
        for _k, _v in _tw.items():
            if _k == "stages":
                _p["stages"] = [tuple(x) for x in _v]
            elif _k == "evidence":
                _p["evidence"] = _v
            else:
                _p[_k] = _v
