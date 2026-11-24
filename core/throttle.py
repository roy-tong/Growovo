#!/usr/bin/env python3
"""节流（架构文档 §5）——随机间隔 + 单次硬上限 + 日预算。

数值来源：x-ops-automation 实测纪律（发布 45-90s、DM 75-150s、follow/like 单次 ≤10、
每日上限检测）+ Reddit/LinkedIn 风控红线（03 渠道文档）。只保守，不放松。
"""
import json
import random
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:  # POSIX 锁；Windows 没有 fcntl 时退化为单进程检查（仍比无检查安全）。
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

# 预算日界：夜间发送窗口 19:00→次日 07:00 跨本地 0 点，若按本地自然日记账，
# 前半夜会继承"当天凌晨"已用额度，导致 19:00-23:59 无额度、0 点后重新计满。
# utc = 按 UTC 0 点（北京时间 08:00）切分，一个夜间窗口落在一个预算日内，
# 也与 X 日上限的重置口径一致。需要回到本地自然日时传 day_boundary="local"。
DAY_BOUNDARY = "utc"


def _now():
    return datetime.now(timezone.utc)

# (min_s, max_s, run_cap, daily_cap)；None = 不设
# 预算口径 = 我们自己的运营预算，一律低于平台官方上限（OFFICIAL_LIMITS），只保守不放松
INTERVALS = {
    # X 官方（help.x.com 2026-05 版，06 文档§2）：未验证账号 50 原创+200 回复/日、DM 500/日、
    # 关注 400/日；页面残留旧"2400/日"字样属历史口径，按新表执行
    ("x", "publish"): (45, 90, 50, 48),       # 官方 50 原创/日 → 预算 48 留余量
    ("x", "reply"): (60, 120, 30, 180),       # 官方 200 回复/日 → 预算 180 留余量
    ("x", "message"): (75, 150, 15, 30),       # 官方 500 DM/日 → 预算 30（节奏比上限更重要）
    ("x", "engage"): (6, 12, 10, 40),          # 官方关注 400/日 → 预算 40
    ("x", "search"): (5, 10, None, None),
    # Gmail 官方：免费 500 封/日（网页），SMTP 社区共识 ~100/日（06 文档§2）
    ("email", "publish"): (60, 150, 50, 100),
    ("email", "reply"): (30, 90, 50, 100),
    ("email", "message"): (60, 150, 50, 100),  # 预算 100 ≤ SMTP 社区共识线
    # Reddit：新号 ~1 条/10 分钟冷却、子版块 karma/账龄门槛（06 文档§2）
    ("reddit", "publish"): (300, 900, 3, 3),   # 03 文档红线：日 ≤3 帖
    ("reddit", "reply"): (120, 300, 10, 20),
    ("reddit", "message"): (120, 300, 5, 10),
    # LinkedIn：邀请 ~100/周（社区共识），预注册到周预算的一半以下
    ("linkedin", "message"): (60, 180, 10, 10),  # Tier B 半自动日 ≤10 连接
    ("linkedin", "reply"): (60, 180, 10, 20),
    # GitHub：REST API 限额 5000 请求/时（认证）——API 不缺额度，预算防的是
    # 对他人仓库的评论骚扰（社区红线：千人千面、回复要人写）
    ("github", "reply"): (30, 90, 20, 110),
    ("github", "publish"): (60, 120, 10, 10),   # release/discussion 发帖（预留）
    ("github", "search"): (2, 5, None, 500),
    # Discord：官方文档未公开 per-user 消息速率（UNPUBLISHED）；社区实测约 5 消息/5 秒，
    # 频道级 slowmode 另计。预算口径：25 秒间隔 / 每小时 6 条 / 每日 120 条
    ("discord", "publish"): (25, 45, 6, 120),
    # 回复与频道发帖同受 slowmode/连发约束；日预算 60 低于 publish 的 120，
    # 因为入站回复优先、对象有限，且不鼓励为凑数制造对话
    ("discord", "reply"): (25, 45, 6, 60),
    # HN：无公开的 per-user 提交限额口径（过频提交的处置是 shadow 处理，社区共识）；
    # 预算取文化纪律——Show HN 是低频发布动作，守帖（回评论）才是日更负担
    ("hn", "publish"): (60, 120, 1, 1),         # 日 ≤1：HN 文化=稀缺发布，多投自毁
    ("hn", "reply"): (60, 180, 20, 30),          # 守帖回评节奏 1-3 分钟，日 ≤30
    ("hn", "search"): (2, 5, None, 500),         # Algolia 公共 API，匿名宽限
}
FALLBACK = (60, 120, 10, 30)  # 未知渠道动作一律走保守缺省

# 平台官方上限参考（证据分级见 06 文档；供人/规划层对照，代码只信 INTERVALS 预算）
OFFICIAL_LIMITS = {
    # X（官方 help.x.com x-limits，2026-05 版；档位细目未公开=UNPUBLISHED）
    ("x", "publish", "free/unverified"): {"daily": "50 原创 + 200 回复", "grade": "官方"},
    ("x", "message", "free/unverified"): {"daily": 500, "grade": "官方"},
    ("x", "engage", "free/unverified"): {"daily_follow": 400, "total_follow": "5000 后按粉丝比例", "grade": "官方"},
    ("x", "publish", "premium"): {"daily": "未公开（社区传 1 万帖/月）", "grade": "未公开"},
    ("x", "reply", "premium"): {"daily": "未公开（官方未单列 premium 回复上限；社区传 1 万帖/月含回复）", "grade": "未公开"},
    # Gmail（官方 support.google.com）
    ("email", "*", "gmail-free"): {"daily": 500, "grade": "官方"},
    ("email", "*", "workspace"): {"daily": 2000, "grade": "官方"},
    ("email", "*", "outlook-free"): {"daily": "~300（社区共识）", "grade": "社区共识"},
    # Reddit（官方 API 文档）
    ("reddit", "*", "free-api"): {"qpm": 100, "grade": "官方"},
    ("reddit", "publish", "new-account"): {"cooldown": "~1 帖/10 分钟，karma 放宽", "grade": "社区共识"},
    # LinkedIn（邀请=社区共识，InMail=官方）
    ("linkedin", "message", "free"): {"invites_weekly": "~100", "inmail_monthly": 0, "grade": "混合"},
    ("linkedin", "message", "sales-navigator-core"): {"invites_weekly": "~200", "inmail_monthly": 50, "grade": "混合"},
    # GitHub（官方 REST API 文档；核心资源 5000 请求/时=认证，60/时=匿名）
    ("github", "*", "authenticated"): {"requests_hour": 5000, "grade": "官方"},
    ("github", "*", "anonymous"): {"requests_hour": 60, "grade": "官方"},
    ("github", "search", "authenticated"): {"requests_minute": 30, "grade": "官方"},
}


class ThrottleError(Exception):
    pass


class Throttle:
    def __init__(self, state_dir, day_boundary=None):
        self.state_dir = Path(state_dir)
        self.day_boundary = day_boundary or DAY_BOUNDARY

    def policy(self, channel, action):
        return INTERVALS.get((channel, action), FALLBACK)

    def interval(self, channel, action, override=0):
        """下一条之间的等待秒数。override>0 用固定值（人类显式指定优先）。"""
        if override > 0:
            return override
        lo, hi, _, _ = self.policy(channel, action)
        return random.randint(lo, hi)

    def check_run(self, channel, action, n):
        """单次运行硬上限：超了直接拒跑整批（x-ops follow ≤10 同款纪律）。"""
        _, _, run_cap, _ = self.policy(channel, action)
        if run_cap is not None and n > run_cap:
            raise ThrottleError(
                f"{channel}/{action} 单次上限 {run_cap}，本次队列 {n} 条；拆批或分天跑"
            )

    def run_cap(self, channel, action):
        """单次运行上限（条）；None = 不设上限。"""
        _, _, cap, _ = self.policy(channel, action)
        return cap

    def slice_batches(self, channel, action, n):
        """把 n 条切成每批不超过单次上限的下标区间；无上限时一批跑完。

        与 check_run 的区别：check_run 供调用方显式选择「整批拒跑」，
        这里供业务继续——切片后逐批执行，超出的部分由调用方记 deferred。
        """
        cap = self.run_cap(channel, action)
        if cap is None or cap <= 0 or n <= cap:
            return [range(0, n)]
        return [range(start, min(start + cap, n)) for start in range(0, n, cap)]

    def advice(self, channel, action, n=0):
        """额度不足时的可执行下一步：拆批节奏 + 顺延到下一预算日。

        只给建议、不抛错也不阻断调用方；涉及平台风控时说明为什么要降频。
        """
        lo, hi, cap, daily_cap = self.policy(channel, action)
        remaining = self.remaining(channel, action)
        parts = []
        if cap is not None and n > cap:
            batches = -(-n // cap)
            parts.append(
                f"单次上限 {cap} 条：本批 {n} 条拆成 {batches} 批，"
                f"每批之间停 {lo}-{hi}s；连续整批发送更容易触发平台风控"
            )
        if daily_cap is not None and remaining is not None and n > remaining:
            when = ("UTC 0 点（北京时间 08:00）" if self.day_boundary != "local"
                    else "本地 0 点")
            if remaining > 0:
                parts.append(
                    f"日预算 {daily_cap} 条、今日剩余 {remaining} 条：先发 {remaining} 条，"
                    f"其余 {n - remaining} 条顺延到下一预算日（{when}重置），"
                    f"或改用其他渠道；把余量一次打满会提高平台风控风险"
                )
            else:
                parts.append(
                    f"日预算 {daily_cap} 条已用完：本次 {n} 条顺延到下一预算日"
                    f"（{when}重置），或改用其他渠道；当天补量连发会提高平台风控风险"
                )
        if not parts:
            parts.append(f"额度充足（本次 {n} 条），按 {lo}-{hi}s 节奏发送")
        return "；".join(parts)

    def _day_key(self):
        now = _now()
        if self.day_boundary == "local":
            return now.astimezone().date().isoformat()
        return now.date().isoformat()

    def _state_file(self):
        if self.day_boundary == "rolling_24h":
            return self.state_dir / "mkt-rolling-24h.json"
        # 文件名带口径前缀：切换日界时不会误读旧口径的账本（旧文件仍留在磁盘可审计）。
        prefix = "utc-" if self.day_boundary != "local" else ""
        return self.state_dir / f"mkt-daily-{prefix}{self._day_key()}.json"

    def _read_state(self, f=None):
        f = f or self._state_file()
        if not f.exists():
            return {}
        try:
            value = json.loads(f.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}

    @contextmanager
    def _locked(self):
        """跨进程串行化预算状态；锁文件只用于互斥，不承载业务数据。"""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        lock = self.state_dir / ".throttle.lock"
        fh = lock.open("a+", encoding="utf-8")
        try:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()

    def _rolling_events(self):
        state = self._read_state()
        cutoff = _now() - timedelta(hours=24)
        events = []
        for item in state.get("events", []):
            try:
                at = datetime.fromisoformat(str(item.get("at", "")).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            if at >= cutoff:
                events.append(at)
        return events

    def _legacy_local_used(self, channel, action):
        if self.day_boundary in ("local", "rolling_24h"):
            return 0
        now = _now()
        utc_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc)
        local_start = utc_start.astimezone().date()
        local_end = (utc_start + timedelta(days=1) - timedelta(microseconds=1)).astimezone().date()
        key = f"{channel}/{action}"
        total = 0
        day = local_start
        while day <= local_end:
            state = self._read_state(self.state_dir / f"mkt-daily-{day.isoformat()}.json")
            try:
                total += max(0, int(state.get(key, 0) or 0))
            except (TypeError, ValueError):
                pass
            day += timedelta(days=1)
        return total

    def daily_used(self, channel, action):
        if self.day_boundary == "rolling_24h":
            return len(self._rolling_events())
        state = self._read_state()
        try:
            current = int(state.get(f"{channel}/{action}", 0))
        except (TypeError, ValueError):
            current = 0
        return current + self._legacy_local_used(channel, action)

    def daily_allows(self, channel, action, n=1):
        _, _, _, daily_cap = self.policy(channel, action)
        if daily_cap is None:
            return True
        return self.daily_used(channel, action) + n <= daily_cap

    def remaining(self, channel, action):
        """只读：今天还剩多少条额度；无日上限返回 None。

        计划阶段用它判断"排得下、但发到一半就撞日上限"，避免把当天注定
        发不出的动作排进波次。
        """
        _, _, _, daily_cap = self.policy(channel, action)
        if daily_cap is None:
            return None
        return max(0, int(daily_cap) - self.daily_used(channel, action))

    def reserve(self, channel, action, n=1):
        """发送前原子预留额度；额度不足抛 ThrottleError。

        预留在发送动作之前完成，因此并发队列也不会同时越过日上限。若后续
        发送异常，额度保持已占用（保守：未知发送不能被当成没发）。
        """
        _, _, _, daily_cap = self.policy(channel, action)
        if daily_cap is None:
            return self.daily_used(channel, action)
        key = f"{channel}/{action}"
        with self._locked():
            f = self._state_file()
            state = self._read_state(f)
            used = self.daily_used(channel, action)
            if used + n > daily_cap:
                raise ThrottleError(
                    f"{channel}/{action} 日预算已用 {used}/{daily_cap}，"
                    f"不能再预留 {n} 次；{self.advice(channel, action, n)}")
            if self.day_boundary == "rolling_24h":
                events = list(state.get("events", []))
                events.extend({"at": _now().isoformat()} for _ in range(n))
                state = {"schema": "growovo.rolling-24h/1", "events": events}
                f.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
                return len(self._rolling_events())
            state[key] = used + n
            f.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
            return state[key]

    def daily_charge(self, channel, action, n=1):
        """兼容旧调用：原子增加已用额度（发送成功后调用）。"""
        if self.day_boundary == "rolling_24h":
            with self._locked():
                f = self._state_file()
                state = self._read_state(f)
                events = list(state.get("events", []))
                events.extend({"at": _now().isoformat()} for _ in range(n))
                f.write_text(json.dumps({"schema": "growovo.rolling-24h/1", "events": events}, ensure_ascii=False, indent=1), encoding="utf-8")
            return
        key = f"{channel}/{action}"
        with self._locked():
            f = self._state_file()
            state = self._read_state(f)
            state[key] = int(state.get(key, 0) or 0) + n
            f.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
