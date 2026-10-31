# 数据 schema（data/，全部 gitignore）

## plans/<plan_id>.json 营销计划（规划层产出，05 产品方案 §2.3）

```json
{
  "plan_id": "am-trial20",
  "goal": "为 <你的产品> 拿到前 20 个企业试用",
  "created": "2026-10-04",
  "hypothesis": "MCP 工具作者有计量痛点",
  "icp": {"org": "做 agent 产品的团队", "persona": "平台/eval 工程负责人", "beachhead": "MCP 工具作者"},
  "audience": {"spec": "github:MCP servers 目录维护者", "target_count": 40, "evidence_grade": "B"},
  "waves": [
    {"wave": 1, "channel": "email", "action": "message", "size": 30,
     "content_policy": "求评式/千人千面/禁模板", "queue": "data/queues/am-trial20-w1.json",
     "caps": {"daily": 30}}
  ],
  "success_criteria": {"metric": "reply_rate", "target": "≥5%", "stop": "退信率>5% 即停"},
  "review": {"human_approved": false, "approved_at": null}
}
```

- 校验：`core.planlint.lint_plan`（schema/beachhead/能力矩阵/size≤内核日预算/判据预注册）
- 强制：`--go` 实跑必须绑定**已人审** plan——受管辖且 `human_approved=false` 拒跑；**无 plan 管辖的 campaign 默认同样拒跑**，仅 CLI 人工通道 `--adhoc` 显式放行（账本记 `adhoc=true`；MCP 不开放）。全局暂停（`data/state/PAUSED`）期间一切写操作拒跑
- 上限是内核属性：waves[].size 不得超过 `core/throttle.py` 的日预算，超过 lint 拒收

## queues/<campaign>.json 队列

```json
{
  "campaign": "er-wave7",
  "channel": "email",
  "action": "message",
  "items": [
    {"id": "w7-001", "target": "someone@example.com", "subject": "…",
     "body_file": "data/content/er-wave7/w7-001.txt", "ref": "contact-0412"}
  ]
}
```

- `body_file`（千人千面正文文件）或内联 `body` 二选一；正文禁止模板占位符（门禁拦截 `{{ }}`/`[NAME]` 等）
- X 渠道的 publish 队列即 vendor 的 `thread.json` 格式（`interval_seconds` + `tweets`）

## ledger.jsonl 全局账本（追加式，跨渠道唯一事实源）

```json
{"ts": "…", "channel": "email", "transport": "smtp", "action": "message",
 "campaign": "er-wave7", "target": "someone@example.com", "result": "committed",
 "evidence": "…", "ref": "w7-001", "detail": ""}
```

result 终态：`committed` / `indeterminate`（人工裁决，禁自动重发）/ `failed` /
`rejected`（门禁拒绝）/ `dry-run` / `no-dm` / `bounced` / `suppressed`。
幂等口径：只有 `committed` 算"已发送"；`no-dm|bounced|suppressed` 进不重试名单。

## contacts.jsonl 线索库（跨平台身份）

```json
{"id": "contact-0412", "name": "…", "org": "…", "title": "…",
 "platforms": {"email": "…", "x": "@…", "linkedin": "…", "reddit": "u/…"},
 "source": "github:<owner>/<repo>", "hooks": ["…"],
 "verification": {"email": "github-public-commit@2026-09"},
 "state": {"dnc": false, "bounced": null, "replies": 0,
           "last_touch": {"email": "2026-09-20"}},
 "tier": 1}
```

## suppress.txt 抑制名单

每行一个目标（email / @handle / URL），`#` 注释与空行忽略。来源：退信、对方明确拒绝、事故分层。

## state/ 日预算状态

`mkt-daily-YYYY-MM-DD.json`：`{"<channel>/<action>": n}`，按日滚动，旧文件即历史。

## demo/ 演示沙盒（mkt demo 生成）

`data/demo/`：plans/queues/ledger 全套演示产物（零网络零账号，transport=demo-memory）。
可整目录删除，不影响真实数据；重复跑只追加演示账本，终态恒为 dry-run/rejected/failed。

## experiments/<exp_id>.json 实验（预注册纪律：规则先于启动）

```json
{
  "exp_id": "p0-d14-gate", "hypothesis": "…", "metric": "manual_hours",
  "metric_spec": {"field": "manual_hours", "agg": "sum|count|latest"},
  "decision_rule": "判定规则（启动前必填，事后不改）",
  "window": {"start": "2026-10-31", "end": "2026-11-14"},
  "status": "planned|running|analyzed|stopped",
  "observations": [{"ts", "note", "data", "evidence_ref", "approximated"}],
  "conclusion": {"result": "met|not_met|inconclusive", "decided_by": "人（必填）", "aggregate"}
}
```

- 强制：无 evidence_ref 的观察必须 `approximated=true`；conclusion 必须署名——机器只聚合，不判定；观察只在 `running` 期接受（planned/analyzed 拒）；启动时对预注册字段存 `prereg_sha256` 指纹，观察/判定前校验未被手改；start/stop 记入 `change_log`
- CLI：`mkt experiment new/start/obs/list/conclude/stop`

## outcomes/events.jsonl 事件与计数（归因口径，非因果）

```json
{"kind": "event", "ts", "subject", "event_type": "signal|signup|activation|paid",
 "channel", "touch", "source_ref", "approximated",
 "verification": {"status": "verified", "method", "ref"}}
{"kind": "count", "ts", "metric", "value", "window", "source_ref", "approximated": true}
```

- 强制：activation/paid 禁止近似口径，且必须带 `source_ref` + `verification{method,ref}`（有效来源与核验状态）；计数必须带 source_ref 与 window；signup 近似主体在漏斗报告单列；存量缺核验价值事件单列披露不剔除
- 漏斗主体经 `contacts.jsonl`（id+平台身份）归一后去重；阶段间时序倒挂单列；归因只输出 last_touch/first_touch 记账口径，因果结论需要 experiment，不能从时间先后推出
- CLI：`mkt outcome event/count/funnel/attribution`
