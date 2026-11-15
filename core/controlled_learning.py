#!/usr/bin/env python3
"""受控学习与策略实验（G-04）。

这是一个**权限**模块，不是统计模块。它回答两个问题：

1. 这次「学到了什么」配得上多大的结论？（实验对象 + 分析口径）
2. 谁有权把结论变成策略改动？（分级授权 + 策略 hash + 回滚）

设计（PRD G-04）：

  GrowthExperiment  hypothesis / segment / mechanism / control_or_baseline /
                    primary_metric / guardrail / window / min_sample_declared /
                    analysis_limitations / stop_conditions / approval
  分析口径          non_causal（前后对照：只能探索，不宣称胜出）
                    causal_allowed（随机分组 + 样本达到预声明门槛）
  权限分级          suggest：Agent 只能建议（受众/查询词/文案公式/配额减少）
                    reapprove：增加单日上限/扩渠道/换账号/解除抑制/改自动发送
                               范围 → 必须重新人审
                    auto_safe：极端失败时自动减量或停机（永不自动升权）

纪律：
1. 预注册先于启动，启动先于观察；批准绑定完整计划指纹，改计划即失效。
2. 同一份证据重复提交 no-op：不多升一次权，也不多记一次实验样本。
3. 每次策略改动记录改动前后 hash；回滚只恢复前一份策略，外部已发动作
   （账本）不在回滚范围，也不删除任何历史记录。
4. 抑制名单永远不由学习自动解除：即使人批准了「解除抑制」，本模块也只
   记录人工动作要求，不碰 contacts.jsonl / suppress.txt（G15）。
5. 数字缺项保持 unknown，不填 0；相关性不写成因果。

存储复用既有账本，不新建第二套学习账：
  data/experiments/<exp_id>.json         既有实验台账（扩展字段）
  data/grow/<run_id>.json                既有 run 状态：strategy_changes /
                                         change_approvals / applied_evidence
  data/ledger.jsonl                      既有执行账本（只读，不可回滚）
  data/outcomes/*                        G-03 三层结果（只读口径）
"""
from __future__ import annotations

import copy
import hashlib
import json
import statistics
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import experiments
from .experiments import ExperimentError
from .ledger import Ledger

#: 与 experiments 共用异常类型：调用方 catch 一个就够了。
ControlledLearningError = ExperimentError

#: GrowthExperiment 的 PRD 字段（全部必填）。
EXPERIMENT_FIELDS = (
    "hypothesis", "segment", "mechanism", "control_or_baseline",
    "primary_metric", "guardrail", "window", "min_sample_declared",
    "analysis_limitations", "stop_conditions",
)
PLAN_FINGERPRINT_FIELDS = EXPERIMENT_FIELDS + (
    "decision_rule", "randomized", "assignment_ref", "owner",
)

#: 改动分级。
TIER_SUGGEST = "suggest"
TIER_REAPPROVE = "reapprove"
TIER_AUTO_SAFE = "auto_safe"
TIERS = (TIER_SUGGEST, TIER_REAPPROVE, TIER_AUTO_SAFE)

#: 学习提案的最低有效样本（与 grow_learning.MIN_SAMPLE 同口径）。
MIN_SUGGEST_SAMPLE = 3
#: 单条爆量判定：峰值 ≥ 同期中位 × 该倍数 → 先复审。
SPIKE_RATIO = 5.0

#: 三种对照替代（§12.5），名字冻结，说明可扩展。
COMPARISON_ARMS = (
    {"name": "manual_light_ops", "description": "人工轻运营（对照基线）"},
    {"name": "generic_agent", "description": "通用 Agent + 浏览器/第三方发布工具"},
    {"name": "growovo", "description": "Growovo（含全部人工审核）"},
)
#: §12.5 结果指标；取不到就是 None（unknown），不写 0。
FALSIFICATION_FIELDS = (
    "approved_actions", "meaningful_responses", "unique_qualified_prospects",
    "independently_observed_activations", "human_minutes",
    "total_execution_cost", "policy_incidents",
)

#: 明确允许 Agent 建议的改动（PRD 原文：目标受众、查询词、文案公式、日配额减少）。
_SUGGEST_PARAMS = frozenset({
    "target.audience", "audience",
    "target.queries", "queries",
    "target.koc_share", "koc_share",
    "content.mix_weights", "content.formula", "content.weights",
})
#: 必须重新人审的改动。
_REAPPROVE_PARAMS = frozenset({
    "channels", "channels.add", "channel.add", "channels.remove",
    "account", "account.change", "guards.account_id",
    "suppression.lift", "suppression", "dnc", "dnc.lift",
    "automation.scope", "scope", "send_scope", "actions",
    "automation.enabled", "go", "live",
})
#: 生命周期动作：极端失败时允许自动执行。
_AUTO_SAFE_PARAMS = frozenset({
    "status", "stop", "pause", "status.stop", "status.pause",
})
#: 配额类：按方向分级（减少=建议/可自动，增加=重新人审）。
_QUOTA_PARAMS = frozenset({
    "actions_per_week", "quota.actions_per_week",
    "quota.max_per_day", "daily_cap", "quota.daily_cap",
})


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _sha(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(value) -> str:
    return str(value if value is not None else "").strip()


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _day(value) -> str:
    text = _text(value)
    if not text:
        return ""
    return text[:10]


def _bounds(window) -> Optional[Tuple[str, str]]:
    if not window:
        return None
    if not isinstance(window, dict):
        raise ControlledLearningError(
            "window 必须是 {'start': 'YYYY-MM-DD', 'end': 'YYYY-MM-DD'}")
    start = _day(window.get("start"))
    end = _day(window.get("end"))
    for value in (start, end):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ControlledLearningError(
                    f"window 日期不合法：{value!r}（要 YYYY-MM-DD）") from exc
    if not start and not end:
        return None
    return start, end


def _in_bounds(value, bounds: Optional[Tuple[str, str]]) -> bool:
    if bounds is None:
        return True
    day = _day(value)
    if not day:
        return False
    start, end = bounds
    return (not start or day >= start) and (not end or day <= end)


def _belongs(campaign: str, run_id: Optional[str]) -> bool:
    if not run_id:
        return True
    campaign = _text(campaign)
    return campaign == "" or campaign == run_id or campaign.startswith(run_id + "-")


# ---------------------------------------------------------------------------
# 策略快照与 hash
# ---------------------------------------------------------------------------

def _norm(value: Any) -> Any:
    """把策略面收敛成可比较的规范形（float 整数值收敛成 int，字典按键排序）。"""
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def strategy_view(data: Dict[str, Any]) -> Dict[str, Any]:
    """策略只读快照：只有这些字段变化才算「换了策略」。

    telemetry / status / 心跳 / 结果卡不入 hash——否则跑一天 tick 就让所有
    历史批准失效，批准机制会被人为绕开。生命周期动作（停/继续）单独记录。
    """
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
    quota = data.get("quota") if isinstance(data.get("quota"), dict) else {}
    guards = data.get("guards") if isinstance(data.get("guards"), dict) else {}
    content = data.get("content") if isinstance(data.get("content"), dict) else {}
    automation = (data.get("automation")
                  if isinstance(data.get("automation"), dict) else {})
    return {
        "id": _text(data.get("id")),
        "channels": sorted(str(c) for c in data.get("channels") or []),
        "actions": sorted(str(a) for a in data.get("actions") or []),
        "plan_actions_per_week": _norm(plan.get("actions_per_week")),
        "quota": {
            "actions_per_week": _norm(quota.get("actions_per_week")),
            "action_weights": _norm(quota.get("action_weights") or {}),
            "max_per_day": _norm(quota.get("max_per_day")),
        },
        "account": _text(guards.get("account_id")),
        "growth_strategy": _norm(data.get("growth_strategy") or {}),
        "content_weights": _norm(content.get("formula_weights") or {}),
        "content_formula": _text(content.get("formula")),
        "targeting": _norm(data.get("targeting") or {}),
        "automation_scope": _norm(automation.get("scope") or {}),
        "windows": _norm(data.get("windows") or {}),
    }


def strategy_hash(data: Dict[str, Any]) -> str:
    return _sha(strategy_view(data))


def change_sha256(change: Dict[str, Any]) -> str:
    """一项改动的指纹：参数 + 改前 + 改后。批准绑定它，改一项就失效。"""
    change = change if isinstance(change, dict) else {}
    return _sha({"param": _text(change.get("param")),
                 "from": _norm(change.get("from")),
                 "to": _norm(change.get("to"))})


def _restore_view(data: Dict[str, Any], view: Dict[str, Any]) -> None:
    """把策略面恢复到快照（不碰状态、账本、外部动作）。"""
    data["channels"] = list(view.get("channels") or [])
    data["actions"] = list(view.get("actions") or [])
    data.setdefault("plan", {})["actions_per_week"] = \
        view.get("plan_actions_per_week")
    quota = data.setdefault("quota", {})
    quota_view = view.get("quota") or {}
    quota["actions_per_week"] = quota_view.get("actions_per_week")
    quota["action_weights"] = copy.deepcopy(quota_view.get("action_weights") or {})
    if quota_view.get("max_per_day") is None:
        quota.pop("max_per_day", None)
    else:
        quota["max_per_day"] = quota_view["max_per_day"]
    data.setdefault("guards", {})["account_id"] = _text(view.get("account"))
    data["growth_strategy"] = copy.deepcopy(view.get("growth_strategy") or {})
    content = data.setdefault("content", {})
    content["formula_weights"] = copy.deepcopy(view.get("content_weights") or {})
    if view.get("content_formula"):
        content["formula"] = view["content_formula"]
    else:
        content.pop("formula", None)
    data["targeting"] = copy.deepcopy(view.get("targeting") or {})
    automation = data.setdefault("automation", {})
    if view.get("automation_scope"):
        automation["scope"] = copy.deepcopy(view["automation_scope"])
    else:
        automation.pop("scope", None)
    data["windows"] = copy.deepcopy(view.get("windows") or {})
    _rebuild_briefs(data)


# ---------------------------------------------------------------------------
# 权限分级
# ---------------------------------------------------------------------------

def classify_change(change: Dict[str, Any]) -> Dict[str, Any]:
    """判定一项策略改动的权限等级。未知参数按高风险处理（fail closed）。"""
    change = change if isinstance(change, dict) else {}
    param = _text(change.get("param"))
    if not param:
        raise ControlledLearningError("改动必须写 param（改什么）")
    before = _number(change.get("from"))
    after = _number(change.get("to"))

    def result(tier: str, direction: str, reason: str) -> Dict[str, Any]:
        return {
            "param": param, "tier": tier, "direction": direction,
            "requires_human_reapproval": tier == TIER_REAPPROVE,
            "auto_allowed_on_extreme_failure": tier == TIER_AUTO_SAFE
            or (tier == TIER_SUGGEST and param in _QUOTA_PARAMS
                and direction == "decrease"),
            "reason": reason,
        }

    if param in _AUTO_SAFE_PARAMS:
        return result(TIER_AUTO_SAFE, "stop",
                      "停机/暂停属于安全动作：极端失败可自动执行")
    if param in _QUOTA_PARAMS:
        if before is not None and after is not None and after <= before:
            return result(TIER_SUGGEST, "decrease",
                          "减少配额是低风险方向：Agent 可建议，极端失败可自动执行")
        return result(TIER_REAPPROVE, "increase",
                      "增加单日/单周上限会扩大外部动作量：必须重新人审")
    if param in _REAPPROVE_PARAMS:
        return result(TIER_REAPPROVE, "change",
                      "扩渠道/换账号/解除抑制/改自动发送范围属于权限变更：必须重新人审")
    if param in _SUGGEST_PARAMS:
        return result(TIER_SUGGEST, "change",
                      "受众/查询词/文案公式属于低风险调整：Agent 可以建议，由人决定")
    return result(TIER_REAPPROVE, "change",
                  "未登记的改动参数按高风险处理：必须重新人审（fail closed）")


def _approval_key(record: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (_text(record.get("change_sha256")), _text(record.get("before_hash")),
            _text(record.get("by")), _text(record.get("at")))


def _find_approval(data: Dict[str, Any], change: Dict[str, Any],
                   approval: Optional[Dict[str, Any]],
                   before_hash: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """批准必须匹配改动 + 改前 hash，并且没有被消费/回滚作废。

    一次性消费：批准一旦用来落地某项升权，就不能再被同一份证据复用。
    回滚恢复旧 hash 时，绑定在旧 hash 上的未用批准也一并作废——否则
    「批准 → 应用 → 回滚 → 用旧批准再来一次」会把升权批两次。
    """
    target = change_sha256(change)
    stored = [r for r in (data.get("change_approvals") or [])
              if isinstance(r, dict)]
    stored_keys = {_approval_key(r) for r in stored}
    records: List[Dict[str, Any]] = []
    if isinstance(approval, dict) and _approval_key(approval) not in stored_keys:
        records.append(approval)
    records.extend(stored)
    stale = consumed = revoked = False
    for rec in reversed(records):
        if _text(rec.get("change_sha256")) != target:
            continue
        if not _text(rec.get("by")):
            continue
        if _text(rec.get("consumed_at")):
            consumed = True
            continue
        if _text(rec.get("revoked_by_rollback")):
            revoked = True
            continue
        if _text(rec.get("before_hash")) != before_hash:
            stale = True
            continue
        return True, rec, ""
    if consumed:
        return False, None, "批准已被消费：每次升权都要重新批准（同一份批准不能复用）"
    if revoked:
        return False, None, "批准已被回滚作废：回滚后重新批准才能再升权"
    if stale:
        return False, None, "批准已失效：策略在批准之后变过，需要按当前策略重新批准"
    return False, None, "这项改动还没有人审批准：先 mkt grow change approve 再应用"


def _consume_approval(data: Dict[str, Any], record: Dict[str, Any],
                      change_id: str, by: str) -> None:
    """把实际用掉的批准标记为一次性；找不到原记录（显式传入）也标记。"""
    stamp = {"consumed_at": _now(), "consumed_by_change": change_id,
             "consumed_by": _text(by) or "human"}
    target_key = _approval_key(record)
    for stored in (data.get("change_approvals") or []):
        if isinstance(stored, dict) and _approval_key(stored) == target_key:
            stored.update(stamp)
            return
    record.update(stamp)


def approve_change(data: Dict[str, Any], change: Dict[str, Any], *,
                   by: str, on: str = "", note: str = "") -> Dict[str, Any]:
    """记录一次人审批准（append-only）。只批准，不应用。"""
    if not _text(by):
        raise ControlledLearningError(
            "批准必须署名（by）：机器不能替人背书，Agent 自报的 approved_by 不算")
    info = classify_change(change)
    record = {
        "at": _now(),
        "on": on or date.today().isoformat(),
        "by": _text(by),
        "note": _text(note),
        "param": info["param"],
        "tier": info["tier"],
        "change_sha256": change_sha256(change),
        "before_hash": strategy_hash(data),
        "summary": _text(change.get("detail"))
        or f"{info['param']}：{change.get('from')} → {change.get('to')}",
    }
    data.setdefault("change_approvals", []).append(record)
    return record


# ---------------------------------------------------------------------------
# 应用与回滚
# ---------------------------------------------------------------------------

def _next_change_id(data: Dict[str, Any]) -> str:
    return "chg-%04d" % (len(data.get("strategy_changes") or []) + 1)


def _rebuild_briefs(data: Dict[str, Any]) -> None:
    """按当前公式配比重算内容需求单（与 grow_v2 的调参落点一致）。"""
    briefs = (data.get("supply") or {}).get("briefs")
    if not briefs:
        return
    from . import grow_content
    weights = (data.get("content") or {}).get("formula_weights")
    family = _text((data.get("goal") or {}).get("family"))
    rebuilt = []
    for brief in briefs:
        rebuilt.append(grow_content.enrich_brief(
            {k: v for k, v in brief.items()
             if k not in ("formulas", "mix", "rules")},
            goal_family=family, formula_weights=weights))
    data.setdefault("supply", {})["briefs"] = rebuilt


def _apply_one(data: Dict[str, Any],
               change: Dict[str, Any]) -> Tuple[bool, str]:
    """执行一项策略改动的落点。返回 (是否改动, 需要人工完成的事项)。"""
    param = _text(change.get("param"))
    target = change.get("to")
    if param in _QUOTA_PARAMS or param == "actions_per_week":
        if param in ("quota.max_per_day", "daily_cap", "quota.daily_cap"):
            value = _number(target)
            if value is None:
                return False, ""
            data.setdefault("quota", {})["max_per_day"] = int(value)
            return True, ""
        value = _number(target)
        if value is None:
            return False, ""
        value = max(1, int(value))
        data.setdefault("plan", {})["actions_per_week"] = value
        data.setdefault("quota", {})["actions_per_week"] = value
        return True, ""
    if param in ("channels",):
        data["channels"] = [str(c) for c in (target or [])]
        return True, ""
    if param in ("channels.add", "channel.add"):
        channels = [str(c) for c in (data.get("channels") or [])]
        add = target if isinstance(target, (list, tuple)) else [target]
        for channel in add:
            channel = _text(channel)
            if channel and channel not in channels:
                channels.append(channel)
        data["channels"] = channels
        return True, ""
    if param in ("account", "account.change", "guards.account_id"):
        data.setdefault("guards", {})["account_id"] = _text(target)
        return True, ""
    if param in ("suppression", "suppression.lift", "dnc", "dnc.lift"):
        # G15：学习/实验永远不自动解除抑制；批准只代表「允许人去处理」。
        return False, ("解除抑制必须由人在 contacts.jsonl / suppress.txt 里操作；"
                       "本模块不会自动解除 DNC（批准记录已留存）")
    if param in ("automation.scope", "scope", "send_scope"):
        data.setdefault("automation", {})["scope"] = copy.deepcopy(target)
        return True, ""
    if param in ("automation.enabled", "go", "live"):
        data.setdefault("automation", {})["enabled"] = bool(target)
        return True, ""
    if param == "target.audience" or param == "audience":
        data.setdefault("targeting", {})["audience"] = _text(target)
        return True, ""
    if param == "target.queries" or param == "queries":
        queries = target if isinstance(target, (list, tuple)) else [target]
        data.setdefault("targeting", {})["queries"] = [
            _text(q) for q in queries if _text(q)]
        return True, ""
    if param in ("target.koc_share", "koc_share"):
        raw = target.get("koc_share") if isinstance(target, dict) else target
        share = _number(raw)
        if share is None:
            return False, ""
        share = max(0.3, min(0.9, float(share)))
        data.setdefault("growth_strategy", {})["koc_share_target"] = share
        return True, ""
    if param in ("content.formula",):
        data.setdefault("content", {})["formula"] = _text(target)
        return True, ""
    if param in ("content.mix_weights", "content.weights"):
        payload = target if isinstance(target, dict) else {}
        boost = _text(payload.get("boost"))
        reduce = _text(payload.get("reduce"))
        if not boost or not reduce:
            return False, ""
        from . import grow_content
        content = data.setdefault("content", {})
        content["formula_weights"] = grow_content.apply_weights(
            content.get("formula_weights"), boost=boost, reduce=reduce)
        _rebuild_briefs(data)
        return True, ""
    if param in _AUTO_SAFE_PARAMS:
        data["status"] = "paused"
        return True, ""
    return False, (f"{param} 没有自动落点：批准记录已留存，请在对应配置里人工执行")


def _already_at_target(data: Dict[str, Any],
                       change: Dict[str, Any]) -> bool:
    """这项改动是否已经落地：在副本上应用后策略 hash 不变。

    放在「批准是否已消费」之前：重试已落地的改动是幂等 no_op，不是二次升权。
    只认会产生实际变动的参数；人工操作类（解除抑制）与未知参数不在此列。
    """
    probe = copy.deepcopy(data)
    mutated, manual = _apply_one(probe, change)
    if manual or not mutated:
        return False
    return strategy_hash(probe) == strategy_hash(data)


def apply_changes(data: Dict[str, Any], changes: Sequence[Dict[str, Any]], *,
                  actor: str, evidence_hash: str = "",
                  approval: Optional[Dict[str, Any]] = None,
                  extreme_failure: bool = False, by: str = "",
                  on: str = "", note: str = "") -> Dict[str, Any]:
    """按权限分级应用策略改动；不满足条件的一律 held（不改任何字段）。

    actor="agent"：只能建议（``applied`` 永远为空）；只有极端失败 + auto_safe
    才允许真正落地（减量/停机）。
    actor="human"：suggest 可直接应用；reapprove 必须有匹配的批准记录。
    """
    if actor not in ("agent", "human"):
        raise ControlledLearningError("actor 只允许 agent / human（谁在改策略）")
    data = data if isinstance(data, dict) else {}
    before_hash = strategy_hash(data)
    if evidence_hash and evidence_hash in (data.get("applied_evidence") or []):
        return {
            "applied": [], "held": [], "manual_actions": [],
            "no_op": True, "reason": "evidence_already_applied",
            "evidence_hash": evidence_hash,
            "before_hash": before_hash, "after_hash": before_hash,
            "status": "no_op", "suggestion_only": False,
            "next_actor": None,
        }

    applied: List[Dict[str, Any]] = []
    held: List[Dict[str, Any]] = []
    already: List[Dict[str, Any]] = []
    manual: List[str] = []
    for change in changes or []:
        if not isinstance(change, dict):
            continue
        info = classify_change(change)
        csha = change_sha256(change)
        if _already_at_target(data, change):
            already.append({"change": change, "tier": info["tier"],
                            "change_sha256": csha,
                            "detail": "目标值已等于 to，策略 hash 未变"})
            continue
        allowed = False
        reason_code: Optional[str] = None
        detail = ""
        used_approval: Optional[Dict[str, Any]] = None
        if actor == "agent":
            if info["auto_allowed_on_extreme_failure"] and extreme_failure:
                allowed = True
            elif info["auto_allowed_on_extreme_failure"]:
                reason_code = "POLICY_DENIED"
                detail = "自动停机/减量只在极端失败时执行；平时仍需人决定"
            elif info["tier"] == TIER_REAPPROVE:
                reason_code = "POLICY_DENIED"
                detail = "Agent 不能申请升权；请把改动交给人审"
            else:
                reason_code = None
                detail = "Agent 只能建议：改动等人工决定后才会生效"
            if not allowed:
                held.append({
                    "change": change, "tier": info["tier"],
                    "reason_code": reason_code, "detail": detail,
                    "change_sha256": csha, "next_actor": "human",
                })
                continue
        else:
            if info["tier"] == TIER_REAPPROVE:
                ok, record, why = _find_approval(data, change, approval,
                                                 before_hash)
                if ok:
                    allowed = True
                    used_approval = record
                    detail = "人审批准：" + (_text((record or {}).get("by")))
                else:
                    reason_code = "REVIEW_REQUIRED"
                    detail = why
            else:
                allowed = True
        if not allowed:
            held.append({
                "change": change, "tier": info["tier"],
                "reason_code": reason_code or "REVIEW_REQUIRED",
                "detail": detail, "change_sha256": csha,
                "next_actor": "human",
            })
            continue

        change_before_view = copy.deepcopy(strategy_view(data))
        change_before_hash = strategy_hash(data)
        mutated, manual_note = _apply_one(data, change)
        if manual_note:
            manual.append(manual_note)
        if not mutated:
            held.append({
                "change": change, "tier": info["tier"],
                "reason_code": "REVIEW_REQUIRED",
                "detail": manual_note or "这项改动没有自动落点，必须人工执行",
                "change_sha256": csha, "next_actor": "human",
            })
            continue
        after_hash = strategy_hash(data)
        record = {
            "change_id": _next_change_id(data),
            "kind": ("auto_safety" if actor != "human"
                     else ("reapproval" if info["tier"] == TIER_REAPPROVE
                           else "decision")),
            "at": _now(),
            "on": on or date.today().isoformat(),
            "by": _text(by) or actor,
            "actor": actor,
            "param": info["param"],
            "tier": info["tier"],
            "from": _norm(change.get("from")),
            "to": _norm(change.get("to")),
            "detail": _text(change.get("detail")),
            "evidence_hash": evidence_hash,
            "before_hash": change_before_hash,
            "after_hash": after_hash,
            "before": change_before_view,
            "after": copy.deepcopy(strategy_view(data)),
            "approved_by": _text(by) if actor == "human" else "",
            "external_actions_changed": False,
        }
        if used_approval is not None:
            _consume_approval(data, used_approval, record["change_id"],
                              _text(by) or actor)
        data.setdefault("strategy_changes", []).append(record)
        data.setdefault("adjustments", []).append({
            "on": record["on"], "kind": record["kind"],
            "param": record["param"], "from": record["from"],
            "to": record["to"], "detail": record["detail"],
            "rollback": change.get("rollback", change_before_view.get(
                "plan_actions_per_week")),
            "change_id": record["change_id"],
            "before_hash": record["before_hash"],
            "after_hash": record["after_hash"],
        })
        applied.append(dict(change, change_id=record["change_id"],
                            before_hash=record["before_hash"],
                            after_hash=record["after_hash"]))
        if evidence_hash:
            data.setdefault("applied_evidence", []).append(evidence_hash)

    after_hash = strategy_hash(data)
    just_duplicates = bool(already) and not applied and not held
    status = ("no_op" if just_duplicates else
              ("no_change" if not applied and not held else
               ("needs_review" if held and not applied else
                ("partial" if held else "applied"))))
    return {
        "applied": applied, "held": held, "manual_actions": manual,
        "already_applied": already,
        "no_op": just_duplicates,
        "reason": "already_applied" if just_duplicates else "",
        "evidence_hash": evidence_hash,
        "before_hash": before_hash, "after_hash": after_hash,
        "status": status,
        "suggestion_only": actor == "agent" and bool(held),
        "next_actor": "human" if held else None,
    }


def record_auto_reduce(data: Dict[str, Any], *, param: str, from_value: int,
                       to_value: int, reason: str, on: str = "") -> Dict[str, Any]:
    """极端失败/账号健康触发的自动减量：只允许向下，永不升权。"""
    if int(to_value) >= int(from_value):
        raise ControlledLearningError(
            "自动减量只能把配额调低；自动升权一律拒绝（G-04）")
    outcome = apply_changes(
        data, [{"param": param, "from": int(from_value), "to": int(to_value),
                "detail": reason}],
        actor="agent", extreme_failure=True, by="system", on=on)
    if not outcome["applied"]:
        raise ControlledLearningError(
            "自动减量没有落地：" + "；".join(
                h.get("detail", "") for h in outcome["held"]))
    return outcome["applied"][0]


def guard_extreme_failure(data: Dict[str, Any], *, failed: int = 0,
                          indeterminate: int = 0,
                          zero_interaction_days: int = 0,
                          on: str = "") -> Dict[str, Any]:
    """极端失败守卫：只允许停机（或先减量），永不自动升权。

    返回 action=none|stop；停机记录进 strategy_changes，不碰配额、渠道、账号
    与抑制名单，也不回滚已发外部动作。
    """
    failed = int(failed or 0)
    indeterminate = int(indeterminate or 0)
    zero = int(zero_interaction_days or 0)
    reasons: List[str] = []
    if failed + indeterminate >= 3:
        reasons.append(f"失败/未知回执 {failed + indeterminate} 次："
                       "先停机，人工裁决后再继续")
    if zero >= 3:
        reasons.append(f"连续 {zero} 天零互动：先停机复审内容与目标人群")
    if not reasons:
        return {"action": "none", "reason": "", "applied": False,
                "external_actions_unchanged": True}
    data["status"] = "paused"
    record = {
        "change_id": _next_change_id(data),
        "kind": "auto_stop",
        "at": _now(),
        "on": on or date.today().isoformat(),
        "by": "system",
        "actor": "system",
        "param": "status",
        "tier": TIER_AUTO_SAFE,
        "from": "active",
        "to": "paused",
        "detail": "；".join(reasons),
        "evidence_hash": "",
        "before_hash": strategy_hash(data),
        "after_hash": strategy_hash(data),
        "before": copy.deepcopy(strategy_view(data)),
        "after": copy.deepcopy(strategy_view(data)),
        "external_actions_changed": False,
    }
    data.setdefault("strategy_changes", []).append(record)
    return {"action": "stop", "reason": "；".join(reasons), "applied": True,
            "record": record, "external_actions_unchanged": True,
            "auto_allowed": True}


def _eligible_rollback(data: Dict[str, Any], change_id: str) -> Optional[Dict[str, Any]]:
    """默认只撤销「最近一次人工策略改动」；自动安全动作与回滚本身不默认撤销。"""
    records = [r for r in (data.get("strategy_changes") or [])
               if isinstance(r, dict)]
    if change_id:
        for record in reversed(records):
            if _text(record.get("change_id")) != change_id:
                continue
            if _text(record.get("kind")) == "rollback":
                return None  # 不能回滚一次回滚
            return record
        return None
    if not records:
        return None
    last = records[-1]
    if _text(last.get("kind")) in ("rollback", "auto_safety", "auto_stop"):
        return None  # 自动减量/停机不是人工策略；要撤必须显式指定 change_id
    if _text(last.get("param")) in _AUTO_SAFE_PARAMS:
        return None
    return last


def rollback_changes(data: Dict[str, Any], *, by: str, on: str = "",
                     change_id: str = "", note: str = "") -> Dict[str, Any]:
    """恢复前一份策略快照。只改策略面，不撤销已发外部动作、不删历史。"""
    data = data if isinstance(data, dict) else {}
    if not _text(by):
        raise ControlledLearningError(
            "回滚必须署名（by）：撤销是人对策略的决定，不能匿名")
    target = _eligible_rollback(data, _text(change_id))
    reversed_ids = {_text(r.get("reverses"))
                    for r in (data.get("strategy_changes") or [])
                    if isinstance(r, dict) and _text(r.get("kind")) == "rollback"}
    if target is not None and _text(target.get("change_id")) in reversed_ids:
        return {"rolled_back": False,
                "reason": ("这项改动已被回滚过：不能重复回滚；"
                           "要重新升权请重新批准"),
                "external_actions_unchanged": True, "record": None}
    if target is None:
        return {"rolled_back": False,
                "reason": ("没有可撤销的人工策略改动（最近一次是自动安全动作、"
                           "回滚，或根本没有改动）"),
                "external_actions_unchanged": True, "record": None}
    current_view = copy.deepcopy(strategy_view(data))
    current_hash = strategy_hash(data)
    candidate = copy.deepcopy(data)
    _restore_view(candidate, target.get("before") or {})
    restored_hash = strategy_hash(candidate)
    expected = _text(target.get("before_hash"))
    if expected and restored_hash != expected:
        # 策略面被外部改过（例如手改 run 文件）：宁可拒绝，也不假装精确恢复。
        return {"rolled_back": False,
                "reason": ("策略面已被外部改动，无法精确恢复到上一份批准策略；"
                           "请人工核对后重做，不要用一个错位的 hash 冒充回滚"),
                "external_actions_unchanged": True, "record": None}
    data.clear()
    data.update(candidate)
    record = {
        "change_id": _next_change_id(data),
        "kind": "rollback",
        "at": _now(),
        "on": on or date.today().isoformat(),
        "by": _text(by),
        "actor": "human",
        "param": _text(target.get("param")),
        "tier": "rollback",
        "from": current_hash,
        "to": restored_hash,
        "reverses": _text(target.get("change_id")),
        "detail": _text(note) or f"撤销 {target.get('change_id')}，恢复前一份策略",
        "evidence_hash": "",
        "before_hash": current_hash,
        "after_hash": restored_hash,
        "before": current_view,
        "after": copy.deepcopy(strategy_view(data)),
        "external_actions_changed": False,
    }
    # 回滚把策略恢复到旧 hash：绑定在旧 hash 上的未用批准一并作废，
    # 否则它们会在回滚后「复活」。回滚之后新批的（at 更晚）不受影响。
    for rec in (data.get("change_approvals") or []):
        if not isinstance(rec, dict) or _text(rec.get("consumed_at")):
            continue
        if _text(rec.get("before_hash")) != restored_hash:
            continue
        if _text(rec.get("at")) > _text(record.get("at")):
            continue
        rec["revoked_by_rollback"] = record["change_id"]
        rec["revoked_at"] = record.get("at")
    data.setdefault("strategy_changes", []).append(record)
    return {
        "rolled_back": True,
        "from_hash": current_hash,
        "restored_hash": restored_hash,
        "record": record,
        "external_actions_unchanged": True,
        "reason": "",
    }


# ---------------------------------------------------------------------------
# 学习评估：样本门槛、复审触发、实验口径
# ---------------------------------------------------------------------------

def review_triggers(rows: Sequence[Dict[str, Any]], *,
                    min_sample: int = MIN_SUGGEST_SAMPLE,
                    spike_ratio: float = SPIKE_RATIO) -> List[str]:
    """爆量/零互动触发复审：这两种情况下的平均值会骗人。"""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    views = [int(r["views"]) for r in rows
             if isinstance(r.get("views"), int) and not isinstance(r["views"], bool)
             and r["views"] >= 0]
    replies = [int(r["replies"]) for r in rows
               if isinstance(r.get("replies"), int)
               and not isinstance(r["replies"], bool)]
    reasons: List[str] = []
    if len(rows) >= min_sample and len(views) >= min_sample:
        median = statistics.median(views)
        peak = max(views)
        if median > 0 and peak >= spike_ratio * median:
            reasons.append(
                f"爆量：单条查看 {peak} 是同期中位 {median} 的 "
                f"{peak / median:.1f} 倍，先复审再调参（均值会被这一条带偏）")
    if len(rows) >= min_sample and len(replies) == len(rows) \
            and all(value == 0 for value in replies):
        reasons.append("零互动：样本内所有回复为 0，先复审内容与目标人群，别靠调权重硬救")
    return reasons


def assess(*, sample: int, rows: Optional[Sequence[Dict[str, Any]]] = None,
           changes: Optional[Sequence[Dict[str, Any]]] = None,
           declared_min: Optional[int] = None, randomized: bool = False,
           feedback: Optional[Sequence[Dict[str, Any]]] = None,
           review_required: Optional[bool] = None,
           review_reasons: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """给一份学习结果定口径：能不能优化、能不能宣称、要不要先复审。

    3 个有效样本只是「开始有方向」，不是胜出证据；样本/来源全无时不允许
    自动优化；随机分组且样本达到预声明门槛才允许实验因果口径。
    """
    sample = int(sample or 0)
    changes = list(changes or [])
    reasons = list(review_reasons or [])
    if review_required is None:
        reasons = reasons or review_triggers(rows or [])
    else:
        reasons = reasons if review_required else []
    strong_negative = any(
        _text(f.get("sentiment")) == "negative"
        and _text(f.get("strength")) == "strong"
        for f in (feedback or []) if isinstance(f, dict))

    if sample <= 0:
        mode, can_optimize = "no_data", False
        next_action = ("没有样本也没有来源：先采集帖子指标或业务事件；"
                       "不自动优化，也不写「效果为 0」")
    elif sample < MIN_SUGGEST_SAMPLE:
        mode, can_optimize = "insufficient", False
        next_action = (f"有效样本 {sample} 条 < {MIN_SUGGEST_SAMPLE}：继续采集，"
                       "不自动优化")
    elif declared_min and sample < int(declared_min):
        mode, can_optimize = "exploration", True
        next_action = (f"样本 {sample} < 预声明 {declared_min}：只做探索性调整，"
                       "不宣称策略胜出")
    elif randomized:
        mode, can_optimize = "causal_allowed", True
        next_action = "随机分组样本达到门槛：可以用实验因果口径，但结论仍由人判定"
    else:
        mode, can_optimize = "exploration", True
        next_action = "无随机分组：只能做描述性前后对照，不宣称策略胜出"

    if reasons:
        can_optimize = False
        next_action = "先复审：" + "；".join(reasons)
    if strong_negative:
        can_optimize = False
        next_action = "收到强反面反馈：先建议停止该策略，由人决定；不自动解除 DNC，也不加额度"
    can_claim_causality = mode == "causal_allowed" and not reasons and not strong_negative
    can_claim_winner = can_claim_causality
    if sample <= 0:
        wording = "无数据：还没有有效样本，不能得出结论"
    elif mode == "insufficient":
        wording = (f"样本不足：{sample} 条有效样本，连探索都算不上；"
                   "不能宣称任何策略胜出")
    elif mode == "exploration":
        wording = (f"探索性结论：{sample} 条有效样本；只能提示方向，不能宣称策略胜出"
                   "（相关性不是因果）")
    else:
        wording = (f"随机分组样本 {sample} 条达到预声明门槛：可以使用实验因果口径；"
                   "结论仍由人判定")
    must_not: List[str] = []
    if strong_negative:
        must_not = ["解除 DNC/抑制名单", "提高发布额度", "自动扩大渠道或换账号"]
    return {
        "analysis_mode": mode,
        "sample": sample,
        "declared_min": int(declared_min) if declared_min else None,
        "randomized": bool(randomized),
        "can_optimize": bool(can_optimize),
        "can_claim_winner": bool(can_claim_winner),
        "can_claim_causality": bool(can_claim_causality),
        "review_required": bool(reasons),
        "review_reasons": reasons,
        "suggest_stop": bool(strong_negative),
        "must_not": must_not,
        "auto_changes": [],
        "limitations": [
            "公开指标与同期变化是相关，不是因果",
            "样本门槛不是统计功效保证；结论由人判定并署名",
        ],
        "next_action": next_action,
        "wording": wording,
    }


# ---------------------------------------------------------------------------
# GrowthExperiment：预注册、批准、启动、观察、分析
# ---------------------------------------------------------------------------

def _present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _plan_payload(exp: Dict[str, Any]) -> Dict[str, Any]:
    return {key: _norm(exp.get(key)) for key in PLAN_FINGERPRINT_FIELDS}


def plan_sha256(exp: Dict[str, Any]) -> str:
    return _sha(_plan_payload(exp))


def _check_plan(exp: Dict[str, Any]) -> None:
    stored = _text(exp.get("plan_sha256"))
    if not stored:
        raise ControlledLearningError(
            "这个实验没有受控计划指纹（不是由 controlled_learning 设计的）："
            "不能用受控实验口径，请新建实验")
    if stored != plan_sha256(exp):
        raise ControlledLearningError(
            "实验计划被改动过：批准/预注册字段与指纹不一致，拒绝继续。"
            "需要变更就停止本实验并新建（预注册纪律）")


def load_experiment(data_dir, exp_id: str) -> Dict[str, Any]:
    return experiments.load_experiment(Path(data_dir), exp_id)


def list_experiments(data_dir) -> List[Dict[str, Any]]:
    directory = Path(data_dir) / "experiments"
    out = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(doc, dict):
            out.append(doc)
    return out


def _stop_conditions(value) -> List[str]:
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace("；", ",").replace("、", ",")
                 .split(",")]
    elif isinstance(value, (list, tuple)):
        parts = [_text(v) for v in value]
    else:
        parts = []
    return [p for p in parts if p]


def design_experiment(data_dir, *, exp_id: str, hypothesis: str, segment: str,
                      mechanism: str, control_or_baseline: str,
                      primary_metric: str, guardrail: str, window,
                      min_sample_declared, analysis_limitations: str,
                      stop_conditions, decision_rule: str = "",
                      owner: str = "", randomized: bool = False,
                      assignment_ref: str = "",
                      dry_run: bool = False) -> Dict[str, Any]:
    """建一个 GrowthExperiment（预注册）。默认只校验，dry_run=False 才落盘。"""
    values = {
        "hypothesis": _text(hypothesis),
        "segment": _text(segment),
        "mechanism": _text(mechanism),
        "control_or_baseline": _text(control_or_baseline),
        "primary_metric": _text(primary_metric),
        "guardrail": _text(guardrail),
        "analysis_limitations": _text(analysis_limitations),
    }
    missing = [name for name, value in values.items() if not value]
    bounds = _bounds(window)
    if bounds is None:
        missing.append("window")
    stops = _stop_conditions(stop_conditions)
    if not stops:
        missing.append("stop_conditions")
    try:
        declared = int(min_sample_declared)
    except (TypeError, ValueError):
        declared = 0
    if declared < 1:
        missing.append("min_sample_declared")
    if _text(exp_id) == "":
        missing.append("exp_id")
    if missing:
        raise ControlledLearningError(
            "GrowthExperiment 缺少或为空：" + "、".join(missing)
            + "（PRD 要求预注册字段齐全后才能审批）")
    if bounds[1] < bounds[0]:
        raise ControlledLearningError(
            f"window 结束日早于开始日：{bounds[1]} < {bounds[0]}")
    if randomized and not _text(assignment_ref):
        raise ControlledLearningError(
            "声明随机分组必须给出 assignment_ref（分组记录在哪）："
            "没有分组记录就不能用实验因果口径")
    rule = _text(decision_rule) or (
        f"预注册：{values['primary_metric']} 在 {bounds[0]}~{bounds[1]} 内累计"
        f" ≥{declared} 条有效观察且 guardrail 未触发后，由人按该定义判定")
    preview = {
        **values,
        "exp_id": _text(exp_id),
        "window": {"start": bounds[0], "end": bounds[1]},
        "min_sample_declared": declared,
        "stop_conditions": stops,
        "decision_rule": rule,
        "owner": _text(owner),
        "randomized": bool(randomized),
        "assignment_ref": _text(assignment_ref),
        "approval": None,
        "status": "planned",
    }
    preview["plan_sha256"] = plan_sha256(preview)
    if dry_run:
        preview["dry_run"] = True
        return preview
    exp = experiments.new_experiment(
        Path(data_dir), exp_id=_text(exp_id),
        hypothesis=values["hypothesis"], metric=values["primary_metric"],
        decision_rule=rule, start=bounds[0], end=bounds[1],
        variant=values["mechanism"], owner=_text(owner))
    extras = {
        **values,
        "window": {"start": bounds[0], "end": bounds[1]},
        "min_sample_declared": declared,
        "stop_conditions": stops,
        "randomized": bool(randomized),
        "assignment_ref": _text(assignment_ref),
        "approval": None,
    }
    combined = dict(exp)
    combined.update(extras)
    extras["plan_sha256"] = plan_sha256(combined)
    return experiments.update_fields(Path(data_dir), _text(exp_id), **extras)


def approve_experiment(data_dir, exp_id: str, *, by: str,
                       note: str = "", dry_run: bool = False) -> Dict[str, Any]:
    """人审批准实验计划。批准绑定计划指纹；计划被改则批准自动失效。"""
    if not _text(by):
        raise ControlledLearningError(
            "批准实验必须署名（by）：机器不能替人背书")
    exp = load_experiment(data_dir, exp_id)
    if exp.get("status") != "planned":
        raise ControlledLearningError(
            f"只有 planned 状态可以批准，当前 {exp.get('status')}")
    _check_plan(exp)
    approval = {
        "by": _text(by),
        "at": _now(),
        "note": _text(note),
        "plan_sha256": _text(exp.get("plan_sha256")),
        "scope": "growth_experiment",
    }
    if dry_run:
        return dict(approval, exp_id=exp_id, dry_run=True)
    return experiments.update_fields(Path(data_dir), exp_id, approval=approval)


def start_experiment(data_dir, exp_id: str, *,
                     dry_run: bool = False) -> Dict[str, Any]:
    """批准 + 计划未变才允许开跑；否则 REVIEW_REQUIRED。"""
    exp = load_experiment(data_dir, exp_id)
    if exp.get("status") != "planned":
        raise ControlledLearningError(
            f"只有 planned 可以启动，当前 {exp.get('status')}")
    _check_plan(exp)
    approval = exp.get("approval") if isinstance(exp.get("approval"), dict) else {}
    if not _text(approval.get("by")):
        raise ControlledLearningError(
            "REVIEW_REQUIRED：实验还没有人审批准，不能开跑")
    if _text(approval.get("plan_sha256")) != _text(exp.get("plan_sha256")):
        raise ControlledLearningError(
            "REVIEW_REQUIRED：批准绑定的计划与当前计划不一致，需要重新批准")
    if dry_run:
        return dict(exp, dry_run=True, would_start=True)
    started = experiments.start_experiment(Path(data_dir), exp_id)
    # start_experiment 会重算 experiments 的预注册指纹；受控指纹保持不变。
    return started


def _valid_observations(exp: Dict[str, Any]) -> List[Dict[str, Any]]:
    metric = _text(exp.get("primary_metric"))
    out = []
    for obs in exp.get("observations") or []:
        if not isinstance(obs, dict):
            continue
        data = obs.get("data") if isinstance(obs.get("data"), dict) else {}
        value = data.get(metric)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(obs)
    return out


def record_observation(data_dir, exp_id: str, *, note: str, value=None,
                       data: Optional[Dict[str, Any]] = None,
                       evidence_ref: str = "", evidence_hash: str = "",
                       approximated: bool = False) -> Dict[str, Any]:
    """记一条观察。同一 evidence（hash/ref）只记一次——重复提交是 no-op。"""
    exp = load_experiment(data_dir, exp_id)
    _check_plan(exp)
    payload = dict(data or {})
    if value is not None:
        payload[_text(exp.get("primary_metric"))] = value
    ref = _text(evidence_ref)
    digest = _text(evidence_hash) or (
        _sha({"ref": ref, "data": _norm(payload)}) if (ref or payload) else "")
    if digest:
        for obs in exp.get("observations") or []:
            if isinstance(obs, dict) and _text(obs.get("evidence_hash")) == digest:
                return {"recorded": False, "reason": "duplicate_evidence",
                        "evidence_hash": digest,
                        "sample": len(_valid_observations(exp))}
    if exp.get("status") != "running":
        raise ControlledLearningError(
            f"{exp.get('status')} 状态不接受新观察——观察只在 running 期有效")
    experiments.record_observation(
        Path(data_dir), exp_id, note=note, data=payload,
        evidence_ref=ref, approximated=bool(approximated),
        evidence_hash=digest)
    exp = load_experiment(data_dir, exp_id)
    return {"recorded": True, "evidence_hash": digest,
            "sample": len(_valid_observations(exp)),
            "analysis": analyze_experiment(data_dir, exp_id)}


def stop_experiment(data_dir, exp_id: str, *, reason: str,
                    by: str = "") -> Dict[str, Any]:
    if not _text(reason):
        raise ControlledLearningError("停止实验必须写原因（reason）")
    if not _text(by):
        raise ControlledLearningError("停止实验必须署名（by）")
    exp = experiments.stop_experiment(Path(data_dir), exp_id, reason=_text(reason))
    return experiments.update_fields(
        Path(data_dir), exp_id,
        stopped={"reason": _text(reason), "by": _text(by), "at": _now()})


def analyze_experiment(data_dir, exp_id: str) -> Dict[str, Any]:
    """只读分析：给出口径与措辞，不自动下结论。"""
    exp = load_experiment(data_dir, exp_id)
    _check_plan(exp)
    valid = _valid_observations(exp)
    sample = len(valid)
    declared = int(exp.get("min_sample_declared") or 0)
    randomized = bool(exp.get("randomized")) and bool(_text(exp.get("assignment_ref")))
    if sample == 0:
        mode = "no_data"
        can_conclude = False
    elif sample < declared:
        mode = "exploration"
        can_conclude = False
    elif randomized:
        mode = "causal_allowed"
        can_conclude = True
    else:
        mode = "exploration"
        can_conclude = True
    can_claim_causality = mode == "causal_allowed"
    limitations = [ _text(exp.get("analysis_limitations")) ] if _text(
        exp.get("analysis_limitations")) else []
    limitations.append("相关性与同期变化不是因果；结论由人判定")
    if not randomized:
        limitations.append("没有随机分组：只能做描述性前后对照")
    if sample == 0:
        wording = "无数据：还没有有效观察，这不是「无效」，是还没测"
    elif mode == "exploration" and sample < declared:
        wording = (f"探索性结论：{sample} 条有效观察，低于预声明门槛 {declared}；"
                   "只能提示方向，不能宣称策略胜出")
    elif mode == "exploration":
        wording = (f"描述性前后对照：{sample} 条有效观察达到数量门槛，但无随机分组；"
                   "不能宣称策略胜出")
    else:
        wording = (f"随机分组样本 {sample} 条达到预声明门槛 {declared}："
                   "可以使用实验因果口径；结论仍由人判定并署名")
    return {
        "exp_id": exp_id,
        "status": exp.get("status"),
        "sample": sample,
        "min_sample_declared": declared,
        "randomized": randomized,
        "assignment_ref": _text(exp.get("assignment_ref")),
        "analysis_mode": mode,
        "can_conclude": can_conclude,
        "can_claim_causality": can_claim_causality,
        "can_claim_winner": can_claim_causality,
        "wording": wording,
        "limitations": limitations,
        "next_action": ("先补有效观察" if sample == 0 else
                        ("由人判定" if can_conclude else
                         f"继续采集到 {declared} 条再判定")),
        "plan_sha256_ok": True,
    }


# ---------------------------------------------------------------------------
# §12.5 可证伪实验：三种替代 + 七项结果指标
# ---------------------------------------------------------------------------

def freeze_comparison(data_dir, exp_id: str, *, product: str,
                      channels: Sequence[str], quota: Dict[str, Any], window,
                      cost_basis: str,
                      arms: Optional[Sequence[Dict[str, Any]]] = None,
                      dry_run: bool = False) -> Dict[str, Any]:
    """冻结「跟谁比、比什么、在什么条件下比」。冻结后同 hash 幂等。"""
    bounds = _bounds(window)
    missing = [name for name, ok in (
        ("product", bool(_text(product))),
        ("channels", bool(channels)),
        ("quota", bool(quota)),
        ("window", bounds is not None),
        ("cost_basis", bool(_text(cost_basis))),
    ) if not ok]
    if missing:
        raise ControlledLearningError(
            "比较上下文缺项：" + "、".join(missing)
            + "（冻结同一产品定位/渠道配额/时间窗/成本口径才能比较）")
    chosen = list(arms or COMPARISON_ARMS)
    names = [_text(a.get("name")) for a in chosen if isinstance(a, dict)]
    if names != [a["name"] for a in COMPARISON_ARMS]:
        raise ControlledLearningError(
            "三种替代必须齐全且顺序固定："
            + "、".join(a["name"] for a in COMPARISON_ARMS))
    payload = {
        "product": _text(product),
        "channels": [str(c) for c in channels],
        "quota": _norm(quota),
        "window": {"start": bounds[0], "end": bounds[1]},
        "cost_basis": _text(cost_basis),
        "arms": [
            {"name": _text(a.get("name")),
             "description": _text(a.get("description")) or
             next((d["description"] for d in COMPARISON_ARMS
                   if d["name"] == a.get("name")), "")}
            for a in chosen],
    }
    comparison = dict(payload, comparison_hash=_sha(payload),
                      frozen_at=_now(),
                      note=("比较口径已冻结；冻结后不能靠换时间窗/换渠道"
                            "把结果说成有效"))
    exp = load_experiment(data_dir, exp_id)
    existing = exp.get("comparison") if isinstance(exp.get("comparison"), dict) else {}
    if existing and _text(existing.get("comparison_hash")) != comparison["comparison_hash"]:
        raise ControlledLearningError(
            "该实验已经冻结过另一套比较上下文（hash "
            f"{_text(existing.get('comparison_hash'))[:20]}…）：要换就新建实验")
    if dry_run:
        return dict(comparison, dry_run=True)
    experiments.update_fields(Path(data_dir), exp_id, comparison=comparison)
    return comparison


def _human_minutes(data_dir: Path, run_id: str,
                   bounds: Optional[Tuple[str, str]]) -> Optional[int]:
    path = data_dir / "grow" / f"{run_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    telemetry = data.get("telemetry") if isinstance(data.get("telemetry"), dict) else {}
    total = 0
    seen = False
    weeks = telemetry.get("weeks") if isinstance(telemetry.get("weeks"), dict) else {}
    for day, entry in (weeks or {}).items():
        if not isinstance(entry, dict) or not _in_bounds(day, bounds):
            continue
        value = entry.get("human_minutes")
        if isinstance(value, int) and not isinstance(value, bool):
            seen = True
            total += value
    days = telemetry.get("days") if isinstance(telemetry.get("days"), dict) else {}
    if not seen:
        for day, entry in (days or {}).items():
            if not isinstance(entry, dict) or not _in_bounds(day, bounds):
                continue
            value = entry.get("human_minutes")
            if isinstance(value, int) and not isinstance(value, bool):
                seen = True
                total += value
    return total if seen else None


def falsification_metrics(data_dir, *, run_id: Optional[str] = None,
                          window=None) -> Dict[str, Any]:
    """§12.5 的七项结果指标。取不到的保持 None（unknown），不写 0。"""
    from . import outcome_layers

    data_dir = Path(data_dir)
    bounds = _bounds(window)
    approved = 0
    for rec in Ledger(data_dir / "ledger.jsonl").all():
        if _text(rec.get("result")) != "committed":
            continue
        if run_id and not _belongs(_text(rec.get("campaign")), run_id):
            continue
        if not _in_bounds(rec.get("ts"), bounds):
            continue
        approved += 1
    summary = outcome_layers.layer_summary(
        data_dir, run_id=run_id, window=window)
    l2 = summary.get("L2") or {}
    l3 = summary.get("L3") or {}
    meaningful = (int(l2.get("unique_subjects") or 0)
                  if l2.get("state") != "UNKNOWN" else None)
    activations = (int(l3.get("activation") or 0)
                   if l3.get("source_connected") else None)
    events = outcome_layers.read_product_events(
        data_dir, run_id=run_id, window=window)
    qualified = None
    if events:
        from .attribution import OutcomeLedger
        ledger_view = OutcomeLedger(data_dir)
        subjects = set()
        states = outcome_layers.qualification_states(data_dir)
        for rec in events:
            if not states.get(_text(rec.get("event_id")), True):
                continue
            etype = _text(rec.get("event_type"))
            if etype in ("meaningful_conversation", "activation", "paid"):
                subject = _text(rec.get("subject_id"))
                if subject:
                    subjects.add(_text(ledger_view.canonical_subject(subject)))
        qualified = len(subjects)
    human = _human_minutes(data_dir, run_id, bounds) if run_id else None
    metrics = {
        "approved_actions": approved,
        "meaningful_responses": meaningful,
        "unique_qualified_prospects": qualified,
        "independently_observed_activations": activations,
        "human_minutes": human,
        "total_execution_cost": None,
        "policy_incidents": None,
    }
    unknowns = [name for name in FALSIFICATION_FIELDS
                if metrics.get(name) is None]
    return dict(metrics, run=run_id or "", window=window or {},
                unknowns=unknowns,
                note=("能归集的按可追溯来源记账；不能归集的保持 unknown。"
                      "committed 只证明发出，不等于业务结果；不按粉丝数/发送数收费"))


def render_falsification(metrics: Dict[str, Any]) -> str:
    labels = {
        "approved_actions": "已批准并确认的动作",
        "meaningful_responses": "目标人群实质回应",
        "unique_qualified_prospects": "独立合格对象（有回应或产品事件）",
        "independently_observed_activations": "独立观察到的激活",
        "human_minutes": "人工分钟",
        "total_execution_cost": "执行总成本",
        "policy_incidents": "政策事件",
    }
    lines = ["# 可证伪实验的七项结果（缺项保持 unknown）", ""]
    for name in FALSIFICATION_FIELDS:
        value = metrics.get(name)
        lines.append(f"- {labels[name]}："
                     + ("取不到（unknown）" if value is None else f"{value:,}"))
    lines += ["", "边界：能归集的按来源记账；没来源的不写 0；"
                  "不按粉丝数、发送数或未经确认的潜客数收费。", ""]
    return "\n".join(lines)
