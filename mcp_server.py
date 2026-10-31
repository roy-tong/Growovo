#!/usr/bin/env python3
"""mkt-server —— 执行内核的 MCP 外壳（05 产品方案 §5.2）。

stdio 传输、JSON-RPC 2.0，零第三方依赖（不装 MCP SDK 也可被 Claude Desktop /
ZCode 等 MCP 客户端连接）。配法（客户端 mcp 配置）：
  {"mkt": {"command": "python3", "args": ["<本仓库绝对路径>/mcp_server.py"]}}

安全设计（把 449 教训做进 API 形状）：
  - 写操作只有两个入口：队列粒度（mkt_run_queue，全渠道）与单条动作（mkt_action，
    全渠道）——两者共用暂停/审批/账本，没有绕过留痕的路径
  - 写操作默认 dry-run；go=true 需显式传
  - UNVERIFIED 渠道（reddit/linkedin/hn/github 写）拒绝实跑，除非 allow_unverified=true
  - 实跑必须绑定已人审 plan（enforce_approval）；无 plan 的 adhoc 放行**只在 CLI
    人工通道（--adhoc）开放**，MCP 不暴露——agent 不得自我授权绕过人审
  - 工具执行期间 stdout 重定向到 stderr，stdout 只留 JSON-RPC（stdio 协议不可污染）
  - 凭据只在服务端进程环境变量，不经工具参数往返
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from channels import REGISTRY  # noqa: E402
from core import transports, workspace  # noqa: E402
from core.ledger import Ledger  # noqa: E402
from core.planlint import PlanNotApproved, enforce_approval, lint_plan  # noqa: E402

DATA = workspace.resolve(base=BASE)
PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "mkt_status",
        "description": "全局账本聚合：按 channel/campaign/result 计数。只读。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mkt_channels",
        "description": "渠道注册表：能力（六原语子集）、验证等级（PROVEN/PORTED/UNVERIFIED）、"
                       "通道矩阵（每个渠道×动作的首选通道与降级链）。只读。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mkt_plan_lint",
        "description": "校验营销计划 plan.json：schema 完整性、渠道能力矩阵、"
                       "波次规模不超内核日预算、失败判据预注册。返回错误清单，空=通过。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {"plan_path": {"type": "string", "description": "plan.json 路径"}},
            "required": ["plan_path"],
        },
    },
    {
        "name": "mkt_search",
        "description": "渠道只读搜索（客群/讨论/对标发现）。channel ∈ x|reddit|linkedin|hn|github。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "enum": ["x", "reddit", "linkedin", "hn", "github"]},
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["channel", "query"],
        },
    },
    {
        "name": "mkt_ledger_query",
        "description": "账本查询：按 channel/campaign/action/target/result 过滤。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "campaign": {"type": "string"},
                "result": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "mkt_inbox",
        "description": "只读收件箱/首响巡检：channel ∈ hn|github|email|x。"
                       "github 需 repo；hn 读 submitted 新回复；email 扫 Gmail；x 扫 vendor inbox。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "enum": ["hn", "github", "email", "x"]},
                "repo": {"type": "string", "description": "github：owner/repo"},
            },
            "required": ["channel"],
        },
    },
    {
        "name": "mkt_audit",
        "description": "GitHub 仓库体检（基础/社区健康/releases/贡献者集中度/AI-readiness/Scorecard）。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}},
            "required": ["repo"],
        },
    },
    {
        "name": "mkt_action",
        "description": "单条写动作（与队列同一套门禁/暂停/审批/账本）。channel ∈ x|reddit|linkedin|github|hn；"
                       "action ∈ publish|reply|message|engage。默认 dry-run；go=true 需已人审 plan；"
                       "UNVERIFIED 渠道实跑需 allow_unverified=true。email 只支持队列。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "enum": ["x", "reddit", "linkedin", "github", "hn"]},
                "action": {"type": "string", "enum": ["publish", "reply", "message", "engage"]},
                "target": {"type": "string"},
                "text": {"type": "string"},
                "title": {"type": "string"},
                "url": {"type": "string"},
                "subreddit": {"type": "string"},
                "number": {"type": "integer"},
                "campaign": {"type": "string"},
                "extra": {"type": "array", "items": {"type": "string"}},
                "go": {"type": "boolean", "default": False},
                "allow_unverified": {"type": "boolean", "default": False},
            },
            "required": ["channel", "action"],
        },
    },
    {
        "name": "mkt_campaign",
        "description": "Campaign 视图：计划/人审状态/队列预检/账本/事件/实验/下一步。只读。"
                       "campaign 形如 <plan_id>-w1。",
        "inputSchema": {
            "type": "object",
            "properties": {"campaign": {"type": "string"}},
            "required": ["campaign"],
        },
    },
    {
        "name": "mkt_run_queue",
        "description": "执行一个队列（全渠道：email/hn/github/reddit/linkedin/x-reply）。"
                       "默认 dry-run；go=true 实跑；UNVERIFIED 渠道实跑需 allow_unverified=true；"
                       "实跑必须绑定已人审 plan（无 plan 的 adhoc 仅 CLI 人工通道，MCP 不开放）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "queue_path": {"type": "string", "description": "data/queues/<campaign>.json"},
                "go": {"type": "boolean", "default": False},
                "allow_unverified": {"type": "boolean", "default": False},
            },
            "required": ["queue_path"],
        },
    },
]


def _text(obj):
    return {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False, indent=1)}]}


def _err(msg):
    return {"content": [{"type": "text", "text": str(msg)}], "isError": True}


def _call_tool(name, args):
    if name == "mkt_status":
        return _text(Ledger(DATA / "ledger.jsonl").summary())

    if name == "mkt_channels":
        out = {}
        for ch, info in REGISTRY.items():
            out[ch] = {
                "status": info["status"], "note": info["note"],
                "actions": sorted(info["actions"]),
                "transport_chains": {
                    a: transports.chain(ch, a) for a in sorted(info["actions"])
                },
            }
        return _text(out)

    if name == "mkt_plan_lint":
        plan = json.loads(Path(args["plan_path"]).read_text(encoding="utf-8"))
        errs = lint_plan(plan)
        return _text({"ok": not errs, "errors": errs})

    if name == "mkt_search":
        ch = args["channel"]
        if ch == "x":
            from channels.x.adapter import run as x_run
            code = x_run("search", [args["query"]], go=True)  # search 只读，--go 仅表执行
            logs = sorted((BASE / "channels" / "x" / "vendor" / "logs").glob("reply-candidates-*.txt"),
                          key=lambda p: p.stat().st_mtime)
            if logs:
                lines = logs[-1].read_text(encoding="utf-8").splitlines()[:50]
                return _text({"done": code == 0, "file": str(logs[-1]), "lines": lines})
            return _text({"done": code == 0, "note": "结果落在 vendor logs/（reply-candidates-*.txt）"})
        if ch == "reddit":
            from channels import reddit
            rows = reddit.search(args["query"], limit=args.get("limit", 10),
                                 out=DATA / "mcp-reddit-search.json")
            return _text({"count": len(rows), "out": str(DATA / "mcp-reddit-search.json")})
        if ch == "linkedin":
            from channels import linkedin
            rows = linkedin.search(args["query"], limit=args.get("limit", 10),
                                   out=DATA / "mcp-linkedin-search.json")
            return _text({"count": len(rows), "out": str(DATA / "mcp-linkedin-search.json")})
        if ch == "hn":
            from channels import hn
            rows = hn.search(args["query"], limit=args.get("limit", 10),
                             out=DATA / "mcp-hn-search.json")
            return _text({"count": len(rows), "rows": rows})
        if ch == "github":
            from channels import github
            rows = github.search(args["query"], limit=args.get("limit", 10),
                                 out=DATA / "mcp-github-search.json")
            return _text({"count": len(rows), "rows": rows})

    if name == "mkt_ledger_query":
        led = Ledger(DATA / "ledger.jsonl")
        recs = led.results(channel=args.get("channel"), campaign=args.get("campaign"),
                           action=args.get("action"), target=args.get("target"))
        recs = [r for r in recs if not args.get("result") or r.get("result") == args.get("result")]
        limit = args.get("limit", 50)
        return _text({"count": len(recs), "records": recs[:limit]})

    if name == "mkt_campaign":
        from core import campaign as camp
        cid = args["campaign"]
        report = camp.collect(DATA, Ledger(DATA / "ledger.jsonl"), cid)
        report["next"] = camp.next_step(DATA, Ledger(DATA / "ledger.jsonl"), cid)
        return _text(report)

    if name == "mkt_run_queue":
        from core.queue_runner import QueueRunError, run_queue
        try:
            results = run_queue(args["queue_path"], data_dir=DATA,
                                go=bool(args.get("go")),
                                allow_unverified=bool(args.get("allow_unverified")))
        except QueueRunError as exc:
            return _err(f"队列拒跑: {exc}")
        tally = {}
        for r in results:
            tally[r.get("result", "?")] = tally.get(r.get("result", "?"), 0) + 1
        return _text({
            "queue": args["queue_path"],
            "dry_run": not args.get("go"),
            "summary": tally,
            "records": [{"id": r.get("ref") or r.get("target"),
                         "target": r.get("target"), "result": r.get("result")}
                        for r in results],
        })

    if name == "mkt_action":
        from core.action_runner import ActionError, run_action
        try:
            rec = run_action(
                channel=args["channel"], action=args["action"], data_dir=DATA,
                campaign=args.get("campaign", ""), go=bool(args.get("go")),
                allow_unverified=bool(args.get("allow_unverified")),
                target=args.get("target"), text=args.get("text"),
                title=args.get("title"), url=args.get("url"),
                subreddit=args.get("subreddit"), number=args.get("number"),
                extra=args.get("extra"))
        except ActionError as exc:
            return _err(f"动作拒跑: {exc}")
        return _text(rec)

    if name == "mkt_inbox":
        ch = args["channel"]
        if ch == "hn":
            from channels import hn
            return _text({"rows": hn.inbox()})
        if ch == "github":
            if not args.get("repo"):
                return _err("github inbox 需要 repo")
            from channels import github
            return _text({"rows": github.inbox(args["repo"])})
        if ch == "email":
            from channels import email_cdp
            rows = email_cdp.scan()
            return _text({"rows": rows if isinstance(rows, list) else [],
                          "note": "结果同时打印在服务端"})
        if ch == "x":
            from channels.x.adapter import run as x_run
            code = x_run("inbox", [], go=True)
            return _text({"done": code == 0, "note": "vendor 扫描结果见服务端日志"})

    if name == "mkt_audit":
        import contextlib
        import io
        from channels import github
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            github.audit(args["repo"])
        return _text({"repo": args["repo"], "markdown": buf.getvalue()})

    return _err(f"未知工具: {name}")


def call_tool(name, args):
    """所有工具输出走 stderr，stdout 只留给 JSON-RPC（stdio 协议不可污染）。"""
    import contextlib
    import sys
    with contextlib.redirect_stdout(sys.stderr):
        return _call_tool(name, args)


def _tally(records):
    out = {}
    for r in records:
        out[r["result"]] = out.get(r["result"], 0) + 1
    return out


def handle(msg):
    method = msg.get("method", "")
    mid = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mkt-server", "version": "0.1.0"},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        try:
            res = call_tool(msg["params"]["name"], msg["params"].get("arguments", {}))
            return {"jsonrpc": "2.0", "id": mid, "result": res}
        except PlanNotApproved as exc:
            return {"jsonrpc": "2.0", "id": mid,
                    "result": _err(f"人审门禁: {exc}")}
        except (Exception, SystemExit) as exc:
            return {"jsonrpc": "2.0", "id": mid,
                    "result": _err(f"{type(exc).__name__}: {exc}")}
    if mid is not None:  # 未知请求；notification 忽略
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
