from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


SCHEMA = "growovo.execution-strategy/1"
INPUT_SCHEMA = "growovo.execution-strategy-input/1"


def strategy_digest(document):
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _has(payload, path):
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or current.get(part) in (None, "", [], {}):
            return False
        current = current[part]
    return True


def _multi_complete(payload, key):
    products = payload.get("products")
    if not isinstance(products, list) or not products:
        return False
    return all(_has(product, key) for product in products)


def _question(field, text, why, example, default=None):
    return {
        "field": field,
        "question": text,
        "why": "为什么需要：" + why,
        "example": example,
        "suggested_default": copy.deepcopy(example if default is None else default),
        "answer_command": "mkt growth output-guide --file draft.json --set %s --value '<JSON>'" % field,
    }


FIELD_SPECS = [
    ("product.name", lambda p: _has(p, "product.name") or _multi_complete(p, "name"),
     lambda p: _question("product.name", "你要推广的产品叫什么？",
                         "产品名会进入目标拆解、内容主题和分产品结果报告。",
                         "Askovo")),
    ("product.url", lambda p: _has(p, "product.url") or _multi_complete(p, "url"),
     lambda p: _question("product.url", "用户可以在哪里查看或运行这个产品？请给一个公开链接。",
                         "链接是原创和私信的承接入口，也用于核对产品事实。",
                         "https://github.com/roy-tong/Askovo")),
    ("outcomes.primary", lambda p: _has(p, "outcomes.primary") or _multi_complete(p, "outcomes"),
     lambda p: _question("outcomes.primary", "最希望外部用户完成哪一个可观察动作？",
                         "主结果决定内容把用户带到哪里，也决定最后怎样验收。",
                         {"metric": "external_demo_runs", "target": 1})),
    ("audience.role", lambda p: _has(p, "audience.role") or _multi_complete(p, "audience"),
     lambda p: _question("audience.role", "哪一类人最可能从这个产品得到结果？",
                         "角色决定话题挖掘、KOC 筛选和私信资格，不能只看粉丝数。",
                         "需要复审产品决定的产品经理")),
    ("audience.trigger", lambda p: _has(p, "audience.trigger") or _multi_complete(p, "audience"),
     lambda p: _question("audience.trigger", "这些人正在遇到什么具体问题？",
                         "触发问题决定回帖是否真正相关，以及正文应该提供什么帮助。",
                         "旧产品决定缺少新证据复审")),
    ("assets.primary", lambda p: _has(p, "assets.primary") or _multi_complete(p, "assets"),
     lambda p: _question("assets.primary", "用户获得结果前，可以先运行或查看哪个资产？",
                         "可运行资产把传播和真实首用连接起来，避免只有产品宣传。",
                         {"id": "decision-replay-demo", "kind": "demo",
                          "url": "https://github.com/roy-tong/Askovo", "cta": "run_demo"})),
    ("window", lambda p: _has(p, "window.start") and _has(p, "window.end"),
     lambda p: _question("window", "这次活动从什么时候开始、什么时候结束？请包含时区。",
                         "波次时间和停止边界必须明确，迟到任务不能越过窗口补发。",
                         {"start": "2026-11-26T20:00:00+08:00",
                          "end": "2026-11-27T07:00:00+08:00"})),
    ("accounts", lambda p: _has(p, "accounts"),
     lambda p: _question("accounts", "使用哪些已授权账号？请按平台填写。",
                         "账号身份、动作额度和平台停止状态都按具体账号核验。",
                         {"x": "@elliwoodtong", "github": "roy-tong"})),
    ("budget", lambda p: _has(p, "campaign.total_actions") and _has(p, "campaign.wave_count"),
     lambda p: _question("budget", "总共计划多少条动作、分成多少波？",
                         "总量会进一步拆成平台、动作、产品和每波配额，并检查是否闭合。",
                         {"total_actions": 300, "wave_count": 10})),
    ("product.per_wave", lambda p: _has(p, "product.per_wave") or _multi_complete(p, "per_wave"),
     lambda p: _question("product.per_wave", "这个产品每波分别要多少原创、回帖和私信？",
                         "动作结构决定需要准备哪些素材，以及每波什么时候可以发送。",
                         {"x/publish": 1, "x/reply": 7, "github/reply": 1, "x/message": 1})),
    ("campaign.quality_floor", lambda p: isinstance((p.get("campaign") or {}).get("quality_floor"), int),
     lambda p: _question("campaign.quality_floor", "用于放量的素材最低质量分是多少？建议 70 分。",
                         "可发送和值得放量是不同门槛，质量分用于筛选对象与内容，不预测转化。",
                         70)),
]


def apply_answer(payload, field, value):
    if field == "budget" and isinstance(value, dict):
        allowed = {"total_actions", "wave_count"}
        if not value or any(key not in allowed for key in value):
            raise ValueError("budget 需要 total_actions 和 wave_count")
        got = copy.deepcopy(payload)
        got.setdefault("campaign", {}).update(copy.deepcopy(value))
        return got
    if field not in {spec[0] for spec in FIELD_SPECS}:
        raise ValueError("未知引导字段：%s" % field)
    got = copy.deepcopy(payload)
    parts = field.split(".")
    current = got
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = copy.deepcopy(value)
    return got


def guide(payload):
    if not isinstance(payload, dict):
        raise ValueError("输入草稿必须是 JSON 对象")
    answered = [field for field, predicate, _ in FIELD_SPECS if predicate(payload)]
    remaining = [field for field, predicate, _ in FIELD_SPECS if not predicate(payload)]
    result = {
        "ready": not remaining,
        "status": "ready" if not remaining else "needs_input",
        "answered_fields": answered,
        "remaining_fields": remaining,
    }
    if remaining:
        field = remaining[0]
        spec = next(row for row in FIELD_SPECS if row[0] == field)
        result["next_question"] = spec[2](payload)
    return result


ALLOWED_ACTIONS = {
    "x": {"publish", "reply", "message"},
    "github": {"publish", "reply"},
    "email": {"publish", "reply", "message"},
    "reddit": {"publish", "reply", "message"},
    "linkedin": {"reply", "message"},
    "hn": {"publish", "reply"},
    "discord": {"publish", "reply"},
}


def _instant(value, label):
    import datetime as dt
    try:
        parsed = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("%s 必须是 ISO 8601 时间" % label) from exc
    if parsed.tzinfo is None:
        raise ValueError("%s 必须包含时区" % label)
    return parsed


def _products(payload):
    if isinstance(payload.get("products"), list) and payload["products"]:
        return copy.deepcopy(payload["products"])
    if isinstance(payload.get("product"), dict):
        product = copy.deepcopy(payload["product"])
        product.setdefault("id", str(product.get("name") or "product").lower())
        for key in ("outcomes", "audience", "assets"):
            if key in payload:
                product[key] = copy.deepcopy(payload[key])
        return [product]
    raise ValueError("至少需要一个产品")


def _validate_mix(mix):
    if not isinstance(mix, dict) or not mix:
        raise ValueError("动作配额 per_wave 必须是非空对象")
    total = 0
    for key, value in mix.items():
        platform, separator, action = str(key).partition("/")
        if not separator or platform not in ALLOWED_ACTIONS or action not in ALLOWED_ACTIONS[platform]:
            raise ValueError("动作配额包含不支持的平台或动作：%s" % key)
        if type(value) is not int or value < 0:
            raise ValueError("动作配额必须是非负整数：%s" % key)
        total += value
    if total <= 0:
        raise ValueError("动作配额合计必须大于 0")


def compile_strategy(payload):
    if not isinstance(payload, dict):
        raise ValueError("策略输入必须是 JSON 对象")
    if payload.get("schema") not in (None, INPUT_SCHEMA):
        raise ValueError("策略输入 schema 必须是 %s" % INPUT_SCHEMA)
    products = _products(payload)
    ids = [str(product.get("id") or product.get("name") or "").strip().lower() for product in products]
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("每个产品需要唯一 id")
    for product in products:
        if not str(product.get("name") or "").strip() or not str(product.get("url") or "").strip():
            raise ValueError("每个产品都需要 name 和 url")
        _validate_mix(product.get("per_wave"))
        if not isinstance(product.get("outcomes"), dict) or not product["outcomes"].get("primary"):
            raise ValueError("产品 %s 缺少主业务目标" % product["name"])
        if not isinstance(product.get("audience"), dict) or not product["audience"].get("role") \
                or not product["audience"].get("trigger"):
            raise ValueError("产品 %s 缺少受众角色或触发问题" % product["name"])
        if not isinstance(product.get("assets"), dict) or not product["assets"].get("primary"):
            raise ValueError("产品 %s 缺少首任务资产" % product["name"])
    campaign = copy.deepcopy(payload.get("campaign") or {})
    total = campaign.get("total_actions")
    wave_count = campaign.get("wave_count")
    if type(total) is not int or total <= 0 or type(wave_count) is not int or wave_count <= 0:
        raise ValueError("campaign.total_actions 和 wave_count 必须是正整数")
    start = _instant((payload.get("window") or {}).get("start"), "window.start")
    end = _instant((payload.get("window") or {}).get("end"), "window.end")
    if end <= start:
        raise ValueError("window.end 必须晚于 window.start")
    accounts = copy.deepcopy(payload.get("accounts") or {})
    used_platforms = {key.split("/", 1)[0] for product in products for key in product["per_wave"]}
    if any(not str(accounts.get(platform) or "").strip() for platform in used_platforms):
        raise ValueError("每个使用中的平台都需要具名账号")
    import datetime as dt
    waves = []
    for index in range(wave_count):
        at = start + dt.timedelta(hours=index)
        if at >= end:
            raise ValueError("波次数量超出活动窗口")
        allocations = {
            product["id"] if product.get("id") else ids[position]: {
                key: value for key, value in product["per_wave"].items() if value}
            for position, product in enumerate(products)}
        waves.append({
            "id": "w%02d" % (index + 1),
            "at": at.isoformat(),
            "allocations": allocations,
            "total": sum(sum(mix.values()) for mix in allocations.values()),
        })
    calculated = sum(wave["total"] for wave in waves)
    if calculated != total:
        raise ValueError("动作配额与 campaign.total_actions 不闭合：%s != %s" % (calculated, total))
    by_action = {}
    by_product = {}
    for wave in waves:
        for product_id, mix in wave["allocations"].items():
            by_product[product_id] = by_product.get(product_id, 0) + sum(mix.values())
            for key, value in mix.items():
                by_action[key] = by_action.get(key, 0) + value
    items = copy.deepcopy(payload.get("items") or [])
    if not isinstance(items, list):
        raise ValueError("items 必须是数组")
    for item in items:
        content = item.get("content") or {}
        text = str(content.get("text") or "")
        if text:
            content["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            item["content"] = content
    from . import growth_quality
    for item in items:
        item["quality"] = growth_quality.evaluate_item(item, {"outcomes": {product.get("id", ids[index]): product.get("outcomes")
                                                                          for index, product in enumerate(products)},
                                                              "assets": {product.get("id", ids[index]): product.get("assets")
                                                                         for index, product in enumerate(products)}})
    planned_items = [item for item in items if not item.get("replaces")]
    recovery_count = len(items) - len(planned_items)
    document = {
        "schema": SCHEMA,
        "campaign": dict(campaign, products=ids, window=copy.deepcopy(payload["window"]),
                         accounts=accounts, total_actions=total, wave_count=wave_count),
        "outcomes": {product.get("id", ids[index]): copy.deepcopy(product["outcomes"])
                     for index, product in enumerate(products)},
        "audiences": {product.get("id", ids[index]): copy.deepcopy(product["audience"])
                      for index, product in enumerate(products)},
        "assets": {product.get("id", ids[index]): copy.deepcopy(product["assets"])
                   for index, product in enumerate(products)},
        "action_decomposition": {
            "publish": "用可保存、可运行的资产承接仓库访问和系列关注。",
            "reply": "回答目标人群正在讨论的问题；获得结果前不索取 Star 或关注。",
            "message": "向具备明确资格的对象提出一个低门槛任务邀请，不请求关注。",
            "causality": "not_claimed",
            "business_boundary": "平台 committed 不自动推进首用、Star、合格粉丝或深度协作。",
        },
        "waves": waves,
        "items": items,
        "preparation": copy.deepcopy(payload.get("preparation") or {"tasks": []}),
        "execution_results": copy.deepcopy(payload.get("execution_results") or []),
        "readiness": {
            "required": total,
            "materialized": len(planned_items),
            "eligible": 0,
            "missing": total - len(planned_items),
            "recovery_items": recovery_count,
            "by_action": by_action,
            "by_product": by_product,
        },
    }
    from . import preparation_plan
    document["preparation"] = preparation_plan.build_preparation_plan(document)
    document["readiness"]["eligible"] = sum(
        1 for item in planned_items if not _item_errors(item, document))
    document["readiness"]["missing"] = max(0, total - document["readiness"]["eligible"])
    quality_floor = int(campaign.get("quality_floor") or 0)
    quality_scores = [int((item.get("quality") or {}).get("score") or 0) for item in planned_items]
    document["readiness"]["quality"] = {
        "floor": quality_floor, "evaluated": len(quality_scores),
        "average": round(sum(quality_scores) / len(quality_scores), 2) if quality_scores else None,
        "below_floor": sum(1 for item in planned_items
                           if not _item_errors(item, document)
                           and int((item.get("quality") or {}).get("score") or 0) < quality_floor),
        "eligible": sum(1 for item in planned_items if not _item_errors(item, document)
                        and int((item.get("quality") or {}).get("score") or 0) >= quality_floor),
    }
    document["readiness"]["independent_channel_acceptance"] = [
        key for key in by_action if key == "x/message" and by_action[key]]
    document["strategy_sha256"] = strategy_digest({key: value for key, value in document.items()
                                                   if key != "strategy_sha256"})
    return document


def _item_errors(item, document):
    errors = []
    target = item.get("target") or {}
    content = item.get("content") or {}
    measurement = item.get("measurement") or {}
    action = str(item.get("action") or "")
    platform = str(item.get("platform") or item.get("channel") or "")
    if not str(item.get("id") or "").strip():
        errors.append("item_id_missing")
    if not str(item.get("wave_id") or "").strip() or not str(item.get("product") or "").strip():
        errors.append("item_scope_missing")
    if platform not in ALLOWED_ACTIONS or action not in ALLOWED_ACTIONS[platform]:
        errors.append("action_invalid")
    if action == "publish":
        if not (target.get("url") or target.get("handle") or target.get("audience")):
            errors.append("target_missing")
    elif action == "reply" and not target.get("url"):
        errors.append("target_missing")
    elif action == "message" and not target.get("handle"):
        errors.append("target_missing")
    if action != "publish" and not str(target.get("context") or "").strip():
        errors.append("context_missing")
    if action != "publish" and not isinstance(target.get("qualification"), dict):
        errors.append("qualification_missing")
    if action == "message":
        qualification = target.get("qualification") or {}
        if not any(qualification.get(key) for key in ("dm_open", "prior_interaction", "requested_contact")):
            errors.append("dm_qualification_missing")
    text = str(content.get("text") or "")
    if not text.strip():
        errors.append("text_missing")
    elif content.get("sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest():
        errors.append("content_hash_mismatch")
    if action == "publish":
        review = content.get("review") or {}
        if (review.get("reviewer") != "my-voice" or review.get("verdict") != "pass"
                or review.get("source_sha256") != content.get("sha256")):
            errors.append("my_voice_review_missing")
    if any(not str(measurement.get(key) or "").strip()
           for key in ("execution_event", "interaction_event", "business_event")):
        errors.append("measurement_missing")
    if not item.get("goal_ids"):
        errors.append("goal_missing")
    return errors


def lint_strategy(document, require_ready=False):
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        return ["schema_invalid"]
    errors = []
    items = document.get("items")
    if not isinstance(items, list):
        return ["items_invalid"]
    ids = set()
    targets = set()
    dm_handles = set()
    per_wave = {}
    recoveries = [item for item in items if item.get("replaces")]
    planned_items = [item for item in items if item not in recoveries]
    for item in items:
        for problem in _item_errors(item, document):
            if problem not in errors:
                errors.append(problem)
        item_id = str(item.get("id") or "")
        if item_id in ids:
            errors.append("duplicate_item_id")
        ids.add(item_id)
        target = item.get("target") or {}
        action = item.get("action")
        handle = str(target.get("handle") or "").lstrip("@").casefold()
        if action == "message" and handle:
            if handle in dm_handles:
                errors.append("duplicate_dm_handle")
            dm_handles.add(handle)
        target_key = target.get("url") or handle
        if action in {"reply", "message"} and target_key:
            identity = (item.get("platform") or item.get("channel"), action, target_key)
            if identity in targets:
                errors.append("duplicate_target")
            targets.add(identity)
        if item in planned_items:
            wave_id = item.get("wave_id")
            product = item.get("product")
            key = "%s/%s" % (item.get("platform") or item.get("channel"), action)
            per_wave.setdefault((wave_id, product), {}).setdefault(key, 0)
            per_wave[(wave_id, product)][key] += 1
    for wave in document.get("waves") or []:
        for product, allocation in (wave.get("allocations") or {}).items():
            actual = per_wave.get((wave.get("id"), product), {})
            if require_ready and actual != allocation:
                errors.append("wave_allocation_mismatch")
            if actual.get("x/publish", 0) > 1:
                errors.append("original_quota_exceeded")
    if recoveries:
        results = document.get("execution_results")
        if not isinstance(results, list) or not results:
            errors.append("recovery_results_required")
        else:
            from . import recovery_scope
            errors.extend(recovery_scope.validate(recoveries, planned_items, results))
    if require_ready:
        required = int((document.get("readiness") or {}).get("required") or 0)
        if len(planned_items) != required:
            errors.append("item_count_mismatch")
        if int(((document.get("readiness") or {}).get("quality") or {}).get("below_floor") or 0):
            errors.append("quality_below_floor")
    return list(dict.fromkeys(errors))


def render_strategy(document):
    readiness = document.get("readiness") or {}
    lines = [
        "# 执行策略：%s" % (document.get("campaign") or {}).get("id", "未命名活动"),
        "",
        "## 目标拆解",
        "",
    ]
    for product, outcomes in (document.get("outcomes") or {}).items():
        primary = outcomes.get("primary") or {}
        lines.append("- **%s**：%s，目标 %s；%s" % (
            product, primary.get("metric", "未命名指标"), primary.get("target", "UNKNOWN"),
            document["action_decomposition"]["business_boundary"]))
    lines.extend(["", "## 波次矩阵", "", "| 波次 | 时间 | 总量 | 各产品动作 |", "|---|---|---:|---|"])
    for wave in document.get("waves") or []:
        allocations = "; ".join("%s=%s" % (product, json.dumps(mix, ensure_ascii=False, sort_keys=True))
                                for product, mix in (wave.get("allocations") or {}).items())
        lines.append("| %s | %s | %s | %s |" % (wave["id"], wave["at"], wave["total"], allocations))
    lines.extend(["", "## 备料 TODO", ""])
    tasks = (document.get("preparation") or {}).get("tasks") or []
    if tasks:
        for task in tasks:
            lines.append("- `%s` ← %s；runner=%s；状态=%s" % (
                task["id"], ", ".join(task.get("depends_on") or []) or "可立即开始",
                task.get("runner"), task.get("status")))
    else:
        lines.append("- 待生成原创标题与正文，并用 my-voice 审查当前正文哈希。")
        lines.append("- 待挖掘回帖话题、KOC，审查回复策略并生成对应回复。")
        lines.append("- 待挖掘私信对象，核验资格并生成一对一邀请。")
    quality = readiness.get("quality") or {}
    lines.extend(["", "## 质量与渠道门", ""])
    lines.append("- 质量门=%s，平均分=%s，低于门槛=%s，达到门槛=%s；评分只用于筛选，不预测转化。" % (
        quality.get("floor"), quality.get("average"), quality.get("below_floor"), quality.get("eligible")))
    if readiness.get("independent_channel_acceptance"):
        lines.append("- 以下渠道仍需独立验收，不能仅凭素材齐备计入可完成量：%s。" % (
            "、".join(readiness["independent_channel_acceptance"])))
    lines.extend(["", "## 对象与正文", ""])
    if document.get("items"):
        for item in document["items"]:
            lines.append("### %s · %s · %s/%s" % (
                item.get("id"), item.get("product"), item.get("platform"), item.get("action")))
            quality = item.get("quality") or {}
            lines.append("质量分：%s；钩子：%s；问题：%s" % (
                quality.get("score"), (quality.get("communication") or {}).get("hook_type"),
                "、".join(quality.get("issues") or []) or "无"))
            lines.append("```json")
            lines.append(json.dumps({"target": item.get("target"), "content": item.get("content")},
                                    ensure_ascii=False, indent=2))
            lines.append("```")
    else:
        lines.append("尚未物化合格对象与正文；候选数量不能抵扣这个缺口。")
    lines.extend(["", "## 验收事件", ""])
    lines.append("- 供给：required=%s，materialized=%s，eligible=%s，missing=%s。" % (
        readiness.get("required"), readiness.get("materialized"),
        readiness.get("eligible"), readiness.get("missing")))
    lines.append("- 执行、真人互动和业务事件分别记录；committed 不等于 Star、粉丝、首用或深度协作。")
    lines.append("- 因果归因：not_claimed；无法观察的业务结果保持 UNKNOWN。")
    return "\n".join(lines) + "\n"
