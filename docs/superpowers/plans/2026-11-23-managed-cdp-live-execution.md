# Managed CDP and Live Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 让所有 Growovo 浏览器动作复用 WebMonitor 受管标签组，并让用户写命令默认表达真实执行意图而非 dry-run。

**Architecture:** `BrowserAttach` 负责唯一受管标签页创建与登记，Playwright 只复用已存在页面。CLI/MCP 通过统一执行意图解析器区分默认 live 与显式 preview；授权、暂停、DNC、配额、身份、回读和未知结果保护不变。

**Tech Stack:** Python 3.9、stdlib unittest、现有 WebBridge/WebMonitor、Playwright、argparse。

## Global Constraints

- 未授权账号、验证码/挑战、429、风控对抗继续禁止。
- 当前工作树已有 X owner-attested 修改必须保留。
- 显式 `go=False` 的旧调用和测试仍是 preview；省略 go 的用户命令默认 live。
- 不通过改变账本状态或伪造回执升级渠道。
- 不自行 git commit；用户未要求提交。

---

### Task 1: 受管标签页注册与 BrowserAttach 解析

**Files:**
- Create: `core/managed_tabs.py`
- Modify: `core/browser_attach.py`
- Test: `tests/test_managed_tabs.py`

**Interfaces:**
- `ManagedTabs(data_dir).get(session, site) -> dict | None`
- `ManagedTabs(data_dir).remember(session, site, tab_id, group_title, url) -> dict`
- `ManagedTabs(data_dir).forget(session, site, tab_id) -> None`
- `BrowserAttach.ensure_managed_tab(data_dir, site, url, group_title, prefer_visible=False) -> int`

- [x] 先写测试：登记后复用同一 tab；失效登记被移除；无页面时仅通过 `wb.nav(..., new_tab=True, group_title=...)` 创建一次。
- [x] 运行 `python3 -m unittest tests.test_managed_tabs -v`，确认因模块/方法不存在而失败。
- [x] 用 `AtomicJSONStore` 实现持久注册表；`ensure_managed_tab` 先验证登记页，再复用/借用同站页，最后调用现有 `ensure_tab(..., allow_new=True)` 创建并登记。
- [x] 重跑测试确认通过。

### Task 2: browser-attach 与 X vendor 禁止新页旁路

**Files:**
- Modify: `core/browser_write.py`
- Modify: `channels/x/adapter.py`
- Modify: `channels/x/vendor/session_page.py`
- Modify: `channels/x/vendor/like_x.py`
- Modify: `channels/x/vendor/follow_x.py`
- Modify: `channels/x/vendor/xdm.py`
- Modify: `channels/x/vendor/scan_inbox.py`
- Modify: `core/cdp.py`
- Test: `tests/test_browser_attach.py`, `tests/test_x_ledger_gates.py`, `tests/test_core.py`

**Interfaces:**
- `automation_page(ctx)` 只返回已存在 X/空白页；没有则抛 `RuntimeError("managed_tab_required")`。
- Growovo X adapter 在调用 vendor 前确保受管 X 页面存在，并传递 `MKT_MANAGED_TAB_URL`。

- [x] 增加失败测试：browser_write 使用 `ensure_managed_tab`；vendor 无页面可复用时抛错而非 `new_page()`；adapter 预检调用受管页。
- [x] 运行相关测试确认失败。
- [x] 替换直接 `ctx.new_page()` / `browser.contexts[0].new_page()`；`core/cdp._reuse_page` 无页面可复用时抛 `managed_tab_required`。
- [x] 重跑相关测试并 grep 确认生产代码不再存在直接 Playwright 新页调用。

### Task 3: 统一默认 live / 显式 preview 解析

**Files:**
- Create: `core/execution_intent.py`
- Modify: `mkt.py`, `mcp_server.py`
- Test: `tests/test_execution_intent.py`, `tests/test_cycle_scheduler.py`, `tests/test_cli_e2e.py`

**Interfaces:**
- `resolve_go(go=None, dry_run=False) -> bool`：`dry_run=True → False`；否则 `go is None → True`；显式 go 保留布尔值。
- `_execution_go(args) -> bool`：CLI 统一入口。

- [x] 写失败测试：`resolve_go()` 为 True、`resolve_go(dry_run=True)` 为 False、显式 False 保留 False；queue/action/cycle/schedule 省略 go 时传 True。
- [x] 运行测试确认失败。
- [x] 为写命令增加互斥 `--go/--dry-run`，省略时 `args.go=None`；所有执行调用使用 `_execution_go(args)`。MCP 增加 `dry_run` 并按同函数解析。
- [x] 旧测试中的 preview 改成显式 `go=False` 或 `--dry-run`，不改变其测试目的。
- [x] 重跑定向测试确认通过。

### Task 4: 文档、全量回归与真实命令验收

**Files:**
- Modify: `README.md`, `docs/platform-automation-matrix.md`, `docs/feature-status.md`, `docs/grow-autonomous-operations.md`, `AGENTS.md`

- [x] 更新默认执行、显式 preview、owner-attested CDP 和受管标签组说明，删除“所有 X CDP 写永久禁止”的旧描述。
- [x] 运行 `python3 -m unittest discover -s tests`，全部通过。
- [x] 运行 `python3 mkt.py demo`，确认离线 demo 仍显式使用 preview，不触发真实账号。
- [x] 运行 `python3 mkt.py doctor` 与 `git diff --check`，记录 live 渠道缺失，不伪造 canary。
