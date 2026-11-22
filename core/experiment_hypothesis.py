#!/usr/bin/env python3
"""增长假设卡 —— 实验启动前的判断框架（Growovo「量」的第二块）。

experiments.py 管的是预注册与观察台账；本模块管的是「这次实验到底在验证
什么、什么结果算支持/证伪、失败后改哪一层」。没有卡就不允许建实验，
避免把 Check → Refine 退化成「结果不好就继续改内容」。

卡（growovo.experiment-hypothesis/1）必填：
  exp_id, hypothesis, support_metric, falsify_metric, decision_rule,
  alternative_explanations(>=2), next_change_on_fail ∈
  audience|channel|content|product|stop, stop_conditions(>=1),
  owner, window{start,end}, variant

纪律：
1. 支持指标与证伪指标必须分开写，且不能是同一个字段——否则「结果不好」
   没有独立的证伪条件，只能事后解释；
2. 失败后下一轮改哪一层必须事先定好（人群/渠道/内容/产品/停止），而不是
   默认继续改文案；
3. 卡随实验文件一起存 hypothesis_card，并存 sha256；卡被手改后 card-check
   会报错（experiments.py 的预注册指纹不覆盖该字段）；
4. next_change_hint 只给建议，不自动改实验状态。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import experiments

SCHEMA = "growovo.experiment-hypothesis/1"
CHANGE_TARGETS = ("audience", "channel", "content", "product", "stop")
CARD_FIELDS = ("exp_id", "hypothesis", "support_metric", "falsify_metric",
               "decision_rule", "alternative_explanations", "next_change_on_fail",
               "stop_conditions", "owner", "window", "variant")
_HINT_TEXT = {
    "audience": "换人群：当前假设可能只对某个细分成立，下一轮换受众而非改文案",
    "channel": "换渠道：先确认这个渠道有没有足够的目标读者，再谈内容好坏",
    "content": "改内容：只有在人群与渠道都成立、指标仍不动时才动文案",
    "product": "改产品：信号显示需求在但产品没接住，实验应转为产品验证",
    "stop": "停止：停止当前假设，不在同一实验上继续加轮次",
}


class HypothesisCardError(ValueError):
    pass


def _sha256(card: dict) -> str:
    payload = json.dumps(card, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _text(value) -> str:
    return str(value or "").strip()


def validate_card(card) -> list:
    """返回错误列表；空列表表示可建实验。"""
    if not isinstance(card, dict):
        return ["假设卡必须是 JSON 对象"]
    errors = []
    for name in ("exp_id", "hypothesis", "support_metric", "falsify_metric",
                 "decision_rule"):
        if not _text(card.get(name)):
            errors.append(f"{name} 不能为空")
    window = card.get("window")
    if not isinstance(window, dict):
        errors.append("window 必须是 {start,end} 对象")
    else:
        for key in ("start", "end"):
            if not _text(window.get(key)):
                errors.append(f"window.{key} 不能为空")
        start, end = _text(window.get("start")), _text(window.get("end"))
        if start and end and start > end:
            errors.append("window.start 不能晚于 window.end")
    if card.get("schema") and card.get("schema") != SCHEMA:
        errors.append(f"schema 只支持 {SCHEMA}")

    support, falsify = _text(card.get("support_metric")), _text(card.get("falsify_metric"))
    if support and falsify and support == falsify:
        errors.append("support_metric 与 falsify_metric 不能是同一指标"
                      "（同一个数无法同时承担支持与证伪）")

    alts = card.get("alternative_explanations")
    if not isinstance(alts, list) or len([a for a in alts if _text(a)]) < 2:
        errors.append("alternative_explanations 至少两条：结果不好时还有哪些解释")

    target = _text(card.get("next_change_on_fail"))
    if target not in CHANGE_TARGETS:
        errors.append(f"next_change_on_fail 只允许 {CHANGE_TARGETS}")

    stops = card.get("stop_conditions")
    if not isinstance(stops, list) or not [s for s in stops if _text(s)]:
        errors.append("stop_conditions 至少一条：什么情况下不再继续这一假设")

    if not _text(card.get("owner")):
        errors.append("owner 不能为空（假设必须有负责人）")
    if not _text(card.get("variant")):
        errors.append("variant 不能为空（说明本轮具体改动，否则无法比较轮次）")
    return errors


def normalize_card(card: dict) -> dict:
    out = {"schema": SCHEMA}
    for name in CARD_FIELDS:
        value = card.get(name)
        if name == "window":
            out[name] = {"start": _text((value or {}).get("start")),
                         "end": _text((value or {}).get("end"))}
        elif name == "alternative_explanations":
            out[name] = [str(a).strip() for a in (value or []) if _text(a)]
        elif name == "stop_conditions":
            out[name] = [str(s).strip() for s in (value or []) if _text(s)]
        else:
            out[name] = _text(value)
    return out


def create_from_card(data_dir: Path, card: dict) -> dict:
    """校验卡后建实验，并把卡写进实验文件（含卡指纹）。"""
    errors = validate_card(card)
    if errors:
        raise HypothesisCardError("假设卡不完整：" + "；".join(errors))
    normalized = normalize_card(card)
    exp = experiments.new_experiment(
        data_dir,
        exp_id=normalized["exp_id"],
        hypothesis=normalized["hypothesis"],
        metric=normalized["support_metric"],
        decision_rule=normalized["decision_rule"],
        start=normalized["window"]["start"],
        end=normalized["window"]["end"],
        variant=normalized["variant"],
        owner=normalized["owner"],
        field=normalized["support_metric"],
        agg="latest" if normalized["support_metric"].endswith("_rate") else "count",
    )
    metric_spec = {
        **(exp.get("metric_spec") or {}),
        "support_metric": normalized["support_metric"],
        "falsify_metric": normalized["falsify_metric"],
        "next_change_on_fail": normalized["next_change_on_fail"],
    }
    return experiments.update_fields(
        data_dir, normalized["exp_id"],
        metric_spec=metric_spec,
        hypothesis_card=normalized,
        hypothesis_card_sha256=_sha256(normalized),
    )


def check_card(data_dir: Path, exp_id: str) -> dict:
    """校验实验内嵌的卡：卡结构、卡指纹是否被改动。"""
    exp = experiments.load_experiment(data_dir, exp_id)
    card = exp.get("hypothesis_card")
    if not card:
        return {"exp_id": exp_id, "ok": False, "errors": ["实验没有假设卡（用 card-new 建，或补卡后重建实验）"]}
    errors = validate_card(card)
    stored = exp.get("hypothesis_card_sha256") or ""
    if stored and stored != _sha256(card):
        errors.append("假设卡与建实验时的指纹不一致（卡被改动）——改假设应新建实验")
    return {"exp_id": exp_id, "ok": not errors, "errors": errors,
            "card_sha256": _sha256(card)}


def load_card(data_dir: Path, exp_id: str) -> dict | None:
    return experiments.load_experiment(data_dir, exp_id).get("hypothesis_card")


def next_change_hint(card: dict, result: str) -> dict:
    """按结论给下一轮建议；只读，不改实验。"""
    if not isinstance(card, dict):
        raise HypothesisCardError("没有假设卡，无法给出下一轮建议")
    if result == "met":
        return {"result": result, "action": "keep",
                "note": "支持指标成立；先确认证伪指标没有反向信号，再决定复现或放大",
                "falsify_metric": card.get("falsify_metric", "")}
    if result == "inconclusive":
        return {"result": result, "action": "review_power",
                "note": "先核对样本量与观察窗口是否足够；仍不明确则按 stop_conditions 处理",
                "stop_conditions": list(card.get("stop_conditions") or [])}
    target = card.get("next_change_on_fail") or "stop"
    return {"result": result, "action": target,
            "note": _HINT_TEXT.get(target, "停止：不在同一实验上继续加轮次"),
            "alternative_explanations": list(card.get("alternative_explanations") or [])}
