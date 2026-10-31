#!/usr/bin/env python3
"""全局安全闸（07 完善清单）——mkt pause 总闸 + 人类可读报告。

pause：data/state/PAUSED 存在即一切写操作拒跑（pipeline / X adapter / MCP 同查此文件）。
处置事故/风控信号时第一动作就是 `mkt pause --on`。
"""
from datetime import datetime, timedelta
from pathlib import Path


def pause_file(data_dir):
    return Path(data_dir) / "state" / "PAUSED"


def is_paused(data_dir):
    """文件存在即暂停（无内容态歧义；--off 即删除）。"""
    return pause_file(data_dir).exists()


def set_pause(data_dir, on, reason=""):
    f = pause_file(data_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    if on:
        f.write_text(f"{reason or 'paused'} @ {datetime.now().isoformat(timespec='seconds')}\n",
                     encoding="utf-8")
    else:
        f.unlink(missing_ok=True)


def report(data_dir, ledger, hours=24):
    """一页纸运营报告（mkt report）：近期活动/异常/暂停态/抑制名单。"""
    from core.gates import load_suppressions
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    recs = [r for r in ledger.all() if r.get("ts", "") >= since]
    by = {}
    for r in recs:
        key = (r.get("channel", "?"), r.get("result", "?"))
        by[key] = by.get(key, 0) + 1
    attention = [r for r in recs if r.get("result") in ("indeterminate", "failed")]
    campaigns = {}
    for r in recs:
        if r.get("campaign"):
            campaigns[r["campaign"]] = campaigns.get(r["campaign"], 0) + 1
    return {
        "window_hours": hours,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "paused": is_paused(data_dir),
        "total_records": len(recs),
        "by_channel_result": [
            {"channel": c, "result": rs, "count": n} for (c, rs), n in sorted(by.items())
        ],
        "needs_attention": [
            {"ts": r.get("ts"), "channel": r.get("channel"), "target": r.get("target"),
             "result": r.get("result"), "detail": (r.get("detail") or "")[:120]}
            for r in attention[:20]
        ],
        "campaigns": dict(sorted(campaigns.items(), key=lambda kv: -kv[1])),
        "suppress_list_size": len(load_suppressions(data_dir)),
        "note": "indeterminate 需人工裁决：平台后台核实后用 mkt ledger resolve <ref> 记回（原账本行不改）",
    }
