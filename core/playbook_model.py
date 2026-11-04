#!/usr/bin/env python3
"""Platform × Motion × Vertical 的可执行 Playbook 组合器。

平台提供规则与执行参数，Motion 提供增长方法，Vertical 提供价值与承接约束。
组合不生成新的发送通道，也不把平台注册误写成执行已验证。
"""
from __future__ import annotations

from typing import Any, Dict

from .platforms import BY_ID

MOTIONS: Dict[str, Dict[str, Any]] = {
    "community_answer": {"label": "社区答疑", "artifact": "可复现解决方案",
                         "observation": "有效追问与真实使用"},
    "content_education": {"label": "内容教育", "artifact": "教程/样例",
                          "observation": "复现与激活"},
    "direct_outreach": {"label": "直接获客", "artifact": "针对性问题解决方案",
                        "observation": "有效对话与后续跟进"},
    "launch": {"label": "产品发射", "artifact": "发布包与真实演示",
               "observation": "真实讨论与试用"},
    "partnership": {"label": "合作传播", "artifact": "联合案例/上游交付",
                    "observation": "合作方采用与持续关系"},
    "crowdfunding": {"label": "众筹", "artifact": "预售页、交付计划与风险说明",
                     "observation": "真实支持与履约"},
    "seo_directory": {"label": "搜索与目录", "artifact": "问题解答页与产品事实页",
                      "observation": "搜索进入后的激活"},
    "private_followup": {"label": "私域跟进", "artifact": "个性化后续材料",
                         "observation": "持续对话与复购/复用"},
}

VERTICALS: Dict[str, Dict[str, Any]] = {
    "devtool": {"label": "开发者工具", "value": "可复现技术结果与最小示例",
                "proof": "测试、日志、开源样例"},
    "b2b_saas": {"label": "B2B SaaS", "value": "业务任务完成与财务/运营证据",
                 "proof": "业务结果、成本与验收"},
    "consumer": {"label": "消费应用", "value": "用户任务与使用体验",
                 "proof": "真实使用与留存"},
    "hardware": {"label": "硬件", "value": "真实场景可用性与交付",
                 "proof": "实物测试、履约与售后"},
}


def compose(platform_id: str, motion: str, vertical: str) -> Dict[str, Any]:
    if platform_id not in BY_ID:
        raise ValueError(f"未知平台: {platform_id}")
    if motion not in MOTIONS:
        raise ValueError(f"未知 Motion: {motion}（可选 {sorted(MOTIONS)}）")
    if vertical not in VERTICALS:
        raise ValueError(f"未知 Vertical: {vertical}（可选 {sorted(VERTICALS)}）")
    platform = BY_ID[platform_id]
    m, v = MOTIONS[motion], VERTICALS[vertical]
    transport = platform.get("write")
    return {
        "schema_version": "1.0", "kind": "growovo.playbook",
        "platform": {"id": platform_id, "name": platform["name"], "kind": platform["kind"]},
        "motion": {"id": motion, **m},
        "vertical": {"id": vertical, **v},
        "account": platform.get("account", []),
        "residency": platform.get("residency", 0),
        "cold_start": platform.get("stages", []),
        "scale": platform.get("scale", {}),
        "risk": platform.get("risks", []),
        "content_tier": platform.get("tier"),
        "execution": {
            "transport": transport or "manual",
            "verification": "注册不等于执行已验证；按具体动作单独验收",
            "requires_approval": True,
            "unknown_result": "needs_review，禁止自动重发",
        },
        "boundary": "组合是候选工作流，不证明渠道有效、平台许可或商业结果。",
    }


def render(playbook: Dict[str, Any]) -> str:
    p, m, v = playbook["platform"], playbook["motion"], playbook["vertical"]
    lines = [f"# Playbook · {p['name']} × {m['label']} × {v['label']}", "",
             f"价值资产：{m['artifact']}；验证：{v['proof']}。",
             f"观察：{m['observation']}。", "",
             "## 开跑前提"]
    lines.extend(f"- {x}" for x in playbook["account"])
    lines.extend(["", "## 执行方式",
                  f"- {playbook['execution']['transport']}",
                  f"- {playbook['execution']['verification']}",
                  f"- {playbook['execution']['unknown_result']}", "",
                  f"> {playbook['boundary']}"])
    return "\n".join(lines)
