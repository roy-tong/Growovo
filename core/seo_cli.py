"""CLI surface for the read-only SEO audit."""
from __future__ import annotations

import json
from pathlib import Path

from . import geo_entity, seo_audit


def register(parser):
    seo = parser.add_parser("seo", help="SEO：技术可发现性审计与索引状态")
    sub = seo.add_subparsers(dest="seo_action", required=True)
    item = sub.add_parser("audit", help="只读技术 SEO 审计")
    item.add_argument("--url", required=True)
    item.add_argument("--repo", default="")
    item.add_argument("--sample", type=int, default=10)
    item.add_argument("--timeout", type=int, default=10)
    item.add_argument("--out", default="")
    item.add_argument("--json", action="store_true")
    item = sub.add_parser("index-status", help="索引状态；无 GSC 凭据时保持 unknown")
    item.add_argument("--site", required=True)
    item.add_argument("--json", action="store_true")
    item = sub.add_parser("report", help="审计并输出 Markdown")
    item.add_argument("--url", required=True)
    item.add_argument("--sample", type=int, default=10)
    item.add_argument("--out", default="")
    item.add_argument("--json", action="store_true")
    return seo


def handle(args, data_dir):
    action = args.seo_action
    as_json = bool(getattr(args, "json", False))
    if action == "index-status":
        result = {"site": args.site, "status": "unknown",
                  "reason": "gsc_not_configured",
                  "note": "没有 GSC 凭据时不猜测索引状态"}
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    entity = geo_entity.load(data_dir, required=False)
    if action in ("audit", "report"):
        result = seo_audit.audit(args.url, entity=entity, sample=args.sample,
                                 timeout=args.timeout)
        path = seo_audit.save(data_dir, result)
        if action == "report" or not as_json:
            text = seo_audit.render(result)
            if args.out:
                Path(args.out).write_text(text + "\n", encoding="utf-8")
            print(text)
            return 0
        result["path"] = str(path)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    raise ValueError("未知 seo 子命令")
