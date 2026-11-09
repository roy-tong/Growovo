# 时区窗口策略（timezone windows）

**状态：策略底座，v1（2026-11-09）。** 实现在 `core/timezone_windows.py`，测试在 `tests/test_timezone_windows.py`。cadence.py「集中发布」动作中的「定好发布窗口（当地时间 + 北京时间）」以本策略为准。

## 为什么需要它

两次实测（见 `launch-autopsy-20261105.md` 与 11-08/09 夜的复现）：

| 时段（受众本地） | 发帖量 | 互动 |
|---|---|---|
| 00:00–06:00 | 121 条 | **零**（点赞/回复/关注均无） |
| 08:00–10:00 | 15 条 | 3 个实质互动（含技术对话与关注） |

凌晨发帖不是低效，是零效。窗口策略把「什么时候发」从直觉变成可计算的字段。

## 三条硬规则

1. **只在黄金窗发送**（受众本地时间）：08–10（启动段）、12–13.5（午休段）、20–23（晚间最强段）。窗口外排队等待，`next_window()` 给出下一个可发时刻。
2. **死区禁投**：受众本地 00:00–06:00。多时区受众重叠时，死区受众的**定向内容**（其语言/话题）禁投，通用英语内容仍可向黄金窗受众投放。
3. **语言与话题跟随受众**，不翻译轰炸：日语受众发日语（实测零稀释）、话题按地区画像（日本=质量/测试/细节；澳洲=solo founder/SaaS；美东=scale/devtools；欧洲=B2B/procurement）。

## 受众目录（v1）

| key | UTC+ | 语言 | 话题画像 |
|---|---|---|---|
| jp | +9 | ja | 质量纪律、测试文化、细节工程 |
| cn | +8 | zh | 效率工具、出海、独立开发 |
| sg | +8 | en | SEA startup、SaaS |
| au-east | +11 | en | solo founder、indie hacker、SaaS |
| eu-west | +1 | en | B2B、procurement、open source |
| us-east | −5 | en | scale、fundraising、devtools |
| us-west | −8 | en | AI tooling、open source、devtools |

（UTC 偏移为冬令时基准；夏令时期间调用方传入 +1 修正。）

## 覆盖计算

- `window_score(utc_hour)`：该时刻黄金窗受众数 = 得分；同时输出死区名单。
- `best_hours()` / `next_window()`：未来 24 小时最优时刻排序与下一个可发点。
- 24 小时滚动：中国早晨（UTC 0）覆盖 jp/cn/sg；我们的下午（UTC 7–9）覆盖欧洲早间；我们的晚 21–23 点（UTC 13–15）覆盖 us-east 早间 + jp/cn 晚间——**一天三个天然发布窗，无需熬夜**。

## 节奏约束

- 同一受众两次触达 ≥6 小时（本地时间），避免同窗疲劳。
- `plan_language_mix()`：非英语受众各占 1/3 容量，英语兜底 2/3——多语言是覆盖手段不是姿态。

## 接口

```python
from core import timezone_windows as tw

tw.next_window(["jp", "cn", "au-east"])     # 下一个可发时刻 + 可用受众
tw.window_score(0)                          # 任意时刻的覆盖评估
tw.content_hint("jp")                       # {"lang": "ja", "topics": [...]}
tw.best_hours(["us-east", "us-west"], top=3)
```

发送通道（channels/x 及未来 channel）在出队前应调用 `next_window` 做门禁：窗口外不投，投则按 `content_hint` 匹配语言。

## 待验证（E1 级假设，勿当结论）

- 黄金窗内**不同窗**的互动率差（启动 vs 晚间）——当前只有单点观察。
- 夏令时修正的自动化（v1 手传）。
- 话题画像的地区有效性（日本=质量文化的假设来自两条帖子样本）。

## 版本

- v1（2026-11-09）：七受众目录、三硬规则、覆盖计算、接口。来源：11-05/08 两次 campaign 共 ~170 条的时段-互动对照 + Roy 的时区定向指令。
