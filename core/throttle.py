#!/usr/bin/env python3
"""节流（架构文档 §5）——随机间隔 + 单次硬上限 + 日预算。

数值来源：x-ops-automation 实测纪律（发布 45-90s、DM 75-150s、follow/like 单次 ≤10、
每日上限检测）+ Reddit/LinkedIn 风控红线（03 渠道文档）。只保守，不放松。
"""
import json
import random
from datetime import date
from pathlib import Path

# (min_s, max_s, run_cap, daily_cap)；None = 不设
# 预算口径 = 我们自己的运营预算，一律低于平台官方上限（OFFICIAL_LIMITS），只保守不放松
INTERVALS = {
    # X 官方（help.x.com 2026-05 版，06 文档§2）：未验证账号 50 原创+200 回复/日、DM 500/日、
    # 关注 400/日；页面残留旧"2400/日"字样属历史口径，按新表执行
    ("x", "publish"): (45, 90, 50, 40),       # 官方 50 原创/日 → 预算 40 留余量
    ("x", "reply"): (60, 120, 30, 40),        # 官方 200 回复/日 → 预算 40
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
    ("github", "reply"): (30, 90, 20, 50),
    ("github", "publish"): (60, 120, 10, 10),   # release/discussion 发帖（预留）
    ("github", "search"): (2, 5, None, 500),
}
FALLBACK = (60, 120, 10, 30)  # 未知渠道动作一律走保守缺省

# 平台官方上限参考（证据分级见 06 文档；供人/规划层对照，代码只信 INTERVALS 预算）
OFFICIAL_LIMITS = {
    # X（官方 help.x.com x-limits，2026-05 版；档位细目未公开=UNPUBLISHED）
    ("x", "publish", "free/unverified"): {"daily": "50 原创 + 200 回复", "grade": "官方"},
    ("x", "message", "free/unverified"): {"daily": 500, "grade": "官方"},
    ("x", "engage", "free/unverified"): {"daily_follow": 400, "total_follow": "5000 后按粉丝比例", "grade": "官方"},
    ("x", "publish", "premium"): {"daily": "未公开（社区传 1 万帖/月）", "grade": "未公开"},
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
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)

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

    def _state_file(self):
        return self.state_dir / f"mkt-daily-{date.today().isoformat()}.json"

    def daily_used(self, channel, action):
        f = self._state_file()
        if not f.exists():
            return 0
        try:
            return int(json.loads(f.read_text(encoding="utf-8")).get(f"{channel}/{action}", 0))
        except (json.JSONDecodeError, ValueError):
            return 0

    def daily_allows(self, channel, action, n=1):
        _, _, _, daily_cap = self.policy(channel, action)
        if daily_cap is None:
            return True
        return self.daily_used(channel, action) + n <= daily_cap

    def daily_charge(self, channel, action, n=1):
        """记账（发送成功后调用；状态文件按日滚动，旧文件即历史）。"""
        f = self._state_file()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state = {}
        if f.exists():
            try:
                state = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                state = {}
        key = f"{channel}/{action}"
        state[key] = int(state.get(key, 0)) + n
        f.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
