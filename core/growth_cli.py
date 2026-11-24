from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import action_resolution, growth_assets, growth_engagement, growth_execution, growth_experiments, growth_intake, growth_learning, growth_metrics, growth_opportunities, growth_service
from . import execution_capabilities, github_app, github_events, growth_runtime
from . import gmail_api, gmail_events, growth_worker, live_acceptance
from . import growth_cycle, risk_policy
from .growth_goals import compile_goal


def register(parser):
    sub = parser.add_subparsers(dest="growth_action", required=True)
    for name in ("compile", "start"):
        item = sub.add_parser(name)
        item.add_argument("--file", required=True)
        item.add_argument("--out", default="")
        item.add_argument("--json", action="store_true")
    for name in ("status", "next", "attempts"):
        item = sub.add_parser(name)
        item.add_argument("service_id")
        item.add_argument("--json", action="store_true")
    sub.add_parser("recover")
    item = sub.add_parser("capabilities")
    item.add_argument("--account", default="default")
    item.add_argument("--platform", default="")
    item = sub.add_parser("capability-upsert")
    item.add_argument("--file", required=True)
    item.add_argument("--by", required=True)
    item = sub.add_parser("github-sync")
    item.add_argument("service_id")
    item.add_argument("--repo", required=True)
    item = sub.add_parser("github-webhook-serve")
    item.add_argument("service_id")
    item.add_argument("--host", default="127.0.0.1")
    item.add_argument("--port", type=int, default=8765)
    item = sub.add_parser("gmail-sync")
    item.add_argument("service_id")
    item.add_argument("--account", default="default")
    item = sub.add_parser("gmail-watch-renew")
    item.add_argument("service_id")
    item.add_argument("--topic", required=True)
    item.add_argument("--account", default="default")
    item = sub.add_parser("gmail-pubsub-pull")
    item.add_argument("service_id")
    item.add_argument("--subscription", required=True)
    item.add_argument("--account", default="default")
    item = sub.add_parser("operator-serve")
    item.add_argument("service_id")
    item.add_argument("--wave", default="")
    item.add_argument("--repo", default="")
    item.add_argument("--gmail-sync", action="store_true")
    item.add_argument("--watch-topic", default="")
    item.add_argument("--pubsub-subscription", default="")
    item.add_argument("--account", default="default")
    item.add_argument("--interval-seconds", type=int, default=300)
    item.add_argument("--once", action="store_true")
    item.add_argument("--max-cycles", type=int, default=None)
    item.add_argument("--webhook-port", type=int, default=0)
    item = sub.add_parser("worker-status")
    item.add_argument("service_id")
    item.add_argument("--account", default="default")
    item = sub.add_parser("worker-history")
    item.add_argument("service_id")
    item.add_argument("--account", default="default")
    item.add_argument("--limit", type=int, default=100)
    item = sub.add_parser("live-preflight")
    item.add_argument("--repo", default="")
    item.add_argument("--gmail", action="store_true")
    item.add_argument("--x", action="store_true")
    item.add_argument("--x-action", choices=["publish", "reply"], default="publish")
    item = sub.add_parser("reconcile")
    item.add_argument("service_id")
    item.add_argument("--action", required=True)
    item.add_argument("--by", required=True)
    item.add_argument("--text", required=True)
    item.add_argument("--target", required=True)
    item.add_argument("--thread-id", default="")
    item.add_argument("--channels", default="{}")
    item = sub.add_parser("transition")
    item.add_argument("service_id")
    item.add_argument("--action", required=True)
    item.add_argument("--status", required=True,
                      choices=["pending", "in_progress", "waiting_manual", "waiting_verification",
                               "completed", "blocked"])
    item.add_argument("--by", required=True)
    item.add_argument("--note", default="")
    item.add_argument("--minutes", type=int, default=0)
    item.add_argument("--evidence", default="")
    item.add_argument("--completion-kind", choices=["verified", "attested_manual"], default="",
                      help="渠道动作标记 completed 时的完成类型；verified 需同时给 --receipt")
    item.add_argument("--receipt", default="", help="verified 回执 JSON：action_id/channel/sent_at/idempotency_key/readback")
    item.add_argument("--reason", default="", help="attested_manual 的人工完成原因")
    item.add_argument("--json", action="store_true")
    item = sub.add_parser("evaluate")
    item.add_argument("service_id")
    item.add_argument("--metrics", required=True)
    item.add_argument("--by", required=True)
    item.add_argument("--json", action="store_true")
    item = sub.add_parser("resolve")
    item.add_argument("service_id")
    item.add_argument("--action", required=True)
    item.add_argument("--channels", default="{}", help="渠道可用性 JSON")
    item.add_argument("--json", action="store_true")
    item = sub.add_parser("opportunity-import")
    item.add_argument("service_id")
    item.add_argument("--file", required=True)
    item = sub.add_parser("inbox-import")
    item.add_argument("service_id")
    item.add_argument("--file", required=True)
    item = sub.add_parser("inbox-draft")
    item.add_argument("service_id")
    item.add_argument("--conversation", required=True)
    item.add_argument("--text", required=True)
    item.add_argument("--by", required=True)
    item = sub.add_parser("inbox-terminal")
    item.add_argument("service_id")
    item.add_argument("--conversation", required=True)
    item.add_argument("--status", required=True,
                      choices=["committed", "dismissed", "needs_reconciliation"])
    item.add_argument("--by", required=True)
    item.add_argument("--reason", default="")
    item.add_argument("--receipt-ref", default="")
    item = sub.add_parser("inbox-status")
    item.add_argument("service_id")
    item.add_argument("--now", default="")
    item.add_argument("--stale-minutes", type=int, default=60)
    item = sub.add_parser("asset-create")
    item.add_argument("service_id")
    item.add_argument("--file", required=True)
    item = sub.add_parser("asset-repurpose")
    item.add_argument("service_id")
    item.add_argument("--asset", required=True)
    item.add_argument("--channels", required=True)
    item = sub.add_parser("run")
    item.add_argument("service_id")
    item.add_argument("--action", required=True)
    item.add_argument("--by", required=True)
    item.add_argument("--account", default="default")
    item.add_argument("--text", default="")
    item.add_argument("--target", default="")
    item.add_argument("--channels", default="{}")
    item.add_argument("--metrics", default="")
    item.add_argument("--asset", default="")
    item = sub.add_parser("experiment-create")
    item.add_argument("service_id")
    item.add_argument("--file", required=True)
    item = sub.add_parser("experiment-observe")
    item.add_argument("service_id")
    item.add_argument("--experiment", required=True)
    item.add_argument("--file", required=True)
    item = sub.add_parser("experiment-diagnose")
    item.add_argument("service_id")
    item.add_argument("--experiment", required=True)
    item.add_argument("--secondary-targets", default="{}")
    item = sub.add_parser("plan")
    item.add_argument("service_id")
    item.add_argument("--minutes", type=int, default=0)
    item.add_argument("--channels", default="{}")
    item = sub.add_parser("advise")
    item.add_argument("service_id")
    item = sub.add_parser("brief")
    item.add_argument("service_id")
    item = sub.add_parser("benchmark")
    item.add_argument("--manual", required=True)
    item.add_argument("--assisted", required=True)
    item = sub.add_parser("intake")
    item.add_argument("--repo", required=True)
    item.add_argument("--slug", default="")
    item.add_argument("--read-metrics", action="store_true")
    item.add_argument("--execution-intent", default="live_with_manual_fallback",
                      choices=["preview", "live", "live_with_manual_fallback"])
    item.add_argument("--minutes", type=int, default=120)
    item.add_argument("--out", required=True)
    item = sub.add_parser("observe-live")
    item.add_argument("service_id")
    item.add_argument("--phase", required=True, choices=["baseline", "final"])
    item.add_argument("--repos", required=True)
    item.add_argument("--handle", required=True)
    item.add_argument("--by", required=True)

    item = sub.add_parser("authorize", help="风险分层常驻授权")
    asub = item.add_subparsers(dest="authorize_action", required=True)
    a = asub.add_parser("grant")
    a.add_argument("service_id")
    a.add_argument("--class", dest="action_class", required=True, choices=list(risk_policy.CLASSES))
    a.add_argument("--by", required=True)
    a.add_argument("--quota", type=int, default=None)
    a.add_argument("--days", type=int, default=None)
    a.add_argument("--scope", default="{}")
    a = asub.add_parser("revoke")
    a.add_argument("service_id")
    a.add_argument("--grant", required=True)
    a.add_argument("--by", required=True)
    a = asub.add_parser("list")
    a.add_argument("service_id")

    item = sub.add_parser("policy", help="解释某类动作为什么允许或拒绝")
    psub = item.add_subparsers(dest="policy_action", required=True)
    a = psub.add_parser("explain")
    a.add_argument("service_id")
    a.add_argument("--class", dest="action_class", required=True, choices=list(risk_policy.CLASSES))
    a.add_argument("--scope", default="{}")

    item = sub.add_parser("cycle", help="Observe → Decide → Act → Observe 循环")
    csub = item.add_subparsers(dest="cycle_action", required=True)
    a = csub.add_parser("request")
    a.add_argument("service_id")
    a.add_argument("--trigger", default="manual")
    a.add_argument("--question", default="")
    a.add_argument("--deadline-hours", type=int, default=24)
    a.add_argument("--observations", default="")
    a.add_argument("--actor", default="operator")
    a.add_argument("--cycle-id", default="")
    a = csub.add_parser("propose")
    a.add_argument("service_id")
    a.add_argument("--cycle", required=True)
    a.add_argument("--file", required=True)
    a = csub.add_parser("status")
    a.add_argument("service_id")
    a.add_argument("--cycle", required=True)
    a = csub.add_parser("execute")
    a.add_argument("service_id")
    a.add_argument("--cycle", required=True)
    a.add_argument("--by", required=True)
    a = csub.add_parser("list")
    a.add_argument("service_id")
    a.add_argument("--limit", type=int, default=50)


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _emit(payload, out=""):
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out:
        path = Path(out)
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)


def handle(args, data_dir):
    defaults = {
        "out": "", "evidence": "", "channels": "{}", "text": "", "target": "",
        "metrics": "", "asset": "", "secondary_targets": "{}", "now": "",
        "stale_minutes": 60, "minutes": 0, "note": "",
        "read_metrics": False, "execution_intent": "live_with_manual_fallback",
        "repo": "", "host": "127.0.0.1", "port": 8765, "x": False, "x_action": "publish",
        "account": "default", "platform": "", "topic": "", "subscription": "",
        "watch_topic": "", "pubsub_subscription": "", "interval_seconds": 300,
        "once": False, "max_cycles": None, "webhook_port": 0, "gmail_sync": False, "thread_id": "", "wave": "",
    }
    for name, value in defaults.items():
        if not hasattr(args, name):
            setattr(args, name, value)
    try:
        if args.growth_action == "compile":
            _emit(compile_goal(_read(args.file)), args.out)
        elif args.growth_action == "start":
            _emit(growth_service.start(data_dir, _read(args.file)), args.out)
        elif args.growth_action == "status":
            _emit(growth_service.load(data_dir, args.service_id))
        elif args.growth_action == "attempts":
            _emit({"service_id": args.service_id,
                   "attempts": growth_runtime.attempts(data_dir, args.service_id)})
        elif args.growth_action == "recover":
            _emit(growth_runtime.recover_expired(data_dir))
        elif args.growth_action == "capabilities":
            _emit({"capabilities": execution_capabilities.view(
                data_dir, account=args.account or None, platform=args.platform or None)})
        elif args.growth_action == "capability-upsert":
            _emit(execution_capabilities.upsert(data_dir, _read(args.file), by=args.by))
        elif args.growth_action == "github-sync":
            rows = github_app.GitHubAppClient.from_env().sync_community_interactions(args.repo)
            result = growth_engagement.import_interactions(data_dir, args.service_id, rows)
            result["replayed"] = github_events.replay(data_dir, args.service_id)["processed"]
            _emit(result)
        elif args.growth_action == "github-webhook-serve":
            secret = os.environ.get("MKT_GITHUB_WEBHOOK_SECRET") or ""
            if not secret:
                raise ValueError("github-webhook-serve 需要 MKT_GITHUB_WEBHOOK_SECRET")
            github_events.replay(data_dir, args.service_id)
            github_events.serve(data_dir, args.service_id, secret, host=args.host, port=args.port)
        elif args.growth_action == "gmail-sync":
            _emit(gmail_events.sync(data_dir, args.service_id, account=args.account))
        elif args.growth_action == "gmail-watch-renew":
            _emit(gmail_events.renew_watch(
                data_dir, args.service_id, gmail_api.GmailClient.from_env(),
                args.topic, account=args.account))
        elif args.growth_action == "gmail-pubsub-pull":
            _emit(gmail_events.pull_notifications(
                data_dir, args.service_id, gmail_api.GmailClient.from_env(),
                args.subscription, account=args.account))
        elif args.growth_action == "operator-serve":
            result = growth_worker.serve(
                data_dir, args.service_id, github_repo=args.repo or None,
                gmail_sync=args.gmail_sync, watch_topic=args.watch_topic or None,
                pubsub_subscription=args.pubsub_subscription or None,
                account=args.account, interval_seconds=args.interval_seconds,
                once=args.once, webhook_port=args.webhook_port, max_cycles=args.max_cycles,
                wave_id=args.wave or None, on_cycle=_emit)
        elif args.growth_action == "worker-status":
            _emit(growth_worker.status(data_dir, args.service_id, account=args.account))
        elif args.growth_action == "worker-history":
            rows = growth_worker.history(
                data_dir, args.service_id, account=args.account, limit=args.limit)
            _emit({"service_id": args.service_id, "count": len(rows), "cycles": rows})
        elif args.growth_action == "live-preflight":
            if not args.repo and not args.gmail and not args.x:
                raise ValueError("live-preflight 需要 --repo、--gmail 或 --x")
            _emit(live_acceptance.preflight(
                repo=args.repo or None, gmail=args.gmail,
                x=args.x, x_action=args.x_action, data_dir=data_dir))
        elif args.growth_action == "reconcile":
            _emit(growth_execution.reconcile(
                data_dir, args.service_id, args.action, by=args.by,
                payload={"target": args.target, "text": args.text, "thread_id": args.thread_id,
                         "channels": json.loads(args.channels)}))
        elif args.growth_action == "next":
            candidates = [growth_engagement.next_action(data_dir, args.service_id),
                          growth_opportunities.next_action(data_dir, args.service_id),
                          growth_service.next_action(data_dir, args.service_id)]
            candidates = [row for row in candidates if row]
            row = max(candidates, key=lambda item: item["priority"]) if candidates else None
            _emit(row or {"status": "no_ready_action"})
        elif args.growth_action == "transition":
            evidence = json.loads(args.evidence) if args.evidence else None
            completion = None
            if args.completion_kind:
                completion = {"kind": args.completion_kind}
                if args.receipt:
                    completion["receipt"] = json.loads(args.receipt)
                if args.reason:
                    completion["reason"] = args.reason
            _emit(growth_service.record_action(
                data_dir, args.service_id, args.action, args.status,
                by=args.by, note=args.note, minutes=args.minutes, evidence=evidence,
                completion=completion))
        elif args.growth_action == "evaluate":
            _emit(growth_service.evaluate(
                data_dir, args.service_id, _read(args.metrics), by=args.by))
        elif args.growth_action == "resolve":
            service = growth_service.load(data_dir, args.service_id)
            action = next((row for row in service["actions"] if row["id"] == args.action), None)
            if action is None:
                raise ValueError(f"服务 {args.service_id} 没有动作 {args.action}")
            _emit(action_resolution.resolve(action, {"channels": json.loads(args.channels)}))
        elif args.growth_action == "opportunity-import":
            _emit(growth_opportunities.import_opportunities(
                data_dir, args.service_id, _read(args.file)))
        elif args.growth_action == "inbox-import":
            _emit(growth_engagement.import_interactions(
                data_dir, args.service_id, _read(args.file)))
        elif args.growth_action == "inbox-draft":
            _emit(growth_engagement.draft(
                data_dir, args.service_id, args.conversation, args.text, by=args.by))
        elif args.growth_action == "inbox-terminal":
            _emit(growth_engagement.record_terminal(
                data_dir, args.service_id, args.conversation, args.status,
                by=args.by, reason=args.reason, receipt_ref=args.receipt_ref))
        elif args.growth_action == "inbox-status":
            _emit(growth_engagement.status(
                data_dir, args.service_id, now=args.now or None,
                stale_minutes=args.stale_minutes))
        elif args.growth_action == "asset-create":
            _emit(growth_assets.create(data_dir, args.service_id, _read(args.file)))
        elif args.growth_action == "asset-repurpose":
            channels = [row.strip() for row in args.channels.split(",") if row.strip()]
            _emit(growth_assets.repurpose(
                data_dir, args.service_id, args.asset, channels))
        elif args.growth_action == "run":
            payload = {"text": args.text, "target": args.target,
                       "channels": json.loads(args.channels), "account": args.account}
            if args.metrics:
                payload["metrics"] = _read(args.metrics)
            if args.asset:
                payload["asset"] = _read(args.asset)
            _emit(growth_execution.run(
                data_dir, args.service_id, args.action, by=args.by, payload=payload))
        elif args.growth_action == "experiment-create":
            _emit(growth_experiments.create(data_dir, args.service_id, _read(args.file)))
        elif args.growth_action == "experiment-observe":
            _emit(growth_experiments.observe(
                data_dir, args.service_id, args.experiment, _read(args.file)))
        elif args.growth_action == "experiment-diagnose":
            _emit(growth_experiments.diagnose(
                data_dir, args.service_id, args.experiment,
                secondary_targets=json.loads(args.secondary_targets)))
        elif args.growth_action == "plan":
            _emit(growth_learning.allocate(
                data_dir, args.service_id, human_minutes=args.minutes,
                channels=json.loads(args.channels)))
        elif args.growth_action == "advise":
            _emit(growth_learning.advise(data_dir, args.service_id))
        elif args.growth_action == "brief":
            _emit(growth_learning.brief(data_dir, args.service_id))
        elif args.growth_action == "benchmark":
            _emit(growth_learning.compare_baseline(
                _read(args.manual), _read(args.assisted)))
        elif args.growth_action == "intake":
            report = growth_intake.inspect_repository(args.repo, slug=args.slug)
            if args.read_metrics:
                report["metrics"] = growth_intake.fetch_repo_metadata(args.slug)
            contract = growth_intake.build_goal(
                report, execution_intent=args.execution_intent,
                human_minutes=args.minutes)
            output = Path(args.out)
            with output.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(contract, ensure_ascii=False, indent=2) + "\n")
            _emit({"report": report, "goal": contract, "goal_path": str(output)})
        elif args.growth_action == "observe-live":
            repos = [row.strip() for row in args.repos.split(",") if row.strip()]
            _emit(growth_metrics.observe_live(
                data_dir, args.service_id, args.phase, repos, args.handle, by=args.by))
        elif args.growth_action == "authorize":
            if args.authorize_action == "grant":
                _emit(risk_policy.grant(
                    data_dir, args.service_id, action_class=args.action_class,
                    granted_by=args.by, scope=json.loads(args.scope),
                    quota=args.quota, days=args.days))
            elif args.authorize_action == "revoke":
                _emit(risk_policy.revoke(data_dir, args.service_id, args.grant, by=args.by))
            elif args.authorize_action == "list":
                _emit({"service_id": args.service_id,
                       "grants": risk_policy.list_grants(data_dir, args.service_id)})
            else:
                raise ValueError("未知 authorize 动作")
        elif args.growth_action == "policy":
            _emit(risk_policy.explain(
                data_dir, args.service_id, action_class=args.action_class,
                scope=json.loads(args.scope)))
        elif args.growth_action == "cycle":
            if args.cycle_action == "request":
                observations = _read(args.observations) if args.observations else None
                _emit(growth_cycle.request(
                    data_dir, args.service_id, trigger=args.trigger,
                    observations=observations, question=args.question,
                    deadline_hours=args.deadline_hours, actor=args.actor,
                    cycle_id=args.cycle_id or None))
            elif args.cycle_action == "propose":
                _emit(growth_cycle.propose(
                    data_dir, args.service_id, args.cycle, _read(args.file)))
            elif args.cycle_action == "status":
                _emit(growth_cycle.status(data_dir, args.service_id, args.cycle))
            elif args.cycle_action == "execute":
                _emit(growth_cycle.execute(
                    data_dir, args.service_id, args.cycle, by=args.by))
            elif args.cycle_action == "list":
                _emit({"service_id": args.service_id,
                       "cycles": growth_cycle.list_cycles(
                           data_dir, args.service_id, limit=args.limit)})
            else:
                raise ValueError("未知 cycle 动作")
        else:
            raise ValueError(f"未知 growth 动作 {args.growth_action}")
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"growth error: {exc}", file=sys.stderr)
        return 2
