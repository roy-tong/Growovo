# 风控卡：Hacker News

> M3 风控情报库首卡 · 建卡 2026-11-03 · 官方口径已亲核（newsguidelines.html 原文，2026-11-03 抓取）
> 用途：`mkt gtm diagnose` 的 hackernews 平台条目引用本卡；操作员发布前过一遍响应阶梯。

## 一、官方口径（亲核，原文直引）

- *"Please don't use HN primarily for promotion. It's ok to post your own stuff part of the time, but the primary use of the site should be for curiosity."* —— 自有项目可以发，账号主体必须是真实社区参与者
- *"Please don't put generated text in HN posts. Write your text yourself—HN is for sharing between humans. For the same reason, please don't automate posting."* —— **零 LLM 明文规则，且禁止自动发帖**
- *"Don't post generated text or AI-edited text."*（评论区同理，AI 改写的也算）
- *"Don't solicit upvotes, comments, or submissions."* —— 拉票明令禁止
- *"Please don't delete and repost."* —— 被 kill 后删了重发是加重情节
- *"Please don't create accounts routinely."* —— 马甲矩阵明文禁止
- Show HN 惯例：限自有项目；标题 ≤80 字符；发布后 1h 内跟首评是社区惯例

## 二、检测手段（已知与推断分列）

| 手段 | 性质 | 说明 |
|---|---|---|
| genAI 文本分类器 | 已知（自家事故：Verovo Show HN 2026-10 被 kill） | 机打/模板文直接判死；**AI 改写文同样高危**（规则本就禁 AI-edited） |
| 社区 flag + 审核员 | 已知 | 推销感重的内容被 flag 后降权/kill |
| karma 与账号历史 | 已知 | 新号低 karma 发 Show HN 通过率低；账号历史是隐性信誉分 |
| 投票模式异常检测 | 推断（行业通用） | 短时间同源投票会被反作弊系统识别 [推断，未见官方文档] |
| 多账号关联 | 推断 | 同设备/同 IP/同行为模式的账号关联处理 [推断] |

## 三、响应阶梯（产品化映射）

| 级别 | 信号 | 动作 |
|---|---|---|
| 🟢 正常 | 讨论自然增长 | 按节奏：日 ≤1 提交（throttle 对齐），只读不限 |
| 🟡 降节奏 | 帖子快速下沉/零互动 | 不重发（官方明令）；转评论参与，攒账号信誉后再战 |
| 🟠 转人工 | 被 flag/降权迹象 | 全部 HN 动作转人写人发；停用一切辅助生成 |
| 🔴 停 | 被 kill / 账号受限 | `mkt pause` 该渠道；**不删号重发、不换号**（都是加重情节）；走 hn@ycombinator.com 沟通 |

## 四、内容生产档位

**HN = 人写人发档（最高档）**：标题、正文、评论全部由账号本人撰写；可用的辅助仅限：事实核查素材、数据整理、拼写校对。my-voice 底线之上，禁止 AI 生成或改写成文。发布窗口周二–周四美东上午；发布后 1h 内首评（设计动机/数据/局限，如实写）。

## 五、恢复路径

被 kill 无正式申诉渠道；唯一途径是邮件沟通 + 改正行为后重建信誉。**判定：不可恢复类风险，预防成本远低于恢复成本——首帖必须最好。**

## 六、自家事故记录（持续追加）

- 2026-10：Verovo Show HN 被分类器判 genAI kill → 固化红线「零 LLM、只读、不拉票」（复盘-GTM全周期-20261031）
