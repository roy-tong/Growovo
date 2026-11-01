# 可复用工作流（Workflow）

> 把一次人工确认过的操作，变成一个**有版本、有指纹、有运行日志**的配方；重放时优先走确定性定位器，页面变化时按语义层级恢复，恢复不了就安全停下。

Growovo 的执行内核本来就区分「计划 → 审批 → 队列/动作 → 账本」。工作流补的是中间那层：同一类任务不必每次都让模型重新探索页面、重新决定点哪里。工作流本身**不发消息**，它只描述步骤；真正的发送仍走 `action_runner`（`action` 步骤）或受控的 commit 点击（`click` + `commit: true`）。

## 1. 结构

```json
{
  "schema_version": "1.0",
  "id": "hn-reply",
  "name": "HN reply",
  "version": 1,
  "channel": "hn",
  "action": "reply",
  "source": {"kind": "observed", "ref": "trace.json"},
  "steps": [
    {"id": "s1", "kind": "open", "intent": "open item",
     "url": "https://news.ycombinator.com/item?id={{item}}"},
    {"id": "s2", "kind": "fill", "intent": "type reply", "value": "{{text}}",
     "locators": [
       {"strategy": "css", "value": "textarea[name=text]"},
       {"strategy": "placeholder", "value": "Comment"},
       {"strategy": "label", "value": "Comment"},
       {"strategy": "role", "role": "textbox", "name": "Comment"},
       {"strategy": "text", "value": "Comment"}
     ],
     "verify": {"readback": "{{text}}"}},
    {"id": "s3", "kind": "click", "intent": "submit", "commit": true,
     "target": "https://news.ycombinator.com/item?id={{item}}",
     "locators": [{"strategy": "css", "value": "input[type=submit]"}]}
  ],
  "exceptions": [{"match": "login|sign in", "branch": "stop", "reason": "login wall"}],
  "recovery": {"ladder": ["css", "placeholder", "label", "role", "text", "visual"],
               "visual": {"provider": "", "enabled": false}},
  "review": {"approved": false}
}
```

步骤类型：

| kind | 作用 | 是否写平台 |
| --- | --- | --- |
| `open` | 打开 URL（模板变量用 `{{name}}`） | 否 |
| `fill` | 定位输入框并填入，回读校验 | 否 |
| `press` | 按键（如 Enter） | 视页面而定 |
| `read` | 读取可见文本，供后续核对 | 否 |
| `click` | 点击；`commit: true` 时受暂停/预算/账本约束 | 可能 |
| `action` | 交给 `action_runner` 的标准写路径 | 是（走原门禁） |

## 2. 定位器阶梯

`core/locators.py` 按固定顺序尝试：`css → placeholder → label → role → text → visual`。

- 某个策略找不到、不可见或报错，继续下一层，并记录 `tried`；
- `visual` 是**操作者提供的回调**（Stagehand / Midscene 或自建视觉定位都可以接），本仓库不内置视觉模型；
- 没有配置视觉回调时，命中 visual 会以 `visual_unavailable` 停止，而不是猜一个坐标点下去；
- 全部失败抛出 `LocatorUnresolved`，运行记为 `locator_unresolved`。

这是「语义恢复」的真实边界：语义定位可用，视觉恢复需要外部 provider，二者都不会被伪装成同一件事。

## 3. 学习

```bash
python3 mkt.py workflow learn hn-reply --trace trace.json
```

trace 是操作者（或观察一次的 Agent）记录下来的步骤：

```json
{"channel": "hn", "action": "reply",
 "steps": [
   {"intent": "open item", "kind": "open", "url": "https://news.ycombinator.com/item?id={{item}}"},
   {"intent": "type reply", "kind": "fill", "value": "{{text}}",
    "locators": [{"strategy": "role", "role": "textbox", "name": "Comment"},
                 {"strategy": "css", "value": "textarea"}]}
 ]}
```

学习只生成**草稿**，按阶梯顺序整理 locators，不会自动审批。

## 4. 审批

```bash
python3 mkt.py workflow approve hn-reply --by "operator" --days 30
```

- 审批写 `workflow_sha256` 指纹（排除 `review` 块）与有效期；
- 任何步骤、定位器或模板改动都会让指纹失配，`--go` 直接被拒；
- 审批事件追加到 `data/workflow-approvals.jsonl`，可审计；
- MCP 只能读工作流，不能审批或重放。

## 5. 重放

```bash
python3 mkt.py workflow replay hn-reply --var item=123 --var text="..."          # dry-run：只解析定位器
python3 mkt.py workflow replay hn-reply --cdp-url http://127.0.0.1:9222 ... --go  # 实跑
```

- dry-run 不填、不点、不发，只报告每一步用哪个策略定位成功；
- `action` 步骤调用 `action_runner.run_action`，沿用暂停、审批、抑制名单、UNVERIFIED 门槛与账本；
- `commit` 点击在执行前检查全局暂停与当日预算，点击后写账本（`transport=workflow-ui`，结果 `committed`）；点击抛错记为 `indeterminate`；
- UI 步骤需要调试浏览器（`--cdp-url`），没有浏览器直接报错，不降级成猜测。

## 6. 运行日志与人工裁决

每次运行写 `data/workflows/runs/<run_id>.json`：

- 每一步先记 `running` 再执行，成功记 `done`，失败记 `failed`，外部结果未知记 `unknown`；
- 出现 `indeterminate` 或中断的 `running` 时，同一工作流的后续 `--go` 会被拒绝；
- 人工核实后用：

```bash
python3 mkt.py workflow reconcile --run <run_id> --outcome sent|not_sent --note "已核对平台后台" --by "operator"
```

工作流仍**不保证** exactly-once：进程在点击与回写之间被杀死时，只能靠人工裁决，不能自动重发。

## 7. 边界

- 不做通用爬虫，不做登录、验证码、风控绕过；
- 视觉恢复需要外部 provider，本仓库不内置；
- 工作流审批的是「步骤与定位器」，不是业务正确性；内容仍需人审；
- 平台动作完成不等于业务转化，账本结果与业务结果分开记录；
- 每个渠道的验证等级（PROVEN/PORTED/UNVERIFIED）不变，工作流不升级渠道等级。
