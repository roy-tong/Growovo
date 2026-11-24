from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from . import action_resolution, growth_assets, growth_experiments, growth_runtime, growth_service


def _finish(data_dir, service_id, action_id, status, by, evidence, completion=None):
    growth_service.record_action(
        data_dir, service_id, action_id, status, by=by, evidence=evidence,
        completion=completion)
    return {"status": status, "action_id": action_id, "evidence": evidence}


def _channel_completion(action, record):
    """把渠道回执映射成完成类型；缺回读引用时只能算人工声明。"""
    readback = str(record.get("evidence") or "").strip()
    claim = str(record.get("claim_key") or "").strip()
    sent_at = str(record.get("finished_at") or "").strip()
    if readback and claim and sent_at:
        return {"kind": "verified", "receipt": {
            "action_id": action["id"],
            "channel": action.get("channel"),
            "sent_at": sent_at,
            "idempotency_key": claim,
            "readback": readback,
        }}
    return {"kind": "attested_manual",
            "reason": "transport committed without a readback reference"}


def _reconcile_completion(action, receipt_ref, observed_at=""):
    """对账读回的唯一外部记录：回执身份即去重依据。"""
    receipt_ref = str(receipt_ref or "").strip()
    return {"kind": "verified", "receipt": {
        "action_id": action.get("id"),
        "channel": action.get("channel"),
        "sent_at": observed_at or dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "sent_at_basis": "readback_observed_at" if not observed_at else "readback_reported_at",
        "idempotency_key": "readback:" + receipt_ref,
        "readback": receipt_ref,
    }}


def _run_channel(data_dir, service, action, by, payload):
    from . import execution_capabilities
    account = str(payload.get("account") or service.get("account") or "default")
    capabilities = execution_capabilities.view(
        data_dir, account=account, platform=action.get("channel"))
    context = {"channels": payload.get("channels") or {}, "account": account}
    if capabilities or payload.get("require_capabilities"):
        context["capabilities"] = capabilities
    resolution = action_resolution.resolve(action, context)
    mode = resolution["execution_mode"]
    if mode == "manual_handoff":
        directory = Path(data_dir) / "state" / "growth-handoffs" / service["id"]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (action["id"] + ".md")
        text = str(payload.get("text") or "")
        content = (
            f"# Manual growth handoff: {action['id']}\n\n"
            f"- Channel: {action.get('channel')}\n- Action: {action.get('action')}\n"
            f"- Asset: {action.get('asset_ref')}\n- CTA: {action.get('cta')}\n"
            f"- Target: {payload.get('target') or action.get('target') or ''}\n\n"
            f"{text or 'Open the linked asset and complete the prepared action.'}\n")
        path.write_text(content, encoding="utf-8")
        handoff = dict(resolution["handoff"], path=str(path))
        _finish(data_dir, service["id"], action["id"], "waiting_manual", by, {"handoff": handoff})
        return {"status": "manual_handoff", "action_id": action["id"], "handoff": handoff}
    if resolution.get("blocked"):
        _finish(data_dir, service["id"], action["id"], "blocked", by, resolution)
        return dict(resolution, status="blocked", action_id=action["id"])
    if mode == "preview":
        _finish(data_dir, service["id"], action["id"], "pending", by, resolution)
        return dict(resolution, status="preview", action_id=action["id"])
    text = str(payload.get("text") or "")
    if not text:
        resolution = {"code": "missing_payload", "reason": "channel text 必填"}
        _finish(data_dir, service["id"], action["id"], "blocked", by, resolution)
        return dict(resolution, status="blocked", action_id=action["id"])
    target = payload.get("target") or action.get("target") or ""
    github_token = None
    if mode == "github_app":
        try:
            from .github_app import GitHubAppClient
            github_token = GitHubAppClient.from_env().installation_token()
        except Exception as exc:
            evidence = {"code": "github_app_auth_unavailable", "error_type": type(exc).__name__}
            _finish(data_dir, service["id"], action["id"], "blocked", by, evidence)
            return dict(evidence, status="blocked", action_id=action["id"])
    from . import action_gateway
    # growth 的 mode="cdp" 是历史命名，实际传输是 browser-attach（WebBridge 受管标签），
    # 不是调试浏览器 9222；X 的真实路由由 action_gateway 统一决定。
    record = action_gateway.execute(
        channel=action["channel"], action=action["action"], data_dir=data_dir,
        campaign=service["id"], go=True, target=target, text=text, account=account,
        transport=("gmail-api" if mode == "gmail_api"
                   else "browser-attach" if mode == "cdp" else ""),
        title=payload.get("title"), url=payload.get("url"), github_token=github_token,
        thread_id=payload.get("thread_id"), gmail_token=None,
        subject=payload.get("subject") or "", in_reply_to=payload.get("in_reply_to") or "")
    result = str(record.get("result") or "")
    if result != "committed":
        route = ("browser-attach" if mode == "cdp"
                 else "gmail-api" if mode == "gmail_api" else mode)
        record.setdefault("recovery", action_resolution.recovery(
            action, {"code": result or "execution_failed",
                     "detail": str(record.get("detail") or record.get("error") or "")},
            route=route))
    completion = None
    if result == "committed":
        status = "completed"
        public_status = "completed"
        completion = _channel_completion(action, record)
    elif result == "indeterminate":
        status = "waiting_verification"
        public_status = "waiting_verification"
    else:
        status = "blocked"
        public_status = result or status
    _finish(data_dir, service["id"], action["id"], status, by, record, completion=completion)
    return {"status": public_status, "action_id": action["id"], "record": record}


def _execute(data_dir, service_id, action_id, by, payload=None):
    if not str(by or "").strip():
        raise ValueError("by 必填")
    payload = payload or {}
    service = growth_service.load(data_dir, service_id)
    action = next((row for row in service["actions"] if row["id"] == action_id), None)
    if action is None:
        raise ValueError(f"服务 {service_id} 没有动作 {action_id}")
    if action["status"] == "completed":
        return {"status": "completed", "action_id": action_id, "already_completed": True}
    kind = action["kind"]
    if kind == "diagnose":
        intake = service["goal"].get("intake") or {}
        diagnosis = {
            "product": service["service"]["product"],
            "asset_count": len(service["goal"].get("assets") or []),
            "channel_count": len(service["goal"]["constraints"].get("channels") or []),
        }
        if intake:
            diagnosis.update({
                "evidence": intake.get("evidence"),
                "readiness": intake.get("readiness"),
                "recommended_first_action": intake.get("recommended_first_action"),
                "metrics_status": intake.get("metrics_status"),
                "metrics": intake.get("metrics"),
            })
        evidence = {"diagnosis": diagnosis}
        return _finish(data_dir, service_id, action_id, "completed", by, evidence)
    if kind == "observe":
        phase = "baseline" if action_id == "baseline-observe" else "final"
        if payload.get("live"):
            from . import growth_metrics
            live = payload["live"]
            service = growth_metrics.observe_live(
                data_dir, service_id, phase, live.get("repos") or [],
                live.get("handle") or "", by=by)
            return {"status": "completed", "action_id": action_id, "phase": phase,
                    "measurement": service["measurement"]}
        metrics = payload.get("metrics")
        if not isinstance(metrics, dict):
            raise ValueError("observe 需要 metrics")
        growth_service.observe(data_dir, service_id, phase, metrics, by=by)
        return {"status": "completed", "action_id": action_id, "phase": phase}
    if kind == "asset":
        spec = payload.get("asset")
        if not isinstance(spec, dict):
            raise ValueError("asset-prepare 需要 asset")
        asset = growth_assets.create(data_dir, service_id, spec)
        status = "completed" if asset["reproduced"] else "blocked"
        _finish(data_dir, service_id, action_id, status, by, {"asset_id": asset["id"],
                                                              "path": asset["path"]})
        return {"status": status, "action_id": action_id, "asset": asset}
    if kind == "evaluate":
        metrics = payload.get("metrics")
        if not isinstance(metrics, dict):
            raise ValueError("evaluate 需要 metrics")
        evaluation = growth_service.evaluate(data_dir, service_id, metrics, by=by)
        return {"status": "completed", "action_id": action_id, "evaluation": evaluation}
    if kind == "experiment":
        primary = next(row for row in service["goal"]["outcomes"] if row["role"] == "primary")
        experiment_id = "exp-" + hashlib.sha256(
            f"{service_id}:{len(service['evaluations'])}".encode("utf-8")).hexdigest()[:10]
        spec = {
            "id": experiment_id,
            "hypothesis": primary.get("hypothesis") or service["goal"]["product"]["name"] + " can earn first successful use",
            "primary_metric": primary["metric"],
            "target": primary.get("target", primary.get("target_delta")),
            "window": service["goal"]["window"],
            "stop_rule": "Stop on platform warning, missing evidence or owner decision",
        }
        try:
            experiment = growth_experiments.create(data_dir, service_id, spec)
        except ValueError:
            from .storage import AtomicJSONStore
            experiment = AtomicJSONStore(
                Path(data_dir) / "state" / "growth-experiments" / service_id / (experiment_id + ".json"),
                schema="1").load()
        diagnosis = growth_experiments.diagnose(data_dir, service_id, experiment_id)
        _finish(data_dir, service_id, action_id, "completed", by,
                {"experiment_id": experiment_id, "diagnosis": diagnosis})
        return {"status": "completed", "action_id": action_id,
                "experiment": experiment, "diagnosis": diagnosis}
    if kind == "channel":
        return _run_channel(data_dir, service, action, by, payload)
    raise ValueError(f"不支持的动作类型 {kind}")


def run(data_dir, service_id, action_id, by, payload=None):
    if not str(by or "").strip():
        raise ValueError("by 必填")
    payload = payload or {}
    growth_runtime.recover_expired(data_dir)
    claim = growth_runtime.claim_action(
        data_dir, service_id, action_id, worker=by, payload=payload)
    if claim["status"] != "claimed":
        return dict(claim)
    try:
        result = _execute(data_dir, service_id, action_id, by, payload)
    except Exception as exc:
        from .action_runner import ActionError
        from .browser_write import ActionBlocked
        service = growth_service.load(data_dir, service_id)
        action = next(row for row in service["actions"] if row["id"] == action_id)
        if isinstance(exc, ActionBlocked):
            recovery = action_resolution.recovery(
                action, {"code": exc.code, "detail": str(exc)}, route="browser-attach")
            evidence = {"code": exc.code, "detail": str(exc)[:300],
                        "attempt_id": claim["attempt_id"], "recovery": recovery}
            _finish(data_dir, service_id, action_id, "blocked", by, evidence)
            return {"status": "blocked", "action_id": action_id,
                    "attempt_id": claim["attempt_id"], "record": evidence}
        if isinstance(exc, ActionError):
            recovery = action_resolution.recovery(
                action, {"code": "action_rejected", "detail": str(exc)})
            evidence = {"code": "action_rejected", "detail": str(exc)[:300],
                        "attempt_id": claim["attempt_id"], "recovery": recovery}
            _finish(data_dir, service_id, action_id, "blocked", by, evidence)
            return {"status": "blocked", "action_id": action_id,
                    "attempt_id": claim["attempt_id"], "record": evidence}
        if action.get("kind") == "channel":
            recovery = action_resolution.recovery(
                action, {"code": "execution_interrupted", "detail": str(exc)})
            evidence = {"code": "execution_interrupted", "error_type": type(exc).__name__,
                        "attempt_id": claim["attempt_id"], "recovery": recovery}
            _finish(data_dir, service_id, action_id, "waiting_verification", by, evidence)
            return {"status": "waiting_verification", "action_id": action_id,
                    "attempt_id": claim["attempt_id"], "record": evidence}
        _finish(data_dir, service_id, action_id, "pending", by,
                {"code": "local_execution_interrupted", "attempt_id": claim["attempt_id"]})
        raise
    if isinstance(result, dict):
        result.setdefault("attempt_id", claim["attempt_id"])
    return result


def reconcile(data_dir, service_id, action_id, by, payload=None):
    if not str(by or "").strip():
        raise ValueError("by 必填")
    payload = payload or {}
    service = growth_service.load(data_dir, service_id)
    action = next((row for row in service["actions"] if row["id"] == action_id), None)
    if action is None:
        raise ValueError(f"服务 {service_id} 没有动作 {action_id}")
    if action.get("status") != "waiting_verification":
        return {"status": action.get("status"), "action_id": action_id}
    if action.get("channel") == "email":
        thread_id = str(payload.get("thread_id") or "")
        recipient = str(payload.get("target") or "")
        text = str(payload.get("text") or "")
        if not all((thread_id, recipient, text)):
            raise ValueError("Gmail 对账需要 thread_id、target 和原始 text")
        from channels import gmail
        matches = gmail.find_sent_replies(thread_id, recipient, text, token=None)
        if len(matches) == 1 and matches[0].get("id"):
            evidence = {"receipt": f"gmail://threads/{thread_id}/messages/{matches[0]['id']}",
                        "message_id": matches[0]["id"], "thread_id": thread_id,
                        "verification": "independent_sent_readback"}
            completion = _reconcile_completion(
                action, evidence["receipt"], observed_at=str(matches[0].get("date") or ""))
            _finish(data_dir, service_id, action_id, "completed", by, evidence,
                    completion=completion)
            return {"status": "completed", "action_id": action_id, "evidence": evidence}
        if len(matches) > 1:
            evidence = {"code": "multiple_matching_messages", "matches": len(matches)}
            _finish(data_dir, service_id, action_id, "waiting_manual", by, evidence)
            return {"status": "waiting_manual", "action_id": action_id, "evidence": evidence}
        evidence = {"code": "matching_message_not_found", "thread_id": thread_id}
        return {"status": "waiting_verification", "action_id": action_id, "evidence": evidence}
    if action.get("channel") != "github" or action.get("action") != "reply":
        evidence = {"code": "automatic_reconciliation_unsupported",
                    "channel": action.get("channel")}
        _finish(data_dir, service_id, action_id, "waiting_manual", by, evidence)
        return {"status": "waiting_manual", "action_id": action_id, "evidence": evidence}
    from .action_runner import _parse_discussion, _parse_issue
    target = payload.get("target") or action.get("target")
    discussion = str(target or "").startswith("discussion:") or "/discussions/" in str(target or "")
    slug, number = _parse_discussion(target) if discussion else _parse_issue(target)
    text = str(payload.get("text") or "")
    if not text:
        raise ValueError("GitHub 对账需要原始 text")
    token = None
    if (payload.get("channels") or {}).get("github", {}).get("github_app") is True:
        from .github_app import GitHubAppClient
        token = GitHubAppClient.from_env().installation_token()
    from channels import github
    matches = (github.find_discussion_replies(slug, number, text, token=token) if discussion
               else github.find_replies(slug, number, text, token=token))
    receipt_key = "url" if discussion else "html_url"
    if len(matches) == 1 and matches[0].get(receipt_key):
        evidence = {"receipt": matches[0][receipt_key], "comment_id": matches[0].get("id"),
                    "verification": "independent_comment_readback"}
        completion = _reconcile_completion(
            action, evidence["receipt"],
            observed_at=str(matches[0].get("created_at") or ""))
        _finish(data_dir, service_id, action_id, "completed", by, evidence,
                completion=completion)
        return {"status": "completed", "action_id": action_id, "evidence": evidence}
    if len(matches) > 1:
        evidence = {"code": "multiple_matching_comments", "matches": len(matches)}
        _finish(data_dir, service_id, action_id, "waiting_manual", by, evidence)
        return {"status": "waiting_manual", "action_id": action_id, "evidence": evidence}
    evidence = {"code": "matching_comment_not_found",
                "target": f"{'discussion' if discussion else 'issue'}:{slug}#{number}"}
    return {"status": "waiting_verification", "action_id": action_id, "evidence": evidence}
