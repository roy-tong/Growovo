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
