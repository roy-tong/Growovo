#!/usr/bin/env python3
"""X 渠道适配器 —— vendor x-ops-automation（PROVEN），架构文档 §6.1。

vendor 脚本原样执行（自带节流/回读校验/防错人/防重发），本层做三件事：
  1. 统一 mkt CLI 语义（写操作默认 dry-run，--go 才实跑）
  2. campaign 维度记账
  3. 把 vendor 的结果 JSON / DM 日志回写全局账本（跨渠道幂等）

vendor 脚本自身的 dry-run 语义不统一（publish/reply/xdm 默认实跑、--dry-run 干跑），
本层在未给 --go 时一律给 vendor 追加 --dry-run，保证 mkt 层语义一致。
"""
import json
import subprocess
import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor"

# action -> (vendor 脚本, 是否写操作, 实跑时是否需要 --go 之外的参数)
ACTIONS = {
    "publish": ("publish_x.py", True),      # 线程发布：--file <queue.json> --thread 可选
    "reply": ("reply_x.py", True),
    "message": ("xdm.py", True),            # DM 队列（读 vendor 默认 outreach 目录）
    "engage": (None, True),                 # follow/like 按子参数分派
    "search": ("search_x.py", False),       # 只读
    "inbox": ("scan_inbox.py", False),      # 只读（Gmail，同 CDP 会话）
}


def _latest_result(patterns, before=None):
    """找 vendor logs/ 里 before 时间点之后生成的最新结果文件。"""
    logs = VENDOR / "logs"
    if not logs.exists():
        return None
    cands = [p for pat in patterns for p in logs.glob(pat)]
    if before is not None:
        cands = [p for p in cands if p.stat().st_mtime >= before - 1]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def _vendor_targets(action, data):
    """从 vendor 结果提取 ledger target。"""
    out = []
    if action == "message":
        for d in data if isinstance(data, list) else [data]:
            if isinstance(d, dict) and d.get("handle"):
                out.append(("@" + d["handle"], d.get("result", "?")))
    elif action == "publish":
        recs = data.get("records", []) if isinstance(data, dict) else []
        for r in recs:
            idx = r.get("index", "?")
            res = "committed" if r.get("posted") else ("dry-run" if r.get("dry_run") else "failed")
            out.append((f"tweet:{idx}", res))
    return out


def _log_to_ledger(action, campaign, ledger, before_lines, log=None):
    """把 vendor 运行结果回写全局账本（审计 + 跨渠道幂等视图）。"""
    wrote = 0
    if action == "message":
        # xdm 追加日志：xdm-log.jsonl（outreach 目录）；只记本次运行新增的行
        logf = VENDOR / "xdm-log.jsonl"
        real = Path.home() / ".x-ops-automation" / "outreach" / "xdm-log.jsonl"
        logf = logf if logf.exists() else (real if real.exists() else None)
        if logf:
            lines = logf.read_text(encoding="utf-8").splitlines()
            for line in lines[before_lines.get("xdm", 0):]:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("handle"):
                    result = {"dry-run-draft": "dry-run", "sent": "committed"}.get(
                        d.get("result"), d.get("result", "?"))
                    ledger.append(channel="x", transport="cdp", action="message",
                                  campaign=campaign, target="@" + d["handle"], result=result)
                    wrote += 1
    else:
        pat = {"publish": "publish-*.json", "reply": "reply-*.json",
               "engage": ["follow-*.json", "like-*.json"]}.get(action)
        pats = pat if isinstance(pat, list) else [pat] if pat else []
        f = _latest_result(pats)
        if f:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
            if action == "publish":
                for target, res in _vendor_targets(action, data):
                    ledger.append(channel="x", transport="cdp", action=action,
                                  campaign=campaign, target=target, result=res,
                                  evidence=str(f))
                    wrote += 1
            elif action == "engage":
                recs = data.get("records", data if isinstance(data, list) else [])
                for r in recs if isinstance(recs, list) else []:
                    t = r.get("handle") or r.get("url") or "?"
                    ok = r.get("followed", r.get("liked", False))
                    ledger.append(channel="x", transport="cdp", action="engage",
                                  campaign=campaign, target=str(t),
                                  result="committed" if ok else "failed")
                    wrote += 1
    if log and wrote:
        log(f"已回写全局账本 {wrote} 条")
    return wrote


def _xdm_log_lines():
    logf = VENDOR / "xdm-log.jsonl"
    real = Path.home() / ".x-ops-automation" / "outreach" / "xdm-log.jsonl"
    logf = logf if logf.exists() else (real if real.exists() else None)
    if not logf:
        return 0
    return len(logf.read_text(encoding="utf-8").splitlines())


def run(action, extra_args, *, go=False, campaign=None, ledger=None, log=print):
    """执行 vendor 脚本并把结果记账。go=False 时写操作自动降级为 dry-run。"""
    if action not in ACTIONS:
        raise SystemExit(f"X 渠道不支持动作: {action}（支持: {sorted(ACTIONS)}）")
    script, is_write = ACTIONS[action]

    if action == "engage":
        # engage 按第一个参数分派 follow/like
        sub = extra_args[0] if extra_args else ""
        script = {"follow": "follow_x.py", "like": "like_x.py"}.get(sub)
        if not script:
            raise SystemExit("engage 需要子动作: follow <@a @b> | like <url...>")
        extra_args = extra_args[1:]

    # 全局暂停闸（core.safety）：写操作一律拒跑
    if is_write:
        from core.safety import pause_file
        DATA_ROOT = VENDOR.parents[2] / "data"   # channels/x/vendor → mkt-automation/data
        if pause_file(DATA_ROOT).exists():
            raise SystemExit("全局暂停中（mkt pause --off 恢复）——X 写操作拒跑")

    before_lines = {"xdm": _xdm_log_lines()}
    cmd = [sys.executable, str(VENDOR / script)] + [str(a) for a in extra_args]
    if is_write and not go:
        cmd.append("--dry-run")
        log(f"[x/{action}] dry-run（加 --go 实跑）")
    if script == "x_search.sh" and go:
        cmd.append("--go")

    proc = subprocess.run(cmd, cwd=str(VENDOR))
    if ledger is not None and proc.returncode == 0:
        try:
            _log_to_ledger(action, campaign or "", ledger, before_lines, log=log)
        except Exception as exc:  # 记账失败不掩盖 vendor 结果
            log(f"账本回写失败（不影响本次执行）: {exc}", "WARN")
    return proc.returncode
