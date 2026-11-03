# Reddit

> 一句话：由百万个自治王国组成的联盟——全站规则只是底线，每个 sub 有自己的法律，"发广告"是最普遍的死因。 · 研究日期：2026-11-03 · 官方口径：10:1 自我推广指南与站点规则已核（原文直引） · **自家实测：一次翻车**（Verovo 2026-10：r/SaaS 被 AutoModerator 判 AI 删帖）

## 1. 这里是什么地方

**谁在这里**：匿名或半匿名的兴趣社群用户；技术 subs（r/selfhosted、r/devops、r/SideProject）密度高、专业度高、对推销的敌意也最高。

**他们来干什么**：讨论具体问题、求推荐、吐槽；"advice thread"文化下，真实的经验分享被极度推崇，营销话术被瞬间识破。

**在 GTM 里的角色**：**长尾信任 + 客群发现**。不是发射平台（去 PH/HN），是"在别人讨论你品类的问题里留下好答案"的地方。Verovo 复盘的对照结论：Reddit AI 生成内容被判死，但深度参与讨论未被探索完 [自家复盘 2026-10-31]。

## 2. 内容怎么被看见

- **分发靠什么**：sub 内投票排序（up/down）+ 热度时间衰减 + 每个 sub 独立的版规（有的只允许链接、有的禁止图片、有的每日限定主题帖）。**没有全站算法推流**——一个 sub 就是一个独立世界。
- **什么内容会被顶**：真实经历、详细复盘、直接回答问题的干货。标题写清楚"做了什么、结果如何"。
- **曝光持续**：帖子 24–48 小时后基本沉底，但 **Google 搜索 Reddit 帖子的权重极高**——"best XX tool reddit" 是海外用户的固定搜索动作，老帖长期被搜到。
- **新手初始曝光**：新号发帖会先过 AutoModerator——低 karma/新账号在多数大 subs 会被自动过滤（门槛数值各 sub 自定且常不公开，[r/NewToReddit 讨论](https://www.reddit.com/r/NewToReddit/comments/xyul3r/)，**官方未公布统一数值**）。

## 3. 官方规矩

**① 自我推广 10:1 指南（官方原文直引，已核）**："**For every 1 time you post self-promotional content, 9 other posts (submissions or comments) should not contain self-promotional content.**"（Reddit 管理员在 [r/modnews 的官方澄清](https://www.reddit.com/r/modnews/comments/2oamgp/moderators_clarifications_around_our_101)）。配套官方指南：[Self-Promotion on Reddit 官方 wiki](https://www.reddit.com/r/reddit.com/wiki/selfpromotion)、[站点规则 redditinc.com/policies/reddit-rules](https://redditinc.com/policies/reddit-rules)。**注意：这是全站指南，各 sub 执行更严**——很多技术 sub 是 0 容忍（直接禁自我推广）。

**② 透明度要求**：与产品相关时披露利益关系（"I built this"）；假装无关用户推荐自己是最重违规之一。[官方指南口径 + 各 sub 明文]

**③ AI 内容（2024–2025 收紧）**：大量 subs 在 2024–2025 年新增 AI 内容条款——**要求明确标注或直接禁止**，未披露 AI 内容可能永封（[sub 规则更新实例](https://www.reddit.com/r/childrensbooks/comments/1n7c38k/rule_update_ai_content_selfpromo_welcoming_new)、[各 sub 政策梳理](https://okara.ai/blog/subreddits-that-allow-self-promotion)，**第三方梳理**）。

**④ 违规后果**：sub 级（删帖/临时禁言/永封）+ 站点级（shadowban——你的内容别人全部看不见，且**不通知你**）。

## 4. 真实打法（按产品类型）

**开发者工具（本平台最强场景）**

1. **第一步：读规则，不发帖**。目标 sub 的 sidebar 规则 + pinned 帖 + 最近一周被删帖的模式（看 mod log 类公开信息）。**判断"这个 sub 允不允许自我推广"比写内容更重要**。
2. **账号**：一个有历史的真实账号（karma 100+、账号数月起 [各 sub 门槛不一]）。先在目标 sub 当两周"居民"：答问题、参与讨论，**攒 9 份非推广贡献**再考虑 1 次提及产品（对齐 10:1）。
3. **发什么**：r/SideProject、r/IndieDev 这类允许展示的 sub 发"show"帖（build in public 叙事）；专业 subs（r/devops、r/selfhosted）只在别人求助帖里以"我做了个工具解决这个，免费版在这"的方式回应——**且必须披露 maker 身份**。
4. **见效周期**：4–8 周居民期 + 长尾搜索收益持续数月。失败的样子：新号首帖即产品链接 → AutoMod 静默过滤或秒删，你看不到任何数据却以为"没人感兴趣"。
5. **自家翻车对照**：Verovo 2026-10 在 r/SaaS 发 AI 生成内容，**被 AutoModerator 判 AI 直接删除**（[自家 GTM 全周期复盘 2026-10-31]）——结论：Reddit 的机器判 AI 比 HN 更早拦你，低 karma + AI 文 = 双重死因。**红线：Reddit 文本人写**。

**SaaS / 效率工具**：搜"alternatives to XX"类求助帖；答对比问题给真实表格（含竞品），不吹自己。

**消费品**：只有相关 lifestyle subs 且规则允许时；否则不投。

**一人公司**：build in public 帖（收入透明、技术栈）在 r/SideProject/r/EntrepreneurRideAlong 有成熟文化。

## 5. 死路

1. **新号直接发产品链接**：AutoMod 静默过滤/秒删——你甚至不知道自己被拦。
2. **AI 生成内容不披露**：2024–25 各 sub 新规的打击对象，永封先例普遍（§3③）；自家 r/SaaS 事故实证（§4）。
3. **同一文案跨 sub 群发**：跨 sub 重复发帖是 spam 判定特征（站点规则），mod 之间有沟通渠道。
4. **买 karma / 投票**：站点反作弊检测，shadowban 风险。
5. **把 mod 当客服吵架**：被 ban 后据理力争只会确认封禁；申诉讲事实认错才可能解。

## 6. 这里的人怎么说话

- **语气**：直白、不耐烦客套、欣赏自嘲。"throwaway account" 文化说明用户重视真实高于形象。
- **格式**：段落正文 + 编辑记录（edit: typos）；长帖用 TL;DR 开头；链接给上下文说明而非裸链。
- **被尊重的表达**：披露利益关系、承认产品缺点、回答批评时给数据、"I might be wrong" 的姿态。
- **一眼营销的信号**：账号历史全是产品相关、话术完美无缺点、首帖即链接、把 sub 名字拼错（真的会发生）。
- **忌讳**：upvote 求助（"upvote for visibility" 违规）、复制粘贴回复、DM 群发（多数 sub 明禁，站点也限）。
- **好的开头长什么样**（按高赞模式还原，[非逐字引用]）：
  - "I built a tool over 6 months to solve my own problem with X. It's free/open-source, here's what it does and doesn't do. Happy to answer anything."
  - "We tried 4 tools for X and here's our honest comparison table. Disclosure: I make one of them, marked in the table."
  - "Long-time lurker here. Finally hitting the problem this sub talks about — here's what we did about it."

## 7. 账号与节奏

- **新号先养**：这不是"刷数据"而是真实参与——答你专业领域的问题攒 karma。各 sub 门槛（10–100+ karma、数天到数月账号年龄）常不公开，发帖被静默移除就是信号。
- **频率**：日常参与无上限感（真实互动）；**产品提及严格遵守 10:1 甚至更低频**。
- **时段**：美国时间早 6–9 点（帖子黄金期，北京时间 21–24 点错峰对应美东清晨）[**第三方共识**]。
- **账号资产**：karma（发帖门槛）、账号年龄、sub 内声望（部分 sub 有 flair/积分体系）。

## 8. 风控与踩线

- **平台怎么识别**（已知）：AutoModerator 关键词/链接/账号年龄 karma 规则（各 sub 自配）；站点反作弊（投票/跨 sub spam 模式）；部分 sub 用 AI 检测器查 AI 文（自家事故实证）。
- **shadowban 识别**：发帖到 [r/ShadowBan](https://www.reddit.com/r/ShadowBan) 由 AutoMod 自动检测；或用检查工具（[RedditMaster](https://www.redditmaster.com/tools/shadowban-checker)）；个人主页显示账号年龄为 "-" 是典型信号（[社区案例](https://www.reddit.com/r/ShadowBan/comments/1wnbtao/)）。
- **被处理了怎么办**：站点级在 [reddit.com/appeals](https://www.reddit.com/appeals) 申诉（社区经验：申诉里说明账号年龄/karma 有助快速解封，[案例](https://www.reddit.com/r/ShadowBan/comments/1wej3ed/i_mentioned_the_age_and_karma_of_my_account_in_my)）；sub 级给 mod team 发 modmail 认错问期限。**更糟的动作**：换号重来（关联封禁）、继续同款发帖（从 sub ban 升级到站点 ban）。

## 9. 怎么衡量有用

- **能拿到**：帖分（upvote 净值）、评论、个人 karma；帖内链接出站点击（自建 UTM）。
- **拿不到**：sub 级阅读量（Reddit 不给曝光数据）；被 AutoMod 拦掉的帖子你什么数据都看不到——**零数据≠零曝光，可能是零触达**。
- **什么算"被问到"**：评论里"link?"、"does it do X?"、私信咨询。
- **常见误读**：把被删当没人气（先查是不是被 AutoMod 拦了）；把一帖爆了当渠道跑通（Reddit 是长期居民游戏）。

## 10. 适不适合

- **适合**：开发者工具/自托管/开源产品；有真实用户问题可以答的团队；能接受 4–8 周无产出的"居民期"纪律。
- **不适合**：要快速脉冲的（去 PH/HN）；国内市场产品（用户不在）；无法人写英文内容的团队（AI 文=死路，自家实证）。
- **搭配**：Reddit 居民期（长尾信任）+ Google 搜索收录（Reddit 帖搜索权重高，双倍长尾）；与 HN/PH 错峰。MCP 侧：Growovo 的 reddit 渠道为 UNVERIFIED，首跑必须人工盯跑。

---

## 附：交接单六问自检

| 交接单的问题 | 在哪回答 |
|---|---|
| 1. 全站内容政策、AI 态度 | §3 ③（站点规则已核 + 2024–25 各 sub AI 新政收紧，第三方梳理已标注） |
| 2. 9:1 规则现状 | §3 ①（官方原文直引 + "各 sub 更严"的现实） |
| 3. AutoMod 常见门槛 | §2 + §7（karma/年龄门槛各 sub 自定常不公开，已标注） |
| 4. 怎么判断 sub 的规矩 | §4 第 1 步（读 sidebar/pinned/删帖模式） |
| 5. shadowban 识别与恢复 | §8（r/ShadowBan 检测 + appeals 申诉路径，带链接） |
| 6. devtool subs 真实获客/翻车案例 | §4 第 5 条（**自家 r/SaaS 被 AutoMod 判 AI 删帖实证**）+ 打法框架 |
