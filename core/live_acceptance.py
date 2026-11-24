from __future__ import annotations

import os


GITHUB_APP_VARS = ("MKT_GITHUB_APP_ID", "MKT_GITHUB_APP_INSTALLATION_ID", "MKT_GITHUB_APP_PRIVATE_KEY_PATH")
GMAIL_OAUTH_VARS = ("MKT_GMAIL_REFRESH_TOKEN", "MKT_GMAIL_CLIENT_ID", "MKT_GMAIL_CLIENT_SECRET")


def check_github_app(repo):
    missing = [] if os.environ.get("MKT_GITHUB_APP_INSTALLATION_TOKEN") else [
        name for name in GITHUB_APP_VARS if not os.environ.get(name)]
    result = {"component": "github_app", "repo": repo, "ok": not missing, "missing": missing}
    if missing:
        return result
    try:
        from .github_app import GitHubAppClient
        repository = GitHubAppClient.from_env().get_repository(repo)
        result["repository_access"] = repository.get("full_name") == repo
        result["ok"] = bool(result["repository_access"])
    except Exception as exc:
        result.update(ok=False, error_type=type(exc).__name__)
    return result


def check_gmail():
    missing = []
    if not (os.environ.get("MKT_GMAIL_ACCESS_TOKEN")
            or os.environ.get("MKT_GMAIL_OAUTH_TOKEN_PATH")):
        missing = [name for name in GMAIL_OAUTH_VARS if not os.environ.get(name)]
    result = {"component": "gmail", "ok": not missing, "missing": missing}
    if missing:
        return result
    try:
        from .gmail_api import GmailClient
        profile = GmailClient.from_env().get_profile()
        result["profile_read"] = bool(profile.get("emailAddress"))
        result["ok"] = bool(result["profile_read"])
    except Exception as exc:
        result.update(ok=False, error_type=type(exc).__name__)
    return result


def check_x(data_dir, action="publish"):
    from . import action_resolution
    from .browser_attach import BrowserAttach
    from .platform_compliance import require_owner_attested_browser_write

    result = {"component": "x", "ok": False, "missing": [],
              "transport": "browser-attach", "action": action}
    action_spec = {
        "kind": "channel", "channel": "x", "action": action,
        "execution": {"intent": "live", "preferred_transports": ["cdp"]},
    }
    solution = action_resolution.resolve(action_spec, {"channels": {"x": {"cdp": True}}})["solution"]
    result["solution"] = solution
    try:
        attach = BrowserAttach()
        tab = attach.ensure_managed_tab(
            data_dir, "x", "https://x.com/home",
            group_title="OVO live preflight", prefer_visible=True)
        identity = attach.observe_identity("x", tab)
        permit = require_owner_attested_browser_write(
            "x", action, data_dir, go=True,
            actual_transport="browser-attach", identity=identity)
        result.update({
            "ok": True, "handle": identity["handle"],
            "browser_context": identity["browser_context"],
            "authorization_route": permit["route"], "window_end": permit["window_end"],
        })
    except Exception as exc:
        code = str(getattr(exc, "code", "") or type(exc).__name__)
        result.update({
            "error_type": type(exc).__name__, "error_code": code,
            "recovery": action_resolution.recovery(
                action_spec, {"code": code, "detail": str(exc)}, route="browser-attach"),
        })
    return result


def preflight(repo=None, gmail=False, x=False, x_action="publish", data_dir=None):
    components = {}
    if repo:
        components["github_app"] = check_github_app(repo)
    if gmail:
        components["gmail"] = check_gmail()
    if x:
        components["x"] = check_x(data_dir or "data", action=x_action)
    return {"ok": bool(components) and all(row["ok"] for row in components.values()),
            "components": components, "writes": 0}
