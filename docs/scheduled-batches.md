# 定时小批次的配置与恢复

适用于 0.23.1rc2 的 `mkt cycle`。定时 wave 使用具名账号和专用队列。已停止的旧计划保留作只读查账。旧的无作用域定时脚本迁移到 jobs-tick 后才具备下面的保证。

```json
{
  "cadence": "hourly",
  "start_at": "2030-01-01T20:00:00+08:00",
  "jobs": [{
    "id": "community-hour-01",
    "kind": "wave",
    "slot": 0,
    "expire_after_sec": 2700,
    "budget_sec": 1200,
    "accounts": {"github": "example-owner"},
    "allowed_channels": ["github"],
    "targets": {"github/reply": 2},
    "queue": "reviewed-replies.json"
  }]
}
```

时间、账号和文件名仅作示例。每个 slot 比前一个晚一小时，同一 slot 可安排多个渠道作业。显式填写的 at 必须与 slot 对齐。整批串行作业可继续逐项给出 at。队列可使用数组或 items 数组，每项包含 id、channel、action、target、text 及回复所需的上下文。引用既有计划的作业填 plan_id，执行前分别核对原计划和定时合同。

```bash
mkt --workspace "$WORKSPACE" cycle jobs-preview --file jobs.json
mkt --workspace "$WORKSPACE" cycle jobs-approve --file jobs.json --by "$OPERATOR"
mkt --workspace "$WORKSPACE" cycle jobs-tick --file jobs.json --job-id community-hour-01
mkt --workspace "$WORKSPACE" cycle jobs-serve --file jobs.json
mkt --workspace "$WORKSPACE" cycle report --file jobs.json
mkt --workspace "$WORKSPACE" cycle notifications
```

`jobs-serve` 由安装后的 Growovo 进程持续等待和执行作业，不需要 Kimi 或其他聊天 Agent 在线；`--once` 用于外部调度器和离线验收。它的 `operations` 分别报告调度器自身 token 为 0、其他环节 token 未测量和实际耗时，不把局部成本当成整批成本。

预检只读。它按账号、当前队列、已知停止记录、窗口、间隔和已有账本列出每个作业的可排量、缺口、准备负责人及截止时间。未来入站、外部配额变化和平台最终回执需要在当波重新观察。负责人复核队列和接单量后执行 jobs-approve，命令会冻结当前程序、作业窗口、账号与各队列项的正文摘要。Python 解释器、已安装的第三方依赖和宿主浏览器沿用部署环境。定时器按到期时刻调用独立 job-id，写前读取实时停止、身份及额度。正常的队列消费可跨进程恢复。包含 GitHub 动作的批次在批准前检查实际 gh 可执行文件，不把缺命令留到发送时。

用户明确要求立即执行或调整业务节奏时，在作业中填写 `policy_overrides`，而不是口头声明例外。支持 `window`、`platforms`、`interleave`、`queue`、`inbox` 和 `content_rules`；例如 `window.start_at/end_at` 使用带时区的精确 ISO 时间，并必须覆盖作业的 at 和截止时间。链接要求、渠道间隔、作者上限等因此可与本批业务目标一致。未知字段、安全停止层和额度安全层不能通过这个入口绕过；429、撤权、验证码、未知回执、空正文和重复动作仍按原规则处理。

签过的作业内容有变化时，重新预检并签署新的作业 ID。补跑用 retry_of 引用原作业，账号、允许的内容和有效窗口须受原授权覆盖。窗口外的新增任务须单独批准。将配置的 plan_status 置为 STOPPED_BY_USER 后，后续派发停止，已有外部回执继续计入原作业。请保留工作区的 approvals、state、账本和操作记录。

jobs-tick 的 changes 提供本次新增的状态变化、阻断原因和下一步。部分完成、过期、停写和执行失败会返回非零退出码，不能让串行 runner 把本次调用记为成功。jobs-status 和 report --file 按同一合同结算，包含缺波次文件的早停和未触发作业。只读结算可以消费部分完成的上游结果，依赖它的后续写作业等待上游成功。报告分别展示承接量、计划量、尝试、提交和业务观测；未填写业务目标的作业保持目标 0，不继承供给策略的默认数量。业务卡的 `operations` 另行保留引擎状态、调度状态、退出码、耗时和 token 测量状态。

每次状态变化先写入持久 notification outbox，再保存作业状态；`cycle notifications` 只读列出尚未确认投递的事件。投递适配器必须返回回执，异常保留待投递。动作 claim 另外记录 dispatching 和 acknowledged（仅表示适配器返回），回读与写入使用同一账号/渠道路线租约，避免写入中导航同一浏览器路线。适配器返回或独立回读都不自动等同于业务结果。

外部写后断线进入只读裁决，历史记录保留。发现 429 后，维护者核验具名账号恢复情况，用 cycle ack-rate-limit --night 原停止日期 --channel 渠道 --by 负责人 --evidence 核验记录引用 登记处置。此命令不发送内容，也不恢复旧定时任务。未知回执仍走原 ledger resolve 流程。
