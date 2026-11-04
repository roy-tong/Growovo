#!/usr/bin/env python3
"""意图信号战术：把"四条渠道并行乱发"收敛成"一条渠道连续四周"。

研究结论（2026-11-03）：单渠道连续 4 周、每周 ≥20 条有效触达，才能判断这条渠道
通不通。本模块负责这四周的节奏、配额与判定，不负责找到具体的人（那是人的活）。

  第 1 周  居民期：只答问题，不提产品（攒信誉）
  第 2 周  触达：≥20 条有效触达
  第 3 周  跟进：不加新量，只把上一周的人跟完
  第 4 周  判定：通 / 不确定 / 不通，三选一，不留"再看看"

数据落盘：data/intent/<id>.json。只读写本地文件，不发送任何消息。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

WEEK_PLAN = {
    1: ("居民期", "只答问题，一条产品都不提，攒够 10 条以上未被删的评论"),
    2: ("触达", "≥20 条有效触达：答问题为主，最多 2 条带产品且必须披露"),
    3: ("跟进", "不加新量，把上周回过话的人跟完；第 3 天问'试了吗'"),
    4: ("复盘与下一轮", "看三个数：被删率、回应率、转场数，选择下一项学习动作"),
}

# 判定线。样本不足时不下结论——四周 20 条/周 = 80 条，先设 40 条为最低。
MIN_SENT = 40
DELETED_MAX = 0.15
RESPONSE_PASS = 0.10
RESPONSE_MAYBE = 0.03


def _today() -> str:
    return date.today().isoformat()


def _days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def phase_of(started: str, on: Optional[str] = None, weeks: int = 4) -> Dict[str, Any]:
    """第几周、该做什么。实验长度可配置，最后停在复盘与下一轮。"""
    on = on or _today()
    day = _days_between(started, on)
    if day < 0:
        raise ValueError("复盘日期不能早于开始日")
    week = min(day // 7 + 1, weeks)
    name, todo = WEEK_PLAN.get(week, ("复盘与下一轮", "选择下一项学习动作"))
    return {"week": week, "phase": name, "todo": todo, "day": day}


class IntentCampaign:
    def __init__(self, path):
        self.path = Path(path)

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            raise FileNotFoundError(f"没有这个战术：{self.path}")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")

    # ---------- 生命周期 ----------

    @classmethod
    def start(cls, path, campaign_id: str, platform: str = "reddit",
              subs: Optional[List[str]] = None, started: Optional[str] = None,
              owner: str = "", weeks: int = 4,
              weekly_target: Optional[int] = None) -> Dict[str, Any]:
        started = started or _today()
        if weeks < 1:
            raise ValueError("weeks 必须为正整数")
        if weekly_target is not None and weekly_target < 1:
            raise ValueError("weekly_target 必须为正整数或留空")
        data = {
            "id": campaign_id,
            "platform": platform,
            "subs": list(subs or []),
            "owner": owner,
            "started": started,
            "ends": (date.fromisoformat(started) + timedelta(days=weeks * 7 - 1)).isoformat(),
            "experiment": {"weeks": weeks, "weekly_target": weekly_target},
            "logs": [],
            "verdict": None,
        }
        cls(path).save(data)
        return data

    def log(self, sent: int = 0, deleted: int = 0, replied: int = 0, moved: int = 0,
            note: str = "", on: Optional[str] = None) -> Dict[str, Any]:
        if min(sent, deleted, replied, moved) < 0:
            raise ValueError("数字不能为负")
        if deleted > sent or replied > sent:
            raise ValueError("被删数或回应数不能大于触达数")
        on = on or _today()
        data = self.load()
        if on < data["started"]:
            raise ValueError("记录日期不能早于开始日")
        data["logs"].append({"on": on, "sent": sent, "deleted": deleted,
                             "replied": replied, "moved": moved, "note": note})
        self.save(data)
        return data

    # ---------- 读数 ----------

    def summary(self, on: Optional[str] = None) -> Dict[str, Any]:
        data = self.load()
        on = on or _today()
        sent = sum(x["sent"] for x in data["logs"])
        deleted = sum(x["deleted"] for x in data["logs"])
        replied = sum(x["replied"] for x in data["logs"])
        moved = sum(x["moved"] for x in data["logs"])

        by_week: Dict[int, Dict[str, int]] = {}
        for x in data["logs"]:
            w = min(_days_between(data["started"], x["on"]) // 7 + 1, 4)
            row = by_week.setdefault(w, {"sent": 0, "deleted": 0, "replied": 0, "moved": 0})
            row["sent"] += x["sent"]
            row["deleted"] += x["deleted"]
            row["replied"] += x["replied"]
            row["moved"] += x["moved"]

        experiment = data.get("experiment") or {}
        weekly_target = experiment.get("weekly_target")
        return {
            **phase_of(data["started"], on, weeks=experiment.get("weeks", 4)),
            "sent": sent, "deleted": deleted, "replied": replied, "moved": moved,
            "deleted_rate": round(deleted / sent, 3) if sent else 0.0,
            "response_rate": round(replied / sent, 3) if sent else 0.0,
            "by_week": by_week,
            "weekly_target": weekly_target,
            "weeks_behind_quota": ([w for w in (2, 3) if by_week.get(w, {}).get("sent", 0) < weekly_target]
                                   if weekly_target is not None else []),
        }

    def verdict(self, on: Optional[str] = None) -> Dict[str, Any]:
        """学习观察，不给渠道生死判决；样本不足和安全异常仍明确提示。"""
        s = self.summary(on)
        sent = s["sent"]
        if sent < MIN_SENT:
            return {"verdict": "样本不足", "reason":
                    f"累计只触达 {sent} 条，低于 {MIN_SENT} 条的最低观察样本量；"
                    "先补足观察，不要提前下结论或判断渠道优劣",
                    **{k: s[k] for k in ("sent", "deleted_rate", "response_rate", "moved", "week")}}
        if s["deleted_rate"] > DELETED_MAX:
            verdict = "安全异常"
            why = (f"被删率 {s['deleted_rate']:.0%} 超过 {DELETED_MAX:.0%}："
                   "先降频并检查平台规则与内容/执行方式，不自动判死渠道，也不加大力度")
        elif s["replied"] or s["moved"]:
            verdict = "观察到互动"
            why = (f"回应率 {s['response_rate']:.0%}，转一对一 {s['moved']} 人："
                   "记录为学习结果，下一步观察激活与真实使用，不自动扩量")
        else:
            verdict = "尚无有效互动"
            why = (f"回应率 {s['response_rate']:.0%}：先检查需求匹配、开口方式、名单与承接，"
                   "不自动换渠道")
        return {"verdict": verdict, "reason": why,
                **{k: s[k] for k in ("sent", "deleted_rate", "response_rate", "moved", "week")}}

    def as_followups(self) -> List[Dict[str, Any]]:
        """把回应过的人交给跟进引擎的对象格式（不含发送动作）。"""
        data = self.load()
        out = []
        for x in data["logs"]:
            if x["replied"]:
                out.append({"on": x["on"], "replied": x["replied"], "moved": x["moved"],
                            "platform": data["platform"]})
        return out
