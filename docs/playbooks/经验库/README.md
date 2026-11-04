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

## 三档证据

| 档 | 含义 | 能不能当依据 |
|---|---|---|
| `rule` | 人工提炼，有出处 | 可以 |
| `verbatim` | 同一句话在 ≥2 篇文章里出现 | 可以 |
| `topic` | 很多篇都谈到这个主题 | **不可以**，只说明这个变量重要 |

## 新鲜度（写进代码的硬约束）

```python
FRESH_DAYS = 60      # 超过 60 天的来源视为过期
MIN_SUPPORT = 2      # 少于两篇独立来源只能算线索
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
