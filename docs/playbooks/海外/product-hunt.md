# Product Hunt

> 一句话：海外新产品的发射台——一天定胜负的加权投票赛：一票不等于一分，老账号的票更重，买票会被整批作废。 · 研究日期：2026-11-03 · 官方口径：Launch Guide 与帮助中心已核（原文直引） · **自家实测：无（Verovo 发射周 2026-11-09~13 首战）**

## 1. 这里是什么地方

**谁在这里**：早期采用者、独立开发者、投资人、产品经理——一群专门来"发现新东西"的人，猎奇心态强、给反馈直接。

**他们来干什么**：每天逛首页看今天有什么新产品；给有意思的投票、评论；好产品被顶上"当日第一"（Product of the Day）。

**在 GTM 里的角色**：**脉冲发射**。一次性大流量入口，配合 HN/blog 形成"发射周"；不是日常运营平台。平台大盘：launch 数量比 2019 涨 32%（2021 峰值 12,137 个），但单品平均票数比 2017 峰值跌 47%（[76,822 个 launch 的数据分析](https://blog.getdot.ai/dot-digest-has-product-hunt-peaked-ai-data-analysis-of-76-822-launches-fcee6542fd00)）——发射台在变挤，单发效应在变弱。

## 2. 内容怎么被看见

- **分发靠什么**：当日排名（加权票数动态更新）+ 编辑推荐 + 关注者通知。**官方帮助中心（2025-02 更新）："One upvote does not always equal one point"——票是加权的，不是计数的**（[官方排名说明](https://help.producthunt.com/en/articles/484938-how-is-the-homepage-ranked)，**已核**；[积分说明](https://help.producthunt.com/en/articles/10275873-what-are-points)）。
- **谁的票重**：活跃老账号 > 新注册账号；新号票可能部分不计入（页面显示票数可比实际计分高 10–30%）（[signals.sh 分析](https://signals.sh)，**第三方逆向**）。
- **什么时段**：排名按太平洋时间每日重置（0:01 PT）；发布时间自选，常见建议 00:01–2:00 PT 开跑抢满一天窗口。[**第三方共识**]
- **曝光持续**：当日为主；进 Top 5 会有次日邮件摘要二次曝光；长尾几乎为零（不同于知乎/掘金）。

## 3. 官方规矩

**① 唯一铁律（Launch Guide 原文直引，已核）**："**The only real rule here is that you cannot ask people directly to upvote your product. Instead, ask them to visit and comment.**"——不能直接求票；可以请大家访问、评论、给反馈、分享给相关的人（[官方 Launch Guide](https://www.producthunt.com/launch)）。

**② 买票/操纵的后果**：检测到操纵时，PH 可以作废可疑票——**连带着作废部分真票**，甚至取消排名资格（[Tom Dekan 指南](https://tomdekan.com/articles/product-hunt-launch-guide)、[Nevo David 三次 launch 经验](https://www.linkedin.com/posts/nevo-david_launched-postiz-3-times-on-product-hunt-activity-7391138567881723905-qQuz)，**第三方经验**）。平台用机器学习模型分析投票者社交图谱识别投票环（**第三方逆向**）。
- **花钱请人 hunt 也属违规**（[Medium 教训帖](https://medium.com/@lachlan.chavasse/product-hunt-launch-guide-what-not-to-do-lessons-from-100-upvotes-a120320155e3)）。

**③ 关系披露**：maker 与产品关系公开（maker 身份标注）；团队成员以真实身份参与答评。[**平台惯例，帮助中心有相关说明**]

**④ hunter 机制现状**：自助发布已成主流，hunter 的算法加成已很小——其价值主要是自有粉丝触达和早期互动信号；**不需要花钱找 hunter**（[社区分析](https://dmkthinks.org/blog/how-we-reached-1-on-product-hunt)，**第三方**）。

## 4. 真实打法（按产品类型）

**面向早期采用者的新产品（本平台定义场景）**

1. **第一步**：发射前一周备齐——30 秒演示 GIF、首图、3 张真实案例卡、maker 故事页；提前在 PH 建好 maker 身份。
2. **账号**：个人真实账号（有历史活跃度更好）；新注册的空号连投票权重都低。
3. **发射日**：00:01 PT 发布 → 全天在线答评（每条评论都回）→ 通知自己的用户/邮件列表"来看看、给点反馈"（**注意话术：不能说"去投票"**）。
4. **见效周期与量级**：当日见分晓。真实数字参考：
   - Momen 拿当日 #1：launch 日约 **500 注册**、网站流量 +500%、一天内反链暴涨（[官方复盘](https://momen.app/blogs/how-momen-achieved-top-1-of-product-of-the-day)）；
   - Swytchcode 以 **400 票**拿当日 #1（[两周全流程复盘](https://www.producthunt.com/p/swytchcode/we-hit-1-product-of-the-day-sharing-our-full-2-week-launch-journey)）；
   - 平庸发射的教训：20–30 个真实活跃用户的支持就足以产生可见影响，但很多 launch 连这个都没有（[Reddit 复盘](https://www.reddit.com/r/SaaS/comments/1tjjuiv/what_we_learned_from_our_underwhelming)）。
5. **失败的样子**：上午发布下午才想起来答评；动员的人全是新注册小号（票不计分）；评论区零回复（排名算法看"真实互动"）。

**开发者工具**：与 HN Show HN 错开 1–2 天，互不抢量；PH 页放演示 GIF，HN 帖放技术细节。
**SaaS**：定价页必须清楚，PH 用户对"contact sales"反感。
**一人公司**：maker 故事是最强物料——"一个人做的"本身就是叙事点。

## 5. 死路

1. **直接求票**：官方唯一铁律，话术上"帮我去投一票"就是违规——说"来看看给点反馈"。群发模板私信求票同理。
2. **买票/投票环**：ML 社交图谱检测，连真票一起作废（§3②）。
3. **动员新注册小号**：票不计入排名分，白忙。
4. **发射日消失**：排名算法重视真实互动，零答评的 launch 排名快速下滑。
5. **把 PH 当持续获客渠道**：脉冲后长尾≈零；期待"每周发一次"的团队会失望（重复 launch 有重大更新间隔要求 [**未核实具体间隔**]）。

## 6. 这里的人怎么说话

- **语气**：直接、好奇、建设性吐槽。"Congrats on the launch!" 是礼仪开场，后面跟真问题。
- **格式**：首评（maker 自己的 comment）讲"为什么做这个 + 路线图"，用 bullet；答评快速、具体、承认不足。
- **被尊重的表达**：公开回应批评、给数据、认领 bug（"good catch, fixing today"）。
- **一眼营销的信号**：评论全是"Amazing product! 🚀"无实质、maker 只回赞美不回质疑、首评是广告文案。
- **忌讳**：假装普通用户夸自己（身份关联会被识破）、评论区刷屏。
- **好的首评长什么样**（按高排名 launch 模式还原，[非逐字引用]）：
  - "We built this because we kept hitting X problem ourselves. The interesting technical decision was Y. What's next: Z. AMA."
  - "Maker here. Honest limitations: it doesn't yet do A, and pricing may change after beta. Here's the 30s demo."

## 7. 账号与节奏

- **新号可发布**（自Serve），但**发射前账号要有基本活跃度**（关注、评论过别人）——既为票重也为社区观感。
- **频率**：一个产品一次 launch；重大更新再发（间隔要求未核实）。
- **时段**：00:01–02:00 PT（北京时间 16:01–18:00 左右，秋冬时区浮动）抢满窗口。
- **账号资产**：个人账号声望（票重）、过往 launch 记录、badge（Product of the Day 等）。

## 8. 风控与踩线

- **平台怎么识别**（已知）：加权计分过滤新号/低质票（官方）；ML 社交图谱识别购票与投票环（第三方逆向）；账号关联识别（推测）。
- **踩线信号**：票数涨但排名不动（票被作废）；收到官方警告；排名异常下滑。
- **被处理了怎么办**：邮件/support 申诉；**更糟的动作**：换号重发、继续动员小号（叠加违规）。

## 9. 怎么衡量有用

- **能拿到**：票数、评论数、排名、访问者点击（PH 分析面板）、badge。
- **拿不到**：PH → 注册的归因（除非产品端做 UTM：`?utm_source=producthunt`，**必做**）。
- **什么算"被问到"**：评论区出现"定价/集成/能不能做 X"；私信合作请求。
- **常见误读**：票数=排名（加权制，显示票数≠计分）；当日 #1=成功（Momen 的 500 注册才是口径，看自己产品的激活定义）；一次 launch=持续流量（脉冲即止）。

## 10. 适不适合

- **适合**：面向早期采用者/开发者/海外市场的新产品（首发或重大版本）；有演示 GIF 和英文物料能力；能全程在线答评一天。
- **不适合**：国内市场为主的产品（用户不在）；B2B 重销售流程产品（PH 用户是探索者不是采购）；没有英文内容产能的团队。
- **搭配**：PH 发射（脉冲）→ HN Show HN（错峰二次脉冲）→ dev.to/blog 长文（长尾）→ GitHub star 沉淀。**中国团队注意**：发布时段按 PT 计算，北京时间的傍晚到深夜是黄金答评期，反而友好；物料与答评必须全英文。[**时区已核，其余经验性**]

---

---

## 11. 发完之后怎么跟

launch 是一次性的，跟才是资产。PH 的特殊之处：**来的人当天就散**，所以必须当天把人接住。

- **场内**：launch 全天守评论（官方鼓励参与的正是这个）。每条当天回，别只说谢谢——问一句他的具体场景。
- **当晚必做**：把评论区里问过具体问题的人、以及在评论里出现的人，逐个记进对象台账（`mkt followup add`）。**这是 PH 唯一能带走的东西。**
- **第 3 天**：给说过"会试试"的人一句"试了吗，卡在哪"。不发促销。
- **第 7 天**：发一条"launch 之后我们改了什么"的公开更新，把跟进结果变成内容。
- **第 45 天**：对当天深度参与过的人再来一次，带一个真实的新东西。

**禁忌**：不要在评论区反复要票（官方明令）；不要私信拉票；不要发完就消失——PH 上"发完没声音"的账号，第二次 launch 基本没人理。

**最小节奏**：当天回完评论（60 分钟）→ 当晚整理台账（20 分钟）→ 第 3 天跟进（15 分钟）→ 第 7 天公开复盘（30 分钟）。

---

## 附：交接单六问自检

| 交接单的问题 | 在哪回答 |
|---|---|
| 1. 官方 guidelines 原文 | §3 ①（Launch Guide 唯一铁律原文直引 + 已核链接；帮助中心排名说明） |
| 2. 排名算法 | §2（官方：一票≠一分；第三方逆向：早期票/老号票权重，已标注来源性质） |
| 3. hunter 机制是否还有效 | §3 ④（自Serve 为主，算法加成很小，不必花钱找） |
| 4. 被降权/移出首页原因 | §3② + §8（操纵检测作废票；零互动下滑） |
| 5. 中国团队额外注意 | §10（时区反而友好；全英文物料门槛） |
| 6. launch 真实转化数字 | §4（Momen 500 注册/+500% 流量、Swytchcode 400 票、平庸案例对照，均带链接） |
