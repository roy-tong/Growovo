#!/usr/bin/env python3
"""效果归因 —— 事件与漏斗台账（Growovo「量」的第二块）。

data/outcomes/events.jsonl 每行一条（两种记录）：
  事件（有主体）：{kind:"event", ts, subject, event_type, channel, touch,
                  source_ref, approximated:false}
  计数（无主体的聚合快照）：{kind:"count", ts, metric, value, window,
                            source_ref, approximated}

event_type 漏斗序（价值事件的口径声明在先）：
  signal（访问/曝光）→ signup（注册，允许近似口径但必须标 approximated）
  → activation（产品内关键动作）→ paid（首笔付费）

纪律：
1. 时间先后 ≠ 因果。本模块只输出 last_touch / first_touch 两种**记账口径**，
   不输出「渠道导致转化」的因果结论；
2. 计数必须带 source_ref 与 window；近似口径（如 GitHub clone 近似注册）
   必须显式 approximated=true，报告中原样展示，不得洗白成真实事件；
3. signup 近似口径不得升级为 activation/paid——价值事件层级只认产品内
   真实发生的行为。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

FUNNEL = ("signal", "signup", "activation", "paid")


class AttributionError(ValueError):
    pass


class OutcomeLedger:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "outcomes" / "events.jsonl"

    # ---- 写入 ----
    def add_event(self, *, subject: str, event_type: str, channel: str = "",
                  touch: str = "", source_ref: str = "", approximated: bool = False) -> dict:
        for name, value in (("subject", subject), ("event_type", event_type)):
            if not str(value or "").strip():
                raise AttributionError(f"{name} 不能为空")
        if event_type not in FUNNEL:
            raise AttributionError(f"event_type 只允许 {FUNNEL}")
        if event_type in ("activation", "paid") and approximated:
            raise AttributionError("activation/paid 不允许近似口径——价值事件必须真实发生")
        rec = {
            "kind": "event",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "subject": subject,
            "event_type": event_type,
            "channel": channel,
            "touch": touch,
            "source_ref": source_ref,
            "approximated": bool(approximated),
        }
        return self._append(rec)

    def add_count(self, *, metric: str, value: int, window: str,
                  source_ref: str, approximated: bool = True) -> dict:
        if not str(metric or "").strip() or not str(window or "").strip():
            raise AttributionError("metric/window 不能为空")
        if not str(source_ref or "").strip():
            raise AttributionError("计数必须带 source_ref（分母口径可核查）")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AttributionError("value 必须是非负整数")
        rec = {
            "kind": "count",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "metric": metric,
            "value": value,
            "window": window,
            "source_ref": source_ref,
            "approximated": bool(approximated),
        }
        return self._append(rec)

    def _append(self, rec: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    # ---- 读取 ----
    def records(self):
        if not self.path.is_file():
            return []
        out = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    out.append(json.loads(line))
        return out

    def funnel(self) -> dict:
        """按漏斗序统计去重主体数与相邻转化率；计数快照单列，不与事件混算。"""
        events = [r for r in self.records() if r.get("kind") == "event"]
        counts = [r for r in self.records() if r.get("kind") == "count"]
        by_stage = {stage: set() for stage in FUNNEL}
        approx_subjects = set()
        for rec in events:
            by_stage[rec["event_type"]].add(rec["subject"])
            if rec.get("approximated"):
                approx_subjects.add(rec["subject"])
        stages = []
        prev = None
        for stage in FUNNEL:
            n = len(by_stage[stage])
            conv = None
            if prev is not None and prev["subjects"] > 0:
                conv = round(n / prev["subjects"], 4)
            stages.append({"stage": stage, "subjects": n, "conversion_from_prev": conv})
            prev = stages[-1]
        return {
            "funnel": stages,
            "approximated_signup_subjects": sorted(
                s for s in by_stage["signup"] if s in approx_subjects
            ),
            "counts": [
                {k: rec.get(k) for k in ("ts", "metric", "value", "window", "source_ref", "approximated")}
                for rec in counts
            ],
            "note": "转化率是比值不是因果；近似口径主体单列，不计入真实 signup 结论",
        }

    def attribution(self) -> dict:
        """last_touch / first_touch 两种记账口径；明确声明非因果。"""
        events = [r for r in self.records() if r.get("kind") == "event"]
        converted = {}
        for rec in events:
            if rec["event_type"] not in ("signup", "activation", "paid"):
                continue
            converted.setdefault(rec["subject"], []).append(rec)
        rows = []
        for subject, recs in sorted(converted.items()):
            touches = [r for r in events if r["subject"] == subject and r["event_type"] == "signal"]
            rows.append({
                "subject": subject,
                "reached": recs[-1]["event_type"],
                "first_touch": (touches[0].get("channel") if touches else "") or "unknown",
                "last_touch": (touches[-1].get("channel") if touches else "") or "unknown",
            })
        return {
            "subjects": rows,
            "models": ["last_touch", "first_touch"],
            "note": "口径≠因果：两种模型只是记账角度，渠道效果比较需要实验（core/experiments）",
        }
