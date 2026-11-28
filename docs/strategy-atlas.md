# 动态策略图谱与证据边界

`core/strategy_intelligence.py` 将既有战术机制、606 条经验记录和真实结果事件组合成动态策略决策。机制是候选，不是长期固定答案；每次选择都绑定用户意图、业务阶段、渠道、资产、内容质量和当前结果。

## 一、语料盘点

`docs/playbooks/经验库/experience.jsonl` 当前有 **606 条记录**：

| knowledge_type | 数量 | 使用边界 |
|---|---:|---|
| `reproducible_method` | 120 | 仍需机制、独立复现、权利和复核证据 |
| `multi_source_practice` | 65 | 多源只表示多篇提到，不能普遍化 |
| `single_expert_experience` | 68 | 单篇资深经验只生成限定实验 |
| `unverified_lead` | 353 | 未复核线索，只用于发现候选，不进入策略或实验 |

按当前 `assess_eligibility`：

- 正式策略 eligible：**2**；
- 内容实践 eligible：**62**；
- 其余主要缺少可追溯复核、权利依据、相关性、 freshness 或独立复现材料，或只能进入限定实验。

平台分布也不均衡：小红书 240、公众号 192、抖音 54、知乎 47、X 27，其余较少。2026-11-27 新增 12 条 newsletter 跨职能经验，另用 `function` 区分市场营销、销售、BD、用户运营与产品增长；渠道数量不能用来估计成功率。详细机器盘点见 `ai输出/GTM与增长/OVO-下午45-20261127/strategy-census.json`。

内容增长来源另有独立的候选层：15,710 个去重来源（来自 31,161 个公开 sitemap URL），其中 4,800 个属于内容运营、内容策划、账号运营、KOL/KOC 和新媒体营销；2,931 个进入正文候选（近一年 2,628＋旧方法论 303），1,869 个过期平台运营来源被排除。实际抓取 2,588 页，近一年去重 2,312 篇，合并 275 篇旧方法论后 2,585 篇去重正文；平台运营近一年 1,680 篇、常青方法论 905 篇，覆盖 15 个来源域名。120 个优先复核来源保存在 `docs/playbooks/经验库/review-queues/cross-functional-20261127/`（平台运营近一年 71、方法论近一年 27、旧方法论 22），完整池在实验包 `mining/`。这些来源只有 `unreviewed` 或 `ready_for_manual_review` 状态，不计入 606 条经验，也不参与策略资格。平台操作结论只用近一年来源；方法论可保留更早来源。

## 二、20 个机制族

| 机制 | 主要意图 | 采用条件／调整方向 |
|---|---|---|
| `borrowed_audience` | 规模、声量、采用 | 小账号、目标人群聚集；无互动时换受众或钩子 |
| `proven_demand` | 规模、声量 | 有近期已验证选题；只借框架，不逐字复制 |
| `search_seat` | 规模、采用、效率 | 有明确搜索原话与长尾需求；修承接与首用 |
| `official_tap` | 规模、声量 | 官方话题、目录或活动入口当前可用 |
| `volume_probability` | 规模、声量 | 单条成本可控且质量分≥70；低质量先修内容 |
| `rule_window` | 规模、声量、效率 | 有明确时间窗与新鲜证据，按窗口调整节奏 |
| `story_pull` | 规模、声量 | 有真实故事、失败或数据，不制造情绪 |
| `trust_nodes` | 规模、采用、效率 | 有垂直 KOC 与信任节点，不唯粉丝量 |
| `sequence_window` | 复用、采用、效率 | 有细分密度和次序，先打透再扩相邻场景 |
| `compound_loop` | 规模、采用、复用 | 已有接受结果；把输出变成下一批输入 |
| `product_embedded` | 规模、采用 | 有可分享产出物，产品出口带承接 |
| `value_exchange` | 采用、商业、规模 | 有私有数据或稀缺资产可交换 |
| `paid_amplify` | 规模、效率 | 明确允许付费且已有赢家，只放大已验证内容 |
| `competitor_events` | 规模、采用、商业 | 有真实竞品事件与迁移承接物 |
| `own_the_venue` | 规模、声量 | 有目录、榜单、认证或周报等长期资产 |
| `alliance_bundle` | 采用、商业、规模 | 有互补伙伴和共同用户 |
| `event_waves` | 规模、声量、采用 | 有监管、关停、预算或平台迁移事件 |
| `radical_transparency` | 声量、采用、规模 | 有可公开真实数据与授权，设退出条件 |
| `agent_surfaces` | 规模、采用 | 有 Agent 可读产品页、API、Demo 或 MCP |
| `diagnostic_probe` | 效率、采用 | 有驳回、403、审核或接口探针，先诊断再调整 |

每张卡保存 `intents / stages / channels / required_assets / evidence_refs / corpus_examples / usage_boundary`。当前多数卡为 `experiment_only`，不是正式策略结论。

## 三、动态选择与调整

```text
用户意图 + 业务阶段 + 渠道 + 资产 + 质量分 + 当前结果
→ 过滤缺失条件
→ 按意图、阶段、渠道、质量与已有接受结果排序
→ 执行当前机制
→ 回填真实事件
→ 选择下一机制和配额倍率
```

| 当前证据 | 下一动作 | 下一机制与倍率 |
|---|---|---|
| 渠道停止／403 | `recover_channel` | `diagnostic_probe`，0 |
| 质量分 <70 | `revise_quality` | 当前机制，0.5 |
| 没有结果数据 | `explore` | 当前机制，1.0 |
| 有接受结果、无第二任务 | `build_retention_loop` | `compound_loop`，1.0 |
| 有接受结果和第二任务 | `scale_up` | 当前机制，1.5 |
| 有首用、无接受结果 | `improve_first_task` | `diagnostic_probe`，0.75 |
| 有互动、无首用 | `improve_asset` | `product_embedded`，0.75 |
| ≥30 动作、无合格互动 | `switch_audience_or_hook` | `borrowed_audience`，0.5 |
| 样本不足 | `explore` | 当前机制，1.0 |

倍率是下一轮实验预算，不覆盖账号、平台、授权、去重、配额和安全停止条件。接受结果只影响当前上下文，不证明因果，也不把经验普遍化。

## 四、CLI

```bash
mkt growth strategy-census
mkt growth strategy-select --file examples/strategy-context.json
mkt growth strategy-adjust \
  --file examples/strategy-context.json \
  --outcomes examples/strategy-outcomes.json
```

`strategy-select` 返回前三候选及每个候选的缺失输入、选择理由和语料实例；`strategy-adjust`  additionally returns 当前调整动作、下一机制和倍率。 `growovo.strategy-decision/1` 是可审计决策合同，决策 ID 同时绑定上下文与所选机制。

## 五、研究边界

- 606 条是已有语料规模，不是策略有效性证明。
- 当前没有新增外部采集；本图谱不声称覆盖所有增长机制或最新平台变化。
- 旧战术中的具体倍数、阈值和时间窗仍需 freshness 复核；官方规则、社区经验与 OVO 自有实测不能混写。
- 后续需要用真实结果事件验证策略卡的外部效度，不能只提高选择分数。
