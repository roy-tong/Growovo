# 对象 × 目标 × 机制 × 渠道：GTM 设计层（v0.10）

> 状态：本地实现（`core/gtm_design.py`、`mkt gtm design/lint/taxonomy`），不执行、不发送、不预测结果
> 上游：`docs/gtm-os/06-orchestration-and-routine.md`（按需选岗与常驻授权）
> 日期：2026-11-04

## 1. 为什么在岗位编排之前再加一层

v0.9 的 `mkt orchestrate` 解决的是「这个任务需要哪些岗位、交付物长什么样」。它默认任务类型已经知道，机制由关键词映射到一套岗位配方。真实增长任务不是这样进来的：

- 用户说「让这个开源项目被更多人真正用起来」，没有岗位，也没有渠道；
- 用户说「给新硬件做预售」，对象是硬件，目标是订单，机制可能是内容 + 交易 + 合作，而不是一张岗位表；
- 同一个产品在冷启动和放量阶段，正确机制完全不同；
- 用户已有的账号、素材、预算和数据，往往比理论最优渠道更能决定方案。

因此新增一层**设计**，只回答四个问题，再把结果交给岗位编排与执行内核：

```text
Object（增长谁）× Objective（要什么结果）× Motion（用什么机制）× Channel（在哪执行）
        ↓  约束检查（授权/素材/预算/地区/数据）
Design（候选机制、渠道、结果契约、未知项）
        ↓
mkt orchestrate（岗位与交付物） → mkt plan/queue（人审） → 执行内核
```

岗位、SOP、模板不删除，仍是专业知识与交付组件；它们不再是任务的入口。

## 2. 四个维度的词表

### 2.1 Object

| id | 对象 | 成功口径的第一道纠偏 |
|---|---|---|
| `software_saas` | 软件 / SaaS / AI 应用 | 注册不等于激活，激活不等于付费 |
| `content_account` | 内容账号 / 自媒体 | 播放量不等于影响力，粉丝不等于目标受众 |
| `hardware` | 硬件 / 消费产品 | 线索不等于可履约需求，预售订单不等于收入 |
| `oss_project` | 开源项目 / 开发者生态 | Star 不等于采用，clone 不等于首次运行成功 |
| `service` | 服务 / 咨询 / 代运营 | 线索不等于合格机会，签约不等于交付价值 |
| `b2b` | B2B / 企业软件 | MQL 不等于收入，pipeline 不等于回款 |

对象由任务文本的信号推断，也可以 `--object` 显式指定。置信度低于 0.4 时返回 `needs_clarification`，不硬凑一个完整方案。文本没有给出目标时，按对象的常用目标暂定（软件→激活、内容账号→认知、硬件→收入、开源→贡献、服务/B2B→获客），并在 `unknowns` 中明示「暂定」，可用 `--objective` 覆盖。

### 2.2 Objective

`awareness`、`acquisition`、`activation`、`revenue`、`retention`、`contribution`。每个目标自带：

- 主信号与定义（不是指标名，是「怎么算」）；
- 领先信号与反指标；
- **不算成功**清单（播放量、Star、GMV、注册数、打开率等）；
- 观察窗口与停止条件；
- 可验证该目标的数据源。

这是「Goal & Outcome Contract」在任务层的实现：不预设统一成功线，也不固定四周。

### 2.3 Motion

| id | 机制 | 典型对象 | 必需能力 |
|---|---|---|---|
| `content_led` | 内容增长 | 全部 | 内容资产 |
| `community_led` | 社区增长 | 开源、软件、B2B、服务 | 真实社区身份 + 技术资产 |
| `product_led` | 产品增长 | 软件、开源、内容账号 | 可自助体验的产品 + 产品事件 |
| `sales_led` | 销售驱动 | B2B、软件、服务、硬件 | 账户名单 + 销售人力 |
| `commerce_led` | 商业/交易增长 | 硬件、内容账号、服务 | 购买路径 + 供应履约 |
| `partnership_led` | 合作增长 | 开源、硬件、B2B、内容、服务、软件 | 合作方名单 |

机制可以组合：最多 3 个，1 个 primary + 最多 2 个 supporting；supporting 只补主目标的上游条件（认知、需求、承接），不单独承诺结果。Proposal 可给每个机制带 `fit.audience_fit`、`fit.resource_fit` 与 `evidence`；有 fit 时按 fit 排序，没有证据的机制标记为 hypothesis 并进入 `combination.evidence_gaps`。`selected_motions` 记录角色与理由，`combination.order` 给出建议顺序；`selected_motion` 保留为主机制，兼容旧调用。

### 2.4 Channel

直接复用 `core/platforms.py` 的 37 个平台，按对象的产品类型或渠道类型过滤，再按地区、授权与证据状态排序。**不新建第五份平台清单。**

输出最多 1 个主渠道 + 3 个辅助渠道；未授权渠道单独列出，并标注 `unauthorized_total`。没有授权时方案仍可设计，但 `executable=false`，且明确「现在不能发布」。`executable=true` 还要求该机制所需能力齐备（`missing` 为空）并至少有一个适配且已授权的渠道。

## 3. Agent 提候选，规则查可行性

`--proposal` 接受 Agent 的候选：

```json
{
  "objective": "activation",
  "motions": [
    {"id": "content_led", "why": "用教程承接搜索与社区里的真实问题"},
    {"id": "partnership_led", "why": "借合作方分发"}
  ],
  "channels": ["github", "x"]
}
```

规则逐条检查并**保留拒绝理由**：机制不适用于该对象、机制不常用于该目标、渠道不在注册表、渠道类型不匹配、没有授权、缺少不可临时补齐的能力。被拒候选不会静默消失。

这与「把增长专家判断编码成几千条规则」相反：Agent 负责提出与解释，规则只负责可行性与边界。

## 4. 结果契约与验证等级

`outcome_contract` 固定包含主信号、领先信号、反指标、不算成功、窗口、停止条件和验证等级：

| 等级 | 含义 |
|---|---|
| `observable` | 声明的数据源能直接观察到该目标（如产品事件 + 激活） |
| `partially_observable` | 只有部分信号可观察，口径待人工确认 |
| `verifiable` | 数据源已连接，且事件级目标已核验关键事件与身份关联 |
| `declared_only` | 只声明了数据源类型或连接未确认；不能把类型匹配当可核验 |
| `unverified` | 没有数据渠道或缺少该目标必需的数据源；只能拿到平台回执 |

验证等级由 `data_sources` 与 `data_receipts` 共同决定。回执至少包含 `source`、`connected`；事件级目标（activation / revenue / retention / contribution）还要求 `event`、`window`、`sample` 与 `receipt_id`。`data_connected` / `events_verified` 两个布尔字段保留兼容，但**单独使用只能到 `declared_only`**，不能升级为 `verifiable`。回执仍是操作者提供的记录，本模块不能替代真实连接检测。

数据源用固定词表：`platform_insights`、`product_events`、`billing`、`order_system`、`crm`、`repo_events`。写错会被拒绝，而不是被当成一个看起来合理的自由文本。

`unverified` 时不允许把平台回执写成业务结果。这也是 Growovo 与「发布量统计工具」的分界线。

## 5. 命令

```bash
# 看词表
python3 mkt.py gtm taxonomy

# 设计：文本推断对象与目标
python3 mkt.py gtm design "增长一个小红书摄影账号，目标是目标粉丝而不是泛流量"

# 带约束：授权渠道、素材、地区、数据源
python3 mkt.py gtm design "推广新上市的智能相机，目标预售订单" \
  --constraints '{"authorized_channels":["xiaohongshu","taobao"],"assets":["content","supply"],"data_sources":["order_system"],"region":"cn"}'

# Agent 候选 + 规则校验
python3 mkt.py gtm design "让开发者真正用起来" --proposal proposal.json --json

# 保存设计，交给岗位编排与目标契约
python3 mkt.py gtm design "增长我们的开发者工具" --save data/gtm/designs/devtool.json
python3 mkt.py gtm handoff data/gtm/designs/devtool.json --product facts.json

# 校验一份设计是否自洽（退出码 0/1）
python3 mkt.py gtm lint design.json
```

JSON 输出含 `object`、`objective`、`motions`、`channels`、`constraint_checks`、`outcome_contract`、`proposal`、`unknowns`、`next_steps`、`handoff` 和 `boundary`，可直接被其他 Agent 或 MCP 客户端消费。

MCP 侧对应只读工具 `mkt_gtm_design`（`task` + 可选 `object/objective/constraints/proposal`），返回同一份设计并附带 `validation.ok`。它不写工作区、不建计划、不发送。

## 5.1 交接：设计 → 岗位编排

`mkt gtm handoff` 做三件事，仍然不执行：

1. 校验设计自洽；`blocked` 设计直接拒绝交接；
2. 把设计目标映射到既有目标族（声量 `awareness` / 使用 `adoption` / 商业 `commercial`），并给出 `mkt goal plan` 命令；
3. 生成 `execution_plan`：对象、目标、机制（含 recipe）、渠道、约束、结果契约与设计 SHA-256 指纹，下游不得重新推断；
4. 提供产品事实包时，按**已选主机制的 recipe** 调用 `core/orchestration.py`，而不是让编排重新猜机制；Workstream 带回 `design_ref`。

Workstream 是按产品事实与任务推断的候选组合，不是发布批准，也不证明设计选中的机制已被执行。

## 6. 与既有模块的关系

| 模块 | 关系 |
|---|---|
| `core/platforms.py` | 唯一平台数据源；本层只做过滤与排序 |
| `core/goals.py` | 目标契约的既有实现，偏产品事实包与平台组合；本层把目标扩展到内容、硬件、开源 |
| `core/orchestration.py` | 下游：设计确定机制后，再按岗位与交付物编排 |
| `core/execution_policy.py` / `core/approval.py` | 执行仍走原门禁；本层不发送，也不扩大授权 |
| `core/gtm.py` | 产品事实包诊断；本层不替代事实包，只消费任务与约束 |

## 7. 边界

- `design_status`、`execution_readiness`、`measurement_readiness` 三者独立：设计完成不等于可执行，可执行不等于可验证；
- 设计不是执行结果，也不是渠道有效性证明；
- 对象与目标由信号推断，可能误判；低置信度会显式要求澄清；
- 机制兼容性是编辑口径，不是统计结论；
- 结果契约不预测数值，`current` 恒为 `unmeasured`；
- 没有真实账号发送、没有平台权限验证、没有业务结果回填时，不得声称增长成立。
