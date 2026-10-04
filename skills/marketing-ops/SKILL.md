---
name: marketing-ops
description: 目标驱动的营销自动化操作协议。用户提供目标平台与业务目标后，按"立场确认→目标拆解→客群证据→计划 plan.json→人审门禁→执行（mkt CLI/MCP，dry-run 优先）→账本复盘"七步走完整周期。用于营销自动化、触达方案、客群寻找、外联波次执行、渠道运营复盘。触发词：营销自动化、触达方案、找目标客群、外联执行、营销运营、growth campaign、mkt。
---

# 营销自动化操作协议（marketing-ops）

本 skill 是 **marketing-ops-automation 执行内核的 agent 操作协议**（05 产品方案 §5.3）：
agent 当参谋（出方案）、内核当执行（守门禁）、人当闸门（审计划）。动作落到
CLI（`python3 mkt.py …`）或 MCP（mkt-server），本协议不发明新动作。

仓库位置：github.com/roy-tong/marketing-ops-automation（克隆到本机的目录；MCP 配置里用其绝对路径）

## 何时使用

- 用户给出"业务目标 + 目标平台"，要落地方案或直接执行
- 需要为目标产品寻找/扩展目标客群并入线索库
- 已有计划要执行（发帖/私信/邮件波次）或复盘（回复率/退信/转化）

## 七步流程

### ① 立场与约束确认
先三行立场（同意/保留/反对）再动手。确认：业务目标一句话、可用渠道、节奏约束、
已有资产（内容/线索）。缺目标不动笔。

### ② 目标拆解
假设 → ICP（组织+persona）→ **单一 beachhead**（不铺多线）。写清假设的可证伪点。

### ③ 客群证据
- 发现：`mkt.py x/reddit/linkedin search`（只读）+ 既有研究资产（leads 库/触达数据库）
- 资格：证据分级 A=公开自证 / B=强信号 / C=推断；**低于 B 不入队**
- 公司账号必须富化到具体员工（部门+权责+persona）——走 [[b2b-account-mapping]] 协议
- 入库 `data/contacts.jsonl`（hooks/title/verification 字段是触达弹药）

### ④ 计划落盘（plan.json）
按 `data/schemas.md` 的 plan schema 写 `data/plans/<plan_id>.json`：
- 渠道优先级用**实测数据**（求评邮件 5.3% > GitHub 站内 > X 冷触达 1.5-2.4%），不平均用力
- 每波：channel×action×size（≤内核日预算）×content_policy（千人千面）
- 成功判据**预注册**（metric+target+stop）
- `review.human_approved` 留 false——**人审不是礼节，是执行前置条件**
- 自校验：`python3 -c "from core.planlint import load_plan,lint_plan; print(lint_plan(load_plan('<path>')))"`

### ⑤ 人审门禁
把 plan 呈给用户（波次/规模/内容方针/停止条件），获批后改 `human_approved: true`。
未经人审的 campaign，内核与 MCP 都会拒跑（这是特性不是阻碍）。

### ⑥ 执行
```bash
cd <仓库>
./launch.command                       # 首次：启动 CDP 会话
python3 mkt.py email message --queue data/queues/<campaign>.json          # dry-run
python3 mkt.py email message --queue data/queues/<campaign>.json --go     # 实跑
python3 mkt.py status                  # 账本
```
- 内容由人审产出（写作走 [[warm-email-outreach]] / [[expert-review-outreach]]）；
  队列正文千人千面，占位符残留会被门禁直接拒绝
- 写操作永远先 dry-run 看截图，再 --go
- UNVERIFIED 渠道（reddit/linkedin）首跑必须用户在场
- 失败即停，不重试轰炸

### ⑦ 复盘
`mkt.py status` + 账本查询 → 回复率/退信/committed 计数 → 对照预注册判据：
达标→加深该渠道；不达标→回 ② 修假设。**账本对不上平台实际状态 = 停该渠道查账。**

## 红线（内核已硬编码，agent 也不得绕过）

- 只驱动本人拥有/运营的账号；不群发、不买量、不模板批发
- 上限是内核属性：计划写多大都会被日预算/单次上限拒绝
- 回读校验不一致绝不发送（449 空正文事故固化）
- 不做对抗：不绕验证码、不伪造指纹、不用代理池
- LinkedIn 全自动默认锁死（半自动=终端人工确认）；Reddit 日 ≤3 帖

## 与其他 skill 的分工

| 场景 | 用哪个 |
|---|---|
| 方案/执行/复盘（本协议） | marketing-ops |
| 外联邮件写作 | warm-email-outreach（求评类 → expert-review-outreach） |
| 公司账号找人/换人 | b2b-account-mapping |
| 客群需求研究（读侧深研究） | user-demand-research |
