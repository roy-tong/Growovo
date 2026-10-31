<p align="center">
  <img src="docs/assets/logo.png" width="400" alt="Growovo">
</p>

<p align="center"><strong>把重复运营工作交给 Agent，留下可核验的执行记录。</strong><br>
<em>Execute authorized recurring operations with reviewable results.</em></p>

<p align="center"><strong>简体中文</strong> · <a href="README.en.md">English</a></p>

Growovo 面向维护产品社区、内容渠道和客户沟通的运营人员。输入工作目标、授权账号与任务清单，先预览和审核，再执行重复操作；每项动作记录成功、失败或待人工核实，支持暂停与后续处理。

产品由 **Operator Engine（操作内核）** 和 **Playbooks（业务工作流）** 组成。当前可用的 GitHub Inbox Playbook 把问题列表分流并生成回复队列；内容、邮件和增长活动复用同一执行内核。CLI、MCP、Skill 共用审批、预算、回读和账本。发送成功只代表操作完成，注册、付费等业务结果单独记录。

[![tests](https://github.com/roy-tong/Growovo/actions/workflows/tests.yml/badge.svg)](https://github.com/roy-tong/Growovo/actions/workflows/tests.yml)

**先试一个结果：** `python3 mkt.py demo` 在本地完成计划校验、审批、dry-run 和账本演示，无需账号或网络。实际平台命令的验证等级见下表。[能力进度与技术取舍](docs/feature-status.md)。

- **Askovo** 负责业务研究与验证，为任务提供依据。
- **Growovo** 执行授权运营任务，记录操作结果。
- **Verovo** 关联费用与业务结果，计算单任务经济性。

三者可独立使用。`mkt.py` 与 `growovo-gtm` 标识保持兼容，GTM 是首批应用场景。

## 合规与纪律（先读这个）

- 仅用于**本人拥有/运营的账号**；不碰他人账号、不做代运营自动化
- CDP 通道零凭据：不保存任何密码/token/Cookie；SMTP 凭据只走环境变量
- **一切写操作默认 dry-run，`--go` 才实跑**；实跑必须绑定**已人审 plan**——无 plan 管辖默认拒跑，仅 CLI 人工通道 `--adhoc` 显式放行（账本记 adhoc=true；MCP 不开放）
- 日预算在发送前原子预留；`indeterminate`（未知结果）不可自动重发，必须人工 `ledger resolve`；contacts 的 DNC/bounced 身份与 `suppress.txt` 合并为同一发送抑制源
- 失败即停（不重试轰炸）；回读校验不一致绝不发送（449 空正文事故的固化）
- 人类节奏：随机间隔 + 单次硬上限 + 日预算（core/throttle.py 的表）
- 不群发：千人千面内容为前提；占位符残留会被门禁直接拒绝
- 不做对抗：不绕验证码、不伪造指纹、不用代理池

## 安装与启动

```bash
pip install -r requirements.txt   # 仅 playwright；CDP 走系统 Chrome，无需下载浏览器
python3 mkt.py init               # 建目录 + 报凭据缺口 + 打印下一步
python3 mkt.py demo               # 零网络零账号，30 秒过内核四步：lint→人审→写路径→账本
python3 mkt.py doctor             # 环境体检（--selftest 连跑全部离线单测）
./launch.command                  # 双击亦可：首次复制登录 profile，启动 CDP 9222
python3 mkt.py check              # 确认 CDP 会话与 X/Gmail 登录态
```

## 命令矩阵与验证等级

| 命令 | 说明 | 等级 |
|---|---|---|
| `python3 mkt.py status` | 全局账本聚合 | — |
| `python3 mkt.py check` | CDP 会话检查（X/Gmail/Reddit/LinkedIn） | — |
| `python3 mkt.py demo` | 零网络零账号演示（计划校验→人审闸门→写路径 dry-run→账本，沙盒可删） | — |
| `python3 mkt.py doctor [--selftest]` | 环境体检：内核导入/数据契约/凭据/CDP/暂停态 | — |
| `python3 mkt.py report [--hours 24] [--format md]` | 一页纸运营报告（异常/暂停态/抑制名单） | — |
| `python3 mkt.py init` | 工作区初始化：建目录/报凭据缺口/给下一步（`--workspace` 或 `MKT_HOME` 隔离多产品） | — |
| `python3 mkt.py next` | 状态机：现在该做什么、为什么 | — |
| `mkt plan new/lint/show/list/approve/revoke` | **计划与审批**：脚手架、校验、带署名的审批事件（署名 + 计划指纹 + 有效期） | — |
| `mkt queue lint <file>` / `mkt queue build` | **编辑期门禁**：占位符/重复/长度在写队列时拦；模板 + CSV 生成千人千面队列 | — |
| `mkt campaign show/report/next` | **Campaign 视图**：计划/人审/队列预检/账本/事件/实验/下一步 → 一页 Markdown | — |
| `mkt review <campaign>` | 人审预览：队列逐条过门禁 + 已有留证 + 批准与执行命令 | — |
| `mkt ledger resolve <ref> --result … --by … --note …` | **indeterminate 人工裁决**（append-only 覆盖，原账本行不改） | — |
| `mkt link create/list` | **追踪链接**：生成带 utm_campaign 的链接并落盘，供事件归位 | — |
| `mkt outcome import <csv>` / `mkt outcome sync` | **记账自动化**：CSV 导入（幂等）+ 账本聚合成 count 快照 | — |
| `mkt queue run <file> [--go]` | **全渠道队列执行**（email/hn/github/reddit/linkedin/x-reply），逐条走同一管道 | — |
| `mkt action <channel> <action> …` | 单条写动作（与队列同一套门禁/审批/账本）；x message/engage 透传 vendor | — |
| `mkt collect serve/event/import` | **本地 collector**：`/t/<link>` 点击归因、`/events` webhook、Plausible/PostHog CSV | — |
| `mkt experiment obs --id X --from-outcomes` | 实验观察从事件台账自动聚合（不再手抄） | — |
| `python3 mkt.py pause --on '原因' / --off` | **全局暂停总闸**（一切写操作拒跑且留痕） | — |
| `python3 mkt.py contacts add/find/dnc/mark/import` | 线索库操作（幂等去重/DNC 隐藏/er-batch 导入） | — |
| `mkt x publish/reply/message/engage/search/inbox …` | X 全动作（vendor x-ops-automation） | **PROVEN** |
| `mkt email message --queue data/queues/w1.json [--go]` | SMTP 发信队列 | **PORTED** |
| `mkt email inbox` / `mkt email bounces` | CDP 只读扫 Gmail / 退信 | **PORTED**（模式 PROVEN） |
| `mkt reddit search --query "…"` | 只读搜索 | **UNVERIFIED** |
| `mkt reddit reply --url <帖> --text "…" [--go]` | 评论（回读校验管道） | **UNVERIFIED** |
| `mkt reddit publish --sub <版块> --title … --body-file … [--go]` | 发帖（日 ≤3） | **UNVERIFIED** |
| `mkt linkedin search --keywords "…"` | 只读搜人 | **UNVERIFIED** |
| `mkt linkedin message --url <profile> --note-file … [--go]` | 半自动连接请求（终端确认） | **UNVERIFIED** |
| `mkt github audit --repo owner/repo` | **仓库体检**（基础/社区健康/releases/贡献者集中度/AI-readiness/Scorecard） | **PROVEN** |
| `mkt github inbox --repo owner/repo` | open issues/PR 清单 + 零回复标记（48h 首响巡检） | **PROVEN** |
| `mkt github search --query "…"` | 仓库搜索（客群/对标发现） | **PROVEN** |
| `mkt github reply --repo o/r --number N --text "…" [--go]` | issue/PR 评论（api 通道写路径首例；需 `MKT_GITHUB_TOKEN`） | **UNVERIFIED** |
| `mkt playbook github-inbox --repo o/r --drafts drafts.json` | 收件箱分流→候选回复→标准队列（不发送；仍需审批/dry-run） | **PORTED** |
| `mkt hn search --query "…"` | HN 讨论/客群发现（Algolia 公共 API，匿名只读） | **PROVEN**（只读） |
| `mkt hn publish --title "Show HN: …" --url … [--go]` | submit 表单提交（CDP；日≤1，标题≤80；Show HN 限自有项目） | **UNVERIFIED** |
| `mkt hn reply --item item:N --text "…" [--go]` | 条目评论（首用=Show HN 首条作者评论，提交后 1h 内） | **UNVERIFIED** |

等级含义：PROVEN=真实账号跑通；PORTED=模式已验证、本封装未整跑；UNVERIFIED=选择器未实测，首跑必须人工盯跑（首跑清单见 03 渠道文档 §5）。

## 规划层：计划是数据，人审是闸门（05 产品方案 §2）

```bash
# 1. 生成计划脚手架（也可直接按 data/schemas.md 手写）
mkt plan new --id askovo-p0 --goal '拿到 20 个激活' --beachhead '独立 UX 研究者' \
  --hypothesis '证据分级是购买动机' --channel email --wave-action message --size 10

# 2. 校验（schema/能力矩阵/size≤内核日预算/判据预注册）
mkt plan lint --id askovo-p0

# 3. 人审：必须署名；写审批事件 + 计划指纹（批准后再改计划会被指纹识别并拒跑）
mkt plan approve askovo-p0 --by roy --days 7

# 4. 队列：模板 + 目标 CSV 生成，或手写后用 lint 过门禁
mkt queue build --campaign askovo-p0-w1 --channel email --wave-action message \
  --template assets/outreach.md --targets data/targets.csv
mkt queue lint data/queues/askovo-p0-w1.json

# 5. 人审预览 → 干跑 → 实跑
mkt review askovo-p0-w1
mkt email message --queue data/queues/askovo-p0-w1.json          # dry-run
mkt email message --queue data/queues/askovo-p0-w1.json --go     # 须已批准

# 6. 记账与复盘
mkt link create --campaign askovo-p0-w1 --channel hn --url https://example.com
mkt outcome import data/events.csv --campaign askovo-p0-w1
mkt outcome sync --campaign askovo-p0-w1
mkt campaign report askovo-p0-w1
```

计划指纹 = 计划内容（不含 review 块）的规范化 sha256；旧计划只有 `human_approved=true`
时仍按 legacy 放行，新审批一律带指纹与有效期。

上限是内核属性：计划里的 size 超过 `core/throttle.py` 日预算会被 lint 拒收。

## MCP 服务（mcp_server.py，stdio，零依赖）

```json
{"mkt": {"command": "python3", "args": ["/绝对路径/Growovo/mcp_server.py"]}}
```

10 个工具，全渠道读写：

- 读：`mkt_status` / `mkt_channels` / `mkt_plan_lint` / `mkt_search`（x/reddit/linkedin/hn/github）/ `mkt_ledger_query` / `mkt_campaign` / `mkt_inbox`（hn/github/email/x）/ `mkt_audit`（github）
- 写：`mkt_run_queue`（全渠道队列，默认 dry-run）/ `mkt_action`（单条动作，同一套门禁/审批/账本）

写操作实跑一律要求已人审 plan；UNVERIFIED 渠道需 `allow_unverified=true`（人工首跑校准）；adhoc 仅 CLI 人工通道，MCP 不开放。MCP 进程同样读 `MKT_HOME`，可与 CLI 用不同工作区。

## Skill（skills/）

- `skills/growovo-gtm/SKILL.md`：**GTM 操作协议**——入口是增长目标（注册/激活/付费）而非单个渠道动作，按四引擎（定位→资产→投放→结果）拆解为七步：目标→plan→客群→人审→执行→复盘。装入 `~/.agents/skills/` 即可在 ZCode 等宿主触发（内容协作走 warm-email-outreach / expert-review-outreach / b2b-account-mapping）。
- `skills/github-project-ops/`：**GitHub 项目经营协议**（开源与闭源通用）——定位诊断（四象限+七阶段）→ 仓库体检清单 → 增长 playbook → 闭源经营模式库；含独立体检脚本 `scripts/repo_audit.py`（零依赖发行版）。与 CLI 的关系：skill 是协议与判断，`mkt github *` 是采数与执行。

## 通道层（core/transports.py）

ACTIVE×4：`api`（OAuth，Reddit 二期）｜`browser-cdp`（PROVEN）｜`os-input`（macOS 键鼠/AX 模拟，UNVERIFIED，transports/os_input.py）｜`protocol`（SMTP/IMAP，PROVEN）。
RESERVE×6（通道储备库）：`webdriver`｜`rss-public`（只读）｜`esp-api`（事务邮件）｜`mobile-appium`｜`integration`（平台原生）｜`semi-auto-ui`（人工确认兜底）。
每渠道×动作声明首选+降级链；**降级用于"路不通"，不用于"绕风控"**——风控信号的响应是降节奏或 `mkt pause`，不是换马甲。风控阈值与账号限额见项目 06 文档（官方口径亲核；代码预算一律低于官方上限，见 throttle.INTERVALS/OFFICIAL_LIMITS）。

## 环境变量

| 变量 | 用途 |
|---|---|
| `MKT_CDP_URL` | CDP 端点（默认 http://127.0.0.1:9222） |
| `MKT_EMAIL_USER` / `MKT_EMAIL_APP_PASSWORD` | Gmail SMTP（App Password，绝不入库） |
| `MKT_LINKEDIN_AUTO=1` | 解锁 LinkedIn 全自动（不建议；默认半自动需终端确认） |
| `MKT_GITHUB_TOKEN` | GitHub PAT（仅写操作 reply 需要；读原语匿名可用，限额 60 次/时） |

## 数据三件套（`data/`，gitignore）

- `queues/<campaign>.json` 队列；`ledger.jsonl` 全局账本；`contacts.jsonl` 线索库
- `suppress.txt` 抑制名单（每行一个目标）；`shots/` 留证；`state/` 日预算状态
- schema 详见 `data/schemas.md`

## 结构

```
core/      ledger 账本（含人工裁决覆盖）· gates 门禁（含 DNC 合并）· throttle 原子日预算 · pipeline 写路径事务 · evidence 留证 · cdp 会话 · transports 通道注册表 · planlint 计划校验 · approval 审批事件/计划指纹 · queue 编辑期门禁 · campaign 视图/人审预览/下一步 · links 追踪链接 · outcome_io CSV 导入与账本聚合 · collector 本地 webhook/跳转 · action_runner 单条动作 · queue_runner 全渠道队列 · playbooks GitHub Inbox 分流/草稿队列 · workspace 多产品隔离 · experiments 预注册实验 · attribution 事件/漏斗/归因 · demo 零账号演示 · doctor 环境体检
channels/  x/(vendor+adapter) · email_smtp · email_cdp · reddit · linkedin · github（api 通道首发） · hn（CDP 写 + Algolia 读）
transports/  os_input（macOS 键鼠/AX 模拟，UNVERIFIED）
skills/    growovo-gtm（GTM 操作协议）· github-project-ops（GitHub 项目经营协议+体检脚本）
tests/     离线单测（118 项，含 449 事故回归、全渠道人审门禁/adhoc 放行、审批指纹与过期、预算原子预留、DNC/indeterminate 阻断、队列编辑期门禁、CSV 导入幂等、账本裁决覆盖、collector 跳转/事件鉴权、MCP stdout 洁净、GitHub Inbox Playbook 分流/草稿队列、漏斗身份归一与时序披露、价值事件核验、实验冻结与预注册防篡改、demo 闭环与 doctor 分级）：python3 -m unittest discover tests -v
mcp_server.py  MCP 外壳（stdio JSON-RPC，零依赖）
```

X 渠道 vendor 引入 x-ops-automation（上游 github: roy-tong/x-ops-automation），
**不改写已验证代码**（见 `channels/x/vendor/PROVENANCE.md`）；上游修选择器时同步。

## License

MIT（见 [LICENSE](LICENSE)）。`channels/x/vendor/` 引入自同作者仓库 x-ops-automation（MIT）。

## Operations memory: learn from verified platform receipts

Inspect recurring channel/action patterns without exposing contact details or automatically executing any past action:

```bash
python3 mkt.py memory --campaign my-campaign
python3 mkt.py memory --channel github --output data/operations-memory.json
```

The summary reports confirmed platform operations, unknown/failing receipts and **human-review candidates**. It intentionally excludes targets and message bodies. A successful platform action is **not** evidence of customer activation, and a pattern is never permission to replay: new runs still require fresh authorization, current platform-policy checks and the standard execution gates. This is a read-only foundation for reusable playbooks, not semantic/visual UI learning. See [feature status](docs/feature-status.md).
