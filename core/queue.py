#!/usr/bin/env python3
"""队列工具 —— 把门禁从"发送时"提前到"编辑时"。

动机：队列是内核唯一认识的执行单位，但此前没有任何编辑期检查——占位符、空正文、
重复目标要等到 run_one 才被 Gate 拒，一条坏项就浪费一次 dry-run 甚至一次人工复核。
queue lint 在写队列时就给出全部错误；queue build 从"模板 + 目标 CSV"生成千人千面
队列，渲染后仍过同一套门禁，残留占位符直接拒绝落盘。

队列 schema（data/queues/<campaign>.json）：
  {campaign, channel, action, items:[{id, target, body|text, ...}]}
"""
import csv
import json
from pathlib import Path

from .gates import GateError, check_content, check_target

# 内容长度上限：只收有官方硬口径的（X 280；HN 标题 80；LinkedIn 邀请附言 300）
MAX_LEN = {
    ("x", "publish"): 280,
    ("x", "reply"): 280,
    ("x", "message"): 280,
    ("hn", "publish"): 80,
    ("hn", "reply"): 4000,
    ("linkedin", "message"): 300,
}


def max_len_for(channel, action):
    return MAX_LEN.get((channel, action))


def load_queue(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def items(queue):
    return list(queue.get("items") or [])


def item_text(item):
    """正文：内联 body/text 优先，其次 body_file（千人千面正文文件）。"""
    for key in ("body", "text"):
        if item.get(key) is not None:
            return item[key]
    bf = item.get("body_file")
    if bf:
        try:
            return Path(bf).read_text(encoding="utf-8")
        except OSError:
            return None
    return None


def default_queue_path(data_dir, campaign):
    return Path(data_dir) / "queues" / f"{campaign}.json"


def lint_queue(queue, *, path=None, max_len=None, registry=None):
    """返回错误列表；空 = 可执行。不发送、不写盘。"""
    if registry is None:
        from channels import REGISTRY as registry
    errs = []

    if not isinstance(queue, dict):
        return ["队列必须是 JSON 对象"]
    campaign = queue.get("campaign")
    if not str(campaign or "").strip():
        errs.append("campaign 必填（队列必须能归属到一个 campaign）")
    elif path is not None and Path(path).stem != campaign:
        errs.append(f"campaign '{campaign}' 与文件名 '{Path(path).stem}' 不一致"
                    "（人审按 campaign 管辖，名字对不上会找不到计划）")
    channel, action = queue.get("channel"), queue.get("action")
    if not channel or not action:
        errs.append("channel/action 必填")
    elif channel not in registry:
        errs.append(f"未知渠道 {channel}")
    elif action not in registry[channel].get("actions", set()):
        errs.append(f"{channel} 不支持动作 {action}（能力: {sorted(registry[channel]['actions'])}）")

    rows = queue.get("items")
    if not isinstance(rows, list) or not rows:
        errs.append("items 不能为空")
        return errs

    seen_ids, seen_targets = set(), set()
    cap = max_len if max_len is not None else max_len_for(channel, action)
    for i, item in enumerate(rows, 1):
        tag = f"items[{i}]"
        if not isinstance(item, dict):
            errs.append(f"{tag}: 必须是对象")
            continue
        iid = str(item.get("id") or "").strip()
        if not iid:
            errs.append(f"{tag}: id 必填（账本按 id 回链）")
        elif iid in seen_ids:
            errs.append(f"{tag}: id 重复 {iid!r}")
        else:
            seen_ids.add(iid)
        raw_target = item.get("target")
        if action == "publish" and channel != "email":
            # publish 的 target 常是版块/账号/产品链接，不是触达对象，不做格式校验
            target = str(raw_target or "").strip()
        else:
            try:
                target = check_target(channel, raw_target) if channel else raw_target
            except GateError as exc:
                errs.append(f"{tag}: {exc}")
                target = raw_target
        if target and str(target).strip():
            if str(target) in seen_targets:
                errs.append(f"{tag}: 目标重复 {target}（同一队列不要重复触达）")
            seen_targets.add(str(target))
        subject = str(item.get("subject") or "")
        if subject:
            try:
                check_content(subject, label=f"[{iid or tag}] 主题 ")
            except GateError as exc:
                errs.append(str(exc))
        text = item_text(item)
        if item.get("body_file") and text is None:
            errs.append(f"{tag}: body_file 读不到：{item.get('body_file')}")
            continue
        try:
            check_content(text or "", max_len=cap, label=f"[{iid or tag}] ")
        except GateError as exc:
            errs.append(str(exc))
    return errs


def build_queue(*, template_path, targets_csv, campaign, channel, action,
                out_path, id_prefix="", force=False, max_len=None,
                subject_template=None):
    """模板 + CSV → 队列。返回 (queue|None, errors)。

    模板使用 str.format_map：列名即变量名，如 "你好 {name}，看到你在 {org}…"。
    渲染后仍过 lint（占位符残留 = 千人千面没做完，拒绝落盘）。
    """
    errs = []
    tpl = Path(template_path).read_text(encoding="utf-8")
    rows = list(csv.DictReader(Path(targets_csv).read_text(encoding="utf-8").splitlines()))
    if not rows:
        return None, ["目标 CSV 为空（至少一行数据 + 表头）"]
    if "target" not in (rows[0] or {}) and "email" not in (rows[0] or {}):
        return None, ["目标 CSV 必须有 target 列（或 email 列）"]

    out_path = Path(out_path)
    if out_path.exists() and not force:
        return None, [f"{out_path} 已存在（加 --force 覆盖）"]

    subject_tpl = Path(subject_template).read_text(encoding="utf-8") \
        if subject_template else None
    prefix = id_prefix or campaign
    built = []
    for i, row in enumerate(rows, 1):
        target = (row.get("target") or row.get("email") or "").strip()
        try:
            text = tpl.format_map({k: (v or "") for k, v in row.items()}).strip()
            subject = (subject_tpl.format_map({k: (v or "") for k, v in row.items()}).strip()
                       if subject_tpl else "")
        except KeyError as exc:
            errs.append(f"第 {i} 行：模板变量 {exc.args[0]!r} 在 CSV 里没有对应列")
            continue
        except ValueError as exc:
            errs.append(f"第 {i} 行：模板花括号不合法（{exc}）；字面花括号请写双份")
            continue
        item = {"id": f"{prefix}-{i:03d}", "target": target, "body": text}
        if subject:
            item["subject"] = subject
        built.append(item)

    queue = {"campaign": campaign, "channel": channel, "action": action, "items": built}
    errs.extend(lint_queue(queue, path=out_path, max_len=max_len))
    if errs:
        return None, errs
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(queue, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    return queue, []
