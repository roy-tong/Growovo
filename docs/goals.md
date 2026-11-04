# 目标选择（Goal Selection）

> 代码：`core/goals.py` · CLI：`mkt goal list|plan|select` · MCP：`mkt_goals` / `mkt_goal_plan`
> 状态：本地实现与测试通过；不是渠道效果证明，也不是收入预测。

## 为什么先选目标

同一个产品，追 star、追真实使用、追第一笔付费，是三套完全不同的动作：

| 目标族 | 这一轮在追什么 | 典型指标 | 它不证明 |
|---|---|---|---|
| 声量 awareness | 有人看见、记住、收藏 | GitHub star、关注者、单次发射的讨论量 | 有人需要、会用、会付钱 |
| 使用 adoption | 有人真的装、跑、提 issue、交数据 | fork、外部 issue、真实数据提交、激活 | 愿意付费、会留存 |
| 商业 commercial | 有人愿意谈、给样本、付钱 | 合格对话、样本、报价、付费、复购 | 可规模化、单位经济成立 |

不先选目标，平台选择就没有判据：GitHub 对 star 有效，对“第一笔企业付费”未必；
Reddit 对真实使用有效，对“声量”可能只带来一次性的围观。**目标是平台选择的自变量，不是结果页上的装饰。**

## 九个可选目标

| id | 目标 | 指标 | 默认形状 | 首选平台 |
|---|---|---|---|---|
| `awareness.stars` | GitHub star 增长 | `github_stars` | 在现有基础上 +50 | GitHub |
| `awareness.followers` | 社媒关注者增长 | `followers` | +200 | X |
| `adoption.forks` | 外部 fork | `forks` | +10 | GitHub |
| `adoption.issues` | 外部 issue / 真实反馈 | `external_issues` | +5 | GitHub |
| `adoption.real_data` | 真实日志 / 数据提交 | `real_data_submissions` | ≥2 | GitHub |
| `adoption.activation` | 注册 → 激活 | `activated_users` | ≥20 | Product Hunt / HN |
| `commercial.conversations` | 合格买方对话 | `qualified_conversations` | ≥3 | LinkedIn / Reddit |
| `commercial.first_paid` | 第一笔付费 | `paid_deals` | ≥1 | LinkedIn / Reddit |
| `commercial.repeat` | 复购 / 续费 | `repeat_purchases` | ≥1 | 邮件 / LinkedIn |

默认值是**排期用的形状，不是预测**；用 `--target` 覆盖。`commercial.repeat` 需要先有付费客户
（`baseline.paid_deals > 0` 或 `baseline.repeat_purchases` 已填），否则判为“不适配”。

## 主目标 + 辅目标

如果这一轮既想要声量、又想要真实使用，可以选 **1 个主目标 + 1 个辅目标**：

```bash
python3 mkt.py goal select --facts data/verovo-finops/facts.json \
  --goal awareness.stars --secondary adoption.real_data \
  --platforms github,hackernews,reddit \
  --out data/verovo-finops/goal-selection.json
```

规则：

- 主目标决定主渠道、主要动作和主要判据；辅目标共用平台和内容，不单独开第二条 campaign；
- **两个都达成才算成功**，只达成一个不算；
- 声量 + 使用有专门的共用动作：同一篇技术拆解既拉 star，也筛出愿意交真实数据的人；
- 如果 star 涨了但真实数据是 0，说明来的是看客，不是用户——改“要数据”的问法，不加曝光；
- 主渠道必须服务主目标；只服务辅目标的平台只能当辅助。

## 适用性怎么判

一个目标可用，满足其一：

1. `product.product_type` 在目标的 `product_types` 里；
2. 目标要求的平台在 `owned_assets.platforms` 里（例如服务型产品但有 GitHub 仓库，也能追 star/fork/issue）。

阶段也会过滤：`idea` 阶段的目标不会被推荐；`beta/launched/growth` 才进入可选。
所有“不适配”都会写明原因，不会静默消失。

## 平台怎么分工

一次最多 **1 个主渠道 + 1 个发射点 + 2 个辅助平台**：

- 主渠道：目标亲和度最高、可重复触达、能持续产出；
- 发射点：一次性曝光（HN / Product Hunt / Kickstarter / Instagram），只换反馈，不背获客指标；
- 辅助：承接主渠道来的人，或补一个主渠道覆盖不到的角落；不单独开 campaign。

发射平台不能单独当主渠道。平台评分 = 目标亲和度 × 3 + 受众标签匹配 × 2 + 已有账号 + 产品类型匹配；
评分只是排序依据，不是渠道效果证明。

## 怎么用

```bash
# 1. 看这个产品有哪些目标可选
python3 mkt.py goal list --facts data/verovo-finops/facts.json

# 2. 看某个目标的完整方案（不选平台时用推荐组合）
python3 mkt.py goal plan --facts data/verovo-finops/facts.json \
  --goal commercial.first_paid

# 3. 自己选平台，写成契约
python3 mkt.py goal select --facts data/verovo-finops/facts.json \
  --goal commercial.first_paid \
  --platforms reddit,hackernews,github \
  --target 1 --window-days 42 \
  --out data/verovo-finops/goal-selection.json

# 4. 策略和四周动作表按选择执行
python3 mkt.py strategy build --facts data/verovo-finops/facts.json \
  --selection data/verovo-finops/goal-selection.json
python3 mkt.py strategy cadence --facts data/verovo-finops/facts.json \
  --selection data/verovo-finops/goal-selection.json --start 2026-11-05
```

`strategy build --selection` 会：

- 用选择里的主渠道/发射点/辅助平台，而不是重新按旧复盘改主渠道；
- 在策略顶部写出目标、判据、证明/不证明；
- 渲染“平台分工”表；
- 让 `cadence` 用目标方案里的前两周动作，而不是套“每周 20 条”的通用模板。

## 事实包需要什么

在 `mkt gtm diagnose` 的事实包基础上，建议补两个字段：

```json
{
  "baseline": {"github_stars": 220, "forks": 10, "paid_deals": 0},
  "constraints": {"human_minutes_budget": 840, "red_lines": ["不做群发"]}
}
```

- `baseline` 不填也能出方案，但目标值会标“基线未提供”，且不做“已达成”判断；
- `human_minutes_budget` 用来标“超预算”，不阻止执行；
- `owned_assets.platforms` 决定“产品类型不匹配时，能否靠已有账号做这条线”。

## 输出契约

`mkt goal select` 写出 `growovo.goal-selection/1`：

```json
{
  "schema": "growovo.goal-selection/1",
  "goal_id": "commercial.first_paid",
  "metric": "paid_deals",
  "target": 1,
  "target_kind": "count",
  "target_text": "≥ 1（基线未提供）",
  "window_days": 42,
  "baseline": null,
  "platforms": {"primary": "reddit", "launch": "hackernews", "support": ["github"]},
  "selected": ["reddit", "hackernews", "github"],
  "facts_sha256": "…",
  "selected_at": "2026-11-04",
  "boundary": "目标是选择，不是预测。…"
}
```

契约是 append-only 的决策记录：改目标或平台就重新 `select`，不要手改旧文件。

## 边界

- 目标达成只证明那一个数变了；不自动证明需求、付费意愿、可复购或可规模化。
- 平台亲和度是**规划启发式**，不是渠道效果；真实效果要由 `mkt result` / `mkt journey` 回填。
- `star`、`fork`、`关注者` 都能被买/换/刷；目录里把“买量、互 star、抽奖、互关”列为反指标，但产品无法自动识别造假。
- 默认目标值是形状，不是承诺；没有基线的目标必须标“基线未提供”。
- “推荐”只表示目标与产品类型/阶段匹配，不表示这条路径一定走得通。
