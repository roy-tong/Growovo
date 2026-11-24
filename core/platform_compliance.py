#!/usr/bin/env python3
"""平台风险说明与账号授权边界。

``policy_row`` 描述业务执行能力和非阻断风险提示，不用平台规则否决业务动作。
``require_autonomous_write`` 与 ``require_user_visible_cdp`` 保留兼容入口并返回风险信息。
账号授权、页面身份、配额、暂停、DNC、回读、验证码/风控停手和未知结果保护是独立的
执行安全边界，仍由 ``require_owner_attested_browser_write`` 与执行器强制校验。
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

#: 许可表按内核渠道名查；平台 id 先归一（hackernews → hn）。
_POLICY_ALIASES = {"hackernews": "hn"}


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
    """显式校验旧 official_api 配置格式；不参与业务执行准入。"""
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


def _risk_notice(platform: str, action: str, actual_transport: str) -> Dict[str, Any]:
    row = policy_row(platform)
    return {
        "route": "business_execution",
        "action": action,
        "transport": actual_transport,
        "risk_code": row.get("code"),
        "risk_warning": row.get("risk_warning") or "",
        "platform_permission": "operator_risk",
    }


def require_autonomous_write(platform: str, action: str, data_dir, *,
                             go: bool, actual_transport: str) -> Optional[Dict[str, Any]]:
    """保留兼容入口；平台规则只返回风险提示，不否决业务执行。"""
    platform = _text(platform).lower()
    action = _text(action).lower()
    if not go or action not in WRITE_ACTIONS or platform != "x":
        return None
    return _risk_notice(platform, action, actual_transport)


def require_user_visible_cdp(platform: str, action: str, data_dir, *,
                             go: bool, actual_transport: str,
                             identity: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """保留兼容入口；单次 CDP 与常驻 CDP 使用同一非阻断风险口径。"""
    platform = _text(platform).lower()
    action = _text(action).lower()
    if not go or action not in WRITE_ACTIONS or platform != "x":
        return None
    return _risk_notice(platform, action, actual_transport)


def owner_attested_path(data_dir, platform: str) -> Path:
    return Path(data_dir) / "policies" / ("owner-attested-%s.json" % _text(platform).lower())


OWNER_ATTESTED_ACTIONS = {
    "x": frozenset({"publish", "reply", "message", "engage"}),
    "github": frozenset({"reply"}),
    "discord": frozenset({"publish", "reply"}),
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


def owner_attested_active(data_dir, platform: str, *, now: Optional[datetime] = None) -> bool:
    """排程期粗检：签名授权存在、路线正确、未撤销且窗口覆盖当前时刻。

    只用于决定队列行要不要进入计划；真正执行前仍会走
    ``require_owner_attested_browser_write`` 的完整校验（scope、身份观测等）。
    """
    auth = load_owner_attested(data_dir, platform)
    if not auth or auth.get("revoked") is True:
        return False
    if _text(auth.get("route")) != "owner_attested_session":
        return False
    start = _parse_time(auth.get("window_start"))
    end = _parse_time(auth.get("window_end"))
    if not start or not end:
        return False
    moment = now or datetime.now(start.tzinfo)
    if moment.tzinfo is None and start.tzinfo is not None:
        moment = moment.replace(tzinfo=start.tzinfo)
    return start <= moment <= end


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
    if actual_transport not in {"browser-attach", "cdp"}:
        raise ComplianceError(
            "transport_mismatch",
            "owner-attested 路线只接受 browser-attach 或受管 cdp，实际传输是 %r。" % actual_transport)
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
        "transport": actual_transport,
        "platform_permission": False,
        "risk_owner": _text(auth.get("approved_by")),
        "approved_at": approved_at.isoformat(),
        "window_end": end.isoformat(),
        "stop_conditions": [_text(s) for s in stops if _text(s)],
        "identity_proof_ref": identity["identity_proof_ref"],
    }


def policy_row(platform: str) -> Dict[str, Any]:
    """返回业务执行说明与风险提示；风险字段不参与执行准入。"""
    platform = _text(platform).lower()
    platform = _POLICY_ALIASES.get(platform, platform)
    rows = {
        "x": {
            "autonomous_write": True,
            "user_visible_cdp_write": True,
            "user_visible_cdp_actions": sorted(WRITE_ACTIONS),
            "owner_attested_browser_write": True,
            "code": "non_api_write_forbidden",
            "reason": "X 非 API 浏览器自动化存在平台规则与账号风险；业务执行不因此停止",
            "next": "按业务目标选择官方 API 或授权 CDP，保留身份、配额、回执和停手证据",
            "source": "docs/risk/automation-compliance.md#§二",
        },
        "email": {
            "autonomous_write": True,
            "code": None,
            "reason": "邮件走 SMTP/Gmail API，可按业务授权自主执行",
            "next": "保持退订/抑制名单、日配额与回执校验；退信率升高时降频并清洗名单。",
            "source": "docs/risk/automation-compliance.md",
        },
        "github": {
            "autonomous_write": True,
            "owner_attested_browser_write": True,
            "code": None,
            "reason": "GitHub API 与授权浏览器路径均可执行，按技术验证和账号授权选择",
            "next": "优先使用官方 API；限流时错峰，保留独立回读。",
            "source": "docs/platform-automation-matrix.md",
        },
        "reddit": {
            "autonomous_write": True,
            "code": "non_api_write_forbidden",
            "reason": "Reddit 非 OAuth 浏览器自动化存在平台规则与账号风险；业务执行不因此停止",
            "next": "可选择 OAuth API 或授权 CDP，保留配额、去重和回读证据",
            "source": "docs/platform-automation-matrix.md",
        },
        "hn": {
            "autonomous_write": True,
            "code": "platform_forbids_automation",
            "reason": "Hacker News 自动化发布存在社区规则风险；业务执行不因此停止",
            "next": "保持低频、真实内容和完整回执；技术首跑仍需显式确认",
            "source": "docs/platform-automation-matrix.md",
        },
        "linkedin": {
            "autonomous_write": True,
            "code": "platform_forbids_automation",
            "reason": "LinkedIn 第三方自动化存在平台规则与账号风险；业务执行不因此停止",
            "next": "按业务授权执行，保留配额、身份和回读；技术首跑仍需显式确认",
            "source": "docs/platform-automation-matrix.md",
        },
        "discord": {
            "autonomous_write": True,
            "owner_attested_browser_write": True,
            "code": "self_bot_forbidden",
            "reason": "Discord 浏览器自动化存在平台规则与账号风险；业务执行不因此停止",
            "next": "可选择官方 Bot 或授权浏览器路径，保留频道范围、配额和回执",
            "source": "docs/platform-automation-matrix.md",
        },
    }
    row = dict(rows.get(platform) or {
        "autonomous_write": False,
        "code": "platform_not_declared",
        "reason": "该平台没有登记的写能力，不能计入自动执行",
        "next": "先接入写原语并完成技术验证。",
        "source": None,
    })
    row["risk_warning"] = ("平台风险提示（不阻断业务执行）：" + row["reason"]
                           if row.get("code") else "")
    return row


def autonomous_channels(channels: Iterable[str]) -> list:
    return [str(ch) for ch in channels
            if policy_row(str(ch)).get("autonomous_write")]
