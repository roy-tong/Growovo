#!/usr/bin/env python3
"""目标选择：先定这一轮到底要什么，再选能把它往前推的平台。

产品事实包只说明「你是谁、卖给谁」，不说明「这一轮要什么」。同一个产品，
追 star、追真实使用、追第一笔付费，是三套完全不同的动作、平台和判据。
本模块把这件事做成可选的目标目录：

  声量（awareness）  —— 有人看见、记住、收藏
  使用（adoption）   —— 有人真的装、跑、提 issue、交数据
  商业（commercial） —— 有人愿意谈、给样本、付钱

每个目标都带：指标定义、基线、目标形状、窗口、领先指标、反指标、
「证明什么 / 不证明什么」、止损线、人工成本。目标由人选，数值由人定；
这里只提供选项、平台匹配和可执行方案，不预测结果、不替人拍板。

设计约束：
- 目标不是 KPI 许愿池：没有基线的目标必须写明「基线未提供」，不编数；
- 平台匹配必须给出理由，且承认「受众标签不匹配」的情况；
- 一次最多 1 个主渠道 + 1 个发射点 + 2 个辅助平台，防止「什么都做」；
- 选择会写成一个契约文件，后续 strategy/cadence/result 都按它执行。
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import gtm
from .platforms import BY_ID as PLATFORM_BY_ID
from .platforms import PLATFORMS

SCHEMA = "growovo.goal-plan/1"
SELECTION_SCHEMA = "growovo.goal-selection/1"
MAX_SUPPORT = 2
MAX_PLATFORMS = 4
WINDOW_MIN, WINDOW_MAX = 7, 180

FAMILIES: Dict[str, Dict[str, str]] = {
    "awareness": {
        "name": "声量",
        "question": "有多少人看见、记住、愿意继续看到你",
        "proves": "有人看见并觉得值得收藏/关注",
        "does_not_prove": "有人需要、会用、会留下来或会付钱",
        "not_doing": "这 6 周不追付费转化，不把 star/粉丝当需求证据",
    },
    "adoption": {
        "name": "使用",
        "question": "有多少人真的装起来、跑起来、把问题带回来",
        "proves": "产品被真实使用，且使用中产生了可回看的信号",
        "does_not_prove": "愿意付费、会长期留存或市场足够大",
        "not_doing": "这 6 周不追规模曝光，不把注册数当激活",
    },
    "commercial": {
        "name": "商业",
        "question": "有多少人愿意谈、给数据、为结果付钱",
        "proves": "有人愿意为结果付出时间、数据或钱",
        "does_not_prove": "可复购、可规模化或单位经济成立",
        "not_doing": "这 6 周不追 star/粉丝，不把“聊得好”当成交",
    },
}

GENERIC_STOP_RULES = [
    "连续 2 周领先指标为 0：换平台或换话术，不加量、不加班",
    "出现平台处罚、删帖或限流：停该平台 7 天，先修内容再回来",
    "目标达成：先原样复现一次，再谈加量——一次达成可能是运气",
]
GENERIC_SWITCH_RULES = [
    "2 周后主指标仍为 0，但另一个目标出现真实信号：换目标，不换产品",
    "代价超过预算 2 倍：缩范围（少一个平台/少一半对象），不加预算",
]


def _goal(**kw: Any) -> Dict[str, Any]:
    spec: Dict[str, Any] = {
        "stages": ["beta", "launched", "growth"],
        "requires_platforms": [],
        "target_kind": "count",
        "window_days": 42,
        "requires_baseline": False,
        "cost_minutes": 480,
        "stop_rules": list(GENERIC_STOP_RULES),
        "switch_rules": list(GENERIC_SWITCH_RULES),
    }
    spec.update(kw)
    return spec


GOALS: List[Dict[str, Any]] = [
    _goal(
        id="awareness.stars",
        family="awareness",
        name="GitHub star 增长",
        metric="github_stars",
        metric_label="GitHub star",
        unit="颗",
        definition="仓库 star 数，按自然增长计；买量、互 star、抽奖换 star 不计。",
        product_types=["devtool", "oss", "sdk", "api"],
        requires_platforms=["github"],
        affinity={"github": 3, "hackernews": 2, "reddit": 2, "x": 1, "product-hunt": 1},
        target_kind="delta",
        default_target=50,
        proves="有人看见并认为值得收藏",
        does_not_prove="有人安装、使用、提交 issue 或付费",
        leading=["仓库访问量", "clone 数", "外部 issue/PR"],
        counter=["买星/互星", "抽奖换 star", "star 涨但 fork/issue 不动"],
        cost_minutes=480,
    ),
    _goal(
        id="awareness.followers",
        family="awareness",
        name="社媒关注者增长",
        metric="followers",
        metric_label="关注者",
        unit="人",
        definition="一个主账号的关注者数；买粉、互关、抽奖涨粉不计。",
        product_types=["devtool", "oss", "saas", "consumer-app", "product"],
        requires_platforms=["x"],
        affinity={"x": 3, "reddit": 2, "hackernews": 1, "linkedin": 1},
        target_kind="delta",
        default_target=200,
        proves="有人愿意持续看到你",
        does_not_prove="有人使用、留存或付费",
        leading=["单条曝光", "主页访问", "新增互动"],
        counter=["买粉", "互关", "抽奖涨粉"],
        cost_minutes=420,
    ),
    _goal(
        id="adoption.forks",
        family="adoption",
        name="外部 fork",
        metric="forks",
        metric_label="外部 fork",
        unit="个",
        definition="外部账号对仓库的 fork；自己开的小号、一次性 fork 不计。",
        product_types=["devtool", "oss", "sdk", "api"],
        requires_platforms=["github"],
        affinity={"github": 3, "hackernews": 1, "reddit": 1},
        target_kind="delta",
        default_target=10,
        proves="有人想改、想自己跑",
        does_not_prove="有人持续使用或付费",
        leading=["clone 数", "安装命令执行", "第一个外部 PR"],
        counter=["自己开小号 fork", "fork 后无提交"],
        cost_minutes=420,
    ),
    _goal(
        id="adoption.issues",
        family="adoption",
        name="外部 issue / 真实反馈",
        metric="external_issues",
        metric_label="外部 issue",
        unit="个",
        definition="非团队成员提交的 issue/讨论；自己提的、灌水的、只问价格的不计。",
        product_types=["devtool", "oss", "saas", "api", "sdk"],
        requires_platforms=["github"],
        affinity={"github": 3, "reddit": 2, "hackernews": 1},
        target_kind="delta",
        default_target=5,
        proves="有人真的用到了会出问题的地方",
        does_not_prove="问题普遍、需求成立或付费意愿",
        leading=["复现率", "首次响应时间", "外部 PR"],
        counter=["自己提的 issue", "灌水 issue", "只问价格不提问题"],
        cost_minutes=420,
    ),
    _goal(
        id="adoption.real_data",
        family="adoption",
        name="真实日志 / 数据提交",
        metric="real_data_submissions",
        metric_label="真实数据提交",
        unit="份",
        definition="有人把脱敏的真实日志、账单或导出交给你跑；公开样例、只截图不算。",
        product_types=["devtool", "service", "api", "sdk"],
        affinity={"github": 2, "reddit": 2, "hackernews": 1, "email": 1},
        target_kind="count",
        default_target=2,
        proves="有人愿意拿真实数据换结果",
        does_not_prove="愿意为完整任务付费，或数据可长期使用",
        leading=["脱敏样本数", "数据完整度", "第二次提交"],
        counter=["只给截图不给导出", "给的是公开样例", "提交后不再回复"],
        cost_minutes=600,
    ),
    _goal(
        id="adoption.activation",
        family="adoption",
        name="注册 → 激活",
        metric="activated_users",
        metric_label="激活用户",
        unit="人",
        definition="完成注册并走到产品内第一个价值点的独立用户；只注册不算。",
        product_types=["saas", "devtool", "consumer-app", "product"],
        affinity={"product-hunt": 2, "hackernews": 2, "x": 2, "reddit": 1},
        target_kind="count",
        default_target=20,
        proves="有人走到产品内第一个价值点",
        does_not_prove="留存、复购或付费",
        leading=["注册→首个关键动作", "首次成功任务", "7 日回访"],
        counter=["只注册不激活", "羊毛党", "一次性流量"],
        cost_minutes=480,
    ),
    _goal(
        id="commercial.conversations",
        family="commercial",
        name="合格买方对话",
        metric="qualified_conversations",
        metric_label="合格对话",
        unit="个",
        definition="对方有触发事件、能拿到数据、现有办法有成本、愿意聊——四条至少中三条。",
        product_types=["b2b-saas", "service", "devtool", "saas"],
        affinity={"linkedin": 3, "reddit": 3, "email": 2, "github": 2, "hackernews": 1},
        target_kind="count",
        default_target=3,
        proves="有人愿意花时间谈这个问题",
        does_not_prove="愿意付钱、预算存在或采购流程可走",
        leading=["回话人数", "愿给数据人数", "愿约 15 分钟人数"],
        counter=["礼貌性回复", "只问价格不谈问题", "同行/供应商来聊"],
        cost_minutes=600,
    ),
    _goal(
        id="commercial.first_paid",
        family="commercial",
        name="第一笔付费",
        metric="paid_deals",
        metric_label="付费客户",
        unit="个",
        definition="以到账为准；免费试用、关系单、降价才成交的要单独标注。",
        product_types=["b2b-saas", "service", "devtool", "saas"],
        affinity={"linkedin": 3, "reddit": 3, "email": 2, "github": 2, "hackernews": 1},
        target_kind="count",
        default_target=1,
        proves="有人愿意为结果付钱",
        does_not_prove="可复购、可规模化或单位经济成立",
        leading=["合格对话", "样本交付", "报价数"],
        counter=["免费试用当付费", "关系单", "降价才成交"],
        cost_minutes=840,
    ),
    _goal(
        id="commercial.repeat",
        family="commercial",
        name="复购 / 续费",
        metric="repeat_purchases",
        metric_label="复购/续费",
        unit="个",
        definition="同一客户第二次付费或续约；首次付费不算。",
        product_types=["b2b-saas", "service", "saas"],
        affinity={"email": 3, "linkedin": 2, "github": 1},
        target_kind="count",
        default_target=1,
        requires_baseline=True,
        baseline_from=["paid_deals"],
        proves="上一次交付产生了可重复的价值",
        does_not_prove="市场足够大或获客成本可接受",
        leading=["上期客户回访", "第二期启动时间", "转介绍"],
        counter=["关系续费", "降价续费", "只续一次"],
        cost_minutes=360,
    ),
]

BY_ID: Dict[str, Dict[str, Any]] = {g["id"]: g for g in GOALS}


class GoalError(ValueError):
    """目标/平台选择不成立。"""


def get(goal_id: str) -> Dict[str, Any]:
    spec = BY_ID.get(goal_id)
    if spec is None:
        raise GoalError(
            f"没有这个目标：{goal_id}；可选：{', '.join(sorted(BY_ID))}")
    return spec


def _tags(facts: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    for hyp in facts.get("icp", []):
        tags.extend(hyp.get("tags", []))
    return sorted(set(tags))


def _has_baseline(spec: Dict[str, Any], facts: Dict[str, Any]) -> bool:
    baseline = facts.get("baseline") or {}
    value = baseline.get(spec["metric"])
    try:
        if value is not None and float(value) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return any((baseline.get(k) or 0) > 0 for k in spec.get("baseline_from") or [])


def _baseline(facts: Dict[str, Any], metric: str) -> Optional[float]:
    raw = (facts.get("baseline") or {}).get(metric)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _target_value(spec: Dict[str, Any], facts: Dict[str, Any],
                  target: Optional[float]) -> Tuple[Optional[float], float, str]:
    """返回 (期末目标值, 使用的目标数, 人话目标)。"""
    value = float(spec["default_target"] if target is None else target)
    baseline = _baseline(facts, spec["metric"])
    if value <= 0:
        raise GoalError("目标必须大于 0")
    if spec.get("target_kind") == "delta":
        end = None if baseline is None else baseline + value
        if baseline is None:
            text = f"在现有基础上 +{value:g}（基线未提供，第一次跑完补）"
        else:
            text = f"从 {baseline:g} 到 {baseline + value:g}（+{value:g}）"
        return end, value, text
    text = f"≥ {value:g}" + ("（基线未提供）" if baseline is None else f"（当前 {baseline:g}）")
    return value, value, text


def _applicable(spec: Dict[str, Any], facts: Dict[str, Any]) -> Tuple[bool, str]:
    product = facts.get("product") or {}
    ptype = product.get("product_type")
    stage = product.get("stage")
    owned = set((facts.get("owned_assets") or {}).get("platforms") or [])
    requires = set(spec.get("requires_platforms") or [])

    if stage and spec.get("stages") and stage not in spec["stages"]:
        return False, f"产品阶段是 {stage}，这个目标更适合 {'/'.join(spec['stages'])}"

    if not ptype:
        if requires & owned:
            return True, f"产品类型未填，但你有 {'/'.join(sorted(requires & owned))} 账号/仓库"
        return True, "产品类型未填，先按通用目标处理；补上类型后会收窄"

    if ptype in spec.get("product_types", []):
        return True, f"产品类型是 {ptype}，这个目标对得上"

    if requires & owned:
        return True, (f"产品类型是 {ptype}，但你已有 "
                      f"{'/'.join(sorted(requires & owned))}，可以做这条线")

    return False, (f"产品类型是 {ptype}，这个目标通常不适用于它"
                   f"（适用：{'/'.join(spec.get('product_types', []))}）")


def _fit(spec: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, str]:
    ok, why = _applicable(spec, facts)
    if not ok:
        return {"level": "不适配", "why": why}
    baseline = _baseline(facts, spec["metric"])
    end, _, _ = _target_value(spec, facts, None)
    if spec.get("requires_baseline") and not _has_baseline(spec, facts):
        return {"level": "不适配",
                "why": "还没有基线（比如付费客户），先做上一个目标"}
    if end is not None and baseline is not None and baseline >= end:
        return {"level": "已达成", "why": f"当前 {baseline:g} 已达到目标，换下一个目标"}
    if spec["family"] == "commercial" and (facts.get("product") or {}).get("stage") in (
            "launched", "growth"):
        return {"level": "推荐", "why": why + "；产品已上线，先把愿意付钱的人找出来"}
    if spec["family"] == "adoption" and (facts.get("product") or {}).get(
            "product_type") in ("devtool", "oss", "sdk", "api"):
        return {"level": "推荐", "why": why + "；开发者产品先用真实使用证明价值"}
    return {"level": "可选", "why": why}


def list_goals(facts: Dict[str, Any]) -> Dict[str, Any]:
    """列出这个产品可选的若干目标；只读，不写任何东西。"""
    gtm._validate_facts(facts)
    rows: List[Dict[str, Any]] = []
    for spec in GOALS:
        fit = _fit(spec, facts)
        baseline = _baseline(facts, spec["metric"])
        end, target, text = _target_value(spec, facts, None)
        ranked = rank_platforms(spec, facts)
        primary = next((r for r in ranked if r["id"] not in gtm.LAUNCH_PLATFORMS), None)
        launch = next((r for r in ranked if r["id"] in gtm.LAUNCH_PLATFORMS), None)
        rows.append({
            "id": spec["id"],
            "family": spec["family"],
            "family_name": FAMILIES[spec["family"]]["name"],
            "name": spec["name"],
            "metric": spec["metric"],
            "metric_label": spec["metric_label"],
            "unit": spec["unit"],
            "definition": spec["definition"],
            "baseline": baseline,
            "target": target,
            "target_kind": spec["target_kind"],
            "target_text": text,
            "window_days": spec["window_days"],
            "fit": fit,
            "primary": {"id": primary["id"], "name": primary["name"]} if primary else None,
            "launch": {"id": launch["id"], "name": launch["name"]} if launch else None,
            "cost_minutes": spec["cost_minutes"],
            "proves": spec["proves"],
            "does_not_prove": spec["does_not_prove"],
        })
    order = {"推荐": 0, "可选": 1, "已达成": 2, "不适配": 3}
    rows.sort(key=lambda r: (order.get(r["fit"]["level"], 9),
                             r["family"], r["id"]))
    notes = []
    if not (facts.get("baseline") or {}):
        notes.append("没提供基线：目标值只是形状，不是预测；第一次跑完把真实数补进 baseline。")
    if not (facts.get("product") or {}).get("product_type"):
        notes.append("没填 product_type：平台匹配只能按已有账号判断，会偏宽。")
    return {
        "schema": "growovo.goal-options/1",
        "product": facts.get("product") or {},
        "families": FAMILIES,
        "goals": rows,
        "notes": notes,
        "boundary": ("目标是选择，不是承诺。任何一个目标达成，都不自动证明需求、"
                     "可复购或可规模化；换目标要重算，不要拿旧结果顶新目标。"),
    }


def rank_platforms(spec: Dict[str, Any], facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按「目标亲和度 × 受众匹配 × 已有账号 × 产品类型」给平台打分。"""
    tags = set(_tags(facts))
    owned = set((facts.get("owned_assets") or {}).get("platforms") or [])
    ptype = (facts.get("product") or {}).get("product_type")
    rows: List[Dict[str, Any]] = []
    for platform in PLATFORMS:
        affinity = int(spec["affinity"].get(platform["id"], 0))
        if affinity <= 0:
            continue
        audience = bool(tags & set(platform.get("icp_tags") or []))
        type_match = bool(ptype and ptype in (platform.get("product_types") or []))
        is_owned = platform["id"] in owned
        score = affinity * 3 + (2 if audience else 0) + (1 if type_match else 0) + (
            1 if is_owned else 0)
        rows.append({
            "id": platform["id"],
            "name": platform["name"],
            "kind": platform.get("kind", ""),
            "affinity": affinity,
            "audience_match": audience,
            "type_match": type_match,
            "owned": is_owned,
            "score": score,
            "channel_status": platform.get("channel_status", ""),
            "tier": platform.get("tier", ""),
            "residency": platform.get("residency", 0),
            "account": list(platform.get("account") or []),
        })
    rows.sort(key=lambda r: (-r["score"], -r["affinity"], 0 if r["owned"] else 1, r["id"]))
    return rows


def _recommended(ranked: List[Dict[str, Any]]) -> Dict[str, Any]:
    non_launch = [r for r in ranked if r["id"] not in gtm.LAUNCH_PLATFORMS]
    launches = [r for r in ranked if r["id"] in gtm.LAUNCH_PLATFORMS]
    primary = non_launch[0] if non_launch else None
    launch = launches[0] if launches else None
    support = [r for r in non_launch[1:1 + MAX_SUPPORT]]
    return {"primary": primary, "launch": launch, "support": support}


def _assign_roles(ranked: List[Dict[str, Any]],
                  platform_ids: List[str]) -> Dict[str, Any]:
    by_id = {r["id"]: r for r in ranked}
    if isinstance(platform_ids, str):
        platform_ids = [x.strip() for x in platform_ids.replace("，", ",").split(",")]
    ids = [str(x).strip() for x in platform_ids if str(x).strip()]
    if not ids:
        raise GoalError("至少选一个平台")
    if len(ids) != len(set(ids)):
        raise GoalError("平台不能重复选")
    if len(ids) > MAX_PLATFORMS:
        raise GoalError(f"最多选 {MAX_PLATFORMS} 个平台（1 主 + 1 发射 + {MAX_SUPPORT} 辅助）")
    unknown = [i for i in ids if i not in PLATFORM_BY_ID]
    if unknown:
        raise GoalError(f"没有这个平台：{', '.join(unknown)}")
    no_fit = [i for i in ids if i not in by_id]
    if no_fit:
        raise GoalError(
            f"这些平台对这个目标没有可验证的作用：{', '.join(no_fit)}；"
            f"可选：{', '.join(r['id'] for r in ranked)}")
    launches = [i for i in ids if i in gtm.LAUNCH_PLATFORMS]
    non_launch = [i for i in ids if i not in gtm.LAUNCH_PLATFORMS]
    if len(launches) > 1:
        raise GoalError("一次只选一个发射平台——发射不是渠道")
    if not non_launch:
        raise GoalError("发射平台不能单独作为主渠道；至少加一个可重复触达的平台")
    primary = non_launch[0]
    launch = launches[0] if launches else None
    support = [i for i in non_launch[1:]]
    return {"primary": by_id[primary], "launch": by_id[launch] if launch else None,
            "support": [by_id[i] for i in support]}


def _merge_pair_ranked(primary_rank: List[Dict[str, Any]],
                       secondary_rank: List[Dict[str, Any]],
                       primary_spec: Dict[str, Any],
                       secondary_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """两个目标的平台并集：主目标的平台排前，同时标出它服务哪个目标。"""
    by_id: Dict[str, Dict[str, Any]] = {}
    for row in primary_rank:
        item = dict(row)
        item["serves"] = [primary_spec["id"]]
        item["score"] = row["score"] + 1
        by_id[row["id"]] = item
    for row in secondary_rank:
        if row["id"] in by_id:
            if secondary_spec["id"] not in by_id[row["id"]]["serves"]:
                by_id[row["id"]]["serves"].append(secondary_spec["id"])
        else:
            item = dict(row)
            item["serves"] = [secondary_spec["id"]]
            by_id[row["id"]] = item
    return sorted(by_id.values(),
                  key=lambda r: (-r["score"], -r["affinity"],
                                 0 if r["owned"] else 1, r["id"]))


def _recommended_pair(merged: List[Dict[str, Any]],
                      primary_spec: Dict[str, Any]) -> Dict[str, Any]:
    non_launch = [r for r in merged if r["id"] not in gtm.LAUNCH_PLATFORMS]
    launches = [r for r in merged if r["id"] in gtm.LAUNCH_PLATFORMS]
    primary = next((r for r in non_launch if primary_spec["id"] in r["serves"]),
                   non_launch[0] if non_launch else None)
    launch = launches[0] if launches else None
    support = [r for r in non_launch if r is not primary][:MAX_SUPPORT]
    return {"primary": primary, "launch": launch, "support": support}


def _assign_roles_pair(merged: List[Dict[str, Any]], platform_ids: List[str],
                       primary_spec: Dict[str, Any]) -> Dict[str, Any]:
    by_id = {r["id"]: r for r in merged}
    if isinstance(platform_ids, str):
        platform_ids = [x.strip() for x in platform_ids.replace("，", ",").split(",")]
    ids = [str(x).strip() for x in platform_ids if str(x).strip()]
    if not ids:
        raise GoalError("至少选一个平台")
    if len(ids) != len(set(ids)):
        raise GoalError("平台不能重复选")
    if len(ids) > MAX_PLATFORMS:
        raise GoalError(f"最多选 {MAX_PLATFORMS} 个平台（1 主 + 1 发射 + {MAX_SUPPORT} 辅助）")
    unknown = [i for i in ids if i not in PLATFORM_BY_ID]
    if unknown:
        raise GoalError(f"没有这个平台：{', '.join(unknown)}")
    no_fit = [i for i in ids if i not in by_id]
    if no_fit:
        raise GoalError(
            f"这些平台对这两个目标都没有可验证的作用：{', '.join(no_fit)}；"
            f"可选：{', '.join(r['id'] for r in merged)}")
    launches = [i for i in ids if i in gtm.LAUNCH_PLATFORMS]
    non_launch = [i for i in ids if i not in gtm.LAUNCH_PLATFORMS]
    if len(launches) > 1:
        raise GoalError("一次只选一个发射平台——发射不是渠道")
    if not non_launch:
        raise GoalError("发射平台不能单独作为主渠道；至少加一个可重复触达的平台")
    primary = non_launch[0]
    if primary_spec["id"] not in by_id[primary].get("serves", []):
        raise GoalError(f"主渠道 {by_id[primary]['name']} 只服务辅目标，不服务主目标；"
                        "换一个主渠道，或把主目标/辅目标对调")
    launch = launches[0] if launches else None
    support = [i for i in non_launch[1:]]
    return {"primary": by_id[primary], "launch": by_id[launch] if launch else None,
            "support": [by_id[i] for i in support]}


def _contract(spec: Dict[str, Any], facts: Dict[str, Any],
              target: Optional[float], window_days: Optional[int]) -> Dict[str, Any]:
    end, value, text = _target_value(spec, facts, target)
    days = int(spec["window_days"] if window_days is None else window_days)
    if days < WINDOW_MIN or days > WINDOW_MAX:
        raise GoalError(f"窗口必须在 {WINDOW_MIN}–{WINDOW_MAX} 天之间")
    baseline = _baseline(facts, spec["metric"])
    return {
        "id": spec["id"],
        "family": spec["family"],
        "family_name": FAMILIES[spec["family"]]["name"],
        "name": spec["name"],
        "metric": spec["metric"],
        "metric_label": spec["metric_label"],
        "unit": spec["unit"],
        "definition": spec["definition"],
        "baseline": baseline,
        "target": int(value) if float(value).is_integer() else value,
        "target_kind": spec["target_kind"],
        "target_value": (int(end) if end is not None and float(end).is_integer() else end),
        "target_text": text,
        "window_days": days,
        "starts": date.today().isoformat(),
        "ends": (date.today() + timedelta(days=days)).isoformat(),
        "leading": list(spec["leading"]),
        "counter": list(spec["counter"]),
        "proves": spec["proves"],
        "does_not_prove": spec["does_not_prove"],
        "cost_minutes": spec["cost_minutes"],
        "stop_rules": list(spec["stop_rules"]),
        "switch_rules": list(spec["switch_rules"]),
    }


def _actions(spec: Dict[str, Any], roles: Dict[str, Any],
             facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    """前两周动作：按目标族给不同动作，不按平台复制一套。"""
    family = spec["family"]
    primary = roles["primary"]["name"]
    support = "、".join(r["name"] for r in roles["support"]) or "目标社区"
    launch = roles["launch"]["name"] if roles.get("launch") else ""

    if family == "awareness":
        rows = [
            (0, "把仓库/主页首屏改成一句话定位 + 一个 60 秒能看懂的演示",
             "human", 60, "陌生人能在 10 秒内说出你是做什么的"),
            (1, "选 3 个用户会主动搜的问题，写成 3 个标题（不是 3 个功能名）",
             "human", 30, "3 个标题，每个都带一个真实搜索词"),
            (2, "写 1 篇能被引用的技术拆解（真实数据/复现步骤），放到公开页",
             "human", 120, "发布链接 + 一段可复现的命令"),
            (3, f"在 {launch or primary} 发一次，只换反馈，不背获客指标",
             "human", 30, "发布记录 + 首日评论/阅读数"),
            (4, f"在 {support} 各做 2 次有效回应/讨论，一条产品都不提",
             "human", 60, "回应记录，带链接"),
            (7, "把首周四个数记下来：访问、clone、star、外部 issue",
             "machine", 10, "四个数入账，注明来源"),
            (10, "根据评论改一次首屏/README，只改被问到的点",
             "human", 45, "改动 diff + 改的理由"),
        ]
    elif family == "adoption":
        rows = [
            (0, "把安装/试用压到 3 步，删掉所有可选步骤",
             "human", 60, "一个新人照着做能在 10 分钟内跑通"),
            (1, "做 1 个可复现的样例/模板，别人能直接跑，不用读文档",
             "human", 120, "样例链接 + 一条命令"),
            (2, "找 10 个真实问题（issue/评论/论坛），按出现频率排序",
             "machine", 30, "10 条问题，带出处"),
            (3, f"在 {support} 各做 2 次有效回应/讨论，附上样例链接",
             "human", 60, "回应记录，带样例"),
            (5, "请 3 个用户跑一遍，只记录卡点，不解释",
             "human", 90, "3 份卡点记录，每份至少 1 个具体位置"),
            (7, "把最高频的卡点改成文档/默认值，不改产品大功能",
             "human", 60, "改动 diff + 频次"),
            (10, "发一条 changelog，点名感谢第一个外部 issue/fork（先征得同意）",
             "human", 30, "发布记录"),
        ]
    elif family == "commercial":
        rows = [
            (0, "列 15 个具名对象，每人一句「他最近在做什么」",
             "machine", 30, "15 行，带来源链接"),
            (1, "写 3 版第一条消息：引用原话 → 给判断 → 一个真问题",
             "human", 60, "3 条草稿，每条不超过 120 词"),
            (2, f"在 {primary} 发前 3 条，记录平台/时间/对象",
             "human", 45, "3 条发出记录"),
            (4, "发后 3 条 + 第 3 天跟进",
             "human", 45, "3 条发出 + 3 条跟进记录"),
            (7, "给愿意的人做 1 个样本（≤3 小时），先给范围再要数据",
             "human", 180, "样本范围一页纸 + 数据清单"),
            (10, "样本交付 + 读回 + 一次商业询问",
             "human", 60, "交付记录 + 读回 + 报价句"),
            (12, "复盘：几个回话、几个样本、几个报价，写一页",
             "machine", 10, "一页判定，带数字"),
        ]
    else:  # repeat
        rows = [
            (0, "把上期客户列出来，标出续约/复购触发日",
             "machine", 20, "客户表，带日期"),
            (1, "给每个客户写一句「上期交付的具体结果」，不写客套",
             "human", 45, "每人一句，带数字"),
            (3, "问三个问题：上期哪一段最有用、哪一段没用、下期什么时候需要",
             "human", 60, "3 条回复记录"),
            (7, "把上期交付物改成可复用的模板/清单",
             "human", 90, "模板链接"),
            (10, "对 1 个客户提出下期范围与价格",
             "human", 45, "报价记录"),
        ]
    out = []
    for offset, what, who, minutes, accept in rows:
        out.append({
            "day": (date.today() + timedelta(days=offset)).isoformat(),
            "what": what, "who": who, "minutes": minutes, "accept": accept,
        })
    return out


def _actions_pair(primary_spec: Dict[str, Any], secondary_spec: Dict[str, Any],
                  roles: Dict[str, Any], facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    """两个目标的前两周动作。声量+使用共用一份内容，不重复写两套。"""
    if {primary_spec["family"], secondary_spec["family"]} == {"awareness", "adoption"}:
        launch = (roles.get("launch") or {}).get("name") or "Hacker News"
        support = "、".join(r["name"] for r in roles["support"]) or "目标社区"
        rows = [
            (0, "把仓库首屏改成一句话定位 + 一个 60 秒能看懂的演示",
             "human", 60, "陌生人 10 秒内能说出你是做什么的", "both"),
            (1, "把安装/试用压到 3 步，删掉可选步骤",
             "human", 60, "新人 10 分钟内能跑通", "使用"),
            (2, "写 1 篇技术拆解 + 做 1 个可复现样例（同一份材料，两种用法）",
             "human", 150, "发布链接 + 一条可复现命令", "both"),
            (3, f"在 {launch} 发一次，只换反馈，不背获客指标",
             "human", 30, "发布记录 + 首日评论/阅读", "声量"),
            (4, f"在 {support} 答 5 个真问题，附样例；一条产品都不提",
             "human", 60, "5 条回答链接", "both"),
            (6, "请 3 个用户跑样例，只记录卡点，不解释",
             "human", 90, "3 份卡点记录，每份至少 1 个具体位置", "使用"),
            (8, "把最高频卡点改成文档/默认值，不改产品大功能",
             "human", 60, "改动 diff + 频次", "使用"),
            (10, "记四个数：访问、clone、star、真实数据提交",
             "machine", 10, "四个数入账，注明来源", "both"),
            (12, "如果 0 人交数据：把“给我数据”改成“我帮你跑一份，你只给脱敏导出”",
             "human", 45, "新问法 + 1 次重试记录", "使用"),
            (13, "根据评论改一次 README，只改被问到的点",
             "human", 45, "改动 diff + 改的理由", "声量"),
        ]
        out = []
        for offset, what, who, minutes, accept, serves in rows:
            out.append({
                "day": (date.today() + timedelta(days=offset)).isoformat(),
                "what": what, "who": who, "minutes": minutes,
                "accept": accept, "serves": serves,
            })
        return out

    merged: Dict[str, Dict[str, Any]] = {}
    for spec, label in ((primary_spec, "主目标"), (secondary_spec, "辅目标")):
        for row in _actions(spec, roles, facts):
            key = row["what"]
            if key in merged:
                merged[key]["serves"].append(label)
                continue
            item = dict(row)
            item["serves"] = [label]
            merged[key] = item
    return sorted(merged.values(), key=lambda r: r["day"])


def _milestones(spec: Dict[str, Any], contract: Dict[str, Any],
                secondary: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    metric = contract["metric_label"]
    lead = (contract.get("leading") or [metric])[0]
    if secondary:
        metric2 = secondary["metric_label"]
        lead2 = (secondary.get("leading") or [metric2])[0]
        return [
            {"day": 14, "date": (date.today() + timedelta(days=14)).isoformat(),
             "check": f"{lead}和{lead2}是否各出现第一个非零信号",
             "if_not": "先改话术/平台，不加量"},
            {"day": 28, "date": (date.today() + timedelta(days=28)).isoformat(),
             "check": f"{lead}是否在持续；{metric2}是否已经 ≥1",
             "if_not": "缩范围到一个平台/一个客群，再跑两周"},
            {"day": contract["window_days"],
             "date": (date.today() + timedelta(days=contract["window_days"])).isoformat(),
             "check": f"主指标 {metric}：{contract['target_text']}；"
                      f"辅指标 {metric2}：{secondary['target_text']}",
             "if_not": "只达成一个不算成功；按止损线判定：换目标 / 换平台 / 停"},
        ]
    return [
        {"day": 14, "date": (date.today() + timedelta(days=14)).isoformat(),
         "check": f"{lead}是否出现第一个非零信号",
         "if_not": "先改话术/平台，不加量"},
        {"day": 28, "date": (date.today() + timedelta(days=28)).isoformat(),
         "check": f"{lead}是否在持续；{metric}是否达到目标的一半左右",
         "if_not": "缩范围到一个平台/一个客群，再跑两周"},
        {"day": contract["window_days"],
         "date": (date.today() + timedelta(days=contract["window_days"])).isoformat(),
         "check": f"主指标 {metric}：{contract['target_text']}",
         "if_not": "按止损线判定：换目标 / 换平台 / 停"},
    ]


def _deprioritized(spec: Dict[str, Any], facts: Dict[str, Any],
                   secondary_spec: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    rows = []
    skip = {spec["id"]}
    if secondary_spec:
        skip.add(secondary_spec["id"])
    for other in GOALS:
        if other["id"] in skip:
            continue
        fit = _fit(other, facts)
        if fit["level"] in ("推荐", "可选"):
            rows.append({"id": other["id"], "name": other["name"],
                         "family": FAMILIES[other["family"]]["name"],
                         "fit": fit["level"],
                         "why": FAMILIES[other["family"]]["not_doing"]})
    return rows[:4]


def build_plan(facts: Dict[str, Any], goal_id: str,
               platforms: Optional[List[str]] = None,
               target: Optional[float] = None,
               window_days: Optional[int] = None,
               secondary_goal_id: Optional[str] = None,
               secondary_target: Optional[float] = None) -> Dict[str, Any]:
    """生成一个目标（可选 + 一个辅目标）的完整方案；只读，不写文件、不发消息。"""
    gtm._validate_facts(facts)
    spec = get(goal_id)
    ok, why = _applicable(spec, facts)
    if not ok:
        raise GoalError(f"{spec['name']} 不适用于这个产品：{why}")
    if spec.get("requires_baseline") and not _has_baseline(spec, facts):
        raise GoalError(f"{spec['name']} 需要先有基线；当前没有 {spec['metric_label']} 数据")

    contract = _contract(spec, facts, target, window_days)

    secondary_spec = None
    secondary_contract = None
    secondary_fit = None
    if secondary_goal_id:
        if secondary_goal_id == goal_id:
            raise GoalError("主目标和辅目标不能是同一个")
        secondary_spec = get(secondary_goal_id)
        ok2, why2 = _applicable(secondary_spec, facts)
        if not ok2:
            raise GoalError(f"辅目标「{secondary_spec['name']}」不适用于这个产品：{why2}")
        if secondary_spec.get("requires_baseline") and not _has_baseline(secondary_spec, facts):
            raise GoalError(f"辅目标「{secondary_spec['name']}」需要先有基线；"
                            f"当前没有 {secondary_spec['metric_label']} 数据")
        secondary_fit = _fit(secondary_spec, facts)
        secondary_contract = _contract(secondary_spec, facts, secondary_target,
                                       contract["window_days"])

    if secondary_spec:
        merged = _merge_pair_ranked(rank_platforms(spec, facts),
                                    rank_platforms(secondary_spec, facts),
                                    spec, secondary_spec)
        roles = (_assign_roles_pair(merged, platforms, spec) if platforms
                 else _recommended_pair(merged, spec))
        ranked_all = merged
    else:
        ranked = rank_platforms(spec, facts)
        roles = _assign_roles(ranked, platforms) if platforms else _recommended(ranked)
        ranked_all = ranked
    if roles["primary"] is None:
        raise GoalError("没有平台能推动这个目标：先补账号或换目标")

    selected = [roles["primary"]]
    if roles.get("launch"):
        selected.append(roles["launch"])
    selected.extend(roles["support"])
    selected_ids = [r["id"] for r in selected]
    not_now = [r for r in ranked_all if r["id"] not in selected_ids]

    fit = _fit(spec, facts)
    budget = ((facts.get("constraints") or {}).get("human_minutes_budget"))
    actions = (_actions_pair(spec, secondary_spec, roles, facts) if secondary_spec
               else _actions(spec, roles, facts))
    first_two_weeks = sum(a["minutes"] for a in actions)
    full_minutes = int(spec["cost_minutes"] + (
        secondary_spec["cost_minutes"] * 0.6 if secondary_spec else 0))

    metrics = [{
        "role": "primary", "id": contract["id"], "name": contract["name"],
        "metric": contract["metric"], "metric_label": contract["metric_label"],
        "target": contract["target"], "target_kind": contract["target_kind"],
        "target_text": contract["target_text"], "baseline": contract["baseline"],
    }]
    if secondary_contract:
        metrics.append({
            "role": "secondary", "id": secondary_contract["id"],
            "name": secondary_contract["name"],
            "metric": secondary_contract["metric"],
            "metric_label": secondary_contract["metric_label"],
            "target": secondary_contract["target"],
            "target_kind": secondary_contract["target_kind"],
            "target_text": secondary_contract["target_text"],
            "baseline": secondary_contract["baseline"],
        })

    boundary = ("目标是选择，不是预测。目标达成只证明这一个数发生了变化；"
                "要证明需求、付费或可规模化，得换目标再验一次。")
    if secondary_contract:
        boundary = ("组合目标：两个都达成才算这一轮成功，只达成一个不算。"
                    "声量达成不代表有人用；使用达成也不代表有人付钱。")

    return {
        "schema": SCHEMA,
        "product": facts.get("product") or {},
        "goal": contract,
        "secondary": secondary_contract,
        "metrics": metrics,
        "fit": fit,
        "secondary_fit": secondary_fit,
        "families": FAMILIES,
        "platforms": {
            "primary": _role_row(roles["primary"], "primary"),
            "launch": _role_row(roles["launch"], "launch") if roles.get("launch") else None,
            "support": [_role_row(r, "support") for r in roles["support"]],
            "not_now": [{"id": r["id"], "name": r["name"], "score": r["score"],
                         "why": "这一轮不选它，避免一次打三个平台"} for r in not_now],
        },
        "selection": {
            "goal_id": contract["id"],
            "secondary_goal_id": secondary_contract["id"] if secondary_contract else None,
            "platforms": selected_ids,
            "roles": {
                "primary": roles["primary"]["id"],
                "launch": roles["launch"]["id"] if roles.get("launch") else None,
                "support": [r["id"] for r in roles["support"]],
            },
        },
        "actions": actions,
        "milestones": _milestones(spec, contract, secondary_contract),
        "cost": {"human_minutes": first_two_weeks,
                 "full_goal_minutes": full_minutes,
                 "cash": 0, "budget_minutes": budget,
                 "over_budget": bool(budget and full_minutes > int(budget))},
        "deprioritized": _deprioritized(spec, facts, secondary_spec),
        "boundary": boundary,
    }


def _role_row(row: Dict[str, Any], role: str) -> Dict[str, Any]:
    if role == "primary":
        why = ("目标匹配度最高" if row["affinity"] >= 3 else "目标匹配度较高")
        if row["audience_match"]:
            why += "，且你的 ICP 在这里"
        if row["owned"]:
            why += "，账号/仓库已经在手"
    elif role == "launch":
        why = "一次性曝光，只换反馈，不背获客指标"
    else:
        why = "补位：承接主渠道来的人，或补一个主渠道覆盖不到的角落"
    return {
        "id": row["id"], "name": row["name"], "role": role, "why": why,
        "affinity": row["affinity"], "audience_match": row["audience_match"],
        "owned": row["owned"], "score": row["score"],
        "channel_status": row["channel_status"], "tier": row["tier"],
        "residency": row["residency"], "account": row["account"],
        "serves": list(row.get("serves") or []),
    }


def select(facts: Dict[str, Any], goal_id: str, platforms: List[str],
           target: Optional[float] = None, window_days: Optional[int] = None,
           note: str = "", secondary_goal_id: Optional[str] = None,
           secondary_target: Optional[float] = None) -> Dict[str, Any]:
    """把「目标 + 平台」写成一个可执行契约；不写文件，调用方决定落盘。"""
    plan = build_plan(facts, goal_id, platforms=platforms,
                      target=target, window_days=window_days,
                      secondary_goal_id=secondary_goal_id,
                      secondary_target=secondary_target)
    payload = {
        "schema": SELECTION_SCHEMA,
        "goal_id": plan["goal"]["id"],
        "secondary_goal_id": plan["selection"]["secondary_goal_id"],
        "metrics": plan["metrics"],
        "family": plan["goal"]["family"],
        "metric": plan["goal"]["metric"],
        "target": plan["goal"]["target"],
        "target_kind": plan["goal"]["target_kind"],
        "target_text": plan["goal"]["target_text"],
        "window_days": plan["goal"]["window_days"],
        "baseline": plan["goal"]["baseline"],
        "platforms": plan["selection"]["roles"],
        "selected": plan["selection"]["platforms"],
        "facts_sha256": hashlib.sha256(
            json.dumps(facts, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16],
        "selected_at": date.today().isoformat(),
        "note": note,
        "boundary": plan["boundary"],
    }
    return payload


def render_list(payload: Dict[str, Any]) -> str:
    from .narrative import table
    p = payload.get("product") or {}
    lines = [f"# 目标选项 · {p.get('name', '')}", ""]
    lines.append("先定这一轮要什么，再谈打哪个平台。同一个产品，追 star、追使用、追付费是三套动作。")
    lines.append("")
    lines.append(table(
        ["目标", "指标", "6 周判据", "首选平台", "人工", "适配"],
        [[f"{g['family_name']}·{g['name']}", g["metric_label"], g["target_text"],
          (g["primary"] or {}).get("name", "—"), f"{g['cost_minutes']} 分钟",
          g["fit"]["level"]] for g in payload["goals"]]))
    lines.append("")
    recommended = [g for g in payload["goals"] if g["fit"]["level"] == "推荐"]
    if recommended:
        lines.append("## 这个产品先看这几个")
        lines.append("")
        for g in recommended[:3]:
            lines.append(f"- **{g['name']}**：{g['proves']}；但不证明{g['does_not_prove']}。"
                         f"首选 {(g['primary'] or {}).get('name', '—')}"
                         + (f"，发射点 {g['launch']['name']}" if g.get("launch") else ""))
        lines.append("")
    lines.append("> 首选平台是按目标亲和度、受众标签和已有账号算出来的，不是唯一答案；"
                 "用 `goal select --platforms` 可以换主渠道，方案会保留你的选择。")
    lines.append(">")
    lines.append("> 如果声量和使用都想要，可以选 1 个主目标 + 1 个辅目标（`--secondary`），"
                 "两个都达成才算成功；不要用容易涨的数掩盖没动的数。")
    lines.append("")
    lines.append("## 三条路，各选一个")
    lines.append("")
    pick_rows = []
    for fam in ("awareness", "adoption", "commercial"):
        group = [g for g in payload["goals"]
                 if g["family"] == fam and g["fit"]["level"] != "不适配"]
        group.sort(key=lambda g: (0 if g["fit"]["level"] == "推荐" else 1,
                                  g["cost_minutes"]))
        for g in group[:2]:
            pick_rows.append([g["family_name"], g["name"], g["target_text"],
                              (g["primary"] or {}).get("name", "—")])
    lines.append(table(["路", "目标", "6 周判据", "首选平台"], pick_rows))
    lines.append("")
    lines.append("## 怎么选")
    lines.append("")
    lines.append("- **声量**：你能连续 6 周产出内容，才选它；它不证明需求。")
    lines.append("- **使用**：产品能在 10 分钟内跑通，才选它；它不证明付费。")
    lines.append("- **商业**：你能拿出 15 个具名对象，才选它；它不证明可规模化。")
    lines.append("")
    for note in payload.get("notes") or []:
        lines.append(f"> {note}")
    if payload.get("notes"):
        lines.append("")
    lines.append("## 下一步")
    lines.append("")
    lines.append("```")
    lines.append("python3 mkt.py goal plan --facts <facts.json> --goal <目标 id>")
    lines.append("python3 mkt.py goal select --facts <facts.json> --goal <目标 id> \\")
    lines.append("  --platforms <主平台>,<发射平台>,<辅助平台> --out data/goals/selection.json")
    lines.append("python3 mkt.py strategy build --facts <facts.json> --selection data/goals/selection.json")
    lines.append("```")
    lines.append("")
    lines.append(f"> {payload['boundary']}")
    lines.append("")
    return "\n".join(lines)


def _serves_label(row: Dict[str, Any], primary_id: str, secondary_id: Optional[str],
                  role: str) -> str:
    serves = row.get("serves") or []
    if secondary_id and len(serves) >= 2:
        return "两个目标"
    if serves:
        return "主目标" if serves[0] == primary_id else "辅目标"
    return "主目标" if role == "primary" else "辅助"


def _action_serves_label(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "、".join(str(x) for x in value) or "—"
    if value == "both":
        return "两个目标"
    return str(value or "—")


def render_plan(plan: Dict[str, Any]) -> str:
    from .narrative import table
    g = plan["goal"]
    sec = plan.get("secondary")
    p = plan["platforms"]
    title = f"# 目标方案 · {g['name']}"
    if sec:
        title += f" + {sec['name']}"
    lines = [title, ""]
    if sec:
        lines.append(f"**这一轮看两个数：{g['metric_label']} {g['target_text']}；"
                     f"{sec['metric_label']} {sec['target_text']}。** "
                     f"窗口 {g['window_days']} 天（{g['starts']} → {g['ends']}）。")
        lines.append("")
        lines.append("> 两个都达成才算这一轮成功；只达成一个不算。")
    else:
        lines.append(f"**这一轮只看一个数：{g['metric_label']}。** 判据：{g['target_text']}，"
                     f"窗口 {g['window_days']} 天（{g['starts']} → {g['ends']}）。")
    lines.append("")
    lines.append(f"> {g['definition']}")
    if sec:
        lines.append(f"> {sec['definition']}")
    lines.append("")
    if sec:
        lines.append("## 两个目标怎么互相成全")
        lines.append("")
        if {g["family"], sec["family"]} == {"awareness", "adoption"}:
            lines.append("声量是 reach，使用是 proof：同一篇技术拆解既拉 star，"
                         "也筛出愿意交真实数据的人。如果 star 涨了但数据是 0，"
                         "说明来的是看客，不是用户——这时改“要数据”的问法，不加曝光。")
        else:
            lines.append("主目标负责方向，辅目标负责验证方向有没有落地。"
                         "两个数一起看，避免用容易涨的数掩盖没动的数。")
        lines.append("")
    lines.append("## 这个组合证明什么" if sec else "## 这个目标证明什么")
    lines.append("")
    proof_rows = [[g["proves"], g["does_not_prove"]]]
    if sec:
        proof_rows.append([sec["proves"], sec["does_not_prove"]])
    lines.append(table(["证明", "不证明"], proof_rows))
    lines.append("")
    lines.append("## 平台分工")
    lines.append("")
    role_rows = []
    if sec:
        if p.get("primary"):
            role_rows.append(["主渠道", p["primary"]["name"],
                              _serves_label(p["primary"], g["id"], sec["id"], "primary"),
                              p["primary"]["why"], p["primary"]["channel_status"]])
        if p.get("launch"):
            role_rows.append(["发射点", p["launch"]["name"],
                              _serves_label(p["launch"], g["id"], sec["id"], "launch"),
                              p["launch"]["why"], p["launch"]["channel_status"]])
        for r in p.get("support") or []:
            role_rows.append(["辅助", r["name"],
                              _serves_label(r, g["id"], sec["id"], "support"),
                              r["why"], r["channel_status"]])
        lines.append(table(["角色", "平台", "服务", "为什么", "当前状态"], role_rows))
    else:
        if p.get("primary"):
            role_rows.append(["主渠道", p["primary"]["name"], p["primary"]["why"],
                              p["primary"]["channel_status"]])
        if p.get("launch"):
            role_rows.append(["发射点", p["launch"]["name"], p["launch"]["why"],
                              p["launch"]["channel_status"]])
        for r in p.get("support") or []:
            role_rows.append(["辅助", r["name"], r["why"], r["channel_status"]])
        lines.append(table(["角色", "平台", "为什么", "当前状态"], role_rows))
    lines.append("")
    lines.append("> 一次最多 1 主 + 1 发射 + 2 辅助。多一个平台，不是多一份产出，是多一份维护。")
    lines.append("")
    if p.get("not_now"):
        lines.append("## 还能换的平台")
        lines.append("")
        lines.append(table(["平台", "匹配分", "这一轮不选的理由"],
                           [[r["name"], r["score"], r["why"]]
                            for r in p["not_now"][:6]]))
        lines.append("")
    lines.append("## 前两周动作")
    lines.append("")
    if sec:
        lines.append(table(["日期", "做什么", "服务", "谁", "分钟", "验收物"],
                           [[a["day"], a["what"], _action_serves_label(a.get("serves")),
                             "人" if a["who"] == "human" else "机器",
                             a["minutes"], a["accept"]] for a in plan["actions"]]))
    else:
        lines.append(table(["日期", "做什么", "谁", "分钟", "验收物"],
                           [[a["day"], a["what"], "人" if a["who"] == "human" else "机器",
                             a["minutes"], a["accept"]] for a in plan["actions"]]))
    lines.append("")
    lines.append("## 怎么算成功")
    lines.append("")
    if sec:
        lines.append(table(["层", "指标", "值"],
                           [["主指标", g["metric_label"], g["target_text"]],
                            ["辅指标", sec["metric_label"], sec["target_text"]],
                            ["领先", "、".join(g["leading"] + sec["leading"]), "先出现，主/辅指标才会动"],
                            ["反指标", "、".join(g["counter"] + sec["counter"]), "出现就说明数在变假"]]))
    else:
        lines.append(table(["层", "指标", "值"],
                           [["主指标", g["metric_label"], g["target_text"]],
                            ["领先", "、".join(g["leading"]), "先出现，主指标才会动"],
                            ["反指标", "、".join(g["counter"]), "出现就说明数在变假"]]))
    lines.append("")
    lines.append("## 检查点")
    lines.append("")
    lines.append(table(["天", "看什么", "没到怎么办"],
                       [[m["day"], m["check"], m["if_not"]] for m in plan["milestones"]]))
    lines.append("")
    lines.append("## 什么时候停 / 换")
    lines.append("")
    for rule in g["stop_rules"]:
        lines.append(f"- {rule}")
    if sec:
        for rule in sec["stop_rules"]:
            if rule not in g["stop_rules"]:
                lines.append(f"- {rule}")
        if {g["family"], sec["family"]} == {"awareness", "adoption"}:
            lines.append("- star 涨但连续 2 周 0 真实数据：内容吸引错人，改“要数据”的问法，不加曝光")
        lines.append("- 只达成一个目标：不算成功，按上面的检查点换目标/换平台/停")
    else:
        for rule in g["switch_rules"]:
            lines.append(f"- {rule}")
    lines.append("")
    lines.append("## 代价")
    lines.append("")
    cost = plan["cost"]
    lines.append(f"- 人工：前两周约 {cost['human_minutes']} 分钟；6 周预计 "
                 f"{cost['full_goal_minutes']} 分钟"
                 + (f"（预算 {cost['budget_minutes']} 分钟"
                    + ("，**已超预算**" if cost["over_budget"] else "，在预算内）")
                    if cost.get("budget_minutes") else ""))
    lines.append("- 现金：0（不发广告、不买量）")
    if plan.get("deprioritized"):
        lines.append("")
        lines.append("**选它，等于这 6 周先不追：**")
        for d in plan["deprioritized"]:
            lines.append(f"- {d['name']}（{d['family']}）")
    lines.append("")
    lines.append("## 下一步")
    lines.append("")
    lines.append("```")
    cmd = ("python3 mkt.py goal select --facts <facts.json> --goal " + g["id"])
    if sec:
        cmd += " --secondary " + sec["id"]
    lines.append(cmd + " \\")
    lines.append("  --platforms " + ",".join(plan["selection"]["platforms"]) + " \\")
    lines.append("  --out data/goals/selection.json")
    lines.append("```")
    lines.append("")
    lines.append(f"> {plan['boundary']}")
    lines.append("")
    return "\n".join(lines)
