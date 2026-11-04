---
name: growovo-gtm
description: Growovo GTM/User Growth 操作协议——面向独立开发者与一人公司，以真实需求、价值资产、授权执行和用户旅程为入口的 Agent-native 增长操作员。输入"把这个产品推向目标客户"，按"复盘→策略→落地→风控→执行→进度→结果→优化"八步闭环推进，并以同范围结果、候选实验和真实使用反馈修订策略。用于 GTM 运营、需求发现、价值内容、增长执行、营销自动化、触达方案、客群寻找、外联执行、用户旅程与渠道复盘。触发词：GTM、增长闭环、把这个产品推向用户、获客、激活转化、付费转化、launch 推广、营销自动化、触达方案、找目标客群、外联执行、growth campaign、mkt。
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
| Positioning 定位（定） | 理解产品、定位 ICP、购买动机与竞争替代 | 研究上游 [askovo](https://github.com/roy-tong/askovo) + `mkt opportunity`（需求匹配/候选动作，不把抱怨当购买意愿） |
| Asset 资产（造） | 生成可直接使用的教程、样例、测试、模板、对比与真实案例 | `mkt artifact lint/render` + 内容协作 skill；必须有直接用户价值、复现步骤和边界，不只是像人写 |
| Campaign 投放（跑） | 选 Platform×Motion×Vertical、执行授权动作、管理反馈 | `mkt playbook compose` + `mkt execute`（统一审批/队列/人工单/读回） |
| Outcome 结果（量） | 追踪真实使用、激活/留存/付费与断点 | `mkt journey`（同范围六阶段）+ `mkt result`（同范围三层投影）+ 可选学习实验 |
| **Follow-up 跟进（守）** | **发完之后把人跟到底：对象台账、到期提醒、三次原则、判定弃或续** | `mkt followup`（台账/三次原则/到期）+ `mkt intent`（可配置学习实验）+ `mkt billing`（动作/结果/订阅/混合报价实验） |

对增长结果负责的是五引擎整体；单渠道动作只是「跑」层的原语。

**为什么多了「守」这一层**：客户语料里最一致的卡点不是"不知道去哪发"，而是**发一次就没声音**。
有效动作是「找对的人 + 有用的话 + 持续跟」。所以任何一次投放结束，都必须落到跟进台账：
没有 `mkt followup add` 的触达，等于没做。


## 八步闭环（内核已实现，不是文档清单）

每个环节都有对应命令与 MCP 工具；**平台清单只有一份**（`core/platforms.py`，37 个），
诊断/策略/执行/日志扫描共用，不会各说各话。

| 步 | 要回答的问题 | 命令 | MCP |
|---|---|---|---|
| 〇 复盘 | 哪做得好、哪做得不好、为什么、改什么 | `mkt retro scan --path <目录>` → `analyze` → `report --facts <口径>` | `mkt_retro_scan` / `mkt_retro_analyze` |
| ① 策略 | 打哪条、怎么冷启动、什么时候放大 | `mkt strategy build --facts f.json --retro-facts r.json` | `mkt_strategy` |
| ② 落地 | 账号、每天做什么、什么算做完 | `mkt strategy cadence --facts f.json --start 2026-11-04` | `mkt_cadence` |
| ③ 风控 | 平台怎么查 AI、我们改什么 | 含在策略输出的「平台会怎么查我们」 | `mkt_strategy` |
| ④ 执行 | 真发出去 | `mkt execute --strategy-file --targets --campaign`（有写通道出队列；没有的出人工单） | — |
| ⑤ 进度 | 今天该动什么、这周比上周好不好 | `mkt progress daily/weekly` | `mkt_progress` |
| ⑥ 结果 | 同范围三层各自多少 | `mkt result --campaign C --since D1 --until D2` | `mkt_result` |
| ⑦ 回流 | 改哪个参数、从多少到多少 | `mkt revise --params '{}'` | `mkt_revise` |

**两条硬纪律（写在代码里）**：
- 复盘报告的每条结论必须同时有「数字 + 出处 + 因果机制 + 可执行动作」，缺一项 `Claim.validate()` 直接抛错；
- 比率只能从人工钉定的口径文件算，机器不替你选哪个数——它是供证据的，不是做裁决的。

**口径文件长这样**（`--facts` / `--retro-facts`）：
```json
{"github": {"动作": 77, "回应": 4, "结果": 30},
 "email_1to1": {"动作": 209, "回应": 11, "结果": 1},
 "email_batch": {"动作": 991, "回应": 0, "结果": 0}}
```
子渠道要拆开：同一个平台里打法不同、效率差一个量级的，合在一起算会把有效打法埋掉。


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
```bash
mkt plan new --id <plan_id> --goal '…' --beachhead '…' --hypothesis '…' \
  --channel email --wave-action message --size 10
mkt plan lint --id <plan_id>
```
- 渠道优先级用**实测数据**（求评邮件 5.3% > GitHub 站内 > X 冷触达 1.5-2.4%），不平均用力
- 每波：channel×action×size（≤内核日预算）×content_policy（千人千面）
- 观察指标是可选学习对象；只有显式受控实验才预注册假设/指标/时间窗，不设产品生死门
- `review.human_approved` 留 false——**人审不是礼节，是执行前置条件**

### ⑤ 人审门禁（审批是事件，不是布尔值）
```bash
mkt plan approve <plan_id> --by <审批人> [--days 7]
```
把 plan 呈给用户（波次/规模/内容方针/停止条件），获批后用**署名审批**：内核写
`data/approvals.jsonl` 事件 + 执行指纹 `plan_sha256`。指纹绑定计划、规范化队列、目标、正文、动作、平台、账号和工作区；批准后任何一项被改 → 指纹失配，
实跑拒跑；过期、撤销或错账号/工作区同样拒跑。
**--go 实跑必须绑定已人审 plan**：无 plan 管辖的 campaign 内核默认拒跑，仅 CLI
人工通道 `--adhoc` 显式放行（账本记 adhoc=true）；MCP 不开放 adhoc——agent 不得
自我授权。全局暂停（`mkt pause`）期间一切写操作拒跑。

### ⑥ 执行
```bash
cd <仓库>
./launch.command                       # 首次：启动 CDP 会话
mkt queue build --campaign <campaign> --channel email --wave-action message \
  --template assets/outreach.md --targets data/targets.csv   # 模板+CSV 生成队列
mkt queue lint data/queues/<campaign>.json                   # 编辑期门禁
mkt review <campaign>                                        # 逐条预检 + 留证
python3 mkt.py email message --queue data/queues/<campaign>.json          # dry-run
python3 mkt.py email message --queue data/queues/<campaign>.json --go     # 实跑（须已人审 plan）
```
- 内容由人审产出（写作走 [[warm-email-outreach]] / [[expert-review-outreach]]）；
  队列正文千人千面，占位符残留会被门禁直接拒绝
- 写操作永远先 dry-run 看截图，再 --go
- UNVERIFIED 渠道（reddit/linkedin/hn）首跑必须用户在场
- 失败即停，不重试轰炸

### ⑦ 复盘（量引擎 + 记账自动化）
```bash
mkt collect serve --port 8787                     # 本地 collector：/t/<link> 点击归因 + /events webhook
mkt collect import --source plausible --csv plausible.csv
mkt collect import --source posthog --csv posthog.csv --event-map user_signed_up=signup
mkt outcome sync --campaign <campaign>            # 账本 → 触达计数快照（幂等）
mkt link create --campaign <campaign> --channel hn --url <落地页>
mkt outcome import data/events.csv --campaign <campaign>   # CSV 导入（幂等）
mkt experiment obs --id <exp> --from-outcomes      # 事件 → 观察值（不再手抄）

# 跟进（发完之后的主线）
mkt next                                            # 到期的跟进会排在一切新动作之前
mkt followup add --platform reddit --target u/xxx --source <讨论链接>
mkt followup due                                    # 今天该跟谁
mkt followup touch <id> --result asked --note '问了流式场景'   # replied/asked/used/declined/no_reply
mkt followup stats                                  # 45 天后仍在对话的人 = 唯一硬指标

# 可配置意图学习实验
mkt intent start reddit-q4 --subs r/SideProject,r/IndieDev --weeks 4
mkt intent log reddit-q4 --sent 20 --deleted 1 --replied 4 --moved 1
mkt intent verdict reddit-q4                        # 样本不足/安全异常/观察互动，不判渠道生死

# 报价实验：按动作/按结果，也可显式测试订阅或混合形状
mkt billing --actions 20 --outcomes 2
mkt billing --experiment-id sub-1 --pricing '{"model":"subscription","monthly_fee":100}'
mkt billing --experiment-id sub-1 --record-acceptance accepted --by roy
mkt campaign report <campaign>                     # 一页纸：计划/执行/结果/下一步
```
- 账本 committed 是「发出去了」，不是「转化了」；事件与触达分栏，不合成转化率
- activation/paid 必须带 source_ref 与核验状态，漏斗主体经 contacts 归一
- 渠道有效性可用可选 `mkt experiment` 做受控学习；也可直接比较同范围结果，不把预注册当产品门槛
- `mkt ledger resolve <ref>` 处置 indeterminate（署名 + 依据，原账本行不改）
- 对照学习结果：发现错误假设→回 ② 修假设；证据不足→补证或停止，不机械换渠道
- **账本对不上平台实际状态 = 停该渠道查账。**

## 红线（内核已硬编码，agent 也不得绕过）

- 只驱动本人拥有或明确授权运营的账号；不群发、不买量、不模板批发
- 实跑必须绑定已人审 plan；adhoc 仅 CLI 人工通道显式放行且账本留痕
- 上限是内核属性：计划写多大都会被日预算/单次上限拒绝
- 回读校验不一致绝不发送（449 空正文事故固化）
- 不做对抗：不绕验证码、不伪造指纹、不用代理池绕过平台限制；代理只在合法网络需求与授权一致时绑定，不能提高配额或绕封禁
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
