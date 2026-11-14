# 平台自动执行矩阵（第一期）

日期：2026-11-14 · 代码：`core/platform_caps.py` · 命令：`python3 mkt.py grow platforms`

判定不看平台名气，只看三件事同时成立：

1. 渠道注册表（`channels/__init__.py`）里的写通道已验证（PROVEN / PORTED）；
2. 内核有可调用的写原语（`core/platforms.py` 的 `WRITE`）；
3. 队列执行器支持该动作（`core/queue_runner.py` 的 `QUEUE_ACTIONS`）。

三条都成立才是 **AUTO**，才进入「全自动」承诺。

| 等级 | 含义 | 平台 |
|---|---|---|
| ✅ AUTO | 可以全自动执行并计入自动发布量 | **X**（PROVEN，reply）、**Email**（PORTED，message） |
| ⚠️ GATED | 有写通道但未实测，首跑需人工盯 + 显式 `allow_unverified` | GitHub（reply）、Reddit（reply）、Hacker News（reply）、LinkedIn（message） |
| ⬜ MANUAL | 没有自动写通道，只出人工作业单，**不计入自动发布量** | 小红书、知乎、公众号、B 站、抖音、Product Hunt、V2EX、即刻、微博、SEO、Newsletter 等其余平台 |

## 为什么第一期只承诺 X + Email

- X：唯一 PROVEN 的写渠道，回读校验（写错绝不发送）、账本幂等、日预算、节奏表都有实测口径；也是「关注者增长」目标的默认主渠道。
- Email：SMTP 是协议通道，`sendmail` 无异常即 committed，不产生 `indeterminate`；适合做结果单投递和一对一承接。
- 其他写渠道（GitHub / Reddit / HN / LinkedIn）选择器按公开 UI 编写、没有在真实账号跑过完整链路。把它们放进「全自动」承诺，等于把未验证的失败风险转嫁给用户。它们保留在系统里，但必须人工首跑、显式解锁，且结果单独标注。

## 边界纪律

- 人工渠道不写入「自动发布量」；结果单会把它们单列，避免把人工动作算成引擎交付。
- 新增 AUTO 平台必须同时满足：写通道实跑验证 + 回读校验 + 队列门禁 + 配额 + 读数通道；缺一项就只能停在 GATED。
- `mkt grow platforms` 输出是唯一事实源；README 与销售话术不得超出这张表。
