# 开源项目生命周期：七阶段、治理、维护、商业化

## 一、七阶段总表

| 阶段 | 目标 | 关键动作 | 常见错误 |
|---|---|---|---|
| **0 立项前** | 想清楚解决什么、给谁 | 搜有没有已有项目（有就加入而非造轮子）；先参与别人的项目攒声誉；只开源「非核心但生产在用」的代码（TODO Group 原则）；判断目标象限（toy 起步，奔 stadium 还是 federation） | 为开源而开源；把开源当代码倾倒场 |
| **1 启动** | 让人敢用、敢贡献 | 四件套 LICENSE+README+CONTRIBUTING+CoC；命名查商标、预注册域名与社交号；从第一天就公开（Fogel 三公理：避免私下讨论、显性代码评审、首日开放）；SemVer/Keep a Changelog/all-contributors 首日即用；发布前清查敏感信息 | 「等写完再开源」；文档规范事后补（追溯成本极高） |
| **2 找到用户** | 被真实使用 | 统一主页；到受众聚集处精准发布（growth-playbook 全套）；「先帮助别人再期待回报」；用发现→使用指标（Traffic/下载/clones） | 全渠道同一天轰炸；买量刷 star |
| **3 社区/贡献者** | 先判象限！ | clubs/federations：贡献者漏斗（每阶段减摩擦、48h 首响、good first issue、公开记录一切）。stadiums：curation——单向镜（代码自由消费、限制消耗维护者时间的渠道）、拒绝 extractive contribution（合并成本>收益的 PR）、CI/机器人/模板降前置成本 | stadiums 型强行办社区、办贡献活动；把 casual contributor 当社区成员（他们在场≠归属） |
| **4 治理** | 决策可预期 | 观察社区动态后再正式化；GOVERNANCE.md 写晋升路径；三模式：BDFL（Python）/精英制（Apache）/自由贡献制（Node.js/Rust）；CoC 用 Contributor Covenant v3 且真执行；涉及资金选财政赞助方（Open Collective/SFC/Apache/LF） | 为形式而形式；被看成「私下决策的小圈子」 |
| **5 长期维护** | 活着且不拖垮人 | 记录一切（愿景写进 README/VISION 防 scope creep）；「No is temporary, yes is forever」；自动化（见下）；防倦怠——「维护者的快乐是项目存活的 non-negotiable requirement」；物色 co-maintainer 降卡车因子；体面退场（README 顶部注明状态） | 硬扛；ghost 掉 issue；静默弃坑（16% 热门项目弃置，其中 41% 靠接盘者救活——想让人接盘就明示） |
| **6（可选）商业化** | 可持续 | 见「商业化与资助」节 | 见该节 |

## 二、治理细则（Fogel《Producing OSS》精华）

- **commit 权限的希波克拉底原则**：「首先，不造成伤害」——最重要的标准是判断力不是技术能力；论坛里好斗者技术再强也不给。态度偏宽松（100 个 committer 里 88 个每年只修错字也好过 12 个大拿独占）
- **撤销权限**：尽量避免陷入；撤销讨论须私下进行（透明原则的唯一明示例外），但规则与名单必须公开（MAINTAINERS/COMMITTERS 文件置顶，不做「神秘星室法庭」）
- **「信用是自由软件世界的主要货币」**：小贡献被致谢的人会回来做更多（all-contributors bot 自动化这件事）
- **forkability 是治理地基**：分叉的可能性正是不存在真独裁者的原因；项目变老会从 BDFL 趋向共识民主，且几乎不会回退
- **成文规则「描述协议而非发明协议」**：以新人高频问题和老手常见抱怨为准，链接到讨论存档；别写「要有礼貌」这类废话
- **bikeshed 意识**：话题越小争论越长——把争议收窄到「对项目目标的影响」上来裁

## 三、维护自动化清单（装完即用）

| 工具 | 作用 |
|---|---|
| semantic-release / release-please / release-drafter | conventional commits 自动发版与 changelog |
| Renovate / Dependabot | 依赖自动更新（Scorecard 检查项） |
| actions/stale | 自动清理无活动 issue/PR |
| all-contributors bot | README 贡献者表格自动化 |
| CodeQL + Artifact Attestation | 代码扫描 + 构建产物溯源 |
| peakoss/anti-slop | 34 条规则自动检测关闭 AI slop PR |

## 四、AI slop 防御（2025-26 必答题，分级选配）

背景数据：curl 2025 年约 20% 提交是 AI slop（安全报告一度 6 份仅 1 份真）、2026-01 关停 bug bounty；Google 2026-10 冻结 OSS 漏洞奖励通道至 2027；GitHub 2026-02 起提供限制/关闭公开 PR 的开关与 PR 速率上限；GitClear：AI 时代重复代码块 8 倍增长。

分级防御（按项目体量选）：
1. **预防（人人该做）**：AGENTS.md + llms.txt 让 agent 学对的文档——多数 slop 源于 agent 学了过时文档（Selenium 实践）
2. **政策（个人项目最低配）**：CONTRIBUTING 写明 AI PR 规则：要求披露「AI 生成+人工复核」，或直接声明不收（Zig/NetBSD/Gentoo 已禁）
3. **自动化（有流量后）**：anti-slop Action、CodeRabbit triage
4. **平台（被淹没时）**：限制 PR 仅协作者可提（Ghostty 受邀制是极端形态）

维护者反向用 AI 减负也是主流：50% 开源项目至少一名维护者在用 Copilot（Octoverse 2025）；Code Review/Autofix 提速 3 倍+。原则：AI 冲击的是「评审带宽」，所以防御要么减少入口、要么加速评审。

## 五、商业化与资助

**模式光谱**：赞助/捐赠（Sponsors、Open Collective）→ 基金会治理（LF/Apache）→ open core（GitLab/Grafana）→ 双许可/source-available（SSPL/BSL/FSL）→ SaaS/托管（云厂商模式）。

2025-26 主流认知（有数据支撑的行业共识）：
- **AGPL 成为「防御性开源」新默认**：过法务审查又能约束云厂商。SSPL/BSL 不被 OSI 视为开源且会触发分叉
- **改许可 = 分叉风险，且原厂回不了头**：Redis→Valkey（83% 大企业在测或已用 Valkey，Redis 2025-05 加回 AGPL 也未扭转）、Terraform→OpenTofu（下载近 1000 万，IBM 仍以 64 亿美元收了 HashiCorp）、Elastic→OpenSearch（1 亿+下载后移交 LF）。改许可前按「分叉会赢」做压力测试
- **「基础设施选型现在是治理决策而不只是技术决策」**

**资助有效性**（Working in Public + NBER 实证）：
- 有效：patronage 订阅赞助、边界清晰的赏金（设计/迁移/安全 bug）、直接资助个人（项目越小越模块化越有吸引力）、机构化基金（Sovereign Tech Fund→EU-STF 是 2024-26 方向）
- 无效：补偿 casual contributors（他们的贡献供过于求）、paywall、micropayments
- 注意 NBER w31668：**约 20 美元的小额赞助反而相对降低创新产出**（挤出内在动机）——要么不给，要么给像样的数目
- 现实基线：主动寻求资助的开发者仅约 2/5 收到过捐赠（ICSE 2024）；46-60% 维护者完全无报酬（Tidelift）

## 六、方法论速查（深挖读原文）

| 资源 | 一句话定位 | 链接 |
|---|---|---|
| opensource.guide | 个人维护者全周期 checklist，13 章 | https://opensource.guide/zh-hans/ （中文） |
| Producing OSS（Fogel） | 多人协作机器的运转原理 | https://producingoss.com/ （含中文版） |
| Working in Public（Eghbal） | 维护者注意力经济学，四象限 | https://press.stripe.com/working-in-public （免费电子版） |
| TODO Group 指南 | 企业/OSPO 视角 24 篇 | https://todogroup.org/guides/ |
| OpenSSF Badge | 工程卫生自认证 | https://www.bestpractices.dev/ |
| OpenSSF Scorecard | 自动安全打分 | https://scorecard.dev/ |
| CHAOSS | 社区健康度量 | https://chaoss.community/ |

## 七、学派矛盾（别混用）

1. **贡献者策略**：opensource.guide 主张漏斗多吸收；Working in Public 主张 stadiums 要过滤保护注意力。调和=先判象限
2. **commit 权限**：opensource.guide 当激励给；Fogel 反对身份化并附撤销程序
3. **治理时机**：个人派「观察后正式化」；企业派（TODO）「立项即明确」
4. **度量**：活动指标（CHAOSS）vs 注意力稀缺（WiP 认为增长即成本）——CHAOSS 的 burnout 指标正是对后者的量化回应
5. **透明例外**：撤销权限讨论、安全漏洞、CoC 举报走私密渠道
6. **立场**：TODO 站企业（IP/CLA/法务），WiP/opensource.guide 站个人维护者（倦怠/资助个人）；WiP 引 Evan You：「公司资助的项目一旦不再服务公司目的就会被砍」
