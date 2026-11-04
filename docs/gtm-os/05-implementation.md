# GTM Work OS · 本地实现与使用说明（v0.8.0）

> 状态：R0 数据纠错 + R1 共享业务内核已在本地实现；不是 40 个岗位全部专业自动化，也不是外部业务效果证明。

## 1. 已实现与未实现的边界

| 能力 | 本地实现 | 边界 |
|---|---|---|
| 能力注册 | `core/capabilities.py` + `capability_data.json`：18 能力域、40 岗位族，含职责、三层 KPI、SOP 摘要、交付物、模板、交接、人审、DoD | 40 岗位是完整注册覆盖，不等于 40 条完整 Step Contract 都已可运行 |
| 模板注册 | `core/templates.py` + `template_data.json`：T01–T32，Base Envelope、专业字段、QA、版本与实例 lint | 模板不会自动证明内容正确；业务审阅仍是独立步骤 |
| 上下文与目标 | `ContextStore` / `ObjectiveStore`：不可变快照、来源、版本、主 KPI | `user_declared` 不自动升级为 Askovo 已研究 |
| WorkOrder | 创建、lint、ready、依赖、状态机、下一步 | 当前以本地 CLI / MCP 为主，不是完整 Web 组织协作系统 |
| SOP | 版本注册、5–8 步 lint、StepRun、产物 / DoD / 人审、阻塞与重开 | 内置一条完整 `sop-devrel-first-success@1.0.0`；其他岗位的摘要需继续展开 |
| 交付物 | 模板实例、提交、逐项 QA、`core/permissions.py` Actor 类型检查、R3/R4 作者终审隔离、修订版本 | 本地人员签名与权限约束，不冒充完整企业 IdP / RBAC |
| 交接 | accepted 交付物、指定接收人、拒收返回步骤、SLA 到期投影 | 通知 / 创建不自动算接收 |
| KPI | 版本化指标、去重、过滤、scope、成熟度、确定性重算 | 当前支持计数和分子 / 分母比率，不是通用 BI 引擎 |
| 执行桥 | accepted Deliverable + 当前 action_request 步骤 → 既有 plan/queue；provenance 进入审批指纹 | 不直接发送；实际 dry-run/go 仍走原命令和门禁 |
| 转化纠错 | cohort 漏斗、全阶段 evidence、资格 / 报价前置、事件追加、初始阶段限制 | paid 保留 evidence-only 兼容；可附 verification method/ref，升级强核验需行业接入 |
| 结果同源 | CLI / MCP 读取 TaskOutcome；平台、任务、业务三层分离 | 观察不自动证明增量或因果 |

## 2. 30 秒体验

```bash
python3 mkt.py work-demo
python3 -m unittest discover -s tests
python3 mkt.py demo
```

`work-demo` 在独立沙盒完成：

```text
不可变上下文 → 运营目标 → WorkOrder ready → SOP run
→ 交付物提交与独立验收 → 接收人签收 → accepted 交付物生成 plan/queue
→ 具名计划审批 → 独立产品事件计算 KPI
```

最后会明确显示：**plan/queue 已落盘且审批，但没有发送账本、没有网络、没有账号**。

## 3. CLI 产品面

### 3.1 能力与模板

```bash
mkt capability list
mkt capability show --capability C18
mkt capability show --role-family 46
mkt capability lint

mkt template list
mkt template show T30
mkt template lint
mkt template lint --file deliverable-instance.json
```

### 3.2 上下文、目标与工作单

```bash
mkt context import --file context.json --by roy
mkt context show ctx-example-product --version 2026-11-04.1

mkt objective create --file objective.json --by roy
mkt work create --file work-order.json --by roy
mkt work lint --id wo-devrel-tutorial-001
mkt work ready --id wo-devrel-tutorial-001 --by roy
mkt work next
```

缺输入验收、上下文 / 目标 / SOP 版本、依赖或负责人时，`ready` 会写入 `blocked` 并返回具体原因，不生成执行队列。

### 3.3 SOP 运行

```bash
mkt sop list
mkt sop show sop-devrel-first-success --version 1.0.0
mkt sop start --work-order-id wo-devrel-tutorial-001 --by roy
mkt sop run --run-id run-wo-devrel-tutorial-001-0001

mkt sop complete \
  --run-id run-wo-devrel-tutorial-001-0001 \
  --step-id confirm-task-and-value-event \
  --outputs step-outputs.json --dod step-dod.json \
  --approval-by technical-reviewer --approval-scope technical_value_event \
  --by roy
```

每步产物必须包含 `deliverable_type/artifact_id/version/evidence_ref`；每项必需 DoD 必须 `pass` 且有 `evidence_ref`。缺任一项不会进入下一步。

### 3.4 交付物与交接

```bash
mkt deliverable create --file deliverable.json --by author
mkt deliverable submit --id del-tutorial --by author
mkt deliverable review --id del-tutorial --results reviews.json --by reviewer

mkt handoff create --file handoff.json --by author
mkt handoff accept --id ho-001 --by receiver
# 或拒收并回到具体步骤
mkt handoff reject --id ho-001 --by receiver \
  --reason "缺干净环境复现证据" --return-step build-and-reproduce
mkt handoff pending
```

R3/R4 作者不能终审自己的交付物；`submitted` 不等于 `accepted`；只有指定接收人可以签收或拒收。

### 3.5 KPI

```bash
mkt metric list
mkt metric show developer_activation_rate
mkt metric measure --metric-id developer_activation_rate --events events.json
mkt metric measure --metric-id developer_activation_rate --events events.jsonl \
  --campaign launch-1 --since 2026-11-01 --until 2026-11-14
```

分母为 0 返回 `insufficient_denominator + value=null`；观察窗未成熟返回 `pending_maturity + value=null`；指定 scope 后没有数据不会回退全历史。

### 3.6 accepted 交付物到执行计划

```bash
mkt work-action materialize \
  --work-order-id wo-devrel-tutorial-001 \
  --step-id authorize-execution \
  --deliverable-id demo-distribution --deliverable-version 1 \
  --strategy-file strategy.json --targets targets.csv \
  --campaign launch-1 --account authorized-account --by roy

# 以下仍是原有真实执行链，不由 Work OS 代替
mkt review launch-1
mkt plan approve launch-1 --by roy
mkt queue run data/queues/launch-1.json          # dry-run
# 只有另行明确授权后才考虑 --go
```

`work-action` 拒绝未 accepted 交付物、错误 WorkOrder、非当前 SOP 步骤及未声明 `action_request` 的步骤；不会覆盖已有 campaign plan/queue。

## 4. 只读 MCP 工具

| 工具 | 用途 |
|---|---|
| `mkt_capabilities` | 18 / 40 注册表、岗位详情与完整性 lint |
| `mkt_templates` | 32 模板、实例 lint |
| `mkt_work_order` | WorkOrder 列表、详情、下一步及关联 SOP / 交付物 / 交接 |
| `mkt_metric_measure` | 对宿主提供的事件做确定性 KPI 计算 |

MCP 不开放 WorkOrder 创建、交付物终审、交接签收或自我授权。真实写动作仍只有原有执行工具与审批路径。

## 5. 本地事实源

| 路径（工作区内） | 内容 |
|---|---|
| `context/snapshots.jsonl` | 不可变上下文导入事件 |
| `work/objectives.jsonl` | 目标版本 |
| `work/orders.jsonl` | WorkOrder 创建、修订和状态事件 |
| `work/sop-runs.jsonl` | SOP 启动、步骤完成、阻塞与重开 |
| `deliverables/events.jsonl` | 交付物创建、修订、提交和审核 |
| `work/handoffs.jsonl` | 交接创建、接收与拒收 |
| `metrics/versions.jsonl` | 自定义指标版本 |
| `sop/versions.jsonl` | 自定义 SOP 版本 |
| `work/action-requests.jsonl` | 已物化执行请求与业务溯源 |
| `deals.jsonl` | `growovo.deal_event` 追加事件 + 旧格式兼容读取 |

Work OS 使用 `core/event_store.py` 的追加写与投影，不整文件重写历史。所有业务事件需要 `by` 和稳定 `entity_id`。

## 6. 后续实现顺序

1. 将 40 个岗位的 SOP 摘要逐条展开为完整 Step Contract，优先 B2B 需求→销售→CS 和 PLG 激活→留存两条链；
2. 增加真实组织身份、数据域权限与完整 RBAC，而不是把 `by` 字符串当企业认证；
3. 扩展 KPI 的 cohort 窗口、财务成熟期和更多行业计算器；
4. 接真实授权数据源与业务接收方 dogfood，分别记录 fixture、live execution、outcome 和 incrementality；
5. 再实现 Web 工作台；当前 CLI / MCP 是同一本地业务内核的产品入口。
