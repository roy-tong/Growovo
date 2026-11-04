#!/usr/bin/env python3
"""跟进引擎：把"发完之后"变成可执行、可到期提醒、可判定的对象台账。

研究结论（2026-11-03）：卡点不是"不知道去哪发"，是"发布一次就没声音"。
有效动作 = 找对的人 + 有用的话 + 持续跟。本模块负责"持续跟"里的记账部分：

  对象台账    每个说过话的人一条记录（不是线索库，线索库是"还没说过话的人"）
  三次原则    首触 + 最多 3 次跟进（第 3 / 14 / 45 天），之后停
  到期提醒    next_due 到点由 `mkt next` / `mkt report` 顶出来，不靠人记
  实质回应    replied / asked / used 才算推进；"谢谢""好的"不算
  拒绝处理    记下原因，45 天后再来一次，不纠缠

数据落盘：data/followups.jsonl（一行一个对象）。只读写本地文件，不发送任何消息。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# 首触之后的跟进间隔（天）。第 3 天、第 14 天、第 45 天，之后停。
CADENCE = (3, 14, 45)
# 首触算一次动作，最多 3 次主动跟进 = 总共 4 次动作。
MAX_ACTIONS = 1 + len(CADENCE)

# 实质回应：只有这三种算推进。"no_reply" 不算；"declined" 是终止信号。
SUBSTANTIVE = ("replied", "asked", "used")
RESULTS = ("no_reply", "replied", "asked", "used", "declined")

STAGES = ("contacted", "replied", "asked", "used", "declined", "dropped", "closed")
OPEN_STAGES = ("contacted", "replied", "asked", "used", "declined")


def _today() -> str:
    return date.today().isoformat()


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _add_days(iso: str, days: int) -> str:
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


class Followups:
    def __init__(self, path):
        self.path = Path(path)

    # ---------- 存取 ----------

    def all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def _write(self, rows: List[Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def get(self, fu_id: str) -> Optional[Dict[str, Any]]:
        for r in self.all():
            if r["id"] == fu_id:
                return r
        return None

    def _require(self, fu_id: str) -> Dict[str, Any]:
        rec = self.get(fu_id)
        if rec is None:
            raise KeyError(f"找不到跟进对象 {fu_id!r}")
        return rec

    # ---------- 写入 ----------

    def add(self, platform: str, target: str, source: str = "", note: str = "",
            on: Optional[str] = None, channel: str = "", campaign: str = "") -> Dict[str, Any]:
        """登记一个刚接触过的对象。同平台同对象不允许重复登记。"""
        if not platform or not target:
            raise ValueError("platform 与 target 不能为空")
        rows = self.all()
        for r in rows:
            if r["platform"] == platform and r["target"] == target and r["stage"] in OPEN_STAGES:
                raise ValueError(f"已在跟进中：{r['id']}（{platform}/{target}）")
        on = on or _today()
        rec = {
            "id": f"fu-{len(rows) + 1:04d}",
            "platform": platform,
            "target": target,
            "source": source,
            "campaign": campaign,
            "stage": "contacted",
            "status": "open",
            "created": on,
            "next_due": _add_days(on, CADENCE[0]),
            "touches": [{
                "at": on, "channel": channel or platform, "campaign": campaign,
                "result": "no_reply", "note": note,
            }],
        }
        rows.append(rec)
        self._write(rows)
        return rec

    def touch(self, fu_id: str, result: str, note: str = "", channel: str = "",
              on: Optional[str] = None, campaign: str = "") -> Dict[str, Any]:
        """记一次跟进结果，并按三次原则算下一次到期日。"""
        if result not in RESULTS:
            raise ValueError(f"result 必须是 {RESULTS} 之一")
        on = on or _today()
        rows = self.all()
        rec = next((r for r in rows if r["id"] == fu_id), None)
        if rec is None:
            raise KeyError(f"找不到跟进对象 {fu_id!r}")
        rec["touches"].append({
            "at": on, "channel": channel or rec["platform"],
            "campaign": campaign or rec.get("campaign", ""),
            "result": result, "note": note,
        })

        if result in SUBSTANTIVE:
            # 关系是活的：3 天后接一句，不设上限。
            rec["stage"] = result
            rec["status"] = "open"
            rec["next_due"] = _add_days(on, 3)
        elif result == "declined":
            # 明确拒绝：记下，45 天后再来一次，中间不打扰。
            rec["stage"] = "declined"
            rec["status"] = "open"
            rec["next_due"] = _add_days(on, CADENCE[-1])
        else:
            actions = len(rec["touches"])
            if actions >= MAX_ACTIONS:
                rec["stage"] = "dropped"
                rec["status"] = "closed"
                rec["next_due"] = None
                rec["closed_reason"] = "三次跟进无实质回应，判定无效"
            else:
                rec["stage"] = "contacted"
                rec["status"] = "open"
                rec["next_due"] = _add_days(on, CADENCE[actions - 1])

        self._write(rows)
        return rec

    def close(self, fu_id: str, reason: str = "") -> Dict[str, Any]:
        rows = self.all()
        rec = next((r for r in rows if r["id"] == fu_id), None)
        if rec is None:
            raise KeyError(f"找不到跟进对象 {fu_id!r}")
        rec["stage"] = "closed"
        rec["status"] = "closed"
        rec["next_due"] = None
        rec["closed_reason"] = reason or "人工关闭"
        self._write(rows)
        return rec

    # ---------- 读取 ----------

    def due(self, on: Optional[str] = None) -> List[Dict[str, Any]]:
        """今天该跟的对象：到期日 <= 今天，且仍在进行中。"""
        on = on or _today()
        out = [r for r in self.all()
               if r.get("status") == "open" and r.get("next_due") and r["next_due"] <= on]
        out.sort(key=lambda r: (r["next_due"], r["id"]))
        return out

    def overdue(self, on: Optional[str] = None) -> List[Dict[str, Any]]:
        """已经跟丢的：过期超过 7 天还没动。"""
        on = on or _today()
        limit = _add_days(on, -7)
        return [r for r in self.due(on) if r["next_due"] < limit]

    def stats(self, on: Optional[str] = None) -> Dict[str, Any]:
        on = on or _today()
        rows = self.all()
        by_stage: Dict[str, int] = {s: 0 for s in STAGES}
        for r in rows:
            by_stage[r["stage"]] = by_stage.get(r["stage"], 0) + 1
        # "45 天后还在说话"：从建立关系到最后一次实质回应，跨度 ≥45 天。
        # 只看"最后一次 >= 今天-45 天"会把跟丢的人误算成还在聊。
        touched_45 = []
        for r in rows:
            touches = r.get("touches") or []
            if not touches or touches[-1]["result"] not in SUBSTANTIVE:
                continue
            if _days(r["created"], touches[-1]["at"]) >= 45:
                touched_45.append(r)
        return {
            "total": len(rows),
            "open": sum(1 for r in rows if r.get("status") == "open"),
            "due": len(self.due(on)),
            "overdue": len(self.overdue(on)),
            "by_stage": by_stage,
            "substantive": sum(1 for r in rows
                               if any(t["result"] in SUBSTANTIVE for t in r.get("touches", []))),
            "conversations_after_45d": len(touched_45),
        }

    def platform_breakdown(self) -> List[Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for r in self.all():
            row = out.setdefault(r["platform"], {"platform": r["platform"], "total": 0,
                                                 "substantive": 0, "open": 0})
            row["total"] += 1
            row["open"] += 1 if r.get("status") == "open" else 0
            if any(t["result"] in SUBSTANTIVE for t in r.get("touches", [])):
                row["substantive"] += 1
        return sorted(out.values(), key=lambda x: (-x["substantive"], -x["total"], x["platform"]))


def describe(rec: Dict[str, Any]) -> str:
    """一行摘要，给 CLI 与 mkt next 用。"""
    last = rec["touches"][-1] if rec.get("touches") else {}
    stage = rec["stage"]
    mark = {"contacted": "·", "replied": "回", "asked": "问", "used": "用",
            "declined": "拒", "dropped": "弃", "closed": "关"}.get(stage, "?")
    due = rec.get("next_due") or "—"
    return (f"{mark} {rec['id']} {rec['platform']}/{rec['target']} "
            f"[{stage}] 到期 {due} 上次 {last.get('at', '—')} {last.get('result', '')}")
