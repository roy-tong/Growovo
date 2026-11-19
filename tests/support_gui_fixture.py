"""伴随 GUI 与 handoff 测试共用的本地 fixture。

全部在临时目录构造：一份运行文件、一条待裁决账本、一条退订回复、
一份 routine 授权。不发送、不审批、不触碰真实 data/。
"""
import json
from pathlib import Path

from core import execution_policy, reply_case

NOW = "2026-11-19T10:00:00"
ON = "2026-11-19"
RUN_ID = "R1"
TARGET = "reader@example.com"
LEDGER_REF = "led-1"

ACCOUNTS = {
    "version": 1,
    "accounts": [{
        "id": "acc-1", "platform": "email", "handle": "owner@example.com",
        "identity_type": "pseudonymous", "status": "eligible",
    }],
    "audit": [],
}

RUN = {
    "schema": "growovo.grow-run/2",
    "id": RUN_ID,
    "status": "active",
    "go": True,
    "owner": "owner@example.com",
    "started": ON,
    "channels": ["x"],
    "goal": {"id": "awareness.followers", "family": "awareness",
             "name": "粉丝增长", "metric": "followers",
             "metric_label": "粉丝", "target": 100,
             "target_kind": "absolute", "target_text": "30 天 +100 粉丝",
             "window_days": 30, "starts": ON,
             "ends": "2026-12-19", "baseline": 0,
             "proves": "账号粉丝净增", "does_not_prove": "不证明付费转化",
             "leading": [], "counter": []},
    "strategy": {"primary": {"channel": "x", "platform": "x",
                             "actions": ["reply"], "why": "目标读者在 X",
                             "tier": "auto", "residency_days": 7},
                 "support": [], "not_doing": [], "tactics": [],
                 "compiled_at": ON, "fingerprint": "fp"},
    "quota": {"actions_per_week": 3, "initial_actions_per_week": 3,
              "max_per_run": 1, "interval_seconds": 60,
              "daily_preview": 1, "action_weights": {"reply": 1}},
    "plan": {"actions_per_week": 3, "initial_actions_per_week": 3},
    "measurement": {"readers": [], "business_campaign": RUN_ID,
                    "verification": "UNVERIFIED",
                    "calibration": {"due": "2026-11-26", "checks": [],
                                    "streak": 0}},
    "supply": {"briefs": []},
    "supply_state": {"staged": 0, "upcoming": 0, "coverage_days": 0,
                     "gap": 0, "daily_quota": 1, "horizon_days": 1,
                     "by_day": {}, "note": "暂无供给"},
    "content": {"mix": {}, "formula_weights": {}, "layers": {}},
    "content_index": {},
    "learning": {"sample": 0, "buckets": {}, "changes": [],
                 "recommendation": "继续采集", "min_sample": 3,
                 "on": ON,
                 "collection": {"pending": 0, "written": 0}},
    "history": [{"at": "2026-11-19T09:00:00", "kind": "launch",
                 "ran": 0, "published": 0}],
    "heartbeat": {"last_tick": "2026-11-19T09:00:00+00:00",
                  "interval_minutes": 30, "ok": True},
}


def build(data):
    """在临时目录里构造最小可用数据，返回定位信息。"""
    data = Path(data)
    (data / "grow").mkdir(parents=True, exist_ok=True)
    (data / "accounts.json").write_text(
        json.dumps(ACCOUNTS, ensure_ascii=False), encoding="utf-8")
    execution_policy.authorize(
        data, by="owner", actions=["x:reply"], days=30, now=NOW)
    (data / "grow" / ("%s.json" % RUN_ID)).write_text(
        json.dumps(RUN, ensure_ascii=False), encoding="utf-8")
    (data / "ledger.jsonl").write_text(json.dumps({
        "ts": "2026-11-19T09:00:00", "channel": "x", "action": "post",
        "campaign": RUN_ID, "target": "@someone", "result": "indeterminate",
        "evidence": "", "ref": LEDGER_REF,
    }, ensure_ascii=False) + "\n", encoding="utf-8")

    csv = data / "replies.csv"
    csv.write_text(
        "ref,text,ts,from,run,action\n"
        'msg-1,"Please take me off your list.",2026-11-19T09:30:00,'
        "%s,%s,action-1\n" % (TARGET, RUN_ID), encoding="utf-8")
    imported = reply_case.import_replies(
        data, csv, account_id="acc-1", channel="email",
        imported_by="owner", authorization_ref="ticket-42", now=NOW)
    case = reply_case.list_cases(data, run_id=RUN_ID)[0]
    return {
        "data": data,
        "run_id": RUN_ID,
        "reply_case_id": case["reply_case_id"],
        "reply_version": "v%d" % int(case.get("version") or 1),
        "ledger_ref": LEDGER_REF,
        "target": TARGET,
        "imported": imported,
    }
