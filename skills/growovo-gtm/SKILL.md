---
name: growovo-gtm
description: Growovo GTM 操作协议——以增长目标（真实注册/激活/付费）为入口的 Agent-native GTM 操作员。输入"把这个产品推向目标客户"，按四引擎（定位→资产→投放→结果）拆解为"立场确认→目标拆解→客群证据→计划 plan.json→人审门禁→执行（mkt CLI/MCP，dry-run 优先）→账本复盘"完整周期，并以预注册实验与归因台账评估渠道有效性。用于 GTM 运营、增长闭环、获客执行、营销自动化、触达方案、客群寻找、外联波次执行、渠道复盘。触发词：GTM、增长闭环、把这个产品推向用户、获客、激活转化、付费转化、launch 推广、营销自动化、触达方案、找目标客群、外联执行、growth campaign、mkt。
---

# Growovo GTM 操作协议（growovo-gtm）

本 skill 是 **Growovo 执行内核的 agent 操作协议**（05 产品方案 §5.3）：
agent 当参谋（出方案）、内核当执行（守门禁）、人当闸门（审计划）。动作落到
CLI（`python3 mkt.py …`）或 MCP（mkt-server），本协议不发明新动作。

仓库位置：github.com/roy-tong/Growovo（克隆到本机的目录；MCP 配置里用其绝对路径）

## 入口是一个 GTM 目标，不是单个渠道动作

用户说"这是我的产品/GitHub 仓库，帮我找到最可能需要它的用户并完成增长闭环"时，
按四引擎拆解（Growovo = 定 → 造 → 跑 → 量）：

| 引擎 | 职责 | 当前承载 |
|---|---|---|
| Positioning 定位（定） | 理解产品、定位 ICP、购买动机与竞争替代 | 研究上游 [askovo](https://github.com/roy-tong/askovo) 的研究判断 + 本协议 ②③ 步 + `gtm-readiness` 就绪体检 |
| Asset 资产（造） | 生成适配目标渠道的页面/内容/演示/触达素材 | 内容协作 skill（warm-email-outreach 等）+ 既有研究资产；素材进队列前过千人千面门禁 |
| Campaign 投放（跑） | 选渠道、执行授权动作、管理反馈 | 本协议 ④⑤⑥ 步 + `mkt` 内核（六原语/统一写路径/账本） |
| Outcome 结果（量） | 追踪激活/付费/留存，评估实验 | `mkt experiment`（预注册实验）+ `mkt outcome`（事件/漏斗/归因台账，口径非因果） |

对增长结果负责的是四引擎整体；单渠道动作只是「跑」层的原语。

## 何时使用

- 用户给出"产品/仓库 + 增长目标"（试用、注册、激活、付费），要方案或直接执行
- 需要为目标产品寻找/扩展目标客群并入线索库
- 已有计划要执行（发帖/私信/邮件波次）或复盘（回复率/退信/转化）
- 要判断哪条渠道真正有效（预注册实验 + 归因台账，不是拍脑袋）

## 七步流程

### ① 立场与约束确认
先三行立场（同意/保留/反对）再动手。确认：业务目标一句话、可用渠道、节奏约束、
已有资产（内容/线索）。缺目标不动笔。

### ② 目标拆解
假设 → ICP（组织+persona）→ **单一 beachhead**（不铺多线）。写清假设的可证伪点。

### ③ 客群证据
- 发现：`mkt.py x/reddit/linkedin/hn search`（只读）+ 既有研究资产（leads 库/触达数据库）
- 资格：证据分级 A=公开自证 / B=强信号 / C=推断；**低于 B 不入队**
- 公司账号必须富化到具体员工（部门+权责+persona）——走 [[b2b-account-mapping]] 协议
- 入库 `data/contacts.jsonl`（hooks/title/verification 字段是触达弹药，也是漏斗身份归一的基础）

### ④ 计划落盘（plan.json）
按 `data/schemas.md` 的 plan schema 写 `data/plans/<plan_id>.json`：
- 渠道优先级用**实测数据**（求评邮件 5.3% > GitHub 站内 > X 冷触达 1.5-2.4%），不平均用力
- 每波：channel×action×size（≤内核日预算）×content_policy（千人千面）
- 成功判据**预注册**（metric+target+stop）
- `review.human_approved` 留 false——**人审不是礼节，是执行前置条件**
- 自校验：`python3 -c "from core.planlint import load_plan,lint_plan; print(lint_plan(load_plan('<path>')))"`

### ⑤ 人审门禁
把 plan 呈给用户（波次/规模/内容方针/停止条件），获批后改 `human_approved: true`。
**--go 实跑必须绑定已人审 plan**：无 plan 管辖的 campaign 内核默认拒跑，仅 CLI
人工通道 `--adhoc` 显式放行（账本记 adhoc=true）；MCP 不开放 adhoc——agent 不得
自我授权。全局暂停（`mkt pause`）期间一切写操作拒跑。

### ⑥ 执行
```bash
cd <仓库>
./launch.command                       # 首次：启动 CDP 会话
python3 mkt.py email message --queue data/queues/<campaign>.json          # dry-run
python3 mkt.py email message --queue data/queues/<campaign>.json --go     # 实跑（须已人审 plan）
python3 mkt.py status                  # 账本
```
- 内容由人审产出（写作走 [[warm-email-outreach]] / [[expert-review-outreach]]）；
  队列正文千人千面，占位符残留会被门禁直接拒绝
- 写操作永远先 dry-run 看截图，再 --go
- UNVERIFIED 渠道（reddit/linkedin/hn）首跑必须用户在场
- 失败即停，不重试轰炸

### ⑦ 复盘（量引擎）
`mkt.py status` + 账本查询 → 回复率/退信/committed 计数；增长结果记入
`mkt outcome`（activation/paid 必须带 source_ref 与核验状态，漏斗主体经
contacts 归一）；渠道有效性判断走 `mkt experiment`（判据预注册，机器只聚合
不判定）。对照预注册判据：达标→加深该渠道；不达标→回 ② 修假设。
**账本对不上平台实际状态 = 停该渠道查账。**

## 红线（内核已硬编码，agent 也不得绕过）

- 只驱动本人拥有/运营的账号；不群发、不买量、不模板批发
- 实跑必须绑定已人审 plan；adhoc 仅 CLI 人工通道显式放行且账本留痕
- 上限是内核属性：计划写多大都会被日预算/单次上限拒绝
- 回读校验不一致绝不发送（449 空正文事故固化）
- 不做对抗：不绕验证码、不伪造指纹、不用代理池
- LinkedIn 全自动默认锁死（半自动=终端人工确认）；Reddit 日 ≤3 帖；HN 日 ≤1 帖（Show HN 限自有项目，提交后 1h 内发首条作者评论）
- 结果事件不得编造或近似：activation/paid 必须真实发生并带核验状态；实验观察只在 running 期接受

## 与其他 skill 的分工

| 场景 | 用哪个 |
|---|---|
| GTM 方案/执行/复盘（本协议） | growovo-gtm |
| 外联邮件写作 | warm-email-outreach（求评类 → expert-review-outreach） |
| 公司账号找人/换人 | b2b-account-mapping |
| 客群需求研究（读侧深研究，研发前上游） | askovo |
| GitHub 项目经营（体检/巡检/社区响应） | github-project-ops |
