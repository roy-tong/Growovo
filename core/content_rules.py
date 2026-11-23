#!/usr/bin/env python3
"""内容规则层：把 Owner 2026-11-22 的内容约束折成可执行检查。

为什么单独一层
--------------
配额决定「发多少」，内容规则决定「发出去的东西有没有可能带来转化」。
2026-11-21 夜间 585 条动作的经验：原创和回复都不带可点开工件、对象一夜
换 150 个，动作量再大也不会产生粉丝、star 或用户。配额由 Owner 保持不变，
本层只补三件事：

  x_publish   原创正文不带外链；工件链接放首条自回复（平台策略 v2 的实测结论），
              带工件按正文点名产品/仓库计
  reply       入站回复必须有对方正文；每波至少 N 条回复带可点开工件
  focus       动作优先落在固定的集中目标上，并报告命中率

边界
----
本层只做机械可查的检查与统计：不写文案、不改配额、不发送。硬规则在
``enqueue``/``plan`` 阶段拒绝，软指标写进波次摘要如实报数，不把
「没达标」粉饰成「已完成」。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

DEFAULTS: Dict[str, Any] = {
    "x_publish": {
        "links_in_body": False,
        "artifact_min_per_wave": 1,
    },
    "reply": {
        "require_context": True,
        "artifact_min_per_wave": 3,
        "artifact_min_ratio": 0.1,
    },
    "focus": {
        "min_share": 0.5,
        "targets_file": "state/focus-targets.json",
    },
}

URL_RE = re.compile(r"https?://[^\s<>\"）)]+")

# 可点开的自有工件：仓库、release、站点、可复核数据页。
ARTIFACT_HOSTS = (
    "github.com",
    "roy-tong.github.io",
    "wiretensors.ai",
)
# 裸站首页不算工件；必须指到具体仓库、release、文章或数据。
BARE_HOSTS = {"github.com", "www.github.com", "x.com", "twitter.com"}

# X 原创正文禁止外链，链接放在首条自回复；因此原创的「带工件」按点名计：
# 正文写到具体产品、仓库或仓库主人，读者可以去搜到同一个工件。
ARTIFACT_MENTIONS = re.compile(r"\b(askovo|growovo|verovo|roy-tong)\b", re.IGNORECASE)

REPLY_CONTEXT_FIELDS = ("their_text", "context", "reply_text", "source_text")


def config(policy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """合并默认值与工作区策略；只认识 content_rules 下的三段。"""
    got = json.loads(json.dumps(DEFAULTS))
    section = (policy or {}).get("content_rules") or {}
    for key, value in section.items():
        if isinstance(value, dict) and isinstance(got.get(key), dict):
            got[key].update(value)
        else:
            got[key] = value
    return got


def links(text: str) -> List[str]:
    return URL_RE.findall(str(text or ""))


def is_artifact(url: str) -> bool:
    """像工件的链接：自有仓库/发布/站点/数据页，而不是社交平台首页。"""
    lowered = url.lower()
    host = lowered.split("://", 1)[-1].split("/", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    if host not in ARTIFACT_HOSTS:
        return False
    rest = lowered.split("://", 1)[-1]
    path = rest.split("/", 1)[1] if "/" in rest else ""
    path = path.split("?", 1)[0].split("#", 1)[0].strip("/")
    if not path:
        return False
    return True


def artifact_links(text: str) -> List[str]:
    return [url for url in links(text) if is_artifact(url)]


def mentions_artifact(text: str) -> bool:
    """正文点名自有产品/仓库，算「可被读者找到的工件」。"""
    return bool(ARTIFACT_MENTIONS.search(str(text or "")))


def publish_has_artifact(text: str) -> bool:
    """X 原创的工件口径：禁外链，所以链接与点名任一命中即算。"""
    return bool(artifact_links(text)) or mentions_artifact(text)


def _target_key(row: Dict[str, Any]) -> str:
    channel = str(row.get("channel") or "")
    target = str(row.get("target") or "")
    actor = str(row.get("actor") or "")
    if channel == "x":
        handle = actor
        match = re.search(r"(?:x|twitter)\.com/([^/?#]+)", target)
        if match:
            handle = match.group(1)
        elif target and "/" not in target and "." not in target:
            handle = target  # focus-targets.json 里直接写 handle
        return "x:%s" % handle.lower().lstrip("@")
    if channel == "github":
        match = re.search(r"github\.com/([^/]+/[^/#?]+)", target)
        repo = match.group(1) if match else target.split("#")[0]
        return "github:%s" % repo.lower()
    return "%s:%s" % (channel, target.lower())


def focus_targets(data_dir, policy: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """读集中目标清单，返回 {规范化键: 理由}；文件缺失按空清单处理。"""
    cfg = config(policy)
    file = Path(str(cfg["focus"].get("targets_file") or ""))
    if not file.is_absolute():
        file = Path(data_dir) / file
    if not file.is_file():
        return {}
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("targets") if isinstance(payload, dict) else payload
    out: Dict[str, str] = {}
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        channel = str(raw.get("channel") or "")
        key = str(raw.get("key") or "")
        if not channel or not key:
            continue
        out[_target_key({"channel": channel, "target": key})] = str(raw.get("why") or "")
    return out


def in_focus(row: Dict[str, Any], focus: Dict[str, str]) -> bool:
    return bool(focus) and _target_key(row) in focus


def check_row(row: Dict[str, Any], policy: Optional[Dict[str, Any]] = None) -> str:
    """硬规则：返回拒绝原因，空字符串表示通过。"""
    cfg = config(policy)
    action = str(row.get("action") or "")
    channel = str(row.get("channel") or "")
    text = str(row.get("text") or "")

    if channel == "x" and action == "publish" and not cfg["x_publish"].get("links_in_body"):
        if links(text):
            return "x_link_in_body"

    if action == "reply" and cfg["reply"].get("require_context"):
        if row.get("context_incomplete"):
            return "reply_context_incomplete"
        has_context = any(str(row.get(name) or "").strip() for name in REPLY_CONTEXT_FIELDS)
        if not has_context:
            return "reply_context_missing"
    return ""


def summarize(rows: Iterable[Dict[str, Any]], policy: Optional[Dict[str, Any]] = None,
              *, data_dir=None, focus: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """统计一组（通常已排程的）动作是否满足内容规则。"""
    cfg = config(policy)
    if focus is None:
        focus = focus_targets(data_dir, policy) if data_dir is not None else {}
    rows = list(rows)
    replies = [r for r in rows if str(r.get("action") or "") == "reply"]
    posts = [r for r in rows if str(r.get("channel") or "") == "x"
             and str(r.get("action") or "") == "publish"]
    reply_artifacts = [r for r in replies if artifact_links(r.get("text") or "")]
    post_artifacts = [r for r in posts if publish_has_artifact(r.get("text") or "")]
    focused = [r for r in replies if in_focus(r, focus)]

    reply_floor = int(cfg["reply"].get("artifact_min_per_wave") or 0)
    reply_ratio = float(cfg["reply"].get("artifact_min_ratio") or 0)
    post_floor = int(cfg["x_publish"].get("artifact_min_per_wave") or 0)
    focus_floor = float(cfg["focus"].get("min_share") or 0)
    total = len(rows)
    reply_total = len(replies)
    share = (len(reply_artifacts) / reply_total) if reply_total else 0.0
    ratio_floor = max(reply_floor, int(reply_ratio * reply_total + 0.999)) if reply_total else 0
    focus_share = (len(focused) / reply_total) if reply_total else 0.0
    return {
        "actions": total,
        "reply_total": reply_total,
        "reply_with_artifact": len(reply_artifacts),
        "reply_artifact_share": round(share, 2),
        "reply_artifact_floor": ratio_floor if reply_total else reply_floor,
        "reply_artifact_floor_met": bool(reply_total and len(reply_artifacts) >= ratio_floor),
        "x_publish_total": len(posts),
        "x_publish_with_artifact": len(post_artifacts),
        "x_publish_artifact_floor": post_floor,
        "x_publish_artifact_floor_met": bool(posts and len(post_artifacts) >= post_floor),
        "focus_total": len(focus),
        "focus_actions": reply_total,
        "focus_hit": len(focused),
        "focus_share": round(focus_share, 2),
        "focus_floor": focus_floor,
        "focus_floor_met": bool(reply_total and focus_share >= focus_floor),
        "focus_missing": sorted(_target_key(r) for r in replies
                                if focus and not in_focus(r, focus))[:10],
    }


def gate_failures(summary: Dict[str, Any], policy: Optional[Dict[str, Any]] = None) -> List[str]:
    """Return enabled wave-level content gates that failed; empty means allowed."""
    section = (policy or {}).get("content_rules") or {}
    enforce = section.get("enforce", False)
    gates = {
        "reply_artifact": "reply_artifact_floor_met",
        "x_publish_artifact": "x_publish_artifact_floor_met",
        "focus": "focus_floor_met",
    }
    selected = set(gates) if enforce is True else {
        key for key in gates if isinstance(enforce, dict) and enforce.get(key) is True
    }
    return sorted(key for key in selected if not summary.get(gates[key], False))


def render(summary: Dict[str, Any]) -> str:
    """一行式人读摘要，供 CLI 与波次日志使用。"""
    if not summary.get("actions"):
        return "内容规则：本波没有排程动作"
    return ("内容规则：回复带工件 %d/%d（下限 %d）｜X 原创带工件 %d/%d（下限 %d）"
            "｜集中目标回复 %d/%d（下限 %.0f%%）" % (
                summary.get("reply_with_artifact", 0), summary.get("reply_total", 0),
                summary.get("reply_artifact_floor", 0),
                summary.get("x_publish_with_artifact", 0), summary.get("x_publish_total", 0),
                summary.get("x_publish_artifact_floor", 0),
                summary.get("focus_hit", 0), summary.get("focus_actions", 0),
                100 * float(summary.get("focus_floor") or 0)))
