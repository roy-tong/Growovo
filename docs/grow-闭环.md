# 结果闭环 v2：给事实和目标，自动执行并交付日/周结果

> 建册：2026-11-14 · 前置：`docs/grow-v2-实现契约.md`
> 引擎：`core/grow_compile.py`（策略编译）+ `core/grow_v2.py`（生命周期）+
> `core/grow_schedule.py`（配额/窗口/供给）+ `core/grow_ops.py`（心跳/体检/安装）+
> `core/result_card.py`（周卡）+ `core/deliver.py`（落盘/投递）

## 用户视角：只做三件事

```bash
# 1) 给事实 + 目标（唯一一次配置）
mkt grow launch g1 --facts facts.json --goal awareness.followers \
  --target 200 --handle your_x_handle --owner you@example.com
mkt grow arm g1          # 人工开闸（唯一必须的点确认）
mkt grow install g1      # 生成定时任务，之后 tick 自动跑

# 可选：让本机 Codex 自动补内容（模型只产文本，目标/公式/时间/发送仍由引擎决定）
mkt grow agent g1        # 启用自动供给
mkt grow produce g1 --max-items 3   # 立即生成+审稿补队列（不发送）

# 2) 每天早上看一屏，决定继续还是优化（不跑命令也能收到日/周结果单）
mkt grow daily g1
mkt grow review g1 --days 7                    # 近 7 天复盘：发布/读数/供给/建议
mkt grow decide g1 --action continue           # 或 --action optimize --note "为什么"

# 供给上游：先找今天值得回复的对象（只读），再由 agent 写内容并 stage
mkt grow prospects g1 --query "AI invoice reconciliation" --koc-only   # 账号级候选+KOC 优先
mkt grow targets g1 --query "AI invoice reconciliation" --max-age-hours 24 --min-likes 2
mkt grow formulas g1                    # 回复/原创公式与配比
mkt grow stage g1 --from drafts.json --date 2026-11-15

# 学习回路：采指标 → 归因 → 次日优化
mkt grow metrics g1 --days 7
mkt grow learn g1
mkt grow decide g1 --action optimize --dry-run

# 3) 异常才需要人处理
mkt grow status g1        # 配额/供给/读数/心跳/决策状态
mkt grow resume g1 --by "$USER" --note "处理了什么"
```

正常一周里，用户需要敲的命令是 **0 次**；`arm` 是唯一的人确认动作。

## 引擎自动做什么

| 环节 | 动作 | 代码 |
|---|---|---|
| 定策略 | 目标契约 → 主渠道/承接渠道/不做什么 → 配额、时间窗、内容需求单、读数计划、护栏 | `grow_compile.py` |
| 供内容 | 需求单写 `data/grow/<id>/briefs.md`；agent 按 brief 生产，`mkt grow stage` 入队（过队列门禁） | `grow_v2.py` stage |
| 执行 | 按日配额切片发送；真实发送之间按 60–120 秒随机停顿；黄金窗内才发，死区不发；重复/抑制跳过，失败停车；过期未发单独计数 | `grow_v2.tick` + `queue_runner` |
| 自动供给 | 可选：Codex 只写正文，目标/公式/时间/发送不交给模型；结构闸门 + 二次审稿；预算、退避、进程锁 | `grow_agent.py` |
| 读数 | 每天自动读粉丝/曝光；读数失败只降级，不停闭环；连续缺数才提醒 | `grow_v2._read_once` |
| 交付 | 每天日结果卡；每周结果单（发布/曝光/粉丝 + 业务事件 + 护栏 + 供给 + 下周动作），落 HTML 并按需投递 | `result_card.py` + `deliver.py` |
| 调参 | 安全层（健康度/失败）→ 供给层（缺口）→ 效果层（完成率）；每次调整可回滚 | `grow_v2._apply_adjustments` |
| 供给上游 | 只读搜索候选回复对象，排除自己/已发/已暂存；产出候选清单 | `grow_targets.py` |
| 账号选择 | 帖子契合度 + 作者主页 + KOC 分档排序，早期默认 KOC 优先 | `grow_fit.py` |
| 内容策略 | 回复/原创公式、配比、结构分与写作闸门；回复禁链接禁产品名 | `grow_content.py` |
| 学习回路 | 采自己帖子的公开指标，按公式/窗口/人群归因，样本≥3 才提调参 | `grow_learning.py` |
| 复盘 | 近 N 天发布/读数/供给/决策/建议，落 md+html | `grow_v2.review` |
| 运维 | 心跳、体检、launchd/cron 生成、前台常驻 daemon 与回退脚本 | `grow_ops.py` / `grow_v2.daemon` |

## 三层承诺（对外怎么说）

**硬承诺（可核验）**
1. 执行量：每天把配额内的内容发出去，账本 committed 逐条可回查；
2. 真实读数：每天落粉丝与曝光，截图留证，UNVERIFIED 起步、7 次人工对数后升级；
3. 闭环责任：完成率 ≥90% 加量 25%（上限 2 倍初始）、供给不足提前 3 天报警、异常停车不自动重试。

**不做承诺**
- 不保证平台算法给多少曝光/粉丝（没人能给）；
- 人工渠道（小红书/知乎等）不计入自动发布量；
- 内容质量由 agent 自闭环 + 队列门禁，不承诺爆款。

**永不自动**：付款、合同、折扣、危机响应；开闸、恢复、换策略永远是人。

## 每日决策：继续 or 优化

`mkt grow decide` 是用户对业务结果的唯一控制杆：

- `continue`：保持当前策略与配额，只记录决定。
- `optimize`：引擎根据近 7 天完成率与未来供给生成提案，并只调整执行参数（默认周计划量），每条改动带 `rollback`；策略级变更（换渠道/换战术/换目标）只出提案，不自动执行。
- 不做决定也照常运行，`status` 会显示「今日决策：未记录（默认继续）」。

## 安全模型

- 所有发送仍走 `run_queue` → `action_runner`：暂停闸、常驻授权、最小动作集、DNC/抑制、日预算、回读校验、账本幂等全部保留；
- 队列按日配额切片，超配额顺延，不会一次把一周内容全发出去；
- 窗口外不发；受众本地 00:00–06:00 死区绝不发；
- 未知结果（indeterminate）停车待人工裁决，绝不自动重试；
- 账号 health < 60 或状态不 eligible 时只能 dry-run，`arm` 被拦。

## 环境准备（2026-11-14 实测）

- 先跑 `launch.command` 启动调试 Chrome，确认已登录 X；cookie 为空时重启调试 Chrome。
- 定时任务装不上或读不到文件时，双击 `data/grow/<id>/run-loop.command` 前台常驻；
  或 `mkt grow daemon <id>` 直接跑常驻循环。
- Chrome 157 + Playwright 1.60 连接会报 `Browser.setDownloadBehavior` 不兼容：已打本地驱动补丁
  （备份 `coreBundle.js.mkt-bak`），`core/cdp.py` 自动设置 `MKT_SKIP_DOWNLOAD_BEHAVIOR=1`。
- launchd 定时任务受 macOS 文档目录保护：需给 `/usr/bin/python3` 或
  `/Library/Developer/CommandLineTools/usr/bin/python3` 完全磁盘访问权限，否则 tick 读不到
  `mkt.py`；临时替代是 `canary-tick.command` 前台常驻。
- 读数校准：`mkt grow calibrate <id> --followers <手读数>`，连续 7 次一致升级 PROVEN；
  详见 [canary 记录](canary-20261114.md)。

## 运营策略

早期增长以 KOC（1k–50k 垂直账号）为主战场，不追头部大号；内容分发现层（回复）/
证明层（原创）/承接层（主页），回复先同意具体观点再补事实最后追问，CTA 只放在
自己的帖子里。完整设计见 [增长运营策略 v1](grow-运营策略-v1.md)。

## 第一期平台边界

只有 **X + Email** 进入全自动承诺。其余写渠道需人工首跑解锁，人工渠道只出作业单。见
[平台自动执行矩阵](platform-automation-matrix.md)。

## v2 之后

- 更多 AUTO 平台（每个都要过写通道实测 + 回读 + 读数四件套）；
- 时间窗/公式的分组效果学习（需要按条读数，当前只有账号级）；
- 多账号矩阵与业务事件（注册/激活/付费）接入后的转化口径。
