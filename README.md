# mkt-automation

自动化营销组件服务：**目标驱动**（业务目标→plan.json→客群→波次）、多渠道（X / Email / Reddit / LinkedIn，可扩展）、**多通道**（API / browser-cdp / os-input 键鼠模拟 / protocol SMTP）、零凭据优先。
统一六原语（publish / reply / message / engage / search / inbox）、统一写路径事务管道、统一账本。
三种外壳共用本内核：**CLI（mkt.py）**／**MCP（mcp_server.py）**／**Skill（skills/marketing-ops）**。

> 产品方案见项目目录 `05-产品方案-*.md`；执行架构见 `02-架构设计-*.md`。

## 合规与纪律（先读这个）

- 仅用于**本人拥有/运营的账号**；不碰他人账号、不做代运营自动化
- CDP 通道零凭据：不保存任何密码/token/Cookie；SMTP 凭据只走环境变量
- **一切写操作默认 dry-run，`--go` 才实跑**
- 失败即停（不重试轰炸）；回读校验不一致绝不发送（449 空正文事故的固化）
- 人类节奏：随机间隔 + 单次硬上限 + 日预算（core/throttle.py 的表）
- 不群发：千人千面内容为前提；占位符残留会被门禁直接拒绝
- 不做对抗：不绕验证码、不伪造指纹、不用代理池

## 安装与启动

```bash
pip install -r requirements.txt   # 仅 playwright；CDP 走系统 Chrome，无需下载浏览器
./launch.command                  # 双击亦可：首次复制登录 profile，启动 CDP 9222
python3 mkt.py check              # 确认 CDP 会话与 X/Gmail 登录态
```

## 命令矩阵与验证等级

| 命令 | 说明 | 等级 |
|---|---|---|
| `python3 mkt.py status` | 全局账本聚合 | — |
| `python3 mkt.py check` | CDP 会话检查（X/Gmail/Reddit/LinkedIn） | — |
| `python3 mkt.py report [--hours 24]` | 一页纸运营报告（异常/暂停态/抑制名单） | — |
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

等级含义：PROVEN=真实账号跑通；PORTED=模式已验证、本封装未整跑；UNVERIFIED=选择器未实测，首跑必须人工盯跑（首跑清单见 03 渠道文档 §5）。

## 规划层：计划是数据，人审是闸门（05 产品方案 §2）

```bash
# 1. agent/人 按 data/schemas.md 的 plan schema 写 data/plans/<plan_id>.json
# 2. 校验（schema/能力矩阵/size≤内核日预算/判据预注册）
python3 -c "from core.planlint import load_plan, lint_plan; print(lint_plan(load_plan('data/plans/x.json')))"
# 3. 人审通过后置 review.human_approved=true —— 否则 send_queue 与 MCP 都拒跑该 campaign
# 4. 生成 data/queues/<campaign>.json 并执行（见上表）
```

上限是内核属性：计划里的 size 超过 `core/throttle.py` 日预算会被 lint 拒收。

## MCP 服务（mcp_server.py，stdio，零依赖）

```json
{"mkt": {"command": "python3", "args": ["/绝对路径/mkt-automation/mcp_server.py"]}}
```

6 个工具：`mkt_status` / `mkt_channels` / `mkt_plan_lint` / `mkt_search` / `mkt_ledger_query`（只读）+ `mkt_run_queue`（写，默认 dry-run；UNVERIFIED 渠道拒实跑；未经人审的 plan 拒跑）。只暴露队列粒度写操作——没有绕过留痕的路径。

## Skill（skills/marketing-ops/SKILL.md）

agent 操作协议：目标→plan→客群→人审→执行→复盘七步。装入 `~/.agents/skills/` 即可在 ZCode 等宿主触发（内容协作走 warm-email-outreach / expert-review-outreach / b2b-account-mapping）。

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

## 数据三件套（`data/`，gitignore）

- `queues/<campaign>.json` 队列；`ledger.jsonl` 全局账本；`contacts.jsonl` 线索库
- `suppress.txt` 抑制名单（每行一个目标）；`shots/` 留证；`state/` 日预算状态
- schema 详见 `data/schemas.md`

## 结构

```
core/      ledger 账本 · gates 门禁 · throttle 节流 · pipeline 写路径事务 · evidence 留证 · cdp 会话 · transports 通道注册表 · planlint 计划校验/人审强制
channels/  x/(vendor+adapter) · email_smtp · email_cdp · reddit · linkedin
transports/  os_input（macOS 键鼠/AX 模拟，UNVERIFIED）
skills/    marketing-ops（agent 操作协议）
tests/     离线单测（33 项，含 449 事故回归与人审门禁）：python3 -m unittest discover tests -v
mcp_server.py  MCP 外壳（stdio JSON-RPC，零依赖）
```

X 渠道 vendor 引入 x-ops-automation（上游 github: roy-tong/x-ops-automation），
**不改写已验证代码**（见 `channels/x/vendor/PROVENANCE.md`）；上游修选择器时同步。

## License

MIT。
