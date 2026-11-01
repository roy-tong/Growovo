#!/usr/bin/env python3
"""Workflow: a versioned, human-approved recipe for one repeated operation.

A workflow records what a task needs — navigate, locate, fill, submit, verify —
as data instead of leaving it in an Agent's head. Replay is deterministic:

- locators resolve through the ladder in ``core.locators`` (CSS, then semantic),
  so an ordinary page change does not require a new model exploration;
- the send still goes through the injected executor (``action_runner`` in the
  CLI), so approval, pause, quota, suppression and the ledger are unchanged;
- every step is journalled before and after it runs; an unknown external result
  stops the run as ``indeterminate`` and requires explicit reconciliation;
- replay requires a human approval fingerprint over the workflow content;
  editing the workflow invalidates it.

This module never performs a platform write by itself. Visual recovery is an
operator-supplied callable; with no provider the ladder stops at
``visual_unavailable`` instead of guessing a click target.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import locators
from .ledger import Ledger
from .safety import is_paused
from .throttle import Throttle, ThrottleError

SCHEMA_VERSION = "1.0"
KIND = "growovo.workflow"
RUN_KIND = "growovo.workflow-run"
STEP_KINDS = {"open", "fill", "click", "press", "read", "action"}
RESULT_STOP = {"indeterminate", "failed", "rejected", "no-dm", "bounced", "suppressed"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


class WorkflowError(RuntimeError):
    pass


class WorkflowNotApproved(WorkflowError):
    pass


class StepUnknown(WorkflowError):
    """The external result cannot be known from here; never retry blindly."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def workflows_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "workflows"


def runs_dir(data_dir: Path) -> Path:
    return workflows_dir(data_dir) / "runs"


def approvals_path(data_dir: Path) -> Path:
    return Path(data_dir) / "workflow-approvals.jsonl"


def fingerprint(workflow: Dict[str, Any]) -> str:
    """Content fingerprint; the review block cannot change its own fingerprint."""
    payload = {key: value for key, value in (workflow or {}).items() if key != "review"}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _default_review() -> Dict[str, Any]:
    return {
        "approved": False,
        "approved_by": "",
        "approved_at": "",
        "expires_at": "",
        "workflow_sha256": "",
        "note": "",
    }


def normalize(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Fill defaults and normalize locators; does not validate semantics."""
    data = json.loads(json.dumps(workflow or {}))
    data.setdefault("schema_version", SCHEMA_VERSION)
    data["kind"] = KIND
    data.setdefault("version", 1)
    data.setdefault("source", {"kind": "manual", "ref": ""})
    data.setdefault("preconditions", [])
    data.setdefault("exceptions", [])
    data.setdefault("verification", {})
    data.setdefault("recovery", {"ladder": list(locators.STRATEGIES), "visual": {"provider": "", "enabled": False}})
    review = data.get("review")
    data["review"] = {**_default_review(), **(review if isinstance(review, dict) else {})}
    steps = data.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict) and isinstance(step.get("locators"), list):
                step["locators"] = [locators.normalize(spec) for spec in step["locators"]]
    return data


def lint(workflow: Dict[str, Any]) -> List[str]:
    """Return every structural error; an empty list is the only replayable state."""
    errors: List[str] = []
    if not isinstance(workflow, dict):
        return ["workflow must be an object"]
    workflow_id = str(workflow.get("id") or "")
    if not NAME_RE.match(workflow_id):
        errors.append("id must be 2-64 chars of lowercase letters, digits, dot, dash or underscore")
    if not str(workflow.get("name") or "").strip():
        errors.append("name is required")
    if not str(workflow.get("channel") or "").strip():
        errors.append("channel is required")
    try:
        version = int(workflow.get("version"))
        if version < 1:
            errors.append("version must be a positive integer")
    except (TypeError, ValueError):
        errors.append("version must be a positive integer")
    steps = workflow.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("steps must be a non-empty array")
        steps = []
    seen: set = set()
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            errors.append(f"steps[{index}] must be an object")
            continue
        step_id = str(step.get("id") or "")
        if not step_id:
            errors.append(f"steps[{index}] needs an id")
        elif step_id in seen:
            errors.append(f"steps[{index}] duplicates id {step_id}")
        seen.add(step_id)
        kind = str(step.get("kind") or "")
        if kind not in STEP_KINDS:
            errors.append(f"{step_id or index} has unknown kind {kind!r}")
            continue
        if kind == "open" and not str(step.get("url") or "").strip():
            errors.append(f"{step_id} open needs a url")
        if kind in ("fill", "click", "press", "read") and not isinstance(step.get("locators"), list):
            errors.append(f"{step_id} {kind} needs a locators array")
        if kind == "fill" and step.get("value") is None:
            errors.append(f"{step_id} fill needs a value (may be an empty string)")
        if kind == "action" and not str(step.get("action") or "").strip():
            errors.append(f"{step_id} action needs an action name")
        if isinstance(step.get("locators"), list):
            for spec in step["locators"]:
                try:
                    locators.normalize(spec)
                except locators.LocatorError as exc:
                    errors.append(f"{step_id}: {exc}")
    for exception in workflow.get("exceptions") or []:
        if not isinstance(exception, dict) or not str(exception.get("match") or "").strip():
            errors.append("each exception needs a match pattern")
    provider = ((workflow.get("recovery") or {}).get("visual") or {}).get("provider") or ""
    if provider and provider not in ("stagehand", "midscene", "custom"):
        errors.append(f"unknown visual provider {provider!r}")
    return errors


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #

def workflow_path(data_dir: Path, workflow_id: str) -> Path:
    return workflows_dir(data_dir) / f"{workflow_id}.json"


def list_workflows(data_dir: Path) -> List[Dict[str, Any]]:
    directory = workflows_dir(data_dir)
    if not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob("*.json")):
        try:
            workflow = normalize(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, locators.LocatorError):
            continue
        rows.append({"workflow": workflow, "path": str(path), "approval": approval(data_dir, workflow)})
    return rows


def find_workflow(data_dir: Path, workflow_id: str) -> Dict[str, Any]:
    path = workflow_path(data_dir, workflow_id)
    if not path.is_file():
        raise WorkflowError(f"unknown workflow {workflow_id!r}; run `mkt workflow list`")
    return normalize(json.loads(path.read_text(encoding="utf-8")))


def save_workflow(data_dir: Path, workflow: Dict[str, Any]) -> Path:
    errors = lint(workflow)
    if errors:
        raise WorkflowError("workflow is invalid: " + "; ".join(errors))
    path = workflow_path(data_dir, str(workflow["id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# learning from an observed trace
# --------------------------------------------------------------------------- #

def learn_from_trace(trace: Dict[str, Any], *, workflow_id: Optional[str] = None,
                     name: Optional[str] = None) -> Dict[str, Any]:
    """Turn an observed step trace into a draft workflow with a locator ladder.

    The trace is produced by the operator (or an Agent observing one run):
      {"channel":"hn","action":"reply","steps":[
        {"intent":"open item","kind":"open","url":"https://news.ycombinator.com/item?id={{item}}"},
        {"intent":"type reply","kind":"fill","value":"{{text}}",
         "locators":[{"strategy":"css","value":"textarea[name=text]"},
                     {"strategy":"role","role":"textbox","name":"Comment"}]}]}
    The draft is never approved automatically.
    """
    if not isinstance(trace, dict):
        raise WorkflowError("trace must be an object")
    channel = str(trace.get("channel") or "").strip()
    action = str(trace.get("action") or "").strip()
    steps = trace.get("steps")
    if not channel or not action:
        raise WorkflowError("trace needs channel and action")
    if not isinstance(steps, list) or not steps:
        raise WorkflowError("trace needs a non-empty steps array")
    wf_id = workflow_id or f"{channel}-{action}-{datetime.now().strftime('%Y%m%d')}"
    workflow = normalize({
        "id": wf_id,
        "name": name or f"{channel} {action} (learned)",
        "version": 1,
        "channel": channel,
        "action": action,
        "source": {"kind": "observed", "ref": str(trace.get("ref") or ""), "learned_at": _now()},
        "steps": steps,
        "exceptions": trace.get("exceptions") or [
            {"match": "login|sign in|登录", "branch": "stop", "reason": "login wall"},
        ],
        "recovery": {
            "ladder": list(locators.STRATEGIES),
            "visual": {"provider": "", "enabled": False},
        },
        "review": _default_review(),
    })
    for index, step in enumerate(workflow["steps"], start=1):
        step.setdefault("id", f"s{index}")
        if isinstance(step.get("locators"), list) and step["locators"]:
            step["locators"] = locators.ladder_specs(step["locators"])
    return workflow


# --------------------------------------------------------------------------- #
# approval
# --------------------------------------------------------------------------- #

def approve(data_dir: Path, workflow: Dict[str, Any], *, by: str, days: int = 30,
            note: str = "") -> Dict[str, Any]:
    if not str(by or "").strip():
        raise WorkflowError("approval requires a named operator")
    if days < 1 or days > 365:
        raise WorkflowError("approval days must be 1..365")
    workflow = normalize(workflow)
    errors = lint(workflow)
    if errors:
        raise WorkflowError("workflow is invalid: " + "; ".join(errors))
    workflow["review"] = {
        "approved": True,
        "approved_by": str(by).strip(),
        "approved_at": _now(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds"),
        "workflow_sha256": fingerprint(workflow),
        "note": str(note or "")[:300],
    }
    path = save_workflow(data_dir, workflow)
    record = {"at": workflow["review"]["approved_at"], "by": workflow["review"]["approved_by"],
              "workflow_id": workflow["id"], "version": workflow["version"],
              "workflow_sha256": workflow["review"]["workflow_sha256"],
              "expires_at": workflow["review"]["expires_at"], "note": workflow["review"]["note"]}
    approvals_path(data_dir).parent.mkdir(parents=True, exist_ok=True)
    with approvals_path(data_dir).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"workflow": workflow, "path": str(path), "approval": workflow["review"]}


def approval(data_dir: Path, workflow: Dict[str, Any]) -> Dict[str, Any]:
    review = workflow.get("review") if isinstance(workflow.get("review"), dict) else {}
    if not review.get("approved"):
        return {"approved": False, "reason": "not approved"}
    if review.get("workflow_sha256") != fingerprint(workflow):
        return {"approved": False, "reason": "content changed after approval"}
    expires_at = str(review.get("expires_at") or "")
    if expires_at:
        try:
            deadline = datetime.fromisoformat(expires_at)
        except ValueError:
            return {"approved": False, "reason": "invalid expiry"}
        if deadline < datetime.now(timezone.utc):
            return {"approved": False, "reason": "approval expired"}
    return {"approved": True, "reason": "approved", "by": review.get("approved_by", ""),
            "expires_at": expires_at}


# --------------------------------------------------------------------------- #
# replay
# --------------------------------------------------------------------------- #

def _render(template: Optional[str], values: Dict[str, Any]) -> str:
    text = "" if template is None else str(template)

    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise WorkflowError(f"template needs {key!r} but it was not supplied")
        return str(values[key])

    return re.sub(r"\{\{(\w+)\}\}", replace, text)


def _match_exception(workflow: Dict[str, Any], text: str) -> Optional[Dict[str, Any]]:
    haystack = text or ""
    for exception in workflow.get("exceptions") or []:
        if not isinstance(exception, dict):
            continue
        try:
            if re.search(str(exception.get("match") or ""), haystack, re.I):
                return exception
        except re.error:
            continue
    return None


def _verify_step(step: Dict[str, Any], page: Any, detail: Dict[str, Any],
                 values: Dict[str, Any]) -> None:
    verify = step.get("verify") if isinstance(step.get("verify"), dict) else {}
    url_contains = str(verify.get("url_contains") or "")
    if url_contains and page is not None and url_contains not in str(getattr(page, "url", "")):
        raise WorkflowError(f"{step.get('id')}: url does not contain {url_contains!r}")
    expected = verify.get("readback")
    if expected is not None and detail.get("readback") is not None:
        if _render(expected, values) != str(detail["readback"]):
            raise WorkflowError(f"{step.get('id')}: readback mismatch")


def _execute_step(workflow: Dict[str, Any], step: Dict[str, Any], *, page: Any,
                  executor: Optional[Callable[..., Dict[str, Any]]], visual: Any,
                  values: Dict[str, Any], go: bool, campaign: str,
                  commit_hook: Optional[Callable[..., None]] = None) -> Dict[str, Any]:
    kind = str(step["kind"])
    detail: Dict[str, Any] = {"kind": kind}
    if kind == "open":
        url = _render(step.get("url"), values)
        if page is None:
            raise WorkflowError(f"{step['id']}: open needs a browser page")
        if not go:
            detail.update({"would": "open", "url": url})
            return detail
        page.goto(url)
        detail["url"] = str(getattr(page, "url", ""))
        return detail

    if kind == "action":
        if executor is None:
            raise WorkflowError(f"{step['id']}: action step needs an executor")
        payload = step.get("payload") if isinstance(step.get("payload"), dict) else {}
        rendered = {key: _render(value, values) if isinstance(value, str) else value
                    for key, value in payload.items()}
        detail["action"] = step.get("action")
        if not go:
            detail["would"] = "action"
            return detail
        result = executor(channel=str(step.get("channel") or workflow.get("channel")),
                          action=str(step.get("action")), campaign=campaign, go=True,
                          **rendered) or {}
        detail["result"] = result.get("result") or result.get("status") or "unknown"
        if str(detail["result"]) in RESULT_STOP:
            raise StepUnknown(f"{step['id']}: external result {detail['result']}")
        return detail

    if page is None:
        raise WorkflowError(f"{step['id']}: {kind} needs a browser page")
    resolved = locators.resolve(page, step.get("locators") or [], visual=visual)
    detail["strategy"] = resolved["strategy"]
    detail["tried"] = resolved["tried"]
    locator = resolved["locator"]
    if kind == "fill":
        value = _render(step.get("value"), values)
        detail["value_len"] = len(value)
        if not go:
            detail["would"] = "fill"
            return detail
        if hasattr(locator, "fill"):
            locator.fill(value)
        else:  # pragma: no cover - fake/odd pages
            locator.type(value)
        readback = None
        if hasattr(locator, "input_value"):
            readback = locator.input_value()
        elif hasattr(locator, "inner_text"):
            readback = locator.inner_text()
        detail["readback"] = readback
        expected = step.get("verify", {}).get("readback")
        if expected is not None and readback is not None:
            if _render(expected, values) != str(readback):
                raise WorkflowError(f"{step['id']}: readback mismatch after fill")
        return detail
    if kind == "click":
        commit = bool(step.get("commit"))
        detail["commit"] = commit
        if not go:
            detail["would"] = "commit_click" if commit else "click"
            return detail
        if commit:
            if commit_hook is None:
                raise WorkflowError(f"{step['id']}: commit click needs the replay commit hook")
            commit_hook("before", step, values)
        try:
            locator.click()
        except Exception:
            if commit:
                commit_hook("unknown", step, values)
            raise
        if commit:
            commit_hook("after", step, values)
        return detail
    if kind == "press":
        key = str(step.get("key") or "Enter")
        detail["key"] = key
        if not go:
            detail["would"] = "press"
            return detail
        locator.press(key)
        return detail
    if kind == "read":
        detail["text"] = locator.inner_text() if hasattr(locator, "inner_text") else ""
        return detail
    raise WorkflowError(f"{step['id']}: unknown kind {kind}")


def replay(data_dir: Path, workflow: Dict[str, Any], *, page: Any = None,
           executor: Optional[Callable[..., Dict[str, Any]]] = None,
           visual: Optional[Callable[[Any, Dict[str, str]], Any]] = None,
           go: bool = False, campaign: str = "", values: Optional[Dict[str, Any]] = None,
           log: Callable[..., Any] = print) -> Dict[str, Any]:
    """Replay one workflow. Dry-run resolves locators; --go performs the steps."""
    data_dir = Path(data_dir)
    workflow = normalize(workflow)
    errors = lint(workflow)
    if errors:
        raise WorkflowError("workflow is invalid: " + "; ".join(errors))
    state = approval(data_dir, workflow)
    if go and not state["approved"]:
        raise WorkflowNotApproved(
            f"workflow {workflow['id']} is not approved for execution: {state['reason']}")
    if go:
        for previous in list_runs(data_dir, workflow["id"]):
            if previous.get("status") in ("indeterminate", "running"):
                raise WorkflowError(
                    f"run {previous.get('run_id')} is {previous.get('status')}; reconcile it "
                    "before replaying this workflow")
    values = dict(values or {})
    run_id = f"{workflow['id']}-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    ledger = Ledger(data_dir / "ledger.jsonl") if go else None
    throttle = Throttle(data_dir / "state") if go else None

    def commit_hook(phase: str, step: Dict[str, Any], current: Dict[str, Any]) -> None:
        """UI commit steps obey pause, the daily budget and the ledger like channel writes."""
        channel = str(step.get("channel") or workflow.get("channel"))
        action = str(step.get("action") or workflow.get("action") or "publish")
        target = _render(step.get("target") or "", current) or str(getattr(page, "url", "") or "")
        if phase == "before":
            if is_paused(data_dir):
                raise WorkflowError("global pause is on; commit click refused")
            try:
                assert throttle is not None
                throttle.reserve(channel, action)
            except ThrottleError as exc:
                raise WorkflowError(str(exc)) from exc
            return
        if ledger is None:  # pragma: no cover - go implies ledger
            return
        ledger.append(channel=channel, action=action, target=target,
                      result="committed" if phase == "after" else "indeterminate",
                      transport="workflow-ui", campaign=campaign, ref=run_id,
                      detail=f"workflow {workflow['id']} step {step.get('id')}")
    run: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": RUN_KIND,
        "run_id": run_id,
        "workflow_id": workflow["id"],
        "workflow_version": workflow.get("version"),
        "workflow_sha256": fingerprint(workflow),
        "approval": state,
        "go": bool(go),
        "campaign": campaign,
        "started_at": _now(),
        "finished_at": None,
        "status": "running",
        "stop_reason": None,
        "steps": [],
    }
    path = runs_dir(data_dir) / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        path.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    save()
    try:
        for step in workflow.get("steps") or []:
            record = {"id": step.get("id"), "kind": step.get("kind"),
                      "intent": step.get("intent", ""), "state": "planned"}
            run["steps"].append(record)
            save()
            record["state"] = "running"
            record["started_at"] = _now()
            save()
            try:
                detail = _execute_step(workflow, step, page=page, executor=executor,
                                       visual=visual, values=values, go=go, campaign=campaign,
                                       commit_hook=commit_hook)
                _verify_step(step, page, detail, values)
            except StepUnknown as exc:
                record["state"] = "unknown"
                record["detail"] = str(exc)
                run["status"] = "indeterminate"
                run["stop_reason"] = "unknown_external_result"
                log(f"[workflow] {run_id} 停在 {record['id']}：外部结果未知，禁止重试", "WARN")
                break
            except Exception as exc:
                record["state"] = "failed"
                record["detail"] = f"{type(exc).__name__}: {exc}"
                exception = _match_exception(workflow, f"{exc} {record.get('detail', '')}")
                if exception is not None and exception.get("branch") == "stop":
                    run["status"] = "stopped_by_policy"
                    run["stop_reason"] = str(exception.get("reason") or "policy stop")
                elif isinstance(exc, locators.LocatorUnresolved):
                    run["status"] = "locator_unresolved"
                    run["stop_reason"] = "recovery_exhausted"
                    record["tried"] = exc.tried
                else:
                    run["status"] = "step_failed"
                    run["stop_reason"] = str(exc)[:300]
                log(f"[workflow] {run_id} 停在 {record['id']}：{record['detail']}", "WARN")
                break
            record["state"] = "done"
            record["finished_at"] = _now()
            record["detail"] = detail
            save()
        else:
            run["status"] = "dry-run" if not go else "completed"
    finally:
        run["finished_at"] = _now()
        if run["status"] == "running":
            run["status"] = "indeterminate"
            run["stop_reason"] = "interrupted"
        save()
    return {"run_id": run_id, "status": run["status"], "stop_reason": run["stop_reason"],
            "steps": len(run["steps"]), "path": str(path), "run": run}


def load_run(data_dir: Path, run_id: str) -> Dict[str, Any]:
    path = runs_dir(data_dir) / f"{run_id}.json"
    if not path.is_file():
        raise WorkflowError(f"unknown run {run_id!r}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_runs(data_dir: Path, workflow_id: Optional[str] = None) -> List[Dict[str, Any]]:
    directory = runs_dir(data_dir)
    if not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob("*.json"), reverse=True)[:200]:
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if workflow_id and run.get("workflow_id") != workflow_id:
            continue
        rows.append(run)
    return rows


def reconcile(data_dir: Path, run_id: str, *, outcome: str, note: str, by: str) -> Dict[str, Any]:
    """Close an unknown/indeterminate run with human-supplied evidence."""
    if outcome not in ("sent", "not_sent"):
        raise WorkflowError("outcome must be sent or not_sent")
    if not str(note or "").strip() or not str(by or "").strip():
        raise WorkflowError("reconciliation needs a named operator and a note")
    path = runs_dir(data_dir) / f"{run_id}.json"
    run = load_run(data_dir, run_id)
    if run.get("status") not in ("indeterminate", "running"):
        raise WorkflowError(f"run {run_id} is {run.get('status')}; nothing to reconcile")
    run.setdefault("reconciliations", []).append({
        "at": _now(), "by": str(by), "outcome": outcome, "note": str(note)[:500],
    })
    run["status"] = "resolved_sent" if outcome == "sent" else "resolved_not_sent"
    run["stop_reason"] = "reconciled"
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run
