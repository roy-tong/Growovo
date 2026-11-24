#!/usr/bin/env python3
"""平台策略层：把 playbooks 里已经分级的知识折成内核可执行参数。

为什么单独一层
--------------
``core/platforms.py`` 回答「这个平台是什么、怎么冷启动、什么信号算放大」；
``core/content_ops.py`` 回答「这一篇怎么写」。中间缺一层：**哪些策略是可以
直接进代码的、它凭什么、什么时候该重新核**。2026-11-17 那轮采集把经验库从
210 条扩到 594 条，但绝大多数是摘要级、单源、甚至只是文章标题——直接写进
审稿器会把噪声变成产品行为。本模块只收三类：

  official  平台/法规明文（手册里标注「官方原文已核」的条目）
  craft     **至少 2 篇独立来源**复现过的做法（经验库 support ≥ 2）
  lead      单源二手经验；只展示为线索，不进自动门禁、不改默认参数

两条纪律写在代码里
------------------
1. 每条规则必须能追到出处：``claim`` 要能在 ``经验库/experience.jsonl`` 里
   逐字找到，support 不能超过库里该条的实际来源数；官方规则用 ``source``
   指向手册小节。``validate()`` 不通过就不该进产品。
2. 规则有保质期：``reviewed_at`` 超过 ``REVIEW_WINDOW_DAYS`` 未复核，
   ``stale()`` 会把它标出来，策略卡显示「待刷新」，不假装还成立。

边界：本层只产出内容/运营/执行约束，不证明平台分发、增长或收入结果。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import platform_risk

SCHEMA = "growovo.platform-strategy/1"

# 平台玩法两三个月就换一轮；60 天是经验库既有的新鲜度窗口。
REVIEW_WINDOW_DAYS = 60

# 检测器是实现契约：本模块只声明「要查什么」，具体怎么查在 content_ops。
# 两边必须一一对应（tests/test_platform_strategy.py 会检查）。
DETECTORS: Tuple[str, ...] = (
    "contact_info",     # 站外联系方式 / 导流暗号
    "external_link",    # 正文里的站外链接
    "induce_follow",    # 用利益换关注（关注后回复/领取）
    "induce_share",     # 用利益换分享/点赞（集赞、分享解锁、转发抽奖）
    "absolute_claim",   # 绝对化用语
    "clickbait",        # 标题党词
    "anxiety_sell",     # 贩卖焦虑
    "ad_begging",       # 引导读者点广告（公众号封禁级）
    "image_count",      # 图片数量（配合 limits.images_min/max）
    "hashtag_count",    # 话题标签数量（配合 limits.hashtag_max）
)

LEVELS = ("error", "warning", "info")
GRADES = ("official", "craft", "lead")
SCOPES = ("content", "execution")
RANK = {"error": 0, "warning": 1, "info": 2}

# ---------------------------------------------------------------------------
# 策略注册表
#
#   role        平台在增长链路里的角色（一句）
#   limits      内容与节奏的硬参数；手册没写的一律不猜
#   cadence     默认节奏；用户指定的战术用 overrides 显式覆盖
#   gates       规则：content 进审稿，execution 进执行前检查，detector=None 只进人工清单
#   overrides   用户指定的战术档（例如小红书「每天两篇」）
#   monetization / cold_start  只写有出处的
#   rules       知识条目；claim 必须逐字存在于经验库
# ---------------------------------------------------------------------------
STRATEGY: Dict[str, Dict[str, Any]] = {
    # ------------------------------------------------------------- 小红书
    "xiaohongshu": {
        "role": "内容沉淀 · 搜索长尾（推荐+搜索双轮，关注关系最弱）",
        "limits": {
            "title_max": 20,
            "title_note": "20 字是编辑预算，不是平台硬上限（手册未给上限）",
            "body_min": 300,
            "body_max": 700,
            "images_min": 3,
            "images_max": 6,
            "hashtag_max": 10,
        },
        "cadence": {
            "per_week_min": 3, "per_week_max": 5,
            "note": "周 3–5 篇是常见节奏，日更属激进；证据不支持日更",
        },
        "gates": [
            {"code": "contact_info", "level": "error", "scope": "content", "detector": "contact_info",
             "statement": "笔记或私信出现微信号、电话、站外链接，按交易导流处理（情节严重封号）",
             "fix": "去掉一切联系方式，只走平台内沟通",
             "source": "docs/playbooks/国内/xiaohongshu.md#§3 官方规矩", "grade": "official"},
            {"code": "induce_follow", "level": "warning", "scope": "content", "detector": "induce_follow",
             "statement": "免费分享资料/公益咨询只要引导站外，同样算导流；「四连拿图」「点赞自取」不被鼓励",
             "fix": "资料前置公开，关注只做自然承接",
             "source": "docs/playbooks/国内/xiaohongshu.md#§3 官方规矩", "grade": "official"},
            {"code": "ai_label", "level": "warning", "scope": "content", "detector": None,
             "statement": "AI 生成内容须显式标识，未标识会限制推荐；虚拟人须亮明身份",
             "fix": "发布前确认：AI 生成或大幅改写的内容加显式标识",
             "source": "docs/playbooks/国内/xiaohongshu.md#§3 官方规矩", "grade": "official"},
            {"code": "anxiety_sell", "level": "warning", "scope": "content", "detector": "anxiety_sell",
             "statement": "《社区商业公约》明确反对虚假种草与贩卖焦虑",
             "fix": "把「再不…就晚了」改成具体场景和具体代价",
             "source": "docs/playbooks/国内/xiaohongshu.md#§3 官方规矩", "grade": "official"},
            {"code": "clickbait", "level": "warning", "scope": "content", "detector": "clickbait",
             "statement": "标题党、虚假宣传在重点整治范围内",
             "fix": "标题写清读者能拿走什么，不用震惊式空话",
             "source": "docs/playbooks/国内/xiaohongshu.md#§5 死路", "grade": "craft"},
            {"code": "repost_within_90d", "level": "warning", "scope": "execution", "detector": None,
             "statement": "自己的笔记 3 个月内不要重复发；搬运/洗稿直接限制推送",
             "fix": "同题换场景、证据或反例，不换标题重发",
             "source": "docs/playbooks/国内/xiaohongshu.md#§5 死路", "grade": "craft"},
            {"code": "duplicate_material", "level": "error", "scope": "content", "detector": None,
             "statement": "同一素材或模板跨账号重复发布（搬运、洗稿、矩阵同稿）属 2026 年双审 8 红线",
             "fix": "一稿只发一个账号；同题要换场景、证据或反例，不换标题重发",
             "source": "docs/risk/automation-compliance.md#§二 逐平台官方口径", "grade": "official"},
        ],
        "overrides": [
            {"id": "daily-two", "per_day": 2, "per_week": 14,
             "note": "用户指定战术，不是平台默认最优节奏；执行条件与效果均待验证",
             "guide": "docs/playbooks/战术/xiaohongshu-daily-two.md"},
        ],
        "monetization": [
            {"threshold": "1000 粉", "what": "入驻蒲公英，接商业报备单（报备后可合规不限流）",
             "source": "docs/playbooks/国内/xiaohongshu.md#§3 官方规矩", "grade": "official"},
        ],
        "cold_start": [
            "先搜再做：产品关键词丢进搜索，读完评论里的抱怨再定方向",
            "第一篇发内测过程而非产品广告：截图 + 为什么做它 + 个人故事",
            "前 10 篇围绕同一主题，让系统认清垂类标签",
            "封面按 3:4 做，标题埋用户会搜的关键词",
            "发布后 2 小时内逐条回评，自己置顶一条补充细节",
            "第 7 天把评论区最高频的问题做成下一篇",
        ],
        "rules": [
            {"claim": "每周稳定2-3篇（或3-5篇），集中爆更和长期断更都会打乱账号权重；不用日更",
             "action": "固定周更 3–5 篇高质量内容，别三天打鱼两天晒网", "grade": "craft"},
            {"claim": "小红书是首页双列推荐机制，封面不吸引人内容再好也没用；封面是双列社区的核心，必须3:4比例",
             "action": "封面按 3:4 制作，把最大精力放在封面点击率上", "grade": "craft"},
            {"claim": "封面=高对比配色+大字标题，标题埋入关键词+点出痛点才有人停留",
             "action": "封面同时满足高对比、大字、埋搜索关键词、点出痛点", "grade": "craft"},
            {"claim": "算法从'流量获取'转向'用户留存'：搜索流量价值超过推荐流量，好笔记不一定当天爆，发布后一周甚至一个月仍能通过搜索持续进长尾流量",
             "action": "正文和话题标签里植入用户会搜索的关键词，吃搜索长尾而不只赌首页推荐", "grade": "craft"},
            {"claim": "3、字数隐形要求：短笔记尽量100字以上，长笔记最好600字左右",
             "action": "短笔记不少于 100 字，长笔记写到 600 字左右", "grade": "craft"},
            {"claim": "绝对化词（最好、第一、100%）会触发广告判定",
             "action": "发布前全文搜一遍绝对化词，全部换掉", "grade": "craft"},
            {"claim": "把标题原样粘进搜索框、筛选切「最新」，搜不到自己=没进搜索池",
             "action": "每条发布后 24 小时做一次这个检查，搜不到就改标题重发", "grade": "craft"},
            {"claim": "曝光和点击率要成对看：曝光低+点击正常=平台不给量；曝光正常+点击低=内容不行",
             "action": "把最近 3 篇的「首页推荐曝光」和点击率列出来，先分清是哪种坏", "grade": "craft"},
            {"claim": "单篇笔记只能微调1-2次，频繁大幅修改会被平台盯上反而不给推流",
             "action": "低数据笔记最多改 1–2 次封面/标题，避免频繁删改", "grade": "craft"},
            {"claim": "不要批量删除笔记，先设为'仅自己可见'；大批量删稿容易二次触发风控",
             "action": "有问题笔记先隐藏（仅自己可见），不要一次删一大批", "grade": "craft"},
            {"claim": "黄金发布时段为中午11:30-13:00、晚上17:00-22:30，要提前半小时发留给审核时间；工作日白天发数据惨",
             "action": "掐点提前 30 分钟发布，赶午休和下班刷手机高峰", "grade": "craft"},
            {"claim": "零经验起号5步：锁定一个垂直赛道→找20个同赛道近30天爆款账号整理规律→搭选题库（分干货/痛点/避坑/经验四类）→统一封面配色字体排版→稳定更新按后台数据筛选放大优质方向",
             "action": "按五步起号，先建选题库再更新", "grade": "craft"},
        ],
        "notes": [
            "「流量池逐级晋级」「新号保护期」无官方依据，只能当假说。",
            "官方口径里没有「限流」这个说法，称流量异常；别把数据差先归因限流。",
        ],
        "reviewed_at": "2026-11-21",
    },

    # ------------------------------------------------------------- 公众号
    "wechat": {
        "role": "私域沉淀 · 搜一搜长尾（订阅弱化，推荐+搜索双引擎）",
        "limits": {
            "title_max": 64,
            "title_ideal": "18-24",
            "title_note": "18–24 字是全库唯一硬字数规则；真诚、清晰、戳需求",
            "body_min": 1200,
            "body_max": 3500,
            "images_min": 0,
            "images_max": 2,
            "hashtag_max": 10,
            "ad_slots": {"floor_chars": 600, "full_chars": 1000},
        },
        "cadence": {
            "per_week_min": 1, "per_week_max": 3,
            "note": "周 1–3 篇是主流；推荐池逻辑下日更水文不如周更干货",
        },
        "gates": [
            {"code": "induce_follow", "level": "error", "scope": "content", "detector": "induce_follow",
             "statement": "「关注后回复 XX 领取」属诱导关注，明文违规",
             "fix": "资料前置发放，关注后置为自然承接",
             "source": "docs/playbooks/国内/wechat-official.md#§3 官方规矩", "grade": "official"},
            {"code": "induce_share", "level": "error", "scope": "content", "detector": "induce_share",
             "statement": "集赞、拆礼盒、分享解锁全文、分享增抽奖属诱导分享",
             "fix": "去掉任何「分享后解锁」的设计",
             "source": "docs/playbooks/国内/wechat-official.md#§3 官方规矩", "grade": "official"},
            {"code": "contact_info", "level": "warning", "scope": "content", "detector": "contact_info",
             "statement": "正文明示或暗示加个人微信/进群并带诱导，重者限制群发",
             "fix": "承接走企业微信组件或公众号客服",
             "source": "docs/playbooks/国内/wechat-official.md#§3 官方规矩", "grade": "official"},
            {"code": "ad_begging", "level": "error", "scope": "content", "detector": "ad_begging",
             "statement": "自己点广告或引导读者点流量主广告，限流到永久封禁",
             "fix": "删除一切「点广告支持我」类表述",
             "source": "docs/playbooks/战术/wechat-运营实战手册.md#七、红线清单", "grade": "craft"},
            {"code": "ai_label", "level": "warning", "scope": "content", "detector": None,
             "statement": "AI 生成内容需主动声明；AI 占比过大仍声明原创可能被投诉撤销",
             "fix": "AI 率控制在 30% 以下，个人视角部分人工重写",
             "source": "docs/playbooks/国内/wechat-official.md#§3 官方规矩", "grade": "official"},
            {"code": "scripted_bulk_publish", "level": "error", "scope": "execution", "detector": None,
             "statement": "使用脚本、程序托管等自动化方式批量、连续发布，属官方专项治理对象",
             "fix": "定时任务只产出草稿；群发逐篇人工确认后再发",
             "source": "docs/risk/automation-compliance.md#§二 逐平台官方口径", "grade": "official"},
            {"code": "ad_slot_floor", "level": "warning", "scope": "content", "detector": None,
             "statement": "广告前后各需 300 字：600 字以下没有文中广告位",
             "fix": "正文过 600 字再考虑文中广告；千字以上才有文中 2+文末 1",
             "source": "docs/playbooks/战术/wechat-运营实战手册.md#六、变现路线", "grade": "craft"},
        ],
        "overrides": [],
        "monetization": [
            {"threshold": "100 粉", "what": "流量主（广告位）",
             "source": "docs/playbooks/战术/wechat-运营实战手册.md#六、变现路线", "grade": "craft"},
            {"threshold": "600 字 / 1000 字", "what": "广告位数量台阶：文中 1+文末 1 / 文中 2+文末 1",
             "source": "docs/playbooks/战术/wechat-运营实战手册.md#六、变现路线", "grade": "craft"},
            {"threshold": "约 5000–10000 粉", "what": "商单广告，报价 0.6–1 元/阅读",
             "source": "经验库 wechat.md#广告报价", "grade": "craft"},
        ],
        "cold_start": [
            "先发 3–5 篇回答「这个号是什么、解决什么、写给谁」的定位文",
            "标题埋产品词/领域词，为搜一搜收录做准备",
            "发布后 30 分钟内做首波互动（私发核心读者点赞/在看/留言）",
            "群发后去搜一搜搜自己的标题，确认被收录",
            "攒稿：流量好的时候留存货，断更一个月重启会重摔",
        ],
        "rules": [
            {"claim": "关键词自然出现即可：标题、首段、小标题、结尾各一次",
             "action": "别堆砌，四处各一次就够", "grade": "craft"},
            {"claim": "群发后去搜一搜搜自己的标题，验证有没有被收录",
             "action": "这一步 30 秒，但大多数人不做", "grade": "craft"},
            {"claim": "推荐是「赛马机制+多维度评估」，算法极度依赖初始互动数据（点赞、在看、留言、转发、完读率）决定是否加推",
             "action": "发布后第一时间引导核心读者互动，并优化完读率（短段落、干货前置）", "grade": "craft"},
            {"claim": "流量主开通门槛一路下调：5000粉→500粉（2019年5月）→百粉级（2026年实测100粉即开通）",
             "action": "以 100 粉为变现第一里程碑，达标立即开通流量主", "grade": "craft"},
            {"claim": "公众号广告报价基准0.6-1元/阅读，粉丝越多报价指数上升",
             "action": "对外报价以真实数据为底；不买刷量，整治时真实数据会全部曝光", "grade": "craft"},
            {"claim": "最好写的选题永远是亲身经历：走过的弯路、创业踩的坑、找用户被拒的过程本身就是内容",
             "action": "建「亲历素材库」，没选题时优先从这里取材，比追热点更可持续", "grade": "craft"},
            {"claim": "引流前先在号里囤5-10篇拿得出手的文章",
             "action": "太少读者无从了解你，太多没阅读量显得失败；先把 5–10 篇打磨到位", "grade": "craft"},
            {"claim": "连续发约3篇原创文章即可开通原创功能与赞赏",
             "action": "新号前期连发 3 篇以上带原创声明的内容，尽快解锁赞赏入口", "grade": "craft"},
        ],
        "notes": [
            "流量主 100 粉是手册实测口径，不是官方公告；以自己后台为准。",
            "「2026 订阅弱化」是灰度观察，不是官方公告。",
        ],
        "reviewed_at": "2026-11-21",
    },

    # ------------------------------------------------------------- 知乎
    "zhihu": {
        "role": "专业问答 · 长尾搜索（回答持续数月被搜到）",
        "limits": {
            "title_max": 0,
            "title_note": "问题标题由平台给定；回答重在结构",
            "body_min": 800,
            "body_max": 4000,
            "images_min": 2,
            "images_max": 8,
            "hashtag_max": 5,
        },
        "cadence": {
            "per_week_min": 2, "per_week_max": 5,
            "note": "周 2–5 答，重质不重量；一天铺 10 答属异常",
        },
        "gates": [
            {"code": "contact_info", "level": "error", "scope": "content", "detector": "contact_info",
             "statement": "回答、私信、简介里引导加微信或 QQ 群属违规导流",
             "fix": "去掉联系方式；必要链接放个人主页",
             "source": "docs/playbooks/国内/zhihu.md#§3 官方规矩", "grade": "official"},
            {"code": "external_link", "level": "warning", "scope": "content", "detector": "external_link",
             "statement": "回答结尾必带链接会被判定为营销号",
             "fix": "把链接收进个人主页，正文只讲做法",
             "source": "docs/playbooks/国内/zhihu.md#§6 这里的人怎么说话", "grade": "craft"},
            {"code": "fabricated_experience", "level": "error", "scope": "content", "detector": None,
             "statement": "编造使用经历博取关注/不当获利，通常永久封禁",
             "fix": "只写真实跑过的过程；没用过就不写使用时长和效果",
             "source": "docs/playbooks/国内/zhihu.md#§3 官方规矩", "grade": "official"},
            {"code": "duplicate_answer", "level": "warning", "scope": "execution", "detector": None,
             "statement": "同一套话术答几十个问题、跨回答高度同构，按营销处置",
             "fix": "同题换问题场景重写；跨答相似度要能看出差异",
             "source": "docs/playbooks/国内/zhihu.md#§3 官方规矩", "grade": "official"},
            {"code": "ai_label", "level": "warning", "scope": "content", "detector": None,
             "statement": "AI 批量回答不标注有风险（官方原文待核）",
             "fix": "AI 起草后人工重写个人视角部分",
             "source": "docs/playbooks/国内/zhihu.md#§3 官方规矩", "grade": "craft"},
        ],
        "overrides": [],
        "monetization": [],
        "cold_start": [
            "先建 20–50 个关键词，再逐个找问题（不是站内随手搜）",
            "搜品类词，挑关注数 100+、回答数 <50 的高关注低竞争问题",
            "用个人实名号（专业背景可查），不用匿名号",
            "前 10 个回答集中一个领域，先拿到领域权重",
            "发出后 48 小时盯评论区逐条回（评论本身是排序信号）",
        ],
        "rules": [
            {"claim": "不要复制、不要搬运，重复内容是最忌讳的",
             "action": "每篇都写自己的实测细节，不用现成段落", "grade": "craft"},
            {"claim": "写真实故事比写枯燥结论更受欢迎",
             "action": "用「我当时遇到什么」开头，而不是「首先、其次」", "grade": "craft"},
            {"claim": "回答要图文并茂、够长、有结构",
             "action": "一篇回答至少 800 字 + 2 张图 + 小标题", "grade": "craft"},
            {"claim": "选题靠关键词库，不是站内随手搜",
             "action": "先建 20–50 个关键词，再去逐个找问题", "grade": "craft"},
            {"claim": "借势热点是引流最快的方式，但热点过了就没了",
             "action": "只蹭与自己产品直接相关的话题", "grade": "craft"},
            {"claim": "定位=服务的细分人群+满足的细分需求；人设选专家",
             "action": "把定位写成「为谁+解决什么」两句话", "grade": "craft"},
            {"claim": "不要刷量",
             "action": "一个粉丝都不要刷", "grade": "craft"},
        ],
        "notes": ["盐值、折叠、AI 标注的官方原文未逐条核实，标 craft 的条目按二手处理。"],
        "reviewed_at": "2026-11-21",
    },

    # ------------------------------------------------------------- 抖音
    "douyin": {
        "role": "大盘流量 · 算法推荐（工具类人群错配，只做展示不做获客）",
        "limits": {
            "title_max": 0,
            "title_note": "短视频为主；文案短，重点在前 3 秒钩子",
            "body_min": 0,
            "body_max": 0,
            "images_min": 0,
            "images_max": 0,
            "hashtag_max": 5,
        },
        "cadence": {
            "per_week_min": 3, "per_week_max": 7,
            "note": "日更或周 3–5 条是二手共识；前 3 条目标是「数据及格」不是爆",
        },
        "gates": [
            {"code": "external_link", "level": "error", "scope": "content", "detector": "external_link",
             "statement": "站外链接/联系方式按导流处理",
             "fix": "去掉链接与联系方式，只用平台内组件",
             "source": "docs/playbooks/国内/douyin.md#§5 死路", "grade": "craft"},
            {"code": "ad_no_report", "level": "warning", "scope": "execution", "detector": None,
             "statement": "达人商业合作须走巨量星图报备，接私单属违规",
             "fix": "有商业合作先报备；未报备不发布带报价口播的内容",
             "source": "docs/playbooks/国内/douyin.md#§3 官方规矩", "grade": "official"},
            {"code": "ai_label", "level": "warning", "scope": "content", "detector": None,
             "statement": "AI 生成视频或文案须标识（《标识办法》2025-09-01 施行）",
             "fix": "AI 生成内容加显式标识",
             "source": "docs/playbooks/国内/douyin.md#§3 官方规矩", "grade": "official"},
            {"code": "repost_without_process", "level": "warning", "scope": "execution", "detector": None,
             "statement": "搬运混剪他人素材、无自有实拍会被判定搬运",
             "fix": "用自己的录屏、出镜和过程素材",
             "source": "docs/playbooks/国内/douyin.md#§5 死路", "grade": "craft"},
        ],
        "overrides": [],
        "monetization": [],
        "cold_start": [
            "竖屏 9:16，演示压到 15–30 秒（不低于 7 秒）",
            "前 3 秒给结论或冲突，口语化 + 大字幕，不用「大家好我是」开场",
            "前 3 条的目标是「数据及格」，不是爆",
            "发布后前 1 小时守评论，置顶一个能引发讨论的问题",
            "第 7 天把跑出来的那条拆成 3 个变体再测",
        ],
        "rules": [
            {"claim": "先给小流量池；赞评转完播好，才推到更大池",
             "action": "前 3 条视频的目标是「数据及格」，不是爆", "grade": "craft"},
            {"claim": "视频越短完播越高，但不低于 7 秒；竖屏 9:16",
             "action": "工具类演示压到 15–30 秒，竖屏拍", "grade": "lead"},
        ],
        "notes": ["分发机制与流量池晋级没有官方公开依据，只能当假说。"],
        "reviewed_at": "2026-11-21",
    },

    # ------------------------------------------------------------- X
    "x": {
        "role": "技术人对话 · 实时脉冲（深对话偶发有效，模板化接近 0）",
        "limits": {
            "title_max": 0,
            "title_note": "无标题；单条 280 字符是官方 API 上限（core/queue.MAX_LEN）",
            "body_min": 0,
            "body_max": 280,
            "images_min": 0,
            "images_max": 4,
            "hashtag_max": 2,
        },
        "cadence": {
            "per_week_min": 7, "per_week_max": 21,
            "note": "一天 1–3 条原创是上限；回复不限但要有质量",
        },
        "gates": [
            {"code": "external_link", "level": "info", "scope": "content", "detector": "external_link",
             "statement": "正文外链会压低分发；回复里不放链接",
             "fix": "把链接放个人主页或首条回复之下",
             "source": "docs/playbooks/海外/x.md#§5 死路", "grade": "craft"},
            {"code": "mass_actions", "level": "error", "scope": "execution", "detector": None,
             "statement": "批量关注/取关、批量私信、批量 @、关键词触发的自动推销回复均违禁",
             "fix": "动作白名单只保留 reply/publish，按账号统计单位时间动作数",
             "source": "docs/risk/automation-compliance.md#§二 逐平台官方口径", "grade": "official"},
            {"code": "non_api_write", "level": "warning", "scope": "execution", "detector": None,
             "statement": "X 非 API 浏览器自动化存在平台规则与账号风险；此提示不阻断业务执行",
             "fix": "按业务目标选择官方 API 或授权 CDP，保留身份、配额、回执和停手证据",
             "source": "docs/risk/automation-compliance.md#§二 逐平台官方口径", "grade": "official"},
            {"code": "daily_original_cap", "level": "error", "scope": "execution", "detector": None,
             "statement": "一天发十几条刷曝光会被限；原创 1–3 条/日是上限",
             "fix": "当日原创数 > 3 即拒发",
             "source": "docs/playbooks/海外/x.md#§7 账号与节奏", "grade": "craft"},
            {"code": "link_cost", "level": "warning", "scope": "execution", "detector": None,
             "statement": "带链接帖按条计费（转述 $0.20/条），且外链帖分发显著走低",
             "fix": "链接默认放首条回复或个人主页；单日带链接帖 ≤5 条",
             "source": "docs/risk/automation-compliance.md#§二 逐平台官方口径", "grade": "official"},
        ],
        "overrides": [],
        "monetization": [],
        "cold_start": [
            "先 2–3 周在别人帖下贡献有价值发言，再发自己内容",
            "0 粉丝直接发推广≈0 触达",
            "针对具体人的深度技术对话，不做模板化冷回复",
            "发布后守第一小时，快速回复每条早期评论",
        ],
        "rules": [
            {"claim": "回复的算法权重约13.5分、转发20分、个人主页点击12分、链接点击11分、收藏10分，点赞仅约1分",
             "action": "把点赞当噪音指标；引导收藏与回复比引导点赞有效", "grade": "lead"},
            {"claim": "帖子可见度潜力约每6小时减半，前30-60分钟几乎决定一切",
             "action": "把第一小时当 launch window：发布后守在现场、快速回复早期评论", "grade": "lead"},
        ],
        "notes": [
            "X 的 27 条新经验全部是单源二手 playbook，只能当实验线索，不进自动门禁、不改默认参数。",
            "手册未写单条字符上限；280 是官方 API 限额，API 侧约 300 帖/3 小时。",
        ],
        "reviewed_at": "2026-11-21",
    },

    # ------------------------------------------------------------- Reddit
    "reddit": {
        "role": "垂直社区 · 长尾（规则散落各 sub，先当居民再谈产品）",
        "limits": {
            "title_max": 300,
            "title_note": "各 sub 自定标题规则；以目标 sub 的规则页为准",
            "body_min": 0,
            "body_max": 40000,
            "images_min": 0,
            "images_max": 20,
            "hashtag_max": 0,
        },
        "cadence": {
            "per_week_min": 3, "per_week_max": 7,
            "note": "产品提及严格 10:1 或更低；日常参与不限",
        },
        "gates": [
            {"code": "promo_ratio", "level": "error", "scope": "execution", "detector": None,
             "statement": "每 1 次自我推广须配 9 条非推广内容（官方 10:1）",
             "fix": "不满 9 条非推广贡献，不发产品提及",
             "source": "docs/playbooks/海外/reddit.md#§3 官方规矩", "grade": "official"},
            {"code": "cross_sub_spam", "level": "error", "scope": "execution", "detector": None,
             "statement": "同一文案跨 sub 群发属 spam 判定",
             "fix": "同一内容只发一个最相关的 sub",
             "source": "docs/playbooks/海外/reddit.md#§5 死路", "grade": "official"},
            {"code": "ai_undisclosed", "level": "error", "scope": "content", "detector": None,
             "statement": "AI 生成内容不披露会被删（自家 r/SaaS 实证）",
             "fix": "正文写明 AI 参与程度；内核不生成 Reddit 正文",
             "source": "docs/playbooks/海外/reddit.md#§5 死路", "grade": "craft"},
            {"code": "maker_disclosure", "level": "warning", "scope": "content", "detector": None,
             "statement": "假扮无关用户推荐自己产品属违规",
             "fix": "提及产品必须披露 maker 身份",
             "source": "docs/playbooks/海外/reddit.md#§3 官方规矩", "grade": "official"},
        ],
        "overrides": [],
        "monetization": [],
        "cold_start": [
            "先读目标 sub 的 sidebar、pinned 与近期删帖模式",
            "先在目标 sub 当两周居民，攒 9 份非推广贡献",
            "账号门槛 karma 100+、账号数月（各 sub 不一）",
            "4–8 周居民期后再提产品",
        ],
        "rules": [],
        "notes": [
            "karma/账号年龄门槛各 sub 自定且不公开，没有统一数值。",
            "shadowban 不通知；零数据不等于零曝光。",
        ],
        "reviewed_at": "2026-11-21",
    },

    # ------------------------------------------------------------- Hacker News
    "hackernews": {
        "role": "技术圈注意力中心 · 一次性脉冲（给信誉不给客户）",
        "limits": {
            "title_max": 80,
            "title_note": "80 字符是 Show HN 惯例，不是硬性规则",
            "body_min": 0,
            "body_max": 4000,
            "images_min": 0,
            "images_max": 0,
            "hashtag_max": 0,
        },
        "cadence": {
            "per_week_min": 0, "per_week_max": 1,
            "note": "提交 ≤1/日；Show HN 一年最多 2 次",
        },
        "gates": [
            {"code": "ai_written", "level": "error", "scope": "content", "detector": None,
             "statement": "帖子与评论不许放生成或 AI 改写的文本，要自己写",
             "fix": "HN 通道只收人写稿；检出生成特征即拦",
             "source": "docs/playbooks/海外/hacker-news.md#§3 官方规矩", "grade": "official"},
            {"code": "vote_begging", "level": "error", "scope": "content", "detector": None,
             "statement": "不要求票、求评论、求提交",
             "fix": "删除一切 upvote/求支持表述",
             "source": "docs/playbooks/海外/hacker-news.md#§3 官方规矩", "grade": "official"},
            {"code": "repost_after_delete", "level": "error", "scope": "execution", "detector": None,
             "statement": "不要删了重发；不要批量注册账号",
             "fix": "URL+标题指纹入库，同目标 24 小时内二次提交即拒",
             "source": "docs/playbooks/海外/hacker-news.md#§3 官方规矩", "grade": "official"},
            {"code": "no_auto_post", "level": "error", "scope": "execution", "detector": None,
             "statement": "不要自动化发帖；主体应是好奇不是推广",
             "fix": "内核只出草稿与人工提交单",
             "source": "docs/playbooks/海外/hacker-news.md#§3 官方规矩", "grade": "official"},
        ],
        "overrides": [],
        "monetization": [],
        "cold_start": [
            "先评论 2–3 周攒 karma，再考虑发帖",
            "Show HN 一次，最多一年两次",
            "首评自写：为什么做、怎么做、哪里不行",
        ],
        "rules": [
            {"claim": "HN 受众对订阅制强烈反感，桌面工具类用一次性买断+限定更新窗口更受欢迎",
             "action": "桌面工具默认买断+限定更新窗（如 $40/2设备/2年更新）；云端协作类才用订阅", "grade": "craft"},
            {"claim": "lifetime 定价公式：约为年费的 3-10 倍（实例：6/月、30/年、100/lifetime；Halide 12/年、80/lifetime）",
             "action": "提供 monthly/annual/lifetime 三档；lifetime 先收钱对小团队现金流更关键（单帖多评论者共识，待第二帖印证后升 craft）", "grade": "lead"},
            {"claim": "HN 发布日是在场定价实验：boringBar（520赞）发布当天因评论区强烈反馈从订阅改为 40 美元买断（2设备+2年更新），多名评论者随后当场购买",
             "action": "Show HN 发布日全天在场；定价变更当天改，并在原评论线程主动告知问过价的人", "grade": "lead"},
            {"claim": "落地页的假证据会被 HN 评论区现场拆穿（randomuser.me 假头像、硬编码用户数、与反AI叙事冲突的AI发布史）",
             "action": "评价只放可验证来源（Google 评价/LinkedIn/推文链接）；用户数要么实时真要么别放", "grade": "lead"},
            {"claim": "AI 文案句式会被 HN 读者现场识别并扣分：'No X. No Y. Just Z.' 句式、风格不统一的 AI 插图、LLM 写作腔",
             "action": "发布文案避开模板化 AI 句式；插图要么风格统一要么不用；发布前逐字校对 typo", "grade": "lead"},
        ],
        "notes": ["标题 80 字符与一小时内补首评都是惯例，不是官方硬规则。",
                  "内容契约来源：经验库 hackernews（2026-11-17 Algolia 一手评论树采集，boringBar 520赞/Write It Down 271赞两帖）。"],
        "reviewed_at": "2026-11-21",
    },

    # ------------------------------------------------------------- LinkedIn
    "linkedin": {
        "role": "职业身份网络 · B2B（半自动，终端人工确认）",
        "limits": {
            "title_max": 200,
            "title_note": "连接说明 200 字符是手册操作口径，非官方明文",
            "body_min": 0,
            "body_max": 3000,
            "images_min": 0,
            "images_max": 9,
            "hashtag_max": 5,
        },
        "cadence": {
            "per_week_min": 2, "per_week_max": 3,
            "note": "内容 2–3 条/周；触达 ≤8/日",
        },
        "gates": [
            {"code": "third_party_automation", "level": "error", "scope": "execution", "detector": None,
             "statement": "禁止任何第三方软件抓取或自动化 LinkedIn",
             "fix": "只走半自动 + 终端人工确认，不做无人值守",
             "source": "docs/playbooks/海外/linkedin.md#§3 官方规矩", "grade": "official"},
            {"code": "reach_cap", "level": "error", "scope": "execution", "detector": None,
             "statement": "日触达 >8 与模板群发连接请求会触发限制",
             "fix": "日触达 ≤8；连接说明引用对方公开的具体事",
             "source": "docs/playbooks/海外/linkedin.md#§4 真实打法", "grade": "craft"},
            {"code": "bait_pitch", "level": "warning", "scope": "content", "detector": None,
             "statement": "装作请教实为推销、接受后立刻 pitch 会被举报",
             "fix": "首条消息不提报价、不放产品链接",
             "source": "docs/playbooks/海外/linkedin.md#§5 死路", "grade": "craft"},
        ],
        "overrides": [],
        "monetization": [],
        "cold_start": [
            "档案做实：标题写价值主张，不写职位名",
            "内容互动 2 周后再触达",
            "连接说明 200 字内、引用对方公开具体事",
        ],
        "rules": [],
        "notes": ["接受率低于约 30% 应停手修定向；封号触发器是接受率/回复率（社区逆向，非官方）。"],
        "reviewed_at": "2026-11-21",
    },
}


def _as_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def platform_ids() -> List[str]:
    return list(STRATEGY)


def get(platform: str) -> Dict[str, Any]:
    """取一个平台的策略；没有就抛错，不返回空壳。"""
    pid = str(platform or "").strip().lower()
    if pid not in STRATEGY:
        raise ValueError(f"没有这个平台的策略卡：{platform}；可选 {', '.join(STRATEGY)}")
    return STRATEGY[pid]


def limits(platform: str) -> Dict[str, Any]:
    return dict(get(platform).get("limits") or {})


def cadence(platform: str) -> Dict[str, Any]:
    return dict(get(platform).get("cadence") or {})


def gates(platform: str, *, scope: str = "content") -> List[Dict[str, Any]]:
    """返回内容质量检查与非阻断平台风险提示。"""
    want = {"content", "execution"} if scope == "all" else {scope}
    out = []
    for gate in get(platform).get("gates") or []:
        if (gate.get("scope") or "content") not in want:
            continue
        row = dict(gate)
        if (row.get("grade") == "official" and row.get("code") != "fabricated_experience"
                and row.get("level") == "error"):
            row["level"] = "warning"
        row["blocking"] = row.get("level") == "error"
        out.append(row)
    return out


def auto_gates(platform: str, *, scope: str = "content") -> List[Dict[str, Any]]:
    """能自动跑的（有 detector 的）；detector=None 的只进人工清单。"""
    return [g for g in gates(platform, scope=scope) if g.get("detector")]


def checklist(platform: str) -> List[str]:
    """人工检查清单：没有自动检测器的门禁 + 冷启动动作。"""
    items = [g["statement"] + " → " + g.get("fix", "")
             for g in (get(platform).get("gates") or []) if not g.get("detector")]
    items += list(get(platform).get("cold_start") or [])
    return items


def overrides(platform: str) -> List[Dict[str, Any]]:
    return [dict(x) for x in (get(platform).get("overrides") or [])]


def rules(platform: str, *, grade: Optional[str] = None) -> List[Dict[str, Any]]:
    out = [dict(r) for r in (get(platform).get("rules") or [])]
    if grade:
        out = [r for r in out if r.get("grade") == grade]
    return out


def age_days(platform: str, on: Optional[Any] = None) -> Optional[int]:
    reviewed = _as_date(get(platform).get("reviewed_at"))
    if reviewed is None:
        return None
    today = _as_date(on) or date.today()
    return (today - reviewed).days


def stale(on: Optional[Any] = None) -> List[Dict[str, Any]]:
    """超过复核窗口的平台：返回平台、复核日与天数，供策略卡标「待刷新」。"""
    out = []
    for pid in STRATEGY:
        days = age_days(pid, on)
        if days is None or days > REVIEW_WINDOW_DAYS:
            out.append({"platform": pid, "reviewed_at": get(pid).get("reviewed_at"),
                        "age_days": days, "window_days": REVIEW_WINDOW_DAYS})
    return out


def coverage() -> Dict[str, Dict[str, Any]]:
    """每个平台的策略覆盖：自动门禁、人工清单、执行门禁、规则与复核状态。"""
    out = {}
    for pid in STRATEGY:
        spec = get(pid)
        content = gates(pid, scope="content")
        execution = gates(pid, scope="execution")
        days = age_days(pid)
        out[pid] = {
            "gates_auto": sum(1 for g in content if g.get("detector")),
            "gates_manual": sum(1 for g in content if not g.get("detector")),
            "gates_execution": len(execution),
            "rules": len(spec.get("rules") or []),
            "official": sum(1 for g in spec.get("gates") or [] if g.get("grade") == "official"),
            "reviewed_at": spec.get("reviewed_at"), "age_days": days,
            "stale": days is None or days > REVIEW_WINDOW_DAYS,
        }
    return out


# ---------------------------------------------------------------------------
# 证据追溯
# ---------------------------------------------------------------------------
def _normalize(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _library() -> List[Dict[str, Any]]:
    from . import experience
    return experience.load()


def _support_index(pid: str) -> Dict[str, int]:
    """平台经验库里 claim → support；用于校验代码里写的规则有没有吹大。"""
    out: Dict[str, int] = {}
    for row in _library():
        if str(row.get("platform") or "") != pid:
            continue
        try:
            support = int(row.get("support") or 0)
        except (TypeError, ValueError):
            support = 0
        key = _normalize(row.get("claim"))
        out[key] = max(out.get(key, 0), support)
    return out


def validate(platforms: Optional[Iterable[str]] = None) -> Dict[str, List[str]]:
    """校验策略层：声明了规则就必须能追到出处，参数必须自洽。

    返回 ``{platform: [问题]}``；空 dict 表示全部通过。
    """
    from . import platforms as platform_registry

    problems: Dict[str, List[str]] = {}
    pids = list(platforms) if platforms else list(STRATEGY)
    for pid in pids:
        issues: List[str] = []
        if pid not in platform_registry.BY_ID:
            issues.append("platforms.py 里没有这个平台，策略卡会与执行层脱节")
            problems[pid] = issues
            continue
        spec = STRATEGY[pid]
        lim = spec.get("limits") or {}
        for low, high in (("body_min", "body_max"), ("images_min", "images_max")):
            if low in lim and high in lim and lim[low] > lim[high]:
                issues.append(f"limits.{low} 大于 {high}")
        if "title_max" in lim and lim["title_max"] < 0:
            issues.append("limits.title_max 不能为负")
        cad = spec.get("cadence") or {}
        if cad.get("per_week_min", 0) > cad.get("per_week_max", 0):
            issues.append("cadence.per_week_min 大于 per_week_max")
        reviewed = _as_date(spec.get("reviewed_at"))
        if reviewed is None:
            issues.append("缺少有效 reviewed_at")
        elif reviewed > date.today():
            issues.append("reviewed_at 在未来")
        for gate in spec.get("gates") or []:
            if gate.get("level") not in LEVELS:
                issues.append(f"门禁 {gate.get('code')} 的 level 非法")
            if gate.get("scope") and gate["scope"] not in SCOPES:
                issues.append(f"门禁 {gate.get('code')} 的 scope 非法")
            if gate.get("detector") and gate["detector"] not in DETECTORS:
                issues.append(f"门禁 {gate.get('code')} 使用了未实现的检测器 {gate['detector']}")
            if gate.get("grade") not in GRADES:
                issues.append(f"门禁 {gate.get('code')} 缺少合法 grade")
            if not gate.get("source"):
                issues.append(f"门禁 {gate.get('code')} 没有出处")
        library = _support_index(pid)
        for rule in spec.get("rules") or []:
            if rule.get("grade") not in GRADES:
                issues.append(f"规则缺少合法 grade：{rule.get('claim', '')[:20]}")
            claim = _normalize(rule.get("claim"))
            if rule.get("grade") == "official":
                if not rule.get("source"):
                    issues.append(f"官方规则缺少 source：{rule.get('claim', '')[:20]}")
                continue
            if not claim:
                issues.append("规则缺少 claim")
                continue
            if claim not in library:
                issues.append(f"经验库找不到这条 claim（可能被改写或删除）：{rule.get('claim')[:30]}")
                continue
            declared = rule.get("support")
            if declared is not None and int(declared) > library[claim]:
                issues.append(f"support 被写大：{rule.get('claim')[:24]} 声明 {declared}，库里 {library[claim]}")
            if rule.get("grade") == "craft" and library[claim] < 2:
                issues.append(f"多源经验至少要有 2 篇独立来源：{rule.get('claim')[:24]} 库里只有 {library[claim]} 篇")
        if issues:
            problems[pid] = issues
    for pid, risk_issues in platform_risk.validate(pids).items():
        problems.setdefault(pid, []).extend(risk_issues)
    return problems


# ---------------------------------------------------------------------------
# 策略卡
# ---------------------------------------------------------------------------
def card(platform: str, *, on: Optional[Any] = None) -> Dict[str, Any]:
    """一页可读的策略卡：参数、门禁、证据、待刷新状态。"""
    pid = str(platform or "").strip().lower()
    spec = get(pid)
    from . import platforms as platform_registry
    entry = platform_registry.BY_ID.get(pid, {})
    days = age_days(pid, on)
    library = _support_index(pid)
    rules_out = []
    for rule in spec.get("rules") or []:
        item = dict(rule)
        item["library_support"] = library.get(_normalize(rule.get("claim")))
        rules_out.append(item)
    return {
        "schema_version": SCHEMA,
        "platform": pid,
        "name": entry.get("name") or pid,
        "kind": entry.get("kind"),
        "tier": entry.get("tier"),
        "role": spec.get("role", ""),
        "limits": limits(pid),
        "cadence": cadence(pid),
        "gates": gates(pid, scope="content"),
        "execution_gates": gates(pid, scope="execution"),
        "risk": platform_risk.card(pid, on=on),
        "checklist": checklist(pid),
        "overrides": overrides(pid),
        "monetization": [dict(x) for x in spec.get("monetization") or []],
        "cold_start": list(spec.get("cold_start") or []),
        "rules": rules_out,
        "reviewed_at": spec.get("reviewed_at"),
        "age_days": days,
        "stale": days is None or days > REVIEW_WINDOW_DAYS,
        "window_days": REVIEW_WINDOW_DAYS,
        "notes": list(spec.get("notes") or []),
        "scope": "内容与运营约束；不证明平台分发、增长或收入结果",
        "validator": (validate([pid]).get(pid) or []),
    }


def _cadence_text(cad: Dict[str, Any]) -> str:
    low, high = cad.get("per_week_min"), cad.get("per_week_max")
    if low and high and low != high:
        return f"每周 {low}–{high} 篇（{cad.get('note', '')}）"
    if high:
        return f"每周约 {high} 篇（{cad.get('note', '')}）"
    return cad.get("note", "未定")


def render_card(platform: str, *, on: Optional[Any] = None) -> str:
    c = card(platform, on=on)
    lines = [f"# {c['name']} · 平台策略卡", ""]
    meta = f"层：{c['kind'] or '—'}｜内容档位：{c['tier'] or '—'}｜复核：{c['reviewed_at']}"
    if c["stale"]:
        meta += f"｜**待刷新**（已 {c['age_days']} 天，窗口 {c['window_days']} 天）"
    lines.append(meta)
    lines.append(f"角色：{c['role']}")
    lines.append("")
    lines.append("## 节奏与上限")
    lines.append(f"- 节奏：{_cadence_text(c['cadence'])}")
    lim = c["limits"]
    if lim.get("title_max"):
        lines.append(f"- 标题：最多 {lim['title_max']} 字；{lim.get('title_note', '')}")
    if lim.get("body_max"):
        lines.append(f"- 正文：{lim.get('body_min', 0)}–{lim['body_max']} 字")
    if lim.get("images_max"):
        lines.append(f"- 图片：{lim.get('images_min', 0)}–{lim['images_max']} 张")
    for ov in c["overrides"]:
        lines.append(f"- 战术覆盖「{ov['id']}」：每天 {ov.get('per_day')} 篇 / 每周 {ov.get('per_week')} 篇"
                     f" — {ov.get('note', '')}")
    lines.append("")
    lines.append("## 硬规则（审稿会拦）")
    if not c["gates"]:
        lines.append("- 暂无内容门禁")
    for g in c["gates"]:
        how = "自动" if g.get("detector") else "人工清单"
        lines.append(f"- [{g['level']}] {g['statement']} → {g.get('fix', '')}（{how}；{g.get('source', '')}）")
    if c["execution_gates"]:
        lines.append("")
        lines.append("## 执行边界（发送前检查）")
        for g in c["execution_gates"]:
            lines.append(f"- [{g['level']}] {g['statement']} → {g.get('fix', '')}（{g.get('source', '')}）")
    lines.append("")
    lines.append(platform_risk.render_card(platform, on=on))
    lines.append("")
    if c["monetization"]:
        lines.append("## 变现门槛")
        for m in c["monetization"]:
            lines.append(f"- {m['threshold']}：{m['what']}（{m.get('source', '')}）")
        lines.append("")
    if c["cold_start"]:
        lines.append("## 冷启动")
        lines += [f"{i}. {x}" for i, x in enumerate(c["cold_start"], 1)]
        lines.append("")
    if c["rules"]:
        lines.append("## 证据规则")
        for r in c["rules"]:
            grade = {"official": "官方", "craft": "多源经验", "lead": "单源线索"}.get(r["grade"], r["grade"])
            src = f"；库里 {r['library_support']} 篇" if r.get("library_support") else ""
            lines.append(f"- [{grade}{src}] {r['claim']}")
            if r.get("action"):
                lines.append(f"  - 所以：{r['action']}")
        lines.append("")
    if c["notes"]:
        lines.append("## 边界")
        lines += [f"- {x}" for x in c["notes"]]
    if c["validator"]:
        lines.append("")
        lines.append("## 校验问题（不该进产品）")
        lines += [f"- {x}" for x in c["validator"]]
    lines.append("")
    lines.append(f"> {c['scope']}。")
    return "\n".join(lines)


def summary_rows() -> List[Dict[str, Any]]:
    return [{"platform": pid, **cov} for pid, cov in coverage().items()]


def render_summary() -> str:
    from . import platforms as platform_registry
    lines = ["# 平台策略层覆盖", "",
             "| 平台 | 自动门禁 | 人工清单 | 执行门禁 | 规则 | 官方 | 复核 | 状态 |",
             "|---|---|---|---|---|---|---|---|"]
    for row in summary_rows():
        name = (platform_registry.BY_ID.get(row["platform"]) or {}).get("name", row["platform"])
        status = "待刷新" if row["stale"] else "有效"
        lines.append(f"| {name} | {row['gates_auto']} | {row['gates_manual']} | "
                     f"{row['gates_execution']} | {row['rules']} | {row['official']} | "
                     f"{row['reviewed_at']} | {status} |")
    lines.append("")
    lines.append("> 单源二手经验只做线索，不进自动门禁；到期未复核的平台会标「待刷新」。")
    return "\n".join(lines)
