# 增长成了研发的新活：上手指南

> 一句话：你被要求负责产品的 GTM 和用户增长，但你受的是工程训练。这份手册把增长翻译成工程问题——系统、证据、可回滚，不要玄学。

## 你是谁（两类人，同一个处境）

- **独立开发 / 一人公司**：产品写完了，增长没人替你做。
- **公司里转型全栈**：职责从代码扩到 GTM，老板要看数。

共同点：有工程纪律，缺增长训练。Growovo 的设计假设恰好是**把增长当系统工程做**——所以对你们，它的上手成本是全场最低的。

## 词汇对照：你熟悉的 ↔ Growovo 里的

| 你熟悉的 | Growovo 里对应 | 一句话解释 |
|---|---|---|
| SLA / SLO | [结果契约](grow-闭环.md)：硬承诺 vs 明确不承诺 | 只承诺可核验的执行量、真实读数、闭环责任；不承诺平台算法给多少曝光 |
| audit log | 账本 ledger，committed 逐条可回查 | 每个真实发出的动作都落账，永不涂改 |
| staging / canary | 一切写操作默认 dry-run，`--go` 才实跑；先 canary 后放量 | 和你部署的习惯一模一样 |
| trust level | 证据分级 UNVERIFIED → PROVEN | 没对过数的读数不冒充已验证 |
| CI 日循环 | `grow daemon` / `grow daily` 每日闭环：执行→读数→调参 | 每天一屏，像看 pipeline 状态 |
| 告警 | 供给缺口提前 3 天报警；异常停车待人工裁决 | 失败即停，不重试轰炸 |
| code review | `grow review --days 7` 周复盘 | 像审 PR 一样审增长结果，决定继续还是优化 |

## 上手路径（七步，每步都有命令）

```bash
# 1. 诊断：先零账号零网络跑一遍，看引擎怎么思考
python3 mkt.py demo
python3 mkt.py work-demo

# 2. 落地：给产品事实包和增长目标，生成策略
python3 mkt.py grow launch g1 --facts facts.json \
  --goal awareness.followers --target 200 --handle your_handle

# 3. 风控：常驻授权只替代逐次人审，付款/合同/危机永不自动
python3 mkt.py policy enable --allow x:reply --by "$USER"
python3 mkt.py grow arm g1        # 确认策略和授权后，开启真实执行

# 4. 执行：引擎常驻跑，发布、读数、调参
python3 mkt.py grow daemon g1     # macOS 后台用 grow install g1 --load

# 5. 进度：每天一屏——发布量/读数/供给/护栏
python3 mkt.py grow daily g1

# 6. 结果：周结果单，三件套口径（发布量/日均曝光/新增粉丝）
#    mkt grow report 逐条可回查，带边界声明

# 7. 复盘：归因给建议，决定继续还是优化
python3 mkt.py grow review g1 --days 7
python3 mkt.py grow learn g1
```

## 三个硬承诺 / 明确不承诺

**承诺**：① 执行量——账本 committed 逐条可查；② 真实读数——每日粉丝与曝光落账，截图留证、带证据级别；③ 闭环责任——完成率 ≥90% 自动加量 25%（上限 2 倍）、<50% 亮注意项，周周有结果单。

**不承诺**：平台算法的数值保证。曝光/粉丝是平台自报数；缺数写「取不到」，不写 0。承诺方式是超量执行 + 自动调参，不是拍胸脯。

## 先看证据再决定信不信

- [发布尸检：11 小时 73 条推文，0 个新 star](launch-autopsy-20261105.md)——用这个工具跑的一次真实 campaign、拿到的数字、以及它暴露的我们自己的账本缺口。
- [为什么不自己写个脚本，或者用通用自动化平台？Why not just write a script?](compare.md)（英文）——逐项对比，每条主张对应仓库文件。
- [平台自动执行矩阵](platform-automation-matrix.md)——第一期 X + Email 全自动；GitHub、Reddit、Hacker News、LinkedIn 需人工首跑解锁；小红书、知乎等人工渠道只出作业单。

## 合规底线（也是你的安全带）

仅用于本人拥有或明确授权运营的账号；CDP 通道零凭据；不做对抗（不绕验证码、不伪造指纹）；不群发。公司账号要接进来时，授权留痕全程可查——**你不会因为引擎越界被开除**。
