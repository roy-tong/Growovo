# 仓库体检清单

先跑 `scripts/repo_audit.py owner/repo` 采集客观数据（基础信息/社区健康文件/releases/贡献者集中度/Scorecard/AGENTS.md 与 llms.txt 存在性），再按本清单人工核对与判断，最后按 SKILL.md 的体检报告模板输出。

## A. 社区健康文件（GitHub Community Standards）

对照脚本输出的 community 部分逐项核对：

| 文件 | 合格标准 |
|---|---|
| README | 存在，且通过 §B 的 landing page 九项 |
| LICENSE | 明确选择。拿不准用 choosealicense.com：简单宽松=MIT；要专利保护=Apache-2.0；防云厂商白嫖=AGPL（2025-26 的防御性开源新默认） |
| CONTRIBUTING.md | 覆盖：本地环境怎么跑（目标 5 分钟内本地跑通）、如何报 bug / 提 PR、哪里求助。GitHub 会在建 PR 时自动展示它 |
| Code of Conduct | Contributor Covenant v3（2025-07 起的最新版，有交互式构建器） |
| SECURITY.md | 写清支持版本范围 + 漏洞报告方式（配合 GitHub 私密漏洞报告） |
| Issue 模板 | 用 YAML Issue Forms（下拉/必填校验），不是裸 markdown。已知坑：community profile API 检测不到 `.github/ISSUE_TEMPLATE/` 下的 YAML Forms（脚本报 ❌ 时会自动复核一次；复核也失败时用页面或 contents API 再确认） |
| PR 模板 | 有 |
| good first issue | 至少 3 个真小的 issue 打好标签（真小=修文档、小 bug，不是「重构解析器」） |

多仓库账户：建 `.github` 特殊仓库放默认社区健康文件，改一处全账户生效且不污染各仓库 Git 历史（LICENSE 除外，必须逐仓放）。

全平台基线供参照（Octoverse 2025）：仅 63% 公开仓库有 README、5.5% 有贡献指南、2% 有行为准则——配齐就是前 5%。

## B. README = landing page（九项）

1. 一句话价值主张，大白话。差例：「flexible polyglot orchestration framework」；好例：「a tool to easily integrate widgets」
2. 首屏视觉 demo：GIF / 终端录制 / 截图，上传到仓库内引用（外链图会失效）
3. 安装：一行可复制命令；多包管理器做表格
4. Quickstart：带用户达成「第一个小成果」，含预期输出
5. 徽章：CI / 版本 / License（shields.io），不超过一行
6. 对比表（vs 现有方案）或明确适用/不适用场景
7. FAQ，至少含「和 X 有什么区别」
8. 明确 CTA：star、文档站链接、讨论区/Discord
9. 总长度：越短转化越好，细节移文档站

原则：README 给人读；构建/测试命令给 agent 读 → 放 AGENTS.md（§E）。

## C. GitHub 站内 SEO（独立于 Google，多数人只做 Google 那半套）

- 仓库名含核心关键词（搜索排名主要看 Name + About + Topics，不看 README）
- About 描述一行以内、核心词靠前——描述越短关键词占比越高，小仓库可排在 star 多 20 倍的大仓库前面
- Topics：单词形式（`cli` 不写 `cli-tool`），放满 20 个
- Homepage 填文档站或 GitHub Pages

## D. 信任与安全

- CI 绿、有测试；发布说明是人类可读的 changelog（Keep a Changelog 六类分组：Added/Changed/Deprecated/Removed/Fixed/Security），不是裸 git log
- 版本号遵循 SemVer；0.y.z 阶段也要标清破坏性变更
- OpenSSF Scorecard（脚本自动拉取）：≥5 及格；优先修 Risk 为 High 的失败项——常见速赢是 Branch Protection、Token-Permissions 只读、Pinned Dependencies。新仓库常未被收录（API 404，非错误）→ 去 https://scorecard.dev/ 手动跑一次，把结果记入快照
- 加分项：signed releases、artifact attestation、CodeQL

## E. AI-readiness 三件套（2025-26 起的标配，缺了体检报告标红）

1. **AGENTS.md**（仓库根）：给 coding agent 的构建/测试命令与代码约定（agents.md 开放规范，Linux 基金会管理，6 万+项目采用）。作用：让 agent 学对的文档，减少「以同样几种方式出错」的 AI PR（Selenium 2026-09 官方实践）
2. **llms.txt**（文档站根）：极简 Markdown 索引——H1 标题 + 一句摘要 + H2 分节链接（llmstxt.org 规范）。口径：规范位置是**文档站根**；仓库根存在但站点 404 = 未部署（半红，脚本会同时查两处）
3. **AI 贡献政策**：CONTRIBUTING 或 README 写清 AI 生成 PR 的规则——要求披露人工复核、或直接拒绝。背景：curl 2025 年约 20% 提交是 AI slop，安全报告一度 6 份仅 1 份为真；Zig/NetBSD/Gentoo 已禁

闭源产品同样适用（AGENTS.md 让用户的 agent 正确调用你的 API，直接影响采用）。

## F. star 质量审计（判断「这个 star 数是真的吗」）

三个信号（教材案例：ECC 27.7 万 star 但 open issue 仅 164 个 vs daily_stock_analysis 5.5 万 fork 是真实使用；REA fork 比率异常被疑刷量）：

1. **star/issue 比**：万级 star 而 issue 极少 → 存疑（用户够多不可能没人提问）
2. **star/fork 比**：正常约 5-20:1；远超此区间 → fork 不是真使用，star 可能是刷的
3. **增长曲线形态**：单日爆发后长期零互动 → 买量或昙花；健康曲线是多节点爬升（每次发布/传播事件一个小峰）。取数口径：star 时间线（`star+json`）需要 token，无 token 时用 star-history.com 对比或直接标「未验证」

背景数据：GitHub 自 2024 年底清理假 star；CMU StarScout（ICSE 2026）检出约 600 万疑似假 star、18,617 个仓库参与刷星，2024-07 高峰时 16.66% 热门仓库被刷过。

## G. 活跃度健康

- **issue/PR 首次响应中位时间**：目标 <48h。确认即可（「收到了，X 周内看」）——响应速度决定新人留存，不必立即解决。口径：只统计**非作者本人**发起的 issue/PR（剔除维护者自建的 tracker/campaign issue，否则中位数被自评论污染）；脚本不算这项，需人工采样评论时间线
- **release 节奏**：近 90 天有无发布；发布是最自然的传播节点（版本即事件）
- **卡车因子**：脚本粗算 top 贡献者占比。65% 热门项目 ≤2 人（Avelino 2016）——单人是风险也是 XZ 式攻击面（单维护者被社工接管）；建议至少物色一名 co-maintainer
- **倦怠信号**：issue 积压增速 > 处理速度。该做的是减法（批量关闭过期 issue、README 收窄承诺范围、写「非目标」），不是加班
- **弃置风险**：16% 热门项目最终弃置，其中 41% 靠接盘者救活——如果自己要退场，README 顶部明示状态（「不再维护，欢迎 fork」比静默死亡好）

## 附：体检常用原始链接

- Community Standards 文档：https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions
- Scorecard：https://scorecard.dev/ （输入 owner/repo 直接看分）
- Best Practices Badge：https://www.bestpractices.dev/
- choosealicense：https://choosealicense.com/
- Contributor Covenant v3：https://www.contributor-covenant.org/version/3/0/
- agents.md 规范：https://agents.md/ ；llms.txt 规范：https://llmstxt.org/
