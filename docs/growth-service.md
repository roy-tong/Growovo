# AI Growth Service

Growovo 的 Growth Service 把产品事实和业务目标编译成可执行动作，并在 Agent 会话之外持久保存服务状态。平台规则与合规口径只提示风险，不单独阻断业务；它保留账号授权、暂停、DNC、配额、身份、回读和未知结果保护，默认表达真实执行意图，只有显式选择才 dry-run。

## 1. 从目标合同开始

```bash
python3 mkt.py growth compile --file examples/growth-goal.json
python3 mkt.py growth start --file examples/growth-goal.json
python3 mkt.py growth next SERVICE_ID
```

合同支持小时级实验和长期 campaign、一个主目标与多个辅助目标、Operator / Autopilot / Advisor 三种模式，以及 `preview / live / live_with_manual_fallback` 三种执行意图。每个动作都带目标、假设、人群、资产、CTA、转化事件、测量引用和依赖。

`service_id` 由合同内容确定性生成，请以 `growth start` 的实际输出为准，不要照抄示例 ID。

## 1.5 从真实仓库启动

```bash
python3 mkt.py growth intake --repo /path/to/repository \
  --slug owner/repo --read-metrics --out intake-goal.json
python3 mkt.py growth start --file intake-goal.json
python3 mkt.py growth run SERVICE_ID --action diagnose-product --by "$USER"
```

Intake 读取 README、Quickstart、AGENTS.md、llms.txt、激活事件合同和 pyproject 项目名，生成带证据哈希的目标合同。`--read-metrics` 显式读取 GitHub 公开 Star/Fork/open issues；未读取或失败时保持 `UNKNOWN`。仓库里已有 demo 也不自动等于已复现，仍需 `asset-prepare` 的真实命令证据。

## 1.6 实时业务读数

```bash
python3 mkt.py growth observe-live SERVICE_ID --phase baseline \
  --repos roy-tong/Askovo,roy-tong/Growovo,roy-tong/Verovo \
  --handle elliwoodtong --by "$USER"
```

GitHub 分仓库读取 Star，全部成功后才合计；X 通过 WebMonitor 受管页读取关注者与曝光，保存页面文本、截图和 JSON 证据。页面按内容就绪而不是固定秒数等待，标准 `beforeunload` 只处理一次，验证码和风控异常立即停止。读不到的指标保持 `UNKNOWN`，不会写成 0。`baseline-observe / final-observe` 作业也可直接声明 `repos` 和 `x_handle`，无需预写 metrics 文件。

## 2. 执行与人工交接

```bash
# 本地诊断
python3 mkt.py growth run SERVICE_ID --action diagnose-product --by "$USER"

# 记录同源基线和终点
python3 mkt.py growth run SERVICE_ID --action baseline-observe \
  --by "$USER" --metrics metrics.json
python3 mkt.py growth evaluate SERVICE_ID --by "$USER" --metrics metrics.json

# 只预览解析，不执行外部写
python3 mkt.py growth resolve SERVICE_ID --action x-publish

# 有真实人工时间预算时选择下一批工作
python3 mkt.py growth plan SERVICE_ID --minutes 20
python3 mkt.py growth brief SERVICE_ID
```

官方 API/SMTP 优先，受管 CDP 次之，无法执行时可生成人工工作包。live 不可用且不允人工降级时返回 `LIVE_BLOCKED`，不会自动变成 dry-run。人工工作包保持 `waiting_manual`，不会反复占用下一动作；只有真实 `committed` 和要求的核验证据才把渠道动作标为完成。

`growth resolve/run` 和队列结果同时给出 Agent 可执行的 `solution/recovery`：实际路线、自动恢复步骤、备用交接和完成条件。X 的身份或旧 `verify_credentials` 失败优先恢复为 `ensure_managed_tab → observe_identity → owner_attested_preflight`，不是直接要求重新登录 9222。`indeterminate` 的第一步固定为 `reconcile_receipt` 且 `retry_write=false`，不会把恢复方案变成自动重发。

## 2.1 可恢复任务运行时

`state/growth-runtime.sqlite3` 是增长服务、动作尝试、事件、来源事件、定时作业结果、能力记录、连接器游标以及会话/机会/资产/实验状态的权威事务存储。会话与会话消息分别规范化保存，并发导入不会退回整文档覆盖。原 JSON/JSONL 文件保留为读取时生成的兼容投影；旧数据首次读取时迁入 SQLite，删除兼容 JSON 后可从权威状态重建。schema 初始化使用进程锁与文件锁并启用 WAL，GitHub/Gmail 独立读取最多四路并发。CLI、MCP 和定时器使用同一服务编号与动作尝试编号。

- `pending`：依赖已满足时可以认领。
- `in_progress`：已有执行者，其他入口只读取原尝试。
- `waiting_manual`：需要负责人处理，不参与自动选择。
- `waiting_verification`：外部结果未知，先查回执，禁止自动重发。
- `completed / blocked`：分别表示已满足当前动作验收、明确受阻；业务结果仍单独观察。
- 渠道动作的 `completed` 必须带完成类型：`verified`（回执含 `action_id/attempt_id/account/target/channel/sent_at/idempotency_key/readback`，且 committed 账本逐项一致）或 `attested_manual`（具名人工声明并说明原因）。只有 `verified` 计入 `completed_verified`；`attested_manual` 可解除内部依赖，但下游标 `dependency_quality: attested`。没有回执也没有人工声明时，状态层直接拒绝。对账使用 Attempt 认领时冻结的 target/text，调用方后续传入的替换值不会生效。

```bash
python3 mkt.py growth attempts SERVICE_ID
python3 mkt.py growth recover
python3 mkt.py growth reconcile SERVICE_ID --action github-engage \
  --target issue:owner/repo#7 --text "原始回复正文" --by "$USER"
```

外部动作租约过期后进入 `waiting_verification`，本地无副作用动作才重新变为 `pending`。GitHub 对账只读评论：唯一正文匹配且有真实 URL 才完成原动作；零匹配保持待核验，多匹配转人工。命令中的 `--target/--text` 只用于没有冻结 payload 的旧记录，新 Attempt 一律读取认领时冻结的值。授权额度在动作认领后按 Attempt 预留；明确未执行会释放，已完成或外部结果未知保留，进程崩溃不会自动变成可重试额度。定时作业在写兼容状态文件前先保存最终结果，状态文件写盘失败不会导致成功作业再次执行。

所有外部写都先取 `action_gateway` 的账号/路线执行租约。`action`、队列、周期、workflow 重放和 growth 执行走 `action_gateway → action_runner → pipeline`；仍直接调用原渠道实现的 CLI 快捷入口（`mkt github/hn/reddit/linkedin`、`mkt email`、`mkt x --legacy-cdp/message/engage`）也在执行前取同一把 lease。SQLite `execution_leases` 按账号和执行路线串行化写入，第二个执行者返回 `account_busy`，只读工作不受影响；租约在 `finally` 中释放。夜间波次先用本地队列、停写状态、配额和授权计算可执行集合，显式波次收件在零可执行动作时不打开浏览器；策略配置的常驻观测独立运行，即使零发送也报告私信。

## 2.2 账号 × 平台 × 动作能力视图

能力记录按 `account + platform + action + route` 保存，分别描述 `read / write / verify` 三项操作。实现、实测、当前可用和授权有效是四个独立条件；标记实测必须提供证据引用和时间，写入与回读任一未通过都不会进入可执行状态。

```bash
python3 mkt.py growth capability-upsert --file capability.json --by "$USER"
python3 mkt.py growth capabilities --account default --platform github
```

接单时服务保存能力缺口快照，重复 `start` 会刷新同一编号的快照。执行时结构化能力记录优先于布尔可用性参数，授权过期、撤权、账号暂停或回读未验证会返回具体缺失原因。MCP 只能读取能力，不允许通过 `capability-upsert` 自行声明实测通过。

可选执行反馈：adapter 同时设置 `feedback_enabled=true` 且 Worker 配置具名执行者时，执行结果会汇总为下一轮 cycle 的 `execution_result` 观察。原 cycle 保存 `feedback_followup`，新 cycle 保存 `parent_cycle_id` 与 `feedback_depth`；默认最大深度 3，可用 `max_feedback_depth` 调整。重复 Worker 轮次不会重复创建同一父 cycle 的反馈，达到深度后停止，防止无限自动循环。反馈只影响后续宿主观察，不直接产生外部写。

## 2.3 GitHub App Community Operator

覆盖 Issue、Issue Comment、Discussion 和 Discussion Comment。Webhook 先验签并持久化，再在事务中投影到统一入站并标记已处理；重复投递不产生重复会话，并发评论不会覆盖会话，投影失败可从来源事件重放。同一 Issue 或 Discussion 的后续评论分别归并为一个 Conversation，各条互动 ID 分别保留。增量同步用于补齐 Webhook 丢失或服务停机期间的事件。Issue 使用官方 REST，Discussion 使用官方 GraphQL。

准备最小权限 GitHub App 和安装范围后，通过环境变量提供配置，不写入仓库：

```bash
export MKT_GITHUB_APP_ID="123456"
export MKT_GITHUB_APP_INSTALLATION_ID="456789"
export MKT_GITHUB_APP_PRIVATE_KEY_PATH="/secure/path/private-key.pem"
export MKT_GITHUB_WEBHOOK_SECRET="only-in-the-local-environment"

python3 mkt.py growth github-sync SERVICE_ID --repo owner/repo
python3 mkt.py growth github-webhook-serve SERVICE_ID --host 127.0.0.1 --port 8765
```

JWT 使用系统 OpenSSL 签名，installation token 只在内存中传递。已有短期 installation token 时也可通过 `MKT_GITHUB_APP_INSTALLATION_TOKEN` 提供。Webhook 默认只绑定本机地址，公网隧道和反向代理由部署者单独配置。处理 `issues/opened`、`issue_comment/created`、`discussion/created` 与 `discussion_comment/created`；其他事件明确忽略。

执行合同优先选择 `github_app`，仍可使用既有 Token API、受管 CDP 或人工路径。Issue 创建使用 `github/publish`、回复使用 `github/reply`，都经过审批、Gate、配额、账号执行路线和动作认领；创建或评论 POST 后独立 GET，Discussion mutation 后独立查询，核对对象 ID/URL、正文、作者和目标。2026-11-24 已在自有仓库完成真实 Issue 创建与评论 canary；Discussion 和其他仓库仍需要各自的授权与验收。

## 2.4 Gmail 双向会话运营

Gmail Lite 使用周期全量/增量同步，Pro 增加 watch 与 Cloud Pub/Sub 拉取。首次同步保存最新消息 `historyId`，后续调用 `history.list`；游标过期返回 404 时全量重建。通知只是变化信号，只有同步成功才 ack。

```bash
export MKT_GMAIL_ACCESS_TOKEN="short-lived-access-token"
# 或配置 MKT_GMAIL_REFRESH_TOKEN / MKT_GMAIL_CLIENT_ID / MKT_GMAIL_CLIENT_SECRET
# 也可只提供本机文件路径；refresh token 存在时优先刷新，不使用文件中的过期 access token
export MKT_GMAIL_OAUTH_TOKEN_PATH="/secure/path/token.json"
export MKT_GMAIL_OAUTH_CLIENT_PATH="/secure/path/client.json"

python3 mkt.py growth gmail-sync SERVICE_ID
python3 mkt.py growth gmail-watch-renew SERVICE_ID --topic projects/PROJECT/topics/TOPIC
python3 mkt.py growth gmail-pubsub-pull SERVICE_ID \
  --subscription projects/PROJECT/subscriptions/SUBSCRIPTION
```

Gmail 回复通过 `gmail-api` 进入统一写入管道，使用 `threadId` 关联会话；新邮件可留空 `thread_id` 并提供 `subject`，发送后使用 Gmail 返回的 threadId。发送后独立 GET 核对线程、收件人和正文；结果未知时只查询 Sent 记录，唯一匹配才完成原动作。SMTP 队列路径保留，OAuth 凭据只经环境变量或上述本机文件路径传入，不写入报告。

## 2.5 长期 Worker 与只读上线检查

```bash
# 单轮验收：恢复过期动作；可按需增加 GitHub/Gmail 连接
python3 mkt.py growth operator-serve SERVICE_ID --once
python3 mkt.py growth operator-serve SERVICE_ID --once --wave 04-0105
python3 mkt.py growth operator-serve SERVICE_ID \
  --repo owner/repo --gmail-sync --watch-topic projects/PROJECT/topics/TOPIC \
  --pubsub-subscription projects/PROJECT/subscriptions/SUBSCRIPTION
python3 mkt.py growth operator-serve SERVICE_ID --max-cycles 120 --interval-seconds 1
python3 mkt.py growth worker-status SERVICE_ID
python3 mkt.py growth worker-history SERVICE_ID --limit 120

# 只读 preflight，不执行写入；可按渠道独立使用
python3 mkt.py growth live-preflight --repo owner/repo --gmail
python3 mkt.py growth live-preflight --x --x-action publish

# 与默认产品路线一致的 X 验收；live 使用工作区已有 owner-attested 授权
python3 scripts/acceptance_live_x.py --preflight --action publish --authorize-by "$USER"
python3 scripts/acceptance_live_x.py --action publish --authorize-by "$USER"
```

Worker 每个组件独立记录成功或失败，单个渠道故障不阻断其他恢复和同步。心跳保存在同一 SQLite 中。每轮另有独立 `cycle_id` 和持久历史，`worker-history` 可回读，CLI 每轮输出结果；`--max-cycles` 用于有界验收，不把“进程还活着”当成“每轮都正确执行”。120 轮无外部动作 soak 已通过，但不等于真实渠道长期生产通过。`operator-tick` 可在 `cycle jobs-tick` 配置中复用同一 `run_once`。持续 Worker 与 Webhook 只能由具名操作者通过 CLI 启动，MCP 不得启动长驻服务。

可选宿主 Agent 唤醒：

```bash
python3 mkt.py growth cycle wake SERVICE_ID --cycle CYCLE_ID --adapter host-adapter.json
python3 mkt.py growth operator-serve SERVICE_ID --host-adapter host-adapter.json
```

`growovo.host-adapter/1` 声明 adapter/version/producer、受控 argv、最小环境变量透传、超时和具名授权。适配器把 open cycle request 写入私有唤醒目录，运行本地宿主进程，从 `proposal.json` 或 stdout 读取 `growovo.growth-cycle-proposal/1`，再复用现有 `growth_cycle.propose` 风险判定。重复唤醒不重复生成；非法输出保持 open。默认只增加 proposal 环节。只有操作者显式配置 `operator-serve --execute-accepted-by NAME` 或 scheduler 的 `execute_accepted_by`，Worker 才逐 cycle 调用现有 `cycle execute`；拒绝项仍不执行，外部动作继续经过全部授权、额度、DNC、回执和恢复链。

X preflight 和默认验收只走 WebBridge 受管标签页，不调用 `verify_credentials`，不读取 9222/9223 或迁移 profile。预检复用或创建一个受管页面、观测当前账号并核对 owner scope，`writes=0`；真实发布/回复由同一个执行器返回 ID/URL。旧 CDP 只能显式 `--legacy-cdp` 使用，不能作为默认健康检查。

## 3. 机会与统一入站

新机会必须包含带时区的 `observed_at`。可显式设置 `valid_until`，或以 `freshness_hours`（默认 48 小时）计算有效期；有效期必须晚于观察时间。只读投影把到期机会标为 `expired`，缺时间的历史记录为 `unknown`；两者保留发现证据，但不进入 `next_action` 或生命周期跟进队列。时效门禁不改变评分、授权、DNC 或渠道执行规则。

```bash
python3 mkt.py growth opportunity-import SERVICE_ID --file opportunities.json
python3 mkt.py growth inbox-import SERVICE_ID --file interactions.json
python3 mkt.py growth inbox-draft SERVICE_ID \
  --conversation conv-github-1 --text "具体答复" --by "$USER"
python3 mkt.py growth inbox-terminal SERVICE_ID \
  --conversation conv-github-1 --status committed \
  --receipt-ref "https://真实回复链接" --by "$USER"
python3 mkt.py growth inbox-status SERVICE_ID --stale-minutes 60
```

已有用户互动优先于陌生触达。忽略需要原因，`committed` 需要真实回执；没有证据时保持未知，不把扫描数量当成处理完成。

## 4. 可复现资产与渠道复用

```bash
python3 mkt.py growth asset-create SERVICE_ID --file asset.json
python3 mkt.py growth asset-repurpose SERVICE_ID --asset demo --channels x,github,email
```

资产生产执行显式授权的 argv 命令，只有退出码 0 才标记 `reproduced`。GitHub/X/Email 版本保留同一事实和 CTA，不机械复制结构，也不生成新的结果承诺。

## 5. 实验与人工对照

```bash
python3 mkt.py growth experiment-create SERVICE_ID --file experiment.json
python3 mkt.py growth experiment-observe SERVICE_ID --experiment EXP_ID --file events.json
python3 mkt.py growth experiment-diagnose SERVICE_ID --experiment EXP_ID
python3 mkt.py growth advise SERVICE_ID
python3 mkt.py growth benchmark --manual manual.json --assisted assisted.json
```

实验按信号、点击、安装、激活、Star/关注分层，区分人群、内容、首次成功和辅助资产问题。人工对照只在工作定义一致时比较人工分钟、验收任务、质量和重大错误；所有因果功劳保持 `not_claimed`。

### 渠道投入效率

```bash
python3 mkt.py growth efficiency SERVICE_ID --file channel-investment.json
```

`growovo.channel-investment/1` 显式登记半开窗口、各渠道人工分钟与时间来源，以及可选成本金额、币种和成本来源。`growth efficiency` 将其与窗口内实际会话、满足 verification/source_ref 的激活事件对照，输出单位互动和单位激活的人工/成本投入。

激活事件必须显式携带 `channel` 才进入该渠道分母；缺失或未声明渠道进入 `unassigned_verified_activations`，未合格事件单列。零分母返回 `null`，不跨币种求和。该投影不证明渠道导致激活，不计算 ROI，也不自动建议增减预算。

只读导出共享实验合同：

```bash
python3 mkt.py growth experiment-export SERVICE_ID \
  --experiment EXP_ID --design experiment-design.json --out experiment-contract.json
```

设计文件显式补充证伪条件、分组、主指标、可比性、注册人和外部动作授权。每个事件必须通过 `observation_bindings` 绑定分组、指标、证据等级和资格；缺绑定即拒绝，不把事件自动当成合格证据。默认结果只是 `inconclusive` 与描述性关联，输出不覆盖已有文件。

### 生命周期与跟进投影

```bash
python3 mkt.py growth lifecycle SERVICE_ID \
  --identity-map identity-map.json --stale-minutes 60
```

该命令只读现有机会、会话和实验事件，按来源前缀主体生成 `discovery → engagement → trial → activation → retention → payment` 的最远已观察阶段。跨来源身份只在显式 `identity-map` 中合并；activation/retention/payment 需要 verification method/ref 与 source_ref，retention 还需更早的已核验 activation，未合格事件单列。缺少前序阶段证据时保留缺口，不把单个高阶事件当成完整漏斗。

生命周期中的激活、留存和支付均要求已核验的 method/ref/source_ref；留存还必须有时间更早的已核验激活，否则进入 `unqualified_events`，不能凭一次使用或自述进入留存。`followup_queue` 优先既有用户和开放会话，再列待处理机会；它是候选工作队列，不是触达授权，实际执行仍需 DNC、账号、范围、配额和执行门禁。投影同时复用执行层唯一的 `suppress.txt ∪ contacts DNC/bounced` 抑制源，命中项从可执行队列移到 `suppressed_followups`，不自动解除抑制；抑制源损坏时整个投影 fail closed。

### 产品反馈导出

```bash
python3 mkt.py growth feedback-export SERVICE_ID \
  --file feedback-reviews.json --by PRODUCT_REVIEWER --out product-feedback.json
```

具名复核人按 `bug / feature_request / usability / praise / other` 分类并给摘要和逐字 `evidence_quote`。引文必须来自现有会话正文或消息；稳定反馈 ID 去重，冲突结论拒绝。输出只写新文件，源会话不变，反馈保持 `canonical_evidence=false` 和 `product_decision_status=not_decided`，不自动形成需求或产品决定。CLI 与 MCP 共用该投影。

### 周期业务汇报

```bash
python3 mkt.py growth report SERVICE_ID \
  --start 2026-11-24T00:00:00Z --end 2026-11-25T00:00:00Z --format md
```

`growth report` 将 cycle 创建数按 `created_at`、实际执行分派及结果按 `executed_at` 分别归入半开窗口，跨午夜执行不会回记到创建日。缺少 `executed_at` 的历史执行记录计入 `undated_records`，不猜测所属期间。报告列出待提案、待审核、阻塞执行和跟进事项。互动、激活、留存和支付来自当前生命周期累计记录，单独标记为 `current_lifecycle_records_not_period_delta`，不得解释为本期新增或因果增长。报告只读；任何 cycle JSON 损坏都会终止报告，不会静默减少工作量或失败数。

## 6. 持久调度

`cycle jobs-tick` 新增正式作业类型：

- `goal-compile`
- `opportunity-scan`
- `inbox-resolve`
- `github-sync`
- `operator-tick`
- `asset-prepare`
- `baseline-observe` / `final-observe`
- `goal-evaluate`
- `next-experiment`

它们复用既有依赖、锁、过期、失败和人工未决语义。旧的 `wave / inbox-update / inbox-collect / receipts / readiness` 继续可用。

## 6.1 引导式执行策略与备料波次

用户不需要先理解完整 schema。可以从只含产品名的草稿开始，由 `output-guide` 每次询问一个问题；每个问题说明用途、给示例和建议默认值。`--set` 以 JSON 合并一个答案并立即保存，`--interactive` 逐题交互；两种入口使用同一问题顺序。

```bash
mkt growth output-guide --file draft.json
mkt growth output-guide --file draft.json \
  --set product.url --value '"https://github.com/roy-tong/Askovo"'
mkt growth output-guide --file draft.json --interactive
```

完整输入编译为 `growovo.execution-strategy/1`，分别给出业务目标、动作拆解、平台波次、备料 TODO、对象与正文、验收事件。JSON 供调度和审批使用，Markdown 供人复核。蓝图可以先有配额而没有合格素材；`readiness` 分开记录 `required / materialized / eligible / missing`，候选数不抵扣对象、上下文、正文或审查缺口。

```bash
mkt growth output-plan --file campaign.json --out execution-plan.json
mkt growth output-plan --file campaign.json --format md --out execution-plan.md
mkt growth output-lint --file execution-plan.json --require-ready
mkt growth preparation-status --file execution-plan.json --wave w01
```

`examples/execution-strategy-300.json` 是数据样例：10 波共 300 条，X 原创 30、X 回帖 180、GitHub 回帖 60、X 私信 30。编译器不内置 OVO 产品名或这组数量。

备料任务分三条支线，独立根任务可以在首波前启动：

- 原创：生成标题 → 生成正文 → `my-voice` 审查当前正文哈希 → 冻结素材。
- 回帖：挖掘话题与 KOC → 资格判断 → 生成回复 → 回复策略审查 → 冻结素材。
- 私信：挖掘对象 → 核验 DM 资格与整夜去重 → 生成邀请 → 内容审查 → 冻结素材。

`local`、`miner`、`host_adapter`、`manual` 是不同任务执行方式。host adapter 使用受控 argv、最小环境变量透传、超时和 `growovo.preparation-result/1`；缺少 adapter 或人工输入时保持等待，不伪装完成。同一 attempt 的已完成 host 结果在恢复时复用，不重复执行。

策略调度文件在顶层声明 `strategy` 和 `preparation`，每个 wave 继承策略引用。`strategy_sha256` 同时进入波次审批；队列 item IDs、正文哈希、账号、窗口继续沿用现有批准范围。没有策略引用的旧作业行为不变。

```json
{
  "strategy": "execution-plan.json",
  "strategy_sha256": "<编译输出的完整 SHA-256>",
  "preparation": {"enabled": true, "max_tasks_per_tick": 2},
  "jobs": [{
    "id": "w01", "kind": "wave", "at": "2030-01-01T20:00:00+08:00",
    "queue": "w01-reviewed.json", "accounts": {"x": "owner"},
    "targets": {"x/publish": 3, "x/reply": 18, "x/message": 3, "github/reply": 6}
  }]
}
```

`jobs-serve` 在 tick 间有界推进备料。波次缺料时只推进缺失支线并返回 `supply_not_ready`；本轮备料有进展时继续循环，不提前报 `no_runnable_jobs`。合格后冻结独立快照，默认等待 `jobs-approve` 的精确范围批准。发送和回读完成后恢复后续备料。dry-run 只读显示备料状态，不运行 miner 或 adapter。429、撤权、验证码、身份异常、未知回执和安全停止优先于备料循环，不能用继续挖掘掩盖执行故障。

冻结不仅检查缺料，也检查每产品／平台／动作的超额和当前／未来波次重复 reply/message 目标；reply 按帖子／Issue URL，message 优先按 handle。已结算旧波次只作历史触达对照，不会单独卡住新补位。问题以 `supply_scope_invalid`、`surplus` 和 `scope_errors` 返回，不能拿多出的素材发送。补位项必须显式填写 `replaces`，lint 根据 `execution_results` 核验原项可补位、产品／平台／动作一致、目标未触达且一对一。补位数量单列，不抵扣原始 `materialized / eligible / missing`。

调度报告的 `recovery.action` 区分 `wait`、`retry_same_job`、`approve_same_job`、`reload_job`、`diagnose_build`、`reconcile` 和 `new_recovery_job`。只有带 `retry_of` 且原审批范围与窗口仍有效的过期作业允许新建恢复作业；其他失败不换 campaign 或作业 ID 掩盖。tick 的 preparation 输出只含缺口、原因、任务状态、快照哈希／条数和状态路径，完整素材与 attempts 仍在状态文件中，不在每次输出里重复传输。

当前实现不证明任何特定外部 miner、X DM 写路线、内容生成器或 my-voice reviewer 已完成生产验收。真实活动仍需分别核验 adapter、账号动作能力、合格素材和实际回执。

## 6.2 业务进展记录

`business-progress.jsonl` 使用 `growovo.business-progress/1`，把运行状态翻译成业务人员可读的“事实、影响、下一步”。类型包括配额核准、证据确认、问题定位、恢复决定、备料进展、修复验证和执行结果；计数、证据路径和技术附录保留在同一记录。它由确定性运行状态生成，不把平台 committed 升级为首用或增长。

```bash
mkt cycle progress --wave w08
mkt cycle progress --type recovery_decision --limit 20
mkt cycle progress --json
mkt cycle progress --technical
```

默认中文输出隐藏技术错误码；`--technical` 显式显示。`jobs-tick`／`jobs-serve` 的新业务进展走 stderr，JSON 输出保持 stdout。dry-run 不写业务进展；连续相同状态去重；日志故障单列，不改写平台结果。详细语义见 `docs/scheduled-batches.md`。

## 6.3 对象、内容质量与规模增量分配

`growovo.execution-quality/1` 为每个策略 item 计算目标匹配分、内容分、问题列表和沟通钩子。默认建议放量质量分为 70；`readiness.eligible` 只表示可发送，`readiness.quality.eligible` 才表示达到放量门槛。评分是启发式审查，不是转化预测。`campaign.quality_floor` 可设置门槛，`output-lint --require-ready` 会阻断低于门槛的素材。

沟通钩子包括 `recalculate_together`、`reproduce_failure`、`review_offer`、`compare_accepted_results` 和 `task_offer`，分别给输入、复现步骤、可观察结果和低门槛 CTA。目标是减少泛话题、通用 `I would` 和缺少可执行帮助的回复，而不是限制规模。

`growovo.outcome-funnel/1` 分开记录 committed、合格互动、资产点击、首用、接受结果、关注／Star 和第二任务。观察不完整时比率为 UNKNOWN；完整观察中的零事件才记 `observed_zero`。`growovo.experiment-allocation/1` 默认保留 20% 高质量探索，其余按质量与真实结果放量，不平均分配产品。结果事件可按 `campaign_id` 自动补入分配证据。

```bash
mkt growth quality-funnel --file examples/growth-outcome-events.json --complete
mkt growth quality-allocate --file examples/growth-quality-allocation.json \
  --events examples/growth-outcome-events.json --complete
```

队列 ID 使用 `campaign:item` 并保留 `strategy_item_id`，跨活动审计不再依赖时间过滤。零配额在编译时省略；`content_rules` 错嵌进 `platforms` 会在批准前拒绝。业务进展会把 GitHub 403、半开延后、私信 unsupported 和原创链接问题翻成中文事实与下一步，deferred 同步进入业务结果卡。

## 6.4 动态策略研究、选择与调整

`core/strategy_intelligence.py` 复用 `core/tactics.py` 的 20 个机制族和 `core/experience.py` 的 594 条经验记录，不把任何单一路线写成长期默认策略。每张策略卡包含意图、阶段、渠道、所需资产、证据出处、语料实例和使用边界。当前正式策略 eligible 为 0，60 条只能进入内容实践／限定实验；原始语料规模不代表策略有效性。

`growovo.strategy-decision/1` 按意图、阶段、渠道、质量和接受结果排序，返回前三候选、选择理由和缺失输入。结果调整分别映射到 `explore`、`revise_quality`、`switch_audience_or_hook`、`improve_asset`、`improve_first_task`、`build_retention_loop`、`scale_up` 和 `recover_channel`；倍率只约束下一轮实验，不覆盖账号和安全门。

```bash
mkt growth strategy-census
mkt growth strategy-select --file examples/strategy-context.json
mkt growth strategy-adjust --file examples/strategy-context.json \
  --outcomes examples/strategy-outcomes.json
```

机制与证据图谱见 `docs/strategy-atlas.md`。选择分数是上下文匹配度，调整依据是当前观察，不证明策略的外部效度或因果效果。

## 7. MCP

`mkt_growth` 使用与 CLI 相同的子命令和参数，并共享同一工作区状态。调用 `run` 或 `reconcile` 时，MCP 用 `action` 指定子命令、用 `action_id` 指定增长动作，避免参数冲突。CLI 的 `compile/start/status/next/attempts/recover/reconcile/transition/evaluate/resolve/run/plan/advise/brief/benchmark` 及 GitHub、机会、入站、资产、实验子命令均可通过该入口调用；`github-webhook-serve` 是长驻命令，不适合由请求响应式 MCP 调用。

## 8. 当前验收边界

- 本地合同、控制器、机会、入站、资产、实验、调度和 MCP 有离线回归与真实命令验收。最新全量运行 **2450/2451 项通过**；唯一错误是既有 `test_original_x_publish_has_one_gate_result` 测试计划缺少 `content_policy`，单独运行也失败，与本次 X 路线改动无关。X 默认路线、恢复、队列、CLI 和身份相关测试均通过。
- 受管 CDP 的页面注册、复用、断页恢复和禁止直接 `new_page()` 有离线验证；只读 live 检查已复用同一 X 页面，标签数 `3 → 3`。
- GitHub App 已在自有仓库创建 [Issue #41](https://github.com/roy-tong/Growovo/issues/41) 并发表 [canary 评论](https://github.com/roy-tong/Growovo/issues/41#issuecomment-7122950093)。独立 GET 核对通过，重复创建和评论均被拦截，外部匹配对象各为 1。Discussion 写 canary 未执行。
- Gmail 已在授权测试账号内完成独立线程自测，message/thread ID 均为 `1a20c07fc90af2fa`；独立 GET 的线程、收件人、主题和正文一致，重复提交后 Sent 匹配仍为 1。
- 一条 X 回复 canary 已在自有账号完成并公开核对：<https://x.com/elliwoodtong/status/2124902346899742875>，账本与 OperationsLog 一致，标签数未增加。重写后的默认验收又完成一条[发布 canary](https://x.com/elliwoodtong/status/2125168278469583248)：公开页面独立回读命中，受管标签数 `2 → 2`，同正文重复提交被 `already_done_content` 拦截且零第二次写入。上述结果只证明对应渠道的受控交付，不证明持续生产 Worker、业务激活或收入。

## 9. Agent Adoption 黑盒实测

```bash
python3 mkt.py geo agent-adoption run \
  --suite agent-adoption-suite.json --workdir ./agent-workspace \
  --out ./agent-adoption-evidence --json
```

`growovo.agent-adoption-suite/1` 声明产品、宿主名称/模型/argv、最小环境变量透传、具名操作者授权，以及 `discover → install → first_success` 三个自然语言步骤。每步启动全新宿主进程，复用同一测试工作区；成功条件可检查退出码、stdout/stderr 片段和工作区文件。证据目录保存每步输出、耗时、检查和聚合报告，禁止覆盖。

默认不继承宿主凭据环境。该运行器是操作者授权的进程测试，不是网络沙箱；`fresh_process_per_step=true` 不等于已验证的新模型会话。合成/demo 输入全过仅为 `OBSERVED_SUCCESS`；`REAL_SUCCESS` 还要求真实授权输入、无答案提示和具名人工接受。发现、安装和首次成功均不等于留存、支付或业务价值。
