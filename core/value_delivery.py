#!/usr/bin/env python3
"""研究到用户完成的价值资产交付状态机。

本地复现是唯一会启动子进程的路径：必须使用 argv（shell=False），并由绑定命令
指纹的显式授权解锁。交付只登记外部授权与真实回执，本模块不发送平台消息。
曝光/发送回执不能进入用户完成状态；没有真实复现记录也不能构建 verified 资产。
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import value_artifact


STAGES = ("research", "solution", "reproduction", "asset",
          "authorized_delivery", "user_readback", "completed")
VALUE_EVENTS = {"task_completed", "activation", "retained_use", "repeat_use", "continued_use", "paid"}
ATTENTION_EVENTS = set(value_artifact.ATTENTION_EVENTS)


class ChainError(ValueError):
    """状态机输入、顺序或证据不满足契约。"""


class ReproductionAuthorizationError(ChainError):
    """本地命令没有绑定到明确授权。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _dt(value: Any) -> datetime:
    text = _text(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def command_fingerprint(command: Iterable[str], *, cwd: Any = "") -> str:
    """授权必须绑定规范化 argv 与工作目录，不能用笼统 authorized=true 放行。"""
    argv = [str(part) for part in command]
    resolved_cwd = str(Path(cwd).expanduser().resolve()) if cwd else ""
    payload = json.dumps({"command": argv, "cwd": resolved_cwd},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_reproduction_authorization(command: List[str], cwd: str,
                                         authorization: Any) -> Dict[str, Any]:
    if not isinstance(authorization, dict):
        raise ReproductionAuthorizationError("本地复现命令未执行：缺少 command 级授权")
    if authorization.get("authorized") is not True:
        raise ReproductionAuthorizationError("本地复现命令未执行：authorized 必须为 true")
    if not _text(authorization.get("authorized_by")) or not _text(authorization.get("authorized_at")):
        raise ReproductionAuthorizationError("本地复现命令未执行：缺少 authorized_by/authorized_at")
    try:
        _dt(authorization.get("authorized_at"))
    except (TypeError, ValueError):
        raise ReproductionAuthorizationError("本地复现命令未执行：authorized_at 不是有效时间")
    expected = command_fingerprint(command, cwd=cwd)
    if authorization.get("command_sha256") != expected:
        raise ReproductionAuthorizationError("本地复现命令未执行：命令或 cwd 与授权指纹不匹配")
    return copy.deepcopy(authorization)


def run_local_reproduction(spec: Dict[str, Any]) -> Dict[str, Any]:
    """真正运行已授权 argv，返回实际 exit code、输出、时间和 source_ref。

    任何授权错误都在 subprocess.run 之前抛出；超时没有 exit code，绝不能 verified。
    """
    if not isinstance(spec, dict):
        raise ChainError("reproduction 必须是 dict")
    raw_command = spec.get("command")
    if not isinstance(raw_command, (list, tuple)) or not raw_command:
        raise ChainError("reproduction.command 必须是非空 argv 数组；不接受 shell 字符串")
    command = [str(part) for part in raw_command]
    if any(not part for part in command):
        raise ChainError("reproduction.command 不能包含空 argv")
    cwd = str(Path(spec.get("cwd")).expanduser().resolve()) if spec.get("cwd") else ""
    if cwd and not Path(cwd).is_dir():
        raise ChainError("reproduction.cwd 不是已存在目录")
    authorization = _validate_reproduction_authorization(
        command, cwd, spec.get("authorization"))
    source_ref = _text(spec.get("source_ref"))
    if not source_ref:
        raise ChainError("本地复现命令未执行：缺少 reproduction.source_ref")
    try:
        timeout = float(spec.get("timeout_seconds", 30))
    except (TypeError, ValueError):
        raise ChainError("reproduction.timeout_seconds 必须是数字")
    if not 0.1 <= timeout <= 60:
        raise ChainError("reproduction.timeout_seconds 必须在 0.1–60 秒之间")
    expected_exit = spec.get("expected_exit_code", 0)
    if not isinstance(expected_exit, int) or isinstance(expected_exit, bool):
        raise ChainError("reproduction.expected_exit_code 必须是整数")

    started = _now()
    timed_out = False
    error = ""
    stdout = stderr = ""
    exit_code: Optional[int] = None
    try:
        completed = subprocess.run(
            command, shell=False, cwd=cwd or None, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
            check=False,
        )
        exit_code = completed.returncode
        stdout, stderr = completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        error = f"复现超过 {timeout:g} 秒，已终止；无真实 exit code，不能 verified"
    except OSError as exc:
        error = f"本地命令启动失败：{exc}"
    finished = _now()
    expected_output = _text(spec.get("expected_output"))
    output_ok = not expected_output or expected_output in stdout
    verified = bool(not timed_out and exit_code is not None and exit_code == expected_exit and output_ok)
    if not output_ok:
        error = "标准输出不包含 expected_output"
    # 限制回传体积；原始 source_ref 用来定位调用方保存的完整日志。
    stdout, stderr = stdout[-65536:], stderr[-65536:]
    return {
        "record_kind": "local_reproduction_execution", "executed": exit_code is not None,
        "command": command, "cwd": cwd, "command_sha256": command_fingerprint(command, cwd=cwd),
        "authorization": authorization, "source_ref": source_ref,
        "started_at": started, "finished_at": finished,
        "exit_code": exit_code, "expected_exit_code": expected_exit,
        "stdout": stdout, "stderr": stderr,
        "output": stdout + (("\n[stderr]\n" + stderr) if stderr else ""),
        "timed_out": timed_out, "expected_output": expected_output,
        "verified": verified, "error": error,
        "verification_basis": "真实 subprocess exit code + 捕获输出 + source_ref；不是自报 verified",
    }


class ValueDeliveryChain:
    """显式单向状态机；每次迁移都留下证据引用，不允许跨阶段。"""

    def __init__(self, chain_id: str):
        self.data: Dict[str, Any] = {
            "schema_version": "1.0", "kind": "growovo.value_delivery_chain",
            "id": _text(chain_id) or "chain-unknown", "state": "research",
            "research": None, "solution": None, "reproduction": None,
            "asset": None, "delivery": None, "user_completion": None,
            "transitions": [], "rejected_readbacks": [], "errors": [],
        }

    def snapshot(self) -> Dict[str, Any]:
        return copy.deepcopy(self.data)

    def _advance(self, expected: str, target: str, evidence_refs: Iterable[str]) -> None:
        if self.data["state"] != expected:
            raise ChainError(f"状态顺序错误：当前 {self.data['state']}，必须先完成 {expected}")
        self.data["transitions"].append({
            "from": expected, "to": target, "at": _now(),
            "evidence_refs": [str(ref) for ref in evidence_refs if str(ref).strip()],
        })
        self.data["state"] = target

    def record_research(self, research: Any) -> None:
        if not isinstance(research, dict):
            raise ChainError("research 必须是 dict")
        refs = [_text(ref) for ref in _list(research.get("source_refs")) if _text(ref)]
        if research.get("source_ref"):
            refs.insert(0, _text(research.get("source_ref")))
        refs = list(dict.fromkeys(ref for ref in refs if ref))
        subject = research.get("subject")
        if isinstance(subject, dict):
            subject_id = _text(subject.get("id") or subject.get("subject_id"))
        else:
            subject_id = _text(subject)
        problem = _text(research.get("problem") or research.get("need"))
        if not refs or not subject_id or not problem:
            raise ChainError("research 必须有 source_ref(s)、subject 与 problem；原始热度不是研究完成")
        self.data["research"] = copy.deepcopy(research)
        self.data["research"]["source_refs"] = refs
        self.data["research"]["subject_id"] = subject_id
        self._advance("research", "solution", refs)

    def define_solution(self, solution: Any) -> None:
        if not isinstance(solution, dict) or not _text(solution.get("summary")):
            raise ChainError("solution 必须有 summary")
        steps = solution.get("steps")
        if not isinstance(steps, (list, tuple)) or not steps or any(not _text(step) for step in steps):
            raise ChainError("solution.steps 必须是非空文字数组")
        self.data["solution"] = copy.deepcopy(solution)
        refs = self.data["research"]["source_refs"]
        self._advance("solution", "reproduction", refs)

    def reproduce(self, spec: Any) -> Dict[str, Any]:
        if self.data["state"] != "reproduction":
            raise ChainError("必须先研究与定义方案，才能复现")
        record = run_local_reproduction(spec)
        self.data["reproduction"] = record
        refs = [record["source_ref"]]
        if record["verified"]:
            self._advance("reproduction", "asset", refs)
        else:
            self.data["transitions"].append({
                "from": "reproduction", "to": "reproduction_failed",
                "at": _now(), "evidence_refs": refs,
            })
            self.data["state"] = "reproduction_failed"
        return copy.deepcopy(record)

    def build_asset(self, artifact: Any) -> Dict[str, Any]:
        if self.data["state"] != "asset":
            raise ChainError("缺真实且通过的本地复现，不能进入 asset/verified")
        built = value_artifact.build(artifact)
        built["reproduction_ref"] = self.data["reproduction"]["source_ref"]
        built["verification"] = {
            "status": "reproduction_verified", "source_ref": self.data["reproduction"]["source_ref"],
            "exit_code": self.data["reproduction"]["exit_code"],
            "note": "这里只核验工具复现，不表示已交付或用户已完成任务",
        }
        self.data["asset"] = built
        self._advance("asset", "authorized_delivery", [built["reproduction_ref"]])
        return copy.deepcopy(built)

    def record_delivery(self, authorization: Any, receipt: Any) -> None:
        if self.data["state"] != "authorized_delivery":
            raise ChainError("资产未准备好，不能登记交付")
        if not isinstance(authorization, dict) or authorization.get("authorized") is not True:
            raise ChainError("交付缺少显式授权；本模块不会发送")
        for key in ("authorized_by", "authorized_at"):
            if not _text(authorization.get(key)):
                raise ChainError(f"交付授权缺少 {key}")
        asset_id = _text(self.data["asset"].get("id"))
        subject_id = self.data["research"]["subject_id"]
        if _text(authorization.get("asset_id")) != asset_id or _text(authorization.get("subject")) != subject_id:
            raise ChainError("交付授权必须绑定当前 asset_id 与 research.subject")
        if not isinstance(receipt, dict) or _text(receipt.get("status")) not in ("sent", "delivered"):
            raise ChainError("交付必须有外部 sent/delivered 回执；本模块不伪造发送")
        for key in ("source_ref", "delivered_at"):
            if not _text(receipt.get(key)):
                raise ChainError(f"交付回执缺少 {key}")
        if _text(receipt.get("subject")) != subject_id:
            raise ChainError("交付回执主体与研究主体不一致")
        try:
            delivered_at = _dt(receipt["delivered_at"])
            authorized_at = _dt(authorization["authorized_at"])
        except (TypeError, ValueError):
            raise ChainError("交付授权/回执时间无效")
        if delivered_at < authorized_at:
            raise ChainError("交付时间早于授权时间，不能确认授权交付")
        self.data["delivery"] = {
            "authorization": copy.deepcopy(authorization), "receipt": copy.deepcopy(receipt),
            "status": "receipt_recorded", "user_value_verified": False,
            "note": "发送/交付只进入读回阶段，不是使用或业务转化",
        }
        self._advance("authorized_delivery", "user_readback", [receipt["source_ref"]])

    def record_user_readback(self, events: Any) -> bool:
        if self.data["state"] != "user_readback":
            raise ChainError("必须先有授权交付回执，才能读回用户完成情况")
        delivered_at = _dt(self.data["delivery"]["receipt"]["delivered_at"])
        subject_id = self.data["research"]["subject_id"]
        accepted: List[Dict[str, Any]] = []
        for event in _list(events):
            if not isinstance(event, dict):
                self.data["rejected_readbacks"].append({"reason": "事件不是 dict"})
                continue
            event_type = _text(event.get("event_type") or event.get("type")).lower()
            reason = ""
            if event_type in ATTENTION_EVENTS:
                reason = "曝光/发送/点击是过程事件，不是用户完成"
            elif event_type not in VALUE_EVENTS:
                reason = "未知事件不能升级为用户完成"
            elif event.get("observed") is not True or event.get("approximated") is True:
                reason = "用户完成必须 observed=true 且非近似"
            elif _text(event.get("subject")) != subject_id:
                reason = "读回主体与交付主体不一致"
            elif not _text(event.get("source_ref")):
                reason = "用户完成缺少 source_ref"
            else:
                try:
                    observed_at = _dt(event.get("observed_at") or event.get("ts"))
                    if observed_at < delivered_at:
                        reason = "用户完成早于交付，不能作为本次资产效果"
                except (TypeError, ValueError):
                    reason = "用户完成缺少有效 observed_at/ts"
            verification = event.get("verification")
            if not reason and (not isinstance(verification, dict)
                               or not _text(verification.get("method")) or not _text(verification.get("ref"))):
                reason = "用户完成需要 verification(method,ref)"
            if reason:
                self.data["rejected_readbacks"].append({
                    "event_type": event_type or "unknown", "source_ref": event.get("source_ref"),
                    "reason": reason,
                })
            else:
                accepted.append(copy.deepcopy(event))
        if not accepted:
            self.data["user_completion"] = {
                "status": "pending", "verified": False,
                "note": "尚无交付后的真实用户完成；保持 user_readback，不用发送代替使用",
            }
            return False
        accepted.sort(key=lambda event: _dt(event.get("observed_at") or event.get("ts")))
        self.data["user_completion"] = {
            "status": "verified", "verified": True, "events": accepted,
            "source_refs": [event["source_ref"] for event in accepted],
        }
        self._advance("user_readback", "completed", self.data["user_completion"]["source_refs"])
        return True


def run_chain(spec: Dict[str, Any]) -> Dict[str, Any]:
    """本地一次性跑完整链；失败时返回停在原阶段的真实状态，不伪造后续产物。"""
    if not isinstance(spec, dict):
        raise ChainError("chain spec 必须是 dict")
    chain = ValueDeliveryChain(spec.get("id") or spec.get("chain_id") or "chain-local")
    try:
        chain.record_research(spec.get("research"))
        chain.define_solution(spec.get("solution"))
        reproduction = chain.reproduce(spec.get("reproduction"))
        if not reproduction["verified"]:
            raise ChainError("本地复现未通过；不得生成 verified 资产或进入交付")
        chain.build_asset(spec.get("asset"))
        delivery = spec.get("delivery")
        if not isinstance(delivery, dict):
            raise ChainError("delivery 必须包含 authorization 与 receipt")
        chain.record_delivery(delivery.get("authorization"), delivery.get("receipt"))
        chain.record_user_readback(spec.get("readback_events"))
    except ChainError as exc:
        chain.data["errors"].append(str(exc))
        if chain.data["state"] not in ("reproduction_failed",):
            chain.data["state"] = "rejected" if isinstance(exc, ReproductionAuthorizationError) else chain.data["state"]
    result = chain.snapshot()
    result["complete"] = result["state"] == "completed"
    result["boundary"] = ("复现 verified 仅指本地命令结果；交付回执不是使用。"
                          "只有交付后同主体、带来源和核验的用户完成事件才能到 completed。")
    return result


run = run_chain
