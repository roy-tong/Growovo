# 经验库：别人的实操，按证据分级

> 用户经验是**主观且会过时**的。所以这里不做「收集」，做「采样 + 分级 + 定期重采」。

## 三个来源

| 来源 | 脚本 | 说明 |
|---|---|---|
| 公众号文章 | `scripts/harvest_experience.py` | 搜狗微信搜索 → 解析跳转页里被拆开的 URL → 抓正文 |
| **B 站视频** | `scripts/harvest_bilibili.py` | 搜索 API → yt-dlp 下音频 → **本地语音转文字**（Vosk）→ 转写稿 |
| 人工提炼 | 手写 | 从上面两类里挑出最可靠的，写成可执行结论 |

B 站视频里讲小红书/抖音运营的很多，所以采集后有一道**归位**：按内容判断这段讲的是哪个平台，
归到那个平台去，而不是都算 B 站。

## 内容增长策略来源批量挖掘（2026-11-27）

原来的经验库偏中文内容平台操作。2026-11-27 增加了一层**来源候选池**，聚焦内容运营、内容策划、账号运营、KOL/KOC 运营与增长、新媒体营销，与正式 `experience.jsonl` 严格分开：

| 层级 | 数量 | 状态 | 位置 |
|---|---:|---|---|
| 原始 sitemap URL | 31,161 | 原始发现 | 执行包 summary |
| 去重、分类来源候选 | 15,710 | `unreviewed`，仅 URL／标题／职能／业务范围／发布时间 | `ai输出/GTM与增长/OVO-下午45-20261127/mining/strategy-sources.jsonl` |
| 五类重点业务范围 | 4,800 | 内容运营、内容策划、账号运营、KOL/KOC、新媒体营销 | `strategy-sources-classified.jsonl` |
| 可研究正文候选 | 2,931 | 近一年 2,628＋旧方法论 303 | `strategy-sources-classified-summary.json` |
| 正文抓取成功 | 2,588 | 40 条 301 为本地化页，按不可用丢弃 | `recent-fulltext-summary.json` |
| 近一年正文去重 | 2,312 | 2,315 篇在一年内，3 篇重复 | `recent-fulltext.jsonl` |
| 合并旧方法论正文 | 2,585 | 275 篇旧方法论，跨库去重 2 篇；15 个域名 | `theme-evidence.json` |
| 平台运营近一年 / 常青方法论 | 1,680 / 905 | 平台机制只用近一年；方法论可早于一年 | `fulltext-analysis-summary.json` |
| 过期平台运营来源 | 1,869 | 一年以前，因机制过期排除 | 不计入正文和策略 |
| 优先复核队列 | 120 | 五类各 24 条；平台运营近一年 71、方法论近一年 27、旧方法论 22 | `docs/playbooks/经验库/review-queues/cross-functional-20261127/review-queue.jsonl` |
| 已人工复核新增策略 | 12 | 10 条单人经验＋2 条多源实践 | `experience.jsonl` 末 12 行 |

时间门：理论／方法论不设一年截止；平台算法、功能、排期、账号机制、广告工具和当前指标只使用近一年来源；混合内容按平台时效处理。来源池只保存公开链接元数据，标题或摘要命中业务词不等于策略有效，也不自动进入正式策略。复核流程：

```text
source candidate
→ 人工读原文并提取一条可执行 claim
→ 确认作者/场景、权利依据、来源日期和边界
→ 写成 knowledge_type + review_record
→ 通过 mkt exp assess / strategy-census
→ 才可进入策略选择
```

这层扩充解决的是“素材太少”，不解决“策略已被证明”。来源池、分类、正文和主题证据的生成与统计在 `ai输出/GTM与增长/OVO-下午45-20261127/mining/`。平台运营类结论必须在策略文档中标注观察日期和季度／事件触发的复核要求。

## 证据轨道（knowledge_type）

旧字段 `kind`（rule/verbatim/topic）只用于历史展示，**不再是资格**。现在每条记录必须带一个轨道：

| 轨道 | 含义 | 能用来做什么 |
|---|---|---|
| `official_rule` | 平台/法规明文 | 当规则 |
| `reproducible_method` | 机制清楚、≥2 独立来源、有人复现过 | 进策略 |
| `single_expert_experience` | 单篇资深作者经验 | 只能生成实验 |
| `short_term_tactic` | 短期技巧，14 天窗口 | 2 源以上才能进策略 |
| `own_test` | 自己实测（动作/结果/样本/成本齐全） | 只在自己的场景算数 |
| `multi_source_practice` | ≥2 篇独立来源 + 采集日期可查 | **内容工艺/实验**；不能当平台许可，也不普遍化 |
| `unverified_lead` | 单源或未复核 | 只作选题参考，永远不当规则 |

两级用法：`multi_source_practice` 可以进内容工艺（`mkt exp usable`、`as_prompt_lines`、语气提示），
但正式策略（`purpose=strategy`）仍要求人工批量签核；`unverified_lead` 在任何用途下都不放行。

2026-11-17 对 412 条缺类型的旧记录做了采集核验迁移：

```bash
python3 mkt.py exp adopt                 # dry-run：多少条能升成多源实践
python3 mkt.py exp adopt --apply         # 写回 experience.jsonl
python3 mkt.py exp adopt --apply --revisit-leads --date-from-git
                                         # 缺日期的用 git 首次记录日补（会写明 date_basis）
python3 mkt.py exp usable --platform xiaohongshu   # 内容工艺口径下的可用条目
```

迁移只补「多源 + 日期 + 权利依据 + ingest 记录」，**不伪造人工复核**。要升到正式策略，
需要按批量签核加 review 记录（`reviewer/reviewed_at/record_ref/source_domains`）。

## 新鲜度（写进代码的硬约束）

```python
MIN_SUPPORT = 2                 # 少于两篇独立来源只能算线索
TRACK_FRESH_DAYS = {            # 不同轨道不同窗口
    "official_rule": 365, "reproducible_method": 180,
    "single_expert_experience": 180, "short_term_tactic": 14,
    "own_test": 90, "multi_source_practice": 120,
}
```

- `mkt exp coverage` — 每个平台多少条、最新一条多久之前
- `mkt exp stale` — 哪些平台该重采、为什么
- `experience.maintenance_plan()` — 每平台目标 50 条、重采命令、建议每 30 天跑一次

## 重跑

```bash
python3 scripts/harvest_experience.py --platform xiaohongshu --pages 50   # 公众号
python3 scripts/harvest_bilibili.py --all --limit 50                       # B 站视频（需本地 Vosk 模型）
python3 scripts/build_experience.py --min-support 2                        # 抽句归类
python3 scripts/render_experience.py                                       # 生成每平台文档
```

ASR 模型：`VOSK_MODEL=/path/to/vosk-model-small-cn-0.22`（默认 `/tmp/vosk-model-small-cn-0.22`）。
