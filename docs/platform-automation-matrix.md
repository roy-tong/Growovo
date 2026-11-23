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
| ⛔ 平台许可拒绝网页脚本写 | X 的常驻与单次非 API 脚本写均不放行；只读、dry-run、草稿与人在 X 页面操作保留 | **X**（`policy.autonomous_write=false`，`user_visible_cdp_write=false`，`owner_attested_browser_write=false`） |
| ⚠️ GATED | 有写通道但未实测，首跑需人工盯 + 显式 `allow_unverified`；**可以编入人工辅助计划**，不当场关闭业务 | GitHub（reply，浏览器可作 owner-attested 备用路线）、Reddit（reply）、Hacker News（reply）、LinkedIn（message）、Discord（publish/reply） |
| ⬜ MANUAL | 没有自动写通道，只出人工作业单，**不计入自动发布量** | 小红书、知乎、公众号、B 站、抖音、Product Hunt、V2EX、即刻、微博、SEO、Newsletter 等其余平台 |

> 「技术可执行」不等于「平台允许执行」。X 的 vendor 网页写脚本无论常驻还是单次都不放行；操作者确认、账号观测与本地配额不能替代平台许可。

## 为什么无人值守自主执行只承诺 Email

- X：所有非 API 网页脚本写都拒绝，包括非 routine 单次 `--go` 的 publish/reply/message/engage。未接官方 API 时只提供只读、dry-run、草稿与人工处理；人在 X 页面自行发送，不由 Growovo 调用 vendor 代发。
- 夜间循环（`mkt cycle`）：X 回复／原创不进入 browser-attach 计划，统一返回 `platform_permission_required`，不会触碰 X 写引擎或提交 committed 账本；X 只读采集与人工处理保留，自主写需符合 X 规则的官方 API。GitHub 评论、Discord 频道发帖与单条回复仍可按平台签发 `owner_attested_session` 授权，在操作者已打开的标签页里按波执行；默认只复用现有标签页，找不到即 `tab_not_found`，不会擅自新开。第一件事是读通知并回别人给我的回复。身份按页面可见账号现场读取，`cdp_observed` 在此只表示"身份来自实际执行的浏览器"。每条允许的动作经过 `action_runner(transport="browser-attach")`，使用独立 owner-attested 授权而非 plan/routine，统一写任务结果和操作日志。这是操作者自担风险的路线，不是平台许可；额度为自设上限。页面出现风控提示、验证码或 429 时停该渠道并给降频建议，不换身份、不换代理。
- Email：SMTP 是协议通道，发送前异常记 failed，**提交后**异常（超时/断连/回执丢失）记 `indeterminate`——服务端可能已收下，按未知结果停机，不自动重发；适合做结果单投递和一对一承接。
- 其他写渠道（GitHub / Reddit / HN / LinkedIn / Discord）选择器按公开 UI 编写、没有在真实账号跑过完整链路。它们不进入「无人值守全自动」承诺，但**可以正常编入人工辅助计划**：内容、排期、账本照常生成，发送前人工确认并显式解锁；运行期遇到平台风控只停当前渠道并给出降频/拆批/顺延建议，不把整个业务判死。

## 边界纪律

- 编译器按同一判定工作：`auto_channels()` 只含 Email；用户指定 X／GitHub／Reddit／HN／LinkedIn／Discord 时，计划编为人工辅助（内容、排期、账本正常，发送需人确认），不会到运行阶段才被合规门拒绝。
- 人工渠道不写入「自动发布量」；结果单会把它们单列，避免把人工动作算成引擎交付。
- 新增 AUTO 平台必须同时满足：写通道实跑验证 + 回读校验 + 队列门禁 + 配额 + 读数通道；缺一项就只能停在 GATED。
- `mkt grow platforms` 输出是唯一事实源；README 与销售话术不得超出这张表。
