#!/usr/bin/env python3
"""批量采集计划器：把「每天 250 条」拆成多次小批量跑。

为什么不能一次跑 250：平台看的是**单位时间内的行为密度**，一次跑完必然触发风控。
所以这里做三件事：

    配额   日预算写在 data/harvest_state.json，跨进程累计，跑完就停
    分批   每次最多 20 条，批间隔随批次递增（第一批 20 秒，后面越来越慢）
    熔断   连续 N 次拿不到内容、或出现验证 → 立刻停，当天不再跑

用法：
    python3 scripts/harvest_plan.py --platform xhs --budget 250
    python3 scripts/harvest_plan.py --platform xhs --budget 60   # 先按小额度试
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
STATE = BASE / "data" / "harvest_state.json"

QUERIES = {
    "xhs": [
        # 运营策略
        "自动化运营", "账号矩阵 运营", "内容运营 方法论", "用户增长 策略",
        "私域 运营 打法", "投放 复盘", "选题 方法论", "爆款 拆解",
        # 自动化 / 工具
        "AI 运营 工作流", "批量 生成 内容", "自动化 发布", "RPA 运营",
        # 风控
        "限流 原因 排查", "封号 申诉", "违规 判定", "风控 机制",
        "养号 方法", "矩阵 号 防关联", "搬运 判定",
    ],
    "douyin": [
        "自动化运营", "矩阵号 玩法", "风控 限流", "投流 复盘", "内容 方法论",
    ],
}

BATCH = 20          # 单次上限（与 harvest_cdp 的硬上限一致）
BREAKER = 3         # 连续空手 N 次就熔断


def load_state() -> dict:
    today = date.today().isoformat()
    if STATE.exists():
        st = json.loads(STATE.read_text(encoding="utf-8"))
    else:
        st = {}
    st.setdefault(today, {})
    return st


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", choices=sorted(QUERIES), default="xhs")
    ap.add_argument("--budget", type=int, default=250)
    ap.add_argument("--stop-after", type=int, default=0, help="跑够这么多条就停（0=用预算）")
    ap.add_argument("--gap", type=float, default=20.0, help="批间隔基准秒数")
    args = ap.parse_args()

    today = date.today().isoformat()
    st = load_state()
    used = st[today].get(args.platform, 0)
    target = args.stop_after or args.budget
    print(f"[{args.platform}] 今日已采 {used} 条，本次目标 {target} 条（日预算 {args.budget}）")

    qs = QUERIES[args.platform]
    qi = 0
    empty_run = 0
    delay = args.gap
    while target > 0:
        if used >= args.budget:
            print(f"达到日预算 {args.budget}，停止")
            break
        n = min(BATCH, target, args.budget - used)
        q = qs[qi % len(qs)]
        qi += 1
        cmd = [sys.executable, "scripts/harvest_cdp.py", "search",
               "--platform", args.platform, "--q", q, "--limit", str(n)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        out = (proc.stdout or "").strip()
        tail = "\n".join(out.splitlines()[-3:])
        got = out.count("✓")
        print(f"\n— {q} → {got} 条\n{tail}")
        if "安全验证" in (proc.stderr or "") or proc.returncode == 2:
            print("⛔ 触发验证，熔断停止", file=sys.stderr)
            st[today][args.platform] = used
            save_state(st)
            return 1
        if got == 0 and proc.returncode not in (0,):
            print(f"  （本批异常退出 {proc.returncode}，非风控，继续下一批）")
        used += got
        target -= got
        st[today][args.platform] = used
        save_state(st)
        empty_run = empty_run + 1 if got == 0 else 0
        if empty_run >= BREAKER:
            print(f"连续 {BREAKER} 批空手，今天先停（可能是风控前兆）")
            break
        if target <= 0:
            break
        print(f"  已采 {used} 条，休息 {delay:.0f} 秒 …")
        time.sleep(delay)
        delay = min(delay * 1.4, 180)

    print(f"\n本次结束：今日累计 {st[today].get(args.platform, 0)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
