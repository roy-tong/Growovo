<p align="center">
  <img src="docs/assets/logo.png" width="400" alt="Growovo">
</p>

<p align="center"><strong>把重复运营工作交给 Agent，留下可核验的执行记录。</strong><br>
<em>Execute authorized recurring operations with reviewable results.</em></p>

<p align="center"><strong>简体中文</strong> · <a href="README.en.md">English</a></p>

Growovo 面向**独立开发者与一人公司**，提供 GTM/User Growth 的需求发现、价值内容、授权执行和真实结果回流。输入产品事实、目标用户与授权边界，先识别值得服务的需求，再生成可复现的价值资产和增长动作；每个动作记录成功、失败或待人工核实，不把发送、曝光或 Star 当成业务结果。

先选目标（声量 / 使用 / 商业），再选渠道（具体的地方，不是平台），然后按阶段做转化。同一个产品换目标就换打法。产品由 **Demand / Execution / Learning 三层能力**与 **Operator Engine + Playbooks** 组成：Signal Interpretation 先校验宿主语义候选，Opportunity Discovery 再判断需求与产品匹配；Value Artifact 经本地复现、授权交付和用户读回形成完整价值链，Journey 诊断真实使用断点并保留多触点非因果边界。GitHub Inbox 只是其中一个 Playbook，而不是产品总定义。CLI、MCP、Skill 共用审批、预算、回读和账本。发送成功只代表操作完成，激活、持续使用和付费分别核验。

**从这里开始。** 给 Growovo 一个增长目标和一份授权边界，它产出可审批的计划，只执行你授权的动作，并把每个结果记成成功、失败或待人工核实。面向没有余量去赌"这次推广到底有没有发生"的一人公司和小团队。

```bash
git clone https://github.com/roy-tong/Growovo.git && cd Growovo
python3 mkt.py demo        # 零网络零账号，一秒内返回
python3 mkt.py next        # 现在该做什么，以及为什么
```

**先看证据再决定信不信：** [发布尸检：11 小时 73 条推文，0 个新 star](docs/launch-autopsy-20261105.md)——用这个工具跑的一次真实 campaign、拿到的数字，以及它暴露出的我们自己的账本缺口。

<details>
<summary>版本更新（v0.8 – v0.13）：GTM Work OS、编排与授权、设计层、执行完整性、内容运营</summary>

**v0.8 新增 GTM Work OS**：18 个能力域、40 个岗位族和 32 个模板进入可校验注册表；不可变上下文、运营目标、WorkOrder、专业 SOP、交付物验收、跨岗位 Handoff 和 KPI 形成业务主链。`work-action` 只把 accepted 交付物物化为既有 plan/queue，不新增发送旁路。当前有一条完整开发者 SOP 运行链，不把注册覆盖说成 40 个专业自动化全部完成。详见[实现说明](docs/gtm-os/05-implementation.md)。

**v0.9 新增按需编排与常驻营销授权**：`mkt orchestrate` 根据产品、目标和任务只选择必要岗位，并逐份决定交付物的内容与表达；日常发帖、回复、私信和邮件可在工作区已有明确 `routine-policy` 时直接执行，不再一律逐次人审。付款、合同、折扣等仍需人工，dry-run、暂停、DNC、配额、回读和未知结果保护不降级。详见[编排与授权](docs/gtm-os/06-orchestration-and-routine.md)。

**v0.10 新增 GTM 设计层**：`mkt gtm design` 把增长任务拆成对象×目标×机制×渠道，先用约束（授权渠道/素材/预算/地区/数据源）筛可行性，再按目标生成结果契约与验证等级。Agent 可以提交候选机制与理由，规则只负责兼容性与边界，并保留拒绝理由；没有数据渠道时只承认平台回执，不把播放量、Star、预售订单或注册数当业务结果。详见[对象×目标×机制×渠道](docs/gtm-os/07-object-motion-design.md)。

**v0.11 合并执行完整性修复并修正设计层**：P0 集成分支进入正式版——MCP 不能自行提高未验证渠道权限，外部发送的未知结果必须人工裁决，账本追加原子化且损坏即拒绝，DNC 抑制数据损坏时停发而不是静默跳过。设计层修正用户目标被 Agent 覆盖、部分 Proposal 误判 blocked、单一 Motion 限制、`ready` 语义混杂和验证等级过宽；交接现在按已选机制生成执行计划并带设计指纹。详见 [执行完整性](docs/gtm-os/06-orchestration-and-routine.md) 与 [设计层](docs/gtm-os/07-object-motion-design.md)。

**内容运营能力（2026-11-05）**：`core/content_ops.py` + `mkt content rules|brief|audit|plan` + MCP `mkt_content` 把小红书/公众号/博客的渠道规则、定位、A/B/C CTA、产品出场、审稿和 14 天排期做成可测试能力。审稿只检查本地写作契约，不发布、不定时、不证明平台效果。详见[内容运营](docs/content-ops.md)。

</details>

[![tests](https://github.com/roy-tong/Growovo/actions/workflows/tests.yml/badge.svg)](https://github.com/roy-tong/Growovo/actions/workflows/tests.yml)

**先试一个结果：** `python3 mkt.py demo` 在本地完成计划校验、审批、dry-run 和账本演示；`python3 mkt.py work-demo` 继续演示 WorkOrder→SOP→交付验收→交接→KPI→已审批 plan/queue，全程无需账号或网络且不执行队列。实际平台命令的验证等级见下表。[能力进度与技术取舍](docs/feature-status.md)。

> 为什么不自己写个脚本，或者用通用自动化平台？[对比页](docs/compare.md)（英文）逐项写了 Growovo 在哪输、在哪赢，并给出每条主张对应的仓库文件。

- **Askovo** 负责业务研究与验证，为任务提供依据。
- **Growovo** 执行授权运营任务，记录操作结果。
- **Verovo** 关联费用与业务结果，计算单任务经济性。

三者可独立使用。`mkt.py` 与 `growovo-gtm` 标识保持兼容，GTM 是首批应用场景。

## 合规与纪律（先读这个）

- 仅用于**本人拥有或明确授权运营的账号**；无授权不碰他人账号，账号归属、权限和人工接管全程留痕
- CDP 通道零凭据：不保存任何密码/token/Cookie；SMTP 凭据只走环境变量
- **一切写操作默认 dry-run，`--go` 才实跑**；实跑须绑定**已人审 plan**，或显式 `--routine` 且工作区常驻策略覆盖该账号/渠道/动作——无授权默认拒跑，仅 CLI 人工通道 `--adhoc` 显式放行（账本记 adhoc=true；MCP 不开放授权管理）
- 日预算在发送前原子预留；`indeterminate`（未知结果）不可自动重发，必须人工 `ledger resolve`；contacts 的 DNC/bounced 身份与 `suppress.txt` 合并为同一发送抑制源
- 失败即停（不重试轰炸）；回读校验不一致绝不发送（449 空正文事故的固化）
- 人类节奏：随机间隔 + 单次硬上限 + 日预算（core/throttle.py 的表）
- 不群发：千人千面内容为前提；占位符残留会被门禁直接拒绝
- 不做对抗：不绕验证码、不伪造指纹、不用代理池绕过平台限制；代理仅可在合法网络需求明确、账号授权一致时绑定使用，且不能提高配额或绕封禁

## 安装与启动

**先拿一个结果，再装环境。** 克隆后不需要任何依赖、任何账号：

```bash
git clone https://github.com/roy-tong/Growovo.git && cd Growovo
python3 mkt.py demo               # 零网络零账号，不到 1 秒过内核四步：lint→人审→写路径→账本
```

要连真实渠道（X / Gmail / GitHub 写）再装运行时，并初始化工作区：

```bash
pip install -r requirements.txt   # 仅 playwright；CDP 走系统 Chrome，无需下载浏览器
python3 mkt.py init               # 建目录 + 报凭据缺口 + 打印下一步
python3 mkt.py doctor             # 环境体检（--selftest 连跑全部离线单测）
./launch.command                  # 双击亦可：首次复制登录 profile，启动 CDP 9222
python3 mkt.py check              # 确认 CDP 会话与 X/Gmail 登录态
```

## 命令矩阵与验证等级

| 命令 | 说明 | 等级 |
|---|---|---|
| `python3 mkt.py status` | 全局账本聚合 | — |
| `python3 mkt.py check` | CDP 会话检查（X/Gmail/Reddit/LinkedIn） | — |
| `python3 mkt.py demo` | 零网络零账号演示（计划校验→人审闸门→写路径 dry-run→账本，沙盒可删） | — |
| `python3 mkt.py doctor [--selftest]` | 环境体检：内核导入/数据契约/凭据/CDP/暂停态 | — |
| `python3 mkt.py report [--hours 24] [--format md]` | 一页纸运营报告（异常/暂停态/抑制名单） | — |
| `python3 mkt.py init` | 工作区初始化：建目录/报凭据缺口/给下一步（`--workspace` 或 `MKT_HOME` 隔离多产品） | — |
| `python3 mkt.py next` | 状态机：现在该做什么、为什么 | — |
| `mkt plan new/lint/show/list/approve/revoke` | **计划与审批**：脚手架、校验、带署名的审批事件（署名 + 计划指纹 + 有效期） | — |
| `mkt queue lint <file>` / `mkt queue build` | **编辑期门禁**：占位符/重复/长度在写队列时拦；模板 + CSV 生成千人千面队列 | — |
| `mkt campaign show/report/next` | **Campaign 视图**：计划/人审/队列预检/账本/事件/实验/下一步 → 一页 Markdown | — |
| `mkt review <campaign>` | 人审预览：队列逐条过门禁 + 已有留证 + 批准与执行命令 | — |
| `mkt ledger resolve <ref> --result … --by … --note …` | **indeterminate 人工裁决**（append-only 覆盖，原账本行不改） | — |
| `mkt link create/list` | **追踪链接**：生成带 utm_campaign 的链接并落盘，供事件归位 | — |
| `mkt outcome import <csv>` / `mkt outcome sync` | **记账自动化**：CSV 导入（幂等）+ 账本聚合成 count 快照 | — |
| `mkt queue run <file> [--go]` | **全渠道队列执行**（email/hn/github/reddit/linkedin/x-reply），逐条走同一管道 | — |
| `mkt action <channel> <action> …` | 单条写动作（与队列同一套门禁/审批/账本）；x message/engage 透传 vendor | — |
| `mkt collect serve/event/import` | **本地 collector**：`/t/<link>` 点击归因、`/events` webhook、Plausible/PostHog CSV | — |
| `mkt experiment obs --id X --from-outcomes` | 实验观察从事件台账自动聚合（不再手抄） | — |
| `python3 mkt.py pause --on '原因' / --off` | **全局暂停总闸**（一切写操作拒跑且留痕） | — |
| `python3 mkt.py contacts add/find/dnc/mark/import` | 线索库操作（幂等去重/DNC 隐藏/er-batch 导入） | — |
| `mkt followup add/due/touch/stats/close` | **跟进台账**：说过话的人单独记；首触 + 第 3/14/45 天，最多三次跟进，无实质回应自动判弃；`mkt next` 会把到期的跟进顶到最前面 | — |
| `mkt intent start/log/show/verdict` | **意图信号学习实验**：可配置周期与可选观察目标，保留样本不足与安全异常，输出学习观察而非渠道生死判决 | — |
| `mkt gtm design "任务" [--constraints …] [--proposal …] [--save …]` | **GTM 设计**：对象×目标×机制×渠道，约束检查、结果契约、未知项与验证等级 | 本地实现，只设计不执行 |
| `mkt gtm handoff <design.json> [--product facts.json]` | **设计交接**：目标契约映射 + 候选岗位 Workstream（复用 orchestrate） | 候选编排，非发布批准 |
| `mkt orchestrate --product P --goal G --task T [--format md]` | **按需编排**：只选必要岗位，逐份确定交付物、内容结构、表达和交接 | 本地实现，候选方案非发布批准 |
| `mkt policy show/enable/disable/check` + `--routine` | **常驻营销授权**：授权账号/动作内日常触达可直接执行；默认 30 天有效；付款等不可加入；其他安全门不变 | 默认仅 email:message；本地实现，无真实发送验证 |
| `mkt capability list/show/lint` / `mkt template list/show/lint` | **GTM 注册表**：18 能力域、40 岗位族、32 模板及交叉引用校验 | 本地实现 |
| `mkt context` / `mkt objective` / `mkt work` | **业务工作**：不可变上下文、目标、WorkOrder 创建/lint/ready/依赖/状态/下一步 | 本地实现 |
| `mkt sop` / `mkt deliverable` / `mkt handoff` | **专业闭环**：SOP 版本与证据门运行、交付物 QA、指定接收人签收/拒收 | 一条完整 SOP；其余为注册摘要 |
| `mkt metric list/show/measure` | **KPI**：版本化计数/比率、去重、scope、成熟度；分母 0 和未成熟返回 null | 本地实现，非通用 BI |
| `mkt work-action materialize` / `mkt work-demo` | accepted 交付物→既有 plan/queue；完整零网络 Work OS 演示，不发送 | 本地实现 |
| `mkt goal list/plan/select` | **目标选择**：声量/使用/商业三类共 9 个目标；按产品类型、阶段与已有账号给适配、平台分工、前两周动作与止损线；可选 1 个辅目标（如声量+使用），两个都达成才算成功；选择写成契约，供 `strategy` / `cadence` 按目标执行 | — |
| `mkt strategy build/cadence` | **策略与落地**：按目标选择给 1 主渠道 + 1 发射点 + 最多 2 个辅助；再给冷启动五阶段、放大条件与四周动作表（`--selection` 时直接用目标方案的动作，不套通用模板） | — |
| `mkt sprint plan` | **冲刺**：把目标方案压成 N 天并行工作流 + 小时级排期；含每日目标、加速杠杆、到点就换、平台容量校验与红线（`--days 3` 为默认） | — |
| `mkt channel venues/plan/score` | **渠道**：具体地方（subreddit/Slack/newsletter/会议），不是平台；带人群、能否发、信号密度、竞争、风险；给主/发射/辅助/旁听组合、经济学区间、容量与预注册实验 | — |
| `mkt deal add/advance/qualify/next/funnel/quote/objections` | **转化**：14 阶段管道（sourced→paid→repeat），五项资格、下一个动作、九个异议、四档 offer、一页 SOW、漏斗与卡住对象 | — |
| `mkt execute --strategy-file --targets --campaign` | **执行桥**：有写通道的平台出计划+队列（过 lint 才写盘）；没有写通道的平台出**人工作业单**，不假装能自动；计划/队列/作业单不等于已执行 | — |
| `mkt signal-interpret --candidates <json>` | **信号语义校验（只读）**：校验宿主结构化来源、主体、问题/技术讨论、替代方案、接触资格与商业证据并去重；工具寻求和自报购买意图不自动升级 | — |
| `mkt opportunity --facts <json> --signals <json>` | **需求发现**：`semantic_candidates` 先过语义校验再匹配产品事实；旧原始信号只标 `unverified_signal`，不把抱怨当购买意愿 | — |
| `mkt artifact lint/render --file <json>` | **价值资产**：校验/渲染教程、样例、可复现测试、模板、对比与真实案例；必须有直接价值、复现步骤与边界 | — |
| `mkt value-chain run --file <json>` | **价值交付链**：研究→方案→授权本地复现→资产→授权交付→用户完成读回；记录真实退出码/输出，未授权命令不执行，不发送平台消息 | — |
| `mkt journey --events <json> --campaign C --since D1 --until D2 [--identity-map <json>]` | **用户旅程**：按真实时间诊断六阶段，区分首次/重复激活并披露乱序、身份别名和多触点；历史 campaign 与 last event 不补进展或因果 | — |
| `mkt evaluate compare/evolution/external` | **能力/竞争/进化评估**：同条件逐项对照、外部未见任务契约与版本回归，不输出综合优胜分 | — |
| `mkt playbook-model compose --platform P --motion M --vertical V` | **三维 Playbook**：平台规则×增长方法×行业承接组合，不为每个平台×行业复制一套流程 | — |
| `mkt account add/list/check/warm/eligible/takeover` | **授权账号资产**：归属/授权证据、养号生命周期、健康度、冷却与人工接管；无登录或发送 | — |
| `mkt network bind/list/export` | **代理引用**：只存环境变量名与账号绑定，不读凭据、不提高配额、不绕封禁 | — |
| `mkt exp show/coverage/stale/assess` | **用户经验与资格**：正式策略要求权利依据、确认人/时间/记录和覆盖来源域的可追溯复核；内部自报只进实验，旧布尔字段不自动合资格 | — |
| `mkt voice show/draft/lint <平台>` | **平台语气**：37 个平台各自的语气、句式、三条开头、禁忌与黑话；`lint` 查出会被当成广告的句子 | — |
| `mkt voice plain --file/--text` | **表达检查**：查出普通人要翻译才能懂的生造词（如「发射开关」），`--fix` 出机械替换稿；JSON 与服务端同源 | — |
| `mkt result` / `mkt revise` | **结果与回流**：按 campaign/subject/time_window 统一过滤动作/任务/业务结果，真实 OutcomeLedger 与动态价值事件可核验；候选解释与已验证机制分开，不做自动渠道生死判决 | — |
| `mkt progress daily/weekly` | **进度**：日报问「今天有什么该动没动」，周报问「这周比上周好还是差」；没有动作就写停摆，不编数字 | — |
| `mkt retro scan/analyze/report` | **复盘引擎**：扫本地日志→带出处的事实→渠道账→业务结论。比率只从人工钉定的 `--facts` 算，机器不替你选口径；每条结论必须带数字、出处、因果机制和可执行动作，缺一项拒绝出报告 | — |
| `mkt billing --actions N --outcomes M` | **报价实验**：保留按动作/按结果，也支持显式订阅/混合实验与接受度记录；接受报价不等于付款 | — |
| `mkt x publish/reply/message/engage/search/inbox …` | X 全动作（vendor x-ops-automation） | **PROVEN** |
| `mkt email message --queue data/queues/w1.json [--go]` | SMTP 发信队列 | **PORTED** |
| `mkt email inbox` / `mkt email bounces` | CDP 只读扫 Gmail / 退信 | **PORTED**（模式 PROVEN） |
| `mkt reddit search --query "…"` | 只读搜索 | **UNVERIFIED** |
| `mkt reddit reply --url <帖> --text "…" [--go]` | 评论（回读校验管道） | **UNVERIFIED** |
| `mkt reddit publish --sub <版块> --title … --body-file … [--go]` | 发帖（日 ≤3） | **UNVERIFIED** |
| `mkt linkedin search --keywords "…"` | 只读搜人 | **UNVERIFIED** |
| `mkt linkedin message --url <profile> --note-file … [--go]` | 半自动连接请求（终端确认） | **UNVERIFIED** |
| `mkt github audit --repo owner/repo` | **仓库体检**（基础/社区健康/releases/贡献者集中度/AI-readiness/Scorecard） | **PROVEN** |
| `mkt github inbox --repo owner/repo` | open issues/PR 清单 + 零回复标记（48h 首响巡检） | **PROVEN** |
| `mkt github search --query "…"` | 仓库搜索（客群/对标发现） | **PROVEN** |
| `mkt github reply --repo o/r --number N --text "…" [--go]` | issue/PR 评论（api 通道写路径首例；需 `MKT_GITHUB_TOKEN`） | **UNVERIFIED** |
| `mkt playbook github-inbox --repo o/r --drafts drafts.json` | 收件箱分流→候选回复→标准队列（不发送；仍需审批/dry-run） | **PORTED** |
| `mkt hn search --query "…"` | HN 讨论/客群发现（Algolia 公共 API，匿名只读） | **PROVEN**（只读） |
| `mkt hn publish --title "Show HN: …" --url … [--go]` | submit 表单提交（CDP；日≤1，标题≤80；Show HN 限自有项目） | **UNVERIFIED** |
| `mkt hn reply --item item:N --text "…" [--go]` | 条目评论（首用=Show HN 首条作者评论，提交后 1h 内） | **UNVERIFIED** |
| `python3 mkt.py memory …` | 只读运营记忆：从已核验回执提取重复模式（见文末） | — |
| `python3 mkt.py workflow …` | 可复用配方：learn / approve / replay（默认 dry-run）/ runs / reconcile | — |
| `python3 mkt.py scorecard` | 记分卡：确认/未知/失败/证据率与业务结果计数（只读） | — |
| `python3 mkt.py optimize` | 优化建议（requires_human，不自动执行） | — |
| `python3 mkt.py gtm diagnose --facts <json>` | **GTM 诊断**：产品事实包→平台优先级+冷启动+放量判据（每条推荐挂证据标签；schema 见 core/gtm.py；风控口径见 docs/risk/） | — |
| `python3 mkt.py schedule …` | 持久化排期：add / tick / status / resume | — |

等级含义：PROVEN=真实账号跑通；PORTED=模式已验证、本封装未整跑；UNVERIFIED=选择器未实测，首跑必须人工盯跑（首跑清单见 03 渠道文档 §5）。

## 规划层：计划是数据，人审是闸门（05 产品方案 §2）

```bash
# 1. 生成计划脚手架（也可直接按 data/schemas.md 手写）
mkt plan new --id askovo-p0 --goal '拿到 20 个激活' --beachhead '独立 UX 研究者' \
  --hypothesis '证据分级是购买动机' --channel email --wave-action message --size 10

# 2. 校验（schema/能力矩阵/size≤内核日预算）
mkt plan lint --id askovo-p0

# 3. 人审：必须署名；写审批事件 + 计划指纹（批准后再改计划会被指纹识别并拒跑）
mkt plan approve askovo-p0 --by roy --days 7

# 4. 队列：模板 + 目标 CSV 生成，或手写后用 lint 过门禁
mkt queue build --campaign askovo-p0-w1 --channel email --wave-action message \
  --template assets/outreach.md --targets data/targets.csv
mkt queue lint data/queues/askovo-p0-w1.json

# 5. 人审预览 → 干跑 → 实跑
mkt review askovo-p0-w1
mkt email message --queue data/queues/askovo-p0-w1.json          # dry-run
mkt email message --queue data/queues/askovo-p0-w1.json --go     # 须已批准

# 6. 记账与复盘
mkt link create --campaign askovo-p0-w1 --channel hn --url https://example.com
mkt outcome import data/events.csv --campaign askovo-p0-w1
mkt outcome sync --campaign askovo-p0-w1
mkt campaign report askovo-p0-w1
```

计划指纹 = 计划内容（不含 review 块）的规范化 sha256；旧计划只有 `human_approved=true`
时仍按 legacy 放行，新审批一律带指纹与有效期。

上限是内核属性：计划里的 size 超过 `core/throttle.py` 日预算会被 lint 拒收。

## MCP 服务（mcp_server.py，stdio，零依赖）

```json
{"mkt": {"command": "python3", "args": ["/绝对路径/Growovo/mcp_server.py"]}}
```

10 个工具，全渠道读写：

- 读：`mkt_status` / `mkt_channels` / `mkt_plan_lint` / `mkt_search`（x/reddit/linkedin/hn/github）/ `mkt_ledger_query` / `mkt_campaign` / `mkt_inbox`（hn/github/email/x）/ `mkt_audit`（github）
- 写：`mkt_run_queue`（全渠道队列，默认 dry-run）/ `mkt_action`（单条动作，同一套门禁/审批/账本）

写操作实跑一律要求已人审 plan；UNVERIFIED 渠道需 `allow_unverified=true`（人工首跑校准）；adhoc 仅 CLI 人工通道，MCP 不开放。MCP 进程同样读 `MKT_HOME`，可与 CLI 用不同工作区。

## Skill（skills/）

- `skills/growovo-gtm/SKILL.md`：**GTM 操作协议**——入口是增长目标（注册/激活/付费）而非单个渠道动作，按四引擎（定位→资产→投放→结果）拆解为七步：目标→plan→客群→人审→执行→复盘。装入 `~/.agents/skills/` 即可在 ZCode 等宿主触发（内容协作走 warm-email-outreach / expert-review-outreach / b2b-account-mapping）。
- `skills/github-project-ops/`：**GitHub 项目经营协议**（开源与闭源通用）——定位诊断（四象限+七阶段）→ 仓库体检清单 → 增长 playbook → 闭源经营模式库；含独立体检脚本 `scripts/repo_audit.py`（零依赖发行版）。与 CLI 的关系：skill 是协议与判断，`mkt github *` 是采数与执行。

## 案例（docs/cases/）

第一波案例来自 ovo 家族自身，每个案例都区分「平台动作」与「业务结果」，不为获客改写状态：

- [Askovo](docs/cases/askovo.md)：首位 dogfood 客户——operator 275 分钟 / human 5 分钟跑完定造跑；2026-11-03 起不设判决日，按迭代节奏推进
- [Verovo](docs/cases/verovo.md)：前置案例——一轮完整 GTM 的代价→默认值映射；Growovo 每条纪律都有出处
- [Growovo](docs/cases/growovo.md)：自举——改名、双语 README、dogfood 回路，每次自我运营走自己的管道

## 通道层（core/transports.py）

ACTIVE×4：`api`（OAuth，Reddit 二期）｜`browser-cdp`（PROVEN）｜`os-input`（macOS 键鼠/AX 模拟，UNVERIFIED，transports/os_input.py）｜`protocol`（SMTP/IMAP，PROVEN）。
RESERVE×6（通道储备库）：`webdriver`｜`rss-public`（只读）｜`esp-api`（事务邮件）｜`mobile-appium`｜`integration`（平台原生）｜`semi-auto-ui`（人工确认兜底）。
每渠道×动作声明首选+降级链；**降级用于"路不通"，不用于"绕风控"**——风控信号的响应是降节奏或 `mkt pause`，不是换马甲。风控阈值与账号限额见项目 06 文档（官方口径亲核；代码预算一律低于官方上限，见 throttle.INTERVALS/OFFICIAL_LIMITS）。

## 环境变量

| 变量 | 用途 |
|---|---|
| `MKT_CDP_URL` | CDP 端点（默认 http://127.0.0.1:9222） |
| `MKT_EMAIL_USER` / `MKT_EMAIL_APP_PASSWORD` | Gmail SMTP（App Password，绝不入库） |
| `MKT_LINKEDIN_AUTO=1` | 解锁 LinkedIn 全自动（不建议；默认半自动需终端确认） |
| `MKT_GITHUB_TOKEN` | GitHub PAT（仅写操作 reply 需要；读原语匿名可用，限额 60 次/时） |

## 数据三件套（`data/`，gitignore）

- `queues/<campaign>.json` 队列；`ledger.jsonl` 全局账本；`contacts.jsonl` 线索库
- `suppress.txt` 抑制名单（每行一个目标）；`shots/` 留证；`state/` 日预算状态
- schema 详见 `data/schemas.md`

## 结构

```
core/      ledger 账本（含人工裁决覆盖）· gates 门禁（含 DNC 合并）· throttle 原子日预算 · pipeline 写路径事务 · evidence 留证 · cdp 会话 · transports 通道注册表 · planlint 计划校验 · approval 执行级审批（计划+队列+账号+工作区指纹）· queue 编辑期门禁 · campaign 视图/人审预览/下一步 · followups 跟进台账 · intent 可配置学习实验 · logscan 日志扫描 · retro 渠道账/冲突/规则 · narrative 四件套叙述 · platforms 平台单一数据源（37 个）· voice 平台语气画像（37 个）· experience 五类经验资格（当前 129 条原始记录，不自动合资格）· strategy 主渠道/冷启动/放大 · cadence 四周动作表 · execute 策略→计划队列 · result 同范围三层结果 · revise 参数回流 · progress 日报周报 · signal_interpretation 语义候选校验 · opportunity 需求发现 · value_artifact 价值资产 · value_delivery 价值交付状态机 · journey 时序/身份/多触点旅程 · playbook_model 三维组合 · accounts 授权账号生命周期 · network 代理引用 · billing 报价实验 · links 追踪链接 · outcome_io CSV 导入与账本聚合 · collector 本地 webhook/跳转 · action_runner 单条动作 · queue_runner 全渠道队列 · playbooks GitHub Inbox 分流/草稿队列 · workflows 配方学习/审批/重放 · locators 定位器阶梯 · scorecard 记分卡 · optimization 优化建议 · scheduler 持久化排期 · workspace 多产品隔离 · experiments 可选学习实验 · attribution 事件/漏斗/归因 · demo 零账号演示 · doctor 环境体检
channels/  x/(vendor+adapter) · email_smtp · email_cdp · reddit · linkedin · github（api 通道首发） · hn（CDP 写 + Algolia 读）
transports/  os_input（macOS 键鼠/AX 模拟，UNVERIFIED）
skills/    growovo-gtm（GTM 操作协议）· github-project-ops（GitHub 项目经营协议+体检脚本）
tests/     离线单测（599 项，含 449 事故回归、全渠道人审门禁/adhoc 放行、执行级审批指纹/队列篡改/账号与工作区绑定、预算原子预留、DNC/indeterminate 阻断、真实 CLI 端到端、37 平台策略/节奏/人工单契约、语义对抗/真实复现交付链/时序身份多触点旅程/经验可追溯复核、账号与报价实验、demo 闭环与 doctor 分级）：python3 -m unittest discover -s tests
mcp_server.py  MCP 外壳（stdio JSON-RPC，零依赖）
```

X 渠道 vendor 引入 x-ops-automation（上游 github: roy-tong/x-ops-automation），
**不改写已验证代码**（见 `channels/x/vendor/PROVENANCE.md`）；上游修选择器时同步。

## License

MIT（见 [LICENSE](LICENSE)）。`channels/x/vendor/` 引入自同作者仓库 x-ops-automation（MIT）。

## 可复用工作流：一次学习，稳定重放

同一类操作不必每次让模型重新探索页面。`workflow` 把步骤、定位器阶梯与回读条件固化成带指纹的配方；批准后才可 `--go`，每次运行都有逐步日志：

```bash
python3 mkt.py workflow learn hn-reply --trace trace.json      # 观察一次 → 草稿（不自动批准）
python3 mkt.py workflow approve hn-reply --by "operator" --days 30
python3 mkt.py workflow replay hn-reply --var item=123 --var text="…"          # dry-run：只解析定位器
python3 mkt.py workflow replay hn-reply --cdp-url http://127.0.0.1:9222 … --go  # 实跑
python3 mkt.py workflow runs
python3 mkt.py workflow reconcile --run <id> --outcome not_sent --note "已核对平台后台" --by "operator"
```

页面变化时按 `css → placeholder → label → role → text` 逐级恢复；视觉恢复需要操作者提供 provider，没有就停在 `visual_unavailable`，不猜点击位置。写入仍走统一门禁：`action` 步骤复用 `action_runner`，`commit` 点击检查暂停与日预算并写账本。详见[工作流](docs/workflows.md)。

## 运营记分卡、排期与优化建议

执行之后要看得见结果，也要能停下来。`scorecard` 把账本与业务事件分开统计：确认率、未知率、失败数与证据覆盖率，不把「发出去了」算成转化。

```bash
python3 mkt.py scorecard --campaign my-campaign
python3 mkt.py optimize --campaign my-campaign          # 人工决策建议，不自动执行
python3 mkt.py schedule add --id daily --queue data/queues/q1.json --interval 1440
python3 mkt.py schedule tick                            # 由 cron/launchd 或人手动触发
python3 mkt.py schedule resume --id daily --note "已核对未知回执"
```

- 排期只跑「到点的一波」；全局暂停、未知结果、失败队列都会让它停下并标 `needs_review`；
- 进程中断后的 `running` 波次不会被自动重跑；
- `optimize` 的比较是描述性的：高确认率不等于渠道效果，建议全部 `requires_human: true`。

## 运营记忆：从已核验的平台回执中学习

在不暴露联系人明细、也不自动重放任何历史动作的前提下，检视重复出现的渠道/动作模式：

```bash
python3 mkt.py memory --campaign my-campaign
python3 mkt.py memory --channel github --output data/operations-memory.json
```

摘要报告已确认的平台操作、未知/失败回执与**人审候选**；有意排除目标对象与消息正文。平台动作成功**不**等于客户激活，模式也永远不是重放许可：新的执行仍需新的授权、当期平台政策检查与标准执行门禁。这是工作流学习的只读输入：它给出模式与风险，真正可重放的配方在批准后由 `mkt workflow` 执行。见[能力进度与技术取舍](docs/feature-status.md)。

## 参与

- 改动前先读 [CONTRIBUTING.md](CONTRIBUTING.md)：里面是**不能破坏的执行不变量**（人审、dry-run、暂停、预算、抑制名单、回读校验、未知结果不重试）与 AI 贡献政策。
- 安全问题走 [SECURITY.md](SECURITY.md) 的私密通道，不要开公开 issue。
- issue/PR 首次响应目标 48 小时内；能力边界与验证等级以 [docs/feature-status.md](docs/feature-status.md) 为准。
- 案例（含失败与判死）在 [docs/cases/](docs/cases/README.md)，不删改状态。
