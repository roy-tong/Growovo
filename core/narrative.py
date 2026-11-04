#!/usr/bin/env python3
"""业务叙述：把数字写成判断，把判断写成能执行的话。

为什么单独一层：复盘报告最常见的毛病不是数据错，是**只有数据没有判断**，或者
只有判断没有依据。本模块用代码强制四件套——

    结论（一句话，带方向）
    依据（必须有数字 + 出处，否则拒绝渲染）
    机制（为什么会这样，禁止正确的废话）
    所以（下一步具体做什么，必须是动作）

任何一条缺失或空泛，`Claim.render()` 直接报错。写不出来就是没想清楚，
不允许用"要多互动""要持续优化"糊过去。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

DIGITS = re.compile(r"\d")

# 正确的废话：出现即拒绝。这些词能让句子看起来完整，但不含任何信息。
# 只收「独立出现才有意义」的空话。像"注意""深度"这种会被"注意力""深度而非"
# 这类正常词命中，宁可漏也不误伤。
BANNED = (
    "要更用心", "要用心", "要真诚", "多互动", "持续优化", "提升效果",
    "加油", "赋能", "抓手", "闭环思维", "全面提升", "进一步",
)

# 机制段必须体现因果，出现这些连接词之一才算写了机制。
CAUSAL = ("因为", "所以", "导致", "说明", "意味着", "本质", "结果", "反而", "于是", "在于")


class ClaimError(ValueError):
    """结论不合格。消息本身就是修改建议。"""


@dataclass
class Claim:
    headline: str                      # 结论
    evidence: str                      # 依据：数字 + 出处
    mechanism: str                     # 机制：为什么
    action: str                        # 所以：下一步做什么
    sources: Sequence[str] = field(default_factory=tuple)

    def validate(self) -> None:
        if not self.headline.strip():
            raise ClaimError("结论为空：先想清楚这句话要让对方记住什么")
        if not DIGITS.search(self.evidence):
            raise ClaimError(f"「{self.headline}」的依据里没有数字——没有数字的判断是观点，不是复盘")
        if not self.sources and ":" not in self.evidence:
            raise ClaimError(f"「{self.headline}」没有出处——每个数字都要能追到文件与行号")
        if len(self.mechanism.strip()) < 12:
            raise ClaimError(f"「{self.headline}」的机制写得太短：要说清为什么会这样")
        if not any(w in self.mechanism for w in CAUSAL):
            raise ClaimError(f"「{self.headline}」的机制没有因果——补上「因为/所以/本质是」")
        low = self.headline + self.mechanism + self.action
        for bad in BANNED:
            if bad in low:
                raise ClaimError(f"「{bad}」是正确但没有信息的话，换成具体动作或数字")
        if len(self.action.strip()) < 6:
            raise ClaimError(f"「{self.headline}」没有可执行动作")

    def render(self, index: Optional[int] = None) -> str:
        self.validate()
        num = f"{index}. " if index else ""
        return (f"{num}**{self.headline}**\n\n"
                f"{self.evidence}\n\n"
                f"为什么：{self.mechanism}\n\n"
                f"所以：{self.action}")


def render_claims(claims: Iterable[Claim], start: int = 1) -> str:
    out = []
    for i, c in enumerate(claims, start):
        out.append(c.render(i))
    return "\n\n".join(out)


def pct(n: float, digits: int = 1) -> str:
    return f"{n * 100:.{digits}f}%"


def compare(name_a: str, value_a: float, name_b: str, value_b: float,
            unit: str = "") -> str:
    """比较两个数，并且把「差多少倍」说出来——这是推理的抓手。"""
    if value_b == 0:
        return f"{name_a} {value_a:g}{unit}，{name_b} 为 0"
    ratio = value_a / value_b
    if ratio >= 2:
        return f"{name_a} {value_a:g}{unit}，是{name_b}（{value_b:g}{unit}）的 {ratio:.1f} 倍"
    if ratio <= 0.5:
        return f"{name_a} 只有 {value_a:g}{unit}，不到{name_b}（{value_b:g}{unit}）的一半"
    return f"{name_a} {value_a:g}{unit}，与{name_b}（{value_b:g}{unit}）接近"


def table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "|" + "|".join("---" for _ in headers) + "|"
    body = []
    for r in rows:
        body.append("| " + " | ".join(str(x) if x is not None else "—" for x in r) + " |")
    return "\n".join([head, sep] + body)


def caveat(text: str) -> str:
    """把限制写成正面的话，不写成免责声明。"""
    return f"> 需要知道：{text}"


def section(title: str, intro: str, body: str = "") -> str:
    parts = [f"## {title}"]
    if intro.strip():
        parts.append(intro.strip())
    if body.strip():
        parts.append(body.strip())
    return "\n\n".join(parts)


def lede(text: str) -> str:
    return f"> {text.strip()}"


def audit_claims(claims: Sequence[Claim]) -> List[str]:
    """批量检查，返回所有问题（用于发布前自检）。"""
    problems: List[str] = []
    for c in claims:
        try:
            c.validate()
        except ClaimError as e:
            problems.append(str(e))
    return problems


# ---------------------------------------------------------------- 由数据生成结论

def _src_of(row: dict, n: int = 1) -> str:
    s = row.get("sources") or []
    return s[0] if s else ""


def build_claims(analysis: dict) -> List[Claim]:
    """把渠道账翻译成结论。每条都带数字和出处，随后还要过 validate。"""
    out: List[Claim] = []
    # 只比较样本量够的渠道：3 次动作的渠道没资格参与排名。
    rows = [r for r in analysis.get("channels", [])
            if r.get("response_per_100") is not None and (r.get("actions") or 0) >= 30]
    if not rows:
        return out

    best = max(rows, key=lambda r: r["response_per_100"] or 0)
    worst = min(rows, key=lambda r: r["response_per_100"] or 0)

    if best["response_per_100"] and (worst["response_per_100"] or 0) < (best["response_per_100"] or 0):
        ratio = (best["response_per_100"] / worst["response_per_100"]
                 if worst["response_per_100"] else 0)
        out.append(Claim(
            headline=f"{best['name']} 的开口效率比{worst['name']}高出一个量级",
            evidence=(f"{best['name']} 每 100 次动作换回 {best['response_per_100']} 次回应；"
                      f"{worst['name']} 只有 {worst['response_per_100'] or 0} 次"
                      + (f"，相差 {ratio:.0f} 倍" if ratio >= 2 else "")
                      + f"（{_src_of(best)}）"),
            mechanism=(f"因为{best['name']}上流通的是对方当下需要的具体东西，"
                       f"而{worst['name']}上消耗的是注意力——同样的动作次数，换回来的东西完全不同"),
            action=f"把下一轮的动作量从{worst['name']}转到{best['name']}，先换产能再谈加量",
            sources=best.get("sources", [])[:2],
        ))

    for w in analysis.get("waste", [])[:2]:
        out.append(Claim(
            headline=f"{w['name']}上的投入没有回头",
            evidence=(f"日志里能数到 {int(w['actions'])} 次动作，回应 {w['responses'] or 0} 次，"
                      f"没有找到任何结果产出（{_src_of(w)}）"),
            mechanism=("因为这类动作是先发出去再等反应，没有为对方设计任何「凭什么回你」的理由，"
                       "所以数量越大，单位价值越低"),
            action=f"在给{word(w['name'])}设计出明确的「他为什么会回」之前，停止在这条渠道加量",
            sources=w.get("sources", [])[:2],
        ))

    for c in analysis.get("conflicts", [])[:1]:
        vals = "、".join(str(v) for v in c["values"][:4])
        out.append(Claim(
            headline=f"{c['name']}的{c['metric']}数字对不上，先统一定义再比较渠道",
            evidence=f"同一件事在日志里出现 {vals} 这几个数（{c['name']}）",
            mechanism="因为不同批次的记录用了不同的分母（有的算批、有的算封、有的算人去重），"
                      "所以任何跨渠道比较在这个口径统一之前都不成立",
            action=f"先把{c['name']}的{c['metric']}口径写死一次，再重算所有渠道的每百次回应",
            sources=[c["name"]],
        ))
    return out


def word(name: str) -> str:
    """加引号，让动作读起来像话。"""
    return f"「{name}」"
