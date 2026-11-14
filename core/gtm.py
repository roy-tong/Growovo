#!/usr/bin/env python3
"""GTM diagnosis engine (product strategy v2, module M1).

Turns a product facts pack into a platform-priority GTM report. Read-only:
no model calls, no network, no writes. Design constraints from the strategy
doc (ai输出/Growovo-产品策略v2-七步闭环-20261103.md):

- every recommendation line must carry an evidence label (no naked advice);
- platform knowledge lives in a registry whose entries cite their sources;
- value events must be defined up front, approximations flagged explicitly;
- the report is a scaffold for judgment, not a promise of outcomes.

Facts pack example (JSON)::

    {
      "product": {"name": "x", "what": "…", "url": "…", "stage": "launched",
                  "product_type": "devtool"},
      "icp": [{"hypothesis": "…", "tags": ["developers"],
               "evidence_label": "hypothesis", "evidence": "…"}],
      "goals": [{"layer": "G2", "metric": "激活", "target": 20}],
      "value_events": [{"name": "激活", "source": "产品内事件",
                        "is_approximation": false}],
      "constraints": {"red_lines": ["不做群发"], "human_minutes_budget": 720},
      "owned_assets": {"platforms": ["github"]}
    }

Evidence labels: ``self_ab`` (own controlled comparison), ``self_incident``
(own incident, usually negative evidence), ``external_baseline`` (third-party
verified numbers), ``hypothesis`` (untested, must be labeled as such).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

LABELS = {
    "self_ab": "已验证·自家A/B",
    "self_incident": "已验证·自家事故",
    "self_research": "已验证·自家研究",
    "external_baseline": "已验证·外部基准",
    "hypothesis": "假设·待测",
}

# 对外报告一律用大白话，不出现内部编码。LABELS 只留在数据结构与测试里。
LABELS_PLAIN = {
    "self_ab": "我们自己对比过",
    "self_incident": "我们自己踩过坑",
    "self_research": "我们自己研究过",
    "external_baseline": "别人公开的数据",
    "hypothesis": "还没验证",
}

# 人群标签的中文说法——"indie-makers" 这种词不该出现在给客户看的报告里。
TAG_LABELS = {
    "b2b-buyers": "企业采购",
    "consumers": "普通消费者",
    "designers": "设计师",
    "developers": "开发者",
    "indie-makers": "独立开发者 / 创作者",
    "oss-maintainers": "开源维护者",
    "product-managers": "产品经理",
    "researchers": "研究者",
    "students": "学生",
    "technical-founders": "技术出身的创始人",
}

CONFIDENCE_PLAIN = {"高": "有把握", "中": "一半有实据", "低": "主要靠判断"}

# 报告是对外交付物：出现在里面的编码一律翻成大白话。
_PLAIN_RULES = (
    ("[已验证·自家A/B", "[我们自己对比过"),
    ("[已验证·自家事故", "[我们自己踩过坑"),
    ("[已验证·自家研究", "[我们自己研究过"),
    ("[已验证·自家", "[自家"),
    ("[已验证·外部基准", "[别人公开的数据"),
    ("[假设·待测", "[还没验证"),
    ("已验证·自家A/B", "我们自己对比过"),
    ("已验证·自家事故", "我们自己踩过坑"),
    ("已验证·自家研究", "我们自己研究过"),
    ("已验证·外部基准", "别人公开的数据"),
    ("假设·待测", "还没验证"),
    ("PROVEN（只读）/ UNVERIFIED（写）", "读取已验证 / 写入未验证"),
    ("半自动（UNVERIFIED）", "半自动，未验证"),
    ("PROVEN", "已验证可用"),
    ("UNVERIFIED", "未验证"),
)


def _plain(text: Any) -> str:
    out = str(text)
    for code, spoken in _PLAIN_RULES:
        out = out.replace(code, spoken)
    return out

# 优先级也只对内讲，对外说人话。
PRIORITY_PLAIN = {"P0": "优先", "P1": "备选", "P2": "暂不考虑", "—": "不适用"}

# 平台注册表：docs/playbooks/ 手册库的结构化投影。motions 每条必须带 label + evidence，
# 与 docs/cases 案例互链；cold_start/scale_gate 同样挂标签；risk 指向手册或 docs/risk/。
# 手册是活文档：打法被实测推翻时先改手册，再同步本表。
from .platforms import PLATFORMS  # 单一数据源：core/platforms.py
from .platforms import BY_ID as PLATFORM_BY_ID
REQUIRED_FACTS = ("product", "icp", "value_events", "constraints", "owned_assets")
REQUIRED_PRODUCT = ("name", "what", "url", "stage")
BANDS = ((5, "P0"), (3, "P1"), (1, "P2"))

# 一次性集中发布：带来曝光与反馈，不是可重复的获客渠道。
# 研究结论（2026-11-03）：一个小团队一次只够把一条渠道做完，主渠道不能选集中发布。
LAUNCH_PLATFORMS = {"product-hunt", "hackernews", "kickstarter", "instagram"}


def _validate_facts(facts: Dict[str, Any]) -> None:
    for key in REQUIRED_FACTS:
        if key not in facts:
            raise ValueError(f"facts 缺少必需字段: {key}")
    for key in REQUIRED_PRODUCT:
        if not facts["product"].get(key):
            raise ValueError(f"product.{key} 不能为空")
    if not facts["icp"]:
        raise ValueError("icp 不能为空：没有 ICP 假设就无法做平台匹配")
    for i, hyp in enumerate(facts["icp"]):
        if hyp.get("evidence_label") not in LABELS:
            raise ValueError(f"icp[{i}].evidence_label 必须是 {sorted(LABELS)} 之一")
        if not hyp.get("evidence"):
            raise ValueError(f"icp[{i}].evidence 缺失：每个假设必须挂出处")
    if not facts["value_events"]:
        raise ValueError("value_events 不能为空：开工前必须定义价值事件（激活/付费）")
    for i, ev in enumerate(facts["value_events"]):
        if not ev.get("source"):
            raise ValueError(f"value_events[{i}].source 缺失：价值事件必须写明数据来源")
        if not isinstance(ev.get("is_approximation"), bool):
            raise ValueError(f"value_events[{i}].is_approximation 必须显式 true/false")
    # goals 是可选业务意图/学习问题，不再用预注册数字限制策略探索。
    if "goals" in facts and not isinstance(facts["goals"], list):
        raise ValueError("goals 必须为数组（可空：记录业务意图或学习问题）")


def _confidence(icp: List[Dict[str, Any]]) -> Dict[str, Any]:
    verified = [h for h in icp if h["evidence_label"] != "hypothesis"]
    share = round(len(verified) / len(icp), 2) if icp else 0.0
    if share >= 0.66:
        level = "高"
    elif share >= 0.34:
        level = "中"
    else:
        level = "低"
    return {"level": level, "verified_share": share}


def _score(platform: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    tags: List[str] = []
    for hyp in facts["icp"]:
        tags.extend(hyp.get("tags", []))
    matched_tags = sorted(set(tags) & set(platform["icp_tags"]))
    # product_type 是可选维度：注册表按 product_types 匹配单一主类型
    ptype = facts["product"].get("product_type")
    type_match = ptype in platform["product_types"] if ptype else False
    owned = platform["id"] in facts["owned_assets"].get("platforms", [])
    score = 2 * len(matched_tags) + (2 if type_match else 0) + (1 if owned else 0)
    return {"score": score, "matched_tags": matched_tags, "type_match": type_match, "owned": owned}


def _priority(score: int) -> str:
    for floor, label in BANDS:
        if score >= floor:
            return label
    return "—"


def diagnose(facts: Dict[str, Any]) -> Dict[str, Any]:
    _validate_facts(facts)
    conf = _confidence(facts["icp"])
    rows = []
    for platform in sorted(PLATFORMS, key=lambda p: p["id"]):
        s = _score(platform, facts)
        alive = [m for m in platform["motions"] if not m.get("killed")]
        killed = [m for m in platform["motions"] if m.get("killed")]
        rows.append({
            "id": platform["id"],
            "name": platform["name"],
            "priority": _priority(s["score"]),
            "score": s["score"],
            "matched_tags": s["matched_tags"],
            "channel_status": platform["channel_status"],
            "motions": deepcopy(alive),
            "killed_motions": deepcopy(killed),
            "cold_start": deepcopy(platform["cold_start"]),
            "scale_gate": platform["scale_gate"],
            "risk": platform["risk"],
            "owned": s["owned"],
        })
    rows.sort(key=lambda r: (-r["score"], r["id"]))

    resolved = [r for r in rows if r["priority"] != "—"]
    # 排序：分数 → 自有账号（更便宜）→ 渠道已验证 → 名字，保证结果稳定可复现。
    ranked = sorted(
        resolved,
        key=lambda r: (-r["score"], 0 if r["owned"] else 1, 0 if "PROVEN" in r["channel_status"] else 1, r["id"]),
    )
    compound = [r for r in ranked if r["id"] not in LAUNCH_PLATFORMS]
    primary = compound[0] if compound else (ranked[0] if ranked else None)
    launch = next((r for r in ranked if r["id"] in LAUNCH_PLATFORMS), None)
    deferred = [
        r for r in ranked
        if r["priority"] == "P0" and r is not primary and (launch is None or r is not launch)
    ]
    focus = {
        "primary": primary["id"] if primary else None,
        "primary_name": primary["name"] if primary else None,
        "launch": launch["id"] if launch else None,
        "launch_name": launch["name"] if launch else None,
        "deferred": [r["name"] for r in deferred],
    }

    unresolved: List[str] = []
    if conf["level"] == "低":
        unresolved.append("ICP 假设全部或大部分未经验证——先明确要验证的不确定性与观察指标，再决定动作和样本，不设统一触达判定线")
    if any(ev["is_approximation"] for ev in facts["value_events"]):
        unresolved.append("存在近似口径的价值事件：报告中已标注，不得当作精确数引用")
    if not any(r["priority"] == "P0" for r in rows):
        unresolved.append("没有平台够到 P0：ICP tags 与注册表无交集，先补 ICP 研究再诊断")

    return {
        "product": facts["product"],
        "goals": facts.get("goals", []),
        "confidence": conf,
        "platforms": rows,
        "focus": focus,
        "value_events": facts["value_events"],
        "unresolved": unresolved,
    }


def markdown(payload: Dict[str, Any]) -> str:
    p = payload["product"]
    conf = payload["confidence"]
    lines: List[str] = []
    lines.append(f"# GTM 诊断报告 · {p['name']}")
    lines.append("")
    lines.append(f"> 产品：{p['what']}（{p['url']}，阶段 {p['stage']}）")
    goals = "；".join(f"{g['layer']} {g['metric']}={g['target']}" for g in payload["goals"])
    lines.append(f"> 目标：{goals}")
    lines.append(f"> 判断把握：**{CONFIDENCE_PLAIN.get(conf['level'], conf['level'])}**（目标人群假设里 {int(conf['verified_share']*100)}% 有实证）")
    lines.append("")
    focus = payload.get("focus") or {}
    if focus.get("primary_name"):
        lines.append("## 本轮怎么打")
        lines.append("")
        lines.append(f"- **主渠道（只打这一个）**：{focus['primary_name']}")
        primary_row = next((r for r in payload["platforms"] if r["id"] == focus["primary"]), None)
        if primary_row is not None:
            lines.append(f"  - 开跑前提：这个平台要有一个**能用的账号**（当前渠道状态：{_plain(primary_row['channel_status'])}）。没有账号就先补账号，别急着发。")
        if focus.get("launch_name"):
            lines.append(f"- **一次性集中发布渠道**：{focus['launch_name']}——只算曝光和反馈，不算获客")
        if focus.get("deferred"):
            names = "、".join(focus["deferred"])
            lines.append(f"- **也值得做、但这一轮不碰**：{names}——人手只够一条渠道，排到下一轮再评估")
        lines.append("")
        lines.append("> 判断依据：一个小团队一次只能把一条渠道做透。同时打三个平台，等于三个都没做起来——"
                     "这是研究里最一致的一条经验，也是「发布一次就没声音」的直接成因。")
        lines.append("")
    lines.append("## 各平台排位")
    lines.append("")
    lines.append("| 平台 | 这一轮的位置 | 为什么排在这 | 能不能直接做 |")
    lines.append("|---|---|---|---|")
    for r in payload["platforms"]:
        basis = "、".join(TAG_LABELS.get(t, t) for t in r["matched_tags"]) or "这一轮没有对得上的人群"
        if r["score"] and not r["matched_tags"]:
            basis += "（产品类型或已有账号对得上）"
        lines.append(f"| {r['name']} | {PRIORITY_PLAIN.get(r['priority'], r['priority'])} | {basis} | {_plain(r['channel_status'])} |")
    lines.append("")
    for r in payload["platforms"]:
        if r["priority"] == "—":
            continue
        lines.append(f"## {r['name']}（{PRIORITY_PLAIN.get(r['priority'], r['priority'])}）")
        lines.append("")
        lines.append("### 能做的事")
        lines.append("")
        lines.append("| 做法 | 凭什么这么说 | 出处 |")
        lines.append("|---|---|---|")
        for m in r["motions"]:
            lines.append(f"| {_plain(m['motion'])} | {LABELS_PLAIN[m['label']]} | {_plain(m['evidence'])} |")
        if r["killed_motions"]:
            lines.append("")
            lines.append("**不做什么（以及为什么）**：")
            for m in r["killed_motions"]:
                lines.append(f"- ~~{_plain(m['motion'])}~~ —— {LABELS_PLAIN[m['label']]}：{_plain(m['evidence'])}")
        lines.append("")
        lines.append("### 冷启动清单")
        lines.append("")
        for item in r["cold_start"]["checklist"]:
            lines.append(f"- [ ] {_plain(item)}")
        lines.append(f"- 预期区间：{_plain(r['cold_start']['expected'])}")
        lines.append("")
        lines.append(f"### 放量判据\n\n{_plain(r['scale_gate'])}")
        lines.append("")
        lines.append(f"风控：{_plain(r['risk'])}")
        lines.append("")
    lines.append("## 价值事件口径")
    lines.append("")
    for ev in payload["value_events"]:
        approx = "（**近似口径，已标注**）" if ev["is_approximation"] else ""
        lines.append(f"- **{ev['name']}**：来源 {ev['source']}{approx}")
    lines.append("")
    if payload["unresolved"]:
        lines.append("## 需要你知道的限制")
        lines.append("")
        for u in payload["unresolved"]:
            lines.append(f"- {_plain(u)}")
        lines.append("")
    lines.append("## 下一步")
    lines.append("")
    lines.append("1. 人审本报告：每条建议后面都写了凭什么，标「还没验证」的先小范围试，别直接放量")
    lines.append("2. `mkt plan new` 建首个 campaign plan，size 不超内核日预算")
    lines.append("3. 冷启动清单执行；复盘用 `mkt gtm retro`（W2 上线）回流本报告")
    lines.append("")
    return "\n".join(lines)
