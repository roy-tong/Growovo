# Growth Goal Compiler and Service Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline with TDD. No git commit unless the user explicitly requests it.

**Goal:** 用同一 GrowthGoalContract 生成业务动作 DAG，并由持久 Growth Service Controller 执行状态管理、下一动作选择、人工打断核算和业务结果评估。

**Architecture:** `growth_goals` 负责合同校验和编译；`growth_service` 负责持久状态与事件；`growth_cli` 提供真实 CLI。后续 Action Resolution、Opportunity、Inbox、Asset 和 Outcome 模块复用同一 action/service id。

**Tech Stack:** Python 3.9、stdlib、AtomicJSONStore、AppendOnlyJSONL、argparse、unittest。

## Tasks

### Task 1: GrowthGoalContract 与 Business Action DAG

- Create `core/growth_goals.py`, `tests/test_growth_goals.py`
- `validate_goal(payload) -> dict`
- `compile_goal(payload) -> {service, actions, deferred_actions, measurement}`
- 支持小时级窗口、多 outcome、live/preview/live_with_manual_fallback、预算内缩减、动作依赖与 measurement_ref。
- 先测试：单主目标+两个辅目标、九小时窗口、非法时间、缺主目标、预算缩减、动作包含 goal/hypothesis/asset/CTA/conversion/measurement。

### Task 2: 持久 Growth Service Controller

- Create `core/growth_service.py`, `tests/test_growth_service.py`
- `start(data_dir, goal) -> service`
- `load(data_dir, service_id) -> service`
- `next_action(data_dir, service_id) -> action | None`
- `record_action(data_dir, service_id, action_id, status, by, note="", minutes=0, evidence=None) -> service`
- `evaluate(data_dir, service_id, metrics, by) -> evaluation`
- 状态保存 `state/growth-services/<id>.json`，事件追加 `state/growth-events.jsonl`。
- 局部阻断不停止其他动作；自主交付率、人工分钟/验收任务、Human Interruption Cost 分开计算。

### Task 3: CLI 与 Action Resolution 基础接口

- Create `core/growth_cli.py`, `tests/test_growth_cli.py`
- `mkt growth compile --file goal.json`
- `mkt growth start --file goal.json`
- `mkt growth status SERVICE_ID --json`
- `mkt growth next SERVICE_ID --json`
- `mkt growth transition SERVICE_ID --action ACTION_ID --status completed|blocked|in_progress|pending --by NAME [--minutes N]`
- `mkt growth evaluate SERVICE_ID --metrics metrics.json --by NAME`
- `core/action_resolution.py: resolve(action, context) -> {execution_mode, blocked, reason, handoff}`；API→CDP→人工交接，live 阻断不自动 dry-run。

### Task 4: 回归与文档

- 定向测试和全量 unittest 通过。
- 用真实样例 goal JSON 完成 compile→start→next→transition→evaluate→status。
- 更新 README、feature-status 与实施计划勾选状态；没有真实外部用户指标时保持 UNKNOWN。
