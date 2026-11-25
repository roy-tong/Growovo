"""Readable GEO report with denominators, intervals, conflicts and not_run."""
from __future__ import annotations

import json
from pathlib import Path

from .geo_questions import load as load_questions
from .geo_runs import load as load_run, observations as load_observations
from .geo_score import load as load_score
from .storage import atomic_write_text


def report_dir(data_dir) -> Path:
    path = Path(data_dir) / "geo" / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _percent(rate) -> str:
    value = rate.get("value")
    if value is None:
        return "—"
    ci = rate.get("ci95") or [None, None]
    if ci[0] is None or ci[1] is None:
        return "%.0f%%" % (value * 100)
    return "%.0f%% [%.0f–%.0f]" % (value * 100, ci[0] * 100, ci[1] * 100)


def _table(headers, rows) -> str:
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    return "\n".join(lines)


def render(data_dir, run_id: str) -> str:
    run = load_run(data_dir, run_id)
    score = load_score(data_dir, run_id)
    question_set = load_questions(data_dir, run["set_id"], run["set_version"])
    observations = load_observations(data_dir, run_id)
    questions = {row["id"]: row for row in question_set.get("questions") or []}
    total = len(observations)
    not_run = run.get("not_run") or []
    groups = score.get("groups") or []
    lines = [
        "# GEO 可见度报告 · %s" % run_id,
        "",
        "> 实体版本：v%s；问题集：%s v%s；样本：%s 个有效观测；未运行：%s 条。"
        % (run["entity_version"], run["set_id"], run["set_version"], total, len(not_run)),
        "> 采集面：%s。" % "；".join(
            "%s（%s / %s）" % (row["name"], row["tier"], row["capture_mode"])
            for row in run["surfaces"]),
        "> 评分版本：%s；输入哈希：`%s`。" % (score.get("score_version"),
                                              score.get("inputs_sha256")),
        "",
        "## 结论摘要",
        "",
    ]
    decision = [row for row in groups if row.get("sample_status") == "decision_grade"]
    if not groups:
        lines.append("- 没有可评分观测；这不是“没有推荐”，是“没有数据”。")
    else:
        lines.append("- 共 %d 个分组；其中 %d 个达到决策级样本（n ≥ 24）。"
                     % (len(groups), len(decision)))
        for row in groups:
            lines.append("- %s / %s / %s：有效推荐率 %s，n=%d（%s）。"
                         % (row["intent"], row["brand_scope"], row["tier"],
                            _percent(row["recommendation_rate"]), row["n"],
                            row["sample_status"]))
    lines.extend(["", "## 分组指标", ""])
    lines.append(_table(
        ["意图", "品牌范围", "层级", "n", "提及率", "引用率", "有效推荐率", "准确率", "未运行"],
        [[row["intent"], row["brand_scope"], row["tier"], row["n"],
          _percent(row["mention_rate"]), _percent(row["citation_rate"]),
          _percent(row["recommendation_rate"]), _percent(row["accuracy_rate"]),
          row["not_run"]] for row in groups]))
    lines.extend(["", "## 事实冲突", ""])
    conflicts = []
    for row in observations:
        for item in row.get("fact_conflicts") or []:
            conflicts.append([
                row["observation_id"], questions.get(row["question_id"], {}).get("text", ""),
                item.get("fact_id"), item.get("expected"), item.get("observed"),
                item.get("severity"),
            ])
    if conflicts:
        lines.append(_table(["观测", "问题", "事实", "期望", "实际", "严重度"], conflicts))
    else:
        lines.append("- 未记录事实冲突。这不等于所有表述都准确；只表示本次观测没有发现冲突。")
    lines.extend(["", "## 未运行", ""])
    if not_run:
        lines.append(_table(
            ["问题", "平台", "原因", "记录时间"],
            [[row.get("question_id"), row.get("surface"), row.get("reason"),
              row.get("recorded_at")] for row in not_run]))
    else:
        lines.append("- 无。")
    lines.extend([
        "",
        "## 限制",
        "",
        "- 这是界面观察（T1）或人工导入（T2），不代表平台官方数据，也不代表其他用户、地区或账号。",
        "- 小样本分组只做描述或探索，不用于宣称提升或下降；未运行条目不会被当成 0。",
        "- 提及、引用、推荐、准确率是中间指标；业务结果要看首次成功使用与第二次使用。",
        "- 本报告不做因果归因；因果结论只能来自有对照、有时间顺序、无并行改动的实验。",
        "",
        "## 下一步",
        "",
        "- 先处理阻断级事实冲突与未运行条目，再决定是否扩大样本。",
        "- 若要做前后比较，保持同一问题集、同一平台、同一评分版本，并设置同期对照组。",
        "",
    ])
    return "\n".join(lines)


def write(data_dir, run_id: str):
    run = load_run(data_dir, run_id)
    score = load_score(data_dir, run_id)
    observations = load_observations(data_dir, run_id)
    question_set = load_questions(data_dir, run["set_id"], run["set_version"])
    questions = {row["id"]: row for row in question_set.get("questions") or []}
    conflicts = []
    for row in observations:
        for item in row.get("fact_conflicts") or []:
            conflicts.append({"observation_id": row["observation_id"],
                              "question_id": row["question_id"],
                              "question": questions.get(row["question_id"], {}).get("text", ""),
                              **item})
    payload = {
        "run": run,
        "score": score,
        "fact_conflicts": conflicts,
        "not_run": run.get("not_run") or [],
        "limitations": [
            "UI observed or manual import; not platform-official data.",
            "Small groups are descriptive or exploratory, not evidence of change.",
            "No causal attribution without a concurrent control group.",
            "Business results require first successful use and second use.",
        ],
    }
    md_path = report_dir(data_dir) / (run_id + ".md")
    json_path = report_dir(data_dir) / (run_id + ".json")
    atomic_write_text(md_path, render(data_dir, run_id).rstrip() + "\n")
    atomic_write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=1,
                                            sort_keys=True) + "\n")
    return {"run_id": run_id, "markdown": str(md_path), "json": str(json_path)}
