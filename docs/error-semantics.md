# 错误语义：一套词表，不共用业务库

日期：2026-11-20 · 代码：`core/error_semantics.py` · 对端：`Verovo/buyer-ledger/buyer_ledger/error_semantics.py`

三个产品各自管自己的对象、数据库和判定。用户在不同产品里碰到"被拒绝"时，应该看到同一套分类：是缺输入、是不支持、是没权限，还是版本过期。**统一的是语义，不是数据，也不是结论。**

不做什么：

- 不建族级错误总线，不让一个产品的错误码决定另一个产品的业务状态。
- 不把 `UNPROVABLE` / `UNOBSERVABLE` 折成 `FALSE`，也不把"被拒绝"折成"失败"。
- 不新增跨产品统一评分或统一 verdict。错误分类只在各自进程内产生，跨产品只共享这份词表。

## 七个族

| 族 | 含义 | 典型触发 | 用户下一步 |
|---|---|---|---|
| `NEEDS_USER_INPUT` | 产品能做，但缺必要输入或待人工裁决 | 缺 `--campaign` / `--targets`；待审回复未裁决 | 补字段或完成审核，命令里给出缺哪些 |
| `UNSUPPORTED_CAPABILITY` | 产品明确不做，或当前环境不具备该能力 | 平台不允许自主写；系统无 `fcntl` 拒绝假装有锁 | 换合规渠道或改人工路径，不要重试同一命令 |
| `INVALID_INPUT` | 输入格式或内容不合格 | JSON 坏、占位符未替换、内容未过验收门 | 按提示改内容后重交 |
| `PERMISSION_DENIED` | 身份或权限不成立 | 非买方组织尝试验收；token 错误 | 用正确身份或取得授权，不要绕过 |
| `SAFETY_BLOCKED` | 安全闸主动拦下 | 外部限流；批次未知结果未裁决；审计链断 | 先核对现场，再按恢复入口继续 |
| `STALE_OR_CONFLICT` | 对象过期或与当前版本冲突 | 签核前快照；旧 `object_version`；源文件哈希变化 | 重新读取当前版本，旧结论作废 |
| `CONTRACT_ERROR` | 产品内部契约或路径不一致 | 状态机缺字段、产物不完整 | 属实现缺陷，保留现场并报告，不要重试 |

约定：`SAFETY_BLOCKED` 不是"永久禁止"，它表示**在完成人工核对前**不得继续；`RATE_LIMITED` 冷却后可重试，`INDETERMINATE_EXTERNAL_ACTION` 必须先确认外部动作是否已发生。

## 每条用户可见错误必须四件齐全

1. **缺什么 / 发生了什么**：具体字段、对象或事件，不说"出错了"。
2. **影响**：不补会怎样，哪些后续被挡住。
3. **合法下一动作**：一条用户能执行的命令或入口。
4. **示例或修复入口**：可复制的示例参数，或指向文档/界面的入口。

Growovo 的真实输出示例（`mkt queue build` 缺参）：

```
业务操作被拒绝：缺少必要输入：--campaign、--template、--targets
影响：队列无法生成，审批与执行都不会开始。
下一步：补上这些参数后重跑 build；示例：--campaign C1 --template T1 --targets targets.json
```

机器可读形式：`GROWOVO_ERROR_JSON=1` 时输出 JSON（`schema=growovo.error/1`，含 `reason_code`、`missing`、`impact`、`next_action`、`example`）。退出码 2 表示业务拒绝，退出码 1 保留给真正的运行时异常；Exit 3 仍专用于"外部提交结果未知"。

## 映射

### Growovo `core/error_semantics.py`

内部异常经 `classify()` 归族，用户可见输出由 `SemanticError` 携带四要素。

| 族 | 归入的现有异常 |
|---|---|
| `NEEDS_USER_INPUT` | `SOPError` |
| `UNSUPPORTED_CAPABILITY` | `PlatformError`、`ComplianceError` |
| `INVALID_INPUT` | `ContentUnusableError`、`PlaceholderContentError`、`DeliverableError`、`HandoffError`、`GuiError` |
| `PERMISSION_DENIED` | `PermissionDenied`、`IdentityBlocked` |
| `SAFETY_BLOCKED` | `QueueRunError`、`GateError`、`SuppressionIntegrityError`、`ApprovalError` |
| `STALE_OR_CONFLICT` | `LedgerLockError`、`BusyRun` |
| `CONTRACT_ERROR` | `CompileError`、`OrchestrationError`，以及未匹配的其他异常 |

### Verovo `buyer_ledger/error_semantics.py`

`close.py` 的 C-01 冻结码不新增、不改含义；映射模块只读，模块加载时校验覆盖完整性。

| 族 | C-01 码 |
|---|---|
| `NEEDS_USER_INPUT` | `INPUT_REQUIRED`、`REVIEW_REQUIRED` |
| `UNSUPPORTED_CAPABILITY` | `UNSUPPORTED_SCOPE`、`UNSUPPORTED_CAPABILITY` |
| `PERMISSION_DENIED` | `AUTH_REQUIRED`、`POLICY_DENIED` |
| `SAFETY_BLOCKED` | `RATE_LIMITED` |
| `STALE_OR_CONFLICT` | `STALE_SOURCE`、`INDETERMINATE_EXTERNAL_ACTION` |
| `CONTRACT_ERROR` | `ARTIFACT_PARTIAL`、`INTERNAL_ERROR` |
| `INVALID_INPUT` | 无（Verovo 的输入错误走 `INPUT_REQUIRED`） |

Askovo 本轮不改代码，不引入映射；其 CLI 仍以自身错误文本为准。族级词表不改变任何产品的判定结果。

## 冻结

- `Growovo/tests/test_cli_contract.py`：七族齐全、缺参拒绝带 `missing`、`queue run` 业务拒绝退出码为 2。
- `Verovo/buyer-ledger/tests/test_error_semantics.py`：每个 `ERROR_CODES` 成员都有家族；未知码回退 `CONTRACT_ERROR`；抽查映射。
- 新增错误码时先改本文件与映射，再改实现；覆盖校验失败会让模块导入直接报错。
