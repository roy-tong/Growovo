# CDP 账号观测与操作日志（2026-11-16）

用户提供已登录的浏览器（CDP 回环端点，默认 `http://127.0.0.1:9222`），Growovo 不要求预先绑定账号，也不保存密码/Cookie/token；执行时读取**当前浏览器实际登录的账号**并写入操作日志。

## 启用

```bash
mkt policy enable --by "<你的名字>" --actions x:reply --days 30 --observe-account
mkt grow arm g1 --by "<你的名字>"
```

`--observe-account` 让 policy 进入 `identity_mode: cdp_observed`：

- 不要求 `account_identities` 预绑定；
- 真实发送前必须从 CDP 读到平台稳定 ID；读不到就拒绝（fail closed）；
- 不校验“是不是授权过的那个账号”，只记录“这次实际是哪个账号”。

## 读取与查看

```bash
mkt account observe --channel x     # 只读探测当前浏览器账号，写 data/identity/probes.jsonl
mkt account log --limit 20          # 账本操作日志：时间/渠道/动作/结果/实际账号
```

每次动作的账本行会带：

```json
{
  "channel": "x",
  "action": "reply",
  "result": "committed",
  "account": "acct-a",
  "identity": {
    "enforced": true,
    "mode": "cdp_observed",
    "observed_platform_identity": "<平台稳定 ID>",
    "handle": "<平台 handle，若可读>",
    "identity_proof_ref": "<CDP 页面/来源引用>"
  }
}
```

## 边界

- 当前只覆盖 **X** 的 CDP 账号身份（`core/account_identity.py` 的 `IDENTITY_CHANNELS`）。Email 走 SMTP 配置，GitHub/Reddit/HN/LinkedIn 等仍走各自通道，不在本模式承诺范围内。
- 只连接回环 CDP；不接收远程端点，不读凭据。
- 页面出现挑战/验证码时停止；不做对抗。
- `account log` 读的是追加式账本；不修改历史行。
- 该模式改变的是“账号来源”，不改变暂停、配额、审批、回读、去重、DNC、indeterminate 人工裁决等安全门。
