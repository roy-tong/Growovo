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
- 强制：`review.human_approved=false` 时，同 campaign 队列被 send_queue 与 MCP 拒跑
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
