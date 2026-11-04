# GTM 闭环（Goal → Channel → Conversion → Delivery → Learning）

> 这条闭环把 Growovo 的模块串起来：目标选择 → 渠道 → 转化 → 交付 → 回流。
> 每个环节只回答一个问题，且只有一个主指标；不能跳步。

## 1. 一张图

```
目标（goals.py）
  ↓ 这一轮要声量/使用/商业？主目标+辅目标
渠道（channels.py）
  ↓ 去哪个 venue？主/发射/辅助/旁听；能不能发；能带多少对话
转化（conversion.py）
  ↓ 每个对象在哪一阶段？下一个动作是什么？卡在哪？
交付（value_delivery.py）
  ↓ 样本/诊断包/月度包；真实复现、授权交付、用户读回
回流（result.py / journey.py / billing.py / experiment）
  ↓ 哪一步掉了？哪个话术有效？哪个价格被接受？
  └──────────────→ 回到目标/渠道/转化
```

## 2. 每个环节的主指标

| 环节 | 主指标 | 证据 | 不能拿什么代替 |
|---|---|---|---|
| 目标 | 一个数（star/数据/对话/付费） | 基线 + 目标形状 | 不能拿“动作数”代替 |
| 渠道 | 合格对话数 | venue + 动作 + 回复 | 不能拿曝光/浏览量代替 |
| 转化 | 阶段推进数 | 阶段 + 证据 | 不能拿“聊得好”代替 |
| 交付 | 用户读回 | 交付物 + 读回原话 | 不能拿“已发送”代替 |
| 回流 | 参数 diff | 旧值 → 新值 + 触发数据 | 不能拿“感觉”代替 |

## 3. 渠道 → 转化的接口

渠道产出的不是“线索”，是**有上下文的对象**：

```
venue + 他的原话 + 触发事件 + 接触资格
        ↓
deal（sourced）
```

没有原话、没有触发事件的对象，不进 deal；进了也会在 `qualified` 卡住。
**渠道的质量，决定转化的起点。**

## 4. 转化 → 交付的接口

`paid` 不是终点，是交付的起点：

```
paid → 交付 7 个文件 → 读回 → delivered → repeat
```

交付必须用 `value_delivery` 的真实复现/授权/读回；**发送 ≠ 交付，交付 ≠ 使用，
使用 ≠ 付费**。

## 5. 回流：回填什么

| 数据 | 回填到 | 用途 |
|---|---|---|
| 每个 venue 的回复率 | `channels.py` 的经济学假设 | 替换默认值，重算排期 |
| 每个阶段的转化率 | `conversion.py` 的漏斗 | 找掉得最多的一步 |
| 每个异议的处理结果 | `conversion.OBJECTIONS` | 补/删分支 |
| 每个报价的接受/拒绝 | `billing.py` | 调价格/范围 |
| 每个话术的回复 | `experiment` / `voice` | 留下有效的，删掉无效的 |

**没有回填，所有假设都只是排期工具**；回填后，旧假设作废，新计划按新数算。

## 6. 三条不能违反的规则

1. **不跳步**：没有 `qualified` 不给样本；没有 `readback` 不报价；没有 `paid` 不交付。
2. **不混层**：曝光、回复、合格、样本、付费是五个不同的数；不能合成一个“转化率”。
3. **不造假**：不买星、不刷回复、不伪造数据；一次造假会让整条闭环的数字失效。

## 7. 什么时候停

- 渠道：连续 2 周 0 合格对话 → 换 venue，不换话术；
- 转化：同一阶段卡住超过阈值 → 问一个真问题，不追第三次；
- 交付：用户不读回 → 先修交付物，不催付款；
- 回流：没有新数据 → 不写“优化”，写“停摆”。

## 8. 模块对照

| 环节 | 代码 | CLI | MCP |
|---|---|---|---|
| 目标 | `core/goals.py` | `mkt goal list/plan/select` | `mkt_goals` / `mkt_goal_plan` |
| 渠道 | `core/channels.py` | `mkt channel venues/plan/score` | `mkt_channel_venues` / `mkt_channel_plan` |
| 转化 | `core/conversion.py` | `mkt deal add/advance/qualify/next/funnel/quote` | `mkt_deals` / `mkt_deal_next` |
| 交付 | `core/value_delivery.py` | `mkt value-chain run` | `mkt_value_artifact` |
| 回流 | `core/result.py` / `journey.py` / `billing.py` | `mkt result` / `journey` / `billing` | `mkt_result` / `mkt_journey` |

## 9. 边界

闭环是**描述和排期**，不是因果证明。渠道有效不等于产品有需求；转化高不等于可规模化；
付费一次不等于可复购。每一层都要单独验证，不能用一个数代替另一个数。
