#!/usr/bin/env python3
"""复盘分析：把扫描出来的事实，算成渠道账、浪费账、节奏账，并给出可执行的判断。

输入是 `core/logscan` 的 claims（带出处），输出是可直接写进复盘报告的结论：
  每个渠道：做了多少、回来多少、花在哪、值不值
  浪费清单：动作不小但产出为零的渠道
  冲突清单：同一件事在日志里有两个数字（复盘必须先承认这一点）
  每条结论都带 evidence（文件名:行号），不允许"感觉"。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# 渠道中文名，报告里不出现内部 id
CHANNEL_NAMES = {
    "x": "X", "email": "邮件", "github": "GitHub", "reddit": "Reddit",
    "hn": "Hacker News", "producthunt": "Product Hunt", "juejin": "掘金",
    "devto": "dev.to", "wechat": "公众号", "xiaohongshu": "小红书", "zhihu": "知乎",
    # 子渠道：同一个平台里打法不同、效率差一个量级的，必须拆开算，
    # 否则平均值会把有效打法一起埋掉。
    "email_1to1": "邮件·一对一求评", "email_batch": "邮件·批量",
    "x_deep": "X·深度对话", "x_cold": "X·冷触达",
}

METRIC_NAMES = {
    "action": "动作", "response": "回应", "outcome": "结果", "failure": "事故",
}


def _channel_name(cid: str) -> str:
    if cid in CHANNEL_NAMES:
        return CHANNEL_NAMES[cid]
    from .platforms import BY_ID
    return BY_ID.get(cid, {}).get("name", cid)


def consolidate(claims: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """按渠道聚合。每个指标取日志中报告的最大值，并保留全部不同取值做冲突提示。"""
    out: Dict[str, Dict[str, Any]] = {}
    for c in claims:
        ch = c["channel"]
        row = out.setdefault(ch, {"channel": ch, "name": _channel_name(ch),
                                  "values": {}, "sources": set()})
        row["sources"].add(f"{c['file']}:{c['line']}")
        row["values"].setdefault(c["metric"], []).append(float(c["value"]))

    from collections import Counter
    result: Dict[str, Dict[str, Any]] = {}
    for ch, row in out.items():
        metrics: Dict[str, Any] = {}
        for metric, vals in row["values"].items():
            counts = Counter(round(v, 4) for v in vals)
            # 取「被反复提到的那个数」，而不是最大值——最大值永远是噪声。
            value, support = counts.most_common(1)[0]
            distinct = sorted(counts)
            metrics[metric] = {
                "value": value,
                "support": support,
                "distinct": distinct[:6],
                "mentions": len(vals),
                "conflicting": len(distinct) > 1,
            }
        result[ch] = {"channel": ch, "name": row["name"], "metrics": metrics,
                      "sources": sorted(row["sources"])}
    return result


def rates(consolidated: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """算渠道效率：每 100 次动作带来多少回应/结果。"""
    rows = []
    for ch, row in consolidated.items():
        m = row["metrics"]
        actions = m.get("action", {}).get("value")
        responses = m.get("response", {}).get("value")
        outcomes = m.get("outcome", {}).get("value")
        failures = m.get("failure", {}).get("value")
        per100 = round(responses / actions * 100, 2) if actions and responses is not None else None
        out100 = round(outcomes / actions * 100, 2) if actions and outcomes is not None else None
        rows.append({
            "channel": ch, "name": row["name"],
            "support": m.get("action", {}).get("support"),
            "actions": actions, "responses": responses,
            "outcomes": outcomes, "failures": failures,
            "response_per_100": per100, "outcome_per_100": out100,
            "sources": row["sources"],
        })
    rows.sort(key=lambda r: (-(r["actions"] or 0)))
    return rows


def waste(rows: List[Dict[str, Any]], min_actions: float = 20) -> List[Dict[str, Any]]:
    """浪费：动作量不小，但没有任何结果。"""
    out = []
    for r in rows:
        if r["actions"] and r["actions"] >= min_actions and not r["outcomes"]:
            out.append({
                "channel": r["channel"], "name": r["name"],
                "actions": r["actions"], "responses": r["responses"], "outcomes": 0,
                "sources": r.get("sources", [])[:2],
                "why": f"投入 {int(r['actions'])} 次动作，日志中没有找到任何结果产出",
            })
    return out


def conflicts(consolidated: Dict[str, Dict[str, Any]],
              only_channels: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """同一指标在日志里有多个数字——复盘必须先承认，再决定用哪个。"""
    keep = set(only_channels) if only_channels else None
    out = []
    for ch, row in consolidated.items():
        if keep is not None and ch not in keep:
            continue
        for metric, info in row["metrics"].items():
            big = [v for v in info["distinct"] if v >= 10]
            if info["conflicting"] and len(big) > 1:
                out.append({"channel": ch, "name": row["name"],
                            "metric": METRIC_NAMES.get(metric, metric),
                            "values": big})
    return out


def sources_of(consolidated: Dict[str, Dict[str, Any]], channel: str, limit: int = 3) -> List[str]:
    row = consolidated.get(channel, {})
    return (row.get("sources") or [])[:limit]


def timeline(claims: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """记录区间用 10%-90% 分位，避开被引用的规划日期（比如"2027 年目标"）。"""
    dates = sorted(c["date"] for c in claims if c.get("date"))
    if not dates:
        return {"start": None, "end": None, "days": 0, "active_days": 0}
    from datetime import date as _d
    lo, hi = dates[len(dates) // 10], dates[len(dates) * 9 // 10]
    return {"start": lo, "end": hi,
            "days": (_d.fromisoformat(hi) - _d.fromisoformat(lo)).days + 1,
            "active_days": len(set(dates)), "first_seen": dates[0], "last_seen": dates[-1]}


def analyze(claims: List[Dict[str, Any]]) -> Dict[str, Any]:
    cons = consolidate(claims)
    r = rates(cons)
    return {
        "channels": r,
        "waste": waste(r),
        "conflicts": conflicts(cons),
        "timeline": timeline(claims),
        "evidence_index": {ch: cons[ch]["sources"][:5] for ch in cons},
    }


def derive_rules(analysis: Dict[str, Any]) -> List[Dict[str, str]]:
    """从数据里推出「下次怎么做」的规则，每条都挂在数字上。"""
    rules: List[Dict[str, str]] = []
    rows = [x for x in analysis["channels"]
            if x["response_per_100"] is not None and (x.get("actions") or 0) >= 30]
    if rows:
        best = max(rows, key=lambda x: x["response_per_100"] or 0)
        worst = min(rows, key=lambda x: x["response_per_100"] or 0)
        if best["channel"] != worst["channel"]:
            rules.append({
                "rule": f"把产能从{worst['name']}挪到{best['name']}",
                "basis": (f"{best['name']} 每 100 次动作带来 {best['response_per_100']} 次回应，"
                          f"{worst['name']} 只有 {worst['response_per_100']} 次"),
            })
    for w in analysis["waste"][:3]:
        rules.append({
            "rule": f"停掉{w['name']}上的批量动作，直到有明确机制为止",
            "basis": w["why"],
        })
    if analysis["conflicts"]:
        c = analysis["conflicts"][0]
        rules.append({
            "rule": f"复盘前先统一定义：{c['name']}的{c['metric']}到底按哪个口径算",
            "basis": f"日志里同时出现 {c['values']}，不统一就没法比较渠道",
        })
    return rules


def evidence_pool(claims, per_channel: int = 6, min_value: float = 10):
    """按渠道把「日志里真正在说事的那句话」摊开，而不是把孤立数字摊开。

    为什么要回到整句：同一句「199 条冷触达，4 天 0 star」在几份复盘里重复出现，
    它本身就是结论；而把「199」抽出来单独排序，只会让「1.2 节」的编号混进来。
    所以这里返回句子 + 出处 + 重复次数，交给读的人判断。
    """
    from collections import Counter, defaultdict

    seen = Counter()
    meta = {}
    for c in claims:
        sent = (c.get("sentence") or "").strip()
        if not sent:
            continue
        if c["metric"] == "action" and c["value"] < min_value:
            continue
        key = (c["channel"], c["metric"], sent)
        seen[key] += 1
        meta.setdefault(key, c.get("file", ""))

    grouped = defaultdict(list)
    for (ch, metric, sent), n in seen.most_common():
        grouped[ch].append({"metric": metric, "sentence": sent, "support": n,
                            "file": meta[(ch, metric, sent)]})

    out = []
    for ch, items in grouped.items():
        # 同一句话只留一次；优先保留重复次数高的
        uniq, seen_sent = [], set()
        for it in items:
            if it["sentence"] in seen_sent:
                continue
            seen_sent.add(it["sentence"])
            uniq.append(it)
        if uniq:
            out.append({"channel": ch, "name": _channel_name(ch), "items": uniq[:per_channel],
                        "volume": sum(i["support"] for i in uniq)})
    out.sort(key=lambda r: -r["volume"])
    return out


def rates_from_facts(facts):
    """facts 是人工钉住的口径：{渠道: {动作, 回应, 结果}}，键用中文或英文都行。"""
    alias = {"动作": "actions", "回应": "responses", "结果": "outcomes",
             "actions": "actions", "responses": "responses", "outcomes": "outcomes"}
    rows = []
    for ch, m in facts.items():
        norm = {}
        for k, v in (m or {}).items():
            if k in alias and isinstance(v, (int, float)):
                norm[alias[k]] = float(v)
        a = norm.get("actions")
        r = norm.get("responses")
        o = norm.get("outcomes")
        rows.append({
            "channel": ch, "name": CHANNEL_NAMES.get(ch, ch),
            "actions": a, "responses": r, "outcomes": o,
            "response_per_100": round(r / a * 100, 2) if a and r is not None else None,
            "outcome_per_100": round(o / a * 100, 2) if a and o is not None else None,
            "sources": [f"人工钉定的口径（{ch}）"],
        })
    rows.sort(key=lambda x: -((x["response_per_100"] or 0)))
    return rows


def wins(analysis):
    """做对了什么：有效率、有量、能复制的动作。复盘不能只列错误。"""
    out = []
    for r in analysis.get("channels", []):
        a = r.get("actions") or 0
        resp = r.get("response_per_100")
        outc = r.get("outcome_per_100")
        if a >= 20 and outc is not None and outc >= 20:
            out.append({
                "channel": r["channel"], "name": r["name"],
                "why": (f"{int(a)} 次动作产出 {r.get('outcomes')} 个可核验结果"
                        f"（每百次 {outc}），是唯一有硬产出的做法"),
                "kind": "产出率",
            })
        elif a >= 20 and resp is not None and resp >= 3:
            out.append({
                "channel": r["channel"], "name": r["name"],
                "why": (f"{int(a)} 次动作换回 {r.get('responses')} 次回应（每百次 {resp}），"
                        "有效率和量都在，可以复用"),
                "kind": "回应率",
            })
        elif 0 < a < 20 and (r.get("responses") or 0) > 0:
            out.append({
                "channel": r["channel"], "name": r["name"],
                "why": (f"只跑了 {int(a)} 次，{int(r.get('responses'))} 次有回应——"
                        "样本小但方向对，值得放大验证"),
                "kind": "小样本",
            })
    return out
