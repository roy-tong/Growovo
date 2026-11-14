#!/usr/bin/env python3
"""战术执行记录：把一条战术的纪律变成代码里的硬约束与告警。

与 core/intent.py 同一范式（JSON 落盘、按周分相、结论人判），差别在：
intent 只记四个总数，这里多管三件事——

  1. 发出前检查（add_draft / check_candidate）：回复禁外链、占位符与长度
     复用 gates 的既有检查、同一条 run 内不许重复话术；
  2. 节奏检查（log）：日上限、每小时上限、每目标日上限超过配额记违规；
     回帖窗口超时、同一公式刷屏记告警；
  3. 交接既有发送路径（as_queue_items → mkt queue lint → plan approve →
     queue run）：本模块自己不发送任何东西；账本回读（sync_from_ledger）
     用来核对实际发出数。

数据落盘：data/tactics/<run_id>.json。启动时把战术配额快照进文件，
之后注册表怎么改都不影响这条 run 的历史判定。
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import tactics as tactics_reg
from .gates import GateError, check_content, check_target

# 默认四周节奏；战术注册表暂不带周模板，先统一这四相。
WEEK_PLAN = {
    1: ("准备周", "建目标清单、过账号门槛；只观察记录，不行动"),
    2: ("执行周", "按配额执行，逐日记录动作与结果"),
    3: ("跟进周", "不加新量，把回应过的人跟完"),
    4: ("判定周", "看三个数：被删率、回应率、违规数；人判结论，不自动判决"),
}

# 回复里永不放外链：链接是评论区第一垃圾信号，且算法天然压低链接权重。
URL_RE = re.compile(
    r"https?://|www\.|[a-z0-9-]+\.(?:com|cn|net|org|io|me|co|ai|cc|xyz)"
    r"(?=[/\\\s，。,.:：;；)）]?|\b)",
    re.IGNORECASE,
)


class TacticRuleError(ValueError):
    """战术纪律硬违规（超日上限、重复话术等）；消息写明拒绝原因。"""


def _today() -> str:
    return date.today().isoformat()


def _days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _norm(text: str) -> str:
    """重复检测用的归一化：去空白、转小写。"""
    return re.sub(r"\s+", "", str(text or "")).lower()


def check_body(channel: str, action: str, text: str, label: str = "") -> None:
    """正文三关：既有门禁（空/长度/禁字符/占位符）+ 战术禁链规则。"""
    from .queue import max_len_for  # 局部导入避免环：queue 依赖 gates，不依赖本模块
    check_content(text, max_len=max_len_for(channel, action), label=label)
    if URL_RE.search(text or ""):
        raise GateError(f"{label}战术回复禁放外链（评论区第一垃圾信号）：去掉链接再试")


def check_candidate(tactic: Dict[str, Any], *, target: str, body: str,
                    formula: str = "", post_age_min: Optional[int] = None,
                    ) -> List[str]:
    """发出前检查一条候选回复。硬违规抛异常（拒绝入账），软违规返回告警列表。

    tactic 是 tactics.spec() 的返回值（含 channel/quota/formulas/queue_action）。
    """
    channel = tactic["channel"]
    action = tactic.get("queue_action") or "reply"
    quota = tactic.get("quota") or {}
    warns: List[str] = []

    check_target(channel, target)          # 目标格式（x: @handle / tweet:url）
    check_body(channel, action, body, label=f"[{target}] ")
    if tactic.get("formulas") and formula and formula not in tactic["formulas"]:
        raise TacticRuleError(
            f"公式 {formula!r} 不在战术公式表内：{tactic['formulas']}")
    window = quota.get("reply_within_minutes")
    if post_age_min is not None and window is not None and post_age_min > window:
        warns.append(f"回帖超窗：帖子已发布 {post_age_min} 分钟（配额窗口 {window} 分钟），"
                     "前排位置大概率没了")
    return warns


class TacticRun:
    def __init__(self, path):
        self.path = Path(path)

    # ---------- 存取 ----------

    def load(self) -> Dict[str, Any]:
        if not self.path.exists():
            raise FileNotFoundError(f"没有这个战术执行记录：{self.path}")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")

    # ---------- 生命周期 ----------

    @classmethod
    def start(cls, path, run_id: str, tactic_id: str, *, account: str = "",
              owner: str = "", started: Optional[str] = None, weeks: int = 4,
              target_list: str = "",
              ) -> Dict[str, Any]:
        spec = tactics_reg.spec(tactic_id)
        if spec is None:
            known = "、".join(sorted(tactics_reg.TACTICS)) or "（无）"
            raise ValueError(f"未知战术 {tactic_id!r}；可用：{known}")
        if weeks < 1:
            raise ValueError("weeks 必须为正整数")
        started = started or _today()
        # 快照：只取执行需要的字段，注册表后续演进不改写本 run 的判定口径。
        snap = {k: spec.get(k) for k in
                ("id", "name", "channel", "queue_action", "quota", "formulas", "risk")}
        data = {
            "id": run_id,
            "tactic": snap,
            "account": account,
            "owner": owner,
            "started": started,
            "ends": (date.fromisoformat(started) + timedelta(days=weeks * 7 - 1)).isoformat(),
            "experiment": {"weeks": weeks},
            "target_list": target_list,
            "campaign": None,
            "drafts": [],
            "logs": [],
            "metric_logs": [],
            "violations": [],
            "verdict": None,
        }
        cls(path).save(data)
        return data

    # ---------- 内部：当日节奏核算 ----------

    @staticmethod
    def _day_rows(data: Dict[str, Any], on: str, key: str) -> List[Dict[str, Any]]:
        rows = []
        for entry in data.get(key) or []:
            if entry.get("on") == on:
                rows.extend(entry.get("items") or [])
        return rows

    def _check_day_caps(self, data: Dict[str, Any], on: str,
                        new_items: List[Dict[str, Any]]) -> List[str]:
        """日/小时/单目标上限与公式轮换核算。硬超限抛错，软违规记告警。"""
        quota = data["tactic"].get("quota") or {}
        warns: List[str] = []
        existing = self._day_rows(data, on, "logs") + self._day_rows(data, on, "drafts")
        day_total = len(existing) + len(new_items)
        cap = quota.get("daily_cap")
        if cap is not None and day_total > cap:
            raise TacticRuleError(
                f"当日第 {day_total} 条超过日上限 {cap}（{on}）：宁可少回，不碰节奏红线")
        per_target = quota.get("per_target_daily_max")
        if per_target is not None:
            counts: Dict[str, int] = {}
            for it in existing + new_items:
                t = str(it.get("target") or "")
                counts[t] = counts.get(t, 0) + 1
                if counts[t] > per_target:
                    raise TacticRuleError(
                        f"目标 {t} 当日第 {counts[t]} 条超过每目标上限 {per_target}"
                        "：同一账号刷屏会被屏蔽")
        hour_cap = quota.get("per_hour_max")
        if hour_cap is not None:
            hours: Dict[int, int] = {}
            for it in existing + new_items:
                h = it.get("hour")
                if h is None:
                    continue
                hours[h] = hours.get(h, 0) + 1
            for h, n in sorted(hours.items()):
                if n > hour_cap:
                    raise TacticRuleError(
                        f"{on} {h} 点档共 {n} 条超过每小时上限 {hour_cap}"
                        "：短时间高密度回复是垃圾行为特征")
        formula_cap = quota.get("same_formula_max_streak")
        if formula_cap is not None:
            fcounts: Dict[str, int] = {}
            for it in existing + new_items:
                f = it.get("formula")
                if f:
                    fcounts[f] = fcounts.get(f, 0) + 1
            hot = max(fcounts, key=fcounts.get) if fcounts else ""
            if hot and fcounts[hot] > formula_cap:
                warns.append(f"公式 {hot!r} 当日已用 {fcounts[hot]} 次（上限 {formula_cap}），"
                             "换一种内容公式，避免被判模板回复")
        return warns

    def _seen_bodies(self, data: Dict[str, Any]) -> set:
        seen = set()
        for key in ("logs", "drafts"):
            for entry in data.get(key) or []:
                for it in entry.get("items") or []:
                    seen.add(_norm(it.get("body")))
        return seen

    # ---------- 候选与队列交接 ----------

    def add_draft(self, target: str, body: str, *, formula: str = "",
                  post_age_min: Optional[int] = None, hour: Optional[int] = None,
                  on: Optional[str] = None) -> Dict[str, Any]:
        """加一条候选回复（发出前）。过全部硬检查，软告警记入 violations。"""
        on = on or _today()
        data = self.load()
        if on < data["started"]:
            raise ValueError("记录日期不能早于开始日")
        warns = check_candidate(data["tactic"], target=target, body=body,
                                formula=formula, post_age_min=post_age_min)
        norm = _norm(body)
        if norm in self._seen_bodies(data):
            raise TacticRuleError("与本次执行记录里已有的回复几乎相同：每条都要按帖子定制")
        item = {"target": target, "body": body, "formula": formula or "",
                "post_age_min": post_age_min, "hour": hour, "queued": False}
        warns += self._check_day_caps(data, on, [item])
        for w in warns:
            data["violations"].append({"on": on, "kind": "warn", "detail": w})
        entry = next((e for e in data["drafts"] if e.get("on") == on), None)
        if entry is None:
            entry = {"on": on, "items": []}
            data["drafts"].append(entry)
        entry["items"].append(item)
        self.save(data)
        return data

    def as_queue_items(self, campaign: Optional[str] = None) -> Dict[str, Any]:
        """把未导出的候选导出成既有队列 schema，交给 mkt queue lint 与审批流程。

        只导出、不发送；导出过的候选标记 queued=True 防止重复导出。
        """
        data = self.load()
        action = data["tactic"].get("queue_action")
        if not action:
            raise TacticRuleError(
                f"战术 {data['tactic'].get('id')} 没有可执行的队列动作"
                "（渠道无发送适配器），按手册人工执行后用 log 记账")
        campaign = campaign or data["id"]
        items = []
        for entry in data.get("drafts") or []:
            for it in entry.get("items") or []:
                if it.get("queued"):
                    continue
                items.append({"id": f"{campaign}-{len(items) + 1:03d}",
                              "target": it["target"], "body": it["body"]})
                it["queued"] = True
        if not items:
            raise TacticRuleError("没有待导出的候选（全部已导出，或还没 add_draft）")
        data["campaign"] = campaign
        self.save(data)
        return {"campaign": campaign, "channel": data["tactic"]["channel"],
                "action": action, "items": items}

    # ---------- 记账 ----------

    def log(self, sent: int = 0, deleted: int = 0, replied: int = 0, moved: int = 0,
            note: str = "", on: Optional[str] = None,
            items: Optional[List[Dict[str, Any]]] = None,
            metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """记录当日实际发生数。items 是逐条明细（人工执行的也补录进来）。

        实际已发生的超限不拒收（事实要入账），但记违规告警，判定时汇总。
        """
        if min(sent, deleted, replied, moved) < 0:
            raise ValueError("数字不能为负")
        if deleted > sent or replied > sent:
            raise ValueError("被删数或回应数不能大于发出数")
        on = on or _today()
        data = self.load()
        if on < data["started"]:
            raise ValueError("记录日期不能早于开始日")
        items = [dict(x) for x in (items or [])]
        if items and sent == 0:
            sent = len(items)
        if sent and not items:
            # 只报了总数：占位明细，保住 by_day/by_week 核算
            items = [{"target": "", "body": "", "formula": "", "hour": None}] * sent
        warns: List[str] = []
        seen = self._seen_bodies(data)
        tactic = data["tactic"]
        for it in items:
            body = it.get("body") or ""
            if body:
                check_body(tactic["channel"], tactic.get("queue_action") or "reply",
                           body, label=f"[{it.get('target') or '?'}] ")
                if _norm(body) in seen:
                    warns.append(f"重复话术：{str(body)[:30]}…")
                seen.add(_norm(body))
        try:
            warns += self._check_day_caps(data, on, items)
        except TacticRuleError as exc:
            # 已发生的事实不拒收，但升级为违规记录，判定时必须看见
            warns.append(f"超限（已发生，须复盘）：{exc}")
        for w in warns:
            data["violations"].append({"on": on, "kind": "warn", "detail": w})
        data["logs"].append({"on": on, "sent": sent, "deleted": deleted,
                             "replied": replied, "moved": moved,
                             "note": note, "items": items})
        if metrics:
            data["metric_logs"].append({"on": on, **metrics})
        self.save(data)
        return data

    def sync_from_ledger(self, data_dir) -> Dict[str, Any]:
        """回读账本核对实际发出数（committed 才算发出）。只核对、不改账。"""
        from .ledger import Ledger
        ledger = Ledger(Path(data_dir) / "ledger.jsonl")
        campaign = self.load().get("campaign") or ""
        if not campaign:
            raise TacticRuleError("还没有关联 campaign（先 as_queue_items 导出队列）")
        rows = [e for e in ledger.results(campaign=campaign)
                if e.get("result") == "committed"]
        by_date: Dict[str, int] = {}
        for e in rows:
            d = str(e.get("ts") or "")[:10] or _today()
            by_date[d] = by_date.get(d, 0) + 1
        summary = {"on": _today(), "campaign": campaign,
                   "committed_total": len(rows), "by_date": by_date}
        data = self.load()
        data["ledger_sync"] = summary
        self.save(data)
        return summary

    # ---------- 读数 ----------

    def phase(self, on: Optional[str] = None) -> Dict[str, Any]:
        data = self.load()
        on = on or _today()
        weeks = data["experiment"].get("weeks", 4)
        day = _days_between(data["started"], on)
        if day < 0:
            raise ValueError("查看日期不能早于开始日")
        week = min(day // 7 + 1, weeks)
        name, todo = WEEK_PLAN.get(week, WEEK_PLAN[4])
        return {"week": week, "phase": name, "todo": todo, "day": day}

    def summary(self, on: Optional[str] = None) -> Dict[str, Any]:
        data = self.load()
        on = on or _today()
        sent = deleted = replied = moved = 0
        by_week: Dict[int, Dict[str, int]] = {}
        for x in data["logs"]:
            sent += x["sent"]; deleted += x["deleted"]
            replied += x["replied"]; moved += x["moved"]
            w = min(_days_between(data["started"], x["on"]) // 7 + 1,
                    data["experiment"].get("weeks", 4))
            row = by_week.setdefault(w, {"sent": 0, "deleted": 0, "replied": 0, "moved": 0})
            row["sent"] += x["sent"]; row["deleted"] += x["deleted"]
            row["replied"] += x["replied"]; row["moved"] += x["moved"]
        drafts_pending = sum(1 for e in data.get("drafts") or []
                             for it in e.get("items") or [] if not it.get("queued"))
        summary = {
            **self.phase(on),
            "tactic": data["tactic"]["id"],
            "sent": sent, "deleted": deleted, "replied": replied, "moved": moved,
            "deleted_rate": round(deleted / sent, 3) if sent else 0.0,
            "response_rate": round(replied / sent, 3) if sent else 0.0,
            "by_week": by_week,
            "drafts_pending": drafts_pending,
            "violations": len(data.get("violations") or []),
            "last_metrics": (data.get("metric_logs") or [None])[-1],
            "ledger_sync": data.get("ledger_sync"),
        }
        # 关联目标清单（koc-first 类战术）：engaged/amplified 直接进 summary
        if data.get("target_list"):
            from .targeting import TargetList
            tpath = self.path.parent.parent / "targets" / f"{data['target_list']}.json"
            if tpath.exists():
                summary["targets"] = TargetList(tpath).summary(on)
        return summary

    def verdict(self, on: Optional[str] = None) -> Dict[str, Any]:
        """学习观察，不给渠道生死判决；样本不足、安全异常、纪律偏离明确提示。"""
        s = self.summary(on)
        quota = self.load()["tactic"].get("quota") or {}
        min_sample = quota.get("min_sample", 40)
        deleted_max = quota.get("deleted_max", 0.15)
        resp_pass = quota.get("response_pass", 0.10)
        resp_maybe = quota.get("response_maybe", 0.03)
        base = {k: s[k] for k in
                ("sent", "deleted_rate", "response_rate", "moved", "week",
                 "violations", "last_metrics")}
        if s.get("targets"):
            t = s["targets"]
            base["koc_engaged_last_7d"] = t["koc_engaged_last_7d"]
            base["koc_amplified_total"] = t["koc_amplified_total"]
        if s["sent"] < min_sample:
            return {"verdict": "样本不足", "reason":
                    f"累计只发出 {s['sent']} 条，低于 {min_sample} 条的最低观察样本量；"
                    "先补足观察，不要提前下结论", **base}
        notes = []
        if s["violations"] >= 3:
            notes.append(f"已记 {s['violations']} 条纪律告警：先停下检查节奏与内容，"
                         "超窗/重复/单目标超限都在此列")
        if s["deleted_rate"] > deleted_max:
            return {"verdict": "安全异常", "reason":
                    f"被删率 {s['deleted_rate']:.0%} 超过 {deleted_max:.0%}："
                    "先降频并核对平台规则与内容方式，不自动加量" + ("；" + "；".join(notes) if notes else ""),
                    **base}
        if s["replied"] or s["moved"]:
            level = "观察到互动"
            why = (f"回应率 {s['response_rate']:.0%}（参考线：过 {resp_pass:.0%}、"
                   f"继续观察 {resp_maybe:.0%}），转一对一 {s['moved']} 人")
        else:
            level = "尚无有效互动"
            why = (f"回应率 {s['response_rate']:.0%}：先检查需求匹配、回复质量与目标清单，"
                   "不自动换渠道")
        if s.get("last_metrics"):
            m = s["last_metrics"]
            extras = ", ".join(f"{k}={v}" for k, v in m.items() if k != "on")
            why += f"；周指标 {extras}"
        if notes:
            why += "；" + "；".join(notes)
        return {"verdict": level, "reason": why, **base}
