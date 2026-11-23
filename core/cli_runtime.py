"""Modern runtime/outreach parser and handlers outside the legacy CLI module."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .command_registry import CommandRegistry, CommandSpec


def _emit(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


def cmd_runtime(data_dir, args):
    data_dir = Path(data_dir)
    if args.action == "project":
        from .projections import ProjectionRegistry
        got = ProjectionRegistry.build(args.name, data_dir, night=str(getattr(args, "night", "") or ""),
                                       scheduled_only=bool(getattr(args, "scheduled_only", False)))
    else:
        from .runtime import RunContext
        context = RunContext.create(data_dir, args.campaign, args.wave,
                                    now=str(getattr(args, "now", "") or "") or None,
                                    metadata={"source": "runtime-cli"})
        got = {"ok": True, "correlation": context.correlation(), "created_at": context.created_at,
               "writes": 0, "external_sends": 0}
    _emit(got)
    return 0


def cmd_outreach(data_dir, args, loader):
    payload = loader(args.file)
    if args.action == "prepare":
        from .content_pipeline import prepare_draft
        got = prepare_draft(payload, today=str(getattr(args, "today", "") or dt.date.today().isoformat()))
    else:
        from .contribution import ContributionStore
        store = ContributionStore(data_dir)
        apply = bool(getattr(args, "apply", False))
        if payload.get("contribution_id"):
            got = store.transition(
                str(payload["contribution_id"]), str(payload.get("status") or ""),
                by=str(payload.get("by") or ""), note=str(payload.get("note") or ""),
                change_ref=str(payload.get("change_ref") or ""),
                reviewer=str(payload.get("reviewer") or ""),
                credit_ref=str(payload.get("credit_ref") or ""),
                followup_ref=str(payload.get("followup_ref") or ""), apply=apply)
        else:
            got = store.record(str(payload.get("interaction_id") or ""),
                               kind=str(payload.get("kind") or ""),
                               by=str(payload.get("by") or ""), apply=apply)
    _emit(got)
    return 0 if got.get("ok") else 2


def cmd_validation(data_dir, args):
    from .validation_orchestrator import run
    got = run(args.file)
    _emit(got)
    return 0 if got["contract_validation"]["status"] == "PASS" else 2


def _configure_runtime(parser):
    sub = parser.add_subparsers(dest="action", required=True)
    project = sub.add_parser("project")
    project.add_argument("name", choices=["wave", "inbox", "receipts", "readiness", "koc", "business"])
    project.add_argument("--night", default="")
    project.add_argument("--scheduled-only", action="store_true")
    context = sub.add_parser("context")
    context.add_argument("--campaign", required=True)
    context.add_argument("--wave", required=True)
    context.add_argument("--now", default="")


def _configure_outreach(parser):
    parser.add_argument("action", choices=["prepare", "feedback"])
    parser.add_argument("--file", required=True)
    parser.add_argument("--today", default="")
    parser.add_argument("--apply", action="store_true")


def _configure_validation(parser):
    parser.add_argument("action", choices=["run"])
    parser.add_argument("--file", required=True)


def register(subparsers, *, runtime_handler, outreach_handler, validation_handler=None):
    registry = CommandRegistry()
    registry.register(CommandSpec(
        name="runtime", module="", attribute="", configure=_configure_runtime,
        _handler=runtime_handler))
    registry.register(CommandSpec(
        name="outreach", module="", attribute="", configure=_configure_outreach,
        _handler=outreach_handler))
    if validation_handler is not None:
        registry.register(CommandSpec(
            name="validation", module="", attribute="", configure=_configure_validation,
            _handler=validation_handler))
    registry.attach(subparsers)
