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
    "external_baseline": "已验证·外部基准",
    "hypothesis": "假设·待测",
}

# 平台注册表：手册/风控卡的知识索引。motions 的每条都必须带 label + evidence，
# 与 docs/cases 的案例互链；冷启动与放量判据同样挂标签。
PLATFORMS: List[Dict[str, Any]] = [
    {
        "id": "github",
        "name": "GitHub",
        "icp_tags": ["developers", "oss-maintainers", "technical-founders"],
        "product_types": ["devtool", "oss", "sdk", "api"],
        "channel_status": "PROVEN",
        "motions": [
            {"motion": "上游交付链：审计→issue→PR→合并", "label": "self_ab",
             "evidence": "Verovo 全周期复盘 2026-10-31：唯二可归因自然星均来自交付链"},
            {"motion": "仓库 README 问题钩子 + 一键安装", "label": "hypothesis",
             "evidence": "Askovo P0 在跑（a6055ad），D14=2026-11-14 回填"},
            {"motion": "买星 / 拉票式求 star", "label": "self_incident",
             "evidence": "Verovo：92+42+~13 购入星污染全部归因，铁律「不刷量永久」",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "README 首屏：一句话定位 + 问题钩子 + 一行安装",
                "issue 入口留「免费任务/反馈」通道",
                "上游交付链首批 5 个候选仓库（审计→有价值 issue）",
            ],
            "expected": "自然 star 个位数/周 [已验证·自家系数：交付链 ~1–3 star/周，Verovo 2026-10-31]",
        },
        "scale_gate": "连续 2 周 star 净增 ≥10 且有人带真实材料进仓 → 才允许加投入",
        "risk": "低-中（API 限额与 spam 判定见 docs/risk/github.md）",
    },
    {
        "id": "hackernews",
        "name": "Hacker News",
        "icp_tags": ["developers", "technical-founders", "researchers"],
        "product_types": ["devtool", "oss", "sdk", "api", "research"],
        "channel_status": "PROVEN（只读）/ UNVERIFIED（写）",
        "motions": [
            {"motion": "Show HN（自有项目，人审零 LLM 底线）", "label": "hypothesis",
             "evidence": "Askovo P0 备稿在等发布窗口；首例数据 D14 回填"},
            {"motion": "机打内容 / LLM 生成文本直接提交", "label": "self_incident",
             "evidence": "Verovo Show HN 被分类器判 genAI kill（2026-10）→ 红线「零 LLM、只读、不拉票」",
             "killed": True},
            {"motion": "目标客群语料挖掘（Algolia 只读）", "label": "external_baseline",
             "evidence": "Askovo P0：8,235 条语料→478 候选的可复现管线（2026-10-31）"},
        ],
        "cold_start": {
            "checklist": [
                "标题 ≤80 字符，Show HN 限自有项目",
                "发布窗口：周二–周四 美东上午",
                "发布后 1h 内跟首评（设计动机/数据）",
            ],
            "expected": "launch 脉冲量级，方差极大 [假设·待测：本项目未验证]",
        },
        "scale_gate": "Show HN 进前 30 或带 ≥10 个真实讨论 → 才规划第二次发射",
        "risk": "中（genAI 分类器与社区旗帜见 docs/risk/hackernews.md）",
    },
    {
        "id": "product-hunt",
        "name": "Product Hunt",
        "icp_tags": ["technical-founders", "designers", "indie-makers"],
        "product_types": ["product", "devtool", "saas"],
        "channel_status": "UNVERIFIED",
        "motions": [
            {"motion": "首发 launch（含 maker 故事页）", "label": "external_baseline",
             "evidence": "行业先例：launch 单次可带数百至数千 star（Verovo 总控 v1.1 引用，未亲核明细）"},
            {"motion": "拉票 / 激励投票", "label": "external_baseline",
             "evidence": "平台规则明令禁止；拉票是 Growovo 永久红线",
             "killed": True},
        ],
        "cold_start": {
            "checklist": [
                "产品页：30s 演示 GIF + 首图 + 3 张真实案例卡",
                "maker 页与关系披露",
                "launch 日全天在线答评",
            ],
            "expected": "脉冲型流量，首位转化待测 [假设·待测：本项目未验证]",
        },
        "scale_gate": "launch 日进品类前 10 → 才规划 PH 二次曝光或跨站同步",
        "risk": "中（拉票检测与关联账号判定见 docs/risk/product-hunt.md）",
    },
]

REQUIRED_FACTS = ("product", "icp", "goals", "value_events", "constraints", "owned_assets")
REQUIRED_PRODUCT = ("name", "what", "url", "stage")
BANDS = ((5, "P0"), (3, "P1"), (1, "P2"))


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
    if not facts["goals"]:
        raise ValueError("goals 不能为空：没有目标数字不谈策略")


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
        })
    rows.sort(key=lambda r: (-r["score"], r["id"]))

    unresolved: List[str] = []
    if conf["level"] == "低":
        unresolved.append("ICP 假设全部或大部分未经验证——首轮目标应设为「判据收集」而非放量，先跑 20–40 个触达过判定线")
    if any(ev["is_approximation"] for ev in facts["value_events"]):
        unresolved.append("存在近似口径的价值事件：报告中已标注，不得当作精确数引用")
    if not any(r["priority"] == "P0" for r in rows):
        unresolved.append("没有平台够到 P0：ICP tags 与注册表无交集，先补 ICP 研究再诊断")

    return {
        "product": facts["product"],
        "goals": facts["goals"],
        "confidence": conf,
        "platforms": rows,
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
    lines.append(f"> 策略置信度：**{conf['level']}**（ICP 假设 {int(conf['verified_share']*100)}% 已验证）")
    lines.append("")
    lines.append("## 平台优先级")
    lines.append("")
    lines.append("| 平台 | 优先级 | 匹配依据 | 渠道状态 |")
    lines.append("|---|---|---|---|")
    for r in payload["platforms"]:
        basis = "、".join(r["matched_tags"]) or "（无 tag 交集）"
        if r["score"] and not r["matched_tags"]:
            basis += " 类型/自有资产"
        lines.append(f"| {r['name']} | {r['priority']} | {basis} | {r['channel_status']} |")
    lines.append("")
    for r in payload["platforms"]:
        if r["priority"] == "—":
            continue
        lines.append(f"## {r['name']}（{r['priority']}）")
        lines.append("")
        lines.append("### 获客动作矩阵（每条带证据标签）")
        lines.append("")
        lines.append("| 动作 | 证据 | 出处 |")
        lines.append("|---|---|---|")
        for m in r["motions"]:
            lines.append(f"| {m['motion']} | {LABELS[m['label']]} | {m['evidence']} |")
        if r["killed_motions"]:
            lines.append("")
            lines.append("**判死动作（不做）**：")
            for m in r["killed_motions"]:
                lines.append(f"- ~~{m['motion']}~~ —— {LABELS[m['label']]}：{m['evidence']}")
        lines.append("")
        lines.append("### 冷启动清单")
        lines.append("")
        for item in r["cold_start"]["checklist"]:
            lines.append(f"- [ ] {item}")
        lines.append(f"- 预期区间：{r['cold_start']['expected']}")
        lines.append("")
        lines.append(f"### 放量判据（预注册）\n\n{r['scale_gate']}")
        lines.append("")
        lines.append(f"风控：{r['risk']}")
        lines.append("")
    lines.append("## 价值事件口径")
    lines.append("")
    for ev in payload["value_events"]:
        approx = "（**近似口径，已标注**）" if ev["is_approximation"] else ""
        lines.append(f"- **{ev['name']}**：来源 {ev['source']}{approx}")
    lines.append("")
    if payload["unresolved"]:
        lines.append("## 未决与诚实声明")
        lines.append("")
        for u in payload["unresolved"]:
            lines.append(f"- {u}")
        lines.append("")
    lines.append("## 下一步")
    lines.append("")
    lines.append("1. 人审本报告（每条推荐已挂证据标签；假设级条目按「待测」管理）")
    lines.append("2. `mkt plan new` 建首个 campaign plan，size 不超内核日预算")
    lines.append("3. 冷启动清单执行；复盘用 `mkt gtm retro`（W2 上线）回流本报告")
    lines.append("")
    return "\n".join(lines)
