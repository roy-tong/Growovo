#!/usr/bin/env python3
"""统一错误语义：七类错误码与结构化载荷。

只统一「错误怎么讲清楚」，不统一业务数据库，也不改变各模块自身的判断逻辑。
每条用户可见错误都应回答四件事：缺什么、影响是什么、合法的下一动作、示例或
修复入口。``SemanticError`` 是主动声明；其余既有异常由 ``describe()`` 按类型与
关键词尽力归类，归类不确定时落到 ``CONTRACT_ERROR`` 并保留原始信息。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

SCHEMA = "growovo.error/1"

CODES = (
    "NEEDS_USER_INPUT",       # 缺输入/缺授权署名/缺文件：补齐即可继续
    "UNSUPPORTED_CAPABILITY",  # 产品明确不做或渠道不支持
    "INVALID_INPUT",          # 输入格式或内容不合格（含内容门拒绝）
    "PERMISSION_DENIED",      # 身份/权限不足
    "SAFETY_BLOCKED",         # 安全门：暂停、抑制、配额、合规、批次未知回执
    "STALE_OR_CONFLICT",      # 版本过期、旧快照、并发冲突
    "CONTRACT_ERROR",         # 产品内部契约或路径不一致
)

# 明确类型的直接映射；未列出的按关键词与基类推断。
_CLASS_CODES = {
    "ContentUnusableError": "INVALID_INPUT",
    "PlaceholderContentError": "INVALID_INPUT",
    "DeliverableError": "INVALID_INPUT",
    "SOPError": "NEEDS_USER_INPUT",
    "QueueRunError": "SAFETY_BLOCKED",
    "GateError": "SAFETY_BLOCKED",
    "SuppressionIntegrityError": "SAFETY_BLOCKED",
    "ApprovalError": "SAFETY_BLOCKED",
    "PermissionDenied": "PERMISSION_DENIED",
    "IdentityBlocked": "PERMISSION_DENIED",
    "CompileError": "CONTRACT_ERROR",
    "OrchestrationError": "CONTRACT_ERROR",
    "HandoffError": "INVALID_INPUT",
    "GuiError": "INVALID_INPUT",
    "PlatformError": "UNSUPPORTED_CAPABILITY",
    "ComplianceError": "UNSUPPORTED_CAPABILITY",
    "LedgerLockError": "STALE_OR_CONFLICT",
    "BusyRun": "STALE_OR_CONFLICT",
}

_KEYWORD_CODES = (
    (("权限", "无权", "未授权", "forbidden", "permission"), "PERMISSION_DENIED"),
    (("暂停", "抑制", "配额", "合规", "批次", "未知回执", "审计缺口", "paused", "suppressed", "quota"), "SAFETY_BLOCKED"),
    (("版本", "过期", "旧快照", "stale", "conflict", "冲突"), "STALE_OR_CONFLICT"),
    (("不支持", "未支持", "unsupported", "not supported"), "UNSUPPORTED_CAPABILITY"),
    (("占位", "模板残留", "空泛", "内容不可用", "placeholder"), "INVALID_INPUT"),
    (("缺少", "缺 ", "需要", "必须提供", "missing", "required"), "NEEDS_USER_INPUT"),
    (("schema", "契约", "contract", "指纹"), "CONTRACT_ERROR"),
)

_IMPACT_DEFAULT = {
    "NEEDS_USER_INPUT": "本次操作没有执行，也没有产生外部动作。",
    "UNSUPPORTED_CAPABILITY": "该能力不在产品支持范围内，本次没有执行任何外部动作。",
    "INVALID_INPUT": "输入未通过校验，本次没有写入正式记录或触发外部动作。",
    "PERMISSION_DENIED": "请求被权限门拒绝，没有产生外部动作。",
    "SAFETY_BLOCKED": "安全门阻断了本次操作，没有继续执行后续项目。",
    "STALE_OR_CONFLICT": "因版本或状态不一致，本次没有沿用旧结论继续执行。",
    "CONTRACT_ERROR": "内部契约不一致，本次操作中止；已产生的记录保持不变。",
}

_NEXT_DEFAULT = {
    "NEEDS_USER_INPUT": "按提示补齐缺失项后重试同一条命令。",
    "UNSUPPORTED_CAPABILITY": "改用在支持范围内的渠道或动作，或先查阅 docs/feature-status.md。",
    "INVALID_INPUT": "修正被点名的字段后重试；不要绕过该门禁。",
    "PERMISSION_DENIED": "换用有权限的账号或先由授权人完成审批。",
    "SAFETY_BLOCKED": "先处理被点名的阻断原因（人工核实/解除抑制/等待配额），再重试。",
    "STALE_OR_CONFLICT": "重新读取最新版本后重做；旧版本记录只作留痕，不作依据。",
    "CONTRACT_ERROR": "保留输出与操作日志，按提示路径修复契约后再重试。",
}


class SemanticError(ValueError):
    """带完整语义的错误：缺什么、影响、下一动作、示例。"""

    def __init__(self, code: str, message: str, *, missing: Optional[Iterable[str]] = None,
                 impact: str = "", next_action: str = "", example: str = "",
                 extra: Optional[Dict[str, Any]] = None) -> None:
        if code not in CODES:
            raise ValueError(f"未知错误码：{code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.missing = [str(item) for item in (missing or []) if str(item).strip()]
        self.impact = impact or _IMPACT_DEFAULT[code]
        self.next_action = next_action or _NEXT_DEFAULT[code]
        self.example = example
        self.extra = dict(extra or {})

    @property
    def payload(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "status": "error",
            "reason_code": self.code,
            "message": self.message,
            "missing": self.missing,
            "impact": self.impact,
            "next_action": self.next_action,
            "example": self.example,
            "semantics": SCHEMA,
        }
        body.update(self.extra)
        return body

    def human_lines(self) -> list:
        lines = [f"{self.code}：{self.message}"]
        if self.missing:
            lines.append("缺什么：" + "、".join(self.missing))
        lines.append("影响：" + self.impact)
        lines.append("下一步：" + self.next_action)
        if self.example:
            lines.append("示例：" + self.example)
        return lines


def classify(exc: BaseException) -> str:
    """把异常归类到七类之一。仅在无法判断时才落到 CONTRACT_ERROR。"""
    if isinstance(exc, SemanticError):
        return exc.code
    explicit = getattr(exc, "semantic_code", None)
    if isinstance(explicit, str) and explicit in CODES:
        return explicit
    mapped = _CLASS_CODES.get(type(exc).__name__)
    if mapped:
        return mapped
    text = str(exc).lower()
    for needles, code in _KEYWORD_CODES:
        if any(needle in text for needle in needles):
            return code
    if isinstance(exc, (FileNotFoundError, IsADirectoryError, NotADirectoryError)):
        return "NEEDS_USER_INPUT"
    if isinstance(exc, PermissionError):
        return "PERMISSION_DENIED"
    if isinstance(exc, ValueError):
        return "INVALID_INPUT"
    return "CONTRACT_ERROR"


def describe(exc: BaseException) -> Dict[str, Any]:
    """把任意异常渲染成统一载荷；不丢弃原始类型与消息。"""
    if isinstance(exc, SemanticError):
        return exc.payload
    payload = getattr(exc, "payload", None)
    if isinstance(payload, dict) and payload.get("reason_code"):
        body = dict(payload)
        body.setdefault("status", "error")
        body.setdefault("semantics", SCHEMA)
        return body
    code = classify(exc)
    return {
        "status": "error",
        "reason_code": code,
        "message": str(exc) or type(exc).__name__,
        "missing": [],
        "impact": _IMPACT_DEFAULT[code],
        "next_action": _NEXT_DEFAULT[code],
        "example": "",
        "semantics": SCHEMA,
        "exception": type(exc).__name__,
    }


def human_lines(payload: Dict[str, Any]) -> list:
    """把载荷渲染成给用户看的几行纯文本。"""
    lines = [f"{payload.get('reason_code', 'CONTRACT_ERROR')}：{payload.get('message', '')}".rstrip("：")]
    missing = payload.get("missing") or []
    if missing:
        lines.append("缺什么：" + "、".join(str(item) for item in missing))
    if payload.get("impact"):
        lines.append("影响：" + str(payload["impact"]))
    if payload.get("next_action"):
        lines.append("下一步：" + str(payload["next_action"]))
    if payload.get("example"):
        lines.append("示例：" + str(payload["example"]))
    return lines
