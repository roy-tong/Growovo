# Growovo AI Growth Team 重构设计

日期：2026-11-23  
状态：用户已确认方向并要求逐项落地；实施按可验收子项目推进，不以接口壳或测试数量冒充业务完成。

## 1. 背景与目标

Growovo 已有执行、授权、账本、结果事件、平台策略和本地工作流基础，但业务目标、策略、动作、入站、激活和下一实验仍需 Agent 手工缝合。目标是在保留执行与证据内核的前提下，增加面向 OPC 的 Growth Service 层：

- 用户给出产品、业务目标、资源和授权，系统生成并持续执行增长服务。
- CMO 层负责目标、增长机制、机会排序和实验决策。
- COO 层负责资产、社区、会话、调度、执行、异常恢复和交付。
- 北极星是单位用户投入时间获得的已验证有效增长结果，而不是动作量。

## 2. 方案取舍

### 方案 A：渐进式服务层（采用）

保留现有 WorkOrder、RunSpec、Queue、Policy、Ledger、Outcome 与适配器，在上层增加统一目标合同、服务控制器和机会/会话/实验状态。优点是复用现有安全与证据能力、每一步可独立验收；代价是需要做兼容映射和清理旧路径。

### 方案 B：推翻重写多 Agent 平台

重新建立产品、岗位、渠道、调度和数据模型。概念上整齐，但会丢失现有执行保护、测试与真实运行记录，风险和时间成本过高，不采用。

### 方案 C：只补单点功能

仅恢复 CDP 写、增加几个策略模板和定时命令。能快速改善局部体验，但仍不能解决目标到结果断裂，不采用。

## 3. 总体架构

```text
GrowthServiceContract / 用户自然语言目标
        ↓
Business Goal Compiler
        ↓
Growth Service Controller（持久状态、Next Best Action、人工打断预算）
        ↓
CMO：Strategy / Opportunity / Experiment
COO：Asset / Engagement / Content / Outcome / Scheduler
        ↓
Action Resolution Engine
        ↓
现有 Execution & Evidence Kernel
        ↓
Official API / SMTP / WebMonitor 受管标签组 CDP / 人工交接
        ↓
回执、会话、产品事件、业务结果回流
```

统一关联键：`service_id / goal_id / hypothesis_id / opportunity_id / conversation_id / experiment_id / run_id / action_id`。旧记录没有这些键时保持兼容，不伪造历史关联。

## 4. 执行意图与安全边界

### 4.1 默认 live

- 写操作默认表达真实执行意图，不再默认 dry-run。
- 显式 `--dry-run` / `execution_intent=preview` 才进入预览。
- `--go` 暂时保留为兼容显式参数，不再是默认实跑所必需。
- live 条件不足返回 `LIVE_BLOCKED` 或按合同生成最小人工交接，不自动降级成 dry-run 成功。
- 默认 live 不等于取消授权：账号、动作范围、批准、暂停、DNC、配额、内容校验、身份、回读与未知结果保护继续生效。

### 4.2 CDP 合规口径

- 未授权账号、绕验证码、挑战、封禁、代理池和风控对抗继续禁止。
- CDP 作为操作者明确授权的浏览器执行通道，覆盖各平台已有写原语；其他平台必须逐个实现动作适配与验收，不能用通用点击脚本冒充支持。
- owner-attested CDP 是操作者自担风险的授权事实，`platform_permission=false` 保留在证据中，不伪称平台官方许可。
- 官方 API/SMTP 仍是优先路径；CDP 不可用时按 Action Resolution 选择其他合法路径或人工交接。

## 5. WebMonitor 受管标签组

### 5.1 唯一标签页所有者

新增受管标签页注册表，按 `session + site` 保存 `tabId、group_title、url、identity_ref、last_used_at、status`。同一服务会话内每个站点只有一个受管执行页，读写共用，波次之间复用。

### 5.2 创建与恢复

1. 先在当前 WebMonitor 会话查找存活受管页。
2. 没有时，将用户已有的同站页面 attach 到受管组。
3. 仍没有时，允许 `BrowserAttach.ensure_tab(..., allow_new=True, group_title=<service group>)` 创建一次受管页并登记。
4. 标签丢失、崩溃或 URL 越界时重新解析；不盲目点击旧引用。
5. 页面出现验证码、挑战、429 或风控文本时停止对应渠道，不换页规避。

### 5.3 禁止旁路

- `browser-attach` 动作不得调用 Playwright `new_page()`。
- 现有 `core/cdp.py`、X vendor、Reddit、HN、LinkedIn 等写路径逐步迁移到统一受管页面解析器。
- 只读采集与执行共享同一受管页；需要并行时使用独立、显式登记的会话，不随意新增标签。
- vendor 的现有代码与账本保留，页面获取改成注入受管 page；不修改其业务动作语义。

## 6. Business Goal Compiler 与 Growth Service Controller

### 6.1 GrowthGoalContract

支持小时级实验和长期 campaign、一个主目标与多个辅助目标、执行意图、阻断降级、人力与现金预算、渠道范围、数据源和基线。

### 6.2 Business Action DAG

每个动作包含目标、假设、人群、触发事件、资产及哈希、CTA、渠道动作、转化事件、测量引用、依赖、成功/停止条件和下一动作。`success_criteria` 是完成合同，不再只是可选学习字段。

### 6.3 GrowthServiceContract 与控制器

每项服务保存目标、范围、权限、预算、交付频率、长期记忆和状态。控制器在 Agent 会话外持久化，支持恢复、过期待办和局部渠道阻断，并计算：

- 有效自主交付率；
- 人工分钟/已验收任务；
- 有效增长结果；
- Human Interruption Cost。

任务分级：L0 自主分析、L1 预授权执行、L2 批量确认、L3 重要决策。默认承担工作，只有必要事项打断用户。

## 7. 业务模块

### 7.1 Growth Opportunity Engine

机会对象包含需求主体、问题与触发事件、意图、产品匹配、可提供价值、可执行动作、收益/成本/风险、下一步和来源。第一期采用可解释规则与置信度，不把概念评分公式当作已校准概率。

### 7.2 Unified Engagement Inbox

统一 GitHub、Email、X、Discord 等授权来源，维护 `Opportunity → Conversation → Relationship → Activation → Outcome`。已有用户互动优先于陌生触达；每条支持范围内入站必须有回复、忽略或未决核对终态。

### 7.3 Content & Value Asset Factory

维护 `Topic → Research → Asset → Publish → Distribute → Observe → Refresh`。资产必须可复现、带版本和 CTA；渠道复用需要重写结构与表达，不机械拆分。

### 7.4 Outcome & Experiment Engine

连接产品事件、journey、业务观察和实验。区分动作完成、有效互动、产品结果和因果判断。按“无人回复/有回复无点击/有点击无激活/有激活无留存”生成不同下一实验。

### 7.5 Action Resolution Engine

依次评估官方 API/SMTP、授权 CDP、人工交接和替代渠道。单渠道阻断不停止整个服务；任何路径都不能绕过授权、挑战和风控。

## 8. 持久作业与长期记忆

在现有作业内核上增加 `goal-compile / opportunity-scan / asset-prepare / inbox-resolve / baseline-observe / final-observe / goal-evaluate / next-experiment`。长期结构化对象：Product、Audience、Opportunity、Conversation、Experiment。聊天上下文不是业务事实源。

## 9. 实施子项目与验收

1. **受管 CDP 执行链**：同一站点不重复开页；断页可恢复；所有写动作经过授权、DNC、配额、回读；禁止 vendor 新页旁路。
2. **默认 live 与 Action Resolution**：无 `--go` 不再自动模拟；显式预览可用；阻断不冒充成功。
3. **Business Goal Compiler**：多目标、短实验、预算内 DAG；动作可回溯到转化事件。
4. **Growth Service Controller**：跨会话持久恢复、局部阻断、人工打断统计。
5. **Community Operator 黄金链**：真实机会发现、统一入站、上下文、回复、执行、跟进和日报。
6. **Launch Operator 黄金链**：从真实仓库诊断到资产、分发、激活反馈。
7. **Outcome/Experiment 与自适应循环**：结果驱动下一实验，不自动放大无证据动作。
8. **内容复用与产品激活**：渠道版本、FAQ、安装失败诊断和产品改进候选。
9. **服务指标与人工对照**：有效自主交付率、人工分钟、有效结果和质量护栏。
10. **渠道扩展**：优先 GitHub/Email，其后 X/Discord/Reddit/HN/LinkedIn；每个平台独立真实验收，未验收标相应状态。

每个子项目需要离线回归、真实命令端到端和适用时的授权 live canary。真实用户增长结果单独观察，不作为代码测试可以制造的事实。

## 10. 现有改动与兼容

当前工作树已有 X owner-attested 与 cycle 放行改动及对应测试，基线 2260 项通过。后续在其上扩展，不回滚、不覆盖。旧 dry-run 记录、账本和计划保持可读；改变默认执行语义不篡改历史。

## 11. 非目标

- 不承诺创造市场需求或保证粉丝、Star、激活和收入。
- 不做绕过平台控制、未授权账号操作或批量骚扰。
- 不一次开发 40 个名义岗位 Agent，不追求全平台统一选择器。
- 不重建完整 PostHog、Chatwoot 或大型 GUI；优先复用适配器和现有 Workbench。
