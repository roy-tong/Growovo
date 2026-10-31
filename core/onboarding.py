#!/usr/bin/env python3
"""初始化与"下一步" —— 降低首次价值时间（clone 之后先看到什么）。

mkt init 做三件事：建目录、报告凭据缺口、给出当前状态下的唯一优先动作。
它不安装依赖、不改系统配置、不碰账号——只把"接下来做什么"缩成一行。
"""
import os
from pathlib import Path

from .workspace import describe, ensure

ENV_CHECKS = (
    ("MKT_EMAIL_USER", "SMTP 发信账号（email 写）", False),
    ("MKT_EMAIL_APP_PASSWORD", "Gmail App Password（email 写）", False),
    ("MKT_GITHUB_TOKEN", "GitHub PAT（github 评论写；读不需要）", False),
    ("MKT_CDP_URL", "CDP 端点（可选，默认 http://127.0.0.1:9222）", True),
)


def env_status(env=None):
    env = os.environ if env is None else env
    return [{"key": k, "purpose": p, "optional": opt, "set": bool(env.get(k))}
            for k, p, opt in ENV_CHECKS]


def counts(data_dir):
    d = Path(data_dir)
    def _lines(p):
        if not p.is_file():
            return 0
        return sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
    return {
        "plans": len(list((d / "plans").glob("*.json"))) if (d / "plans").is_dir() else 0,
        "queues": len(list((d / "queues").glob("*.json"))) if (d / "queues").is_dir() else 0,
        "ledger": _lines(d / "ledger.jsonl"),
        "events": _lines(d / "outcomes" / "events.jsonl"),
        "experiments": len(list((d / "experiments").glob("*.json"))) if (d / "experiments").is_dir() else 0,
        "approvals": _lines(d / "approvals.jsonl"),
        "links": _lines(d / "links.jsonl"),
    }


def init(data_dir, *, env=None):
    ensure(data_dir)
    info = {
        "workspace": describe(data_dir),
        "path": str(Path(data_dir)),
        "env": env_status(env),
        "counts": counts(data_dir),
    }
    try:
        probe = Path(data_dir) / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        info["writable"] = True
    except OSError:
        info["writable"] = False
    from .campaign import next_step
    from .ledger import Ledger
    info["next"] = next_step(data_dir, Ledger(Path(data_dir) / "ledger.jsonl"))
    return info


def render_init(info):
    lines = [f"工作区：{info['workspace']}",
             f"可写：{'是' if info['writable'] else '否'}"]
    missing = [e for e in info["env"] if not e["set"] and not e["optional"]]
    if missing:
        lines.append("凭据缺口：" + "；".join(f"{e['key']}（{e['purpose']}）" for e in missing))
    else:
        lines.append("凭据：" + "，".join(e["key"] for e in info["env"]) + " 均已配置")
    c = info["counts"]
    lines.append(f"已有：计划 {c['plans']} · 队列 {c['queues']} · 审批 {c['approvals']} · "
                 f"账本 {c['ledger']} · 事件 {c['events']} · 实验 {c['experiments']} · "
                 f"链接 {c['links']}")
    if c["plans"] == 0 and (c["events"] or c["experiments"] or c["ledger"]):
        lines.append("注意：有事件/实验/账本但没有计划——可能来自其他产品混在同一工作区；"
                     "建议 --workspace 或 MKT_HOME 一个产品一个目录。")
    n = info["next"]
    lines += ["", f"下一步：{n['title']}", f"  {n['command']}"]
    if n.get("why"):
        lines.append(f"  理由：{n['why']}")
    return "\n".join(lines)
