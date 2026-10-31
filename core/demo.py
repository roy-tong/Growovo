#!/usr/bin/env python3
"""零网络零账号演示（W1 清单 B3）——陌生人 30 秒看懂内核纪律。

不动真实 data/：全部产物写进 data/demo/ 沙盒，可整目录删除。
四步即产品主张的最小闭环（计划是数据，人审是闸门）：
  1. 计划校验   plan 过 schema/能力矩阵/日预算 lint
  2. 人审闸门   human_approved=false 时同 campaign 拒跑；模拟人审后放行
  3. 写路径事务 门禁 → FILL → 回读校验 → dry-run 停点（send 永不触发）
  4. 账本       三条终态留痕：dry-run / rejected / failed
"""
import json
from datetime import date, datetime
from pathlib import Path

from .gates import Gate
from .ledger import Ledger
from .pipeline import WritePipeline
from .planlint import PlanNotApproved, enforce_approval, lint_plan
from .safety import pause_file
from .throttle import Throttle

CAMPAIGN = "demo-p0-w1"

# 队列三项分别演示三种终态：正常干跑 / 占位符残留被门禁拒 / 回读丢字拒绝发送（449 同族）
DEMO_ITEMS = [
    {"id": "d-001", "target": "sam@example.com",
     "body": "Sam，看到你在维护 mcp-server-template。工具调用的计量口径我们在三个项目里"
             "踩过同样的坑，整理了一份对照笔记想请你看看，也欢迎你试用 demo 版直接反驳我们。"},
    {"id": "d-002", "target": "alex@example.com",
     "body": "你好 {{NAME}}，这是一封没做完千人千面的群发草稿。"},
    {"id": "d-003", "target": "jordan@example.com",
     "body": "Jordan，你上周在 HN 上问的口径一致性问题，我们做了一版跨系统对照实验，"
             "三个系统的计量差异最大到 11%，数据和方法都放在附录里，欢迎挑刺。"},
]
CORRUPT_IDS = {"d-003"}  # 模拟编辑器丢字：回读比期望少 12 字符


def _demo_plan():
    return {
        "plan_id": "demo-p0",
        "goal": "为 demo-product 拿到 20 个开发者试用反馈",
        "created": date.today().isoformat(),
        "hypothesis": "独立 agent 开发者有计量口径痛点，求评式邮件可换来真实反馈",
        "icp": {"org": "独立开发者/小团队", "persona": "兼管增长的开发者",
                "beachhead": "MCP 工具作者"},
        "audience": {"spec": "github:MCP servers 目录维护者", "target_count": 3,
                     "evidence_grade": "B"},
        "waves": [
            {"wave": 1, "channel": "email", "action": "message", "size": 3,
             "content_policy": "求评式/千人千面/禁模板",
             "queue": "data/queues/demo-p0-w1.json"},
        ],
        "success_criteria": {"metric": "reply_rate", "target": "≥10%",
                             "stop": "退信率>5% 即停"},
        "review": {"human_approved": False, "approved_at": None},
    }


def run_demo(base):
    """base = 仓库根。返回 0 = 四步全部按预期；1 = 有一步偏离（演示即自检）。"""
    base = Path(base)
    data = base / "data" / "demo"
    (data / "plans").mkdir(parents=True, exist_ok=True)
    (data / "queues").mkdir(parents=True, exist_ok=True)
    plan_file = data / "plans" / "demo-p0.json"

    print(f"mkt demo —— 零网络零账号，沙盒 {data.relative_to(base)}（可随时整目录删除）\n")

    # ── 1. 计划校验 ─────────────────────────────────────────────
    plan = _demo_plan()
    errs = lint_plan(plan)
    if errs:
        print(f"[FAIL] 计划校验未过: {errs}")
        return 1
    plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    (data / "queues" / f"{CAMPAIGN}.json").write_text(json.dumps(
        {"campaign": CAMPAIGN, "channel": "email", "action": "message",
         "items": DEMO_ITEMS}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[1/4] 计划校验   PASS — schema/beachhead/能力矩阵/size≤内核日预算（email/message 日预算 100，本计划 3）")

    # ── 2. 人审闸门 ─────────────────────────────────────────────
    try:
        enforce_approval(data, CAMPAIGN)
        print("[FAIL] 人审闸门未拦截未经审批的计划")
        return 1
    except PlanNotApproved as exc:
        print(f"[2/4] 人审闸门   拦截 — {exc}")
    plan["review"] = {"human_approved": True,
                      "approved_at": datetime.now().isoformat(timespec="seconds")}
    plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    from .approval import approve_plan
    approve_plan(data, "demo-p0", by="demo-human", note="demo：模拟一次带署名的审批")
    enforce_approval(data, CAMPAIGN)  # 模拟人审通过后放行
    print("      模拟人审（mkt plan approve：署名 + 计划指纹 + 审批事件）→ 放行")

    # ── 3. 写路径事务（dry-run，send 永不触发）──────────────────
    ledger = Ledger(data / "ledger.jsonl")
    pipe = WritePipeline(gate=Gate(ledger=ledger, suppress=set()),
                         ledger=ledger, throttle=Throttle(data / "state"),
                         pause_file=pause_file(data))
    box = {}

    def compose(item):
        box.clear()

    def fill(item):
        box["text"] = item.get("body", "")

    def readback(item):
        got = box.get("text", "")
        if item["id"] in CORRUPT_IDS and len(got) > 12:
            return got[:-12]  # 模拟编辑器丢字：回读与期望不一致
        return got

    def send(item):
        raise RuntimeError("demo 渠道无真实发送；dry-run 不应走到 SEND")

    def confirm(item):
        return False

    explain = {
        "dry-run": "干跑停点：门禁与回读校验都过，未发送已留痕（--go 才实跑）",
        "rejected": "门禁拒绝：占位符残留 = 千人千面没做完",
        "failed": "回读不一致：宁可失败绝不发送（449 空正文事故的固化）",
    }
    print("[3/4] 写路径事务 三条队列项（dry-run）:")
    for it in DEMO_ITEMS:
        rec = pipe.run_one(it, channel="email", action="message", campaign=CAMPAIGN,
                           transport="demo-memory", target=it["target"],
                           expect=it["body"], compose=compose, fill=fill,
                           readback=readback, send=send, confirm=confirm,
                           dry_run=True)
        print(f"      {it['id']} → {rec['result']:<8} {explain.get(rec['result'], rec.get('detail', ''))}")

    # ── 4. 账本 ─────────────────────────────────────────────────
    final = {r["ref"]: r["result"] for r in ledger.results(campaign=CAMPAIGN)}
    expected = {"d-001": "dry-run", "d-002": "rejected", "d-003": "failed"}
    ok = final == expected
    print(f"[4/4] 账本       {data / 'ledger.jsonl'} — {'三条终态与预期一致' if ok else f'终态偏离预期: {final}'}")

    print("\n下一步（真实使用）：写 data/plans/<plan_id>.json → lint → 人审 → "
          "mkt email message --queue data/queues/<campaign>.json（默认 dry-run，--go 才实跑）")
    return 0 if ok else 1
