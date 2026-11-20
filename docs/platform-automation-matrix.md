# 平台自动执行矩阵（第一期）

日期：2026-11-14 · 代码：`core/platform_caps.py` · 命令：`python3 mkt.py grow platforms`

判定不看平台名气，只看三件事同时成立：

1. 渠道注册表（`channels/__init__.py`）里的写通道已验证（PROVEN / PORTED）；
2. 内核有可调用的写原语（`core/platforms.py` 的 `WRITE`）；
3. 队列执行器支持该动作（`core/queue_runner.py` 的 `QUEUE_ACTIONS`）。

三条都成立才是 **AUTO**（技术可执行）；再叠加一条「平台许可」才是可承诺的自主执行。

| 等级 | 含义 | 平台 |
|---|---|---|
| ✅ AUTO | 写通道已验证、队列可执行，**且平台允许自主执行** | **Email**（PORTED，message） |
| ⛔ 自主写 / 单次 CDP 分层 | 常驻非 API 自动写禁止；当次非 routine `--go` 的 publish/reply 可在账号观测与全部门禁通过后走 CDP | **X**（PROVEN；`policy.autonomous_write=false`，`user_visible_cdp_write=true`） |
| ⚠️ GATED | 有写通道但未实测，首跑需人工盯 + 显式 `allow_unverified` | GitHub（reply）、Reddit（reply）、Hacker News（reply）、LinkedIn（message） |
| ⬜ MANUAL | 没有自动写通道，只出人工作业单，**不计入自动发布量** | 小红书、知乎、公众号、B 站、抖音、Product Hunt、V2EX、即刻、微博、SEO、Newsletter 等其余平台 |

> 「技术可执行」不等于「平台允许自主执行」。X 不计入可承诺的无人值守自主执行；单次 CDP 只是操作者明确要求的执行路线，不代表 X 官方认可，仍须遵守本文的平台风险边界。

## 为什么第一期自主执行只承诺 Email

- X：常驻非 API 写仍拒绝。非 routine `--go` 的单条 publish/reply 可以使用操作者提供的已登录浏览器，但要求 `cdp_observed` 实际账号证据、既有审批／adhoc 留痕、配额、防重发和回读全部通过；message/engage 不在该路线内。
- Email：SMTP 是协议通道，发送前异常记 failed，**提交后**异常（超时/断连/回执丢失）记 `indeterminate`——服务端可能已收下，按未知结果停机，不自动重发；适合做结果单投递和一对一承接。
- 其他写渠道（GitHub / Reddit / HN / LinkedIn）选择器按公开 UI 编写、没有在真实账号跑过完整链路。把它们放进「全自动」承诺，等于把未验证的失败风险转嫁给用户。它们保留在系统里，但必须人工首跑、显式解锁，且结果单独标注。

## 边界纪律

- 编译器按同一判定工作：`auto_channels()` 只含 Email；用户指定 X 时，计划编为人工辅助（内容、排期、账本正常，发送需人确认），不会到运行阶段才被合规门拒绝。
- 人工渠道不写入「自动发布量」；结果单会把它们单列，避免把人工动作算成引擎交付。
- 新增 AUTO 平台必须同时满足：写通道实跑验证 + 回读校验 + 队列门禁 + 配额 + 读数通道；缺一项就只能停在 GATED。
- `mkt grow platforms` 输出是唯一事实源；README 与销售话术不得超出这张表。
