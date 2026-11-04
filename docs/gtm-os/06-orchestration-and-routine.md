# 按需岗位编排、内容表达与日常营销常驻授权（v0.9.0）

> 本轮修正三件事：不默认使用全部岗位；不默认一种方案表达；日常营销触达不再一律逐次人审。付款、合同、折扣等仍由有权人员处理。

## 1. 根据产品、目标和任务选最小 Workstream

`core/orchestration.py` 先推断任务类型，再选择必需岗位；可选岗位只有在任务包含相应触发条件时才加入。

```bash
mkt orchestrate \
  --product product.json \
  --goal "14 天内首次成功调用" \
  --task "写开发者教程并做 GitHub 发布" \
  --audience developer --channel github --format md
```

输出包括：

- `selection`：最小岗位和能力域，以及入选原因；
- `workstream.work_orders`：每个岗位的交付物、前置依赖和 SOP 状态；
- `workstream.handoffs`：只连接被选中的上下游；
- `content_plan.deliverables`：每份交付物的必写内容、表达形式、语气和渠道规则；
- `validation`：岗位、模板、能力域和依赖引用校验。

### 示例

| 任务 | 选中的岗位 | 不会无故加入 |
|---|---|---|
| SDR 账户研究与资格交接 | 26 SDR、27 AE、42 Sales Ops | Demand Gen、品牌、电商、投放 |
| 开发者教程 + GitHub 发布 + 首次成功 | 46 DevRel、24 激活、44 数据、12 社媒 | 销售、品牌、直播、会员 |
| ABM + SDR + POC | 18、26、27、28、42、43 | 品牌、电商、App、私域 |
| 风险客户邮件召回 + CS + 服务恢复 | 34、35、38、40 | 获客、广告、商家、BD |
| SEO 内容 + 小红书分发 | 14、09、12 | 视觉（未要求）、销售、直播 |

编排结果是 `candidate_not_execution_approval`，不会把“少选岗位”变成“跳过必要验收”。只有开发者 SOP 当前是完整运行链，其他步骤会标 `requires_expansion`。

## 2. 根据交付目的和受众决定表达

### 2.1 整体方案表达

| 受众 | 输出形式 | 表达重点 |
|---|---|---|
| 高管 / 决策人 | `decision_memo` | 结论、取舍、基线、资源、风险、需要决策的事项；正文短，执行细节入附录 |
| 运营执行者 | `operator_runbook` | 输入、RACI、步骤、日程、DoD、异常、KPI |
| 开发者 | `technical_tutorial` | 环境、版本、最小示例、命令、预期输出、错误和限制 |
| 销售接收人 | `sales_handoff` | 账户触发、对方原话、资格、承诺、未决风险、下一动作 |
| 客户 | `customer_ready_plan` | 客户目标、范围、双方责任、里程碑和验收 |
| 合作伙伴 | `partner_onepager` | 共同价值、双方投入、归属、结算和退出 |

### 2.2 每份交付物单独决定表达

- POC 是客户可验收的章程，不是社媒文章；
- SDR 是一页资格交接，不是完整市场研究；
- RevOps 是指标合同与经营读数，不是高管宣传稿；
- 技术教程必须可复现，不能被渠道化成营销口吻；
- 社媒规则只覆盖公开发布资产，不覆盖内部收入字典、POC、客服记录等。

### 2.3 渠道 Overlay

| 渠道 | 表达规则 |
|---|---|
| Email | 具体称呼与触发、一个价值点、一个 CTA、主题一致、无占位符 |
| GitHub | 先给复现结果、引用 issue/版本、命令和预期输出、不求 Star |
| X | 首条独立成立、短句、一条主线、链接后置、不批量灌水 |
| LinkedIn | 场景洞察、事实/案例、专业判断、一个讨论问题 |
| 小红书 | 场景标题、短段落、经验/步骤卡片、避免硬广与绝对承诺 |
| 公众号 | 摘要、小标题、完整论证、结尾一个行动建议 |
| 落地页 | 单一价值主张、证据、适用边界、一个主 CTA、FAQ |

`--format json` 输出机读契约，`--format md` 输出可阅读方案，`--out` 可保存候选 Workstream。

## 3. 日常营销常驻授权

### 3.1 行为变化

旧行为：所有 `--go` 都必须有逐次已人审 plan。

新行为：满足以下全部条件时，日常营销动作可直接执行：

1. 当前工作区存在有效 `routine-marketing.json`；
2. 调用显式 `routine=True` / `--routine`；
3. `channel:action` 在封闭日常动作集合和授权范围内；
4. 执行账号与授权范围精确匹配；
5. 其他安全门全部通过。

放行类型是独立的 **`routine-policy`**，不会伪装成 `plan` 或 `adhoc`。

### 3.2 当前工作区

根据用户在本轮对话中的明确授权，当前默认工作区已启用内置日常动作，账号范围为默认/未指定账号。没有真实发送发生在本次实现和测试中。

其他工作区由人通过 CLI 设置：

```bash
# 授权全部内置日常动作，但仅限默认账号
mkt policy enable --by <授权人>

# 或严格限定动作、命名账号和有效期
mkt policy enable --by <授权人> \
  --actions x:publish,x:reply,email:message \
  --accounts brand-main --days 30

mkt policy show
mkt policy check --channel x --action-name publish --account brand-main
mkt policy disable --by <操作人>
```

空账号列表只允许 `""` 或字面 `default`，**不是任意账号通配符**。

`--days` 默认 30 天；只有显式 `--days 0` 才是不过期，且会在 stderr 提示、`mkt policy show` 里带 `warning`。已有旧策略不会因为升级被自动改写。

### 3.3 直接执行日常触达

```bash
# 发帖 / 回复 / 私信：显式 routine + go
mkt action x publish --text "<内容>" --campaign <id> \
  --account default --go --routine

# 邮件队列
mkt queue run data/queues/<campaign>.json --go --routine

# MCP 同样只传 routine/account；不能通过 MCP 创建、扩大或撤销授权
```

未指定 `--go` 时仍然是 dry-run。未指定 `--routine` 或策略不匹配时，仍走原有 plan 审批。UNVERIFIED 平台仍需相应技术确认，不能借 routine 把未验证选择器说成已验证。

### 3.4 只允许哪些日常动作

- `email:message`
- `x:publish / reply / message / engage`
- `reddit:publish / reply`
- `hn:publish / reply`
- `linkedin:message`
- `github:reply`

付款、合同、折扣、危机响应和未知动作不在封闭集合内；即使手工修改 JSON 也无法把它们加入。

## 4. 仍然保留的安全门

常驻授权**只替代逐次 plan 审批**，不会降低以下约束：

- dry-run 默认；
- 全局暂停；
- DNC / suppression / bounced；
- 日预算和原子预留；
- 内容 lint、占位符检查与回读一致；
- 渠道 PROVEN / PORTED / UNVERIFIED 技术等级；
- 未知结果人工裁决，禁止自动重发；
- 账本与三层结果分离；
- 付款、合同、折扣和其他高风险行为由人处理。

## 5. 边界

- 编排器是确定性的候选工作分解，不代替业务负责人判断任务语义；
- 渠道表达规则是写作约束，不等于目标平台最新官方规则审计；
- 常驻授权是工作区本地审计记录，不是企业 IdP；
- 日常营销可直接执行不等于可绕平台限制、绕验证码、绕封禁或骚扰用户；
- 本次测试全部使用 fake vendor/SMTP 或 dry-run，没有真实账号或网络发送。
