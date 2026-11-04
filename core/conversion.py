#!/usr/bin/env python3
"""转化：把「聊过一次」变成「付过一次」，再变成「每月都付」。

渠道负责「遇见」；本模块负责「遇见之后每一步做什么」。核心不是 CRM，是四件事：

1. **阶段**：sourced → contacted → engaged → qualified → sample → readback →
   quoted → SOW → paid → delivered → repeat；每一步都有进入条件和证据。
2. **下一个动作**：每个阶段只给一个动作、一个截止日、一个停止条件；
   不给「多跟进」这种没边的话。
3. **异议**：把「已经有工具/太贵/数据敏感/没预算/发资料」变成可回应的分支，
   并明确哪些情况应该走开。
4. **报价**：从 offer 阶梯生成一页 SOW；报价只出现一次，降价只缩范围。

只读部分（funnel/next/quote）不写文件；`advance` 写在 `deals.jsonl`，append-only。
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

STAGES = ["sourced", "contacted", "engaged", "qualified", "sample_offered",
          "sample_received", "sample_delivered", "readback", "quoted",
          "sow_sent", "paid", "delivered", "repeat", "lost"]

STAGE_LABELS = {
    "sourced": "已定位", "contacted": "已触达", "engaged": "有回应",
    "qualified": "已合格", "sample_offered": "已给样本范围",
    "sample_received": "已收到数据", "sample_delivered": "样本已交付",
    "readback": "已读回", "quoted": "已报价", "sow_sent": "SOW 已发",
    "paid": "已付费", "delivered": "已交付", "repeat": "已复购",
    "lost": "已放弃",
}

TRANSITIONS = {
    "sourced": ["contacted", "lost"],
    "contacted": ["engaged", "lost"],
    "engaged": ["qualified", "lost"],
    "qualified": ["sample_offered", "lost"],
    "sample_offered": ["sample_received", "lost"],
    "sample_received": ["sample_delivered", "lost"],
    "sample_delivered": ["readback", "lost"],
    "readback": ["quoted", "lost"],
    "quoted": ["sow_sent", "lost"],
    "sow_sent": ["paid", "lost"],
    "paid": ["delivered", "lost"],
    "delivered": ["repeat", "lost"],
    "repeat": [],
    "lost": [],
}

# 每一次阶段推进都必须留下可复核依据；发送动作本身不是业务证据。
EVIDENCE_STAGES = set(STAGES)
DEAL_EVENT_KIND = "growovo.deal_event"

# 每个阶段在业务上意味着什么：不是代码，是判断。
STAGE_GUIDE = {
    "sourced": {
        "meaning": "你只知道他是谁、他最近在做什么；还不知道他有没有触发事件。",
        "know": "名字、来源、最近一条公开内容。",
        "need": "一个触发事件 + 一个可沟通的身份。",
        "why": "第一条消息不是介绍产品，是证明你读过他。",
        "risk": "群发式开场，没有引用原话。",
        "walk_away": "没有来源、没有原话 → 不进管道。",
    },
    "contacted": {
        "meaning": "你发了第一条；还没证明他愿意花时间。",
        "know": "他收到了，或者没回。",
        "need": "一个回复，哪怕是拒绝。",
        "why": "第 3 天只补一个具体发现；不追第二次。",
        "risk": "追太紧，或第二条像模板。",
        "walk_away": "3 天没回 → 判弃。",
    },
    "engaged": {
        "meaning": "他回了；可能只是礼貌，也可能真是买家。",
        "know": "他的原话。",
        "need": "触发事件、数据、现有办法。",
        "why": "问三个资格问题；不跳步给样本。",
        "risk": "把礼貌当意向。",
        "walk_away": "拒绝，或只问价格不谈问题。",
    },
    "qualified": {
        "meaning": "有触发事件、能拿数据、现有办法有成本；第一个可以给样本的信号。",
        "know": "资格五项。",
        "need": "一个归属单元的数据。",
        "why": "先给范围，再要数据；24 小时。",
        "risk": "白嫖，或数据不全。",
        "walk_away": "不愿给数据，或只想要免费咨询。",
    },
    "sample_offered": {
        "meaning": "你给了范围；他还在评估要不要信你。",
        "know": "范围、交付、边界。",
        "need": "三份文件（发票/成本/用量）。",
        "why": "确认数据清单；数据不到不开工。",
        "risk": "无限等。",
        "walk_away": "7 天不给数据。",
    },
    "sample_received": {
        "meaning": "他给了数据；这是信任的第一个硬证据。",
        "know": "发票、成本导出、用量导出。",
        "need": "3 页差异报告。",
        "why": "24 小时出结果；先给差异，不给结论。",
        "risk": "范围蔓延。",
        "walk_away": "数据不可用，或拒绝脱敏。",
    },
    "sample_delivered": {
        "meaning": "你给了差异；他还没说「这就是我要的」。",
        "know": "差异清单。",
        "need": "一句读回原话。",
        "why": "读回才算交付；不读回就追一次。",
        "risk": "自嗨，把发送当交付。",
        "walk_away": "不读回，或只说「收到」。",
    },
    "readback": {
        "meaning": "他说了「这就是我月底要交的东西」；这是报价的唯一时机。",
        "know": "他的读回。",
        "need": "范围、价格、验收。",
        "why": "报价 $1,500，一页 SOW，只出现一次。",
        "risk": "报太早，或报太晚。",
        "walk_away": "明确拒绝，或预算不匹配。",
    },
    "quoted": {
        "meaning": "报了价；他在比较、拖，或等预算。",
        "know": "报价记录。",
        "need": "一个决定，或一个障碍。",
        "why": "第 3 天问一次；降价只缩范围。",
        "risk": "降价，或追第三次。",
        "walk_away": "3 次不决，或明确拒绝。",
    },
    "sow_sent": {
        "meaning": "他拿了 SOW；在走签字/付款。",
        "know": "SOW 已发。",
        "need": "签字或付款。",
        "why": "确认一次；不催第三次。",
        "risk": "流程卡住，没人推。",
        "walk_away": "3 次无进展。",
    },
    "paid": {
        "meaning": "到账；交付开始。",
        "know": "付款。",
        "need": "7 个文件 + 读回。",
        "why": "先交付，再谈复购。",
        "risk": "交付拖延，或范围蔓延。",
        "walk_away": "—",
    },
    "delivered": {
        "meaning": "交付完成；复购窗口在 30 天后。",
        "know": "交付包 + 读回。",
        "need": "下一期需求。",
        "why": "30 天后用上期结果开场。",
        "risk": "交付完就消失。",
        "walk_away": "—",
    },
    "repeat": {
        "meaning": "再次付费；这才是可复购的证据。",
        "know": "第二期。",
        "need": "多账期/多供应商。",
        "why": "先复现，再加量。",
        "risk": "过早扩。",
        "walk_away": "—",
    },
    "lost": {
        "meaning": "明确拒绝，或无回应判弃。",
        "know": "原因。",
        "need": "90 天后可再试一次。",
        "why": "停；不换号、不纠缠。",
        "risk": "反复追。",
        "walk_away": "—",
    },
}


NEXT_DUE_DAYS = {
    "sourced": 1, "contacted": 3, "engaged": 2, "qualified": 1,
    "sample_offered": 2, "sample_received": 1, "sample_delivered": 1,
    "readback": 1, "quoted": 3, "sow_sent": 2, "paid": 1,
    "delivered": 30, "repeat": 30, "lost": 0,
}

QUALIFICATION = [
    ("trigger", "有触发事件 + 日期（发票到了/CFO 要数/续约/账单异常）"),
    ("contactable", "责任人可沟通（本人发帖/回帖/熟人引荐）"),
    ("data", "能拿到发票 + 至少一份导出"),
    ("cost", "现有办法有成本（表格/工程师工时/顾问费）"),
    ("price", "接受明确范围与价格（或至少愿意听）"),
]

OBJECTIONS = [
    {"key": "already_have_tool", "label": "已经有工具了",
     "signal": "“我们有 CloudZero/Vantage/FinOps 工具”",
     "response": "问一句：它能不能把发票和成本导出行对行、给每个差异一个原因码？"
                 "大多数停在分摊。能，就走开；不能，就只卖最后一公里。",
     "walk_away": "对方能拿出 invoice-level reconciliation 的证据"},
    {"key": "too_expensive", "label": "太贵了",
     "signal": "“$1,500 做一次对账太贵”",
     "response": "缩范围，不降价：先做一个供应商/一个归属单元；或先做免费样本。"
                 "把“省多少钱”换成“这份包能不能签字”。",
     "walk_away": "对方预算低于 $500 且没有合规/关账压力"},
    {"key": "data_sensitive", "label": "数据太敏感",
     "signal": "“账单不能给外部”",
     "response": "三个选项：本地跑 checker 只给输出；只给一个归属单元；NDA + 脱敏 + 交付后删除。",
     "walk_away": "对方连脱敏导出都不愿给，且不愿自己跑"},
    {"key": "no_budget", "label": "今年没预算",
     "signal": "“要等下一个财年”",
     "response": "问预算归属和续约/关账时间；把首单压到部门费用（<$5k）或挂在续约窗口。",
     "walk_away": "没有日期、没有 owner、没有触发事件"},
    {"key": "send_info", "label": "先发资料看看",
     "signal": "“发个 deck 过来”",
     "response": "不发 deck，发一页 checklist + 一个问题：你们上个月怎么分的？"
                 "资料不是进展，问题才是。",
     "walk_away": "连续两次只要资料、不回答任何问题"},
    {"key": "not_now", "label": "现在不是时候",
     "signal": "“下个季度再说”",
     "response": "要一个具体日期和触发事件（关账/续约/审计）；到点再跟一次。",
     "walk_away": "没有日期"},
    {"key": "need_approval", "label": "要老板批",
     "signal": "“我得问 CFO/采购”",
     "response": "给审批人一页：范围、价格、验收、数据边界；主动提出 15 分钟三方会。",
     "walk_away": "审批人始终不出现"},
    {"key": "vendor_lock", "label": "供应商说他们能对",
     "signal": "“OpenAI/Anthropic 后台能做”",
     "response": "供应商不会替客户做跨家对账，也不会为发票签字；独立核验的价值在“不站在供应商一边”。",
     "walk_away": "对方只需要供应商自证，不需要独立材料"},
    {"key": "diy", "label": "我们自己做",
     "signal": "“一个工程师两周就做出来了”",
     "response": "同意，云侧自建很常见；问 AI 供应商那一段谁维护、月末 7 天修订谁盯。",
     "walk_away": "对方已有稳定运行的 AI 对账流程"},
]

OFFERS = {
    "sample": {
        "name": "免费样本", "price": 0, "currency": "USD",
        "scope": "1 个供应商 × 1 个账户 × 1 个账期 × 1 个归属单元",
        "turnaround": "24 小时",
        "deliverables": ["总额与口径", "逐行对账 + 原因码", "Top 3 + 缺什么数据"],
        "acceptance": ["复算合计 = 发票总额（到分）", "每笔差异有原因码",
                       "同一输入重跑一致", "财务能读懂总额/差异/下一步"],
        "exclusions": ["不承诺追回/节省", "不做全成本 TCO", "不替代签字"],
    },
    "diagnostic": {
        "name": "诊断包", "price": 1500, "currency": "USD",
        "scope": "1 个供应商 × 1 个账户 × 1 个完整账期",
        "turnaround": "5 个工作日",
        "deliverables": ["01_scope", "02_mapping", "03_recalc", "04_reconciliation",
                        "05_residual", "06_cfo_onepager", "07_signoff"],
        "acceptance": ["分摊合计 = 发票总额（到分）", "每笔差异有原因码",
                       "同一输入重跑一致", "财务可直接入账/计提", "残余单独列示"],
        "exclusions": ["不承诺追回/节省/ROI", "不做实时监控", "不做系统集成"],
    },
    "monthly": {
        "name": "月度包", "price": 900, "currency": "USD",
        "scope": "每账期重复 + 环比 + 下月动作",
        "turnaround": "3–5 个工作日",
        "deliverables": ["诊断包 7 个文件", "环比", "下月动作"],
        "acceptance": ["同诊断包", "环比口径一致"],
        "exclusions": ["不承诺节省", "不含多供应商"],
    },
    "quarterly": {
        "name": "季度试点", "price": 4000, "currency": "USD",
        "scope": "3 个账期 + 映射搭建 + 一次复盘",
        "turnaround": "6 周",
        "deliverables": ["3 期诊断包", "映射表", "复盘"],
        "acceptance": ["同诊断包", "3 期口径一致"],
        "exclusions": ["不含多供应商/云市场"],
    },
}


class ConversionError(ValueError):
    """阶段/证据/报价不成立。"""


def _today() -> str:
    return date.today().isoformat()


def _add_days(on: str, days: int) -> str:
    return (date.fromisoformat(on) + timedelta(days=days)).isoformat()


def offer(offer_id: str) -> Dict[str, Any]:
    if offer_id not in OFFERS:
        raise ConversionError(f"没有这个 offer：{offer_id}；可选：{', '.join(OFFERS)}")
    return dict(OFFERS[offer_id])


def qualify(criteria: Dict[str, Any]) -> Dict[str, Any]:
    hits = []
    missing = []
    for key, label in QUALIFICATION:
        if criteria.get(key):
            hits.append(key)
        else:
            missing.append(label)
    score = len(hits)
    return {"score": score, "qualified": score >= 3,
            "hits": hits, "missing": missing,
            "note": "至少中 3 项才算合格对话；缺 data 时先给“你自己跑、我审输出”"}


def next_action(stage: str, days_since_update: int = 0) -> Dict[str, Any]:
    if stage not in STAGES:
        raise ConversionError(f"未知阶段：{stage}")
    table = {
        "sourced": ("第一条消息：引用对方原话 + 两个真问题",
                    "先给判断，不介绍产品", 0),
        "contacted": ("第 3 天跟进一次；只补一个具体发现",
                      "不追第二次", 3),
        "engaged": ("问三个资格问题：触发事件、数据、现有办法",
                    "问完再给样本，不跳步", 1),
        "qualified": ("给免费样本：1 个归属单元、24h、3 页",
                      "先给范围，再要数据", 1),
        "sample_offered": ("确认数据清单；24h 内给样本范围",
                           "数据不到就不开工", 2),
        "sample_received": ("跑样本；24h 出 3 页",
                            "先给差异，不给结论", 1),
        "sample_delivered": ("请对方读回：这是不是月底要交的东西",
                             "读回才算交付", 1),
        "readback": ("报价：$1,500 诊断包，一页 SOW；只出现一次",
                     "报价不是追问，是给决定", 1),
        "quoted": ("第 3 天跟进一次：问决定/障碍，不降价",
                   "降价只缩范围", 3),
        "sow_sent": ("确认签字/付款；不要催第三次",
                     "超过 3 次就停", 2),
        "paid": ("交付：7 个文件 + 读回",
                 "先交付，再谈复购", 1),
        "delivered": ("30 天后问复购/下一期",
                      "用上期结果开场", 30),
        "repeat": ("扩到多账期/多供应商/顾问渠道",
                   "先复现，再加量", 30),
        "lost": ("停；记原因；90 天后可再试一次",
                 "不换号、不纠缠", 0),
    }
    action, why, due_in = table[stage]
    guide = STAGE_GUIDE.get(stage, {})
    base = {"stage": stage, "meaning": guide.get("meaning", ""),
            "know": guide.get("know", ""), "need": guide.get("need", ""),
            "risk": guide.get("risk", ""), "walk_away": guide.get("walk_away", "")}
    if stage in ("contacted", "quoted") and days_since_update < due_in:
        return {**base, "action": "等", "why": f"还没到第 {due_in} 天",
                "due_in_days": due_in - days_since_update, "stop": False}
    return {**base, "action": action, "why": why,
            "due_in_days": due_in, "stop": stage == "lost"}


def objection(key: str) -> Dict[str, Any]:
    for row in OBJECTIONS:
        if row["key"] == key:
            return dict(row)
    raise ConversionError(f"没有这个异议：{key}；可选：{', '.join(o['key'] for o in OBJECTIONS)}")


def quote(deal: Dict[str, Any], offer_id: str = "diagnostic") -> Dict[str, Any]:
    if deal.get("stage") != "readback":
        raise ConversionError("只有当前处于 readback 的 deal 才能报价；发送/报价动作不能伪造读回")
    spec = offer(offer_id)
    return {
        "deal": deal.get("id", ""), "contact": deal.get("name", ""),
        "campaign": deal.get("campaign", ""), "offer": spec,
        "issued": _today(),
        "payment": ("50% 启动 / 50% 交付验收" if spec["price"] else "免费"),
        "note": ("报价只出现一次；对方压价就缩范围（少一个归属单元/延后一个月），"
                 "不降价。"),
    }


class Deals:
    """Append-only deal events with a legacy-current-row compatible projection."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def all(self) -> List[Dict[str, Any]]:
        """Project events to current deals; legacy snapshot rows remain readable.

        A legacy line is a complete current deal. New lines are events. Appending
        an event for a legacy deal therefore updates its projection without
        rewriting the original line.
        """
        if not self.path.exists():
            return []
        projected: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for lineno, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            if raw.get("kind") != DEAL_EVENT_KIND:
                deal_id = raw.get("id")
                if not deal_id:
                    raise ConversionError(f"{self.path}:{lineno}：旧 deal 行缺 id")
                if deal_id not in projected:
                    order.append(deal_id)
                projected[deal_id] = raw
                continue

            event = raw.get("event")
            deal_id = raw.get("deal_id")
            if event == "add":
                deal = deepcopy(raw.get("deal"))
                if not isinstance(deal, dict):
                    raise ConversionError(f"{self.path}:{lineno}：add 事件缺 deal")
                deal_id = deal_id or deal.get("id")
                if not deal_id or deal.get("id") != deal_id:
                    raise ConversionError(f"{self.path}:{lineno}：add 事件 deal_id 不一致")
                if deal_id not in projected:
                    order.append(deal_id)
                projected[deal_id] = deal
                continue

            if not deal_id or deal_id not in projected:
                raise ConversionError(f"{self.path}:{lineno}：事件指向不存在的 deal：{deal_id}")
            row = projected[deal_id]
            if event == "advance":
                entry = deepcopy(raw.get("entry"))
                if not isinstance(entry, dict):
                    raise ConversionError(f"{self.path}:{lineno}：advance 事件缺 history entry")
                row["stage"] = raw["stage"]
                row["updated"] = raw["at"]
                row["next_due"] = raw["next_due"]
                row.setdefault("history", []).append(entry)
            elif event == "qualify":
                row["qualification"] = deepcopy(raw.get("qualification") or {})
                row["qualified"] = raw.get("qualified") is True
                row["updated"] = raw["at"]
            else:
                raise ConversionError(f"{self.path}:{lineno}：未知 deal 事件：{event}")
        return [deepcopy(projected[deal_id]) for deal_id in order]

    def _append(self, event: Dict[str, Any]) -> None:
        """Append exactly one JSONL event; never replace or compact the file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"kind": DEAL_EVENT_KIND, "schema_version": 1, **event}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _next_id(rows: List[Dict[str, Any]]) -> str:
        numbers = []
        for row in rows:
            deal_id = str(row.get("id") or "")
            if deal_id.startswith("deal-") and deal_id[5:].isdigit():
                numbers.append(int(deal_id[5:]))
        return f"deal-{max(numbers, default=0) + 1:04d}"

    def add(self, contact_id: str, name: str = "", campaign: str = "",
            owner: str = "", stage: str = "sourced",
            note: str = "", on: Optional[str] = None,
            migration: bool = False) -> Dict[str, Any]:
        if stage not in STAGES:
            raise ConversionError(f"未知阶段：{stage}")
        if stage != "sourced" and not migration:
            raise ConversionError("普通 add 只能从 sourced 开始；旧数据迁移须显式 migration=True")
        rows = self.all()
        if any(r["contact_id"] == contact_id and r.get("campaign") == campaign
               for r in rows):
            raise ConversionError(f"已存在：{contact_id}/{campaign}")
        on = on or _today()
        entry = {"at": on, "stage": stage, "by": owner or "system",
                 "evidence": "", "note": note}
        if migration:
            entry["migration"] = True
        deal = {
            "id": self._next_id(rows),
            "contact_id": contact_id, "name": name, "campaign": campaign,
            "owner": owner, "stage": stage, "offer": "", "value": 0,
            "currency": "USD", "qualification": {}, "qualified": False,
            "created": on, "updated": on,
            "next_due": _add_days(on, NEXT_DUE_DAYS.get(stage, 1)),
            "history": [entry],
        }
        self._append({"event": "add", "deal_id": deal["id"], "at": on,
                      "migration": bool(migration), "deal": deal})
        return deal

    def get(self, deal_id: str) -> Dict[str, Any]:
        for row in self.all():
            if row["id"] == deal_id:
                return row
        raise ConversionError(f"没有这个 deal：{deal_id}")

    def advance(self, deal_id: str, stage: str, by: str, evidence: str = "",
                note: str = "", on: Optional[str] = None,
                verification_method: str = "", verification_ref: str = "") -> Dict[str, Any]:
        if stage not in STAGES:
            raise ConversionError(f"未知阶段：{stage}")
        evidence = str(evidence or "").strip()
        verification_method = str(verification_method or "").strip()
        verification_ref = str(verification_ref or "").strip()
        for row in self.all():
            if row["id"] != deal_id:
                continue
            allowed = TRANSITIONS.get(row["stage"], [])
            if stage not in allowed:
                raise ConversionError(
                    f"{row['stage']} 不能直接到 {stage}；可选：{', '.join(allowed) or '无'}")
            if stage in EVIDENCE_STAGES and not evidence:
                raise ConversionError(f"到 {stage} 必须给非空 evidence；平台动作本身不是业务证据")
            if stage == "qualified" and row.get("qualified") is not True:
                raise ConversionError("进入 qualified 前必须先 qualify 且结果为合格")
            if stage == "paid" and bool(verification_method) != bool(verification_ref):
                raise ConversionError(
                    "paid 如提供 verification，verification_method 与 verification_ref 必须同时非空")
            on = on or _today()
            updated = deepcopy(row)
            entry = {"at": on, "stage": stage, "by": by,
                     "evidence": evidence, "note": note,
                     "verification_method": verification_method,
                     "verification_ref": verification_ref}
            updated["stage"] = stage
            updated["updated"] = on
            updated["next_due"] = _add_days(on, NEXT_DUE_DAYS.get(stage, 1))
            updated.setdefault("history", []).append(entry)
            self._append({"event": "advance", "deal_id": deal_id, "at": on,
                          "stage": stage, "next_due": updated["next_due"],
                          "entry": entry})
            return updated
        raise ConversionError(f"没有这个 deal：{deal_id}")

    def qualify(self, deal_id: str, criteria: Dict[str, Any]) -> Dict[str, Any]:
        result = qualify(criteria)
        for row in self.all():
            if row["id"] != deal_id:
                continue
            on = _today()
            updated = deepcopy(row)
            updated["qualification"] = {k: bool(criteria.get(k)) for k, _ in QUALIFICATION}
            updated["qualified"] = result["qualified"]
            updated["updated"] = on
            self._append({"event": "qualify", "deal_id": deal_id, "at": on,
                          "qualification": updated["qualification"],
                          "qualified": updated["qualified"]})
            return {"deal": updated, "result": result}
        raise ConversionError(f"没有这个 deal：{deal_id}")

    def funnel(self, on: Optional[str] = None) -> Dict[str, Any]:
        on = on or _today()
        rows = self.all()
        counts = {s: 0 for s in STAGES}
        cohort_counts = {s: 0 for s in STAGES}
        deal_stages: List[set] = []
        for row in rows:
            counts[row["stage"]] = counts.get(row["stage"], 0) + 1
            history = row.get("history")
            entered = {entry.get("stage") for entry in (history or [])
                       if isinstance(entry, dict) and entry.get("stage") in STAGES}
            # Very old current rows may not have complete history. Their current
            # stage is known, but earlier stages are never invented by inference.
            if row["stage"] in STAGES:
                entered.add(row["stage"])
            deal_stages.append(entered)
            for stage in entered:
                cohort_counts[stage] += 1

        order = [s for s in STAGES if s != "lost"]
        rates = {}
        for i in range(1, len(order)):
            prev, cur = order[i - 1], order[i]
            denominator = sum(prev in entered for entered in deal_stages)
            numerator = sum(prev in entered and cur in entered for entered in deal_stages)
            rates[f"{prev}->{cur}"] = round(numerator / denominator, 2) if denominator else None

        sourced = sum("sourced" in entered for entered in deal_stages)
        paid = sum("sourced" in entered and "paid" in entered for entered in deal_stages)
        overall = round(paid / sourced, 2) if sourced else None
        stuck = []
        for row in rows:
            if row["stage"] in ("repeat", "lost"):
                continue
            try:
                days = (date.fromisoformat(on) - date.fromisoformat(row["updated"])).days
            except ValueError:
                continue
            threshold = NEXT_DUE_DAYS.get(row["stage"], 3) + 2
            if days > threshold:
                stuck.append({"id": row["id"], "name": row.get("name", ""),
                              "stage": row["stage"], "days": days})
        return {
            "date": on,
            # ``counts`` remains as a compatibility alias; both are current stock.
            "counts": counts,
            "current_counts": counts,
            "cohort_counts": cohort_counts,
            "counts_note": "counts/current_counts 是当前阶段存量；cohort_counts 是 history 曾进入阶段的 deal 数。",
            "rates_note": ("rates 以上游 history cohort 为分母、其中曾进入下游者为分子；"
                           "overall_paid_rate 是 sourced cohort 中曾进入 paid 的比例；分母 0 为 None。"),
            "rates": rates,
            "overall_paid_rate": overall,
            "stuck": stuck,
            "total": len(rows),
        }


def render_status(funnel: Dict[str, Any]) -> str:
    from .narrative import table
    counts = funnel.get("current_counts", funnel["counts"])
    total = funnel["total"]
    paid = counts["paid"] + counts["delivered"] + counts["repeat"]
    stuck = funnel["stuck"]
    lines = ["# 转化判断：现在卡在哪", ""]
    lines.append("## 结论")
    lines.append("")
    lines.append(f"总线索 {total} 个，已付费 {paid} 个，卡住 {len(stuck)} 个。"
                 f"先看卡住的人，不要先看总数。")
    lines.append("")
    lines.append("## 每个阶段意味着什么")
    lines.append("")
    rows = []
    for stage in STAGES:
        if not counts.get(stage):
            continue
        guide = STAGE_GUIDE.get(stage, {})
        rows.append([STAGE_LABELS[stage], counts[stage],
                     guide.get("meaning", ""), guide.get("why", "")])
    lines.append(table(["阶段", "数量", "业务含义", "下一步"], rows))
    lines.append("")
    lines.append("## 漏斗")
    lines.append("")
    lines.append("数量是当前阶段存量；转化率按 history 曾进入阶段的 cohort 计算，"
                 "对象继续前进不会从上游分母或下游分子中消失。")
    lines.append("")
    if funnel["rates"]:
        def label(key):
            a, b = key.split("->")
            return f"{STAGE_LABELS.get(a, a)} → {STAGE_LABELS.get(b, b)}"
        lines.append(table(["转化", "比率"],
                           [[label(k), "—" if v is None else f"{v:.0%}"]
                            for k, v in funnel["rates"].items()]))
    else:
        lines.append("还没有可以算的相邻阶段。")
    lines.append("")
    rate = funnel["overall_paid_rate"]
    lines.append(f"总付费率：{'—' if rate is None else f'{rate:.0%}'}。"
                 f"样本少于 10 个时，这个数只当线索，不当结论。")
    lines.append("")
    if stuck:
        lines.append("## 卡住的人")
        lines.append("")
        for row in stuck:
            guide = STAGE_GUIDE.get(row["stage"], {})
            lines.append(f"- **{row['name'] or row['id']}**：{STAGE_LABELS[row['stage']]}，"
                         f"{row['days']} 天没动。{guide.get('risk', '')}")
        lines.append("")
    lines.append("> 阶段是记录，不是预测；`paid` 才叫付费，漏斗是描述，不是因果。")
    lines.append("")
    return "\n".join(lines)


def render_next(rows: List[Dict[str, Any]]) -> str:
    from .narrative import table
    lines = ["# 转化判断：今天该跟谁", ""]
    if not rows:
        lines.append("今天没有到期的对象。")
        return "\n".join(lines)
    lines.append("## 结论")
    lines.append("")
    lines.append(f"今天有 {len(rows)} 个对象需要动作。不是「多发几条」，"
                 f"是每人一个具体动作，做完就停。")
    lines.append("")
    lines.append("## 今天做什么")
    lines.append("")
    lines.append(table(["对象", "现在在哪", "今天做什么", "为什么", "几天内"],
                       [[r["name"] or r["id"], STAGE_LABELS[r["stage"]],
                         r["next"]["action"], r["next"]["why"],
                         r["next"]["due_in_days"]] for r in rows]))
    lines.append("")
    lines.append("## 他可能会怎么拒绝")
    lines.append("")
    for obj in OBJECTIONS[:4]:
        lines.append(f"- **{obj['label']}**：{obj['response']}"
                     f"（走开：{obj['walk_away']}）")
    lines.append("")
    lines.append("## 什么时候报价")
    lines.append("")
    lines.append("只在「读回」之后报价：他说了「这就是我月底要交的东西」，"
                 "才说明样本有价值。报价 $1,500，一页 SOW，只出现一次；"
                 "对方压价就缩范围，不降价。")
    lines.append("")
    lines.append("## 什么情况下放弃")
    lines.append("")
    lines.append("- 同一条消息 3 天没回：判弃，不追第二次。")
    lines.append("- 不愿给数据、只想要免费咨询：不是买家。")
    lines.append("- 连续两次只要资料、不回答任何问题：不是买家。")
    lines.append("- 报价后 3 次不决：判弃，记原因，90 天后可再试一次。")
    lines.append("")
    lines.append("> 阶段是记录，不是预测；报价不是收入，样本不是付费。")
    lines.append("")
    return "\n".join(lines)


def render_quote(q: Dict[str, Any]) -> str:
    spec = q["offer"]
    lines = [f"# {spec['name']} · 一页 SOW", ""]
    lines.append(f"**客户**：{q.get('contact') or q.get('deal', '')}")
    lines.append(f"**日期**：{q['issued']}")
    lines.append(f"**价格**：{spec['currency']} {spec['price']:,}（{q['payment']}）")
    lines.append("")
    lines.append(f"**范围**：{spec['scope']}")
    lines.append(f"**周期**：{spec['turnaround']}")
    lines.append("")
    lines.append("## 交付")
    for d in spec["deliverables"]:
        lines.append(f"- {d}")
    lines.append("")
    lines.append("## 验收")
    for a in spec["acceptance"]:
        lines.append(f"- {a}")
    lines.append("")
    lines.append("## 不做")
    for e in spec["exclusions"]:
        lines.append(f"- {e}")
    lines.append("")
    if spec["price"]:
        lines.append("## 为什么是这个价")
        lines.append("")
        lines.append("- 一个账期 8–12 小时；这个价约合 $125–190/小时，低于外部顾问 $300/小时。")
        lines.append("- 按账期收费，不按订阅：买家的仪式是月度关账，不是再上一个平台。")
        lines.append("- 不按节省分成：AI 账单的节省归因太难；首单要的是有人付钱，不是分成最优。")
        lines.append("")
    else:
        lines.append("## 为什么免费")
        lines.append("")
        lines.append("免费只做样本：拿到真实数据、证明方法、让对方判断值不值得付。")
        lines.append("完整账期是收费项；免费做完整账期会把服务变成白嫖。")
        lines.append("")
    lines.append(f"> {q['note']}")
    return "\n".join(lines)
