---
name: github-project-ops
description: 经营 GitHub 项目的完整操作系统（开源与闭源通用）：仓库体检与整改、开源项目立项启动与发布冲榜、README/文档/站内 SEO 优化、增长与贡献者社区运营、治理与长期维护、AI slop 防御、开源商业化、闭源产品用 GitHub 做分发与信任。触发场景：经营/运营开源项目或 GitHub 仓库、launch 或发布一个项目、涨 star 或做增长、仓库体检/健康度审计、写 README 或 CONTRIBUTING、贡献者社区问题、闭源产品要不要上 GitHub 及怎么上、开源商业化选模式、AGENTS.md/llms.txt/AI 贡献政策。用户说「我想把这个工具开源出去」「帮我看看这个 repo 经营得怎么样」「为什么我的项目没人 star」「帮我看看这个项目能不能火」也算。
---

# GitHub 项目经营（开源 + 闭源）

一套从立项到商业化的运营操作系统，蒸馏自 2026-10 四方向实网调研（opensource.guide / Producing OSS / Working in Public / TODO Group / OpenSSF / CHAOSS + 一手增长复盘）和 164 个项目案例库（开源 144 + 闭源经营者 20，全字段带来源；2026-10-16 扩充：HN 发帖人身份实证 20 项目、成本/计量细分位专项、平民爆款横断面）。核心面向个人与小团队项目；公司项目可参考但治理条款需法务。

## 0. 第一步永远是定位诊断，不要直接跳到打法

先回答三个问题，答案决定后面所有动作：

### 0.1 象限：用户多不多 × 贡献者多不多（Working in Public 四象限）

| 象限 | 特征 | 正确动作 | 典型错误 |
|---|---|---|---|
| Toys 玩具 | 用户少、贡献者少 | 把 README 写清楚、发到对的地方，先求被用 | 建一堆社区流程（CoC/治理文档） |
| **Stadiums 体育场** | 用户多、贡献者少（现代最常见） | **curation**：文档、triage、单向镜、机器人过滤 | 强行「建设社区」、办贡献活动 |
| Clubs 俱乐部 | 用户少、贡献者多（成员即用户） | 降低参与门槛、共识决策 | 只输出不接纳贡献 |
| Federations 联邦 | 用户多、贡献者多 | 正式治理（Rust/Linux 级） | 继续创始人一言堂 |

**关键规则：先判象限，再定「多吸收贡献者」（opensource.guide 路线）还是「保护维护者注意力」（Working in Public 路线）。这两条路线对立，选错会把项目拖垮。2025-26 的 AI slop 潮（curl 约 20% 提交是 AI 垃圾、Google 冻结开源漏洞奖励通道至 2027）让天平进一步倒向保护注意力。** 多数刚起步项目是 toys，目标是长成 stadiums；长成 stadiums 后社区工作的本质是筛选不是吸收。

### 0.2 阶段：生命周期七阶段

0 立项前 → 1 启动 → 2 找到用户 → 3 社区/贡献者 → 4 治理 → 5 长期维护 → 6（可选）商业化。每阶段的目标、动作与常见错误见 `references/open-source-lifecycle.md`。诊断时明确说出「当前处于阶段 N」。

### 0.3 开源还是闭源

产品闭源时，GitHub 上经营的是信任与分发而非源码。先读 `references/closed-source-playbook.md` 的「分层开源决策」再动手——闭源在 GitHub 有十二种已验证的获客/信任机制，选错姿势会引发社区反感。

## 1. 任务路由

| 用户任务 | 步骤 | 必读 |
|---|---|---|
| 仓库体检/审计/「经营得怎么样」 | 跑 `scripts/repo_audit.py` 采数 → 按清单逐项核对（脚本结果有已知误报点，见清单 §A/§E）→ 产出按影响力排序的整改清单 | `references/audit-checklist.md` |
| 新项目立项/「想开源出去」 | 定位诊断（象限+阶段）→ 用案例模式库找定位 → 立项检查 | `references/growth-playbook.md` §案例模式库 + `references/open-source-lifecycle.md` 阶段 0-1 |
| 发布/launch | 发布前 8 项检查 → 排期表 → 发布日执行清单 | `references/growth-playbook.md` |
| 增长策划/涨 star | 诊断现状与指标 → 选渠道组合 → 30 天计划 | `references/growth-playbook.md` + `references/viral-mechanics.md` |
| 爆款分析/传播机制/「为什么没人 star」/主词与命名选择 | 六共性自检 → 星数动机与天花板分档 → 渠道×类型绑定 → 演示/采用形态/素材设计 | `references/viral-mechanics.md` |
| README/文档优化 | 按 landing page 九项 + 站内 SEO 三项核对 | `references/audit-checklist.md` §B §C |
| 社区/贡献者问题 | 先判象限再选路线 | `references/open-source-lifecycle.md` 阶段 3-4 |
| 维护负担/倦怠/AI slop | 分级防御清单 | `references/open-source-lifecycle.md` 阶段 5 |
| 商业化/资助/许可证 | 模式光谱与 2025-26 共识 | `references/open-source-lifecycle.md` 阶段 6 |
| 闭源产品上 GitHub | 分层开源决策 + 十二模式库 | `references/closed-source-playbook.md` |

多个任务叠加时（如「体检 + 给个增长计划」），先体检再基于诊断结果做计划，不要并列两份互不引用的输出。

体检的事实源约定：**以 GitHub 远端为唯一事实源**；用户给本地仓库路径时只用来取 slug（`git -C <路径> remote -v`，SSH remote 带 alias 前缀的如 `github-iread:owner/repo` 要剥掉冒号前的部分）。脚本结果只覆盖可自动采集的维度，issue/PR 首响、CONTRIBUTING 实际内容、good-first-issue 数量等需按清单人工补采，统一并入报告「数据快照」节并按原则 8 标来源与日期。

## 2. 八条硬原则（任何任务开始前先核对，输出中不要违反）

1. **文档先行**：文档站建好之前不放大推广。Wasp 复盘：文档建好后「开发者乱批评的意愿明显下降」，之后 Reddit 才爆发。
2. **README = landing page**：一句话大白话价值主张 + 首屏 demo GIF + 一行可复制安装 + Quickstart 到「第一个小成果」。越长转化越差，细节移文档站。
3. **star 是注意力不是采用**（Borges & Valente 2018；CMU 检出 600 万疑似假 star）：判采用看下载量、dependents、Docker pulls、fork 率、文档→注册转化。审计 star 真伪用 `audit-checklist.md` §F 三信号。星的机制=情绪货币（好奇/身份表态/收藏三动机），**监测型主词的天花板显著低于解决型**（实测 ≈19k vs ≈164k）——对外 messaging 选「解决档」动词，把「监测档」动词降为技术描述词；机制、分档与预期校准见 `references/viral-mechanics.md` §二。
4. **响应速度 = 留存**：issue/PR 首次响应目标 48 小时内；确认即可（「收到了，X 周内看」），不必立即解决。响应时长直接决定新人留存（Zhou & Mockus 2015，IEEE TSE）。
5. **先判象限再定贡献者策略**：stadiums 型项目拒绝 extractive contribution、用 CI/机器人/模板降前置成本是对的；clubs/federations 才搞贡献者漏斗。
6. **2025-26 必答三件套**：AI 贡献政策（slop 防御）、AGENTS.md（给 agent 的构建/测试说明，6 万+项目采用）、llms.txt（文档站给模型的索引）。缺这三样的体检报告要标红。
7. **不刷量、不代发、不拉票**：GitHub Trending 按窗口期内真实 star 增速排名，GitHub 自 2024 年底清理假 star，刷量会进黑名单；HN 官方指南明文禁止请朋友代发/拉票（vote-ring 检测杀帖）。增长靠多渠道分日排期在 24-48 小时窗口集中释放真实动能；天花板帖靠事件化素材+第三方自然发现（实证：17/20 项目最高分 HN 帖为第三方所发，机制=筛选效应），见 `references/viral-mechanics.md` §四。
8. **数字必须标来源与日期**：任何 star/下载/融资数字后注（实测API / 页面读数 / 搜索快照 / 自述 / 未验证）+ 快照日期；引用案例主张区分一手复盘 / 平台数据 / 轶事。宁可标「未验证」也不要裸数字。

## 3. 输出格式约定

### 体检报告
```
# 仓库体检报告：<owner/repo>（<日期>）
## 数据快照（repo_audit.py 输出 + 人工补采项，全部标注来源与日期）
## 诊断：象限 / 阶段 / 核心问题（一句话）
## 整改清单（按影响力排序，每条标注工作量 S/M/L 与预期效果）
## 30 天动作（前三件事）
```

### 发布计划
```
# 发布计划：<项目名>
## 定位诊断（象限/阶段/messaging 一句话）
## 发布前检查（8 项逐条过，未完成标 ⚠）
## 渠道排期表（D1…Dn，每渠道一条主帖+响应值守安排）
## 发布日执行清单（逐小时）
## 发布后 30 天（增长动作 + 指标基线）
```

### 增长计划
```
# 增长计划：<项目名>（当前 star X，采用指标 Y）
## 现状诊断（含 star 质量三信号）
## 渠道组合与理由（对照渠道性格表）
## 30 天动作排期
## 指标与复盘节点（用什么采用指标判断成败）
```

## 4. 资料边界与失效条件

- 案例数字是 2026-10 快照；涉及重要决策（融资/选型/对标）先复核最新数据。
- 完整调研报告与 164 条案例库在 `projects/待归类/开源项目库/`（六页签宽表 + 方法论全景报告），需要更多案例细节时可读 `deliverables/如何做好GitHub开源项目-方法论全景.md`、`deliverables/平民爆款开源项目解剖-20261015.md` 与 `数据/merged-*.json`。
- 本 skill 不覆盖企业 OSPO 合规（CLA/法务流程）——那是 TODO Group 指南的领地；也不覆盖「如何给别人的项目贡献代码」。

## 5. 管线化执行（mkt-automation）

GitHub 经营已并入 `projects/自动化营销组件/`（mkt-automation 仓库）：体检采数、零回复巡检、issue 评论走 `mkt github audit|inbox|reply`（评论默认 dry-run，过门禁/节流/留证/账本）。批量整改/发布/响应动作应写成 plan.json 过人审后执行。完整管线（雷达→体检→规划→人审→执行→响应→复盘）见该项目 08 文档。本 skill 的仓库内发行副本：`mkt-automation/skills/github-project-ops/`（改正本后需同步）。
