"""表达规则：用日常说法，不生造比喻词。

这是写作层的第一条规则，跟 voice（用什么语气）和 lint（会不会被平台当广告）
并列，管的是另一件事：**看的人第一遍能不能懂**。

规则本身只有一句：
    一个词如果需要先解释才能懂，就换成日常说法；判断标准是把它单独拿给
    不在这个行业里的人看，他能不能说出这句话在讲什么。

为什么把这条写进代码而不是只写在文档里：生造词对作者是省事、对读者是成本。
它会自然地从作者的脑子里长出来，写一次就会被复制十次；只有让检查器每次
都拦一下，这个成本才会留在作者这边。

常见的生造词是「把两个不相干的概念拼成一个名词」：把「发布」说成「发射」，
再派生出「发射点」「发射窗口」「发射开关」。听的人每次都要在脑子里翻译一遍——
这就是要拦的东西。

约定：
- 只拦「建议替换」级别的词，判断不了上下文的不拦，避免误报把检查变成噪音；
- 检查器不改成句：`fix()` 只做机械替换，人要在替换后重读一遍；
- 面向机器的 JSON 字段名不受此限（英文键名本来就没人读成句子）。
"""
from __future__ import annotations

import functools
import re
from typing import Any, Callable, Dict, List, Tuple

#: 规则的完整表述。给人和 Agent 看的是这一段，不是代码。
RULE = (
    "用日常说法，不生造比喻词。一个词如果需要先解释才能懂，就换成日常说法。"
    "判断标准：把它单独拿给不在这个行业里的人看，他能不能说出这句话在讲什么。"
)

#: 生造词 → 日常说法。长的写在前面，避免「发射点」先被「发射」吃掉。
COINED_TERMS: Dict[str, str] = {
    "发射开关": "发布前准备",
    "发射窗口": "发布窗口",
    "发射点": "集中发布渠道",
    "发射平台": "集中发布",
    "发射渠道": "集中发布",
    "一次性发射": "一次性集中发布",
    "发射": "集中发布",
    "弹药库": "素材库",
    "弹药": "素材",
    "子弹": "机会",
    "打法": "做法",
    "抓手": "入手点",
    "赋能": "提供能力",
    "颗粒度": "详细程度",
    "势能": "影响力",
    "破圈": "扩到圈外",
    "组合拳": "配套做法",
}

#: 误报保护：这些词里含有上面的字，但本身是正常说法。
_ALLOW = ("发射器", "发射率")


def find(text: str) -> List[Dict[str, Any]]:
    """找出文本里的生造词。返回 [{term, replace_with, line, column, context}]。"""
    if not text:
        return []
    hits: List[Dict[str, Any]] = []
    taken: List[Tuple[int, int]] = []
    for term in sorted(COINED_TERMS, key=len, reverse=True):
        for m in re.finditer(re.escape(term), text):
            start, end = m.span()
            if any(s <= start < e for s, e in taken):
                continue  # 已被更长的词覆盖
            if any(a in text[max(0, start - 2):end + 2] for a in _ALLOW):
                continue
            line = text.count("\n", 0, start) + 1
            col = start - (text.rfind("\n", 0, start) + 1) + 1
            ctx_start = max(0, start - 12)
            ctx_end = min(len(text), end + 12)
            hits.append({
                "term": term,
                "replace_with": COINED_TERMS[term],
                "line": line,
                "column": col,
                "context": text[ctx_start:ctx_end].replace("\n", " "),
            })
            taken.append((start, end))
    hits.sort(key=lambda h: (h["line"], h["column"]))
    return hits


def fix(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """机械替换生造词，返回（替换后的文本，替换记录）。替换后必须重读。"""
    hits = find(text)
    if not hits:
        return text, []
    out = text
    for term in sorted({h["term"] for h in hits}, key=len, reverse=True):
        out = out.replace(term, COINED_TERMS[term])
    return out, hits


def finalize(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """渲染出口的最后一道：把生造词换掉再交出去。

    和 `fix()` 的区别是用途：这个是产品自己生成文本时的强制关口，任何
    render 出来的东西都要过；`fix()` 是给人改草稿用的。替换是机械的，
    所以只做已经确认过语义的对照表，不做猜测。
    """
    return fix(text)


def plain_output(fn: Callable[..., str]) -> Callable[..., str]:
    """渲染函数装饰器：返回值强制过一遍 finalize。

    加在每个 render() 上，等于把这条规则钉在产品代码里——以后谁往里加一句
    生造词，输出也会被换掉，不靠人记得跑检查。
    """
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        out = fn(*args, **kwargs)
        if not isinstance(out, str):
            return out
        fixed, _hits = finalize(out)
        return fixed
    return wrapper


def check(text: str) -> Dict[str, Any]:
    """给 CLI / MCP 用的统一结果。ok=False 表示有过得去的问题。"""
    hits = find(text)
    return {
        "rule": RULE,
        "ok": not hits,
        "count": len(hits),
        "findings": hits,
        "hint": ("把「" + "」「".join(sorted({h['term'] for h in hits})) + "」换成日常说法；"
                 "换完自己重读一遍，机器只做机械替换。") if hits else "",
    }


def render(result: Dict[str, Any]) -> str:
    """人读的检查结果。没有问题时也要说清楚检查了什么。"""
    lines: List[str] = []
    if result["ok"]:
        lines.append("没有生造词。")
        lines.append("规则：" + result["rule"])
        return "\n".join(lines)
    lines.append(f"{result['count']} 处生造词：")
    for h in result["findings"]:
        lines.append(f"  - 第 {h['line']} 行「{h['term']}」→ 建议「{h['replace_with']}」"
                     f"（…{h['context']}…）")
    lines.append("")
    lines.append("规则：" + result["rule"])
    lines.append(result["hint"])
    return "\n".join(lines)
