#!/usr/bin/env python3
"""零网络零账号演示（W1 清单 B3）——陌生人 30 秒看懂内核纪律。

不动真实数据：默认写 data/demo/；显式工作区用其 demo/ 子目录。每次运行前重建沙盒。
五步即产品主张的最小闭环（计划是数据，人审是闸门，成功与拦截都要可核验）：
  1. 计划校验   plan 过 schema/能力矩阵/日预算 lint
  2. 人审闸门   human_approved=false 时同 campaign 拒跑；模拟人审后放行
  3. 成功投递   队列项过门禁 → 回读一致 → 离线发送（进程内存）→ 账本 committed
                → 补 L1 凭证（不伪造 L2/L3）
  4. 三道拦截   dry-run 停点 / rejected（占位符残留）/ failed（回读不一致）
  5. 账本对账   四条终态与预期一致；日结果卡上的 committed 与三层结果可回查

发送回调只往进程内 list 追加，不导入网络模块、不读账号、不写 profiles/；
账本 transport 固定为 demo-memory，dry-run 路径的 send 永远抛错。
"""
import json
import shutil
from datetime import date, datetime
from pathlib import Path

from .gates import Gate
from .ledger import Ledger
from .pipeline import WritePipeline
from .planlint import PlanNotApproved, enforce_approval, lint_plan
from .safety import pause_file
from .throttle import Throttle

CAMPAIGN = "demo-p0-w1"
RUN_ID = "demo-p0"
SUCCESS_ID = "d-000"

# 四项分别演示成功与三种拦截；含占位符的坏样本不进入获批队列。
DEMO_ITEMS = [
    {"id": SUCCESS_ID, "target": "robin@example.com",
     "body": "Robin，看到你在维护 mcp-server-template。工具调用的计量口径我们在三个项目里"
             "踩过同样的坑，整理了一份对照笔记想请你看看；如果方向不对，直接回信反驳就行。"},
    {"id": "d-001", "target": "sam@example.com",
     "body": "Sam，看到你在维护 mcp-server-template。工具调用的计量口径我们在三个项目里"
             "踩过同样的坑，整理了一份对照笔记想请你看看，也欢迎你试用 demo 版直接反驳我们。"},
    {"id": "d-002", "target": "alex@example.com",
     "body": "你好 {{NAME}}，这是一封没做完千人千面的群发草稿。"},
    {"id": "d-003", "target": "jordan@example.com",
     "body": "Jordan，你上周在 HN 上问的口径一致性问题，我们做了一版跨系统对照实验，"
             "三个系统的计量差异最大到 11%，数据和方法都放在附录里，欢迎挑刺。"},
]
APPROVED_DEMO_ITEMS = [item for item in DEMO_ITEMS if item["id"] != "d-002"]
CORRUPT_IDS = {"d-003"}  # 模拟编辑器丢字：回读比期望少 12 字符
EXPECTED_RESULTS = {
    SUCCESS_ID: "committed",
    "d-001": "dry-run",
    "d-002": "rejected",
    "d-003": "failed",
}


def _demo_plan():
    return {
        "plan_id": "demo-p0",
        "goal": "为 demo-product 拿到 20 个开发者试用反馈",
        "created": date.today().isoformat(),
        "hypothesis": "独立 agent 开发者有计量口径痛点，求评式邮件可换来真实反馈",
        "icp": {"org": "独立开发者/小团队", "persona": "兼管增长的开发者",
                "beachhead": "MCP 工具作者"},
        "audience": {"spec": "github:MCP servers 目录维护者",
                     "target_count": len(APPROVED_DEMO_ITEMS), "evidence_grade": "B"},
        "waves": [
            {"wave": 1, "channel": "email", "action": "message",
             "size": len(APPROVED_DEMO_ITEMS),
             "content_policy": "求评式/千人千面/禁模板",
             "queue": f"data/queues/{CAMPAIGN}.json"},
        ],
        "success_criteria": {"metric": "reply_rate", "target": "≥10%",
                             "stop": "退信率>5% 即停"},
        "review": {"human_approved": False, "approved_at": None},
    }


def _reset_sandbox(data):
    """每次重建 data/demo：committed 会让门禁挡住重跑，幂等靠干净重建保证。"""
    if data.exists():
        shutil.rmtree(data)
    (data / "plans").mkdir(parents=True)
    (data / "queues").mkdir(parents=True)


def _write_success_run(data, on, results):
    """写一份最小 v2 运行记录，供生产代码 GrowRunV2.daily 生成日结果卡。

    只填演示需要的字段；goal 指向计划里的回复率，但没有回复读数通道，
    所以日卡会如实显示「取不到」，不填 0。护栏计数由本轮账本终态汇总。
    """
    plan = json.loads((data / "plans" / "demo-p0.json").read_text(encoding="utf-8"))
    queue = json.loads((data / "queues" / f"{CAMPAIGN}.json").read_text(encoding="utf-8"))
    run_data = {
        "schema": "growovo.grow-run/2",
        "id": RUN_ID,
        "started": on,
        "status": "armed",
        "go": False,
        "goal": {"metric": "reply_rate", "name": plan["goal"],
                 "target_text": "回复率 " + plan["success_criteria"]["target"],
                 "baseline": None, "target": None},
        "quota": {"actions_per_week": len(queue["items"]), "action_weights": {}},
        "supply": {"min_cover_days": 3},
        "telemetry": {},
        "decisions": [],
        "learning": {},
        "automation": {},
    }
    from .grow_v2 import GrowRunV2
    counts = {}
    for result in results.values():
        counts[result] = counts.get(result, 0) + 1
    GrowRunV2._record_day_telemetry(
        run_data, on, action="message",
        published=counts.get("committed", 0), duplicates=0, suppressed=0,
        failed=counts.get("failed", 0), indeterminate=0,
        rejected=counts.get("rejected", 0))
    run_path = data / "grow" / f"{RUN_ID}.json"
    run = GrowRunV2(run_path)
    run.save(run_data)
    ledger = Ledger(data / "ledger.jsonl")
    committed_targets = run._committed_targets(ledger, run_data)
    run_data["supply_state"] = run._supply_state(
        run_data, data, on, committed_targets)
    run.save(run_data)
    return run


def run_demo(base, *, data_dir=None):
    """base = 仓库根；显式 data_dir 只用于已选择的独立 demo 沙盒。"""
    base = Path(base)
    data = Path(data_dir) if data_dir is not None else base / "data" / "demo"
    _reset_sandbox(data)
    plan_file = data / "plans" / "demo-p0.json"
    on = date.today().isoformat()

    print(f"mkt demo —— 零网络零账号，沙盒 {data.relative_to(base)}"
          "（每次运行重建，可随时整目录删除）\n")

    # ── 1. 计划校验 ─────────────────────────────────────────────
    plan = _demo_plan()
    errs = lint_plan(plan)
    if errs:
        print(f"[FAIL] 计划校验未过: {errs}")
        return 1
    plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    (data / "queues" / f"{CAMPAIGN}.json").write_text(json.dumps(
        {"campaign": CAMPAIGN, "channel": "email", "action": "message",
         "items": APPROVED_DEMO_ITEMS}, ensure_ascii=False, indent=1), encoding="utf-8")
    size = sum(int(w["size"]) for w in plan["waves"])
    daily_cap = Throttle(data / "state").policy("email", "message")[3]
    print(f"[1/5] 计划校验   PASS — schema/beachhead/能力矩阵/"
          f"size≤内核日预算（email/message 日预算 {daily_cap}，本计划 {size}）")

    # ── 2. 人审闸门 ─────────────────────────────────────────────
    try:
        enforce_approval(data, CAMPAIGN)
        print("[FAIL] 人审闸门未拦截未经审批的计划")
        return 1
    except PlanNotApproved as exc:
        print(f"[2/5] 人审闸门   拦截 — {exc}")
    plan["review"] = {"human_approved": True,
                      "approved_at": datetime.now().isoformat(timespec="seconds")}
    plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    from .approval import approve_plan
    approve_plan(data, "demo-p0", by="demo-human", note="demo：模拟一次带署名的审批")
    enforce_approval(data, CAMPAIGN)  # 模拟人审通过后放行
    print("      模拟人审（mkt plan approve：署名 + 计划指纹 + 审批事件）→ 放行")

    # ── 3. 成功投递（离线发送，只写进程内内存）──────────────────
    ledger = Ledger(data / "ledger.jsonl")
    pipe = WritePipeline(
        gate=Gate(ledger=ledger, suppress=set()),
        ledger=ledger, throttle=Throttle(data / "state"),
        pause_file=pause_file(data),
        approval=lambda campaign: enforce_approval(data, campaign))
    sent = []
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

    def send_offline(item):
        """离线模拟发送：只记进程内 list，不联网、不碰账号。"""
        sent.append(item["id"])

    def send_blocked(item):
        raise RuntimeError("demo 渠道无真实发送；dry-run 不应走到 SEND")

    def confirm(item):
        return item["id"] in sent

    success = next(item for item in DEMO_ITEMS if item["id"] == SUCCESS_ID)
    rec = pipe.run_one(success, channel="email", action="message", campaign=CAMPAIGN,
                       transport="demo-memory", target=success["target"],
                       expect=success["body"], compose=compose, fill=fill,
                       readback=readback, send=send_offline, confirm=confirm,
                       dry_run=False)
    if rec["result"] != "committed" or sent != [SUCCESS_ID]:
        print(f"[FAIL] 成功路径未提交: {rec.get('result')} / sent={sent}"
              f" / detail={rec.get('detail', '')}")
        return 1
    from . import outcome_layers
    synced = outcome_layers.sync_action_receipts(data, run_id=RUN_ID)
    layers = outcome_layers.layer_summary(
        data, run_id=RUN_ID, window={"start": on, "end": on})
    read_len = len(box.get("text", ""))
    print(f"[3/5] 成功投递   {SUCCESS_ID} → committed — 门禁通过，"
          f"回读一致（{read_len} 字符）")
    print(f"      离线发送：transport=demo-memory（进程内内存，无网络、无账号）；"
          f"补 L1 凭证 {layers['L1']['receipts']} 张"
          f"（来自 {synced['written']} 条 committed 账本行）")

    # ── 4. 三道拦截（dry-run 永不触发 SEND）────────────────────
    explain = {
        "dry-run": "干跑停点：门禁与回读校验都过，未发送已留痕（--go 才实跑）",
        "rejected": "门禁拒绝：占位符残留 = 千人千面没做完",
        "failed": "回读不一致：宁可失败绝不发送（449 空正文事故的固化）",
    }
    print("[4/5] 三道拦截   dry-run / rejected / failed（对照：坏样本必须被挡住）:")
    for it in DEMO_ITEMS:
        if it["id"] == SUCCESS_ID:
            continue
        rec = pipe.run_one(it, channel="email", action="message", campaign=CAMPAIGN,
                           transport="demo-memory", target=it["target"],
                           expect=it["body"], compose=compose, fill=fill,
                           readback=readback, send=send_blocked, confirm=confirm,
                           dry_run=True)
        if rec["result"] != EXPECTED_RESULTS[it["id"]]:
            print(f"[FAIL] {it['id']} 终态 {rec['result']} 偏离预期 "
                  f"{EXPECTED_RESULTS[it['id']]}")
            return 1
        print(f"      {it['id']} → {rec['result']:<8} {explain.get(rec['result'], rec.get('detail', ''))}")

    # ── 5. 账本对账 + 日结果卡 ──────────────────────────────────
    final = {r["ref"]: r["result"] for r in ledger.results(campaign=CAMPAIGN)}
    ok = final == EXPECTED_RESULTS
    total = len(EXPECTED_RESULTS)
    summary = f"{total} 条终态与预期一致" if ok else f"终态偏离预期: {final}"
    print(f"[5/5] 账本       {data / 'ledger.jsonl'} — {summary}")

    run = _write_success_run(data, on, final)
    card = run.daily(data, on=on)
    from .grow_v2 import GrowRunV2
    rendered = GrowRunV2.render_daily(card)
    print("\n[日卡] 生产代码 GrowRunV2.daily 生成；数字来自本轮账本与队列：\n")
    print(rendered)

    committed = [r for r in ledger.results(campaign=CAMPAIGN)
                 if r.get("result") == "committed"]
    if committed:
        row = committed[0]
        print(f"[日卡回查] 账本 {row['ref']} · {row['channel']}/{row['action']} → "
              f"{row['target']} · {row['result']}（transport={row['transport']}）"
              f"——日卡「发布量 {card['published']['total']}」与 L1 同源")
    card_l1 = (card.get("layers") or {}).get("L1") or {}
    card_l2 = (card.get("layers") or {}).get("L2") or {}
    card_l3 = (card.get("layers") or {}).get("L3") or {}
    checks = {
        f"ledger {total} 条终态与预期一致": ok,
        "日卡 published 与账本 committed 一致":
            card["published"]["total"] == len(committed) == 1,
        "日卡 L1 OBSERVED 且有 1 条执行":
            card_l1.get("state") == "OBSERVED" and card_l1.get("actions") == 1,
        "日卡 L2/L3 保持 UNKNOWN（没有伪造数字）":
            card_l2.get("state") == "UNKNOWN" and card_l3.get("state") == "UNKNOWN",
    }
    bad = [name for name, passed in checks.items() if not passed]
    if bad:
        print("\n[FAIL] 日结果卡校验未过: " + "；".join(bad))
        return 1
    print(f"\n[自检] 日结果卡校验 PASS — 账本 {total} 条终态、日卡 1 条 committed、"
          "L1 有记录、L2/L3 为 UNKNOWN（不是 0）")

    print("\n下一步（真实使用）：写 data/plans/<plan_id>.json → lint → 人审 → "
          "mkt email message --queue data/queues/<campaign>.json（默认 dry-run，--go 才实跑）")
    return 0
