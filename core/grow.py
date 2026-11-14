#!/usr/bin/env python3
"""增长闭环引擎：用户给产品事实和目标，引擎把执行、读数、调参连成日循环。

定位（结果契约 v1，2026-11-14）：交付业务结果——发布量（账本 committed 可
核验）、日均曝光与新增粉丝（平台后台读数，截图留证）——而不是文档或策略。

分工边界（写死在这里，不许漂移）：
  引擎做：跑暂存队列（常驻授权 routine 路径，暂停/预算/抑制/回读门全保留）、
          周窗口出结果卡、按透明规则调下一周的量；
  运营 agent / 人做：暂存内容（队列文件、战术候选），质量门在前（voice 与
          战术闸）；引擎不生成文案。

自治阶梯：L0 逐条人审 → L1 常驻授权执行（routine policy）→ L2 定时闭环
（本模块 tick + cron）→ L3 自动调参（本模块已含安全子集）。付款、合同、
折扣、危机响应永远不在自动路径里（execution_policy 封闭动作集强制）。

数据落盘：data/grow/<id>.json。队列发现规则：data/queues/<id>-*.json 且
campaign 以 <id> 开头，每日至多各跑一次（重复条目由账本幂等闸拒绝，安全）。
"""
from __future__ import annotations

import json
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import result_card
from .safety import is_paused

SCHEMA = "growovo.grow-run/1"

# 每个渠道闭环需要的常驻授权动作（封闭集合，与 execution_policy.ROUTINE_ACTIONS 对齐）
CHANNEL_ACTIONS: Dict[str, List[str]] = {
    "x": ["x:publish", "x:reply", "x:message"],
    "email": ["email:message"],
}

# 安全调参规则（透明、可审计；策略级建议只记录不执行）
SCALE_UP_RATIO = 1.25
SCALE_UP_CAP_RATIO = 2.0
SCALE_UP_MIN_HIT = 0.9   # 本周完成 ≥90% 计划才加量
ATTENTION_BELOW = 0.5    # 完成 <50% 只提示，不降量（缺数先查读数与暂存）


class GrowError(RuntimeError):
    pass


def _today() -> str:
    return date.today().isoformat()


def preflight_policy(data_dir, channels: List[str]) -> Tuple[List[str], str]:
    """检查常驻授权是否覆盖闭环需要的全部动作；返回（缺哪些，启用命令）。"""
    from .execution_policy import allows
    unknown = [c for c in channels if c not in CHANNEL_ACTIONS]
    if unknown:
        raise GrowError(f"闭环暂不支持渠道 {unknown}；当前支持：{sorted(CHANNEL_ACTIONS)}")
    missing: List[str] = []
    wanted: List[str] = []
    for ch in channels:
        for act in CHANNEL_ACTIONS[ch]:
            wanted.append(act)
            channel, action = act.split(":", 1)
            if not allows(Path(data_dir), channel, action, "", ""):
                missing.append(act)
    command = ("mkt policy enable --by \"$USER\" --actions "
               + ",".join(wanted) + " --days 30")
    return missing, command


def discover_queues(data_dir, run_id: str) -> List[Path]:
    """发现属于本 run 的暂存队列：queues/<run_id>-*.json。"""
    directory = Path(data_dir) / "queues"
    if not directory.is_dir():
        return []
    return sorted(directory.glob(f"{run_id}-*.json"))


class GrowRun:
    def __init__(self, path):
        self.path = Path(path)

    # ---------- 存取 ----------

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            raise FileNotFoundError(f"没有这个闭环执行记录：{self.path}")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")

    # ---------- 生命周期 ----------

    @classmethod
    def start(cls, path, run_id: str, facts_path: str, goal_id: str,
              channels: List[str], *, target: Optional[int] = None,
              window_days: Optional[int] = None, started: Optional[str] = None,
              actions_per_week: int = 20, go: bool = False,
              data_dir: Optional[Path] = None, owner: str = "") -> Dict[str, Any]:
        """开一个闭环。预检常驻授权；缺授权直接拒，并给出精确的启用命令。"""
        from .goals import get as goal_get
        from .goals import select as goal_select
        facts_file = Path(facts_path)
        if not facts_file.is_file():
            raise GrowError(f"找不到产品事实包：{facts_file}")
        facts = json.loads(facts_file.read_text(encoding="utf-8"))
        missing, command = preflight_policy(data_dir or path.parent.parent, channels)
        if missing:
            raise GrowError(
                f"常驻授权未覆盖 {missing}；先人工执行：{command}（授权是人工动作，"
                "引擎与 agent 不能自我授权）")
        # 目标契约的平台 = 执行渠道里该目标认可的子集（如 followers 只认 x）；
        # email 等渠道是承接层，留在闭环的 channels 里，不硬塞给目标选择。
        spec = goal_get(goal_id)
        affinity = set((spec.get("affinity") or {}).keys())
        goal_platforms = [c for c in channels if c in affinity]
        if not goal_platforms:
            raise GrowError(
                f"目标 {goal_id} 与渠道 {channels} 都对不上；可选：{sorted(affinity)}")
        try:
            selection = goal_select(facts, goal_id, platforms=goal_platforms,
                                    target=target, window_days=window_days)
        except Exception as exc:  # goals 的 GoalError 原样透出，附上下文
            raise GrowError(f"目标契约生成失败：{exc}") from exc
        started = started or _today()
        data = {
            "schema": SCHEMA,
            "id": run_id,
            "owner": owner,
            "started": started,
            "status": "active",
            "go": bool(go),
            "facts_ref": str(facts_file),
            "facts_sha256": hashlib.sha256(
                facts_file.read_bytes()).hexdigest()[:16],
            "goal": {k: selection.get(k) for k in
                     ("goal_id", "family", "metric", "target", "target_kind",
                      "target_text", "window_days", "baseline")},
            "channels": list(channels),
            "policy": {"required_actions": [a for c in channels
                                            for a in CHANNEL_ACTIONS[c]],
                       "checked_at": datetime.now().isoformat(timespec="seconds")},
            "plan": {"actions_per_week": int(actions_per_week),
                     "initial_actions_per_week": int(actions_per_week)},
            "last_queue_run": {},       # queue path -> 最后执行日
            "result_cards": [],         # {week, start, end, card}
            "adjustments": [],          # {on, kind: auto|attention|proposal, detail}
            "history": [],
        }
        cls(path).save(data)
        return data

    def arm(self) -> Dict[str, Any]:
        """把 go 从 false 翻成 true（实跑开关，人工确认动作）。"""
        data = self.load()
        if data.get("go"):
            return data
        data["go"] = True
        data["history"].append({"at": _today(), "kind": "arm",
                                "note": "实跑已开闸；暂停闸与全部执行门照旧"})
        self.save(data)
        return data

    def resume(self, *, note: str, by: str = "") -> Dict[str, Any]:
        """人工裁决后恢复循环。needs_attention 是安全停车，不是终态；
        恢复必须留一句人写的处理说明，历史里可查谁在什么时候放行。"""
        data = self.load()
        if data.get("status") != "needs_attention":
            return data
        note = (note or "").strip()
        if not note:
            raise GrowError("恢复需要 --note 写清处理了什么（谁修的、哪条队列、结论）")
        data["status"] = "active"
        data["history"].append({"at": _today(), "kind": "resume",
                                "by": by, "note": note[:300]})
        self.save(data)
        return data

    # ---------- 日循环 ----------

    def tick(self, data_dir, *, on: Optional[str] = None,
             runner: Callable[..., Any] = None,
             log: Callable[..., Any] = print) -> Dict[str, Any]:
        """一步日循环：跑到期队列（每队列每日一次）→ 周窗口到期出结果卡并调参。

        cron/launchd 每小时或每天调一次；幂等（按日与按周去重）。
        runner 可注入（测试用）；默认 queue_runner.run_queue。
        """
        if runner is None:
            from .queue_runner import run_queue as runner  # noqa: E731 延迟导入便于注入
        from .queue_runner import rejection_kind  # 拒绝分层与执行器共用一套口径
        on = on or _today()
        data = self.load()
        if data.get("status") != "active":
            return {"run": data["id"], "skipped": data.get("status"), "ran": []}
        if is_paused(data_dir):
            data["history"].append({"at": on, "kind": "skip", "reason": "paused"})
            self.save(data)
            return {"run": data["id"], "skipped": "paused", "ran": []}

        ran: List[Dict[str, Any]] = []
        needs_attention = False
        for queue_path in discover_queues(data_dir, data["id"]):
            if data["last_queue_run"].get(str(queue_path)) == on:
                continue  # 今天跑过了
            records: List[Dict[str, Any]] = []
            err = None
            try:
                records = list(runner(
                    queue_path, data_dir=data_dir, go=bool(data.get("go")),
                    routine=True, account="", log=log,
                    continue_on_duplicate=True) or [])
            except Exception as exc:  # 队列级失败不吞：记下并停循环
                err = f"{type(exc).__name__}: {exc}"[:300]
            results = [str(r.get("result") or "") for r in records if isinstance(r, dict)]
            dup = [r for r in records if isinstance(r, dict)
                   and rejection_kind(r) == "duplicate"]
            suppressed = [r for r in records if isinstance(r, dict)
                          and (r.get("result") == "suppressed"
                               or rejection_kind(r) == "suppression")]
            blocking = [r for r in records if isinstance(r, dict)
                        and (r.get("result") in ("failed", "indeterminate")
                             or (r.get("result") == "rejected"
                                 and rejection_kind(r) == "other"))]
            entry = {"queue": str(queue_path), "records": len(records),
                     "results": results, "go": bool(data.get("go")),
                     "duplicates": len(dup), "suppressed": len(suppressed)}
            if err:
                entry["error"] = err
                needs_attention = True
            elif blocking:
                detail = str(blocking[0].get("detail") or "")[:120]
                entry["error"] = ("存在未知/失败/被拒结果，需要人工裁决"
                                  + (f"：{detail}" if detail else ""))
                needs_attention = True
            elif dup:
                entry["note"] = (f"{len(dup)} 条此前已发过，未重复发送"
                                 "（不计入本周发布量）")
            if suppressed:
                entry["note"] = ((entry.get("note", "") + "；" if entry.get("note") else "")
                                 + f"{len(suppressed)} 条在抑制名单，已跳过")
            ran.append(entry)
            data["last_queue_run"][str(queue_path)] = on
            if needs_attention:
                break

        card_summary = None
        windows = result_card.week_bounds(data["started"], on)
        # 卡片存的是 week_start/week_end；end 兼容早期只存 end 的卡。
        closed_ends = [str(c.get("end") or c.get("week_end") or "")
                       for c in data["result_cards"]]
        last_end = max((d for d in closed_ends if d), default=None)
        due_window = None
        for week, start, end in windows:
            if end < on and end != last_end and (last_end is None or end > last_end):
                due_window = (week, start, end)
                break
        if due_window:
            week, start, end = due_window
            card = result_card.build(data_dir, data, start, end, on=on)
            committed = card["published"]["total"]
            planned = card["planned_actions"] or 0
            if planned and committed >= SCALE_UP_MIN_HIT * planned:
                old = data["plan"]["actions_per_week"]
                # 上限锚定开始时的计划量，不跟着当前值复利上浮
                initial = data["plan"].get("initial_actions_per_week") or old
                cap = round(SCALE_UP_CAP_RATIO * initial)
                new = min(round(old * SCALE_UP_RATIO), cap)
                if new != old:
                    data["plan"]["actions_per_week"] = new
                    data["adjustments"].append({
                        "on": end, "kind": "auto",
                        "param": "actions_per_week", "from": old, "to": new,
                        "detail": f"第 {week} 周完成 {committed}/{planned}（≥90%），"
                                  f"下周计划量 {old} → {new}（上限 {cap}）"})
            elif planned and committed < ATTENTION_BELOW * planned:
                if not data.get("go") and committed == 0:
                    # 没开闸的一周不该被记成"没完成"：这是配置状态，不是执行失败
                    data["adjustments"].append({
                        "on": end, "kind": "dry-run",
                        "detail": f"第 {week} 周未开闸（dry-run），不计完成率；"
                                  "确认后 mkt grow arm 再跑一周"})
                else:
                    data["adjustments"].append({
                        "on": end, "kind": "attention",
                        "detail": f"第 {week} 周只完成 {committed}/{planned}：先查读数通道"
                                  "与暂存队列，不自动降量"})
            card_summary = {"week": week, "start": start, "end": end,
                            "published": committed,
                            "avg_daily_impressions": card["impressions"]["avg_daily"],
                            "new_followers": card["followers"]["new"]}
            # 存整张卡：历史卡不可变（报告重放当周口径，不随调参改写）
            card["week"] = week
            data["result_cards"].append(card)

        if needs_attention:
            data["status"] = "needs_attention"
            data["history"].append({"at": on, "kind": "tick",
                                    "note": "有队列需要人工处理，循环暂停"})
        else:
            data["history"].append({"at": on, "kind": "tick",
                                    "ran": len(ran),
                                    "card": (f"week {card_summary['week']}"
                                             if card_summary else "")})
        self.save(data)
        return {"run": data["id"], "on": on, "ran": ran, "card": card_summary,
                "status": data["status"]}

    # ---------- 读数 ----------

    def goal_progress(self, data_dir) -> Dict[str, Any]:
        """目标进度：目前只自动读 followers（与 awareness.followers 对应），
        其他指标如实标注「人工核对」。"""
        from .attribution import OutcomeLedger
        data = self.load()
        metric = (data.get("goal") or {}).get("metric")
        if metric != "followers":
            return {"metric": metric, "current": None,
                    "note": "该指标暂无自动读数通道，人工核对后入账"}
        by_day = result_card._count_by_day(
            OutcomeLedger(Path(data_dir)).records(), "x_followers")
        current = list(by_day.values())[-1] if by_day else None
        goal = data.get("goal") or {}

        def _num(v):
            return int(v) if isinstance(v, float) and v.is_integer() else v

        baseline = _num(goal.get("baseline"))
        target = _num(goal.get("target"))
        return {"metric": metric, "current": current,
                "baseline": baseline, "target": target,
                "progress": (round((current - baseline) / (target - baseline) * 100)
                             if None not in (current, baseline, target)
                             and isinstance(current, int)
                             and isinstance(baseline, (int, float))
                             and isinstance(target, (int, float))
                             and target > baseline else None),
                "note": "" if current is not None else "还没有粉丝读数（先跑 mkt x stats）"}

    def status(self, data_dir, on: Optional[str] = None) -> Dict[str, Any]:
        on = on or _today()
        data = self.load()
        pending = [str(p) for p in discover_queues(data_dir, data["id"])
                   if data["last_queue_run"].get(str(p)) != on]
        return {
            "run": data["id"], "status": data["status"], "go": data.get("go"),
            "started": data["started"],
            "week": (result_card.week_bounds(data["started"], on) or [(0, "", "")])[-1][0],
            "goal": self.goal_progress(data_dir),
            "plan_actions_per_week": data["plan"]["actions_per_week"],
            "pending_queues_today": pending,
            "result_cards": data["result_cards"],
            "adjustments": len(data["adjustments"]),
        }

    def report(self, data_dir, on: Optional[str] = None) -> str:
        """最新一个完整周的结果单（markdown）。历史卡存档不可变，直接重放。"""
        on = on or _today()
        data = self.load()
        cards = data["result_cards"]
        if not cards:
            return (f"{data['id']}：还没有完成整周（开始于 {data['started']}，"
                    "周窗口走完自动出第一张结果单）")
        latest = cards[-1]
        if latest.get("schema"):       # v0.14 起存整张卡
            return result_card.render(latest)
        card = result_card.build(data_dir, data,
                                 str(latest.get("start") or latest.get("week_start") or ""),
                                 str(latest.get("end") or latest.get("week_end") or ""),
                                 on=on)
        return result_card.render(card)
