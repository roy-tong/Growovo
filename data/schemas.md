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

## outcomes/records.jsonl 任务结果与判断质量（三层分离，append-only）

```json
{"schema_version": "1.0", "kind": "growovo.task_outcome", "ts", "task_id", "channel",
 "action_taken", "action_result": "sent|acked|failed|unknown",
 "task_outcome": "resolved|partial|unresolved|unknown|needs_human",
 "business_outcome": "observed|none|inconclusive",
 "decision_basis", "alternatives_considered": ["…"],
 "risk_class": "low|medium|high|irreversible",
 "verification_method", "evidence_refs": ["…"],
 "outcome_evidence_refs": ["…"], "escalation_reason"}
```

- 三层互不推导：平台 `sent` 不是任务完成，任务完成不是商业转化；`business_outcome=observed` 必须有自己的 `outcome_evidence_refs`，不能借任务结果充当业务证据
- 强制：`resolved`/`partial` 必须有 `verification_method` + 至少一个 `evidence_refs`；`action_result=unknown` 与 `task_outcome=needs_human` 必须有 `escalation_reason`；`unknown` 不得声称 resolved/partial（先人工核对平台，禁止自动重试）；`failed` 不得记 resolved；`observed` 必须有独立结果证据
- 写入：`core.task_outcome.append_outcome(data_dir, record)` → `<data>/outcomes/records.jsonl`，写前校验，坏记录不落盘；`read_records` 按追加顺序读回
- 自动记录：`core.action_runner.record_ledger_outcome` 在真实 `--go` 动作后按账本结果追加（`committed`→`sent`+`unknown`，`indeterminate`→`unknown`+升级原因，`failed`→`failed`+`unresolved`；dry-run 不写），记录失败只记日志、不改写动作结果
- 基准：`scripts/github_benchmark.py` 只评估离线 fixture（`tests/fixtures/github_benchmark/cases.jsonl`，fixture v2：33 例，21 dev / 12 holdout）上的 keyword/rule baseline，`--split dev|holdout|all`；输出字段准确率、category/action 混淆矩阵与安全/滥用类的有害动作率。`--fail-on-harmful` 在敏感用例仍会被公开动作时以退出码 1 阻断，可用作自动写路径的门。baseline 分数是下限与失败地图，不是分流能力证明；不含网络或模型调用

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

## GTM Work OS（v0.8，本地 R0/R1）

统一采用 `core/event_store.py` 的 `{schema_version,event_id,event_type,entity_id,ts,by,payload}` 事件包；当前状态由事件投影生成，业务事件必须署名。完整使用说明见 `docs/gtm-os/05-implementation.md`。

| 路径 | 事实与主要事件 |
|---|---|
| `context/snapshots.jsonl` | `context.imported`；不可变 `BusinessContextSnapshot`，来源为 `askovo` 或 `user_declared` |
| `work/objectives.jsonl` | `objective.created`；固定 context 与主指标版本 |
| `work/orders.jsonl` | `work_order.created/updated/state_changed`；`draft/blocked/ready/running/review/accepted/closed` |
| `work/sop-runs.jsonl` | `sop_run.started/step_completed/step_blocked/step_reopened`；步骤产物、DoD、人审与当前步骤 |
| `deliverables/events.jsonl` | `deliverable.created/revised/submitted/reviewed`；模板版本、字段、QA 与 accepted/rejected |
| `work/handoffs.jsonl` | `handoff.created/accepted/rejected`；只有指定 receiver 可决定 |
| `metrics/versions.jsonl` | `metric.registered`；不可变指标定义版本 |
| `sop/versions.jsonl` | `sop.registered`；5–8 步专业 SOP 版本 |
| `work/action-requests.jsonl` | `work_action.materialized`；WorkOrder/SOP/Deliverable 溯源及 plan/queue 路径 |
| `deals.jsonl` | `growovo.deal_event` 追加事件；`all()` 投影当前 deal，兼容旧当前行；漏斗按 history cohort |

关键约束：

- WorkOrder `ready` 必须有已验收输入、固定上下文/目标/SOP、交付物、Owner、Receiver 和结果指标；依赖未验收只会 `blocked`。
- SOP `done` 必须有产物 `{deliverable_type,artifact_id,version,evidence_ref}`、必需 DoD `pass+evidence_ref`，以及步骤声明的具名审批。
- Deliverable `submitted≠accepted`；R3/R4 作者不能终审自己；只有 accepted 交付物可交接或进入 `work-action`。
- `work-action` 只生成既有 plan/queue，`plan.provenance` 参与执行审批指纹；真实发送仍走 `plan approve`、`pipeline`、`action_runner` 与 `queue_runner`。
- KPI 计数/比率按 MetricVersion、去重键、过滤器和 scope 重算；分母 0 与未成熟窗口均返回 `value=null`，不回退全历史。

## 按需编排与 routine policy（v0.9）

`mkt orchestrate` 输出 `growovo.workstream_candidate/1`：`selection`（最小岗位及原因）、`workstream`（WorkOrder DAG 与 Handoff）、`content_plan`（每份交付物的 required_content、表达、语气、渠道规则）和 `validation`。它是候选方案，不是执行批准。

`policies/routine-marketing.json`：

```json
{
  "version": 1,
  "enabled": true,
  "allow_actions": ["email:message", "x:publish", "x:reply"],
  "allow_accounts": ["default"],
  "authorized_by": "human-owner",
  "authorized_at": "2026-11-04T12:00:00+00:00",
  "expires_at": null,
  "revoked": false
}
```

- 封闭动作集见 `core/execution_policy.py`；付款、合同、折扣、危机和未知动作不能加入。
- 非空 `allow_accounts` 精确、区分大小写匹配；空列表只允许未指定账号或字面 `default`，不是通配符。
- 只有显式 `routine=True` 且策略有效才返回 `routine-policy`；非 routine 仍走原 plan/adhoc 逻辑。
- routine 仅替代逐次 plan 审批；dry-run、pause、DNC、quota、readback、UNVERIFIED 和 indeterminate 人工裁决不变。
- 授权管理只在 CLI 人工通道；MCP 仅接受执行时的 routine/account 参数，不能创建、扩大或撤销策略。

## 战术执行记录（v0.13，2026-11-14）

`data/tactics/<run_id>.json`：非常规增长战术（`core/tactics.py` 注册表）的一条
四周执行记录。启动时把战术配额快照进文件，注册表后续改动不改写历史判定。

```json
{
  "id": "xrg-1",
  "tactic": {"id": "x-reply-guy", "name": "X 高价值回帖（四周）", "channel": "x",
              "queue_action": "reply", "quota": {"daily_cap": 20, "...": "..."},
              "formulas": ["数据型", "经验型"], "risk": "white"},
  "account": "", "owner": "",
  "started": "2026-11-14", "ends": "2026-12-11",
  "experiment": {"weeks": 4},
  "campaign": "xrg-1",
  "drafts": [{"on": "2026-11-15", "items": [
    {"target": "tweet:https://…", "body": "……", "formula": "数据型",
     "post_age_min": 4, "hour": 9, "queued": false}]}],
  "logs": [{"on": "2026-11-15", "sent": 18, "deleted": 0, "replied": 2,
             "moved": 0, "note": "", "items": []}],
  "metric_logs": [{"on": "2026-11-22", "profile_visits": 210, "new_followers": 31}],
  "violations": [{"on": "2026-11-15", "kind": "warn", "detail": "回帖超窗：…"}],
  "ledger_sync": {"on": "2026-11-16", "campaign": "xrg-1",
                   "committed_total": 18, "by_date": {"2026-11-15": 18}},
  "verdict": null
}
```

强制规则：
- 候选（drafts）入池前过全部硬检查：目标格式、占位符/长度/禁字符（复用
  `core/gates.py`）、**回复禁外链**、与本次执行记录内已有话术去重；超日上限
  （daily_cap）、超每小时上限（per_hour_max）、超每目标日上限
  （per_target_daily_max）直接拒绝。
- 回帖窗口超时（post_age_min > reply_within_minutes）、同一公式当日刷屏，记
  `violations` 告警，不拒绝。
- 已发生的事实（log）不拒收，但超限记违规，判定（`verdict`）时必须汇总；
  结论人判（`mkt tactic verdict` 只给数据与建议）。
- 队列导出（`mkt tactic export`）只生成既有 `queues/<campaign>.json` 并过
  `lint_queue`，发送仍走 `plan approve` → `queue run` 既有审批路径；
  `sync_from_ledger` 只统计 `committed` 行。

## 结果闭环 grow run（v0.14，2026-11-14）

`data/grow/<id>.json`：结果闭环执行记录。v1（`growovo.grow-run/1`，`mkt grow start`）
是「跑现成队列 + 周卡」的旧版；v2（`growovo.grow-run/2`，`mkt grow launch`）在此之上
增加策略编译、日配额、自动读数、日结果与决策。新闭环一律用 v2。

```json
{
  "schema": "growovo.grow-run/2",
  "id": "g1", "owner": "you@example.com", "started": "2026-11-14",
  "status": "active", "go": false,
  "facts_ref": "data/verovo-finops/facts.json", "facts_sha256": "…16位…",
  "goal": {"id": "awareness.followers", "metric": "followers",
            "target": 200, "target_kind": "delta", "baseline": 120,
            "window_days": 42, "target_text": "…", "starts": "2026-11-14",
            "ends": "2026-12-25"},
  "strategy": {"primary": {"channel": "x", "actions": ["reply"],
                            "why": "…", "residency_days": 14},
                "support": [{"channel": "email", "actions": ["message"]}],
                "not_doing": [], "tactics": ["x-reply-guy"],
                "fingerprint": "…16位…", "compiled_at": "2026-11-14"},
  "channels": ["x", "email"],
  "actions": ["email:message", "x:reply"],
  "quota": {"initial_actions_per_week": 20, "actions_per_week": 20,
             "action_weights": {"reply": 0.85, "message": 0.15},
             "daily_preview": {"2026-11-14": {"reply": 2, "message": 1}, "…": {}},
             "max_per_run": 10, "interval_seconds": [60, 120]},
  "windows": {"audience": "us-west", "timezone_offset": -8,
               "local": [[20.0, 23.0], [8.0, 10.0], [12.0, 13.5]],
               "dead_zone": [0.0, 6.0]},
  "supply": {"briefs": [{"id": "b-x-reply", "channel": "x", "action": "reply",
                          "count_per_week": 17, "formula": "…",
                          "max_len": 280, "gates": ["voice", "tactic", "no-link"]}],
              "min_cover_days": 3},
  "measurement": {"readers": [{"metric": "x_followers", "handle": "verovo",
                                "daily": true}],
                   "business_campaign": "g1", "verification": "UNVERIFIED"},
  "guards": {"account_id": "", "min_health": 60, "max_delete_rate": 0.1,
              "pause_on": ["takeover", "limited"]},
  "heartbeat": {"interval_minutes": 30, "last_tick": "…", "ok": true},
  "plan": {"actions_per_week": 20, "initial_actions_per_week": 20},
  "telemetry": {"days": {"2026-11-14": {"readings": {"followers": 121},
                                         "counts": {"reply": {"published": 2,
                                                              "duplicates": 0,
                                                              "suppressed": 0,
                                                              "failed": 0,
                                                              "indeterminate": 0,
                                                              "rejected": 0}}}},
                 "weeks": {"2026-11-14": {"published": 17, "failed": 0,
                                           "duplicates": 1, "readings_days": 6}}},
  "supply_state": {"staged": 12, "gap": 5, "coverage_days": 2},
  "daily_cards": [{"date": "2026-11-14", "published": {"total": 3, "planned": 3}}],
  "decisions": [{"on": "2026-11-14", "action": "continue", "by": "roy",
                  "note": "先观察", "changes": []}],
  "result_cards": [{"week": 1, "week_start": "2026-11-14",
                     "week_end": "2026-11-20", "published": {"total": 18},
                     "impressions": {"avg_daily": 4200},
                     "followers": {"new": 31},
                     "business": {"available": false, "funnel": {}},
                     "guardrails": {"failed": 0, "duplicates": 2},
                     "supply": {}, "next_week": {"changes": []}}],
  "adjustments": [{"on": "2026-11-20", "kind": "auto", "param": "actions_per_week",
                    "from": 20, "to": 25, "rollback": 20, "detail": "…"}],
  "history": []
}
```

强制规则（v2）：

- `launch` 先预检：facts 必填、渠道必须在 `core/platform_caps.py` 的 AUTO 白名单
  （第一期 x/email）、常驻授权只覆盖计划动作并集、账号健康（注册过才校验）、
  环境探针（`--skip-probe` 可跳过但要 doctor）。任一不过不落盘。
- 执行：`data/queues/<id>-*.json`，条目可有 `scheduled_for`/`window`；tick 按
  `quota.daily` 的日配额切片执行（`only_ids`），超过配额或窗口外顺延，受众本地
  00:00–06:00 死区不发；账本幂等命中跳过并继续，真拒绝/失败/未知才停车。
- 读数：tick 每日读一次并写 OutcomeLedger（approximated=true）；传感器失败只记
  `readings.error`，不改 status；连续缺数由 doctor 提醒。
- 结果：日结果卡每日生成；周卡在窗口结束后生成，含三件套、业务事件（有数据源才
  有）、护栏、供给、下周动作；`data/grow/<id>/cards/` 落 md/html。
- 决策：`decide continue` 只记录；`decide optimize` 生成提案并只改执行参数
  （默认 `actions_per_week`），每条改动带 `rollback`；策略级变更只出提案。
- 预算/暂停/DNC/回读/账本/人工开闸语义不变；needs_attention 不自动重试，恢复需
  `mkt grow resume --by --note`。

## 排期 schedules（v0.14 补文档；代码自 v0.12 已有）

`data/schedules/<id>.json`（`core/scheduler.py`）：显式队列文件的定时波次。

```json
{
  "schema_version": "1.0", "kind": "growovo.schedule",
  "id": "s1", "queues": ["data/queues/c1.json"],
  "interval_minutes": 60, "go": false,
  "routine": false, "account": "",
  "max_waves": null, "waves_done": 0,
  "status": "active", "created_at": "…", "last_run_at": null,
  "next_due_at": "…", "stop_reason": null, "history": []
}
```

- 不是守护进程：`mkt schedule tick` 由 cron/launchd/人调用，状态文件幂等。
- `routine: true`：波次按常驻授权跑（`run_queue(routine=True, account)`）；
  policy 无效时在审批门 fail-closed 拒跑，不会伪装通过。
- 每次至多一波；tick 发现 running=上次崩溃 → needs_review，人工 resume 才继续；
  队列 failed/indeterminate/rejected → 整个排期 needs_review；暂停闸波前波中都查。

## Growovo 自动内容供给（2026-11-14）

`grow/<run>.json` 的 `automation` 包含 `enabled/provider/daily_budget`、按日 `days.attempts/retry_at` 与 `last_result`。预算在调用模型前持久化。`grow/<run>/production.jsonl` 记录尝试、入队及拒绝原因；队列项携带 `editorial_review.approved/reason/body_sha256` 和 `fit_review`。模型字段不能覆盖目标或动作。`grow/<run>/evidence/<date>-<status_id>.png` 与帖子指标 `source_ref` 对应；指标快照可按日追加，但分析每条帖子只算一个样本。

## 信任节点目标库（v0.15，2026-11-14）

`data/targets/<id>.json`（`core/targeting.py`，`mkt target`）：把「先打谁」变成
评分与阶段——信任随粉丝量与商单密度衰减，目标选择=买在信任价格最低点。

```json
{
  "schema": "growovo.target-list/1",
  "id": "dream", "niche": "AI 独立开发者", "owner": "",
  "created": "2026-11-14",
  "targets": [{
    "key": "x:koc_dev", "platform": "x", "handle": "koc_dev",
    "followers": 12000, "engagement_rate": 0.04, "sponsored_recent": 0,
    "niche_match": true, "note": "",
    "score": 96, "tier": "koc_window", "in_koc_window": true,
    "reasons": ["…"],
    "stage": "engaged", "added_on": "2026-11-13",
    "stage_history": [{"stage": "candidate", "on": "…", "note": ""}]
  }],
  "history": []
}
```

强制规则：
- 评分（score_candidate）：信任档位系数（KOC 甜区 1 千-5 万=1.0，50 万+=0.22）
  × 同档互动溢价（0.5-2.0）× 商业化折价（每条商单 -20%，下限 0.2）× 领域匹配
  （1.2/0.5）；0-100 分并输出理由。
- 同 platform+handle 去重（防重复触达）；候选数据由运营 agent 从平台读入，
  本模块不抓取任何平台。
- 阶段只进不退且逐级推进：candidate → contacted → engaged → amplified → partner
  （一步一证据）；engaged/amplified 是 koc-first 战术判定的输入
  （TacticRun 关联 `--target-list` 后，summary/verdict 自动带目标进度）。
- `mkt tactic list` 默认排除证据过期（超过 revalidate_days）的战术——衰减定律
  进了建议路径；`--include-stale` 放开。

## 对手事件监控（v0.16，2026-11-14）

`data/watch/<id>.json`（`core/watch.py`，`mkt watch`）：对手公开页面的 diff 管线——
对手涨价/故障/changelog 变化时自动产出事件并联动战术注册表的承接打法。

```json
{
  "schema": "growovo.watch-list/1",
  "id": "rivals", "owner": "",
  "created": "2026-11-14T21:00:00",
  "entries": [{
    "url": "https://rival.example/pricing", "kind": "price",
    "competitor": "Rival", "note": "",
    "added_at": "…", "baseline_len": 4821,
    "last_snapshot": "文本签名（去标签压空白后的 diff 基准）",
    "last_checked": "…", "last_change": null, "changes": 0
  }],
  "history": []
}
```

`data/watch/events.jsonl`（追加式变化事件）：
```json
{"on": "2026-11-14", "ts": "…", "list": "rivals", "competitor": "Rival",
 "kind": "price", "url": "…", "before_len": 4821, "after_len": 4910,
 "diff": "'…49 dollars…' → '…99 dollars…'",
 "tactics": ["price-hike-harvest", "vs-page-matrix", "migration-narrative"]}
```

强制规则：
- kind 三类：price/status/changelog，变化时事件自动带上对应承接战术 id。
- add 时即抓基线快照：抓取失败或内容过少（<20 字符签名）拒收，不收死链。
- check 时抓取失败的项跳过不误报（网络失败≠页面变化）；diff 比较的是文本签名。
- 只读监控：本模块不发送任何东西，承接动作走既有战术执行与审批路径。
- 事实包+目标的战术组合选择（`mkt tactic recommend` / MCP `mkt_tactics`）
  无独立数据文件：纯函数，透明打分（目标族+3/usable+2/渠道+2/阶段+1），
  同机制最多 2 条，过期证据默认排除。
