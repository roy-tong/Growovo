#!/usr/bin/env python3
"""优化回流：在同一结果范围内提出可检验的参数改动。

观察数字、候选解释与已验证机制分开。没有当前动作或真实反馈时，不拿历史
回应率推导本轮加量，也不因“有人回应但尚未付费”机械换渠道。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

PARAM = "参数级"
STRATEGY = "策略级"

MIN_RESPONSE_RATE = 3.0
MAX_DELETE_RATE = 15.0
MIN_MOVE_RATE = 30.0
MIN_WEEKLY_ACTIONS = 10


def _item(param, old, new, why, kind=PARAM, evidence="观察"):
    return {"param": param, "from": old, "to": new, "why": why,
            "kind": kind, "evidence_status": evidence}


def propose(result: Dict[str, Any], params: Dict[str, Any],
            intent: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    p = result.get("platform", {})
    t = result.get("task", {})
    b = result.get("business", {})
    sent = p.get("sent", 0)
    responded = t.get("replied", 0) + t.get("asked", 0)
    rate = responded / sent * 100 if sent else 0.0
    value_count = sum((b.get("by_type") or {}).values()) if isinstance(b, dict) else 0

    if sent == 0:
        out.append(_item("当前执行状态", "本轮没有真实发出", "先完成一次授权动作并读回",
                         "当前范围没有真实动作，不能用历史回应率推导本轮加量或渠道优劣", evidence="范围校验"))
        return out

    if sent < MIN_WEEKLY_ACTIONS:
        out.append(_item("本周动作数", sent, params.get("actions_per_week", 20),
                         f"只跑了 {sent} 次，低于 {MIN_WEEKLY_ACTIONS} 次的最低观察样本量；先补足动作，不做渠道判断",
                         evidence="样本不足"))
    elif rate < MIN_RESPONSE_RATE:
        out.append(_item("第一条消息的开头", "现在这版", "小流量测试两版：先给具体帮助 vs 先介绍产品",
                         f"每百次动作只换回 {rate:.1f} 次回应。低回应可能与开口方式、名单或需求匹配有关，先做对照而不是换渠道",
                         evidence="候选解释"))
    elif rate >= MIN_RESPONSE_RATE and value_count:
        out.append(_item("本周动作数", params.get("actions_per_week", 20),
                         int(params.get("actions_per_week", 20) * 1.25),
                         f"每百次动作换回 {rate:.1f} 次回应，且当前范围有 {value_count} 个已核验价值事件；可在人工复核后小幅加人，不加频率",
                         evidence="动作+价值事件"))
    elif rate >= MIN_RESPONSE_RATE:
        out.append(_item("下一步观察", "只有回应", "先补齐试用/激活/真实使用事件，再决定是否加量",
                         f"每百次动作有 {rate:.1f} 次回应，但尚无可核验价值事件；加量只能放大动作，不能证明增长",
                         evidence="价值缺口"))

    if intent:
        deleted = float(intent.get("deleted_rate") or 0) * 100
        if deleted > MAX_DELETE_RATE:
            out.append(_item("发布频率", params.get("actions_per_week", 20),
                             max(int(params.get("actions_per_week", 20) * 0.6), 5),
                             f"被删率 {deleted:.0f}% 超过 {MAX_DELETE_RATE}%，先降频并核查平台规则",
                             evidence="安全信号"))
            out.append(_item("内容档位", params.get("tier", "AI 生成"), "人写人发",
                             "高删除率提示内容或执行方式不被平台接受，先降低自动化与内容档位",
                             evidence="安全信号"))

    moved = t.get("moved", 0)
    if responded >= 3 and moved / max(responded, 1) * 100 < MIN_MOVE_RATE:
        out.append(_item("转一对一的第一句话", "现在的版本", "先确认对方遇到的问题，再决定是否给下一步材料",
                         f"{responded} 个回应里只有 {moved} 个转成一对一，优先检查承接与跟进，而非增加触达",
                         evidence="任务断点"))

    if sent >= MIN_WEEKLY_ACTIONS and responded >= 5 and not value_count:
        out.append(_item("价值诊断", "有人回应但无价值事件", "检查激活、产品价值、采购权限与报价，不自动换渠道",
                         f"{responded} 个回应尚无可核验价值事件；可能是人群、承接、产品或采购问题，需要逐一排除",
                         kind=STRATEGY, evidence="候选解释"))

    if not out:
        out.append(_item("无", "—", "—", "当前证据不足或暂不需要改参数；保留观察，不制造动作",
                         evidence="数据不足"))
    return out


def render(diff: List[Dict[str, Any]]) -> str:
    from .narrative import table
    lines = ["# 下一轮改什么", "",
             "每条建议都落到具体参数；观察、候选解释和已验证机制分开。", ""]
    lines.append(table(["档位", "参数", "从", "到", "依据", "证据状态"],
                       [[d["kind"], d["param"], d["from"], d["to"], d["why"],
                         d.get("evidence_status", "观察")] for d in diff]))
    lines.append("")
    lines.append("> 参数级改动需人工复核后生效；策略级进入复盘。安全与合规边界不改，产品学习可调整。")
    return "\n".join(lines)
