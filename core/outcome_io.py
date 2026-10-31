#!/usr/bin/env python3
"""记账自动化 —— 把"账本/CSV → 事件台账"的人工搬运变成一条命令。

两块：
1. import_csv：把任何带 subject + event_type 的 CSV（Stripe/PostHog/Plausible
   导出、表单、手工表）导成事件；幂等（import_key 去重），逐行报错不中断。
2. sync_ledger：把某个 campaign 的账本聚合写成 count 快照（touches_committed /
   touches_dry_run）。这是"发出去了多少"的机器口径，不伪装成漏斗事件——
   触达数不是转化，也不该占用 signal 槽位。

纪律沿用 attribution：activation/paid 必须有 source_ref 与核验依据；近似口径
必须显式 approximated=true；导入只记账，不做因果推断。
"""
import csv
import hashlib
import io
from pathlib import Path

from .attribution import FUNNEL, OutcomeLedger

ALIASES = {
    "subject": ("subject", "email", "user", "user_id", "who", "id",
                "distinct_id", "$distinct_id", "person_id"),
    "event_type": ("event_type", "type", "event", "event_name", "stage"),
    "channel": ("channel", "source", "utm_source"),
    "touch": ("touch", "content", "utm_content"),
    "ts": ("ts", "time", "timestamp", "date", "created_at"),
    "source_ref": ("source_ref", "ref", "reference", "note"),
    "approximated": ("approximated", "approx", "is_approx"),
    "verify_method": ("verify_method", "method", "verification_method"),
    "verify_ref": ("verify_ref", "verification_ref", "proof", "receipt"),
    "campaign": ("campaign", "utm_campaign"),
}


def _norm_row(row):
    low = {}
    for k, v in (row or {}).items():
        if k is None:
            continue
        key = str(k).strip().lower().replace("-", "_").replace(" ", "_")
        low[key] = (v or "").strip() if isinstance(v, str) else v
    out = {}
    for field, names in ALIASES.items():
        for n in names:
            if low.get(n) not in (None, ""):
                out[field] = low[n]
                break
        else:
            out[field] = ""
    return out


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "y", "是", "近似")


def _key(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def import_csv(data_dir, path, *, campaign="", dry_run=False, event_map=None):
    """导入事件 CSV。返回 {total, imported, skipped, errors, dry_run}。

    event_map：原始事件名 → 漏斗阶段，如 {"user_signed_up": "signup"}；
    未命中的事件名原样校验（必须是 signal/signup/activation/paid）。
    """
    event_map = {str(k).strip().lower(): str(v).strip().lower()
                 for k, v in (event_map or {}).items()}
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    ledger = OutcomeLedger(data_dir)
    existing = {r.get("import_key") for r in ledger.records() if r.get("import_key")}

    result = {"total": 0, "imported": 0, "skipped": 0, "errors": [],
              "dry_run": bool(dry_run)}
    for i, raw in enumerate(reader, 2):  # 2 = 表头后第一行
        result["total"] += 1
        row = _norm_row(raw)
        subject = str(row.get("subject") or "").strip()
        etype = str(row.get("event_type") or "").strip().lower()
        etype = event_map.get(etype, etype)
        if not subject or not etype:
            result["errors"].append({"row": i, "error": "缺 subject 或 event_type"})
            continue
        if etype not in FUNNEL:
            result["errors"].append({"row": i, "error": f"event_type 只允许 {FUNNEL}（收到 {etype!r}）"})
            continue
        rc = str(row.get("campaign") or campaign or "").strip()
        ts = str(row.get("ts") or "").strip()
        if ts and "T" not in ts:
            ts = f"{ts}T00:00:00"
        key = _key(rc, subject, etype, ts, row.get("source_ref"))
        if key in existing:
            result["skipped"] += 1
            continue
        verification = None
        if row.get("verify_method") and row.get("verify_ref"):
            verification = {"method": row["verify_method"], "ref": row["verify_ref"]}
        try:
            if not dry_run:
                ledger.add_event(
                    subject=subject, event_type=etype,
                    channel=str(row.get("channel") or ""),
                    touch=str(row.get("touch") or ""),
                    source_ref=str(row.get("source_ref") or ""),
                    approximated=_truthy(row.get("approximated")),
                    verification=verification, campaign=rc, import_key=key,
                    ts=ts or None,
                )
            existing.add(key)
            result["imported"] += 1
        except Exception as exc:  # 逐行报错，不中断整批
            result["errors"].append({"row": i, "error": str(exc)})
    return result


def sync_ledger(data_dir, ledger, campaign, *, dry_run=False):
    """把账本聚合成 count 快照（按渠道 × committed/dry-run）。幂等。"""
    recs = ledger.results(campaign=campaign)
    agg = {}
    for r in recs:
        result = r.get("result")
        if result not in ("committed", "dry-run"):
            continue
        key = (r.get("channel", "?"), result)
        agg[key] = agg.get(key, 0) + 1

    ol = OutcomeLedger(data_dir)
    existing = {(r.get("campaign"), r.get("metric"), r.get("window"))
                for r in ol.records() if r.get("kind") == "count"}
    created, skipped = [], []
    for (channel, result), n in sorted(agg.items()):
        metric = "touches_committed" if result == "committed" else "touches_dry_run"
        window = f"campaign:{campaign}"
        if (campaign, metric, window) in existing:
            skipped.append({"channel": channel, "metric": metric, "value": n})
            continue
        if not dry_run:
            ol.add_count(metric=metric, value=n, window=window,
                         source_ref="ledger.jsonl", approximated=False,
                         campaign=campaign,
                         import_key=_key(campaign, metric, channel, n))
        created.append({"channel": channel, "metric": metric, "value": n})
    return {"campaign": campaign, "created": created, "skipped": skipped,
            "dry_run": bool(dry_run), "records": len(recs)}
