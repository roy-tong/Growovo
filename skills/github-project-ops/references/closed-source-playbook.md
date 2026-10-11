# 闭源产品的 GitHub 经营

产品闭源时，GitHub 上经营的不是源码，是三样东西：**开发者注意力（搜索与社交证明的场）、信任（敢不敢把代码/数据/工作流交给你）、分发（让用户在原生环境里碰到你）**。反面边界：纯营销姿态会被社区反感——价值先行，90:10 法则同样适用。

## 一、第一个决策：开源哪一层（分层开源框架）

六个已验证的切法，按「你想换什么」选（案例数据为 2026-10 快照）：

| 切法 | 换来什么 | 案例 | 数据 |
|---|---|---|---|
| **按恐惧分层**：开源用户最怕/最不透明的那层 | 信任——「敢把代码交上来」 | Railway 开源构建器 nixpacks/railpack，托管平台闭源 | nixpacks 3.6k 星；CLI 月下载 172 万；Series B 1 亿美元 |
| **按传播力分层**：开源最有演示效果的工具层 | 流量漏斗——免费用户跑生产就需要闭源云 | Browserbase 开源 agent 浏览器框架 Stagehand，无头浏览器农场闭源 | Stagehand 26k 星；npm 月下载 751.7 万 |
| **开源痛点工具带闭源 API** | 高频痛点的自然转化 | Resend 开源 react-email（邮件模板），发送 API 收钱 | react-email 20k 星；org 102 个仓 |
| **starter 模板矩阵** | fork 即进付费漏斗 | Convex 209 个公开仓大量模板（ai-town/chef），SDK 内置 | chef 4.6k 星；npm 月下载 689 万 |
| **内容仓做搜索入口** | 长尾 SEO，几乎零维护 | Modal 的 gpu-glossary 纯术语表仓 | 744 星；examples 1.3k 星 |
| **引擎开源化的时机** | 融资/企业信任节点的一轮背书 | Inngest 引擎 1.0 起开源自托管；Convex 2025 起开源 backend | Inngest 引擎 5.9k 星；Convex backend 13k 星 |

配套通用件：**CLI / SDK / docs 永远开源**（无锁定信任）。LM Studio 范本：桌面 App 闭源，CLI/TS+Python SDK/引擎/文档全开源并认真维护——「闭源 App 配全开源工装」，让闭源产品获得开源生态的信任传导。

## 二、十二个机制模式库（闭源在 GitHub 获客与建信任）

| # | 模式 | 机制 | 案例（数据） | 适用与风险 |
|---|---|---|---|---|
| 1 | **配置文件+徽章进仓** | 接入你服务的仓库 README 带你徽章、根目录带你配置文件=自愿广告位 | 三代演进：DeepSource（.deepsource.toml+徽章，org 几乎不维护也持续获客）→ CodeRabbit（.yaml 配置+PR 评论进用户仓库）→ M8ven（给第三方 MCP 仓塞「Verified 89/100」徽章，被打分对象自发传播） | 产品以「分析/评分/守护」形态嵌进别人仓库时用；徽章信任度在衰减，需给真实价值 |
| 2 | **GitHub App 市场分发** | 官方市场是开发者原生安装入口 | CodeRabbit（AI 代码审查 GitHub App，Series C 1.43 亿美元） | 工作流嵌在 GitHub 内的产品首选 |
| 3 | **Sponsors 赞助生态位** | 赞助目标用户群的开源项目，换曝光与好感 | CodeRabbit org 赞助 103 个开源项目 | 预算换生态位；效果难归因，配 awesome list 自建目录沉淀长尾 |
| 4 | **卫星 demo 仓矩阵** | 每个「用你的 API 能做什么」小仓=一个长尾搜索入口 | Exa：MCP server 5.1k 星 + 几十个 demo 仓（幻觉检测器、公司调研器）；npm 月下载 443.9 万 | API 型产品标准动作；维护成本随仓数涨，归档旧仓维持整洁 |
| 5 | **生态贡献出口「装开源」** | 核心闭源但插件/技能库/tap/示例全开放，让开发者有处可 PR | Amp（Sourcegraph 分拆）：amp.nvim 197 星、contrib 技能库 72 星 | 闭源 agent/工具建「半开源姿态」的标准样本；姿态要配真实响应否则反噬 |
| 6 | **空仓+issues** | 仓库只有 README+许可证，star/issues/watch 的社交证明全保留 | WebContainers（StackBlitz）：4.6k 星 stub 仓、约 820 个 open issues 即公开 bug 库与路线图；npm 月下载 55.7 万 | 无法开源但有品牌的项目；issue 即 UGC 内容，需投入分类运营 |
| 7 | **PR 即上架的商店** | 第三方贡献走 PR、过审自动发布，贡献者即布道者 | Raycast extensions 仓：7.8k 星，扩展商店整个跑在 GitHub 上 | 做生态/市场平台时用；审核依赖社区维护者，质量参差 |
| 8 | **标准即护城河** | 把评分/质量标准开源显中立，排名与数据入口闭源收租 | Glama：TDQS 评分框架开源，MCP 目录闭源 | 信任评测赛道（与 M8ven 同赛道不同切面）；标准采用度低则无效 |
| 9 | **URL 寄生** | 把服务嵌进大平台 URL 结构，零注册摩擦 | GitMCP：github.com 换 gitmcp.io 即得该仓 MCP server；Show HN 185 赞 | 依赖宿主平台结构，官方内置类似功能即被抹平 |
| 10 | **协议即分发** | 选一个所有客户端都认的开放协议当壳，闭源功能件进所有编辑器 | Browser MCP：MCP 壳 + 闭源 Chrome 扩展复用登录态；Show HN 616 赞 | 2025-26 的 MCP 窗口期打法；权限敏感品类要过隐私审视 |
| 11 | **自愿埋点网络** | 用户用 README 徽章+依赖装你的 SDK 换服务，你得到安装遥测网络 | Scarf：@scarf/scarf 月下载 6490 万 | 隐私争议大（postinstall 埋点长期被批评），采用前想清楚透明度方案 |
| 12 | **镜像仓信任背书** | 0 星镜像仓+同步 bot，纯「我们有 GitHub」的背书位 | M8ven：两个镜像仓，Show HN 0 评论 | 最轻也最弱；只适合配套其他打法（其主力是徽章+免费工具） |

选型启发：**「进别人仓库」（1-3）传播系数最高但要有真实价值；「自己铺卫星」（4-7）可控但吃维护；「结构寄生」（8-10）杠杆最大但平台风险高。**

## 三、闭源仓库运营要点

- README 仍按 landing page 九项（audit-checklist §B 通用）；站内 SEO 同样适用（§C）
- **Issues 当客服**：LM Studio 单独开 bug-tracker 仓；CodeRabbit 的 VSCode 扩展仓明写「只放 Issues 和 Discussions」——公开客服即公开的信任资产
- **Releases 当更新日志**：闭源二进制挂 release assets，changelog 格式与开源一致
- **Discussions 当路线图/FAQ**：比 issue 更适合非 bug 对话
- **AGENTS.md / llms.txt 对闭源 API 更重要**：让用户的 agent 正确调用你的 API = 自然采用（agent 时代 SDK 的文档质量直接决定被 agent 选用）
- 免费小工具获客：M8ven 把官方审核清单做成免费 Pre-Flight 工具——「每个 MCP 提交者都路过他家门口」；Inngest 的 typedwebhook.tools 独立小工具仓做搜索入口

## 四、边界与风险（违反会被社区反噬）

1. **别把开源社区当广告位**：价值先行，90:10；Resend 因「react-email 为商业引流」被质疑过——用真实维护投入对冲
2. **许可纪律**：说清哪些仓开源哪些闭源；别用非标准许可冒充开源（Convex backend 的 Other 条款引发 open-core 担忧）
3. **遥测透明**：埋点类（Scarf 模式）必须显眼披露 opt-out
4. **装开源自证会被反噬**：卫星仓开放却无人响应 PR 比不开放更糟（Graphite 早期 CLI 收走/归档也有口碑风险）
5. **第三方目录站环绕是爆发先导信号**（Jev 案例）——竞品监测时盯这个信号

## 五、给闭源经营者的体检增量项

在 audit-checklist 基础上加查：
- 卫星仓矩阵覆盖了哪些搜索入口？有没有 demo/模板/内容仓？
- 用户仓库里有没有你的自然入口（配置/徽章/SDK）？
- Issues/Discussions 响应速度（公开客服的水位）
- AGENTS.md/llms.txt 是否让 agent 能正确用你的 API
- 组织页叙事：分拆/融资/里程碑是否用 GitHub 做了叙事节点（Amp 的分拆公告打法）
