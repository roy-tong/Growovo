# 平台自动执行矩阵

日期：2026-11-24（当前工作树） · 代码：`core/platform_caps.py`、`core/platform_compliance.py`、`core/action_resolution.py`

## 1. 判定方式

Growovo 以业务交付为核心，分别判断三件事：

1. **业务执行能力**：渠道有写原语、队列支持该动作，写通道为 PROVEN / PORTED；
2. **技术首跑状态**：UNVERIFIED 的选择器或流程仍需显式 `allow_unverified`，这不是平台合规阻断；
3. **执行安全条件**：账号授权、身份、暂停、DNC、配额、回读、防重发、验证码与风控停手、未知结果对账。

平台规则和社区口径只进入 `code / reason / risk_warning`，**不得单独否决业务动作或把 AUTO 降为人工辅助**。`non_api_write_forbidden`、`self_bot_forbidden`、`platform_forbids_automation` 是历史风险码名称，不代表当前执行结果。

写操作默认 **live**，显式 `--dry-run` 才预览。live 条件不足时返回 `LIVE_BLOCKED` 或人工工作包，不自动降级成 dry-run 成功。

## 2. 当前矩阵

| 等级 | 平台 | 业务执行方式、验证与风险提示 |
| --- | --- | --- |
| ✅ AUTO | **X** | publish/reply 默认通过 WebBridge 受管标签执行（身份观测与执行在同一标签）；routine 也改走 owner-attested，不再默认 routine CDP；message/engage 与显式 `--legacy-cdp` 走调试浏览器 CDP；X 只读默认 WebBridge；官方 X API 写入尚未实现。`non_api_write_forbidden` 仅提示平台与账号风险，不再阻断业务 |
| ✅ AUTO | **Discord** | publish 可通过授权 WebMonitor 受管页面执行；reply 仍有独立 UNVERIFIED 技术边界。`self_bot_forbidden` 仅提示风险 |
| ✅ AUTO | **Email** | SMTP / Gmail API + 有效 routine/plan、DNC、配额和未知结果保护。Gmail 授权测试账号自测 canary 已通过；真实 SMTP 验收仍是独立边界 |
| ⚠️ GATED | **GitHub** | GitHub App 优先，Issue 使用 REST、Discussion 使用 GraphQL。自有仓库 Issue #41 创建与评论 canary 已通过；Discussion 和其他仓库仍需技术验收，当前全局成熟度字段仍待按动作/路线拆分 |
| ⚠️ GATED | **Reddit** | 当前 CDP 写流程 UNVERIFIED，首跑需显式技术确认；`non_api_write_forbidden` 仅提示风险，不再改判为人工辅助 |
| ⚠️ GATED | **Hacker News** | 当前 CDP 写流程 UNVERIFIED，首跑需显式技术确认；`platform_forbids_automation` 仅提示社区风险 |
| ⚠️ GATED | **LinkedIn** | 当前 CDP 写流程 UNVERIFIED，首跑需显式技术确认；已移除 `MKT_LINKEDIN_AUTO` 和终端确认锁，平台规则仅提示风险 |
| ⬜ MANUAL | 其余 30 个注册平台 | 没有已接入写原语，只出人工作业单，不计入自动发布量 |

`mkt grow platforms` 是业务执行能力的统一展示入口。AUTO 表示具备自主执行技术路径，不表示平台官方许可，也不保证每个账号、动作或业务结果成功。

## 3. 受管 CDP 与账号授权合同

所有 CDP 读写使用 WebMonitor 受管标签组：

- 按 `session + site` 持久登记 `tabId / group_title / url`；
- 优先复用存活页，其次 attach 用户已有的同站页；仍没有时由 `ensure_managed_tab` 通过 WebMonitor 创建一次；
- 失效页重绑，使用结束只释放引用，不 `page.close()`；
- Playwright 只复用页面，不得自行 `new_page()`；
- 身份必须来自当前页面；owner-attested 是操作者账号授权事实和风险提示，不是平台官方许可；
- 未授权账号、身份不符、验证码、挑战、429、封禁或风控信号停止对应渠道，不换页、换号或代理规避。

## 4. Action Resolution

执行按以下顺序选择：

1. 已接入的官方 API / SMTP；
2. 有账号授权且技术条件满足的受管 CDP；
3. 最小人工工作包，带目标、资产、CTA、负责人和回读待办；
4. 不允许人工降级时返回 `LIVE_BLOCKED`。

单渠道技术故障不应停止其他可执行工作。平台风险只提示；未知结果不自动重试，技术首跑未确认时不伪称 PROVEN。
