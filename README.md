# Growovo

**Agent-native GTM Operator · From Product to Customers.**

> **定 → 造 → 跑**：输入一个已发布的产品，交付可验证的真实用户增长（注册、激活、付费），中间每一步带证据。

**演进说明**：本仓库由 marketing-ops-automation 进化而来。原有执行内核完整保留、继续可用，并构成 Growovo 的 Growth Orchestrator 与治理层：

- 统一六原语（publish / reply / message / engage / search / inbox）、统一写路径事务管道（门禁→节流→回读校验→截图→dry-run 停点→发送→确认→账本）、统一账本
- 三种外壳共用本内核：**CLI（mkt.py）**／**MCP（mcp_server.py）**／**Skill（skills/marketing-ops + skills/github-project-ops）**
- 多渠道（X / Email / Reddit / LinkedIn / **GitHub** / **HN**，可扩展）、**多通道**（API / browser-CDP / OS 键鼠模拟 / SMTP 等四类十种，含降级储备）、零凭据优先
- GitHub 渠道承载**项目经营管线**：audit 体检 → inbox 零响应巡检 → reply 社区响应（见 skills/github-project-ops）

在此之上，Growovo 将逐步构建：**定**（产品理解、ICP、定位、渠道策略）、**造**（增长资产工厂）、**量**（归因与实验）。证据契约复用 [askovo/SURE](https://github.com/roy-tong/askovo) 的 E0–E5 证据分级与 claim 分型。当前处于演进早期：`mkt` 命令、数据契约与全部文档保持可用，新能力按 P0 dogfood 节奏推进。

> 内核纪律一句话（不变）：agent 当参谋（出方案，产出可校验的数据工件）、内核当执行（守门禁，上限是内核属性）、人当闸门（未经人审的计划拒跑）。
> 仓库内 `data/schemas.md` 自含全部数据契约。上游是 [research-to-publish](https://github.com/roy-tong/research-to-publish)（研究 → 文章 → 公众号/小红书交付包），其交付包与文章链接进本内核队列执行发布与互动；X 渠道 vendor 自已归档的 [x-ops-automation](https://github.com/roy-tong/x-ops-automation)。

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
| `mkt github audit --repo owner/repo` | **仓库体检**（基础/社区健康/releases/贡献者集中度/AI-readiness/Scorecard） | **PROVEN** |
| `mkt github inbox --repo owner/repo` | open issues/PR 清单 + 零回复标记（48h 首响巡检） | **PROVEN** |
| `mkt github search --query "…"` | 仓库搜索（客群/对标发现） | **PROVEN** |
| `mkt github reply --repo o/r --number N --text "…" [--go]` | issue/PR 评论（api 通道写路径首例；需 `MKT_GITHUB_TOKEN`） | **UNVERIFIED** |
| `mkt hn search --query "…"` | HN 讨论/客群发现（Algolia 公共 API，匿名只读） | **PROVEN**（只读） |
| `mkt hn publish --title "Show HN: …" --url … [--go]` | submit 表单提交（CDP；日≤1，标题≤80；Show HN 限自有项目） | **UNVERIFIED** |
| `mkt hn reply --item item:N --text "…" [--go]` | 条目评论（首用=Show HN 首条作者评论，提交后 1h 内） | **UNVERIFIED** |

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
{"mkt": {"command": "python3", "args": ["/绝对路径/marketing-ops-automation/mcp_server.py"]}}
```

6 个工具：`mkt_status` / `mkt_channels` / `mkt_plan_lint` / `mkt_search` / `mkt_ledger_query`（只读）+ `mkt_run_queue`（写，默认 dry-run；UNVERIFIED 渠道拒实跑；未经人审的 plan 拒跑）。只暴露队列粒度写操作——没有绕过留痕的路径。

## Skill（skills/）

- `skills/marketing-ops/SKILL.md`：agent 操作协议，目标→plan→客群→人审→执行→复盘七步。装入 `~/.agents/skills/` 即可在 ZCode 等宿主触发（内容协作走 warm-email-outreach / expert-review-outreach / b2b-account-mapping）。
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
core/      ledger 账本 · gates 门禁 · throttle 节流 · pipeline 写路径事务 · evidence 留证 · cdp 会话 · transports 通道注册表 · planlint 计划校验/人审强制
channels/  x/(vendor+adapter) · email_smtp · email_cdp · reddit · linkedin · github（api 通道首发） · hn（CDP 写 + Algolia 读）
transports/  os_input（macOS 键鼠/AX 模拟，UNVERIFIED）
skills/    marketing-ops（agent 操作协议）· github-project-ops（GitHub 项目经营协议+体检脚本）
tests/     离线单测（45 项，含 449 事故回归与人审门禁）：python3 -m unittest discover tests -v
mcp_server.py  MCP 外壳（stdio JSON-RPC，零依赖）
```

X 渠道 vendor 引入 x-ops-automation（上游 github: roy-tong/x-ops-automation），
**不改写已验证代码**（见 `channels/x/vendor/PROVENANCE.md`）；上游修选择器时同步。

## License

MIT（见 [LICENSE](LICENSE)）。`channels/x/vendor/` 引入自同作者仓库 x-ops-automation（MIT）。
