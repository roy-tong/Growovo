from __future__ import annotations


def _route(mode, channel=""):
    if mode == "cdp":
        return "browser-attach"
    if mode:
        return mode
    if channel == "x":
        return "browser-attach"
    return "unselected"


def recovery(action, error=None, route=""):
    error = error or {}
    code = str(error.get("code") or "").strip().lower()
    detail = str(error.get("detail") or error.get("message") or "")
    channel = str(action.get("channel") or "")
    route = route or _route("", channel)
    if code in {"indeterminate", "waiting_verification", "execution_interrupted"}:
        return {
            "route": route,
            "retry_write": False,
            "steps": [{
                "operation": "reconcile_receipt",
                "automatic": True,
                "reason": "先查询外部对象和回执；唯一匹配才完成，零匹配保持待核验，多匹配转人工",
            }],
            "completion": {"operation": "unique_external_receipt", "allow_retry_write": False},
        }
    identity_or_browser = (
        "verify_credentials" in detail
        or code in {
            "identity_unknown", "identity_unavailable", "identity_missing", "target_missing",
            "tab_not_found", "tab_hidden", "bridge_unavailable", "action_rejected",
        }
    )
    if (route == "browser-attach" or channel == "x") and identity_or_browser:
        urls = {"x": "https://x.com/home", "github": "https://github.com/notifications",
                "discord": "https://discord.com/channels/@me"}
        return {
            "route": "browser-attach",
            "retry_write": False,
            "steps": [
                {"operation": "ensure_managed_tab", "automatic": True,
                 "site": channel, "url": urls.get(channel, ""), "allow_new": True},
                {"operation": "observe_identity", "automatic": True,
                 "require_current_account": True},
                {"operation": "owner_attested_preflight", "automatic": True,
                 "scope": "%s:%s" % (channel, action.get("action") or "publish")},
            ],
            "resume": {"operation": "execute_authorized_action", "requires_explicit_resume": True},
            "completion": {"operation": "readback_receipt", "allow_retry_write": False},
        }
    return {
        "route": route,
        "retry_write": False,
        "steps": [{
            "operation": "run_read_only_preflight", "automatic": True,
            "reason": detail or code or "先定位能力、授权或目标缺口",
        }],
        "resume": {"operation": "select_next_ready_action", "requires_explicit_resume": True},
        "completion": {"operation": "evidence_backed_terminal_state", "allow_retry_write": False},
    }


def _solution(action, result, context):
    mode = str(result.get("execution_mode") or "")
    channel = str(action.get("channel") or "")
    route = _route(mode, channel)
    if mode == "manual_handoff":
        steps = [
            {"operation": "prepare_complete_work_package", "automatic": True},
            {"operation": "record_external_receipt", "automatic": False},
        ]
    elif mode in {"blocked", "preview", "local"}:
        steps = [{"operation": "select_next_ready_action", "automatic": True}]
    elif route == "browser-attach":
        steps = [
            {"operation": "ensure_managed_tab", "automatic": True, "allow_new": True},
            {"operation": "observe_identity", "automatic": True},
            {"operation": "execute_authorized_action", "automatic": True, "write": True},
            {"operation": "readback_receipt", "automatic": True},
        ]
    else:
        steps = [
            {"operation": "ensure_execution_route", "automatic": True, "route": route},
            {"operation": "observe_identity", "automatic": True},
            {"operation": "execute_authorized_action", "automatic": True, "write": True},
            {"operation": "readback_receipt", "automatic": True},
        ]
    solution = {
        "route": route,
        "steps": steps,
        "completion": {
            "operation": "readback_receipt",
            "required_result": "committed" if mode not in {"preview", "local"} else mode,
            "business_outcome": "observed_separately",
        },
        "fallback": result.get("handoff") or None,
    }
    if context.get("error"):
        solution["recovery"] = recovery(action, context["error"], route=route)
    return solution


def _resolve(action, context=None):
    context = context or {}
    execution = action.get("execution") or {}
    intent = execution.get("intent") or "live"
    if intent == "preview":
        return {"execution_mode": "preview", "blocked": False, "reason": "explicit_preview"}
    if action.get("kind") != "channel":
        return {"execution_mode": "local", "blocked": False, "reason": "local_action"}
    channel = action.get("channel") or ""
    availability = (context.get("channels") or {}).get(channel) or {}
    structured = context.get("capabilities") if "capabilities" in context else None
    account = str(context.get("account") or "default")
    missing = []
    for transport in execution.get("preferred_transports") or []:
        if transport == "manual_handoff":
            continue
        if structured is not None:
            row = next((item for item in structured
                        if item.get("account") == account and item.get("platform") == channel
                        and item.get("action") == action.get("action")
                        and item.get("route") == transport), None)
            if row and row.get("executable") is True:
                return {"execution_mode": transport, "blocked": False,
                        "reason": "channel_ready", "capability": row}
            missing.extend(row.get("missing") if row else ["capability_record_missing"])
        elif availability.get(transport) is True:
            return {"execution_mode": transport, "blocked": False, "reason": "channel_ready"}
    missing = list(dict.fromkeys(missing))
    if execution.get("on_channel_blocked") == "manual_handoff":
        return {
            "execution_mode": "manual_handoff",
            "blocked": False,
            "reason": "live_channel_unavailable_manual_handoff",
            "handoff": {
                "target": channel,
                "action": action.get("action") or "",
                "asset_ref": action.get("asset_ref") or "",
                "cta": action.get("cta") or "",
                "requires_human": True,
            },
            "missing": missing,
        }
    return {
        "execution_mode": "blocked",
        "blocked": True,
        "code": "LIVE_BLOCKED",
        "reason": "requested_live_execution_but_no_authorized_channel_is_ready",
        "missing": missing,
    }


def resolve(action, context=None):
    context = context or {}
    result = _resolve(action, context)
    result["solution"] = _solution(action, result, context)
    return result
