# Growovo grow v2 实现契约（内部规范，2026-11-14）

本文件是并行开发的唯一契约。所有模块按此实现；签名/字段不得自行改名。
总目标：用户给 facts + 增长目标，`mkt grow launch` 自动编译策略与执行参数，
每日自动执行/读数/出日结果，每周出结果单并投递；用户每天可据业务结果决定
「继续」或「优化策略」。第一期只允许自动执行平台：**x、email**。

## 0. 全局约定

- Python 3.11，标准库，不新增依赖；中文 docstring；日期一律 ISO `YYYY-MM-DD`，
  时间戳 ISO `YYYY-MM-DDTHH:MM:SS`。
- 纯函数优先；所有 `now` 可注入；测试不联网、不真发送、不真等 sleep。
- data 目录由调用方传入 `data_dir`；禁止模块内硬编码仓库 `data/`。
- 失败语义：可预期错误抛模块自带 `XxxError`；未知异常不吞。
- 任何新增写路径都必须复用 `core/action_runner.py` / `core/pipeline.py`，
  不得绕过 pause / DNC / 日预算 / 回读 / 账本。

---

## 1. `core/platform_caps.py`（新建）——平台自动执行白名单

```python
AUTO = "auto"      # 第一期可自动执行
GATED = "gated"    # 有写通道但 UNVERIFIED，需 allow_unverified + 显式 opt-in
MANUAL = "manual"  # 无写通道，只能出人工作业单

def classify(channel: str) -> dict
# {"channel","platform","status","tier","write": (channel, action) | None,
#  "actions": ["reply"], "reason": str, "queue_action": "reply"|None}

def matrix() -> list[dict]      # 按 tier 排序：auto → gated → manual
def auto_channels() -> list[str]   # ["x", "email"]
def gated_channels() -> list[str]
def is_auto(channel: str) -> bool
def assert_auto(channels: list[str]) -> None  # 非 auto 抛 PlatformError
```

判定规则（不得放宽）：

- `AUTO`：`channels.REGISTRY[ch].status in {"PROVEN","PORTED"}` 且存在写通道
  （`core/platforms.WRITE`）且 `queue_runner.QUEUE_ACTIONS` 支持该动作。
  当前结果必须恰为 `x`、`email`。
- `GATED`：注册表有该渠道且存在写通道，但 status == "UNVERIFIED"。
  当前：github、reddit、hn（hackernews）、linkedin。
- `MANUAL`：其余全部平台。
- `email` 动作是 `message`，`x` 默认动作 `reply`（publish/message 在 actions 里列出但
  primary action 用 reply）。
- reason 用中文一句话说明证据级别。`matrix()` 覆盖 platforms.BY_ID 的全部平台，
  未知渠道落 MANUAL。

测试：`tests/test_platform_caps.py`（auto 集合恰为 {x,email}、gated 集合、
未知渠道 MANUAL、assert_auto 抛错、matrix 排序稳定）。

文档：`docs/platform-automation-matrix.md`（一张表：平台/状态/能否自动/动作/第一期是否启用/原因）。

---

## 2. `core/grow_schedule.py`（新建）——配额、窗口、供给水位

```python
def week_start_of(day: str, started: str) -> str
# 以 started 为锚，返回 day 所在周窗口的起始日（每 7 天一段）

def split_week(actions_per_week: int, week_start: str,
               action_weights: dict | None = None) -> dict[str, dict[str, int]]
# 返回 {date: {action: count}} 共 7 天；总和恰等于 actions_per_week。
# 无权重时：base=plan//7，余数从第 1 天起每天 +1（20 → 3,3,3,3,3,3,2）。
# 有权重时按最大余数法分摊，仍保证总和相等（权重默认 {"reply":1.0}）。

def parse_window(window: str) -> tuple[str, list[tuple[float, float]]] | None
# "20:00-23:00@us-west" → ("us-west", [(20.0,23.0)])；""/None → None

def window_allows(window, now_utc: datetime) -> bool
# window 为 None/"" → True（不限窗）。否则按 timezone_windows.AUDIENCES 的 tz
# 换算受众本地小时；落在任一窗即 True；落在死区(0-6) 也必须 False。

def in_dead_zone(audience: str, now_utc: datetime) -> bool

def item_due(item: dict, *, today: str, now_utc: datetime,
             allow_out_of_window: bool = False) -> bool
# scheduled_for <= today（缺省视为今天）且窗口允许。

def supply_status(queues: list[dict], *, today: str, days: int = 3,
                  daily_quota: int, committed_targets: set[str] | None = None) -> dict
# queues: [{"path": str, "queue": dict}]
# 返回 {"staged": n, "upcoming": n, "coverage_days": int, "gap": int,
#       "daily_quota": n, "by_day": {date: n}, "note": str}
# upcoming = 未 committed 且 scheduled_for >= today 的项；
# coverage_days = 从今天起连续「当天可用条数 >= daily_quota」的天数（无项即停）。

DEFAULT_WINDOWS = {"audience": "us-west", "local": [[20.0, 23.0], [8.0, 10.0], [12.0, 13.5]]}
```

测试：`tests/test_grow_schedule.py`（周切分总和与余数、跨周锚点、窗口换算/死区、
时区边界、供给水位与缺口）。

---

## 3. `core/deliver.py`（新建）——结果单落盘与投递

```python
def render_html(markdown: str, *, title: str = "") -> str
# 无第三方依赖的最小渲染：标题(#/##/###)、无序列表(-)、表格(|)、粗体(**x**)、
# 段落、代码块(```)；其余转义。自包含 CSS，无外链。

def write_card(data_dir, run_id: str, kind: str, label: str, markdown: str,
               *, title: str = "") -> dict
# 落 data/grow/<run_id>/cards/<kind>-<label>.md 与 .html
# 返回 {"markdown": path, "html": path}

def deliver_email(markdown: str, *, to: str, subject: str, dry_run: bool = True,
                  outbox_dir=None) -> dict
# dry_run=True（默认）：把 .eml 写进 outbox_dir（缺省 data/grow/<id>/outbox）并返回
# {"sent": False, "dry_run": True, "path": ...}，绝不联网。
# dry_run=False：用 MKT_EMAIL_USER / MKT_EMAIL_APP_PASSWORD，smtp.gmail.com:465；
# 缺配置抛 DeliverError。返回 {"sent": bool, "to":..., "error": str|""}。

def deliver_card(data_dir, run_id, kind, label, markdown, *, title, to="", subject="",
                 live: bool = False, outbox_dir=None) -> dict
# write_card + （to 非空时）deliver_email；返回合并结果。
```

测试：`tests/test_deliver.py`（HTML 渲染标题/表格/转义、落盘路径、dry-run 邮件不联网、
缺配置报错）。

---

## 4. `core/grow_ops.py`（新建）——心跳、体检、安装

```python
def stamp_heartbeat(run: dict, *, at: str, interval_minutes: int = 30,
                    ok: bool = True) -> dict   # 原地更新 run["heartbeat"] 并返回
def heartbeat_state(run: dict, *, now: datetime | None = None) -> dict
# {"last_tick","age_minutes","interval_minutes","ok","stale"}
# 无 last_tick → ok False, stale True；age > 2*interval → stale True。

def doctor(data_dir, run: dict, *, now: datetime | None = None) -> dict
# {"ok": bool, "checks": [{"name","ok","level":"blocker|warn|info",
#                          "detail","fix"}]}
# 至少检查：run 文件/状态、心跳、授权（execution_policy.allows 逐动作）、
# 读数覆盖（run.telemetry/measurement）、供给水位（grow_schedule.supply_status）、
# 队列文件 lint、账号健康（accounts.AccountRegistry 可读时，缺失只 warn）。

def launchd_plist(*, label: str, python: str, mkt_path: str, run_id: str,
                  interval_minutes: int, log_path: str, cwd: str) -> str
def install_launchd(*, out_path: str | Path, plist: str, load: bool = False) -> dict
# 写 plist 文件；load=True 时执行 launchctl load 并返回命令与返回码。
def crontab_line(*, python: str, mkt_path: str, run_id: str,
                 interval_minutes: int, log_path: str) -> str
```

测试：`tests/test_grow_ops.py`（心跳过期、doctor 阻断项、plist 内容含间隔与 run id、
crontab 行、不实际 load）。

---

## 5. `core/result_card.py`（扩展现有文件）——周结果单 v2

在 `build()` 现有三件套基础上**只增不改**，新增键：

```python
"business": {"available": bool, "funnel": {"signal":n,"signup":n,
             "activation":n,"paid":n}, "note": str}
# 来源 OutcomeLedger(data_dir).funnel(campaign=run["id"])；无事件时 available False。

"guardrails": {"failed": n, "indeterminate": n, "duplicates": n,
               "suppressed": n, "readings_days": n, "account_health": n|None}
# 优先读 run["telemetry"]["weeks"][week_start]；缺失给 0/None。

"supply": run.get("supply_state") or {}
"next_week": {"actions_per_week": int|None,
              "changes": [{"detail": str} ...]}   # 本周产生的调整
```

`render(card)` 追加三节（有数据才渲染，不改变现有三件套表格与边界声明）：

```
## 业务事件（漏斗比值口径，不证明因果）
| 阶段 | 数量 |
...
## 护栏与供给
- 失败 0｜未知 0｜重复拦截 4｜抑制跳过 2｜读数 6/7 天｜账号健康 92
- 下周已暂存 14/25（缺口 11）
## 下周动作
- ...
```

现有测试必须全绿（他们是 assertIn，不会冲突）。新增测试 `tests/test_result_card_v2.py`：
有/无业务事件、护栏缺省、render 追加且保持「不等于业务转化」声明。

---

## 6. `core/grow_compile.py`（Lead 实现）——策略编译器

```python
def compile_run(*, run_id, facts, goal_id, channels, target=None,
                window_days=None, actions_per_week=20, started=None,
                owner="", audience="us-west") -> dict  # 返回 RunSpec v2（不写盘）
def fingerprint(spec: dict) -> str
```

- 调 `core.goals.select(facts, goal_id, platforms=..., target=..., window_days=...)`
  拿目标契约；调 `core.strategy.build(product, goal_plan=selection)` 拿主/辅渠道 why。
- 渠道过滤：只用 `platform_caps.auto_channels()`；非 auto 抛 `CompileError`。
- actions：x → `["reply"]`（若用户指定 publish 则并入）；email → `["message"]`。
- quota：周计划 = actions_per_week；`split_week` 生成 `quota.daily`（按 started 锚定）。
- supply.briefs：每个 (channel, action) 一条 brief，`count_per_week` 为该动作周量。
- measurement.readers：x → followers + impressions，handle 来自 facts.owned_assets 或 `--handle`。
- guards.account_id 来自 `--account`；min_health=60。
- 输出含 `strategy.fingerprint = sha256(canonical json)[:16]`。

## 7. `core/grow.py`（Lead 重构）——v2 生命周期

保留现有公开方法（`start/arm/resume/tick/status/report/goal_progress`）与 v1 字段
（`plan`、`last_queue_run`、`adjustments`、`history`、`result_cards`），新增：

```python
@classmethod
def launch(cls, path, *, run_id, facts_path, goal_id, channels, target=None,
           window_days=None, actions_per_week=20, started=None, owner="",
           account="", handle="", data_dir, probe=None, skip_probe=False) -> dict
# 预检：facts 存在/必填字段、platform_caps 全 auto、minimal 授权（只查计划动作）、
# 账号健康（可读时）、环境探针（probe 可注入；skip_probe 跳过）。
# 编译 RunSpec → 落盘 → 返回 {"run":..., "summary":[...5 行...], "missing_policy":...}

def daily(self, data_dir, *, on=None, reader=None) -> dict      # 日结果卡（不投递）
def decide(self, *, action, note="", by="", on=None) -> dict   # continue|optimize
def doctor(self, data_dir, *, now=None) -> dict
def status(self, data_dir, *, on=None) -> dict   # 扩展：quota committed today / supply / heartbeat / pending decision
```

`tick()` 新行为（保持幂等，单测可注入 runner/reader/clock）：

1. 心跳：`stamp_heartbeat`；
2. 读数：若 `reader` 注入则调用（返回 `{"followers":int|None,"impressions":int|None}`），
   写入 OutcomeLedger（`channels.x.read_stats.record` 语义）；失败只记
   `telemetry.days[date].readings.error`，**不改 status**；连续 3 天无读数 → doctor warn；
3. 队列：按 `scheduled_for` 与日配额选条目，窗口外留到下个 tick；
   `run_queue(..., continue_on_duplicate=True)`；发送后 sleep 由 runner 负责
   （grow 只传 `min_interval` 提示，不在测试里真等）；
4. 日结果卡：写 `daily_cards` + 文件（不投递，投递在 CLI 层由 `--deliver` 控制）；
5. 周窗口：出周卡 v2 → 文件 + 若 `deliver_to` 配置则 dry-run 投递；
6. 调参：安全层（health<60 不加载）→ 供给层 → 效果层，全部记 adjustments；
7. `needs_attention` 语义不变：只有 failed/indeterminate/其它 rejected 才停。

## 8. `mkt.py` CLI（Lead）

```text
mkt grow platforms                        # 平台自动执行矩阵
mkt grow launch <id> --facts F --goal G [--target N] [--channels x,email]
                 [--actions-per-week N] [--account A] [--handle H]
                 [--owner MAIL] [--skip-probe]
mkt grow arm <id>
mkt grow resume <id> --by --note
mkt grow tick <id> [--on DATE] [--deliver]
mkt grow daily <id> [--on DATE]
mkt grow decide <id> --action continue|optimize [--note] [--by] [--dry-run]
mkt grow status <id>
mkt grow report <id> [--week N]
mkt grow doctor <id>
mkt grow install <id> [--interval 30] [--load]
mkt grow start ...                        # 兼容旧版，等价 launch（不投递）
```

退出码：0 正常；2 参数/预检失败；3 needs_attention。

## 9. 完成定义

- 808+ 测试全绿，新增测试覆盖上述每个模块；
- `python3 mkt.py demo` 通过；
- 临时工作区可完整跑：launch → 写队列 → tick（虚拟时钟 7 天）→ daily → decide
  → 第二周 report，全程零真实发送；
- README 首屏、`llms.txt`、`docs/feature-status.md`、`docs/grow-闭环.md` 与
  实际命令/承诺一致：**给 facts 和目标，自动执行并交付日/周结果**。
