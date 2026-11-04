#!/usr/bin/env python3
"""全局账本 —— 跨渠道唯一事实源（架构文档 §4.2）。

data/ledger.jsonl 每行一条 JSON：
  {ts, channel, transport, action, campaign, target, result,
   evidence, ref, detail...}

result 终态约定（与写路径状态机对齐）：
  committed      发送成功且已确认
  indeterminate  已提交但未能确认（人工裁决；禁止自动重发）
  failed         发送前失败（gate 拒绝 / 回读不一致 / UI 或协议失败）
  dry-run        干跑留证（永远不算已发送）
  no-dm          渠道级终态：目标不接受私信（X）
  bounced        渠道级终态：退信（Email）
  suppressed     抑制名单命中（不该出现在新队列里，出现即拦截）

账本回答三个问题：发过没有（幂等）、campaign 进度到哪（断点续跑）、
谁在不重试名单里（no-dm / bounced / suppressed）。
"""
import json
import os
from datetime import datetime
from pathlib import Path

OK_RESULTS = ("committed",)
# indeterminate = 可能已发送但未能确认。自动重发会造成重复触达，必须先由人
# 通过 ledger.resolve 覆盖为 committed/failed；不能在未知状态上再发一次。
NO_RETRY_CODES = frozenset({"indeterminate", "no-dm", "bounced", "suppressed"})


class LedgerCorruptError(RuntimeError):
    """账本/裁决文件出现坏行：静默跳过会把 committed 记录变成"没发过"，
    重跑队列即重复发送。读侧显式报错，发送门禁按 fail-closed 拒绝。"""


class LedgerLockError(RuntimeError):
    """账本文件锁不可用：并发安全是账本可靠性的前置条件，
    锁获取失败不得静默降级为无锁写入（review 2026-11-04 复核）。"""


def _append_line(path: Path, line: str) -> None:
    """O_APPEND + flock + 单次 write：与 event_store 同一追加纪律，
    多进程（MCP + CLI）并发追加不撕裂、不交错。锁不可用时 fail closed。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = line.encode("utf-8")
    fd = os.open(str(path), os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o644)
    locked = False
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            locked = True
        except ImportError as exc:
            raise LedgerLockError(
                "平台缺少 fcntl（%s）；无法保证 %s 的并发追加安全，拒绝无锁写入。"
                % (exc, path)) from exc
        except OSError as exc:
            raise LedgerLockError(
                "获取账本文件锁失败（%s：%s）；拒绝无锁写入 %s。"
                % (path, exc, path)) from exc
        written = os.write(fd, payload)
        if written != len(payload):  # 极端短写也按失败处理，不留半行
            raise OSError("short write to %s (%d/%d bytes)"
                          % (path, written, len(payload)))
    finally:
        if locked:
            try:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def _read_jsonl_fail_loud(path: Path, *, kind: str):
    """读 JSONL；空行/注释行跳过，坏行报 LedgerCorruptError（带行号）。"""
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as handle:
        for number, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise LedgerCorruptError(
                    "%s 第 %d 行不是合法 JSON：%s。静默跳过会把已发送记录当成"
                    "未发送（重跑即重复触达）；请修复该行或人工裁决后重试，"
                    "发送门禁在此之前按 fail-closed 拒绝。" % (path, number, exc)
                ) from exc
    return out


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.resolutions_path = self.path.parent / "ledger_resolutions.jsonl"

    def append(self, *, channel, action, target, result, transport=None,
               campaign=None, evidence=None, ref=None, **extra):
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "channel": channel,
            "transport": transport or "",
            "action": action,
            "campaign": campaign or "",
            "target": target,
            "result": result,
            "evidence": evidence or "",
            "ref": ref or "",
        }
        rec.update(extra)
        _append_line(self.path, json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def _raw(self):
        return _read_jsonl_fail_loud(self.path, kind="账本")

    def _resolutions(self):
        """ref → 最新裁决（append-only 覆盖，不改原账本行）。"""
        out = {}
        for rec in _read_jsonl_fail_loud(self.resolutions_path, kind="裁决"):
            if rec.get("ref"):
                out[rec["ref"]] = rec
        return out

    def all(self):
        """账本 + 人工裁决覆盖后的视图（原行不改）。"""
        resolutions = self._resolutions()
        out = []
        for r in self._raw():
            hit = resolutions.get(r.get("ref"))
            if hit:
                r = dict(r, result=hit.get("result", r.get("result")),
                         resolved_by=hit.get("by", ""),
                         resolved_at=hit.get("ts", ""),
                         resolution_note=hit.get("note", ""))
            out.append(r)
        return out

    def resolve(self, ref, *, result, by, note):
        """人工裁决 indeterminate（平台后台核实后记回）。返回裁决记录。"""
        if not str(ref or "").strip():
            raise ValueError("ref 不能为空（要裁决哪一条账本记录）")
        if result not in ("committed", "failed", "suppressed", "bounced", "no-dm"):
            raise ValueError("裁决 result 只允许 committed/failed/suppressed/bounced/no-dm")
        if not str(by or "").strip():
            raise ValueError("裁决必须署名（--by）")
        if not str(note or "").strip():
            raise ValueError("裁决必须写依据（--note，如平台后台链接/截图路径）")
        if not any(r.get("ref") == ref for r in self._raw()):
            raise ValueError(f"账本里没有 ref={ref!r} 的记录")
        rec = {"ts": datetime.now().isoformat(timespec="seconds"),
               "ref": ref, "result": result, "by": by, "note": note}
        _append_line(self.resolutions_path, json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def indeterminate(self):
        return [r for r in self.all() if r.get("result") == "indeterminate"]

    def results(self, *, channel=None, campaign=None, action=None, target=None):
        return [
            r for r in self.all()
            if (channel is None or r.get("channel") == channel)
            and (campaign is None or r.get("campaign") == campaign)
            and (action is None or r.get("action") == action)
            and (target is None or r.get("target") == target)
        ]

    def already(self, *, channel, target, campaign=None, ok=OK_RESULTS):
        """同渠道同目标（可限 campaign）是否已有成功终态——幂等/防重发依据。"""
        return any(
            r.get("result") in ok
            for r in self.results(channel=channel, target=target, campaign=campaign)
        )

    def no_retry(self, channel, codes=NO_RETRY_CODES):
        """不重试名单：渠道级终态目标的集合。"""
        return {
            r.get("target")
            for r in self.results(channel=channel)
            if r.get("result") in codes and r.get("target")
        }

    def summary(self):
        """按 channel/campaign/result 聚合计数（mkt status 用）。"""
        agg = {}
        for r in self.all():
            key = (r.get("channel", "?"), r.get("campaign") or "-", r.get("result", "?"))
            agg[key] = agg.get(key, 0) + 1
        return {
            "total": sum(agg.values()),
            "breakdown": [
                {"channel": c, "campaign": cp, "result": rs, "count": n}
                for (c, cp, rs), n in sorted(agg.items())
            ],
        }
