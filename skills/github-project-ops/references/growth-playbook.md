# 增长 Playbook（冷启动与持续增长实战）

证据基础：PHPStan / Wasp / Digger / Dub.co 一手复盘 + uv / shadcn-ui / Ollama / browser-use 案例查证 + 开源项目库 125 条（2026-10）。所有案例数字为快照，引用时标注来源与日期。

## 一、发布前（1-8 周）

1. **先验证问题再写代码**。Wasp 做了 25 次问题访谈才动手，第一个里程碑是「确认四大痛点」而不是 100 star；PHPStan 的教训：「先做最难的核心功能，纠结名字/域名/首页是拖延」。
2. **README 按 landing page 重做**（对照 audit-checklist §B 九项）。
3. **GitHub 站内 SEO**（audit-checklist §C）：仓库名、About、Topics 埋核心词——这是免费且多数人漏掉的半套。
4. **文档站是前置件不是后置件**。Wasp 关键发现：Docusaurus 建好后「开发者乱批评的意愿明显下降」，之后 Reddit 才爆发。文档同时是转化页和长尾 SEO 资产：约 80% 流量在任务级关键词，每个 API 配一篇「How to do X in Y」recipe，50 篇 recipe ≈ 每月 2500 高质量访客。
5. **打磨 messaging 比加功能重要**。Wasp 从「新编程语言」改为「配合 React & Node 的胶水层 / less boilerplate」后，Reddit 从 20 分扑街变 263/365 赞。测试方法：一句话给外行听，复述不出来就还没打磨好。
6. **贡献者上手设施**：CONTRIBUTING + 预打好 good-first-issue 的小 issue + 一个「从这里开始」的入口 issue。别指望新人做大事（Wasp Hacktoberfest 24 个 PR 中 20 个是 xs/s 级）。
7. **社区底座与物料**：提前建 Discord、养 X 账号——「发布不是一条推文而是持续分享过程」（PHPStan）。轮播图、首条评论文案、统一短链。
8. **Reddit 账号预热**：6 个月以上有 karma 的老号；先在目标子版当贡献者；90:10 法则（90% 有用内容，<10% 提自己项目）；拿不准先私信版主。

## 二、发布日

### 渠道性格表

| 渠道 | 吃什么 | 关键动作 |
|---|---|---|
| Show HN | 谦逊 + 技术细节 + 秒回 | 自述语气像和老同事聊天；禁最高级形容词（「最快/最好」）；结构=团队→一句话→为什么难→背景→方案→差异→邀请反馈；**发布后第一小时守着回评论**；被批评先认同能认同的部分——说服对象是围观者不是批评者（Fly.io 联创一人回 53 条）；标题直白，「X 的开源替代品」直接写进标题 |
| Reddit | 故事 + 数字 + 守版规 | 讲带数字的故事（"we cut docker build times by 70%"）；身份披露放末尾；选版块：r/opensource、r/SideProject 可自发，r/webdev 只许周六 Showoff Saturday，r/selfselfhosted 严格限流；**转化最好的是技术栈小版块**（r/reactjs、r/golang）。Wasp 实测两个子版 ≈ 270 stars，单次最大跳涨 |
| X/Twitter | 视觉缩略图 + 直接 CTA | demo GIF 放前两推；每条推文必有视觉元素（Supabase：让缩略图说话）；meme 能带来不看功能的人；直接开口求 star 不丢人 |
| Product Hunt | 重准备 + 首小时冲票 | 周二至四、提前一周 Teaser、12:01 AM PST 上线、回每条评论（Dub.co 三榜第一：1085 票/663 注册）。但 2025 起共识降温：开发者工具 PH 转化率约 3.1%，远低于 Indie Hackers 精华帖的 23.1%——当徽章和背书用，别当主战场 |
| awesome-xxx 清单 | 格式礼貌 | 读目标清单的 CONTRIBUTING 与 awesome-lint 规则（按字母序、单 PR 单条目、描述以句号结尾）；大清单先开 issue 问 |

### 时机数据

- Show HN：周日 06:00 UTC 上首页概率约为周三 09:00 UTC 的 2.5 倍（低竞争时段胜出）；2025 年对 2.3 万帖的分析同样指向周日晚/周一凌晨（太平洋时间）。**共识：比选时点更重要的是发后第一小时守着回评论。**

### 排期模板（多渠道分日，不要同一天全发）

```
D1  Reddit 技术栈子版（首个反馈池）
D2  长文/大 V 直播（dev.to、YouTube）
D3  Show HN（选低竞争时段）
D4  Product Hunt / 目录站批量
D5+ awesome 清单 PR、复盘长文
```
Digger 实际路径：Reddit → 大 V 直播 → 周日 Show HN → 周一进语言 trending → 周二进全球 Trending。

### Trending 机制（冲榜逻辑）

Trending 排的是**窗口期内 star 增速而非总量**——500 star 的仓库一天涨 300，可以排在只涨 50 的 10 万 star 仓库之上。含义：把所有渠道动能在 24-48 小时内集中释放，让「连续的小胜利互相放大」；错峰等于把动能拆散。刷量不可行：Trending 靠真实用户 star，GitHub 2024 年底起清理假 star。

## 三、发布后 30 天

- **star 请求做成显式 CTA**：文档站顶部提示条（Algolia 做法）、README 末尾、推文里直接说
- **issue/PR 响应速度 = 留存**：回应每一个 bug 和 PR（PHPStan）；48h 内首响
- **长文分发**：dev.to / Hashnode 跨发借官方账号二次曝光；Hashnode 可绑自定义域名保 SEO
- **10 月蹭 Hacktoberfest**：提前备小 issue、上下文标签（docs/example）、入口 issue、官方 Discord 自荐——「只贴标签不会有人来，营销投入决定回报」（Wasp：24 个贡献者 PR）
- **复盘与再发布**：每个里程碑写复盘长文（复盘本身又是传播素材）；**首发扑街可以换 messaging 再发**——Wasp 首发 20 分，一年后换定位再上 HN 拿 222 分、约 200 stars

## 四、持续增长（30 天后）

- **recipe 文档持续产**：每个公开 API 至少一篇 How-to；监控文档页自然落地、文档→注册转化、「站内搜索零结果」找内容缺口
- **版本即事件**：Supabase 式 Launch Week——把功能攒成一周集中发，事前预告事后总结，把普通 release 变成可传播节点
- **生态位设计决定复利**（分发模型即增长引擎）：
  - shadcn/ui：「这不是组件库，是你构建自己组件库的方式」——CLI 把源码复制进用户仓库（完全所有权），registry 模式催生第三方生态，后被 AI 编码工具采用为默认输出
  - uv：入口刻意做成 `uv pip install` 兼容层降低迁移成本，被 AI 编码代理采用形成正循环，月下载 1.26 亿次——「增长看下载量不是 star」
  - Ollama：`ollama run llama3` 的 Docker 式体验 + OpenAI 兼容 API，成为生态默认后端
  - browser-use：抓 agent 浪潮，演示视频在 X 病毒传播，数月 50k stars
  - 共性：降低迁移成本 + 让别人能在你之上建生态，传播被写进产品里
- **指标体系**：star=注意力（用于叙事）；下载量/dependents/Docker pulls/活跃贡献者/文档→注册转化=采用（用于决策）

## 五、中文渠道打法（一手案例，2026-10 快照）

- **公众号私域引爆**：AIHOT（卡兹克）13 天 7.1k star——公众号长文 + 私域转发链
- **知乎发酵**：banana-slides 15.7k star
- **中文内容仓直接爆**：HowToLifeBetter 中文生活内容仓一个月 59.1k star
- **X 连载造共识再开源**：Superpowers 先在 X 连载占住「skills」品类心智，开源后 297k star
- **README 赞助位**：歸藏的 PPT skill 在 README 挂四家赞助
- 路径启示：中文生态竞争强度低于英文 trending，冷启动快；但招聘/融资/长期国际采用信号弱于英文。可组合双轨：中文引爆拿第一批案例与反馈 → 英文渠道做长期采用

## 六、案例模式库（策划新项目找定位时用）

开源项目库 105 条归纳的五个启发标签：

| 标签 | 含义 | 代表案例（快照数据） |
|---|---|---|
| 平民化 | 把原来贵/难/专业的能力变人人可用 | 最大品类（22 条）；AIHOT 13 天 7.1k |
| 连接器 | 桥接两个既有生态，蹭双侧流量 | REA 桥 Ghidra/IDA/JADX 的 MCP 服务器（npm rea-agents），一周 4.2k→78.6k star |
| 禁忌 | 做灰色但合法的地带，天然话题性 | 逆向/安全类 7 条。注意：要有法律免责声明——REA 没写，DMCA §1201 风险裸奔，是反面教材 |
| 新分层 | 在爆发生态上占住新品类词 | Superpowers 占「skills」（297k）；skill 单文件格式霸榜：mattpocock/skills 284.9k、ponytail 160.6k |
| 反讽/脱钩 | HN 声量与仓库采用脱钩 | Performative UI：HN 1181 分仅 923 star；Micasa：657 分 7 star。启示：HN 热度≠采用，选题对标时别被 front page 迷惑 |

配套纪律：引用任何案例数字标注来源与日期；star 数过审计三信号（audit-checklist §F）再当对标。

## 七、被反复验证的八条共性

1. 没有魔法发布日，只有梯子——多家路径完全不同，都是「多次小胜利互相放大」；首发扑街正常，换 messaging 再发
2. 文档先行——文档站建好前不大推
3. README = landing page——大白话价值主张 + demo GIF + 一行安装 + 第一个成果
4. 渠道各有性格——按渠道性格表选组合，别一套文案全渠道群发
5. 动能在 24-48h 窗口打透——Trending 按 star 增速排名，分日排期形成连续小胜利
6. 贡献者冷启动靠「小 + 带路 + 秒回」——小 PR 的价值是让人成为贡献者
7. 分发模型即增长引擎——降低迁移成本 + 允许别人在你之上建生态
8. 用采用指标而非 star 衡量成败
