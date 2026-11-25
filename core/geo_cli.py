"""CLI surface for Growovo SEO/GEO. Business logic lives in the geo_* modules."""
from __future__ import annotations

import json
from pathlib import Path

from . import (geo_agent, geo_capture, geo_entity, geo_experiment, geo_facts,
               geo_questions, geo_report, geo_runs, geo_score)


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _emit(payload, as_json=True):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


def _parse_surface(value):
    parts = str(value).split(":")
    name = parts[0].strip()
    tier = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "T1"
    mode = parts[2].strip() if len(parts) > 2 and parts[2].strip() else "ui_observed"
    session = parts[3].strip() if len(parts) > 3 and parts[3].strip() else "fresh"
    return {"name": name, "tier": tier, "capture_mode": mode, "session": session}


def register(parser):
    geo = parser.add_parser("geo", help="GEO：实体事实、固定问题集、采样、评分与报告")
    sub = geo.add_subparsers(dest="geo_action", required=True)

    entity = sub.add_parser("entity", help="实体事实表")
    esub = entity.add_subparsers(dest="entity_action", required=True)
    item = esub.add_parser("init", help="建立或更新事实表")
    item.add_argument("--file", required=True)
    item.add_argument("--json", action="store_true")
    item = esub.add_parser("check", help="校验事实表")
    item.add_argument("--file", default="")
    item.add_argument("--json", action="store_true")
    item = esub.add_parser("show", help="查看当前事实表")
    item.add_argument("--json", action="store_true")

    questions = sub.add_parser("questions", help="固定问题集")
    qsub = questions.add_subparsers(dest="questions_action", required=True)
    item = qsub.add_parser("import", help="导入新版本问题集")
    item.add_argument("--file", required=True)
    item.add_argument("--json", action="store_true")
    item = qsub.add_parser("freeze", help="冻结问题集版本")
    item.add_argument("--set", required=True)
    item.add_argument("--version", type=int, default=None)
    item.add_argument("--json", action="store_true")
    item = qsub.add_parser("list", help="列出问题集")
    item.add_argument("--json", action="store_true")
    item = qsub.add_parser("show", help="查看问题集")
    item.add_argument("--set", required=True)
    item.add_argument("--version", type=int, default=None)
    item.add_argument("--json", action="store_true")

    run = sub.add_parser("run", help="采样批次")
    rsub = run.add_subparsers(dest="run_action", required=True)
    item = rsub.add_parser("start", help="开始一个采样批次")
    item.add_argument("--set", required=True)
    item.add_argument("--version", type=int, default=None)
    item.add_argument("--surface", action="append", default=[],
                      help="name[:tier[:capture_mode[:session]]]，可重复")
    item.add_argument("--samples", type=int, default=3)
    item.add_argument("--operator", required=True)
    item.add_argument("--question", action="append", default=[])
    item.add_argument("--run-id", default="")
    item.add_argument("--json", action="store_true")
    item = rsub.add_parser("close", help="关闭批次")
    item.add_argument("--run", required=True)
    item.add_argument("--json", action="store_true")
    item = rsub.add_parser("status", help="查看批次状态")
    item.add_argument("--run", required=True)
    item.add_argument("--json", action="store_true")
    item = rsub.add_parser("list", help="列出批次")
    item.add_argument("--json", action="store_true")

    capture = sub.add_parser("capture", help="采集模板与导入")
    csub = capture.add_subparsers(dest="capture_action", required=True)
    item = csub.add_parser("template", help="生成采集模板")
    item.add_argument("--run", required=True)
    item.add_argument("--out", default="")
    item.add_argument("--json", action="store_true")
    item = csub.add_parser("import", help="导入观测 JSON/JSONL")
    item.add_argument("--run", required=True)
    item.add_argument("--file", required=True)
    item.add_argument("--json", action="store_true")
    item = csub.add_parser("not-run", help="记录未运行")
    item.add_argument("--run", required=True)
    item.add_argument("--question", required=True)
    item.add_argument("--surface", required=True)
    item.add_argument("--reason", required=True)
    item.add_argument("--json", action="store_true")

    item = sub.add_parser("score", help="计算指标与区间")
    item.add_argument("--run", required=True)
    item.add_argument("--json", action="store_true")
    item = sub.add_parser("report", help="生成可读报告")
    item.add_argument("--run", required=True)
    item.add_argument("--out", default="")
    item.add_argument("--json", action="store_true")

    facts = sub.add_parser("facts", help="事实表与本地资产一致性")
    fsub = facts.add_subparsers(dest="facts_action", required=True)
    item = fsub.add_parser("check", help="检查 README/包描述/llms/MCP")
    item.add_argument("--repo", required=True)
    item.add_argument("--site", default="")
    item.add_argument("--json", action="store_true")

    experiment = sub.add_parser("experiment", help="GEO 对照实验")
    esub = experiment.add_subparsers(dest="experiment_action", required=True)
    item = esub.add_parser("create", help="建立预注册实验")
    item.add_argument("--service", required=True)
    item.add_argument("--file", required=True)
    item.add_argument("--json", action="store_true")
    item = esub.add_parser("report", help="按基线/干预/对照生成报告")
    item.add_argument("--service", required=True)
    item.add_argument("--experiment", required=True)
    item.add_argument("--json", action="store_true")
    item = esub.add_parser("status", help="查看实验")
    item.add_argument("--service", required=True)
    item.add_argument("--experiment", required=True)
    item.add_argument("--json", action="store_true")

    item = sub.add_parser("agent-check", help="Agent 可读性与抓取/调用下限")
    item.add_argument("--repo", required=True)
    item.add_argument("--logs", default="")
    item.add_argument("--usage", default="")
    item.add_argument("--json", action="store_true")
    return geo


def _run_list(data_dir):
    root = Path(data_dir) / "geo" / "runs"
    if not root.is_dir():
        return []
    rows = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        try:
            rows.append(geo_runs.status(data_dir, folder.name))
        except (ValueError, OSError):
            continue
    return rows


def handle(args, data_dir):
    action = getattr(args, "geo_action", "")
    as_json = bool(getattr(args, "json", False))
    if action == "entity":
        sub = args.entity_action
        if sub == "init":
            return _emit(geo_entity.init(data_dir, _read_json(args.file)), as_json)
        if sub == "check":
            return _emit(geo_entity.check(data_dir, file=args.file or None), as_json)
        if sub == "show":
            return _emit(geo_entity.load(data_dir), as_json)
    if action == "questions":
        sub = args.questions_action
        if sub == "import":
            return _emit(geo_questions.import_set(data_dir, _read_json(args.file)), as_json)
        if sub == "freeze":
            return _emit(geo_questions.freeze(data_dir, args.set, args.version), as_json)
        if sub == "list":
            return _emit({"sets": geo_questions.list_sets(data_dir)}, as_json)
        if sub == "show":
            return _emit(geo_questions.load(data_dir, args.set, args.version), as_json)
    if action == "run":
        sub = args.run_action
        if sub == "start":
            surfaces = [_parse_surface(value) for value in (args.surface or [])]
            if not surfaces:
                surfaces = [{"name": "chatgpt_search", "tier": "T1",
                             "capture_mode": "ui_observed", "session": "fresh"}]
            return _emit(geo_runs.start(
                data_dir, args.set, surfaces=surfaces, samples_per_question=args.samples,
                operator=args.operator, set_version=args.version,
                question_ids=args.question or None, run_id=args.run_id or None), as_json)
        if sub == "close":
            return _emit(geo_runs.close(data_dir, args.run), as_json)
        if sub == "status":
            return _emit(geo_runs.status(data_dir, args.run), as_json)
        if sub == "list":
            return _emit({"runs": _run_list(data_dir)}, as_json)
    if action == "capture":
        sub = args.capture_action
        if sub == "template":
            rows = geo_capture.template(data_dir, args.run)
            if args.out:
                path = Path(args.out)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                                encoding="utf-8")
                return _emit({"run": args.run, "count": len(rows), "out": str(path)}, as_json)
            return _emit({"run": args.run, "count": len(rows), "rows": rows}, as_json)
        if sub == "import":
            return _emit(geo_capture.import_file(data_dir, args.run, args.file), as_json)
        if sub == "not-run":
            return _emit(geo_runs.add_not_run(
                data_dir, args.run, args.question, args.surface, args.reason), as_json)
    if action == "facts":
        if args.facts_action == "check":
            result = geo_facts.check(data_dir, args.repo, site=args.site or None)
            if as_json:
                return _emit(result, True)
            print(geo_facts.render(result))
            return 0
    if action == "agent-check":
        return _emit(geo_agent.check(data_dir, args.repo, logs=args.logs,
                                     usage=args.usage), as_json)
    if action == "experiment":
        if args.experiment_action == "create":
            return _emit(geo_experiment.create(
                data_dir, args.service, _read_json(args.file)), as_json)
        if args.experiment_action == "report":
            return _emit(geo_experiment.report(
                data_dir, args.service, args.experiment), as_json)
        if args.experiment_action == "status":
            return _emit(geo_experiment.status(
                data_dir, args.service, args.experiment), as_json)
    if action == "score":
        return _emit(geo_score.score(data_dir, args.run), as_json)
    if action == "report":
        result = geo_report.write(data_dir, args.run)
        if args.out:
            Path(args.out).write_text(Path(result["markdown"]).read_text(encoding="utf-8"),
                                      encoding="utf-8")
            result["out"] = args.out
        if as_json:
            return _emit(result, True)
        print(Path(result["markdown"]).read_text(encoding="utf-8"))
        return 0
    raise ValueError("未知 geo 子命令")
