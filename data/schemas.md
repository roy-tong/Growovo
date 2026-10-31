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
- 强制：`--go` 实跑必须绑定**已人审** plan——受管辖且 `human_approved=false` 拒跑；**无 plan 管辖的 campaign 默认同样拒跑**，仅 CLI 人工通道 `--adhoc` 显式放行（账本记 `adhoc=true`；MCP 不开放）。受管辖计划在审批前还必须过 `lint_plan`；旧式只有 `human_approved=true` 的无效计划也不能绕过。全局暂停（`data/state/PAUSED`）期间一切写操作拒跑
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
幂等口径：只有 `committed` 算"已发送"；`indeterminate|no-dm|bounced|suppressed` 进不重试名单。
预算：发送前调用 `Throttle.reserve` 原子预留；预算不足不会进入 send，未知结果占用的额度保留。

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
执行时还会合并 `contacts.jsonl` 中 `state.dnc=true` 或 `state.bounced` 联系人的全部平台身份；DNC 不再只是 UI 隐藏条件。

## playbooks/github-inbox/<campaign>.json 运营 Playbook

`mkt playbook github-inbox` 产出：repo、campaign、生成时间、counts、items[{id,number,kind,title,labels,age_days,disposition,priority,suggested_action,draft_required,draft_status}]。
`disposition`：`reply_candidate|review|escalate|triage_only`。候选回复 JSON 为 `{id|number: body}` 或 `{drafts:[{id,body}]}`；只有 reply_candidate 且有草稿的项会进入标准队列。队列构建后仍需 plan 审批、dry-run、queue run 和目标平台回读；Playbook 本身不发消息。

## state/ 日预算状态

`mkt-daily-YYYY-MM-DD.json`：`{"<channel>/<action>": n}`，按日滚动，旧文件即历史。

## demo/ 演示沙盒（mkt demo 生成）

`data/demo/`：plans/queues/ledger 全套演示产物（零网络零账号，transport=demo-memory）。
可整目录删除，不影响真实数据；重复跑只追加演示账本，终态恒为 dry-run/rejected/failed。

## experiments/<exp_id>.json 实验（预注册纪律：规则先于启动）

```json
{
  "exp_id": "p0-d14-gate", "hypothesis": "…", "metric": "manual_hours",
  "metric_spec": {"field": "manual_hours", "agg": "sum|count|latest",
                   "source": "outcomes（可选：从事件台账自动聚合）"},
  "campaign": "askovo-p0-w1（可选：把实验挂到某轮 campaign）",
  "decision_rule": "判定规则（启动前必填，事后不改）",
  "window": {"start": "2026-10-31", "end": "2026-11-14"},
  "status": "planned|running|analyzed|stopped",
  "observations": [{"ts", "note", "data", "evidence_ref", "approximated"}],
  "conclusion": {"result": "met|not_met|inconclusive", "decided_by": "人（必填）", "aggregate"}
}
```

- 强制：无 evidence_ref 的观察必须 `approximated=true`；conclusion 必须署名——机器只聚合，不判定；观察只在 `running` 期接受（planned/analyzed 拒）；启动时对预注册字段存 `prereg_sha256` 指纹，观察/判定前校验未被手改；start/stop 记入 `change_log`
- CLI：`mkt experiment new/start/obs/list/conclude/stop`；`obs --from-outcomes` 按 `metric_spec.source=outcomes` 自动聚合（`field` 取漏斗阶段或 `<stage>_rate`，比率口径 = 该阶段去重主体 / signal 去重主体）

## outcomes/events.jsonl 事件与计数（归因口径，非因果）

```json
{"kind": "event", "ts", "subject", "event_type": "signal|signup|activation|paid",
 "channel", "touch", "source_ref", "approximated", "campaign": "（可选，归位用）",
 "import_key": "（CSV 导入幂等键）",
 "verification": {"status": "verified", "method", "ref"}}
{"kind": "count", "ts", "metric", "value", "window", "source_ref", "approximated": true,
 "campaign": "（可选）"}
```

- 强制：activation/paid 禁止近似口径，且必须带 `source_ref` + `verification{method,ref}`（有效来源与核验状态）；计数必须带 source_ref 与 window；signup 近似主体在漏斗报告单列；存量缺核验价值事件单列披露不剔除
- 漏斗主体经 `contacts.jsonl`（id+平台身份）归一后去重；阶段间时序倒挂单列；归因只输出 last_touch/first_touch 记账口径，因果结论需要 experiment，不能从时间先后推出
- CLI：`mkt outcome event/count/import/sync/funnel/attribution`
- `import`：CSV 列名兼容 `subject/email`、`event_type/type`、`ts/timestamp`、`source_ref/ref`、`campaign/utm_campaign` 等；逐行校验、`import_key` 幂等、可 `--dry-run`
- `sync`：把账本按渠道聚合成 `touches_committed` / `touches_dry_run` 计数快照（触达数不是漏斗事件，不占 signal 槽位）

## approvals.jsonl 审批事件（人审可复核）

```json
{"ts": "…", "plan_id": "askovo-p0", "action": "approve|revoke",
 "by": "roy", "note": "…", "plan_sha256": "…", "expires_at": null}
```

- 计划内 `review` 块：`{human_approved, approved_at, approved_by, plan_sha256, expires_at}`
- `plan_sha256` = 计划内容（**不含 review 块**）规范化 JSON 的 sha256；批准后改任何实质内容 → 指纹失配，实跑被拒（必须重新审批）
- 批准时把当时版本快照到 `data/approvals/<plan_id>-<sha12>.json`，事件里记 `snapshot` 路径；之后计划再改也能取回"批的是哪一版"
- 旧计划只有 `human_approved=true`、无指纹时按 legacy 放行；新审批一律带指纹，可选 `--days` 有效期
- CLI：`mkt plan approve <id> --by <人> [--days N]` / `mkt plan revoke <id> --by <人>`

## ledger_resolutions.jsonl 人工裁决（append-only）

```json
{"ts": "…", "ref": "w7-001", "result": "committed|failed|suppressed|bounced|no-dm",
 "by": "roy", "note": "平台后台核实：…"}
```

- `mkt ledger resolve <ref>` 只追加裁决记录，读取时按 ref 覆盖 result；**原账本行不改**，可回滚审计
- 只允许裁决已存在的 ref；必须署名 + 写依据

## links.jsonl 追踪链接

```json
{"id": "…", "ts": "…", "campaign": "askovo-p0-w1", "channel": "hn",
 "content": "show-hn", "url": "https://…", "tagged_url": "https://…?utm_source=hn&…"}
```

- 只追加 `utm_*`，保留原有非 utm 参数；同一 `campaign|channel|url|content` 幂等
- 用途：导入事件时按 `utm_campaign` / `source_ref` 把 signal 与 campaign 归位
- CLI：`mkt link create/list`

## 队列 lint 与 campaign 视图

- `mkt queue lint <file>`：编辑期门禁（campaign/文件名一致、渠道能力、id 与目标去重、占位符、em dash、长度上限）；`body_file` 不存在直接报错
- `mkt campaign report <campaign>`：join plan/approval/queue/ledger/outcomes/experiment → 一页 Markdown；账本 committed 与事件分栏，不合成转化率
- `mkt review <campaign>`：队列逐条预检 + 干跑留证 + 批准/执行命令
- `mkt next`：按产物状态给出唯一优先动作（plan → approve → queue → dry-run → go → capture → experiment → report）

## 工作区（多产品隔离）

- `--workspace <dir>` 或 `MKT_HOME=<dir>` 指定 data 目录；默认 `<repo>/data`
- 一个产品一个工作区：账本、事件、实验、审批、链接都在该目录下，互不污染


## 队列执行（全渠道）与单条动作

- `mkt queue run <file> [--go] [--only <id>] [--allow-unverified]`：队列先过 `queue.lint`，再统一做
  暂停/UNVERIFIED/人审计检查，逐条交给 action_runner。email 仍走 `send_queue` 整队执行。
  X 队列只支持 `reply`；`publish` 用 `mkt action x publish`，`message/engage` 用 `mkt action` 透传 vendor 参数。
- `mkt action <channel> <action>`：单条写动作，与队列共用暂停/审批/账本；`email` 只支持队列。
  LinkedIn 实跑要求 `MKT_LINKEDIN_AUTO=1`（MCP/队列无 TTY，不能交互确认）。

## 本地 collector（core/collector.py）

默认只绑 `127.0.0.1:8787`，三种接入：

| 路由 | 用途 | 写入 |
|---|---|---|
| `GET /t/<link_id>?cid=…` | 追踪链接跳转 | 一条 `signal`（同主体 30 分钟内只记一次），302 到带 utm 的目标页 |
| `POST /events` | 表单/产品后端/PostHog Webhook | event 或 count；默认只收 signal/signup，activation/paid 必须带 `source_ref` + `verification` |
| `GET /health` | 健康检查 | — |

鉴权：`Authorization: Bearer <token>` 或 `?token=`；token 来自 `MKT_COLLECT_TOKEN` / `--token`。
写入一律经 `OutcomeLedger` 校验（价值事件不能靠匿名 POST 成立）。

CSV 导入：
- `mkt collect import --source plausible --csv <导出.csv>`：Plausible 是聚合数据，只写 `visits` count，
  不伪造漏斗事件；按 `(campaign, metric, window)` 幂等。
- `mkt collect import --source posthog --csv <导出.csv> --event-map user_signed_up=signup,activated=activation`：
  PostHog 事件级导入，`distinct_id`/`event`/`timestamp`/`utm_campaign` 列自动映射，走 `import_key` 幂等。
