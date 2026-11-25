"""Operator-authorized black-box Agent discovery, installation and first-success runs."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "growovo.agent-adoption-suite/1"
RESULT_SCHEMA = "growovo.agent-adoption-result/1"
STEP_IDS = ("discover", "install", "first_success")
OUTPUT_LIMIT = 1024 * 1024


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate(spec):
    if not isinstance(spec, dict) or spec.get("schema") != SCHEMA:
        raise ValueError("agent adoption suite schema must be %s" % SCHEMA)
    for key in ("suite_id", "input_class", "product", "host", "authorization"):
        if not spec.get(key):
            raise ValueError("agent adoption suite.%s is required" % key)
    if spec["input_class"] not in ("synthetic", "demo", "real_authorized"):
        raise ValueError("unknown input_class")
    if not str(spec["authorization"].get("authorized_by") or "").strip():
        raise ValueError("authorization.authorized_by is required")
    host = spec["host"]
    if not isinstance(host.get("argv"), list) or not host["argv"]:
        raise ValueError("host.argv must be a non-empty array")
    if not str(host.get("name") or "").strip() or not str(host.get("model") or "").strip():
        raise ValueError("host name/model are required")
    steps = spec.get("steps")
    if not isinstance(steps, list) or tuple(row.get("id") for row in steps) != STEP_IDS:
        raise ValueError("steps must be discover/install/first_success in order")
    for step in steps:
        if not str(step.get("intent") or "").strip():
            raise ValueError("every step needs a natural-language intent")


def _environment(host):
    env = {"PYTHONUNBUFFERED": "1"}
    for key in ("PATH", "HOME", "SYSTEMROOT", "COMSPEC"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    for key in host.get("env_passthrough") or []:
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _argv(host, intent, workspace):
    values = [str(value).replace("{intent}", intent).replace("{workspace}", str(workspace))
              for value in host["argv"]]
    if not any("{intent}" in str(value) for value in host["argv"]):
        values.append(intent)
    return values


def _safe_output(workspace, relative):
    path = (workspace / relative).resolve()
    path.relative_to(workspace.resolve())
    return path


def _checks(step, result, workspace):
    success = step.get("success") if isinstance(step.get("success"), dict) else {}
    expected_exit = int(success.get("exit_code", 0))
    checks = [{"check": "exit_code", "passed": result["exit_code"] == expected_exit,
               "expected": expected_exit, "actual": result["exit_code"]}]
    for stream in ("stdout", "stderr"):
        for phrase in success.get(stream + "_contains") or []:
            checks.append({"check": stream + "_contains", "passed": phrase in result[stream],
                           "expected": phrase})
    for item in success.get("output_files") or []:
        try:
            path = _safe_output(workspace, item["path"])
            text = path.read_text(encoding="utf-8")
            passed = str(item.get("contains") or "") in text
            detail = "present" if passed else "content mismatch"
        except (OSError, ValueError) as exc:
            passed = False
            detail = str(exc)
        checks.append({"check": "output_file", "passed": passed,
                       "path": item["path"], "detail": detail})
    if result.get("truncated"):
        checks.append({"check": "output_limit", "passed": False,
                       "detail": "stdout/stderr exceeded the evidence cap"})
    return checks


def run(spec, workspace, out_dir):
    _validate(spec)
    workspace = Path(workspace).resolve()
    out = Path(out_dir).resolve()
    if out.exists():
        raise FileExistsError("refusing to overwrite adoption evidence: %s" % out)
    out.mkdir(parents=True)
    workspace.mkdir(parents=True, exist_ok=True)
    step_dir = out / "steps"
    step_dir.mkdir()
    records = []
    stopped = False
    for index, step in enumerate(spec["steps"], 1):
        relative_base = "steps/%02d-%s" % (index, step["id"])
        if stopped:
            records.append({"id": step["id"], "status": "NOT_RUN",
                            "reason": "previous step failed"})
            continue
        started = time.monotonic()
        started_at = _now()
        result = {"exit_code": None, "stdout": "", "stderr": "", "truncated": False}
        error = ""
        try:
            completed = subprocess.run(
                _argv(spec["host"], step["intent"], workspace), cwd=workspace,
                env=_environment(spec["host"]), text=True, capture_output=True,
                timeout=min(600, max(1, int(step.get("timeout_seconds", 60)))))
            result.update({"exit_code": completed.returncode,
                           "stdout": completed.stdout, "stderr": completed.stderr})
        except subprocess.TimeoutExpired as exc:
            error = "TIMEOUT"
            result["stdout"] = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
            result["stderr"] = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        except (OSError, ValueError) as exc:
            error = type(exc).__name__ + ": " + str(exc)
        for stream in ("stdout", "stderr"):
            if len(result[stream].encode("utf-8", errors="replace")) > OUTPUT_LIMIT:
                result[stream] = result[stream][:OUTPUT_LIMIT]
                result["truncated"] = True
        stdout_ref = relative_base + ".stdout.txt"
        stderr_ref = relative_base + ".stderr.txt"
        (out / stdout_ref).write_text(result["stdout"], encoding="utf-8")
        (out / stderr_ref).write_text(result["stderr"], encoding="utf-8")
        checks = _checks(step, result, workspace) if not error else []
        passed = not error and all(row["passed"] for row in checks)
        record = {
            "id": step["id"], "status": "PASS" if passed else "FAIL",
            "started_at": started_at, "finished_at": _now(),
            "duration_ms": round((time.monotonic() - started) * 1000),
            "exit_code": result["exit_code"], "error": error,
            "stdout_ref": stdout_ref, "stderr_ref": stderr_ref,
            "checks": checks,
        }
        records.append(record)
        if not passed:
            stopped = True
    all_passed = all(row["status"] == "PASS" for row in records)
    review = spec.get("human_review") if isinstance(spec.get("human_review"), dict) else {}
    real_success = (all_passed and spec["input_class"] == "real_authorized"
                    and review.get("accepted") is True
                    and bool(str(review.get("reviewed_by") or "").strip())
                    and spec.get("solution_hints_provided") is not True)
    outcome = ("REAL_SUCCESS" if real_success else
               "OBSERVED_SUCCESS" if all_passed else "FAILED")
    report = {
        "schema": RESULT_SCHEMA,
        "suite_id": spec["suite_id"],
        "outcome": outcome,
        "real_success_eligible": real_success,
        "input_class": spec["input_class"],
        "product": copy.deepcopy(spec["product"]),
        "agent": {"host": spec["host"]["name"], "model": spec["host"]["model"]},
        "fresh_process_per_step": True,
        "fresh_model_session_verified": False,
        "solution_hints_provided": spec.get("solution_hints_provided") is True,
        "steps": records,
        "human_review": review,
        "authorization": copy.deepcopy(spec["authorization"]),
        "external_result_observed": False,
        "evidence_boundary": "Operator-authorized host processes, not a network sandbox. Discovery/install/first-success observations are not retention, payment or causal product value.",
        "started_at": records[0].get("started_at") if records else _now(),
        "finished_at": _now(),
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    report["evidence_dir"] = str(out)
    return report
