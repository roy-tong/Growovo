"""Deterministic, rebuildable read projections over domain facts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from .storage import AppendOnlyJSONL


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def correlation_report(data_dir, context) -> Dict[str, Any]:
    data_dir = Path(data_dir)
    operations = AppendOnlyJSONL(data_dir / "operations.jsonl").all()
    from .ledger import Ledger
    ledger = Ledger(data_dir / "ledger.jsonl").all()
    contributions_path = data_dir / "state" / "contributions.jsonl"
    contributions = AppendOnlyJSONL(contributions_path).all()
    ops = [row for row in operations if row.get("attempt_id") == context.attempt_id]
    led = [row for row in ledger if row.get("campaign") == context.campaign]
    linked = [row for row in led if row.get("attempt_id") == context.attempt_id
              or row.get("run_id") == context.run_id]
    cons = [row for row in contributions if row.get("attempt_id") == context.attempt_id
            or row.get("run_id") == context.run_id]
    return {"schema_version": "growovo.run-correlation/1", "correlation": context.correlation(),
            "counts": {"operations": len(ops), "ledger": len(led), "linked_ledger": len(linked),
                       "legacy_unlinked": len(led) - len(linked), "contributions": len(cons)},
            "action_ids": sorted({str(row.get("action_id")) for row in ops if row.get("action_id")}),
            "note": "legacy ids are not fabricated; missing correlation remains visible"}


class ProjectionRegistry:
    @staticmethod
    def build(name: str, data_dir, **kwargs) -> Dict[str, Any]:
        data_dir = Path(data_dir)
        if name == "wave":
            from . import cycle_report
            body = cycle_report.build_report(data_dir, night=str(kwargs.get("night") or ""),
                                             scheduled_only=bool(kwargs.get("scheduled_only", False)))
        elif name == "inbox":
            from . import inbox_tracking
            body = inbox_tracking.status_report(data_dir, stale_minutes=int(kwargs.get("stale_minutes", 60)))
        elif name == "receipts":
            from . import receipt_audit
            body = receipt_audit.audit(data_dir, night=str(kwargs["night"]),
                                       scheduled_only=bool(kwargs.get("scheduled_only", False)),
                                       live=bool(kwargs.get("live", False)))
        elif name == "readiness":
            from . import engagement_cycle, supply_readiness
            body = supply_readiness.assess(data_dir, engagement_cycle.load_policy(data_dir),
                                           now=kwargs.get("now"), waves=int(kwargs.get("waves", 3)))
        elif name == "koc":
            from . import koc_qualification
            body = koc_qualification.report(data_dir)
        elif name == "business":
            from . import business_observations
            body = business_observations.compare(data_dir, str(kwargs["night"]))
        else:
            raise ValueError("未知投影：%s" % name)
        return {"schema_version": "growovo.projection/1", "name": name,
                "projection_sha256": hashlib.sha256(canonical(body)).hexdigest(), "body": body}
