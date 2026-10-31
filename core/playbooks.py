#!/usr/bin/env python3
"""Playbook：把平台日常运营从“单条动作”提升为可审核的工作闭环。

第一个 Playbook 是 GitHub Inbox Operations：
  读取授权的 GitHub issue/PR
  → 规则分流（bug / question / feature / PR / security / triage）
  → 生成可复核的 triage 计划
  → 人工或 Agent 提供候选回复
  → 构建标准队列（仍需 plan 审批 + dry-run + queue runner）
  → 由统一写路径执行并在目标平台回读

本模块只读写本地计划/队列，不直接发送任何消息。所有发送仍由 mkt queue run 的
Gate、暂停闸、日预算、人审和账本执行；不能绕过现有安全边界。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .queue import lint_queue

SCHEMA_VERSION = "1.0"
_PLAYBOOK = "github-inbox"
_NAME_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
_SECURITY_RE = re.compile(
    r"\b(security|vulnerab|exploit|cve-|credential|secret leak|data leak|"
    r"unauthori[sz]ed access|rce|xss|csrf)\b", re.I)
_QUESTION_RE = re.compile(
    r"(\?|？|\bhow\b|\bwhat\b|\bwhy\b|\bcan i\b|\bhelp\b|怎么|如何|为什么|是否|请问)",
    re.I)
_BUG_LABELS = {"bug", "regression", "defect", "crash"}
_FEATURE_LABELS = {"enhancement", "feature", "feature-request", "improvement"}


def _clean(value, limit=1200):
    return str(value or "").replace("\x00", "").strip()[:limit]


def _labels(row):
    return {str(x).strip().lower() for x in (row.get("labels") or []) if str(x).strip()}


def _priority(row, default="P2"):
    age = row.get("age_days")
    try:
        age = int(age)
    except (TypeError, ValueError):
        age = None
    if row.get("zero_reply") and age is not None and age >= 7:
        return "P0"
    if row.get("zero_reply") or (age is not None and age >= 3):
        return "P1"
    return default


def triage_github_inbox(rows, repo):
    """对 GitHub issue/PR 行做确定性分流；不生成最终回复文本。

    disposition:
      reply_candidate  可以进入候选回复（仍需排队审批）
      review            PR/需要代码审查，交给人
      escalate         安全、敏感或权限问题，不公开自动回复
      triage_only       仅记录，暂不回复
    """
    if not _NAME_RE.match(str(repo or "").strip()):
        raise ValueError("repo 应为 owner/repo")
    if not isinstance(rows, list):
        raise ValueError("rows 必须是数组")
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            number = int(row.get("n"))
        except (TypeError, ValueError):
            continue
        title = _clean(row.get("title"), 200)
        body = _clean(row.get("body"))
        labels = _labels(row)
        kind = "PR" if str(row.get("kind", "")).upper() == "PR" else "issue"
        blob = f"{title}\n{body}"
        if _SECURITY_RE.search(blob) or labels.intersection({"security", "vulnerability"}):
            disposition, action = "escalate", "按 SECURITY.md 私下处理，不公开自动回复"
            default_priority = "P0"
        elif kind == "PR":
            disposition, action = "review", "人工审查代码与测试，再决定评论"
            default_priority = "P1"
        elif labels.intersection(_BUG_LABELS):
            disposition, action = "reply_candidate", "先复现或要求最小复现，再给出状态"
            default_priority = "P1"
        elif labels.intersection(_FEATURE_LABELS):
            disposition, action = "reply_candidate", "确认场景与替代方案，不做未承诺的路线图承诺"
            default_priority = "P2"
        elif _QUESTION_RE.search(blob):
            disposition, action = "reply_candidate", "引用文档/已知限制，回答问题"
            default_priority = "P1"
        else:
            disposition, action = "triage_only", "补充标签或等待更多信息"
            default_priority = "P2"
        priority = "P0" if disposition == "escalate" else _priority(row, default_priority)
        items.append({
            "id": f"gh-{number}",
            "number": number,
            "kind": kind,
            "title": title,
            "author": _clean(row.get("author"), 80),
            "labels": sorted(labels),
            "created": _clean(row.get("created"), 30),
            "age_days": row.get("age_days"),
            "zero_reply": bool(row.get("zero_reply")),
            "url": _clean(row.get("url"), 300),
            "disposition": disposition,
            "priority": priority,
            "suggested_action": action,
            "draft_required": disposition == "reply_candidate",
            "draft_status": "missing" if disposition == "reply_candidate" else "not_required",
        })
    items.sort(key=lambda x: (x["priority"], -(x["age_days"] or 0), x["number"]))
    counts = {}
    for item in items:
        counts[item["disposition"]] = counts.get(item["disposition"], 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "growovo.playbook.plan",
        "playbook": _PLAYBOOK,
        "repo": repo,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "counts": counts,
        "items": items,
    }


def plan_path(data_dir, campaign):
    return Path(data_dir) / "playbooks" / _PLAYBOOK / f"{campaign}.json"


def save_plan(data_dir, plan, *, campaign):
    if not str(campaign or "").strip():
        raise ValueError("campaign 不能为空（计划必须能归属）")
    plan = dict(plan)
    plan["campaign"] = campaign
    path = plan_path(data_dir, campaign)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return plan, path


def load_drafts(path):
    """读取候选回复：{id|number: body} 或 {drafts:[{id,body}, ...]}。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("drafts"), (dict, list)):
        data = data["drafts"]
    if isinstance(data, list):
        pairs = []
        for row in data:
            if isinstance(row, dict) and row.get("id") is not None:
                pairs.append((row.get("id"), row.get("body")))
    elif isinstance(data, dict):
        pairs = list(data.items())
    else:
        raise ValueError("drafts 必须是对象映射或 {drafts:[...]}")
    out = {}
    for key, value in pairs:
        raw = str(key).strip()
        if raw.startswith("gh-"):
            raw = raw[3:]
        try:
            number = int(raw)
        except ValueError:
            continue
        body = str(value or "").strip()
        if body:
            out[number] = body
    return out


def build_reply_queue(plan, drafts, out_path, *, force=False):
    """只把 reply_candidate 且有草稿的项变成标准队列；不发送。"""
    repo = plan.get("repo")
    if not _NAME_RE.match(str(repo or "")):
        return None, ["计划缺少合法 repo"], {}
    campaign = str(plan.get("campaign") or "").strip()
    if not campaign:
        return None, ["计划缺少 campaign"], {}
    out_path = Path(out_path)
    if out_path.exists() and not force:
        return None, [f"{out_path} 已存在（加 --force 覆盖）"], {}
    items, missing, ignored = [], [], []
    for item in plan.get("items") or []:
        if item.get("disposition") != "reply_candidate":
            if item.get("number") in drafts:
                ignored.append(item.get("number"))
            continue
        body = drafts.get(item.get("number"))
        if not body:
            missing.append(item.get("number"))
            continue
        items.append({
            "id": item["id"],
            "target": f"issue:{repo}#{item['number']}",
            "body": body,
            "title_ref": item.get("title", ""),
        })
    if not items:
        return None, ["没有可入队的回复草稿（reply_candidate 为空或全部缺草稿）"], {
            "missing": missing, "ignored": ignored}
    queue = {
        "campaign": campaign,
        "channel": "github",
        "action": "reply",
        "playbook": _PLAYBOOK,
        "items": items,
    }
    errs = lint_queue(queue, path=out_path)
    if errs:
        return None, errs, {"missing": missing, "ignored": ignored}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(queue, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    return queue, [], {
        "missing": missing,
        "ignored": ignored,
        "items": len(items),
    }


def render_markdown(plan):
    lines = [
        f"# GitHub Inbox Playbook · {plan.get('repo')}",
        "",
        f"- campaign：`{plan.get('campaign') or '未指定'}`",
        f"- 生成时间：{plan.get('generated_at')}",
        f"- 分流：{json.dumps(plan.get('counts') or {}, ensure_ascii=False)}",
        "",
        "| 优先级 | 事项 | 类型 | 分流 | 建议 | 草稿 |",
        "|---|---|---|---|---|---|",
    ]
    for item in plan.get("items") or []:
        lines.append(
            "| {p} | #{n} {title} | {k} | {d} | {a} | {s} |".format(
                p=item.get("priority", ""), n=item.get("number", ""),
                title=str(item.get("title", "")).replace("|", "\\|"),
                k=item.get("kind", ""), d=item.get("disposition", ""),
                a=str(item.get("suggested_action", "")).replace("|", "\\|"),
                s=item.get("draft_status", "")))
    lines += [
        "",
        "下一步：为 `reply_candidate` 提供候选回复 JSON，再构建队列；",
        "队列仍需 `mkt plan approve`、dry-run 和 `mkt queue run --go`。",
        "本 Playbook 不会自动发送，也不会自动修改 issue 标签。",
        "",
    ]
    return "\n".join(lines)
