#!/usr/bin/env python3
"""平台语气引擎：不是「写得好」，是「像这个地方的人说的话」。

为什么单独一层：同一个意思，在 Hacker News 上是「我踩了这个坑，复现在下面」，
在小红书上是「我用了三个月，终于把这件事搞定了」，在 V2EX 上是
「分享个自己写的小东西，不喜勿喷」。**语气错了，内容再对也是广告。**

本模块提供三件事：
  profile(pid)      这个平台的语气画像（tone / 句式 / 开头 / 禁忌 / 黑话 / 长度）
  draft(pid, ...)   按画像给出 3 个开头骨架（不是全文，全文要人写）
  lint(pid, text)   检查一段文字在这个平台上会不会被当成广告

数据来自 docs/playbooks 各手册的第 6 节「这里的人怎么说话」；手册更新后，
这里要跟着改——所以有 test_voice 检查每个平台都有画像，不许漏。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# 渠道类型默认语气：先用类型的通性兜底，具体平台再覆盖
KIND_VOICE: Dict[str, Dict[str, Any]] = {
    "社区": {
        "tone": "直白、克制，先给结论再讲原因；可以自嘲，不能自夸",
        "sentences": "短句。有代码/配置就直接贴，别用文字描述",
        "openers": ["我踩过这个坑，复现在下面",
                    "先说结论：<一句话>，原因有三点",
                    "这个问题我查了两天，答案有点反直觉"],
        "taboos": ["通篇没有一句缺点", "开头自我介绍或公司介绍", "结尾 CTA"],
        "memes": "圈内黑话可以用，但要确定对方也在圈里",
        "length": "150–400 字，超过没人读",
    },
    "社媒": {
        "tone": "有观点、口语、短",
        "sentences": "一句话一个意思，多用换行",
        "openers": ["hot take：<观点>，我这么说是因为我做过",
                    "这周做了件事：<具体>，结果 <数字>",
                    "我原来以为 X，直到 <转折>"],
        "taboos": ["每条都带产品链接", "永远积极从不批评", "全篇 emoji"],
        "memes": "技术圈的梗可以用，过气梗不要用",
        "length": "60–200 字",
    },
    "内容": {
        "tone": "第一人称、讲过程和代价，不端着",
        "sentences": "口语化，可以有不完整句；每段只讲一件事",
        "openers": ["我用了一个月，终于把 <问题> 解决了，过程比想象中折腾",
                    "先说我踩的坑：<具体>，再说怎么绕过",
                    "这个我用到现在，最有用的是第 3 点"],
        "taboos": ["通篇讲产品多好", "搬运别人的说法", "在评论区留联系方式"],
        "memes": "可以用生活化的比喻，别用力过猛",
        "length": "300–800 字，配图更好",
    },
    "视频": {
        "tone": "开头三秒给结论或冲突，全程口语",
        "sentences": "短。一句一个信息点，别念稿",
        "openers": ["三分钟，教你把 <问题> 解决掉",
                    "我花了两周踩完这些坑，你花三分钟看完",
                    "先说结果：<数字>，过程在下面"],
        "taboos": ["开头放 logo 动画", "全程只讲优点", "演示用假数据"],
        "memes": "平台内流行的表达可以用，但不追过时梗",
        "length": "3–10 分钟；脚本 800–2000 字",
    },
    "发射": {
        "tone": "直接说明做了什么、为什么做、哪里不行",
        "sentences": "短句。标题写「你做了什么」，不写「为什么你该用」",
        "openers": ["Show HN: <你做了什么>，因为 <你被什么坑过>",
                    "我做了个 <东西>，解决 <具体问题>，缺点写在下面",
                    "这不是新概念，是把 <已有做法> 做得更省事"],
        "taboos": ["标题党", "通篇没有局限", "求票"],
        "memes": "不需要梗，需要具体",
        "length": "标题 ≤80 字符；首评 150–300 字",
    },
    "直连": {
        "tone": "因为对方会读，所以必须像一对一发给他一个人的",
        "sentences": "前三句决定他读不读：先证明你看过他的东西",
        "openers": ["看到你在 <具体事> 里提到 <细节>，我这边有个同类问题的复现",
                    "你上次说的 <判断>，我按这个测了一遍，结果和预期不一样",
                    "不推销：先给你一份 <能用的东西>，用得上再说"],
        "taboos": ["群发", "第一句就报价", "通篇讲自己"],
        "memes": "不需要",
        "length": "120–250 字，超过 300 字基本没人读完",
    },
    "搜索": {
        "tone": "不存在的语气——读者只看有没有答案",
        "sentences": "第一屏给结论或可复制的命令，再做解释",
        "openers": ["直接给答案：<命令/配置>，下面解释为什么",
                    "如果你遇到 <错误>，原因通常是 <三条>",
                    "这个问题的答案是 <结论>，但只适用于 <条件>"],
        "taboos": ["开头铺垫行业背景", "通篇没有版本号或代码", "每段都引导联系"],
        "memes": "不需要",
        "length": "800–2000 字，越可复制越好",
    },
    "目录": {
        "tone": "采购者视角：适用场景、限制、价格",
        "sentences": "短条目，能量化就量化",
        "openers": ["适合 5–50 人的 <团队>，不适合 <场景>",
                    "把 <流程> 从 <X 天> 压到 <Y 小时>",
                    "免费层够用；付费从 <价格> 起"],
        "taboos": ["堆功能不写结果", "不标价格", "所有竞品都写成不如自己"],
        "memes": "不需要",
        "length": "产品页 200–500 字",
    },
}

# 具体平台的覆盖：手册第 6 节里最能体现「这里的人怎么说话」的内容
VOICE_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "hackernews": {
        "tone": "工程师笔记，不是产品稿。短句、直白、可以自嘲，绝不形容词堆砌",
        "sentences": "直接给数字和取舍；先说缺点的人更被信任",
        "openers": ["Our billing pipeline double-counted retries for six weeks before anyone noticed.",
                    "I got tired of reading JSON logs, so I wrote a thing that turns them into a timeline. It's 400 lines and probably breaks on Windows.",
                    "I know this looks like something from 2009. That's the point."],
        "taboos": ["revolutionary / seamless / cutting-edge", "感叹号", "We're excited to announce",
                   "第一段是公司介绍", "标题带 emoji"],
        "memes": "自嘲可以，「我今天又搞挂生产环境」这类",
        "length": "标题 ≤80 字符；首评 150–300 字",
    },
    "github": {
        "tone": "技术性、克制、就事论事——维护者每天看几十个 issue，你只有三行机会",
        "sentences": "先给复现，再给判断；版本号必须写",
        "openers": ["On v2.4.1, retry=true with a timeout causes the token counter to increment twice. Minimal repro below (12 lines).",
                    "I might be misreading the docs, but --strict seems to skip validation for nested arrays.",
                    "My mistake, this was a config error on my side. Sorry for the noise."],
        "taboos": ["您好，我是XX公司的", "issue 里谈商务", "催维护者", "一稿多投"],
        "memes": "基本不用；技术幽默可以有，但别在 issue 里",
        "length": "issue 200–600 字，越短越好",
    },
    "xiaohongshu": {
        "tone": "像跟朋友讲一件事，讲过程和崩溃，不讲产品",
        "sentences": "短句换行，口语，可以有语气词",
        "openers": ["真的被这个烦死了，试了三个月终于搞定",
                    "不是广，先说踩的坑：<具体>",
                    "笔记一用副词就凉？小眼睛卡 500 以下先看这条",
                    "素人从 0 到 2000 粉，我把这一个月拆开给你看"],
        "taboos": ["评论区留微信/公众号", "通篇产品功能", "AI 味（排比+总结句）",
                   "一开始就说「推荐一款」"],
        "memes": "平台流行的说法可以用；过度玩梗反而假",
        "length": "300–600 字 + 3–6 张图",
    },
    "zhihu": {
        "tone": "行业内部人，先说结论再展开，敢说「这个我没测过」",
        "sentences": "「先说结论」结构 + 真实使用细节 + 承认局限",
        "openers": ["先说结论：<判断>。展开讲三件事。",
                    "这个我踩过，说点具体的：<细节>",
                    "不同意高赞的某个说法，理由是 <数据>",
                    "顺手整理了一份关键词库，从这 20 个词开始找问题"],
        "taboos": ["模板化回答矩阵", "伪造经历", "回答里留联系方式", "通篇不下判断"],
        "memes": "可以引用行业黑话，但要解释",
        "length": "800–2000 字",
    },
    "v2ex": {
        "tone": "邻居聊天，分寸感是生死线：先示弱，再给东西",
        "sentences": "短，口语，不要商业腔",
        "openers": ["分享个自己写的小东西，不喜勿喷",
                    "做了个 <工具>，主要解决我自己的问题，顺手发出来",
                    "问个问题，顺便分享下我的做法"],
        "taboos": ["综合节点发广告", "伪装普通帖软广", "标题党", "小号顶帖"],
        "memes": "本站黑话（如「mark」「铜币」）可以用，但别过度",
        "length": "200–500 字",
    },
    "reddit": {
        "tone": "社区成员，不是作者——先答问题，产品只能顺带",
        "sentences": "短句，直接给方案；披露身份放在同段",
        "openers": ["I ran into this exact issue. Here's what worked for me (code below).",
                    "Disclosure: I build a tool in this space, but here's the fix regardless.",
                    "Not sure this applies to your setup, but on v2.4 this happens because..."],
        "taboos": ["新号直发产品链接", "不披露身份自我推荐", "跨 sub 复制同一段话",
                   "AI 生成文本（AutoModerator 会拦）"],
        "memes": "sub 内梗要看懂再用，用错比不用更糟",
        "length": "100–300 字",
    },
    "email": {
        "tone": "像写给一个具体的人，不是写给名单",
        "sentences": "前三句必须证明你看过他的东西",
        "openers": ["看到你在 <项目> 里提到 <细节>，我们这边复现了一个相关问题，数据和复现步骤在下面。",
                    "你上次说 <判断>，我按这个测了一遍，结果和你说的不一样，想确认是不是我理解错了。",
                    "不推销：先给你一份 <能用的清单>，用得上再说。"],
        "taboos": ["群发", "第一句报价", "通篇讲自己", "附件带 exe"],
        "memes": "不需要",
        "length": "120–250 字",
    },
    "weibo": {
        "tone": "短、有态度、跟着热点走；工具类产品别硬蹭",
        "sentences": "一两句话说清，配图",
        "openers": ["关于 <热点>，说个大部分人没注意的点",
                    "实测：<结论>，数据在评论里"],
        "taboos": ["买热搜", "水军转发", "通篇产品广告"],
        "memes": "热点梗要快，晚了就俗",
        "length": "80–200 字",
    },
    "douyin": {
        "tone": "前 3 秒给冲突或结果，口语到底",
        "sentences": "一句一个信息点，别念稿",
        "openers": ["先看结果：<演示>", "这东西我找了三年，今天有人做出来了"],
        "taboos": ["开头 logo 动画", "报备外私单", "虚假演示"],
        "memes": "平台热梗可用，但要跟产品相关",
        "length": "15–60 秒",
    },
    "bilibili": {
        "tone": "可以长、可以讲原理，但要密度高；UP 主口吻",
        "sentences": "分段清晰，有章节感",
        "openers": ["这期讲清楚 <问题>，看完你能自己动手",
                    "我用了 20 小时踩完坑，这期给你 20 分钟看完"],
        "taboos": ["搬运", "恰饭不标注", "水文凑时长"],
        "memes": "B 站梗可用，但别堆砌",
        "length": "5–15 分钟",
    },
    "juejin": {
        "tone": "实践派：真实项目 + 数据 + 踩坑",
        "sentences": "先给结构，再给代码",
        "openers": ["背景：<项目>，问题：<具体>，方案：<三步>",
                    "这篇记录一次线上问题排查，结论在最后",
                    "先给结论：<判断>，下面是完整的排查过程和代码"],
        "taboos": ["转载拼凑", "互赞群", "纯理论无实践"],
        "memes": "技术圈梗少量",
        "length": "1500–4000 字",
    },
    "seo": {
        "tone": "不存在的语气——读者只看有没有答案",
        "sentences": "第一屏给结论或可复制命令",
        "openers": ["直接给答案：<命令>，下面解释为什么",
                    "如果你遇到 <错误>，原因通常是这三条"],
        "taboos": ["开头铺垫行业背景", "批量 AI 生成页面", "隐藏文字"],
        "memes": "不需要",
        "length": "800–2000 字",
    },
}


VOICE_OVERRIDES.update({
    "bilibili": {
        "tone": "可以长、可以讲原理，但要密度高；UP 主口吻，不端着",
        "sentences": "分段清晰，有章节感；开头先把这期能学到什么说清楚",
        "openers": ["这期讲清楚 <问题>，看完你能自己动手",
                    "我用了 20 小时踩完坑，这期给你 20 分钟看完",
                    "先说结论：<判断>，后面是完整过程"],
        "taboos": ["搬运", "恰饭不标注", "水文凑时长"],
        "memes": "B 站梗可用，但别堆砌",
        "length": "5–15 分钟；脚本 2000–5000 字",
    },
    "jike": {
        "tone": "像发朋友圈，松弛、具体、可以自嘲；这个圈子小，装会被记住",
        "sentences": "短句，一条说一件事",
        "openers": ["做了个东西，自己先用了一周", "今天被人问到一个好问题：<问题>",
                    "踩坑记录：<具体>"],
        "taboos": ["一上来发产品链接", "只发不互动", "通篇营销腔"],
        "memes": "「即友」语境里的表达可以用，但别硬凹",
        "length": "50–200 字",
    },
    "sspai": {
        "tone": "深度、克制、有评测感；编辑会改稿，所以要经得起读",
        "sentences": "结构清楚：场景→问题→方案→局限",
        "openers": ["这篇文章想回答一个问题：<问题>",
                    "用了三个月之后，我对 <工具> 的看法变了",
                    "如果你也在 <场景> 里，这篇可能省你一周"],
        "taboos": ["通篇产品软文", "没有实测数据", "图片版权不清"],
        "memes": "数码圈的说法可以用，要准确",
        "length": "2000–5000 字",
    },
    "zhishixingqiu": {
        "tone": "对已经付钱的人说话：给判断，不给资料",
        "sentences": "第一人称，周更节奏，每条要有结论",
        "openers": ["这周我试了 <东西>，结论是 <判断>",
                    "有个成员问 <问题>，我的答案是 <结论>，理由三条",
                    "本月复盘：<三个数字>，下一步改 <一件事>"],
        "taboos": ["把公开内容原样搬进来", "收了钱不更新", "只发资料不发判断"],
        "memes": "不需要",
        "length": "300–800 字/条",
    },
    "wechat": {
        "tone": "写给订阅者，像一封信；标题决定打开率，正文决定转发",
        "sentences": "段落短，每段一个意思；小标题清楚",
        "openers": ["这件事我想了很久，今天写清楚",
                    "如果你也在 <场景> 里卡住，这篇是我踩完坑的总结",
                    "先说结论：<判断>。下面是三条理由。",
                    "发完别急着关，去搜一搜搜一下标题，看有没有被收录"],
        "taboos": ["诱导关注领资料", "正文直接引导加个人微信", "标题党"],
        "memes": "不需要",
        "length": "1500–3000 字",
    },
    "douyin": {
        "tone": "前 3 秒给冲突或结果，全程口语，不念稿",
        "sentences": "一句一个信息点；字幕必须加",
        "openers": ["先看结果：<演示>", "这东西我找了三年，今天有人做出来了",
                    "别划走，三分钟省你三天"],
        "taboos": ["开头 logo 动画", "报备外私单", "虚假演示"],
        "memes": "平台热梗可用，但要跟产品相关",
        "length": "15–60 秒",
    },
    "channels": {
        "tone": "熟人转发的语气：这条对我有用，所以转给你",
        "sentences": "口播、短、有结论",
        "openers": ["如果你也在 <场景>，这条值得看", "一分钟讲清楚 <问题>",
                    "我们踩了个坑，分享出来免得你也踩"],
        "taboos": ["没有私域就想靠算法", "通篇广告", "长视频搬过来不改"],
        "memes": "不需要",
        "length": "30–90 秒",
    },
    "instagram": {
        "tone": "视觉先行，文字只做补充；一句能记住就够",
        "sentences": "极短；前 3 秒画面定生死",
        "openers": ["Watch this before you <做某事>", "We tried it for 30 days. Here's the result.",
                    "This took 3 iterations. The last one worked."],
        "taboos": ["第三方自动化工具", "虚假互动", "图文无关"],
        "memes": "平台热梗可用，但要贴产品",
        "length": "15–45 秒 / 图注 80 字内",
    },
    "tiktok": {
        "tone": "钩子、快剪、有反差；工具类基本不适用",
        "sentences": "一句一镜，别解释",
        "openers": ["POV: <场景>", "Wait for it…", "Nobody talks about this part of <事>"],
        "taboos": ["买量", "虚假互动", "搬运"],
        "memes": "平台梗要快，过时就俗",
        "length": "15–30 秒",
    },
    "youtube": {
        "tone": "教程口吻：先给结果，再带步骤；标题写清能不能学会",
        "sentences": "分段、章节化；每段开头说这段解决什么",
        "openers": ["By the end of this video you'll have <结果>",
                    "I spent a week figuring this out so you don't have to",
                    "Three ways to do <事>; the third one is what I actually use"],
        "taboos": ["标题党", "通篇宣传片", "搬运"],
        "memes": "不需要",
        "length": "5–15 分钟",
    },
    "stack-overflow": {
        "tone": "给能跑的答案，不寒暄；第一行就是结论",
        "sentences": "结论 → 最小可运行代码 → 版本差异",
        "openers": ["Short answer: <结论>. Minimal repro below.",
                    "This changed in v3: <差异>",
                    "You can't do that directly; the workaround is <做法>"],
        "taboos": ["答案里贴产品链接", "自问自答推广", "不写版本号"],
        "memes": "不需要",
        "length": "100–500 字",
    },
    "discord": {
        "tone": "聊天室语气，短、随意、不用敬语",
        "sentences": "一两句话；不要一次发一大段",
        "openers": ["same problem here, fixed it by <做法>", "quick q: does this work with <场景>?",
                    "if anyone's stuck on this, the fix is <一句话>"],
        "taboos": ["进群发链接", "私信群成员", "在技术频道推销"],
        "memes": "群内梗要看懂再用",
        "length": "20–80 字",
    },
    "bluesky": {
        "tone": "技术人的公开笔记本：短、有观点、随时可以吵",
        "sentences": "短句换行，不用 hashtag 堆砌",
        "openers": ["built a thing that <做什么>. it's 200 lines and probably wrong.",
                    "hot take: <观点>",
                    "we ran the same prompt through 5 providers. 3 of them <结果>."],
        "taboos": ["跨实例批量刷同一条", "营销腔", "只发链接"],
        "memes": "技术圈梗少量",
        "length": "60–250 字",
    },
    "kickstarter": {
        "tone": "讲一个必须被做出来的东西，讲团队和风险",
        "sentences": "先讲为什么，再讲是什么，最后讲风险",
        "openers": ["We built this because <真实原因>", "Here's the prototype. It works. Here's what's left.",
                    "Why now: <时机>"],
        "taboos": ["无列表裸发", "隐瞒风险", "夸大交付"],
        "memes": "不需要",
        "length": "页面 800–1500 字；视频 90 秒",
    },
    "newsletter": {
        "tone": "一句话说清「这跟你有什么关系」，编辑只读第一句",
        "sentences": "结论 → 谁在用 → 和现有方案的区别 → 链接",
        "openers": ["<一句话>：它解决 <问题>", "<产品> turns <X> into <Y> in one command",
                    "We <做了什么>; here's the one-line why."],
        "taboos": ["公司介绍开头", "形容词堆砌", "通篇无限制条件"],
        "memes": "不需要",
        "length": "80–150 字",
    },
    "directory-sites": {
        "tone": "采购者视角：适用谁、限制、价格",
        "sentences": "短条目，能量化就量化",
        "openers": ["适合 <规模> 的 <角色>，不适合 <场景>",
                    "把 <流程> 从 <X> 压到 <Y>",
                    "免费层够用，付费从 <价格> 起"],
        "taboos": ["堆功能不写结果", "不标价格", "所有竞品都不如自己"],
        "memes": "不需要",
        "length": "200–500 字",
    },
    "weibo": {
        "tone": "短、有态度、跟着热点；工具类别硬蹭",
        "sentences": "一两句话说清，配图",
        "openers": ["关于 <热点>，说个大部分人没注意的点", "实测：<结论>，数据在评论里",
                    "这条不是广告，就是把话说清楚"],
        "taboos": ["买热搜", "水军转发", "通篇产品广告"],
        "memes": "热点梗要快，晚了就俗",
        "length": "80–200 字",
    },
    "douban": {
        "tone": "文青社区，讲究真诚和具体；广告是公敌",
        "sentences": "平实叙述，不用感叹号",
        "openers": ["用了半年，说说真实感受", "小组里看到有人问这个，我的答案可能不主流",
                    "先说缺点：<具体>"],
        "taboos": ["小组发广告", "营销腔", "水军"],
        "memes": "小组内梗可用，别硬凹",
        "length": "200–800 字",
    },
    "tieba": {
        "tone": "吧内熟人语境，口语、直接",
        "sentences": "短，带表情符号可以",
        "openers": ["吧里有人问这个，统一回一下", "用了段时间，说点实话"],
        "taboos": ["发广告（秒删）", "挖坟", "小号顶帖"],
        "memes": "吧内梗可用",
        "length": "50–200 字",
    },
    "csdn": {
        "tone": "技术搬运与原创混杂，读者要能直接抄",
        "sentences": "先给完整代码，再解释",
        "openers": ["直接上代码，后面解释", "踩坑记录：<错误> 的三种解法"],
        "taboos": ["搬运洗稿", "只贴错误截图", "标题党"],
        "memes": "不需要",
        "length": "800–3000 字",
    },
    "36kr": {
        "tone": "商业媒体语气：事件、数据、判断，不要第一人称抒情",
        "sentences": "导语给结论，正文分层",
        "openers": ["<公司/产品> 做了 <事>，这意味着 <判断>", "一组数据：<数字>，背后是 <原因>"],
        "taboos": ["软文冒充报道", "无信源数字", "通篇宣传"],
        "memes": "不需要",
        "length": "1000–2500 字",
    },
    "maimai": {
        "tone": "职场语境：专业、克制、不带情绪",
        "sentences": "先给观察，再给判断",
        "openers": ["观察到一个现象：<具体>，我的理解是 <判断>", "这个问题我们内部讨论过，结论是 <判断>"],
        "taboos": ["匿名区爆料式营销", "通篇产品推广"],
        "memes": "职场梗少量",
        "length": "100–300 字",
    },
    "taobao": {
        "tone": "买家视角：解决什么、保不保修、多少钱",
        "sentences": "短句，参数化",
        "openers": ["这款解决 <具体问题>，不适合 <场景>", "同价位里它的 <优势> 是 <具体>"],
        "taboos": ["刷单", "虚假评价", "夸大功效"],
        "memes": "不需要",
        "length": "详情页 300–800 字",
    },
    "kuaishou": {
        "tone": "接地气、直接、讲实惠",
        "sentences": "一句话说清好处",
        "openers": ["这东西帮你省 <时间/钱>", "我用了一个月，说说实话"],
        "taboos": ["搬运", "刷量", "夸大"],
        "memes": "平台梗可用",
        "length": "15–60 秒",
    },
    "product-hunt": {
        "tone": "为「发现新品的人」写：一句话说清是给谁做的",
        "sentences": "Tagline 一句话；描述三段：给谁、解决什么、和谁不一样",
        "openers": ["<X> for <谁> — 一句话说清做什么", "We built <X> because <真实原因>",
                    "Free to start. No signup needed to see the demo."],
        "taboos": ["求票", "通篇功能列表", "页面出现价格数字"],
        "memes": "不需要",
        "length": "Tagline ≤60 字符；描述 150 字",
    },
})


VOICE_OVERRIDES.update({
    "x": {
        "tone": "有观点、口语、短；自嘲可以，自夸不行",
        "sentences": "一句话一个意思，多换行",
        "openers": ["spent 3 days chasing a bug that turned out to be a 2-char typo. i'm fine.",
                    "we ran the same prompt through 5 providers. 3 of them double-count on retry.",
                    "hot take: most 'AI agent' demos are a for-loop with extra steps. i say this as someone who built one."],
        "taboos": ["每条都带产品链接", "求关注", "一天发十几条", "永远积极"],
        "memes": "技术圈梗可用，过气梗反效果",
        "length": "60–200 字",
    },
    "linkedin": {
        "tone": "职业语境：克制、有数据、不煽情；模板化一眼被认出",
        "sentences": "一段一个结论，少用感叹号",
        "openers": ["过去三个月我们把 <流程> 从 <X> 压到 <Y>，三个做法：",
                    "一个观察：<现象>。我理解的原因是 <判断>。",
                    "不推销，分享一份我们内部在用的 <清单>。"],
        "taboos": ["第三方自动化工具", "模板群发", "通篇励志", "连接后立刻推销"],
        "memes": "不需要",
        "length": "150–400 字",
    },
    "indie-hackers": {
        "tone": "独立开发者之间：带数字、带失败、不吹",
        "sentences": "先给数字，再讲为什么",
        "openers": ["Month 3: <数字> MRR, <数字> users. What worked and what didn't.",
                    "I killed my first idea after 6 weeks. Here's what I'd do differently.",
                    "Sharing numbers because nobody else does: <数字>."],
        "taboos": ["通稿式发布", "只报喜不报忧", "没有数字"],
        "memes": "build in public 的梗可以用",
        "length": "200–600 字",
    },
})


def resolve(pid: str) -> str:
    """允许传中文名：「小红书」和「xiaohongshu」都要能用。"""
    from .platforms import BY_ID, PLATFORMS
    if pid in BY_ID:
        return pid
    for p in PLATFORMS:
        if pid == p["name"] or pid in p.get("aliases", ()):
            return p["id"]
    return pid


def _kind_of(pid: str) -> str:
    from .platforms import BY_ID
    p = BY_ID.get(resolve(pid))
    return (p or {}).get("kind", "社区")


def profile(pid: str) -> Dict[str, Any]:
    """这个平台的语气画像。缺省用渠道类型的通性兜底。"""
    pid = resolve(pid)
    kind = _kind_of(pid)
    base = dict(KIND_VOICE.get(kind, KIND_VOICE["社区"]))
    base.update(VOICE_OVERRIDES.get(pid, {}))
    # 每个平台至少三条开头，缺的用同类型的补，避免「没有建议」
    fallback_openers = KIND_VOICE.get(kind, KIND_VOICE["社区"])["openers"]
    openers = list(base.get("openers") or [])
    for o in fallback_openers:
        if len(openers) >= 3:
            break
        if o not in openers:
            openers.append(o)
    base["openers"] = openers[:5]
    base["platform"] = pid
    base["kind"] = kind
    return base


def coverage() -> Dict[str, Any]:
    """哪些平台有专门画像、哪些只靠类型兜底。手册更新后这里会显示缺口。"""
    from .platforms import PLATFORMS
    bespoke = [p["id"] for p in PLATFORMS if p["id"] in VOICE_OVERRIDES]
    fallback = [p["id"] for p in PLATFORMS if p["id"] not in VOICE_OVERRIDES]
    return {"bespoke": bespoke, "fallback": fallback,
            "ratio": round(len(bespoke) / max(len(PLATFORMS), 1), 2)}


def draft(pid: str, *, hook: str = "", what: str = "", ask: str = "") -> Dict[str, Any]:
    """给三个开头骨架。全文必须人写——这里只解决「第一句说什么」。"""
    pid = resolve(pid)
    v = profile(pid)
    out = []
    for tpl in v["openers"][:3]:
        text = tpl
        if hook:
            text = text.replace("<具体>", hook).replace("<细节>", hook)
        if what:
            text = text.replace("<你做了什么>", what).replace("<东西>", what)
        if ask:
            text = text.replace("<判断>", ask)
        out.append({"opener": text, "tone": v["tone"], "length": v["length"]})
    return {"platform": pid, "tone": v["tone"], "sentences": v["sentences"],
            "drafts": out, "taboos": v["taboos"], "memes": v["memes"]}


# 全局营销腔：任何平台出现这些词都判失败
MARKETING = ("颠覆", "革命性", "极致", "赋能", "闭环", "抓手", "全面提升",
             "行业领先", "一站式解决方案", "深度赋能", "降本增效")
# 留联系方式：内容平台的红线
CONTACT = re.compile(r"(微信|vx|VX|加我|私我|公众号[：:]|↓↓↓|戳链接)")

# 改写可以换语气，不能悄悄丢掉数字、链接或利益关系披露。这里做的是
# 可解释的保守检查，不把关键词匹配伪装成完整的事实推理。
URL_FACT = re.compile(r"https?://[^\s<>\"'）】]+", re.I)
NUMBER_FACT = re.compile(
    r"(?<![A-Za-z0-9_])v?\d+(?:\.\d+){0,3}"
    r"(?:\s?(?:%|％|天|日|周|个月|月|年|小时|分钟|秒|元|块|万|千|倍|人|条|篇|次|个|字|"
    r"lines?|users?|days?|hours?|minutes?|seconds?))?", re.I)
DISCLOSURE_PATTERNS = {
    "作者/产品关系": re.compile(
        r"(?:disclosure\s*:|利益(?:相关|披露)|关系披露|我是.{0,30}(?:作者|开发者|维护者)|"
        r"\bI\s+(?:built|build|maintain|work\s+on)\b|\bwe\s+(?:built|build|maintain)\b)", re.I),
    "商业合作": re.compile(
        r"(?:\bsponsored\b|\baffiliate\b|广告合作|商业合作|付费合作|推广合作|赞助|恰饭|含推广)", re.I),
    "免费样品/试用": re.compile(r"(?:free\s+(?:sample|review\s+unit)|免费(?:样品|试用)|产品由.{0,20}提供)", re.I),
}


def _normalized_text(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower().replace("％", "%")


def extract_facts(text: str) -> List[str]:
    """Extract checkable numeric and URL facts, excluding digits inside URLs."""
    urls = [x.rstrip(".,;，。；") for x in URL_FACT.findall(text or "")]
    without_urls = URL_FACT.sub(" ", text or "")
    numbers = [re.sub(r"\s+", "", x) for x in NUMBER_FACT.findall(without_urls)]
    return sorted(set(urls + numbers), key=lambda x: (x not in urls, x))


def disclosure_markers(text: str) -> List[str]:
    return sorted(name for name, pattern in DISCLOSURE_PATTERNS.items() if pattern.search(text or ""))


def check_preservation(source_text: str, candidate_text: str, *,
                       facts: Optional[List[str]] = None,
                       disclosures: Optional[List[str]] = None) -> Dict[str, Any]:
    """Check that a rewrite retains source facts and disclosures.

    ``facts``/``disclosures`` can provide an editorial checklist when a fact is
    not numeric or a disclosure uses wording outside the built-in markers.
    """
    candidate = _normalized_text(candidate_text)
    checked_facts = list(dict.fromkeys(facts)) if facts is not None else extract_facts(source_text)
    missing_facts = [f for f in checked_facts if _normalized_text(f) not in candidate]
    explicit = list(dict.fromkeys(disclosures or []))
    missing_explicit = [d for d in explicit if _normalized_text(d) not in candidate]
    source_groups = set(disclosure_markers(source_text))
    candidate_groups = set(disclosure_markers(candidate_text))
    missing_groups = sorted(source_groups - candidate_groups)
    missing_disclosures = missing_explicit + [f"{x}披露" for x in missing_groups]
    issues = [f"事实未保持：缺少 {f}" for f in missing_facts]
    issues += [f"披露未保持：缺少 {d}" for d in missing_disclosures]
    return {
        "ok": not issues,
        "issues": issues,
        "checked_facts": checked_facts,
        "missing_facts": missing_facts,
        "checked_disclosures": explicit + sorted(source_groups),
        "missing_disclosures": missing_disclosures,
    }


def preservation_lint(source_text: str, candidate_text: str, *,
                      facts: Optional[List[str]] = None,
                      disclosures: Optional[List[str]] = None) -> List[str]:
    return check_preservation(source_text, candidate_text, facts=facts,
                              disclosures=disclosures)["issues"]


def lint(pid: str, text: str, *, source_text: Optional[str] = None,
         facts: Optional[List[str]] = None,
         disclosures: Optional[List[str]] = None) -> List[str]:
    """这段文字在这个平台会不会被当成广告。返回问题列表，空 = 通过。"""
    pid = resolve(pid)
    v = profile(pid)
    problems: List[str] = []
    low = text or ""

    for bad in MARKETING:
        if bad in low:
            problems.append(f"「{bad}」是广告词，这个平台读得出来")
    for taboo in v["taboos"]:
        key = taboo.split("（")[0]
        if key and key in low:
            problems.append(f"踩到这个平台的禁忌：{taboo}")
    if pid in ("xiaohongshu", "zhihu", "wechat", "jike", "v2ex") and CONTACT.search(low):
        problems.append("正文里出现联系方式或导流词，这条会被限流")
    if pid in ("hackernews", "github") and re.search(r"[🚀🔥💡✨]", low):
        problems.append("这两个平台不用 emoji")
    if low.count("!") + low.count("！") > 1:
        problems.append("感叹号超过一个，读起来像广告")
    limit = {"hackernews": 300, "email": 300, "reddit": 300, "v2ex": 500}.get(pid)
    if limit and len(low) > limit * 2:
        problems.append(f"这个平台超过 {limit * 2} 字基本没人读完")
    if source_text is not None or facts is not None or disclosures is not None:
        problems.extend(preservation_lint(source_text or "", low, facts=facts,
                                          disclosures=disclosures))
    return problems


def render(pid: str) -> str:
    pid = resolve(pid)
    v = profile(pid)
    lines = [f"## {pid} 的语气", "",
             f"- 语气：{v['tone']}",
             f"- 句式：{v['sentences']}",
             f"- 长度：{v['length']}",
             f"- 黑话：{v['memes']}",
             "", "**开头可以这样写：**"]
    for o in v["openers"][:3]:
        lines.append(f"- {o}")
    lines.append("")
    lines.append("**别这么写：**")
    for t in v["taboos"]:
        lines.append(f"- {t}")
    try:
        from . import experience
        current = experience.eligible(pid)
        if current:
            lines.append("")
            lines.append(f"> 当前有 {len(current)} 条通过资格与新鲜度校验的经验，"
                         f"见 `docs/playbooks/经验库/{pid}.md`；语气只是外壳，实操细节在那。")
        elif experience.for_platform(pid):
            lines.append("")
            lines.append("> 旧经验语料尚未通过资格或新鲜度校验，只能用于复核/生成实验，"
                         "不能写成这个平台的当前打法。")
    except Exception:
        pass
    return "\n".join(lines)


def current_experience(pid: str, limit: int = 6) -> List[Dict[str, Any]]:
    """Only current eligible experience may be presented as a current playbook."""
    from . import experience
    return experience.eligible(resolve(pid))[:limit]
