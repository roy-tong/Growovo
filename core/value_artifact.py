#!/usr/bin/env python3
"""Value Artifact：可复现、可观察、带边界的用户价值资产。

支持教程、样例、可复现测试、模板、对比和真实案例。``lint`` 返回错误列表；
``build`` 在校验通过后返回规范化副本；``render`` 渲染业务正文。全部只处理
结构化 dict，不访问网络、不调用模型、不写文件。

曝光、阅读、点击、发送、报名等是传播或过程指标，不能占 ``observation_events``
的用户价值槽位。价值观察必须显式声明非近似口径；已观察的激活/付费还需核验。
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List

ARTIFACT_TYPES = (
    "tutorial", "example", "reproducible_test", "template", "comparison", "real_case",
)

_KIND_ALIASES = {
    "tutorial": "tutorial", "guide": "tutorial", "教程": "tutorial",
    "example": "example", "sample": "example", "样例": "example", "示例": "example",
    "reproducible_test": "reproducible_test", "reproduction_test": "reproducible_test",
    "repro_test": "reproducible_test", "可复现测试": "reproducible_test",
    "template": "template", "模板": "template",
    "comparison": "comparison", "compare": "comparison", "对比": "comparison",
    "real_case": "real_case", "case_study": "real_case", "case": "real_case",
    "真实案例": "real_case", "案例": "real_case",
}

VALUE_EVENTS = {
    "task_completed", "reproduction_passed", "activation", "retained_use",
    "repeat_use", "continued_use", "paid",
}
ATTENTION_EVENTS = {
    "impression", "impressions", "exposure", "reach", "view", "views", "page_view",
    "visit", "click", "open", "like", "share", "follow", "signal", "sent", "committed",
    "engagement", "signup", "trial", "trial_started",
}
_EVENT_ALIASES = {
    "payment": "paid", "purchase": "paid", "activated": "activation",
    "retention": "retained_use", "task_complete": "task_completed",
    "reproduction_pass": "reproduction_passed",
}
_EVENT_LABELS = {
    "task_completed": "完成目标任务",
    "reproduction_passed": "通过复现测试",
    "activation": "完成产品激活",
    "retained_use": "持续使用",
    "repeat_use": "重复使用",
    "continued_use": "持续完成任务",
    "paid": "真实付费",
}
_KIND_LABELS = {
    "tutorial": "教程", "example": "样例", "reproducible_test": "可复现测试",
    "template": "模板", "comparison": "对比", "real_case": "真实案例",
}
_REQUIRED_SCALARS = ("title", "scenario", "direct_user_value", "platform", "motion", "vertical")
_REQUIRED_LISTS = ("evidence", "reproduction_steps", "limitations", "observation_events")
_ATTENTION_VALUE = re.compile(
    r"^(?:(?:提升|增加|获得|扩大|提高|带来|更多|大量|产品|品牌|内容|账号|的|\s)+)?"
    r"(?:曝光|知名度|阅读量|浏览量|播放量|点击量|流量|粉丝量|关注量)$",
    re.IGNORECASE,
)


class ArtifactError(ValueError):
    """资产不满足可复现或价值观察契约。"""


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _first(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _kind(data: Dict[str, Any]) -> str:
    raw = str(_first(data, "kind", "artifact_type", "type") or "").strip().lower()
    return _KIND_ALIASES.get(raw, "")


def _canonical(data: Dict[str, Any]) -> Dict[str, Any]:
    """只整理校验所需字段；不修改原对象。"""
    out = dict(data)
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    pmv = data.get("platform_motion_vertical") if isinstance(data.get("platform_motion_vertical"), dict) else {}
    out["kind"] = _kind(data)
    out["scenario"] = _first(data, "scenario", "demand_scenario", "need_scenario")
    out["direct_user_value"] = _first(data, "direct_user_value", "user_direct_value", "direct_value")
    out["evidence"] = _first(data, "evidence", "basis", "rationale")
    out["reproduction_steps"] = _first(data, "reproduction_steps", "steps")
    out["observation_events"] = _first(data, "observation_events", "observations", "value_events")
    for key in ("platform", "motion", "vertical"):
        out[key] = _first(data, key) or _first(context, key) or _first(pmv, key)
    return out


def _nonempty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_nonempty(v) for v in value.values())
    return value is not None


def _valid_verification(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    status = str(value.get("status") or "verified").strip().lower()
    return (status in ("verified", "confirmed", "passed")
            and bool(str(value.get("method") or "").strip())
            and bool(str(value.get("ref") or "").strip()))


def _lint_scenario(scenario: Any) -> List[str]:
    if isinstance(scenario, dict):
        errors = []
        if not _nonempty(_first(scenario, "user", "audience", "role")):
            errors.append("scenario 缺少 user/audience：谁在这个场景里")
        if not _nonempty(scenario.get("trigger")):
            errors.append("scenario.trigger 必填：什么触发需求")
        if not _nonempty(_first(scenario, "task", "outcome")):
            errors.append("scenario.task/outcome 必填：用户要完成什么")
        return errors
    return []


def lint(artifact: Dict[str, Any]) -> List[str]:
    """返回全部校验错误；空列表表示可构建。不会只报第一个错误。"""
    if not isinstance(artifact, dict):
        return ["artifact 必须是结构化 dict"]
    data = _canonical(artifact)
    errors: List[str] = []
    if not data["kind"]:
        errors.append(f"kind 必须是 {list(ARTIFACT_TYPES)} 之一（支持对应中文别名）")
    for key in _REQUIRED_SCALARS:
        if not _nonempty(data.get(key)):
            errors.append(f"缺必填字段或内容为空: {key}")
    for key in _REQUIRED_LISTS:
        if not isinstance(data.get(key), (list, tuple)) or not data.get(key):
            errors.append(f"{key} 必须是非空列表")
    errors.extend(_lint_scenario(data.get("scenario")))

    value = data.get("direct_user_value")
    if isinstance(value, str) and _ATTENTION_VALUE.match(re.sub(r"[。！!.，,\s]+", "", value.strip())):
        errors.append("direct_user_value 不能只是曝光、流量或关注；要写用户完成任务后的直接收益")

    evidence = data.get("evidence")
    if isinstance(evidence, (list, tuple)):
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                errors.append(f"evidence[{index}] 必须是 dict，且包含 summary 与 source_ref")
                continue
            if not _nonempty(_first(item, "summary", "text", "claim")):
                errors.append(f"evidence[{index}].summary 必填")
            if not _nonempty(item.get("source_ref")):
                errors.append(f"evidence[{index}].source_ref 必填：依据必须可回溯")

    steps = data.get("reproduction_steps")
    if isinstance(steps, (list, tuple)):
        for index, step in enumerate(steps):
            if isinstance(step, str):
                if not step.strip():
                    errors.append(f"reproduction_steps[{index}] 不能为空")
            elif isinstance(step, dict):
                if not _nonempty(_first(step, "action", "step", "text")):
                    errors.append(f"reproduction_steps[{index}].action 必填")
            else:
                errors.append(f"reproduction_steps[{index}] 必须是字符串或 dict")

    limitations = data.get("limitations")
    if isinstance(limitations, (list, tuple)):
        for index, item in enumerate(limitations):
            if not isinstance(item, (str, int, float)) or not str(item).strip():
                errors.append(f"limitations[{index}] 必须是非空文字")

    observations = data.get("observation_events")
    observed_real_case = False
    if isinstance(observations, (list, tuple)):
        for index, event in enumerate(observations):
            if not isinstance(event, dict):
                errors.append(f"observation_events[{index}] 必须是 dict")
                continue
            raw_type = str(_first(event, "event_type", "type") or "").strip().lower()
            event_type = _EVENT_ALIASES.get(raw_type, raw_type)
            if event_type in ATTENTION_EVENTS or raw_type in ATTENTION_EVENTS:
                errors.append(f"observation_events[{index}] 的 {raw_type} 是过程/曝光指标，不能作为用户价值事件")
            elif event_type not in VALUE_EVENTS:
                errors.append(f"observation_events[{index}].event_type 必须是 {sorted(VALUE_EVENTS)} 之一")
            if not _nonempty(_first(event, "source", "source_ref")):
                errors.append(f"observation_events[{index}].source 必填：说明从哪里观察")
            if not isinstance(event.get("approximated"), bool):
                errors.append(f"observation_events[{index}].approximated 必须显式 true/false")
            elif event["approximated"]:
                errors.append(f"observation_events[{index}] 不允许近似口径充当用户价值")
            observed = event.get("observed") is True
            if observed:
                observed_real_case = True
                for key in ("subject", "source_ref"):
                    if not _nonempty(event.get(key)):
                        errors.append(f"observation_events[{index}] 已观察时必须带 {key}")
                if not _nonempty(_first(event, "ts", "observed_at")):
                    errors.append(f"observation_events[{index}] 已观察时必须带 ts/observed_at")
                if event_type in ("activation", "paid") and not _valid_verification(event.get("verification")):
                    errors.append(f"observation_events[{index}] 已观察的 activation/paid 必须带 verification(method,ref)")

    kind = data["kind"]
    if kind == "reproducible_test":
        has_expected = _nonempty(artifact.get("expected_result"))
        if isinstance(steps, (list, tuple)):
            has_expected = has_expected or any(
                isinstance(step, dict) and _nonempty(_first(step, "expected_result", "expected"))
                for step in steps)
        if not has_expected:
            errors.append("可复现测试必须有 expected_result 或步骤内 expected_result")
    if kind in ("example", "template") and not _nonempty(_first(artifact, "body", "example", "template_body")):
        errors.append(f"{_KIND_LABELS[kind]}必须带 body/example/template_body 正文")
    if kind == "comparison":
        comparison = artifact.get("comparison")
        if not isinstance(comparison, dict):
            errors.append("对比资产必须带 comparison.criteria 与 comparison.alternatives")
        else:
            if not isinstance(comparison.get("criteria"), (list, tuple)) or not comparison.get("criteria"):
                errors.append("comparison.criteria 必须是非空列表")
            alternatives = comparison.get("alternatives")
            if not isinstance(alternatives, (list, tuple)) or len(alternatives) < 2:
                errors.append("comparison.alternatives 至少要有两个可比较对象")
    if kind == "real_case" and not observed_real_case:
        errors.append("真实案例至少需要一条 observed=true 的价值观察，并带来源、主体与时间")
    return errors


def build(artifact: Dict[str, Any]) -> Dict[str, Any]:
    """校验并返回规范化深拷贝；任何错误都会拒绝构建。"""
    errors = lint(artifact)
    if errors:
        raise ArtifactError("；".join(errors))
    data = _canonical(artifact)
    out = copy.deepcopy(artifact)
    out["schema_version"] = "1.0"
    out["kind"] = data["kind"]
    out["scenario"] = copy.deepcopy(data["scenario"])
    out["direct_user_value"] = data["direct_user_value"]
    for key in ("platform", "motion", "vertical"):
        out[key] = data[key]
    out["evidence"] = []
    for item in data["evidence"]:
        row = copy.deepcopy(item)
        row["summary"] = _first(item, "summary", "text", "claim")
        out["evidence"].append(row)
    out["reproduction_steps"] = []
    for step in data["reproduction_steps"]:
        if isinstance(step, str):
            out["reproduction_steps"].append({"action": step, "expected_result": ""})
        else:
            row = copy.deepcopy(step)
            row["action"] = _first(step, "action", "step", "text")
            row["expected_result"] = _first(step, "expected_result", "expected") or ""
            out["reproduction_steps"].append(row)
    out["limitations"] = [str(x).strip() for x in data["limitations"]]
    out["observation_events"] = []
    for event in data["observation_events"]:
        row = copy.deepcopy(event)
        raw_type = str(_first(event, "event_type", "type") or "").strip().lower()
        row["event_type"] = _EVENT_ALIASES.get(raw_type, raw_type)
        row["name"] = event.get("name") or _EVENT_LABELS[row["event_type"]]
        row["source"] = _first(event, "source", "source_ref")
        row["observed"] = event.get("observed") is True
        out["observation_events"].append(row)
    out["value_policy"] = {
        "attention_is_not_value": True,
        "approximation_allowed": False,
        "note": "曝光、阅读、点击、发送和报名只能用于过程诊断；资产价值以真实任务、激活、持续使用或付费观察为准。",
    }
    return out


def render(artifact: Dict[str, Any]) -> str:
    """把合格资产渲染为业务正文；不会先渲染再补免责声明。"""
    data = build(artifact)
    lines = [f"# {data['title']}", "",
             f"> {_KIND_LABELS[data['kind']]}｜用户直接价值：{data['direct_user_value']}", ""]
    lines.extend(["## 需求场景", ""])
    scenario = data["scenario"]
    if isinstance(scenario, dict):
        lines.append(f"- 用户：{_first(scenario, 'user', 'audience', 'role')}")
        lines.append(f"- 触发：{scenario['trigger']}")
        lines.append(f"- 任务：{_first(scenario, 'task', 'outcome')}")
        if scenario.get("current_substitute"):
            lines.append(f"- 当前替代：{scenario['current_substitute']}")
    else:
        lines.append(str(scenario))
    lines.append("")

    if data.get("body") or data.get("example") or data.get("template_body"):
        lines.extend(["## 业务正文", "",
                      str(_first(data, "body", "example", "template_body")), ""])

    lines.extend(["## 依据", ""])
    for item in data["evidence"]:
        lines.append(f"- {item['summary']}（来源：{item['source_ref']}）")
    lines.append("")
    lines.extend(["## 复现步骤", ""])
    for index, step in enumerate(data["reproduction_steps"], 1):
        lines.append(f"{index}. {step['action']}")
        if step.get("expected_result"):
            lines.append(f"   - 预期结果：{step['expected_result']}")
    lines.append("")

    if data["kind"] == "comparison":
        comparison = data["comparison"]
        lines.extend(["## 对比口径", ""])
        lines.append("- 判据：" + "、".join(str(x) for x in comparison["criteria"]))
        names = [str(x.get("name", "")) if isinstance(x, dict) else str(x)
                 for x in comparison["alternatives"]]
        lines.append("- 对象：" + "、".join(names))
        lines.append("")

    lines.extend(["## 适用范围与限制", "",
                  f"- Platform × Motion × Vertical：{data['platform']} × {data['motion']} × {data['vertical']}"])
    for limitation in data["limitations"]:
        lines.append(f"- 限制：{limitation}")
    lines.append("")
    lines.extend(["## 观察什么才算用户价值", ""])
    for event in data["observation_events"]:
        state = "已观察" if event["observed"] else "待观察"
        lines.append(f"- {event['name']}（{_EVENT_LABELS[event['event_type']]}，{state}；来源：{event['source']}；非近似口径）")
    lines.extend(["", "> 曝光、阅读、点击、发送和报名只能帮助诊断传播过程，不能冒充上述用户价值或商业结果。"])
    return "\n".join(lines)


# 与仓库 lint/render 习惯并存，同时给主线程一个语义明确的 validate 名称。
validate = lint
render_artifact = render
