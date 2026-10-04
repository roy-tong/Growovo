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
INTERVALS = {
    ("x", "publish"): (45, 90, 50, 100),      # X 免费号每日 ~100 上限（横幅检测在渠道层）
    ("x", "reply"): (60, 120, 30, 60),
    ("x", "message"): (75, 150, 15, 30),       # xdm.py 实测节奏
    ("x", "engage"): (6, 12, 10, 40),          # follow/like 单次 ≤10（硬限制，实测纪律）
    ("x", "search"): (5, 10, None, None),
    ("email", "publish"): (60, 150, 50, 100),  # Gmail SMTP 500/日，预算远低于限额
    ("email", "reply"): (30, 90, 50, 100),
    ("email", "message"): (60, 150, 50, 100),
    ("reddit", "publish"): (300, 900, 3, 3),   # 03 文档红线：日 ≤3 帖
    ("reddit", "reply"): (120, 300, 10, 20),   # 90/10 法则靠内容配额，不靠节奏
    ("reddit", "message"): (120, 300, 5, 10),
    ("linkedin", "message"): (60, 180, 10, 10),  # Tier B 半自动日 ≤10 连接
    ("linkedin", "reply"): (60, 180, 10, 20),
}
FALLBACK = (60, 120, 10, 30)  # 未知渠道动作一律走保守缺省


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
