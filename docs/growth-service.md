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
- 渠道动作的 `completed` 必须带完成类型：`verified`（回执含 `action_id/channel/sent_at/idempotency_key/readback`）或 `attested_manual`（具名人工声明并说明原因）。只有 `verified` 计入 `completed_verified`；`attested_manual` 可解除内部依赖，但下游标 `dependency_quality: attested`。没有回执也没有人工声明时，状态层直接拒绝。

```bash
python3 mkt.py growth attempts SERVICE_ID
python3 mkt.py growth recover
python3 mkt.py growth reconcile SERVICE_ID --action github-engage \
  --target issue:owner/repo#7 --text "原始回复正文" --by "$USER"
```

外部动作租约过期后进入 `waiting_verification`，本地无副作用动作才重新变为 `pending`。GitHub 对账只读评论：唯一正文匹配且有真实 URL 才完成原动作；零匹配保持待核验，多匹配转人工。定时作业在写兼容状态文件前先保存最终结果，状态文件写盘失败不会导致成功作业再次执行。

所有外部写都先取 `action_gateway` 的账号/路线执行租约。`action`、队列、周期、workflow 重放和 growth 执行走 `action_gateway → action_runner → pipeline`；仍直接调用原渠道实现的 CLI 快捷入口（`mkt github/hn/reddit/linkedin`、`mkt email`、`mkt x --legacy-cdp/message/engage`）也在执行前取同一把 lease。SQLite `execution_leases` 按账号和执行路线串行化写入，第二个执行者返回 `account_busy`，只读工作不受影响；租约在 `finally` 中释放。夜间波次先用本地队列、停写状态、配额和授权计算可执行集合，显式波次收件在零可执行动作时不打开浏览器；策略配置的常驻观测独立运行，即使零发送也报告私信。

## 2.2 账号 × 平台 × 动作能力视图

能力记录按 `account + platform + action + route` 保存，分别描述 `read / write / verify` 三项操作。实现、实测、当前可用和授权有效是四个独立条件；标记实测必须提供证据引用和时间，写入与回读任一未通过都不会进入可执行状态。

```bash
python3 mkt.py growth capability-upsert --file capability.json --by "$USER"
python3 mkt.py growth capabilities --account default --platform github
```

接单时服务保存能力缺口快照，重复 `start` 会刷新同一编号的快照。执行时结构化能力记录优先于布尔可用性参数，授权过期、撤权、账号暂停或回读未验证会返回具体缺失原因。MCP 只能读取能力，不允许通过 `capability-upsert` 自行声明实测通过。

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

X preflight 和默认验收只走 WebBridge 受管标签页，不调用 `verify_credentials`，不读取 9222/9223 或迁移 profile。预检复用或创建一个受管页面、观测当前账号并核对 owner scope，`writes=0`；真实发布/回复由同一个执行器返回 ID/URL。旧 CDP 只能显式 `--legacy-cdp` 使用，不能作为默认健康检查。

## 3. 机会与统一入站

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

## 7. MCP

`mkt_growth` 使用与 CLI 相同的子命令和参数，并共享同一工作区状态。调用 `run` 或 `reconcile` 时，MCP 用 `action` 指定子命令、用 `action_id` 指定增长动作，避免参数冲突。CLI 的 `compile/start/status/next/attempts/recover/reconcile/transition/evaluate/resolve/run/plan/advise/brief/benchmark` 及 GitHub、机会、入站、资产、实验子命令均可通过该入口调用；`github-webhook-serve` 是长驻命令，不适合由请求响应式 MCP 调用。

## 8. 当前验收边界

- 本地合同、控制器、机会、入站、资产、实验、调度和 MCP 有离线回归与真实命令验收。最新全量运行 **2450/2451 项通过**；唯一错误是既有 `test_original_x_publish_has_one_gate_result` 测试计划缺少 `content_policy`，单独运行也失败，与本次 X 路线改动无关。X 默认路线、恢复、队列、CLI 和身份相关测试均通过。
- 受管 CDP 的页面注册、复用、断页恢复和禁止直接 `new_page()` 有离线验证；只读 live 检查已复用同一 X 页面，标签数 `3 → 3`。
- GitHub App 已在自有仓库创建 [Issue #41](https://github.com/roy-tong/Growovo/issues/41) 并发表 [canary 评论](https://github.com/roy-tong/Growovo/issues/41#issuecomment-7122950093)。独立 GET 核对通过，重复创建和评论均被拦截，外部匹配对象各为 1。Discussion 写 canary 未执行。
- Gmail 已在授权测试账号内完成独立线程自测，message/thread ID 均为 `1a20c07fc90af2fa`；独立 GET 的线程、收件人、主题和正文一致，重复提交后 Sent 匹配仍为 1。
- 一条 X 回复 canary 已在自有账号完成并公开核对：<https://x.com/elliwoodtong/status/2124902346899742875>，账本与 OperationsLog 一致，标签数未增加。重写后的默认验收又完成一条[发布 canary](https://x.com/elliwoodtong/status/2125168278469583248)：公开页面独立回读命中，受管标签数 `2 → 2`，同正文重复提交被 `already_done_content` 拦截且零第二次写入。上述结果只证明对应渠道的受控交付，不证明持续生产 Worker、业务激活或收入。
