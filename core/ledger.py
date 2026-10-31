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
from datetime import datetime
from pathlib import Path

OK_RESULTS = ("committed",)
# indeterminate = 可能已发送但未能确认。自动重发会造成重复触达，必须先由人
# 通过 ledger.resolve 覆盖为 committed/failed；不能在未知状态上再发一次。
NO_RETRY_CODES = frozenset({"indeterminate", "no-dm", "bounced", "suppressed"})


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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def _raw(self):
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 坏行不炸账本，跳过并保持只读语义
        return out

    def _resolutions(self):
        """ref → 最新裁决（append-only 覆盖，不改原账本行）。"""
        if not self.resolutions_path.exists():
            return {}
        out = {}
        for line in self.resolutions_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
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
        self.resolutions_path.parent.mkdir(parents=True, exist_ok=True)
        with self.resolutions_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
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
