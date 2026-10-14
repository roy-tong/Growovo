#!/usr/bin/env python3
"""mkt-server —— 执行内核的 MCP 外壳（05 产品方案 §5.2）。

stdio 传输、JSON-RPC 2.0，零第三方依赖（不装 MCP SDK 也可被 Claude Desktop /
ZCode 等 MCP 客户端连接）。配法（客户端 mcp 配置）：
  {"mkt": {"command": "python3", "args": ["<本仓库绝对路径>/mcp_server.py"]}}

安全设计（把 449 教训做进 API 形状）：
  - 只暴露队列粒度的写操作（mkt_run_queue）——没有绕过留痕的细粒度"发一条"
  - 写操作默认 dry-run；go=true 需显式传
  - UNVERIFIED 渠道（reddit/linkedin）拒绝实跑，除非 allow_unverified=true
  - campaign 属于某个 plan 且未经人审 → 拒跑（core.planlint.enforce_approval）
  - 凭据只在服务端进程环境变量，不经工具参数往返
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from channels import REGISTRY  # noqa: E402
from core import transports  # noqa: E402
from core.ledger import Ledger  # noqa: E402
from core.planlint import PlanNotApproved, enforce_approval, lint_plan  # noqa: E402

DATA = BASE / "data"
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
        "description": "渠道只读搜索（客群发现/讨论发现）。channel ∈ x|reddit|linkedin|hn。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "enum": ["x", "reddit", "linkedin", "hn"]},
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
        "name": "mkt_run_queue",
        "description": "执行一个队列（当前支持 email SMTP 通道；其他渠道请用 CLI）。"
                       "默认 dry-run；go=true 实跑；UNVERIFIED 渠道实跑需 allow_unverified=true；"
                       "campaign 属于未经人审的计划时拒跑。",
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


def call_tool(name, args):
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
            return _text({"count": len(rows), "out": str(DATA / "mcp-hn-search.json")})

    if name == "mkt_ledger_query":
        led = Ledger(DATA / "ledger.jsonl")
        recs = led.results(channel=args.get("channel"), campaign=args.get("campaign"),
                           action=args.get("action"), target=args.get("target"))
        recs = [r for r in recs if not args.get("result") or r.get("result") == args.get("result")]
        limit = args.get("limit", 50)
        return _text({"count": len(recs), "records": recs[:limit]})

    if name == "mkt_run_queue":
        qpath = Path(args["queue_path"])
        q = json.loads(qpath.read_text(encoding="utf-8"))
        channel = q.get("channel", "email")
        go = bool(args.get("go"))
        if go:
            status = REGISTRY.get(channel, {}).get("status")
            if status == "UNVERIFIED" and not args.get("allow_unverified"):
                return _err(f"{channel} 渠道为 UNVERIFIED，拒绝实跑（需人工首跑校准后再启用）")
        if channel != "email":
            return _err(f"MCP 通道当前只支持 email 队列，{channel} 请用 CLI（python3 mkt.py）")
        campaign = q.get("campaign", qpath.stem)
        if go:
            from core.safety import is_paused
            if is_paused(DATA):
                return _err("全局暂停中（mkt pause）——拒绝实跑")
            enforce_approval(DATA, campaign)  # PlanNotApproved 直接报给调用方
        from channels.email_smtp import send_queue
        res = send_queue(qpath, go=go, data_dir=DATA)
        return _text({
            "campaign": campaign, "dry_run": not go,
            "summary": _tally(res),
            "records": [{"target": r["target"], "result": r["result"]} for r in res],
        })

    return _err(f"未知工具: {name}")


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
        except Exception as exc:
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
