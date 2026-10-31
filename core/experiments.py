#!/usr/bin/env python3
"""实验引擎 —— 预注册的增长实验台账（Growovo「量」的第一块）。

data/experiments/<exp_id>.json 每个实验一份：
  {exp_id, hypothesis, metric, metric_spec, decision_rule,
   window{start,end}, variant, owner, status, observations[], conclusion}

纪律（与 askovo/SURE 证据契约对齐）：
1. 判定规则必须在 running 之前预注册（decision_rule 非空才允许启动），
   事后改规则等于没做实验；
2. 机器只做确定性聚合（count/sum/latest），实验结论 met/not_met/inconclusive
   由人判定并署名（decided_by），机器不产出因果声明；
3. 每条 observation 可带 evidence_ref（指向账本/研究记录），无出处的数据
   必须标注 approximated=true；
4. 观察只在 running 状态接受（启动前/判定后追加 = 破坏预注册纪律，拒）；
   启动时对预注册字段存 sha256 指纹，观察/判定前校验未被改动（防手改文件
   事后凑数）；启动/停止记入 change_log。

状态机：planned → running → analyzed；任意状态可 stopped。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

EXP_STATUSES = ("planned", "running", "analyzed", "stopped")
AGGS = ("count", "sum", "latest")
PREREG_FIELDS = ("hypothesis", "metric", "metric_spec", "decision_rule", "window", "variant")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


class ExperimentError(ValueError):
    pass


def _exp_path(data_dir: Path, exp_id: str) -> Path:
    return Path(data_dir) / "experiments" / f"{exp_id}.json"


def load_experiment(data_dir: Path, exp_id: str) -> dict:
    path = _exp_path(data_dir, exp_id)
    if not path.is_file():
        raise ExperimentError(f"experiment {exp_id!r} not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _prereg_hash(exp: dict) -> str:
    payload = json.dumps({k: exp.get(k) for k in PREREG_FIELDS},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _check_prereg(exp: dict) -> None:
    """启动后预注册字段不得改动；旧实验（无指纹）跳过校验以保持兼容。"""
    stored = exp.get("prereg_sha256")
    if stored and stored != _prereg_hash(exp):
        raise ExperimentError(
            "预注册字段与启动时指纹不一致（hypothesis/metric/metric_spec/"
            "decision_rule/window/variant 被改动）——预注册纪律被破坏，拒绝继续。"
            "如确需变更，停止本实验并新建。")


def _save(data_dir: Path, exp: dict) -> None:
    path = _exp_path(data_dir, exp["exp_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(exp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def new_experiment(data_dir: Path, *, exp_id: str, hypothesis: str, metric: str,
                   decision_rule: str, start: str, end: str,
                   variant: str = "", owner: str = "", field: str = "",
                   agg: str = "count", campaign: str = "", source: str = "") -> dict:
    if not _ID_RE.match(exp_id or ""):
        raise ExperimentError("exp_id 只允许小写字母/数字/连字符（2–41 字符）")
    for name, value in (("hypothesis", hypothesis), ("metric", metric),
                        ("decision_rule", decision_rule), ("start", start), ("end", end)):
        if not str(value or "").strip():
            raise ExperimentError(f"{name} 不能为空（预注册纪律：规则先于启动）")
    if agg not in AGGS:
        raise ExperimentError(f"agg 只支持 {AGGS}")
    if _exp_path(data_dir, exp_id).exists():
        raise ExperimentError(f"experiment {exp_id!r} already exists")
    exp = {
        "exp_id": exp_id,
        "hypothesis": hypothesis,
        "metric": metric,
        "metric_spec": {"field": field or metric, "agg": agg,
                        **({"source": source} if source else {})},
        "decision_rule": decision_rule,
        "window": {"start": start, "end": end},
        "variant": variant,
        "owner": owner,
        "campaign": campaign,
        "status": "planned",
        "observations": [],
        "conclusion": None,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    _save(data_dir, exp)
    return exp


def start_experiment(data_dir: Path, exp_id: str) -> dict:
    exp = load_experiment(data_dir, exp_id)
    if exp["status"] != "planned":
        raise ExperimentError(f"只有 planned 可启动，当前 {exp['status']}")
    exp["status"] = "running"
    exp["started_at"] = datetime.now().isoformat(timespec="seconds")
    exp["prereg_sha256"] = _prereg_hash(exp)  # 冻结预注册字段指纹
    exp.setdefault("change_log", []).append(
        {"ts": exp["started_at"], "action": "start",
         "note": f"预注册指纹 {exp['prereg_sha256'][:12]}…"})
    _save(data_dir, exp)
    return exp


def record_observation(data_dir: Path, exp_id: str, *, note: str,
                       data: dict | None = None, evidence_ref: str = "",
                       approximated: bool = False) -> dict:
    exp = load_experiment(data_dir, exp_id)
    if exp["status"] != "running":
        raise ExperimentError(
            f"{exp['status']} 状态不接受新观察——观察只在 running 期有效"
            "（启动前没有可观察对象，判定后追加 = 事后凑数）")
    _check_prereg(exp)
    if not str(note or "").strip():
        raise ExperimentError("note 不能为空")
    if not evidence_ref and not approximated:
        raise ExperimentError("无 evidence_ref 的观察必须显式 approximated=true")
    exp["observations"].append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "data": data or {},
        "evidence_ref": evidence_ref,
        "approximated": bool(approximated),
    })
    _save(data_dir, exp)
    return exp


def observe_from_outcomes(data_dir: Path, exp_id: str) -> dict:
    """从 outcome 事件台账自动生成一条观察值（记账自动化）。

    metric_spec 约定：
      {"source": "outcomes", "field": "signup", "agg": "count"|"sum"}
        field ∈ signal|signup|activation|paid：count=去重主体数，sum=事件条数
      {"source": "outcomes", "field": "signup_rate"}
        <阶段>_rate = 该阶段去重主体 / signal 去重主体（比值口径，非因果）
    campaign 过滤取实验自身的 campaign 字段；为空表示全部事件。
    """
    from .attribution import FUNNEL, OutcomeLedger

    exp = load_experiment(data_dir, exp_id)
    if exp["status"] != "running":
        raise ExperimentError(f"{exp['status']} 状态不接受新观察（观察只在 running 期有效）")
    spec = exp.get("metric_spec") or {}
    if spec.get("source") != "outcomes":
        raise ExperimentError(
            "该实验的 metric_spec.source 不是 outcomes——用 --data 手工观察，"
            "或新建实验时指定 --metric-field/--metric-source")
    field = spec.get("field") or exp.get("metric")
    campaign = exp.get("campaign") or ""
    events = [r for r in OutcomeLedger(data_dir).records(campaign or None)
              if r.get("kind") == "event"]

    def subjects(stage):
        return {r.get("subject") for r in events if r.get("event_type") == stage}

    if field.endswith("_rate"):
        base = field[:-5]
        if base not in FUNNEL:
            raise ExperimentError(f"无法解析比率指标 {field!r}（应为 <stage>_rate）")
        denom = len(subjects("signal"))
        value = round(len(subjects(base)) / denom, 4) if denom else None
    elif field in FUNNEL:
        value = len(subjects(field)) if spec.get("agg", "count") == "count" else \
            sum(1 for r in events if r.get("event_type") == field)
    else:
        raise ExperimentError(f"field {field!r} 不是漏斗阶段，也不是 <stage>_rate")
    if value is None:
        raise ExperimentError("分母为 0（没有 signal 事件）——先补访问口径再观察")
    return record_observation(
        data_dir, exp_id,
        note=f"自动聚合 outcome 事件（campaign={campaign or '全部'}；口径为比值/计数，非因果）",
        data={field: value}, evidence_ref="outcomes/events.jsonl")


def aggregate(exp: dict):
    """确定性聚合观测值；metric_spec 决定字段与聚合方式。不解释、不外推。"""
    field = exp["metric_spec"]["field"]
    agg = exp["metric_spec"]["agg"]
    values = [obs.get("data", {}).get(field) for obs in exp["observations"]]
    values = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if agg == "count":
        return len(values)
    if agg == "sum":
        return sum(values) if values else None
    return values[-1] if values else None


def conclude(data_dir: Path, exp_id: str, *, result: str, decided_by: str,
             evidence_refs=None) -> dict:
    exp = load_experiment(data_dir, exp_id)
    if exp["status"] not in ("running", "analyzed"):
        raise ExperimentError(f"{exp['status']} 状态不能判定（running 后由人判定）")
    if result not in ("met", "not_met", "inconclusive"):
        raise ExperimentError("result 只允许 met / not_met / inconclusive")
    if not str(decided_by or "").strip():
        raise ExperimentError("结论必须署名（decided_by）——机器不判定实验成败")
    _check_prereg(exp)
    if exp["status"] == "analyzed" and exp.get("conclusion"):
        raise ExperimentError("已有结论——改判应新建实验或先停止（保留原判定历史）")
    exp["conclusion"] = {
        "result": result,
        "decided_by": decided_by,
        "decided_at": datetime.now().isoformat(timespec="seconds"),
        "aggregate": aggregate(exp),
        "evidence_refs": list(evidence_refs or []),
        "note": "结论按预注册规则由人判定；聚合值不构成因果声明",
    }
    exp["status"] = "analyzed"
    _save(data_dir, exp)
    return exp


def stop_experiment(data_dir: Path, exp_id: str, *, reason: str) -> dict:
    exp = load_experiment(data_dir, exp_id)
    if exp["status"] != "stopped":
        exp.setdefault("change_log", []).append(
            {"ts": datetime.now().isoformat(timespec="seconds"),
             "action": "stop", "reason": reason})
    exp["status"] = "stopped"
    exp["stopped_reason"] = reason
    _save(data_dir, exp)
    return exp
