#!/usr/bin/env python3
"""平台执行许可 —— 单次用户可见浏览器操作与无人值守写分开授权。

X 官方自动化规则（docs/risk/automation-compliance.md §二）对非 API 自动化、
未经请求自动回复与批量动作有明确限制。本模块不把 CDP 能点页面说成平台许可：
routine/autonomous 的非 API 写始终拒绝，official_api 授权也不能挪给 CDP 使用。

唯一的浏览器写例外是操作者当次明确发起的非 routine ``--go`` publish/reply：
必须先从实际执行浏览器观测当前账号（cdp_observed），再继续通过既有人审／adhoc、
暂停、DNC、配额、幂等、防重发与回执门禁。它不形成常驻扩权，不覆盖 message/engage，
也不构成平台认可或账号安全保证。dry-run 与只读动作不受影响。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

AUTHORIZATION_NAME = "platform-authorization.json"
AUTHORIZATION_SCHEMA = "growovo.platform-authorization/1"

#: 需要「自主执行许可」的写动作（读动作不在内）。
WRITE_ACTIONS = frozenset({"publish", "reply", "message", "engage"})

#: 只有官方 API 通道可以自主执行；浏览器自动化不是可授权路线。
ALLOWED_ROUTE = "official_api"


class ComplianceError(RuntimeError):
    """平台规则拒绝这次自主写；不是技术故障，重试也不会变。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def authorization_path(data_dir) -> Path:
    return Path(data_dir) / AUTHORIZATION_NAME


def _text(value) -> str:
    return str(value or "").strip()


def load_authorization(data_dir, platform: str) -> Optional[Dict[str, Any]]:
    """读授权文件；不存在返回 None，损坏返回 None 并保留原因在调用方提示里。

    这里不抛解析异常：授权文件坏了等价于没有授权（fail closed），
    但不能把 JSON 解析错误当成「技术崩溃」掩盖真正的问题。
    """
    path = authorization_path(data_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if _text(payload.get("platform")).lower() != str(platform).lower():
        return None
    return payload


def _parse_time(value) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_authorization(payload: Dict[str, Any], *,
                           platform: str, action: str,
                           actual_transport: str) -> Dict[str, Any]:
    """校验一份授权是否覆盖这次动作；不通过抛 ComplianceError。"""
    if _text(payload.get("schema")) != AUTHORIZATION_SCHEMA:
        raise ComplianceError(
            "authorization_invalid",
            "平台授权文件 schema 不对（应为 %s）" % AUTHORIZATION_SCHEMA)
    if not _text(payload.get("approved_by")):
        raise ComplianceError(
            "authorization_invalid", "平台授权必须由具名负责人签署（approved_by）")
    if _parse_time(payload.get("approved_at")) is None:
        raise ComplianceError(
            "authorization_invalid",
            "平台授权缺少可解析的 approved_at（ISO 8601）")
    route = _text(payload.get("route"))
    if route != ALLOWED_ROUTE:
        raise ComplianceError(
            "non_official_route",
            "只有 official_api 路线可以自主执行；当前授权路线是 %r。"
            "浏览器自动化（CDP）不在可授权范围内。" % (route or "空"))
    transport = _text(payload.get("transport"))
    if transport != str(actual_transport):
        raise ComplianceError(
            "transport_mismatch",
            "授权声明的传输是 %r，实际执行用的是 %r：不能用「批了 API」的授权"
            "去跑另一条通道。" % (transport or "空", actual_transport))
    if str(actual_transport) != "api":
        raise ComplianceError(
            "non_api_transport_forbidden",
            "实际执行传输是 %r：X 官方只允许经 API 的自动化写，非 API 写路径"
            "不能自主执行。" % actual_transport)
    scopes = {str(item).strip().lower()
              for item in (payload.get("scopes") or [])}
    if action not in scopes:
        raise ComplianceError(
            "action_out_of_scope",
            "动作 %r 不在授权范围 %s 内。" % (action, sorted(scopes)))
    if action == "message":
        for field, label in (("recipient_consent_policy", "收件人同意依据"),
                             ("opt_out_path", "退出机制")):
            if not _text(payload.get(field)):
                raise ComplianceError(
                    "consent_missing",
                    "DM 自主发送必须写明%s（%s）；平台规则和收件人意愿都不是"
                    "传输方式能替代的。" % (label, field))
    if action == "reply":
        if not _text(payload.get("automated_reply_approval_ref")):
            raise ComplianceError(
                "approval_missing",
                "自动回复必须写明 X 的书面批准引用"
                "（automated_reply_approval_ref）；没有批准就不能自主回复。")
    return payload


def require_autonomous_write(platform: str, action: str, data_dir, *,
                             go: bool, actual_transport: str) -> Optional[Dict[str, Any]]:
    """自主写许可门禁。返回授权（放行）或 None（非写动作/干跑）；否则抛错。"""
    platform = _text(platform).lower()
    action = _text(action).lower()
    if not go or action not in WRITE_ACTIONS:
        return None
    if platform != "x":
        return None
    payload = load_authorization(data_dir, platform)
    if payload is None:
        raise ComplianceError(
            "non_api_write_forbidden",
            "X 非 API 自动写被官方规则禁止（脚本操作网站做自动化属违规）；"
            "本机 CDP 通道只保留只读与人工辅助。要自主发送，请接入 X 官方 API "
            "并写入 %s（route=official_api，transport=api，含具名审批）；"
            "否则请人工发送。"
            % authorization_path(data_dir))
    return validate_authorization(
        payload, platform=platform, action=action,
        actual_transport=actual_transport)


USER_VISIBLE_CDP_ACTIONS = frozenset({"publish", "reply"})


def require_user_visible_cdp(platform: str, action: str, data_dir, *,
                             go: bool, actual_transport: str,
                             identity: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Permit one explicitly requested browser action with observed identity.

    This is not standing/autonomous authorization. The caller reaches this mode
    only from a non-routine ``--go`` invocation; plan/adhoc approval, pause,
    suppression, quota, idempotency and receipt gates still run afterwards.
    DM/engage stay outside this route because recipient consent and bulk-action
    limits cannot be inferred from a logged-in browser.
    """
    platform = _text(platform).lower()
    action = _text(action).lower()
    if not go or action not in WRITE_ACTIONS or platform != "x":
        return None
    if actual_transport != "cdp":
        raise ComplianceError(
            "transport_mismatch",
            "用户当次确认的浏览器路线只接受 cdp，实际传输是 %r。" % actual_transport)
    if action not in USER_VISIBLE_CDP_ACTIONS:
        raise ComplianceError(
            "action_out_of_scope",
            "用户当次确认的 CDP 路线只支持 publish/reply；%s 仍需人工操作或官方 API 授权。"
            % action)
    if not isinstance(identity, dict) or identity.get("enforced") is not True:
        raise ComplianceError(
            "identity_observation_required",
            "CDP 实跑前必须观测当前浏览器账号；请用 observe_identity 的常驻策略，"
            "但不要求预先绑定某个账号。")
    if identity.get("mode") != "cdp_observed":
        raise ComplianceError(
            "identity_mode_mismatch",
            "用户当次确认的 CDP 路线要求 identity_mode=cdp_observed，不能用自报账号替代。")
    for field in ("observed_platform_identity", "handle", "identity_proof_ref",
                  "browser_context", "observed_at"):
        if not _text(identity.get(field)):
            raise ComplianceError(
                "identity_observation_incomplete",
                "当前账号观测缺少 %s；拒绝在账号不明时执行浏览器写操作。" % field)
    return {
        "route": "user_visible_cdp", "transport": "cdp",
        "authorization": "single_non_routine_go_plus_existing_approval",
        "observed_platform_identity": identity["observed_platform_identity"],
        "handle": identity["handle"], "identity_proof_ref": identity["identity_proof_ref"],
        "external_action_not_yet_executed": True,
    }


def owner_attested_path(data_dir, platform: str) -> Path:
    return Path(data_dir) / "policies" / ("owner-attested-%s.json" % _text(platform).lower())


OWNER_ATTESTED_ACTIONS = {
    "x": frozenset({"publish", "reply"}),
    "github": frozenset({"reply"}),
    "discord": frozenset({"publish"}),
}


def load_owner_attested(data_dir, platform: str) -> Dict[str, Any]:
    """读操作者自担风险的 browser-attach 常驻授权；缺失或损坏即无授权。"""
    path = owner_attested_path(data_dir, platform)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def require_owner_attested_browser_write(platform: str, action: str, data_dir, *,
                                        go: bool, actual_transport: str,
                                        identity: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """操作者自担风险的 browser-attach 写：独立授权路线，不放宽任何既有拒绝路径。

    约束：必须走 browser-attach 传输；动作在该平台白名单内；授权文件写明
    route=owner_attested_session、批准人、批准时间、有效窗口、停止条件与
    ``platform:action`` scope；身份必须是页面内观测到的当前账号。返回里
    明确 ``platform_permission: False``——这不是平台许可，也不保证账号安全。
    """
    platform = _text(platform).lower()
    action = _text(action).lower()
    if not go or action not in WRITE_ACTIONS:
        raise ComplianceError("action_out_of_scope", "只有真实写动作才需要 owner-attested 授权。")
    if actual_transport != "browser-attach":
        raise ComplianceError(
            "transport_mismatch",
            "owner-attested 路线只接受 browser-attach，实际传输是 %r。" % actual_transport)
    allowed = OWNER_ATTESTED_ACTIONS.get(platform)
    if not allowed:
        raise ComplianceError("platform_out_of_scope", "%s 不在 owner-attested 平台白名单内。" % platform)
    if action not in allowed:
        raise ComplianceError(
            "action_out_of_scope",
            "%s 的 owner-attested 只支持 %s；%s 仍需官方 API 或人工操作。"
            % (platform, "/".join(sorted(allowed)), action))
    auth = load_owner_attested(data_dir, platform)
    if not auth:
        raise ComplianceError("authorization_missing",
                              "缺少 %s 的 owner-attested 授权文件（%s）。"
                              % (platform, owner_attested_path(data_dir, platform)))
    if _text(auth.get("route")) != "owner_attested_session":
        raise ComplianceError("authorization_route_invalid",
                              "授权文件 route 必须是 owner_attested_session。")
    if auth.get("revoked") is True:
        raise ComplianceError("authorization_revoked", "该平台 owner-attested 授权已被撤销。")
    if not _text(auth.get("approved_by")):
        raise ComplianceError("authorization_incomplete", "授权缺少 approved_by。")
    approved_at = _parse_time(auth.get("approved_at"))
    if not approved_at:
        raise ComplianceError("authorization_incomplete", "授权 approved_at 不可解析。")
    start = _parse_time(auth.get("window_start"))
    end = _parse_time(auth.get("window_end"))
    if not start or not end:
        raise ComplianceError("authorization_incomplete",
                              "授权必须写明可解析的 window_start / window_end；不设永久窗口。")
    now = datetime.now(start.tzinfo) if start.tzinfo else datetime.now()
    if not (start <= now <= end):
        raise ComplianceError(
            "authorization_window_closed",
            "当前时间不在授权窗口内（%s — %s）。" % (start.isoformat(), end.isoformat()))
    stops = auth.get("stop_conditions")
    if not isinstance(stops, list) or not [s for s in stops if _text(s)]:
        raise ComplianceError("authorization_incomplete", "授权必须列出 stop_conditions。")
    scopes = auth.get("scopes")
    if not isinstance(scopes, list) or ("%s:%s" % (platform, action)) not in {
            _text(s) for s in scopes}:
        raise ComplianceError("authorization_scope_missing",
                              "授权 scopes 未包含 %s:%s。" % (platform, action))
    if not isinstance(identity, dict) or identity.get("enforced") is not True:
        raise ComplianceError("identity_observation_required",
                              "browser-attach 写前必须观测当前浏览器账号。")
    if identity.get("mode") != "cdp_observed":
        raise ComplianceError("identity_mode_mismatch",
                              "要求 identity_mode=cdp_observed，不能用自报账号替代。")
    for field in ("observed_platform_identity", "handle", "identity_proof_ref",
                  "browser_context", "observed_at"):
        if not _text(identity.get(field)):
            raise ComplianceError("identity_observation_incomplete",
                                  "当前账号观测缺少 %s；拒绝在账号不明时执行。" % field)
    return {
        "route": "owner_attested_session",
        "transport": "browser-attach",
        "platform_permission": False,
        "risk_owner": _text(auth.get("approved_by")),
        "approved_at": approved_at.isoformat(),
        "window_end": end.isoformat(),
        "stop_conditions": [_text(s) for s in stops if _text(s)],
        "identity_proof_ref": identity["identity_proof_ref"],
    }


def policy_row(platform: str) -> Dict[str, Any]:
    """给平台能力矩阵用的许可说明（不是执行门禁，只是让界面说真话）。"""
    platform = _text(platform).lower()
    if platform == "x":
        return {
            "autonomous_write": False,
            "user_visible_cdp_write": True,
            "user_visible_cdp_actions": sorted(USER_VISIBLE_CDP_ACTIONS),
            "owner_attested_browser_write": True,
            "code": "non_api_write_forbidden",
            "reason": ("无人值守非 API 自动写不放行；单次非 routine --go 的 publish/reply "
                       "可在观测当前账号并通过既有审批、配额、防重发与回执门禁后走 CDP；"
                       "操作者自担风险的 browser-attach 常驻授权见 owner-attested 文件"),
            "source": "docs/risk/automation-compliance.md#§二",
        }
    return {"autonomous_write": True, "code": None, "reason": None, "source": None}


def autonomous_channels(channels: Iterable[str]) -> list:
    return [str(ch) for ch in channels
            if policy_row(str(ch)).get("autonomous_write")]
