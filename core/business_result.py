"""Canonical action and business outcome projection for waves and scheduler jobs."""
from __future__ import annotations


def _action_results(result, job=None):
    rows = result.get("results") if isinstance(result.get("results"), list) else []
    skipped = result.get("skipped") if isinstance(result.get("skipped"), list) else []
    actions = {}
    for source_rows, is_skipped in ((rows, False), (skipped, True)):
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            channel = str(row.get("channel") or "unknown")
            action = str(row.get("action") or "unknown")
            key = "%s/%s" % (channel, action)
            entry = actions.setdefault(key, {
                "channel": channel, "action": action, "target": 0, "planned": 0, "attempted": 0,
                "committed": 0, "failed": 0, "indeterminate": 0, "skipped": 0,
                "attainment_rate": None, "gap": 0,
                "verified": None, "other_results": {}, "receipt_refs": [], "receipt_count": 0,
            })
            if is_skipped:
                entry["skipped"] += 1
                continue
            state = str(row.get("ledger_result") or row.get("result") or "unknown")
            state = "committed" if state == "success" else state
            entry["attempted"] += 1
            if state in {"committed", "failed", "indeterminate"}:
                entry[state] += 1
            else:
                entry["other_results"][state] = entry["other_results"].get(state, 0) + 1
            if "verified" in row or "public_verified" in row:
                if entry["verified"] is None:
                    entry["verified"] = 0
                entry["verified"] += int(
                    row.get("verified") is True or row.get("public_verified") is True)
            if row.get("ref"):
                entry["receipt_count"] += 1
                if len(entry["receipt_refs"]) < 10:
                    entry["receipt_refs"].append(str(row["ref"]))
    supply = result.get("supply") if isinstance(result.get("supply"), dict) else {}
    for key in set(actions) | set(supply):
        entry = actions.setdefault(key, {
            "channel": key.split("/", 1)[0], "action": key.split("/", 1)[-1],
            "target": 0, "planned": 0, "attempted": 0, "committed": 0,
            "failed": 0, "indeterminate": 0, "skipped": 0,
            "attainment_rate": None, "gap": 0, "verified": None,
            "other_results": {}, "receipt_refs": [], "receipt_count": 0,
        })
        item = supply.get(key) if isinstance(supply.get(key), dict) else {}
        entry["planned"] = int(item.get("scheduled") or 0)
        entry["target"] = int(item.get("configured", item.get("effective")) or 0)
        entry["attainment_rate"] = (round(entry["committed"] / entry["target"], 4)
                                    if entry["target"] else None)
        entry["gap"] = max(0, entry["target"] - entry["committed"])
    ovo = job.get("ovo") if isinstance(job, dict) and isinstance(job.get("ovo"), dict) else {}
    bound_targets = "approved_targets" in result or "business_targets" in ovo or "targets" in (job or {})
    if "business_targets" in ovo:
        targets = ovo.get("business_targets") or {}
    elif "approved_targets" in result:
        targets = result.get("approved_targets") or {}
    else:
        targets = (job or {}).get("targets") or {}
    for key, value in targets.items():
        if key not in actions:
            actions[key] = {
                "channel": key.split("/", 1)[0], "action": key.split("/", 1)[-1],
                "target": 0, "planned": 0, "attempted": 0, "committed": 0,
                "failed": 0, "indeterminate": 0, "skipped": 0,
                "attainment_rate": None, "gap": 0, "verified": None,
                "other_results": {}, "receipt_refs": [], "receipt_count": 0,
            }
        entry = actions[key]
        entry["target"] = int(value)
        entry["attainment_rate"] = round(entry["committed"] / entry["target"], 4) if entry["target"] else None
        entry["gap"] = max(0, entry["target"] - entry["committed"])
    if bound_targets:
        for key, entry in actions.items():
            if key not in targets:
                entry["target"] = 0
                entry["attainment_rate"] = None
                entry["gap"] = 0
    return actions


def _action_summary(actions):
    labels = {
        "x/publish": "X 原创", "x/reply": "X 回帖",
        "github/publish": "GitHub 发布", "github/reply": "GitHub 回帖",
        "discord/publish": "Discord 发布", "discord/reply": "Discord 回帖",
        "email/message": "Email 消息",
    }
    order = ["x/publish", "x/reply", "github/publish", "github/reply",
             "discord/publish", "discord/reply", "email/message"]
    parts = []
    for key in [key for key in order if key in actions] + sorted(set(actions) - set(order)):
        row = actions[key]
        label = labels.get(key, key)
        parts.append("%s：执行 %d，发送成功 %d，失败 %d，未知 %d，跳过 %d" % (
            label, row["attempted"], row["committed"], row["failed"],
            row["indeterminate"], row["skipped"]))
    return "；".join(parts) if parts else "业务动作：执行 0，发送成功 0，失败 0，未知 0，跳过 0"


def build_business_result(job, result, data_dir=None):
    result = result if isinstance(result, dict) else {}
    rows = result.get("results") if isinstance(result.get("results"), list) else []
    skipped = result.get("skipped") if isinstance(result.get("skipped"), list) else []
    states = [str(row.get("ledger_result") or row.get("result") or "") for row in rows
              if isinstance(row, dict)]
    committed = int(result.get("committed") or states.count("committed"))
    failed = int(result.get("failed") or states.count("failed"))
    indeterminate = int(result.get("indeterminate") or states.count("indeterminate"))
    planned = int(result.get("planned") or len(rows))
    reason = str(result.get("reason") or "")
    blockers = {}
    for row in skipped:
        if isinstance(row, dict):
            key = str(row.get("reason") or "skipped")
            blockers[key] = blockers.get(key, 0) + 1
    if reason:
        blockers[reason] = blockers.get(reason, 0) + 1
    # 渠道被停掉（429、未知回执、桥接不可用）时行根本没有结果，不写进阻断原因
    # 就会显示成「本波没有平台动作」，负责人看不到真正原因。
    stopped = result.get("stopped") if isinstance(result.get("stopped"), dict) else {}
    for channel, why in stopped.items():
        key = "%s:%s" % (channel, why)
        blockers[key] = blockers.get(key, 0) + 1
    deferred = result.get("deferred") if isinstance(result.get("deferred"), list) else []
    for row in deferred:
        if isinstance(row, dict) and row.get("reason"):
            key = str(row["reason"])
            blockers[key] = blockers.get(key, 0) + 1
    action_blocked = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        state = str(row.get("ledger_result") or row.get("result") or "")
        row_reason = str(row.get("reason") or row.get("code") or "")
        if state != "committed" and row_reason:
            action_blocked = True
            blockers[row_reason] = blockers.get(row_reason, 0) + 1
    ovo = job.get("ovo") if isinstance(job.get("ovo"), dict) else {}
    goal = str(ovo.get("business_goal") or ovo.get("wave") or job.get("id") or "")
    metrics = ovo.get("business_metrics") or [
        "external_first_run", "second_use", "x_followers", "github_stars", "verovo_deep_cases"]
    if isinstance(metrics, dict):
        metrics = list(metrics)
    comparison = {}
    if data_dir is not None and result.get("night"):
        from . import business_observations
        comparison = business_observations.compare(
            data_dir, str(result["night"]), pair_id=str(ovo.get("observation_pair_id") or ""))
    observed = comparison.get("status") in {"observed", "independently_verified"}
    actions = _action_results(result, job)
    action_summary = _action_summary(actions)
    if indeterminate:
        status = "reconciliation_required"
        disposition = "reconcile_only"
        headline = "已执行动作但有 %d 条未知结果；先对账，不能判定业务增长。" % indeterminate
        next_action = "只读核对未知回执，不重试同一动作"
    elif committed and any(row["gap"] for row in actions.values()):
        status = "partial"
        disposition = "review_shortfall"
        headline = "已提交 %d 条，仍有 %d 条目标缺口。" % (committed, sum(row["gap"] for row in actions.values()))
        next_action = "核对可承接量和阻断原因，在有效窗口内处理剩余获批项"
    elif reason or action_blocked or (stopped and not committed):
        status = "blocked"
        disposition = ("schedule_future_wave" if reason == "outside_window" else
                       "manual_rebaseline" if reason == "source_version_changed" else "manual_review")
        why = reason or ("渠道停止 " + "、".join(sorted(stopped)) if stopped else "action_blocked")
        headline = "业务任务未执行：%s；外部写 %d。" % (why, committed)
        next_action = ("修正后续波次时间，本波不越窗补跑" if reason == "outside_window" else
                       "冻结并验收构建后，由负责人决定是否新开受控波次"
                       if reason == "source_version_changed" else "处理阻断后另开受控波次，不自动重试")
    elif committed:
        status = "executed_business_observed" if observed else "executed_business_unproven"
        disposition = "observe_business_metrics"
        headline = ("已提交 %d 条平台动作；业务读数已更新，但不声明因果归因。" % committed
                    if observed else "已提交 %d 条平台动作；业务指标未测量，不能判定业务增长。" % committed)
        next_action = ("结合逐产品首用、复用与深度案例判断，不声明单波因果" if observed else
                       "补做外部首用、复用、X 粉丝、GitHub Star 与深度案例观测")
    elif skipped:
        status = "blocked_supply"
        disposition = "replenish_supply"
        headline = "业务产出为 0：%d 条供给被跳过；调度成功不代表业务完成。" % len(skipped)
        next_action = "补齐合格正文、目标与 KOC 资格后再开新波"
    else:
        status = "no_business_output"
        disposition = "prepare_next_wave"
        headline = "本波没有平台动作，也没有业务结果证据。"
        next_action = "准备可执行任务与合格供给"
    headline = action_summary + "。" + headline
    by_key = {key: row["attempted"] for key, row in actions.items()}
    run_context = result.get("run_context") if isinstance(result.get("run_context"), dict) else {}
    return {
        "schema_version": "growovo.business-result/1",
        "job_id": str(job.get("id") or ""),
        "campaign": str(result.get("campaign") or run_context.get("campaign") or ""),
        "wave": str(result.get("wave") or run_context.get("wave") or ""),
        "status": status,
        "goal": goal,
        "headline": headline,
        "action_summary": action_summary,
        "actions": actions,
        "execution": {
            "target": sum(row["target"] for row in actions.values()),
            "accepted": result.get("accepted"), "planned": planned, "attempted": len(rows), "committed": committed, "failed": failed,
            "indeterminate": indeterminate, "skipped": len(skipped), "deferred": len(deferred),
            "attainment_rate": (round(committed / sum(row["target"] for row in actions.values()), 4)
                                if sum(row["target"] for row in actions.values()) else None),
            "gap": sum(row["gap"] for row in actions.values()),
            "external_writes": committed, "by_channel_action": by_key,
        },
        "receipt_refs": [str(row.get("ref")) for row in rows
                         if isinstance(row, dict) and row.get("ref")][:10],
        "receipt_count": len([row for row in rows if isinstance(row, dict) and row.get("ref")]),
        "summary_path": str(result.get("summary_path") or ""),
        "blockers": blockers,
        "business_outcome": comparison.get("status") if comparison else "not_measured",
        "business_metrics": (comparison.get("metrics") if comparison.get("metrics") else
                             {str(metric): "not_measured" for metric in metrics}),
        "business_attribution": comparison.get("attribution") or "not_claimed",
        "business_observation": comparison,
        "operations": {
            "engine_ok": bool(result.get("ok")),
            "scheduler_status": str(result.get("job_status") or "not_applicable"),
            "exit_code": result.get("exit_code"),
            "reason": reason or None,
            "elapsed_sec": result.get("elapsed_sec"),
            "token_usage": result.get("token_usage") or "not_measured",
        },
        "recovery": {"disposition": disposition, "automatic_retry": False},
        "next_action": next_action,
    }
