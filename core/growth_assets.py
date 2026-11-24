from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import growth_runtime


def _safe(value, label):
    text = str(value or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-_]*", text):
        raise ValueError(f"{label} 格式不正确")
    return text


def _dir(data_dir, service_id, asset_id):
    return Path(data_dir) / "state" / "growth-assets" / _safe(service_id, "service_id") / _safe(asset_id, "asset_id")


def _render(spec):
    steps = "\n".join(f"{index}. {step}" for index, step in enumerate(spec["steps"], 1))
    limitations = "\n".join(f"- {row}" for row in spec.get("limitations") or [])
    return (
        f"# {spec['title']}\n\n"
        f"## 场景\n{spec['scenario']}\n\n"
        f"## 读者\n{spec['audience']}\n\n"
        f"## 你能得到什么\n{spec['value']}\n\n"
        f"## 复现步骤\n{steps}\n\n"
        f"## 预期输出\n{spec['expected_output']}\n\n"
        f"## 边界\n{limitations}\n"
    )


def _save(data_dir, service_id, asset):
    directory = _dir(data_dir, service_id, asset["id"])
    directory.mkdir(parents=True, exist_ok=True)
    markdown = directory / "asset.md"
    markdown.write_text(asset["markdown"], encoding="utf-8")
    asset["markdown_sha256"] = hashlib.sha256(markdown.read_bytes()).hexdigest()
    asset["path"] = str(markdown)
    asset["metadata_path"] = str(directory / "asset.json")

    def replace(current):
        current.clear()
        current.update(copy.deepcopy(asset))
        return current

    growth_runtime.update_domain_state(
        data_dir, "growth_assets", f"{service_id}/{asset['id']}", directory / "asset.json", replace,
        default={})
    return asset


def load(data_dir, service_id, asset_id):
    return growth_runtime.load_domain_state(
        data_dir, "growth_assets", f"{service_id}/{asset_id}",
        _dir(data_dir, service_id, asset_id) / "asset.json")


def create(data_dir, service_id, spec):
    for key in ("id", "title", "scenario", "audience", "value", "steps", "expected_output"):
        if not spec.get(key):
            raise ValueError(f"asset.{key} 必填")
    if not isinstance(spec["steps"], list) or not all(isinstance(row, str) for row in spec["steps"]):
        raise ValueError("asset.steps 必须是字符串数组")
    reproduction = dict(spec.get("reproduction") or {})
    if reproduction.get("authorized") is not True or not str(reproduction.get("by") or "").strip():
        raise ValueError("reproduction 必须 authorized=true 且署名 by")
    command = reproduction.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(row, str) for row in command):
        raise ValueError("reproduction.command 必须是 argv 数组")
    timeout = int(reproduction.get("timeout_sec") or 60)
    result = subprocess.run(
        command, shell=False, cwd=reproduction.get("cwd") or str(Path(__file__).resolve().parents[1]),
        capture_output=True, text=True, timeout=timeout)
    asset = {
        "schema": "growovo.value-asset/1",
        "id": _safe(spec["id"], "asset_id"),
        "service_id": _safe(service_id, "service_id"),
        "title": spec["title"],
        "scenario": spec["scenario"],
        "audience": spec["audience"],
        "value": spec["value"],
        "cta": spec.get("cta") or {},
        "steps": list(spec["steps"]),
        "expected_output": spec["expected_output"],
        "limitations": list(spec.get("limitations") or []),
        "markdown": _render(spec),
        "reproduced": result.returncode == 0,
        "reproduction": {
            "command_sha256": hashlib.sha256(
                json.dumps(command, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "exit_code": result.returncode,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
            "by": reproduction["by"],
            "executed_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "variants": {},
    }
    return _save(data_dir, service_id, asset)


def _x_text(asset):
    url = str((asset.get("cta") or {}).get("url") or "")
    title = asset["title"]
    value = asset["value"]
    suffix = ("\n\n" + url) if url else ""
    room = 280 - len(title) - len(suffix) - 4
    if room < 40:
        title = title[:80].rstrip()
        room = 280 - len(title) - len(suffix) - 4
    value = value[:max(1, room)].rstrip()
    return f"{title}\n\n{value}{suffix}"[:280].rstrip()


def repurpose(data_dir, service_id, asset_id, channels):
    asset = load(data_dir, service_id, asset_id)
    if not asset.get("reproduced"):
        raise ValueError("asset 必须 reproduced 后才能分发")
    directory = _dir(data_dir, service_id, asset_id) / "variants"
    directory.mkdir(parents=True, exist_ok=True)
    variants = {}
    for channel in channels:
        if channel == "x":
            text = _x_text(asset)
        elif channel == "github":
            text = asset["markdown"]
        elif channel == "email":
            text = (f"Subject: {asset['title']}\n\n{asset['value']}\n\n"
                    + "\n".join(f"{index}. {row}" for index, row in enumerate(asset["steps"], 1))
                    + "\n\n" + str((asset.get("cta") or {}).get("url") or ""))
        else:
            raise ValueError(f"不支持的渠道版本 {channel}")
        path = directory / f"{channel}.md"
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
        variants[channel] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    asset["variants"] = variants
    _save(data_dir, service_id, asset)
    return variants
