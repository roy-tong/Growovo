# CDP 账号观测与操作日志（2026-11-17）

用户提供已登录的浏览器（CDP 回环端点，默认 `http://127.0.0.1:9222`），Growovo 不要求预先绑定账号，也不保存密码、Cookie 或 token；真实执行前读取**当前浏览器实际登录的账号**。当前该身份观测只覆盖 **X**。

## 启用与查看

```bash
mkt policy enable --by "<你的名字>" --actions x:reply --days 30 --observe-account
mkt grow arm g1 --by "<你的名字>"

mkt account observe --channel x     # 只读探测；写 data/identity/probes.jsonl
mkt account log --limit 20          # 读 data/operations.jsonl
mkt account log --limit 20 --json   # 查看完整字段、实际账号 proof 与 receipt
```

`--observe-account` 让 policy 进入 `identity_mode: cdp_observed`：

- 不要求 `account_identities` 预绑定；
- 真实发送前必须从 CDP 读到平台稳定 ID；读不到、过期或渠道不符都拒绝（fail closed）；
- 不校验“是不是预绑定的那个账号”，只记录“这次实际是哪个账号”；
- 队列级预检之后，每个条目进入 `action_runner` 时仍重新观测，不能拿第一条的身份覆盖第二条。

`account log` 优先读取统一操作日志 `data/operations.jsonl`。旧工作区没有该文件时回退到 `data/ledger.jsonl`，不修改任何历史行。

## 三份本地证据的分工

| 文件 | 回答的问题 | 不能证明什么 |
|---|---|---|
| `data/operations.jsonl` | 这次动作由谁执行、关联哪个 run/campaign/queue item、实际账号是谁、回执和回读引用是什么 | 不能单独证明平台真的接受了动作，更不能证明业务转化 |
| `data/ledger.jsonl` | 是否已 committed、是否需要防重发、是否处于 indeterminate/no-retry | 不是完整操作上下文 |
| `data/identity/events.jsonl` / `probes.jsonl` | 身份门禁允许/拒绝及探测分类 | 登录态或 handle 本身不是稳定账号证明 |

操作日志是 **append-only**，schema 为 `growovo.operations/2`。它复用账本的 `O_APPEND + flock + 单次 write` 追加方式，不覆盖旧行。缺失的可选关联或证据写 JSON `null`，不写空串冒充证据；没有错误时 `error: null`，没有工件时 `artifacts: []`。

## 操作日志 schema

每条从执行器返回的动作记录包含以下最低字段：

```json
{
  "schema": "growovo.operations/2",
  "kind": "operation",
  "operation_id": "x:campaign-001:reply:abc123::https://x.com/target/status/2",
  "run_id": "run-001",
  "campaign": "campaign-001",
  "queue_item_id": "item-002",
  "channel": "x",
  "action": "reply",
  "target": "https://x.com/target/status/2",
  "content_sha256": "<精确发送正文的 SHA-256>",
  "browser_context_ref": "cdp:http://127.0.0.1:9223",
  "account": {
    "platform_user_id": "100002",
    "authorized_account": null,
    "handle": "account_b",
    "observed_at": "2026-11-17T09:00:02+00:00",
    "proof_ref": "http://127.0.0.1:9223/json#target-2@verify_credentials",
    "switched_from": {
      "platform_user_id": "100001",
      "handle": "account_a",
      "operation_id": "<上一条操作的 operation_id>"
    }
  },
  "operator": "queue:switch-campaign.json",
  "approval_ref": "routine-policy",
  "policy_sha256": "<执行时 policy 文件的 SHA-256>",
  "started_at": "2026-11-17T09:00:02+00:00",
  "finished_at": "2026-11-17T09:00:03+00:00",
  "result": "committed",
  "receipt": {
    "id": "179100002",
    "url": "https://x.com/account_b/status/179100002",
    "readback_sha256": "<实际回读正文的 SHA-256>"
  },
  "artifacts": [
    {"path": "<vendor 原始结果 JSON 路径>", "sha256": "<该文件的 SHA-256>"}
  ],
  "error": null
}
```

字段约束：

- `operation_id` 优先使用本次账本 `ref`，没有时生成局部唯一 ID；它用于连接账本行、操作日志和回执，不是业务结果 ID。
- `run_id`、`campaign`、`queue_item_id` 按入口能取得多少写多少；单动作没有 run 或 queue item 时为 `null`。
- `account` 只写本条动作观测到的稳定平台 ID、handle、观测时间和 proof 引用。A→B 切换时，第二条增加 `account.switched_from` 指向 A；第一条不改，两条的 receipt 仍各属于各自动作。
- `receipt.id/url/readback_sha256` 是平台回执与回读引用；`artifacts[].path/sha256` 指向本地原始结果文件。缺哪项就写 `null`，不根据 handle 猜 receipt。
- `receipt.readback_sha256` 与 `content_sha256` 不一致，或显式 `readback.status=mismatched` 时，操作结果降为 `indeterminate`，`error.code=readback_mismatch`、`retryable=false`，恢复入口是人工核对后 `mkt ledger resolve`；禁止自动重发。
- `error.recovery_ref` 记录可取得的恢复引用；没有则 `null`。前置门禁在动作返回前直接抛错时，不写伪造的动作回执，身份拒绝原因留在 `identity/events.jsonl`。
- 操作日志拒绝 `cookie/token/password/secret/credential/authorization` 等凭据字段。环境里的 CDP URL 只保留校验后的回环 origin，不写 URL 查询串、userinfo 或凭据；环境没有 URL 时，沿用身份门禁生成的不含凭据 target/context 引用。

## 门禁与本地测试边界

- 该模式只改变“实际账号从哪里来”，不改变 dry-run、pause、quota、suppression、审批、readback、去重和 indeterminate 人工裁决。
- X 的 committed 操作日志必须有观测到的 `account.platform_user_id`；缺身份时真实发送在 vendor 之前 fail closed。
- 重复执行仍由原账本/claim 门禁处理。操作日志不会把重复意图改成可重试，也不会清除 indeterminate。
- 离线测试使用注入 `AccountIdentityProof` 和本地假 vendor，覆盖连续 A→B、receipt 对账、缺身份零 vendor 调用及重复意图零新增发送。**这些只证明本地代码路径和状态机，不等于 live-proven，也不证明真实平台权限、真实账号切换或外部送达。**
- 测试不发送真实账号内容、不读取或修改 `profiles/`、不联网。页面挑战/验证码仍必须停止，不做对抗。

## 仍需人工 live 验收

以下步骤必须由有权操作真实账号的人显式执行；仓库测试不会自动执行：

1. 在用户自管的隔离浏览器环境中登录专用测试账号 A，使用独立回环 CDP 端口和专用测试对象，重新签发最小范围的 `cdp_observed` 授权；先 dry-run 核对目标与正文。
2. 由人显式执行一次低风险真实动作，确认稳定平台 ID、proof_ref、receipt ID/URL 和实际回读正文哈希都来自同一个浏览器上下文。
3. 在同一浏览器环境中由人切换到专用测试账号 B，再对另一个专用测试对象执行第二个动作；确认 B 是平台实际账号，而不是只改了本地 `account` 参数。
4. 用 `mkt account log --json` 对照 `operations.jsonl` 与 `ledger.jsonl`：两条 `operation_id/ref` 一一对应，A/B 不串号，第二条 `switched_from` 指向 A，receipt URL 所属账号与各自 proof 一致。
5. 对第一个已完成条目再次发起相同执行意图，确认原幂等门在 vendor 前拒绝且没有第二条平台动作；另做一次身份不可读检查，确认零发送。不要为验收制造重复触达。
6. 人工保存真实平台回读与本地 artifact 哈希的核对结果。只有这些真实证据验收通过后，才能另行更新 live 状态；当前仍是 **live 未验收**。
