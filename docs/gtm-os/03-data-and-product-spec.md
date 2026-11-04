# 数据模型与核心产品需求

> 本文把 v5 设计转成研发可实施的契约。字段名为逻辑模型，不要求一次性采用特定数据库；但 ID、版本、证据、权限与状态语义必须保持一致。

## 1. 总体架构与事实源

### 1.1 五个平面

| 平面 | 对象 | 事实源 / 说明 |
|---|---|---|
| 上下文平面 | BusinessContextSnapshot、Objective | Askovo / 用户确认快照与运营目标，不随原文浮动 |
| 业务工作平面 | Capability、Workstream、WorkOrder、SOPRun、Handoff | 谁在何时以何种方法做什么、交给谁 |
| 交付物平面 | TemplateVersion、DeliverableVersion、AcceptanceReview | 真实产物、质量与业务验收 |
| 执行平面 | ActionRequest、Plan、Queue、Workflow、Ledger | 复用现有授权执行内核；外部动作唯一写路径 |
| 结果与学习平面 | Event、TaskOutcome、OutcomeObservation、KPI Measurement、RuleChange | 三层结果、指标、实验和版本化改进 |

### 1.2 存储原则

- **事件追加 + 当前状态投影**：业务状态变化追加事件，当前快照可重建；不允许文档声称 append-only、实现却整文件改写历史。
- **不可变版本**：Context、SOP、Template、Metric、Permission Policy、Deliverable 和执行审批均绑定版本或哈希。
- **稳定关联**：所有对象带 `workspace_id` 和稳定 ID；跨模块用 ID，不靠 campaign 名称或人工重复填写。
- **事实与解释分开**：原始事件 / 回执不可被复盘文字覆盖；机制、归因和决策是引用事实的新记录。
- **不静默回退**：当前 scope 无数据时不能用全历史数据补出“本轮成果”。
- **兼容迁移显式进行**：旧 plan / deal / event 的迁移写映射和限制，不继承 legacy 审批放行。

## 2. 核心对象契约

### 2.1 BusinessContextSnapshot

```json
{
  "context_id": "ctx-growovo",
  "version": "2026-11-04.1",
  "source": {"kind": "askovo|user_declared", "ref": "..."},
  "product_facts": [],
  "audiences": [],
  "approved_messages": [],
  "proof_refs": [],
  "commercial_guardrails": {},
  "market_scope": {},
  "value_event_definitions": [],
  "non_goals": [],
  "approved_by": "",
  "approved_at": "",
  "content_sha256": ""
}
```

规则：快照只读；`user_declared` 不显示为已验证；字段缺口以依赖项方式阻塞工作，不用一个总分掩盖。Growovo 的修订建议写入新版本候选，不能修改 Askovo 原件。

### 2.2 Objective

| 字段 | 说明 |
|---|---|
| `objective_id / context_ref` | 稳定 ID 与上下文版本 |
| `business_question` | 本轮要做的业务决定 |
| `subject_type / eligibility` | 人、账户、机会、订单、商家、门店、开发者等及资格条件 |
| `primary_metric_ref` | 一个主结果 KPI，含字典版本 |
| `secondary_metric_refs / guardrail_refs` | 辅助结果与护栏 |
| `baseline / target_or_learning_question` | 基线可未知；没有依据时不生成伪目标 |
| `window / timezone` | 目标与观察窗口 |
| `owner / decision_maker` | 执行 Owner 与结果决策人可不同 |
| `resource_constraints` | 预算、人工时间、账号、数据、渠道和非目标 |

### 2.3 CapabilityDefinition / RoleAssignment

`CapabilityDefinition` 对应 18 个能力域，`role_families` 指向 07–46；每个岗位族包含职责、KPI、SOP、模板、交接、风险与人审点。

`RoleAssignment` 是具体企业内的人员 / Agent 分工：

```json
{
  "assignment_id": "assign-001",
  "actor_id": "person-or-agent-id",
  "actor_type": "human|agent|service",
  "role_family": "26",
  "raci": "responsible|accountable|consulted|informed",
  "scope": {"workspace_id": "...", "data_domains": [], "subjects": []},
  "permission_policy_version": "..."
}
```

Agent 可以 Responsible，不能因“执行人是 Agent”自动获得 Accountable 或审批权。

### 2.4 Workstream / WorkOrder

Workstream 组织共同服务一个 Objective 的工作；WorkOrder 是可独立验收的工作单元。

| 字段 | 必需规则 |
|---|---|
| `work_order_id / workstream_id / objective_ref` | 全链路稳定关联 |
| `title / trigger / business_purpose` | 说明为什么现在做，不写空泛“运营一下” |
| `subject_refs / scope / exclusions` | 明确对象和边界 |
| `capability_id / role_family` | 主能力域与主责岗位 |
| `context_ref / input_bindings` | 所有输入带 ID、版本、验收状态 |
| `sop_version_ref` | 开工后固定版本 |
| `deliverable_specs` | 至少一个；每项含 TemplateVersion 与验收人 |
| `dependencies` | 上游 WorkOrder / Handoff；未满足不能 ready |
| `owner / raci / receiver` | 一个主责 Owner，接收方明确 |
| `schedule / sla / budget` | 执行与观察时间、成本范围 |
| `risk_class / permission_ref` | 数据、账号、预算、动作权限 |
| `stop_rollback / observation_plan` | 异常停止、回滚与结果观察 |

### 2.5 SOPDefinition / SOPVersion / SOPStep

SOP 是步骤 DAG，允许线性、条件与并行，不允许无出口的步骤。

```json
{
  "step_id": "qualify-opportunity",
  "purpose": "判断该对象是否值得销售投入",
  "actor": {"type": "human|agent|shared", "role": "26"},
  "inputs": [{"name": "account_research", "artifact_ref": "...", "required": true}],
  "method": ["核验触发", "核验角色", "核验问题与时机"],
  "tools": [{"kind": "read_only|draft|action_request", "ref": "..."}],
  "outputs": [{"deliverable_type": "qualification_card", "required": true}],
  "dod": [{"rule_id": "trigger-evidence", "expression": "...", "required": true}],
  "on_pass": "schedule-meeting",
  "on_fail": "return-to-nurture",
  "on_unknown": "human-review",
  "approval": {"required": false, "scope": ""},
  "sla": {"seconds": 0, "escalate_to": "..."}
}
```

步骤约束：

- `purpose / method / inputs / outputs / dod / on_fail / on_unknown` 必填；
- “持续互动、提升质量、提供价值”等不可验收文字不能独立构成 DoD；
- 自动写平台只允许 `kind=action_request`，不得声明 transport 回调；
- skipped 必须有条件、理由和审批（必需步骤不可随意跳过）；
- 运行实例保留实际执行人、输入 / 输出版本、开始结束时间、证据和异常。

### 2.6 TemplateVersion / DeliverableVersion / AcceptanceReview

- `TemplateVersion`：`template_id/version/slots/required_slots/schema/example/rubric/permissions/status`；
- `DeliverableVersion`：`deliverable_id/version/work_order_id/template_ref/body_or_uri/facts_refs/created_by/content_sha256`；
- `AcceptanceReview`：`reviewer_id/time/items[{rubric_id,verdict,evidence_ref,note}]/overall/signature`；
- `submitted` 不等于 `accepted`；必需项 fail 阻止下游；NA 要有理由；
- 作者可以自检，但 R3/R4 交付不得由同一 Agent 或同一人员自审自批，除非权限策略明确允许该低风险企业场景并记录。

### 2.7 Handoff

```json
{
  "handoff_id": "ho-001",
  "work_order_id": "wo-001",
  "from_role": "26",
  "to_role": "27",
  "subject_refs": ["account-01", "opportunity-01"],
  "artifact_refs": [{"id": "qualification-01", "version": "1"}],
  "context": {"trigger": "...", "verbatim_refs": [], "actions_taken": []},
  "commitments": [],
  "open_issues": [],
  "acceptance_rules": [],
  "sla_due": "",
  "status": "pending|accepted|rejected|expired|cancelled",
  "receiver_id": "",
  "decision_at": "",
  "rejection": {"reason": "", "return_step_id": ""}
}
```

只有接收角色中的有权人员 / 系统能 accepted。通知、邮件或 webhook 回执不能自动把 Handoff 标记为 accepted。

### 2.8 Subject 与行业对象

共用 `Subject {subject_id, type, workspace_id, identity_refs, source_refs, consent_refs, owner_id}`，同时使用行业专属记录承载真实字段：

| 行业 | 主对象与顺序 | 不可混用的结果 |
|---|---|---|
| B2B | Account → BuyingGroup → Lead / Opportunity → Contract → Customer | SAL、机会、合同、回款、续约分别核验 |
| PLG / 订阅 | User / Account → ValueEvent → Cohort → Subscription | 注册、激活、留存、续费分开 |
| 电商 / 直播 | Merchant → SKU → Order → Fulfillment / Refund | GMV、支付、净收入、贡献利润分开 |
| App | StoreListing / Version → Install → Activation → Retention | 版本 / OS / deeplink 必须保留 |
| 线下 | Region → Store → Visit / Order / Member | 签到、有效拜访、订单 / 回款分开 |
| DevRel | Developer / Org → Repo / Example → SuccessfulCall → Adoption | Star / 下载、首次成功、持续采用分开 |

不要用一个充满可空字段的“超级对象”取代这些业务语义；共享的是 ID、证据、任务和交接机制。

### 2.9 ActionRequest

业务层只能创建 Action Request，字段至少包括：`work_order_id/step_run_id/deliverable_ref/action_kind/channel/target/account/workspace/budget/approval_scope/payload_sha256`。

实际执行由现有 plan / queue / approval / pipeline / action_runner 接管；审批指纹应扩展绑定：

```text
WorkOrder + SOPVersion + InputSnapshot + DeliverableVersion
+ normalized queue/payload + account + workspace + budget + permission policy
```

返回的 `ledger_ref/action_result` 回写 StepRun，但只更新执行层，不自动更新任务或业务结果。

### 2.10 Event / TaskOutcome / OutcomeObservation

- **Action / Ledger**：外部系统接受、失败、未知、抑制等；
- **TaskOutcome**：工作是否 resolved / partial / unresolved / needs_human，必须有独立验证；
- **OutcomeObservation**：激活、机会、订单、净收入、续约、成功调用等业务事实；
- **KPI Measurement**：在指定字典版本下对事件计算的投影，可重算但不是新事实；
- **RuleChange**：由人或授权流程决定的规则版本变化，不能由指标自动写生产规则。

## 3. 状态机

### 3.1 WorkOrder

```text
draft ──lint fail──> blocked
  └────lint pass───> ready ──start──> running ──all steps submitted──> review
                       ↑                 │  ↑                            │
                       │                 ├─ blocked / paused            ├─ revise → running
                       │                 └─ cancelled                   └─ accepted
                       └─ dependency fixed                                 │
                                                                       observe → closed
```

- `accepted` 表示工作交付验收，不表示业务目标达成；
- 结果可以 `observed / not_observed / inconclusive`，无论哪种都可 closed，但必须记录决定；
- 依赖未完成、输入未验收、权限缺失只能 blocked，不能生成执行队列；
- paused 是全局或局部安全状态，不与 failed 混用。

### 3.2 StepRun

`pending → ready → running → submitted → done`；旁路状态为 `blocked / failed / needs_human / skipped`。`done` 必须有所需输出、DoD pass 和实际执行人。`on_unknown` 不得自动重试。

### 3.3 Deliverable / Handoff / Action

- Deliverable：`draft → submitted → in_review → accepted → handed_off → observed → archived`；
- Handoff：`pending → accepted | rejected | expired | cancelled`；rejected 返回明确步骤；
- Action：沿用现有 `dry-run / committed / failed / indeterminate / rejected / suppressed / ...`，不发明“业务成功”执行状态。

### 3.4 四类“完成”必须在 UI 分开展示

| 标签 | 含义 | 不可替代 |
|---|---|---|
| 步骤已完成 | 该 SOP 步骤产物与 DoD 通过 | 不等于整项工作验收 |
| 交付已验收 | Deliverable 被指定审阅人接受 | 不等于外部已执行或用户有用 |
| 动作已确认 | 平台 / 系统回执已确认 | 不等于任务解决或业务结果 |
| 业务结果已观察 | 独立结果源核验到定义事件 | 不等于实验增量或可复制 |

## 4. KPI 与结果计算

### 4.1 MetricDefinition

```json
{
  "metric_id": "activation_rate",
  "version": "1.0.0",
  "name": "激活率",
  "layer": "business|task|process|guardrail",
  "subject_type": "account",
  "eligibility": "new_account_in_cohort",
  "numerator": {"event": "activation", "filter": "verified = true"},
  "denominator": {"event": "eligible_new_account"},
  "formula": "numerator_count / denominator_count",
  "unit": "ratio",
  "cohort": {"field": "eligible_at", "window": "..."},
  "observation_window": {"value": 7, "unit": "day"},
  "timezone": "Asia/Shanghai",
  "dedupe_key": "account_id",
  "source_refs": ["product_event_store"],
  "verification_policy": "verified_non_approximate",
  "owner_id": "...",
  "baseline": null,
  "target": null,
  "guardrail_refs": [],
  "known_limitations": []
}
```

### 4.2 计算硬规则

1. 分母为 0 返回 `null + insufficient_denominator`，不是 0%；
2. 比率同时返回分子、分母、样本、资格、窗口和时区；
3. 数据未成熟显示 `pending_maturity`，不能把尚未到期对象记作流失或失败；
4. 当前 scope 无数据不回退全历史；
5. `approximated=true` 或缺核验的价值事件单列，不进入已核验结果；
6. `sourced / influenced / first_touch / last_touch / self_reported / experimental_incremental` 分列，不能相加；
7. 财务区分 bookings、billed、collected、net_revenue、contribution_margin，并披露退款 / 成熟窗和成本范围；
8. 成本与任务经济性读取 Verovo / 财务来源，不由 Growovo 自己估一个成本证明 ROI；
9. 同一输入和 MetricVersion 必须确定性重算一致；代码 / 查询版本也留痕；
10. 不用综合分掩盖主结果与护栏冲突。

### 4.3 漏斗必须按 cohort / 进入事件计算

错误算法：`当前处于 stage B 的人数 / 当前处于 stage A 的人数`。对象前进后离开 A，该比率会失真。

正确最小语义：

```text
conversion(A→B, cohort C, window W)
= C 中在 W 内曾进入 B 的去重主体数
  / C 中在 W 前已达到 A 进入资格的去重主体数
```

必须保留进入时间、队列定义、尚未成熟对象、回退 / 跳级 / 重复进入规则。示例：10 个主体进入 sourced，5 个曾进入 engaged，其中 4 个曾进入 qualified，即使他们后续继续前进，两阶段分别为 5/10 与 4/5。

## 5. 权限、审批与职责分离

### 5.1 三种权限不可混用

- **组织权限**：能否创建、编辑、审核、接收、批准某类工作；
- **数据权限**：能否查看 / 导出某类主体、字段、区域、客户和财务数据；
- **账号 / 平台执行权限**：能否代表某账号在指定范围执行某动作。

`by="roy"` 只是署名，不构成身份认证；产品化需要真实 Actor、会话 / 服务身份和授权记录。账号授权也不等于平台允许自动化。

### 5.2 PermissionPolicy

```json
{
  "policy_version": "...",
  "rules": [{
    "actor_or_role": "...",
    "effect": "allow|deny",
    "operation": "deliverable.review|action.publish|budget.increase|...",
    "scope": {"risk": "R0-R4", "data_domain": "...", "workspace": "..."},
    "conditions": ["different_author", "approved_context", "within_budget"]
  }],
  "default_effect": "deny"
}
```

CLI、MCP、Skill、服务层使用同一策略。Agent 不能自审、自批、自我授权；高风险作者与终审人分离；所有拒绝也留审计。新对象不采用旧 plan 的 legacy 放行。

## 6. 产品页面与交互

### P01 · 今日工作台

- 今日应做、到期交接、待我审批、阻塞、未知结果、风险与结果待读；
- 每张卡显示：业务目的、当前步骤、缺什么、谁在处理、下一动作和截止；
- 默认按业务风险与到期排序，不只按发送时间；
- 空状态显示“无工作 / 输入未齐 / 结果未成熟”，不显示虚假增长。

### P02 · 目标与 Workstream

- 左侧目标树，中央 WorkOrder DAG，右侧 KPI / 资源 / 风险；
- 支持从目标拆任务、查看依赖和缺口；
- 不允许多个主结果 KPI 或无人负责的并行任务；
- cadence / sprint 成为此 DAG 的日历投影。

### P03 · 岗位能力目录

- 18 能力域 × 40 岗位族；
- 详情包含职责、KPI、SOP、模板、交接、权限、适用行业和上线状态；
- 可按企业类型切换 Overlay，而非复制 Agent；
- 显示“已配置 / 本地测试 / 真实执行 / 业务观察”四个独立状态。

### P04 · WorkOrder 详情（核心页面）

固定七区：业务目的与上下文、输入、SOP 步骤、交付物、审批 / 异常、交接、结果。用户在任何一步都能回答：为什么做、依据什么、谁做、交什么、谁验收、下一步是什么。

### P05 · 交付物工作室

- 模板字段、正文、事实引用、版本 diff、rubric 和评论；
- 业务审批与执行载荷审批分开；
- 支持源文件、数据、代码、订单、合同等多类型验证器；
- 拒收自动生成返工任务并定位步骤。

### P06 · 交接中心

- 待接收、已拒收、即将超时、承诺未决；
- 按接收角色显示统一队列；
- 可查看主体完整时间线，但不能跨权限展示敏感内容。

### P07 · 执行与异常台

- 复用现有 review / dry-run / ledger / pause / suppression；
- 明确业务 Owner 与平台 Owner；
- 异常包括 unknown、平台处罚、预算、数据不同步、投诉、危机和自动化漂移；
- 提供人工裁决与回滚，不提供盲目重试。

### P08 · KPI / 经营复盘

- 结果、过程、护栏三栏；指标可展开定义和证据；
- cohort / 主体 / 时间 / 版本筛选；
- 结果未成熟、缺失、近似和口径冲突显著展示；
- 复盘输出 RuleChange diff，不只输出文字报告。

### P09 · SOP / 模板 / 指标治理

- 版本、适用范围、Owner、评审、发布、弃用、回滚；
- 变更前显示受影响 WorkOrder 与审批；
- 运行中实例默认继续旧版，迁移必须明确选择。

## 7. 功能需求与入口

| ID | 产品需求 | 最低验收 |
|---|---|---|
| FR-01 | 能力 / 岗位注册与完整性 lint | 07–46 全覆盖；缺职责 / KPI / SOP / 模板 / 人审不能标 complete |
| FR-02 | 上下文导入与快照 | 来源、版本、证据、non-goals 固定；不可原地改 Askovo 快照 |
| FR-03 | WorkOrder 创建、lint 与依赖 | 缺输入 / Owner / 交付物 / 接收人时 blocked |
| FR-04 | SOP 运行与异常分支 | 无产物 / DoD 不可 done；unknown 不自动重试 |
| FR-05 | 模板与交付物版本 | 被引用版本不可改；变更影响审批 |
| FR-06 | 交付物逐项验收 | 必需 rubric fail 不解锁下游；submitted≠accepted |
| FR-07 | Handoff 接收 / 拒收 / SLA | 只有接收人 accepted；拒收返具体步骤 |
| FR-08 | ActionRequest 桥接 | 所有外部写只走既有 pipeline / action_runner |
| FR-09 | 统一主体与事件链接 | WorkOrder / Deliverable / Action / Outcome 可由同一 subject 串联 |
| FR-10 | 指标字典与确定性计算 | 分母 0 为 null；同输入重算一致；比率可展开 |
| FR-11 | 三层结果 | committed 不增加 task resolved 或 business observed |
| FR-12 | RBAC / 数据 / 账号权限 | 各入口同策略；默认拒绝；职责分离和越权测试通过 |
| FR-13 | 行业 Overlay | 非 FinOps 不出现发票、固定 $1,500、7 文件等案例内容 |
| FR-14 | 复盘与规则版本 | 复盘可产生有 diff / 审批 / 回滚的 RuleChange |
| FR-15 | 全链路审计 | 从 KPI 可回到事件、工作、交付物、审批和动作回执 |

建议新增入口（**设计，不代表当前已实现**）：

```bash
mkt capability list/show/lint
mkt context import/show
mkt work create/show/lint/next
mkt sop lint/start/step/exception
mkt deliverable submit/review/diff
mkt handoff pending/accept/reject
mkt metric list/show/measure
mkt review-rules propose/approve/rollback
```

CLI 可执行被授权的本地操作；MCP / Skill 默认读、草拟和创建待审请求，不能自我审批或绕过人工通道。界面显示命令前必须核对真实实现状态。

## 8. 现有实现的 P0 修正项

以下是当前代码审计发现的明确问题，不应被新能力覆盖后继续保留：

1. **转化证据门**：`core/conversion.py` 当前只有部分阶段强制非空 evidence；应为每个阶段定义类型化证据与资格，不能用任意字符串证明支付。
2. **报价前置条件**：报价生成需检查当前机会阶段和批准范围，不能脱离 readback / 资格独立生成固定案例报价。
3. **初始阶段**：普通新建机会从起点开始；任意阶段创建只能用于显式 migration，并写来源与审批。
4. **队列漏斗**：当前阶段存量相除改为历史进入事件 / cohort 计算；当前存量看板可保留但改名。
5. **追加式语义**：当前 `Deals._write` 整文件重写，与 append-only 文档表述不一致；改为事件追加 + 投影或修正实现与文档。
6. **执行桥证据**：`core/execute.py` 不应固定生成 `evidence_grade="B"`；没有真实证据就标未知 / 用户假设。
7. **结果同源**：`core/result.py` 的任务层应读取已有 TaskOutcome，而不是只用 followups / intent 替代工作结果。
8. **跨对象链接**：Opportunity、WorkOrder、ValueDelivery、Journey 共享主体和关联 ID；paid 等关键事件按规则创建后续 WorkOrder / Handoff，不靠人再填三套 ID。
9. **行业硬编码**：现有 FinOps 资格、异议、报价、数据清单和七文件交付迁为 `finops-revenue` Overlay，不作全局默认。
10. **渠道参数回流**：固定 assumptions 只能标初始假设；实测参数必须带 scope、版本、来源与成熟度，不自动放大预算。

## 9. 非功能要求

- **可审计**：所有状态、版本、权限、输入输出与证据可回放；
- **幂等**：业务键唯一，重复事件不重复产生外部副作用；
- **隐私与保留**：最小化字段、分域授权、删除 / 保留策略、客户数据不默认进入训练或公开案例；
- **时区 / 币种 / 语言**：显式保存，不用运行机器默认设置隐式计算；
- **可恢复**：崩溃后能区分未开始、已失败和结果未知；未知人工裁决；
- **可扩展**：能力、模板、指标、Overlay、验证器以版本化注册扩展，不改安全内核语义；
- **诚实状态**：`configured / fixture_passed / live_verified / outcome_observed / incrementality_validated` 独立，前者不能自动升级后者。
