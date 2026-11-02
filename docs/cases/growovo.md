# 案例：Growovo —— 吃自己狗粮的操作员

> **角色**：Growovo 自己的仓库与 GTM。工具没上线之前，它自己的改名、文档、测试与 dogfood 循环先跑在自己的门禁上。
> **周期**：2026-10-04 建仓（时名 marketing-ops-automation）→ 至今 · 数据截至 2026-11-02
> **[English version](growovo.en.md)**

## 这个案例在证明什么

一个工具类仓库最容易造假的地方是「自己从来不用自己」。Growovo 的自举路径是：**每一次自我运营动作都走自己的管道**——计划、人审、dry-run、账本、分钟记录，与本仓交付给外部客户的是同一套。

## 已发生的动作（全部可核验）

| 动作 | 证据 |
|---|---|
| 仓库改名 marketing-ops-automation → Growovo，README 重写为操作员定位 | [c305fe0](https://github.com/roy-tong/Growovo/commit/c305fe0)；改名经 API 验证生效 |
| 零账号四步演示（lint → 人审闸门 → 写路径 dry-run → 账本） | [a76f523](https://github.com/roy-tong/Growovo/commit/a76f523)，`python3 mkt.py demo` 30 秒本地复现 |
| 双语 README 全文对等（中文默认 + 英文 1:1，含完整命令矩阵） | [README.md](../../README.md) / [README.en.md](../../README.en.md) |
| 离线单测 | 186 项，GitHub Actions 门禁，`python3 -m unittest discover tests` |
| 任务结果三层记录（平台动作 / 任务结果 / 业务结果独立分层，矛盾即拒收） | [ea194e1](https://github.com/roy-tong/Growovo/commit/ea194e1)，`core/task_outcome.py` |
| GitHub 专家任务基准（33 例，21 dev / 12 holdout；`--fail-on-harmful` 让基准可以当门禁用） | `scripts/github_benchmark.py` + `tests/fixtures/github_benchmark/` |
| Skill 协议发布 | `skills/growovo-gtm`（GTM 操作协议）、`skills/github-project-ops`（GitHub 项目经营协议） |

## Dogfood 回路：客户端的教训回流成产品能力

Askovo P0（见[案例一](askovo.md)）不是单向服务。第一周内已经发生一次完整回流：

1. dogfood 中自研的 GTM readiness 体检（12 维）被吸收进 Growovo 工具链（v1.1）；
2. 体检暴露的 2 处脚本误报当场修复，过门分 30 → 32；
3. 「PyPI 未发布」类阻塞的处理模式（先验证替代路径，不把等待放进关键路径）进入操作手册。

每一笔 GTM 动作都记 operator/human 分钟（Askovo 台账首日即 275 / 5 分钟）——这套账本同时是服务交付物和产品需求来源。

## 状态与证据等级（截至 2026-11-02）

| 项 | 状态 |
|---|---|
| star / fork | 1 / 0。如实记录：自举极早期，无增长成果可宣称 |
| 测试 | 186 项离线单测全绿；真实账号验证按渠道标注 PROVEN / PORTED / UNVERIFIED，不统一宣称 |
| 边界 | 明确不做：冷邮件群发、广告投放、一键涨粉、对抗平台风控 |
| 下一验证点 | Askovo D14 门禁（2026-11-14）与 Verovo 发射周中检（11/14）——两个都是预注册判据，结果出来前不提前叙事 |
