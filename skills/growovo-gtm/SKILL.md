---
name: growovo-gtm
description: Growovo GTM 业务操作系统——以不可变业务上下文、WorkOrder、专业 SOP、交付物验收、跨岗位 Handoff、授权执行和真实 KPI 为主链。覆盖 18 能力域/40 岗位族与 32 模板，用于品牌内容、需求获客、销售售前、激活转化、用户留存、客户成功、RevOps、GTM 工程和 DevRel。输入"把这个产品推向目标客户"或具体 GTM 工作时，先建工作契约与验收，再决定是否生成 plan/queue；发送、曝光和 Star 不算业务结果。触发词：GTM、WorkOrder、SOP、增长闭环、获客、激活转化、销售交接、客户成功、RevOps、DevRel、营销自动化、growth campaign、mkt。
---

# Growovo GTM 操作协议（growovo-gtm）

本 skill 是 **Growovo 执行内核的 agent 操作协议**（05 产品方案 §5.3）：
agent 负责编排与执行、内核守门禁、人负责账号/策略授权及付款等高风险决定。动作落到
CLI（`python3 mkt.py …`）或 MCP（mkt-server），本协议不发明新动作。

仓库位置：github.com/roy-tong/Growovo（克隆到本机的目录；MCP 配置里用其绝对路径）

## 结果闭环 v2（2026-11-14 起推荐入口）：给事实和目标，自动执行

用户给产品事实包 + 一个增长目标，Growovo 自己编译策略、按日配额执行、每天出结果。
agent 在这条链路里的职责是**供给内容**，不是替用户做执行决策：

```bash
python3 mkt.py grow platforms            # 第一期只有 X + Email 能全自动
python3 mkt.py grow launch g1 --facts facts.json \
  --goal awareness.followers --target 200 --handle your_handle --owner you@example.com
python3 mkt.py grow briefs g1            # 读内容需求单，按 brief 生产
python3 mkt.py grow stage g1 --from drafts.json --date 2026-11-15   # 入队，过门禁才落盘
```

agent 不做：`arm`（人工开闸）、`resume`（人工裁决）、`decide`（用户决定继续/优化）。
agent 可以做：读 `briefs`、`status`、`daily`、`report`，按需求单生产内容并 `stage`。
MCP 已提供只读的 `mkt_grow`（platforms/briefs/status/daily/report/stage），没有 arm/resume。

每日结果由 tick 自动生成并投递；用户据结果决定 `continue` 或 `optimize`，优化只改执行
参数且带回滚。内容质量由 agent 自闭环（voice/tactic/编辑审稿 + 队列门禁），不承诺爆款。

## v0.8 默认主链：先验收工作，再决定执行

不要把用户需求直接翻译成发帖或群发。先判断 18 个能力域 / 40 个岗位族中的责任位置，再按以下主链：

```text
Product + Goal + Task → orchestrate（最小岗位 + 交付物 + 表达方式）
→ BusinessContext → Objective → WorkOrder → SOPRun → Deliverable accepted
→ Handoff accepted（有下游时） → ActionRequest → 既有 plan/queue 或常驻营销授权
→ 平台动作 / 任务结果 / 业务结果分层观察 → 规则版本回流
```

先跑编排，不默认使用全部岗位，也不默认输出一种格式：

```bash
python3 mkt.py orchestrate --product product.json \
  --goal "14 天内首次成功调用" \
  --task "写开发者教程并做 GitHub 发布" \
  --audience developer --channel github --format md
# 高管 → decision memo；运营 → runbook；开发者 → 可复现教程；
# 销售 → 资格交接；email → 个性化触发 + 一个价值点 + 一个 CTA。
```

```bash
python3 mkt.py capability list
python3 mkt.py capability show --role-family 46
python3 mkt.py template show T30
python3 mkt.py context import --file context.json --by <人>
python3 mkt.py objective create --file objective.json --by <人>
python3 mkt.py work create --file work-order.json --by <人>
python3 mkt.py work ready --id <work_order_id> --by <人>
python3 mkt.py sop start --work-order-id <work_order_id> --by <人>
# 交付物提交与独立验收、接收人签收后，才物化执行计划
python3 mkt.py work-action materialize --work-order-id <id> --step-id <step> \
  --deliverable-id <id> --deliverable-version 1 \
  --strategy-file strategy.json --targets targets.csv --campaign <id> --by <人>
```

硬约束：

- 只选择目标产品、目标和任务需要的岗位；没有触发条件的渠道、销售、投放、公关、CS 等角色不得为了“显得完整”加入。
- 按受众、交付目的和渠道选择内容结构与表达；内部技术/收入/POC 文档不得被渠道化成社媒口吻，每份交付物分别确定 required_content。
- `ready` 前检查输入验收、上下文 / 目标 / SOP 版本、Owner、Receiver、交付物、指标和依赖；缺项只能 blocked。
- SOP 步骤没有产物和 DoD 证据不能完成；R3/R4 作者不能终审自己；Handoff 只有指定接收人能 accepted。
- 当前只有 `sop-devrel-first-success@1.0.0` 是完整可执行 Step Contract；其他岗位的注册摘要不能冒充已运行 SOP。
- `work-action` 只生成既有 plan/queue，真实发送走 `plan approve` 或有效 `routine-policy`，并始终经过 dry-run、`pipeline/action_runner` 等既有门禁。MCP 的 `mkt_capabilities`、`mkt_templates`、`mkt_work_order`、`mkt_metric_measure` 只读。
- KPI 分母为 0 或观察未成熟返回 null；committed 只增加平台动作，不推进任务或业务结果。
- 完整命令与边界见 `docs/gtm-os/05-implementation.md`；先跑 `python3 mkt.py work-demo` 可看全链。

## 日常营销执行与高风险决定的边界

日常发帖、私信回复和邮件可以在工作区已有有效 `routine-policy` 时直接执行，不必逐次 plan 人审：

```bash
# 授权管理只能由人通过 CLI 完成；agent 不自我创建/扩大授权
python3 mkt.py policy show
python3 mkt.py policy check --channel x --action-name publish --account default
# 已授权的日常动作：显式 routine + go；不传 go 仍只是 dry-run
python3 mkt.py action x publish --text "<内容>" --campaign <id> --account default --go --routine
python3 mkt.py queue run data/queues/<campaign>.json --go --routine
```

- 只执行封闭动作集及授权账号范围内的日常营销触达；空账号列表只允许 default/未指定账号。
- 付款、合同、折扣、危机响应及未知动作必须交由有权人员，不能用 routine。
- `routine` 只替代逐次 plan 审批；pause、DNC、quota、readback、UNVERIFIED、indeterminate 人工裁决全部保留。
- 不传 `--routine` 或策略不匹配时，仍执行原 plan 人审；MCP 不开放授权创建、扩大或撤销。

## 入口是一个 GTM 目标，不是单个渠道动作

用户说"这是我的产品/GitHub 仓库，帮我找到最可能需要它的用户并完成增长闭环"时，
按四引擎拆解（Growovo = 定 → 造 → 跑 → 量）：

| 引擎 | 职责 | 当前承载 |
|---|---|---|
| Positioning 定位（定） | 理解产品、定位 ICP、购买动机与竞争替代 | 研究上游 [askovo](https://github.com/roy-tong/askovo) + `mkt signal-interpret`（先审宿主语义候选）+ `mkt opportunity`（只接收 qualified 语义候选；旧信号保持 unverified） |
| Asset 资产（造） | 生成并交付可直接使用的教程、样例、测试、模板、对比与真实案例 | `mkt artifact lint/render` + `mkt value-chain run`（真实本地复现→资产→授权交付→用户读回）；不以发送代替使用 |
| Campaign 投放（跑） | 选 Platform×Motion×Vertical、执行授权动作、管理反馈 | `mkt playbook-model compose` + `mkt execute`（统一审批/队列/人工单/读回） |
| Outcome 结果（量） | 追踪真实使用、激活/留存/付费与断点 | `mkt journey`（真实时序/身份归一/首次激活/多触点非因果）+ `mkt result`（同范围三层投影）+ 可选学习实验 |
| **Follow-up 跟进（守）** | **发完之后把人跟到底：对象台账、到期提醒、三次原则、判定弃或续** | `mkt followup`（台账/三次原则/到期）+ `mkt intent`（可配置学习实验）+ `mkt billing`（动作/结果/订阅/混合报价实验） |

对增长结果负责的是五引擎整体；单渠道动作只是「跑」层的原语。

**为什么多了「守」这一层**：客户语料里最一致的卡点不是"不知道去哪发"，而是**发一次就没声音**。
有效动作是「找对的人 + 有用的话 + 持续跟」。所以任何一次投放结束，都必须落到跟进台账：
对一对一外联和需要持续关系的触达，没有 `mkt followup add` 就等于没完成承接；品牌、文档、内部运营等不适用该触达类型的工作，按各自 WorkOrder 的交付物和接收方验收，不机械加跟进。


## 第 0 步：先选目标，再选平台

同一个产品，追 star、追真实使用、追第一笔付费是三套动作、三套平台、三套判据。
不要一上来问「打哪个平台」，先问「这一轮到底要什么」。

| 目标族 | 追什么 | 它不证明 |
|---|---|---|
| 声量 awareness | GitHub star、关注者、一次发射的讨论量 | 有人需要、会用、会付钱 |
| 使用 adoption | fork、外部 issue、真实数据提交、激活 | 愿意付费、会留存 |
| 商业 commercial | 合格对话、样本、报价、付费、复购 | 可规模化、单位经济成立 |

```bash
python3 mkt.py goal list --facts data/<workspace>/facts.json
python3 mkt.py goal plan --facts data/<workspace>/facts.json --goal commercial.first_paid
# 人选目标 + 平台；不选平台时用推荐组合
python3 mkt.py goal select --facts data/<workspace>/facts.json \
  --goal commercial.first_paid --platforms reddit,hackernews,github \
  --out data/<workspace>/goal-selection.json
# 也可以 1 主 + 1 辅（两个都达成才算成功）
python3 mkt.py goal select --facts data/<workspace>/facts.json \
  --goal awareness.stars --secondary adoption.real_data \
  --platforms github,hackernews,reddit \
  --out data/<workspace>/goal-selection.json
python3 mkt.py strategy build --facts data/<workspace>/facts.json \
  --selection data/<workspace>/goal-selection.json
python3 mkt.py strategy cadence --facts data/<workspace>/facts.json \
  --selection data/<workspace>/goal-selection.json --start 2026-11-05
# 只有 3 天：换冲刺打法（不压缩六周计划）
python3 mkt.py sprint plan --facts data/<workspace>/facts.json \
  --selection data/<workspace>/goal-selection.json --days 3 --start 2026-11-05
# 渠道：具体地方（subreddit/Slack/newsletter），不是平台
python3 mkt.py channel plan --facts data/<workspace>/facts.json \
  --selection data/<workspace>/goal-selection.json
# 转化：阶段、下一个动作、漏斗、报价
python3 mkt.py deal list
python3 mkt.py deal next
python3 mkt.py deal funnel
python3 mkt.py deal quote --id <deal> --offer diagnostic
```

硬规则：

- 一次最多 **1 主渠道 + 1 发射点 + 2 辅助平台**；发射平台不能单独当主渠道；
- 可选 **1 个主目标 + 1 个辅目标**；两个都达成才算成功，只达成一个不算；
- 目标没定，不建名单、不发消息、不报价；
- 目标值是排期形状，不是预测；没有基线就标「基线未提供」，不编数；
- 旧的复盘数字不能改写这一轮的主渠道——人群可能换了，旧效率不迁移；
- 目标达成只证明那一个数变了，不自动证明需求、付费或可规模化。

### 渠道与转化的两条硬规则

1. **渠道单位是 venue，不是平台。** r/FinOps 是渠道，Reddit 不是。先看 venue 的
   `promo`（allowed/limited/banned）、`access`、`signal_density`；banned 只能旁听/回答，
   不能发产品帖。一次 1 主 + 1 发射 + 2 辅助 + 2 旁听；经济学是假设区间，不是预测。
2. **转化单位是 deal stage，不是消息。** 14 个阶段，每个阶段只有一个下一个动作；
   没有 `qualified` 不给样本，没有 `readback` 不报价，没有 `paid` 不交付。
   所有阶段推进都必须有 evidence；进入 qualified 前先通过资格，报价只允许 readback；paid 可附独立 verification method/ref。同一对象最多 2 次动作，第三次是骚扰。

```bash
python3 mkt.py channel plan --facts f.json --selection s.json
python3 mkt.py deal next            # 今天该跟谁
python3 mkt.py deal funnel          # 掉在哪一步
python3 mkt.py deal quote --id deal-0001 --offer diagnostic
```

## 八步闭环（内核已实现，不是文档清单）

每个环节都有对应命令与 MCP 工具；**平台清单只有一份**（`core/platforms.py`，37 个），
诊断/策略/执行/日志扫描共用，不会各说各话。

| 步 | 要回答的问题 | 命令 | MCP |
|---|---|---|---|
| 〇 复盘 | 哪做得好、哪做得不好、为什么、改什么 | `mkt retro scan --path <目录>` → `analyze` → `report --facts <口径>` | `mkt_retro_scan` / `mkt_retro_analyze` |
| ① 策略 | 这一轮要什么、打哪条、怎么冷启动、什么时候放大 | `mkt goal list/plan/select` → `mkt strategy build --facts f.json --selection s.json` | `mkt_goals` / `mkt_goal_plan` / `mkt_strategy` |
| ①b 渠道 | 具体去哪、能不能发、能带多少对话、什么时候停 | `mkt channel venues/plan/score` | `mkt_channel_venues` / `mkt_channel_plan` |
| ①c 转化 | 每个对象下一步做什么、卡在哪、怎么报价 | `mkt deal add/advance/qualify/next/funnel/quote` | `mkt_deals` / `mkt_deal_next` |
| ② 落地 | 账号、每天做什么、什么算做完 | `mkt strategy cadence --facts f.json --start 2026-11-04` | `mkt_cadence` |
| ③ 风控 | 平台怎么查 AI、我们改什么 | 含在策略输出的「平台会怎么查我们」 | `mkt_strategy` |
| ④ 执行 | 真发出去 | `mkt execute --strategy-file --targets --campaign`（有写通道出队列；没有的出人工单） | — |
| ⑤ 进度 | 今天该动什么、这周比上周好不好 | `mkt progress daily/weekly` | `mkt_progress` |
| ⑥ 结果 | 同范围三层各自多少 | `mkt result --campaign C --since D1 --until D2` | `mkt_result` |
| ⑦ 回流 | 改哪个参数、从多少到多少 | `mkt revise --params '{}'` | `mkt_revise` |

**两条硬纪律（写在代码里）**：
- 复盘报告的每条结论必须同时有「数字 + 出处 + 因果机制 + 可执行动作」，缺一项 `Claim.validate()` 直接抛错；
- 比率只能从人工钉定的口径文件算，机器不替你选哪个数——它是供证据的，不是做裁决的。

**G1–G4 纵深纪律（同样由代码门禁承载）**：

1. **原帖不是需求。** 宿主 Agent 先给结构化语义候选：原始 `source_ref`、主体 ID/角色、`problem` 或 `technical_discussion`、工具寻求证据、当前替代及不满足原因、接触资格。先跑 `mkt signal-interpret --candidates candidates.json --json`，只有 `qualified` 可进入 `mkt opportunity` 的 `semantic_candidates`。技术讨论不是需求；工具寻求、关键词和 self-declared `purchase_intent` 都不是已观察商业意图；不可接触对象不得入可执行机会。
2. **资产必须走到用户读回。** `mkt value-chain run --file chain.json --json` 依次跑研究→方案→本地复现→资产→授权交付→用户完成。复现只接受 argv（不是 shell 字符串），授权必须含 `authorized_by/authorized_at/command_sha256`；指纹可用 `core.value_delivery.command_fingerprint(argv, cwd=...)` 生成。没有真实 exit code/output/source_ref 或退出码不符就不能标 verified。交付授权绑定 asset+subject；本命令不发送平台消息，外部 sent/delivered 回执也不是使用。只有交付后、同主体、带 source_ref 与 verification 的真实完成事件才到 `completed`。
3. **Journey 不按输入数组或最后事件编故事。** 保留真实时间顺序与 unknown/unverified；晚到前序事件记 out_of_order，不补造阶段。邮箱做确定性归一，其他身份合并必须显式 `--identity-map`；首次 activation 与重复激活/retained_use 分开。多触点只并列披露，不分配 last-touch 因果。
4. **经验旧布尔值不是策略资格。** `mkt exp assess --file record.json --purpose strategy` 会检查权利依据、确认人、确认时间、权利记录，以及 reviewer/reviewed_at/record_ref/source_domains。`internal_self_report` 只能进限定实验；正式策略/普遍化需要 `traceable_review`，旧数据继续可读但默认不合资格。

**口径文件长这样**（`--facts` / `--retro-facts`）：
```json
{"github": {"动作": 77, "回应": 4, "结果": 30},
 "email_1to1": {"动作": 209, "回应": 11, "结果": 1},
 "email_batch": {"动作": 991, "回应": 0, "结果": 0}}
```
子渠道要拆开：同一个平台里打法不同、效率差一个量级的，合在一起算会把有效打法埋掉。


## 何时使用

- 用户给出"产品/仓库 + 增长目标"（试用、注册、激活、付费），要方案或直接执行
- 需要为目标产品寻找/扩展目标客群并入线索库
- 已有计划要执行（发帖/私信/邮件波次）或复盘（回复率/退信/转化）
- 要判断哪条渠道真正有效（预注册实验 + 归因台账，不是拍脑袋）

## 七步流程

### ① 立场与约束确认
先三行立场（同意/保留/反对）再动手。确认：业务目标一句话、可用渠道、节奏约束、
已有资产（内容/线索）。缺目标不动笔。

### ② 目标拆解
假设 → ICP（组织+persona）→ **单一 beachhead**（不铺多线）。写清假设的可证伪点。

### ③ 客群证据
- 发现：`mkt.py x/reddit/linkedin/hn search`（只读）+ 既有研究资产（leads 库/触达数据库）
- 资格：证据分级 A=公开自证 / B=强信号 / C=推断；**低于 B 不入队**
- 公司账号必须富化到具体员工（部门+权责+persona）——走 [[b2b-account-mapping]] 协议
- 入库 `data/contacts.jsonl`（hooks/title/verification 字段是触达弹药，也是漏斗身份归一的基础）

### ④ 计划落盘（plan.json）
```bash
mkt plan new --id <plan_id> --goal '…' --beachhead '…' --hypothesis '…' \
  --channel email --wave-action message --size 10
mkt plan lint --id <plan_id>
```
- 渠道优先级用**实测数据**（求评邮件 5.3% > GitHub 站内 > X 冷触达 1.5-2.4%），不平均用力
- 每波：channel×action×size（≤内核日预算）×content_policy（千人千面）
- 观察指标是可选学习对象；只有显式受控实验才预注册假设/指标/时间窗，不设产品生死门
- `review.human_approved` 留 false——**人审不是礼节，是执行前置条件**

### ⑤ 人审门禁（审批是事件，不是布尔值）
```bash
mkt plan approve <plan_id> --by <审批人> [--days 7]
```
把 plan 呈给用户（波次/规模/内容方针/停止条件），获批后用**署名审批**：内核写
`data/approvals.jsonl` 事件 + 执行指纹 `plan_sha256`。指纹绑定计划、规范化队列、目标、正文、动作、平台、账号和工作区；批准后任何一项被改 → 指纹失配，
实跑拒跑；过期、撤销或错账号/工作区同样拒跑。
**--go 实跑必须绑定已人审 plan**：无 plan 管辖的 campaign 内核默认拒跑，仅 CLI
人工通道 `--adhoc` 显式放行（账本记 adhoc=true）；MCP 不开放 adhoc——agent 不得
自我授权。全局暂停（`mkt pause`）期间一切写操作拒跑。

### ⑥ 执行
```bash
cd <仓库>
./launch.command                       # 首次：启动 CDP 会话
mkt queue build --campaign <campaign> --channel email --wave-action message \
  --template assets/outreach.md --targets data/targets.csv   # 模板+CSV 生成队列
mkt queue lint data/queues/<campaign>.json                   # 编辑期门禁
mkt review <campaign>                                        # 逐条预检 + 留证
python3 mkt.py email message --queue data/queues/<campaign>.json          # dry-run
python3 mkt.py email message --queue data/queues/<campaign>.json --go     # 实跑（须已人审 plan）
```
- 内容由人审产出（写作走 [[warm-email-outreach]] / [[expert-review-outreach]]）；
  队列正文千人千面，占位符残留会被门禁直接拒绝
- 写操作永远先 dry-run 看截图，再 --go
- UNVERIFIED 渠道（reddit/linkedin/hn）首跑必须用户在场
- 失败即停，不重试轰炸

### ⑦ 复盘（量引擎 + 记账自动化）
```bash
mkt collect serve --port 8787                     # 本地 collector：/t/<link> 点击归因 + /events webhook
mkt collect import --source plausible --csv plausible.csv
mkt collect import --source posthog --csv posthog.csv --event-map user_signed_up=signup
mkt outcome sync --campaign <campaign>            # 账本 → 触达计数快照（幂等）
mkt link create --campaign <campaign> --channel hn --url <落地页>
mkt outcome import data/events.csv --campaign <campaign>   # CSV 导入（幂等）
mkt experiment obs --id <exp> --from-outcomes      # 事件 → 观察值（不再手抄）

# 跟进（发完之后的主线）
mkt next                                            # 到期的跟进会排在一切新动作之前
mkt followup add --platform reddit --target u/xxx --source <讨论链接>
mkt followup due                                    # 今天该跟谁
mkt followup touch <id> --result asked --note '问了流式场景'   # replied/asked/used/declined/no_reply
mkt followup stats                                  # 45 天后仍在对话的人 = 唯一硬指标

# 可配置意图学习实验
mkt intent start reddit-q4 --subs r/SideProject,r/IndieDev --weeks 4
mkt intent log reddit-q4 --sent 20 --deleted 1 --replied 4 --moved 1
mkt intent verdict reddit-q4                        # 样本不足/安全异常/观察互动，不判渠道生死

# 报价实验：按动作/按结果，也可显式测试订阅或混合形状
mkt billing --actions 20 --outcomes 2
mkt billing --experiment-id sub-1 --pricing '{"model":"subscription","monthly_fee":100}'
mkt billing --experiment-id sub-1 --record-acceptance accepted --by roy
mkt campaign report <campaign>                     # 一页纸：计划/执行/结果/下一步
```
- 账本 committed 是「发出去了」，不是「转化了」；事件与触达分栏，不合成转化率
- activation/paid 必须带 source_ref 与核验状态，漏斗主体经 contacts 归一
- 渠道有效性可用可选 `mkt experiment` 做受控学习；也可直接比较同范围结果，不把预注册当产品门槛
- `mkt ledger resolve <ref>` 处置 indeterminate（署名 + 依据，原账本行不改）
- 对照学习结果：发现错误假设→回 ② 修假设；证据不足→补证或停止，不机械换渠道
- **账本对不上平台实际状态 = 停该渠道查账。**

## 红线（内核已硬编码，agent 也不得绕过）

- 只驱动本人拥有或明确授权运营的账号；不群发、不买量、不模板批发
- 实跑必须绑定已人审 plan；adhoc 仅 CLI 人工通道显式放行且账本留痕
- 上限是内核属性：计划写多大都会被日预算/单次上限拒绝
- 回读校验不一致绝不发送（449 空正文事故固化）
- 不做对抗：不绕验证码、不伪造指纹、不用代理池绕过平台限制；代理只在合法网络需求与授权一致时绑定，不能提高配额或绕封禁
- LinkedIn 全自动默认锁死（半自动=终端人工确认）；Reddit 日 ≤3 帖；HN 日 ≤1 帖（Show HN 限自有项目，提交后 1h 内发首条作者评论）
- 结果事件不得编造或近似：activation/paid 必须真实发生并带核验状态；实验观察只在 running 期接受

## 与其他 skill 的分工

| 场景 | 用哪个 |
|---|---|
| GTM 方案/执行/复盘（本协议） | growovo-gtm |
| 外联邮件写作 | warm-email-outreach（求评类 → expert-review-outreach） |
| 公司账号找人/换人 | b2b-account-mapping |
| 客群需求研究（读侧深研究，研发前上游） | askovo |
| GitHub 项目经营（体检/巡检/社区响应） | github-project-ops |


## 内容写作与整篇审稿

内容运营任务使用 `mkt content brief/audit/review`。先阅读 `docs/playbooks/战术/reader-first-editorial.md`：写作目标是读者能理解并做判断，博客也不能写成内部实验日志。`audit` 的结构通过不代表整篇合格；用 `review` 输出的十项清单逐篇审查并填写原文引用。修改正文后必须重做版本绑定审稿，发布状态继续以实际回执为准。
